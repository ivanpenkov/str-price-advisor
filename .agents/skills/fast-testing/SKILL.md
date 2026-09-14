---
name: fast-testing
description: >-
  Operational playbook and reference for ultra-fast development, targeted test execution,
  and high-efficiency tool usage in the STR Price Advisor project. Use whenever developing features,
  debugging issues, running unit tests, or executing tool sequences to eliminate waiting times.
---

# Fast Development & High-Efficiency Testing Playbook

This skill establishes standard parameters and practices to prevent slow development cycles, eliminate artificial delays, and minimize token/time overhead during coding and code reviews.

---

## 1. Test Suite Performance Hierarchy

The repository contains 277 unit tests across 20 test modules. Use this execution hierarchy to match test scope to your current development phase:

| Phase | Recommended Command | Expected Duration | Notes |
|---|---|---|---|
| **Inner Loop (Single Function/Fix)** | `.venv/bin/python -m unittest tests.test_module.TestCase.test_name` | **< 0.2s** | Fastest possible feedback loop. |
| **Module Validation** | `.venv/bin/python -m unittest tests/test_module.py` | **0.2s – 3.0s** | Run on the exact module being modified. |
| **Pre-Review Gate** | `.venv/bin/python -m unittest discover tests` | **~35s** | Run ONCE before launching Subagent Code Review. |
| **Final Verification** | `.venv/bin/python -m unittest discover tests` | **~35s** | Run ONCE at completion before presenting results. |

> [!WARNING]
> **NEVER run `unittest discover tests` inside an iterative edit-and-test loop.** Always test the specific target module until it passes, then run the full suite once at milestone checkpoints.

---

## 2. Test Module Latency Profile (Reference)

| Test Module | Test Count | Typical Duration | Primary Latency Drivers & Mitigations |
|---|---|---|---|
| `test_platform_comparator.py` | 31 | ~7.6s (was 23.3s) | Mock channel scrapes with `retry_delay=0.0` and `sequential_delay=0.0`. |
| `test_airbnb_collector.py` | 29 | ~9.0s (was 18.8s) | Fallback PDP responses with `pdp_timeout=0.05`. |
| `test_proxy_pool.py` | 21 | ~0.7s (was 9.4s) | Forwarder startup delays eliminated via `STEALTH_STARTUP_DELAY=0.0`. |
| `test_stealth_connection.py` | 17 | ~0.8s (was 4.8s) | Startup sleeps eliminated via `STEALTH_STARTUP_DELAY=0.0`. |
| `test_comp_management.py` | 13 | ~6.0s | SQLite memory database transactions. |
| `test_html_dashboard_ui.py` | 15 | ~10.5s | Real Chromium browser viewport rendering tests. |
| *14 Other Modules* | 151 | **< 2.5s total** | Pure unit tests; all run in < 0.3s each. |

---

## 3. Safe Parameters for Tool Calls

### A. `run_command`
- **Synchronous Execution (`WaitMsBeforeAsync: 25000`)**:
  - Always set `WaitMsBeforeAsync` to `25000` (up to 30000) for targeted unit test runs and quick terminal commands.
  - This guarantees synchronous execution, avoiding the creation of background tasks, schedule timer calls, and task status polling.
- **Asynchronous Long-Running Scrapes (`WaitMsBeforeAsync: 500`)**:
  - For live scraping runs (`run --quick`, `run --weekly`, `enrich-comps`), set `WaitMsBeforeAsync: 500` or `1000`.
  - Once launched, schedule a timer or stop calling tools to allow reactive wakeup upon completion.

### B. `view_file`
- View up to 800 lines in a single call instead of paging through files in 100-line increments.
- Use `grep_search` first to locate exact line numbers rather than manually scrolling through large source files.

### C. `replace_file_content`
- Group contiguous edits into a single replacement block.
- Include 2–3 lines of unique surrounding context to guarantee first-attempt chunk matching.

---

## 4. Zero-Sleep Test Invariant Guidelines
When adding new network or async code:
1. **Configurable Delays**: Always expose retry delays, polling intervals, and backoff sleeps as instance attributes or environment variables.
2. **Zero Default in Tests**: Set delays to `0.0` in `TestCase.setUp()` so mock tests never wait on wall-clock timers.

