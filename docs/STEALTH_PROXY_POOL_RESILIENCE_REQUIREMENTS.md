# Stealth Proxy Pool Resilience & Session Lifecycle
**Product Requirements Document (PRD) & Technical Specification**

---

## 1. Executive Summary & Problem Context

### 1.1 Background & System Role
The **STR Price Advisor** platform automates competitive pricing intelligence, calendar segmentation, cross-platform rate parity sweeps, and revenue recommendations for Villa del Sol (luxury 5-bedroom short-term rental in Tempe, AZ). 

To prevent IP rate-limiting, CAPTCHAs, bot detection, and competitor surveillance, all external scraping against Airbnb, VRBO, and Booking.com is routed through a parallel out-of-state proxy pool managed by `src/stealth_connection.py` (`StealthConnectionManager`). This pool creates local `pproxy` forwarder bridges bound to verified **NordVPN SOCKS5** servers across top travel feeder markets (Los Angeles, San Francisco, Dallas, Chicago, Atlanta).

### 1.2 The Incident: Daily Quick Scan Stalled at 9/10 Healthy Servers
On **2026-09-14 at 10:35:03 AM**, during execution of the automated daily quick scan (`python -m src.cli run --quick --limit 12 --force --push`), the process stalled for **6 minutes and 22 seconds** in an active retry loop at Step 4b (Cross-Platform Comparison):

```text
================================================================================
⏳ [2026-09-14 10:35:03] WAITING FOR HEALTHY STEALTH SERVERS (9/10 Healthy)
================================================================================
Elapsed: 3m 21s | Target: 10 healthy servers | Next check in 60s

✅ HEALTHY SERVERS (9/10):
    1. feeder-la-1      (Los Angeles, CA)      -> los-angeles.us.socks.nordhold.net:1080 [ONLINE]
    2. feeder-sf-1      (San Francisco, CA)    -> san-francisco.us.socks.nordhold.net:1080 [ONLINE]
    3. feeder-sf-2      (San Francisco, CA)    -> socks-us46.nordvpn.com:1080 [ONLINE]
    4. feeder-la-2      (Los Angeles, CA)      -> socks-us61.nordvpn.com:1080 [ONLINE]
    5. feeder-atl-1     (Atlanta, GA)          -> socks-us68.nordvpn.com:1080 [ONLINE]
    6. feeder-sf-3      (San Francisco, CA)    -> socks-us70.nordvpn.com:1080 [ONLINE]
    7. feeder-dal-2     (Dallas, TX)           -> socks-us73.nordvpn.com:1080 [ONLINE]
    8. Backup (feeder-dal-1) (Dallas, TX)           -> socks-us60.nordvpn.com:1080 [ONLINE]
    9. Backup (feeder-chi-1) (Chicago, IL)          -> socks-us71.nordvpn.com:1080 [ONLINE]

❌ UNHEALTHY SERVERS (13 failing):
    1. feeder-dal-1     (Dallas, TX)           -> dallas.us.socks.nordhold.net:1080
      Stage: SOCKS5_AUTH | Category: [AUTH_FAIL] | Fails: 1
      Detail: SOCKS5 auth rejected (status 0x01: credentials invalid or 10-connection limit reached)
    2. feeder-chi-1     (Chicago, IL)          -> chicago.us.socks.nordhold.net:1080
      Stage: SOCKS5_AUTH | Category: [AUTH_FAIL] | Fails: 1
      Detail: SOCKS5 auth rejected (status 0x01: credentials invalid or 10-connection limit reached)
    3. feeder-us-1      (US Anycast)           -> us.socks.nordhold.net:1080
      Stage: SOCKS5_AUTH | Category: [AUTH_FAIL] | Fails: 4
      Detail: SOCKS5 auth rejected (status 0x01: credentials invalid or 10-connection limit reached)
    ... and 10 reserve candidate nodes failing with status 0x01.
```

At `10:38:15 AM`, the scan finally reached 10/10 servers, resumed execution, and pushed to GitHub at `10:39:34 AM`. While the scan eventually finished, the stall caused an unexpected 6.5-minute delay and posed a high risk of permanent deadlock.

---

## 2. Root Cause Analysis (RCA)

Investigation revealed five interlocking root causes:

### RCA-1: NordVPN 10-Connection Hard Limit per Account
NordVPN accounts enforce an unyielding limit of **10 simultaneous active connections** across all devices, VPN protocols (WireGuard/OpenVPN), and SOCKS5 authentication sessions. Any attempt to open an 11th connection results in RFC 1929 subnegotiation rejection: `status 0x01` (`AUTH_FAIL`).

### RCA-2: NordVPN RADIUS/AAA Server-Side Session Linger (~3 to 6 Minutes)
When local Python processes (`pproxy`) terminate and close their TCP sockets, NordVPN's authentication and accounting backend does **not** instantly decrement the account's active connection count. Stale sessions linger in the provider's session table until an internal accounting keepalive expires (measured experimentally at 180–360 seconds).

*Can NordVPN be explicitly notified of disconnection to expedite accounting?*
**No.** Neither the transport protocol nor NordVPN's infrastructure support an explicit client-side session termination signal:
1. **RFC 1928 (SOCKS5) & RFC 1929 (Auth) Protocol Limitations**: SOCKS5 defines no application-layer "LOGOUT", "TERMINATE", or "CLOSE_SESSION" command opcode. Session termination is conveyed solely by closing the underlying TCP transport stream (via TCP `FIN` or `RST`).
2. **Distributed AAA/RADIUS Accounting Asynchrony**: In a global fleet across 111+ countries, edge proxy daemons report session starts and stops to centralized authentication backends via batched or asynchronous RADIUS/Diameter accounting packets (`Acct-Status-Type = Stop`). To prevent distributed lock contention and database saturation from millions of rapid client reconnects, session counters are decremented on accounting sync intervals or lease TTLs (typically 3–5 minutes) rather than instantly on socket close.
3. **Absence of Out-of-Band Session Management APIs**: NordVPN provides no REST API or webhook for users to programmatically clear or deregister individual SOCKS5 sessions. (The consumer portal button "Log out of all devices" invalidates OAuth2 refresh tokens for desktop/mobile GUI apps, not granular SOCKS5 TCP sockets).

### RCA-3: Teardown and Immediate Recreation of the Proxy Pool between Pipeline Steps
In `src/cli.py:run_weekly_advisory()`:
1. **Step 3 (`AirbnbCollector`)** allocates 10 proxy bridges, scrapes 12 intervals over ~4.5 minutes, and then calls `collector.close_browser()` which terminates all 10 `pproxy` processes.
2. Only **2.5 seconds later** (`await asyncio.sleep(2.5)`), **Step 4b (`PlatformComparator`)** initializes and immediately calls `start_pool(num_workers=10)`.
3. Because NordVPN still had previous connections counted against the account, exactly 9 new connections were accepted; the 10th connection (and every candidate node tested thereafter) was rejected with `0x01`.

### RCA-4: Rigid Quorum Requirement (`min_healthy=10`) with Infinite Wait
In both `AirbnbCollector` and `PlatformComparator`:
```python
proxy_configs = await self.proxy_mgr.start_pool(num_workers=num_workers, min_healthy=num_workers)
```
- `min_healthy` was set to `num_workers` (10).
- In `StealthConnectionManager.start_pool()`, `max_wait_seconds` defaults to `None`.
- When `wait_for_full_pool` is True and `max_wait_seconds` is `None`, the loop polls indefinitely.
- Even though **9 out of 10 healthy servers (90% capacity)** were operational and capable of executing the scrape, the engine refused to proceed.

### RCA-5: Zero Headroom for Operator Personal Devices
Because `DEFAULT_MAX_STEALTH_CONNECTIONS = 10`, the scraper consumes 100% of the account's connection budget. If the operator enables NordVPN on their phone, laptop, or router, only 9 connections remain available for the workstation, making a 10/10 quorum physically impossible to satisfy and causing a permanent deadlock.

### RCA-6: Out-of-Process Pre-Flight Health Checks Self-Poisoning Connection Quota
In `scripts/launchd/run_daily_quickscan.sh` and `run_weekly_fullscan.sh`, a pre-flight health check was invoked immediately prior to launching the scraper:
```bash
"$PROJECT_ROOT/.venv/bin/python" -m src.cli test-stealth --count 10 >> "$LOG_FILE" 2>&1
caffeinate -i "$PROJECT_ROOT/.venv/bin/python" -u -m src.cli run --quick --limit 12 --force --push >> "$LOG_FILE" 2>&1
```
This created a critical self-poisoning race condition:
1. `test-stealth` executed in its own isolated Python process, spawning 10 local forwarders and authenticating 10 SOCKS5 sessions with NordVPN.
2. Upon test completion, `test-stealth` exited, terminating its 10 TCP connections.
3. Due to **RCA-2** (RADIUS/AAA accounting linger of 3–6 minutes), NordVPN's backend continued counting all 10 connections as active on the account.
4. Exactly **0.1 seconds later**, `run_daily_quickscan.sh` launched `cli.py run`. When Step 3 attempted to authenticate its own proxy bridges, NordVPN rejected them immediately with `0x01 AUTH_FAIL` because the account's quota was 100% consumed by the lingering pre-flight test sessions!
5. **Architectural Rule**: Connection health testing must NEVER run out-of-process immediately before a scan. Health verification must happen **in-process** via connection sharing, so the exact connections tested are kept open and reused for the scrape.

---

## 3. User Personas & Core User Stories

### 3.1 Personas
- **STR Operator / Revenue Manager**: Requires automated morning scans (6:15 AM) to finish in <10 minutes without getting stuck in polling loops, regardless of whether their personal mobile phone is connected to NordVPN.
- **System Engineer / Implementer**: Requires clean proxy lifecycle management, deterministic context leasing, and unit tests that pass in milliseconds without wall-clock sleeps.

### 3.2 User Stories
1. **Unattended Execution Reliability**:
   > *"As an operator, I want daily scans to proceed immediately when 8 or 9 healthy servers are available, so that transient connection hiccups on a single node never stall the daily advisory pipeline."*
2. **Personal Device Headroom**:
   > *"As an operator using NordVPN on my phone, I want the scraper to consume at most 8 connections by default, leaving 2 slots free so my personal devices never break the scraping job."*
3. **Session Reuse across Scrape Phases**:
   > *"As an engineer, I want the proxy pool to persist across pipeline steps (Airbnb collection -> Platform comparison) instead of being repeatedly torn down and rebuilt within seconds, eliminating authentication churn."*
4. **Bounded Recovery Timeout**:
   > *"As an operator, I want the proxy startup wait to time out gracefully after a bounded window (e.g. 60s) and proceed with all verified healthy nodes, rather than looping indefinitely."*

---

## 4. Functional Requirements (FR)

### FR-1: Resilient Minimum Healthy Quorum
- **FR-1.1**: The system MUST allow the stealth proxy pool to proceed as soon as a resilient quorum of healthy servers is reached, rather than requiring 100% full capacity.
- **FR-1.2**: If `min_healthy` is not explicitly passed, the default quorum MUST be calculated dynamically as:
  $$\text{min\_healthy} = \max(3, \text{num\_workers} - 2)$$
  *(e.g., for 10 workers, quorum is 8; for 8 workers, quorum is 6).*
- **FR-1.3**: When `len(healthy_endpoints) >= min_healthy` and `wait_for_full_pool` is False (or timeout is reached), `start_pool()` MUST immediately return the healthy endpoints. The pool MUST return only verified unique healthy endpoints without duplicate round-robin padding, ensuring each worker context maps 1-to-1 to a distinct out-of-state IP.

### FR-2: Default Bounded Startup Timeout (`max_wait_seconds`)
- **FR-2.1**: `StealthConnectionManager.start_pool()` MUST NOT wait indefinitely by default.
- **FR-2.2**: The default value for `max_wait_seconds` MUST be configurable via environment variable `STEALTH_MAX_WAIT_SECONDS`, with a production default of **60.0 seconds** (reduced to **0.01 seconds** in unit test environments).
- **FR-2.3**: If `max_wait_seconds` elapses and `len(active_slots) >= min_healthy`, the pool MUST log a warning and proceed immediately with the active forwarders.
- **FR-2.4**: If `max_wait_seconds` elapses and `len(active_slots) < min_healthy`, the system MUST raise a descriptive `RuntimeError` highlighting the shortfall and troubleshooting steps.

### FR-3: Cross-Step Proxy Pool Persistence & In-Process Health Verification
- **FR-3.1**: The pipeline orchestrator (`src/cli.py:run_weekly_advisory`) MUST instantiate a single, shared `StealthConnectionManager` instance inside a `try...finally` block immediately before Step 3 (Comp Scraping). Kivoya API ingestion (Step 1) and Calendar Segmentation (Step 2) MUST execute prior to proxy pool initialization so zero proxy connections are held open unnecessarily.
- **FR-3.2**: Connection verification MUST occur in-process during `shared_proxy_mgr.start_pool()` right before Step 3, directly sharing those exact verified connections with Step 3 and Step 4b.
- **FR-3.3**: Automated shell scripts (`run_daily_quickscan.sh` and `run_weekly_fullscan.sh`) MUST NOT execute an out-of-process `test-stealth` pre-flight check immediately before `run`. Doing so causes connection quota self-poisoning due to RADIUS accounting linger (RCA-6).
- **FR-3.4**: `AirbnbCollector` MUST accept an optional `proxy_mgr` parameter in its constructor. If provided, it MUST reuse the existing manager rather than creating a new one.
- **FR-3.5**: `PlatformComparator` MUST accept an optional `proxy_mgr` parameter in its constructor. If provided, it MUST reuse the existing manager and avoid calling `stop_pool()` during its internal `close_browser()`.
- **FR-3.6**: When using a shared proxy pool, `AirbnbCollector.close_browser()` and `PlatformComparator.close_browser()` MUST close browser contexts and Playwright browser instances, but MUST NOT stop the underlying proxy pool. The pool MUST be cleanly stopped once in the `finally:` block of `run_weekly_advisory()`.

### FR-4: Headroom Awareness & Default Connection Budget
- **FR-4.1**: The default maximum connections (`DEFAULT_MAX_STEALTH_CONNECTIONS`) MUST be lowered from **10** to **8** to guarantee 2 reserved slots for personal operator devices (phones, laptops).
- **FR-4.2**: The connection budget MUST remain overridable via the existing `STEALTH_MAX_CONNECTIONS` environment variable in `.env`.
- **FR-4.3**: All application callers (`AirbnbCollector`, `PlatformComparator`, `cli.py test-stealth`) MUST respect `STEALTH_MAX_CONNECTIONS` dynamically.

### FR-5: Dynamic Queue Adaptation for Variable Pool Sizes
- **FR-5.1**: `AirbnbCollector._worker_queue` and `PlatformComparator._context_queue` MUST dynamically adjust their concurrency to match the exact number of endpoints returned by `start_pool()`.
- **FR-5.2**: If 8 endpoints are provisioned, exactly 8 browser contexts MUST be created and placed in the worker queue. Context leasing (`lease_context()`, `lease_channel_context()`) MUST operate seamlessly with any pool size $\ge 1$.

### FR-6: Diagnostic CLI Quorum Tolerance & Reporting (`cli.py test-stealth`)
- **FR-6.1**: `cli.py test-stealth` MUST default its connection count to `DEFAULT_MAX_STEALTH_CONNECTIONS` (8), dynamically respecting `STEALTH_MAX_CONNECTIONS`.
- **FR-6.2**: `test-stealth` MUST evaluate health using resilient quorum: `min_healthy = max(3, count - 2)`.
- **FR-6.3**: When quorum is met ($\ge \text{min\_healthy}$), `test-stealth` MUST exit with status 0. If healthy nodes are below target count ($< \text{count}$) but meet quorum, it MUST display a visible warning banner (e.g. `⚠️ Quorum met: X/Y healthy nodes (minimum Z required). Pipeline ready to proceed.`).
- **FR-6.4**: `test-stealth` MUST exit with a non-zero error code only if healthy nodes fail to meet quorum ($< \text{min\_healthy}$).

---

## 5. Non-Functional Requirements (NFR)

### NFR-1: Performance & Execution Speed
- **NFR-1.1**: Pipeline startup delay between Step 3 and Step 4b MUST be reduced from ~380 seconds to **< 1 second** by eliminating pool recreation.
- **NFR-1.2**: Unit tests for proxy pool management MUST execute within **< 2.0 seconds** total, strictly adhering to the *Zero-Sleep Test Environment Invariant*.

### NFR-2: Reliability & Fault Tolerance
- **NFR-2.1**: Automated launchd daily quick scans MUST achieve $\ge 99.9\%$ completion reliability without operator intervention.
- **NFR-2.2**: The system MUST gracefully survive individual NordVPN node outages, DNS resolution failures, and SOCKS5 authentication resets by falling back to verified candidate nodes or proceeding with quorum.

### NFR-3: Security & Anti-Detection
- **NFR-3.1**: Out-of-state feeder hub isolation MUST remain strictly enforced. Local Phoenix nodes MUST be rejected unconditionally to prevent local competitor surveillance.
- **NFR-3.2**: Credentials (`NORDVPN_USER`, `NORDVPN_PASS`) MUST never be logged in plaintext, error traces, or investigation reports.

### NFR-4: Backward Compatibility
- **NFR-4.1**: Standalone scripts, CLI commands (`cli.py compare-platforms`, `cli.py enrich-listings`), and test suites that instantiate `AirbnbCollector()` or `PlatformComparator()` without passing `proxy_mgr` MUST continue to operate with standalone auto-managed proxy pools.

---

## 6. Success Metrics & Verification Criteria

| Metric | Current State (Pre-Fix) | Target State (Post-Fix) |
| :--- | :--- | :--- |
| **Step 3 $\to$ 4b Pool Transition Time** | 380 seconds (6m 20s stall) | **< 2 seconds** (Zero re-auth churn) |
| **Minimum Healthy Quorum** | Rigid 10/10 (100%) | **$\ge$ 8/10 (or 6/8) Quorum** |
| **Unattended Daily Quick Scan Duration** | 12m 22s | **< 6m 30s** |
| **Personal Device Account Collisions** | Immediate deadlock at 9/10 | **Zero collisions** (2-connection headroom) |
| **Default Startup Wait Timeout** | `None` (Infinite wait) | **60.0 seconds** (Bounded) |
| **Unit Test Suite Execution Time** | Clean / Fast (<3s) | **< 3.0s** (Zero-sleep preserved) |


