# STR Price Advisor — Antigravity Engineering Guidelines & Operational Rules

These rules govern all automated development, bug fixes, refactors, and feature implementations within the `str-price-advisor` repository. Agents operating in this repository must follow these rules without exception.

---

## 1. Mandatory Subagent Code Review Protocol

Before declaring any non-trivial code modification, bug fix, refactor, or feature implementation complete (and before requesting final user review or presenting results), you MUST execute the multi-round subagent code review workflow defined in the `code-review` skill (`.agents/skills/code-review/SKILL.md`):

1. **Automated Subagent Review with Clean Context Invariant**:
   - **Clean Context Requirement**: Every review round MUST be conducted by a **freshly spawned subagent** (`Model: 'inherit'`, `TypeName: 'research'` or dedicated reviewer) with a completely clean context window and brand-new conversation transcript.
   - **No Agent Reuse (`send_message`)**: NEVER use `send_message` to follow up or request subsequent review rounds from an existing subagent. Reusing conversation IDs carries conversational baggage, token bloat, and confirmation bias.
   - **Zero Cross-Round Context Leakage**: DO NOT pass previous review transcripts, prior reviewer verdicts, or conversational debate into subsequent review prompts. The reviewer must evaluate the current state of code and diff with 100% objective, unprimed, independent scrutiny.
   - **Hermetic Payload**: Provide the reviewer strictly with: (a) task/CL description, (b) implementation plan, (c) full unified `git diff`, and (d) automated test suite results.
2. **Strict 4-Tier Rubric & Structured Output**:
   - The subagent must evaluate code against correctness, edge cases, regressions, and test coverage, rating issues as `[BLOCKER]`, `[MAJOR]`, `[MINOR]`, or `[NIT]` with clickable file/line links (`[file:L10-L20](file://...)`), clear problem statements, proposed solutions, and an overall verdict (`APPROVE` or `REQUEST_CHANGES`).
3. **Conditional Multi-Round Review Loop (Up to 3 Cycles)**:
   - **Round 1 Completion if Approved**: If Round 1 returns `APPROVE` with 0 unresolved issues, the code review is complete immediately. A second round is NOT required.
   - **Round 2 Trigger (Only if Round 1 Did Not Approve)**: A second round of code review is executed **only if Round 1 did not approve** (i.e., returned `REQUEST_CHANGES` or identified issues requiring fixes). In that case, address the feedback, re-run all automated tests, and spawn a **fresh, new subagent** with completely clean context (`Role: "Code Reviewer Round 2"`) without passing prior round conversations, evaluating the updated diff.
   - If Round 2 returns `APPROVE` with 0 unresolved issues, the review is complete.
   - **Round 3 Contingency**: If issues persist or new issues are introduced in Round 2, address them, re-run tests, and spawn `Role: "Code Reviewer Round 3"` with clean context for a 3rd and final convergence cycle.
4. **Transparency & Documentation**:
   - Provide a concise summary of the review trajectory in the chat response.
   - Document the full review findings, severities, and resolutions in the `walkthrough.md` artifact under a dedicated "Subagent Code Review" section.

---

## 2. Fast Development, Targeted Testing, and Zero-Sleep Environment

To maintain high development velocity and prevent idle delays during automated coding, follow these operational efficiency rules:

### 1. Targeted Test Execution (Inner-Loop Iteration)
- **DO NOT** run the full repository test suite (`unittest discover tests`) during iterative feature coding, bug fixes, or minor tweaks.
- **ALWAYS** run only the targeted test file or method corresponding to the code being touched:
  - Single file: `.venv/bin/python -m unittest tests/test_foo.py` (executes in 0.2s–2.0s).
  - Specific test method: `.venv/bin/python -m unittest tests.test_foo.TestClass.test_method` (executes in <0.2s).
- **Milestone Gating**: Execute the full test suite (`unittest discover tests`) ONLY at two specific milestones:
  1. Once immediately prior to dispatching Subagent Code Review Round 1.
  2. Once during final end-to-end verification before presenting results to the user.

### 2. Zero-Sleep Test Environment Invariant
- Unit tests must NEVER execute real wall-clock sleeps (`asyncio.sleep()`, `time.sleep()`, socket timeouts) when running against mocks.
- Any delays, backoffs, or retry timeouts in production code must be configurable or read from environment variables with zero-delay test defaults:
  - `STEALTH_STARTUP_DELAY=0.0` (eliminates proxy forwarder wait in unit tests).
  - `retry_delay=0.0` and `sequential_delay=0.0` in `PlatformComparator` (eliminates scrape retry backoffs).
  - `pdp_timeout=0.05` in `AirbnbCollector` (eliminates wait for PDP responses in mocks).
- When writing new async workflows, always expose delay parameters to ensure unit test suites complete in milliseconds.

### 3. High-Efficiency Tool Parameter Protocol
- **`run_command`**:
  - For quick shell commands, scripts, or targeted unit tests expected to finish in under 25 seconds, specify `WaitMsBeforeAsync: 25000` (up to 30000ms). This allows the command to return synchronously, avoiding background task creation, scheduling overhead, and status polling.
  - When launching truly long-running commands (e.g. multi-interval live scraping), set `WaitMsBeforeAsync: 500` or `1000` to background them immediately, and stop calling tools to rely on reactive wakeup.
- **`view_file`**:
  - Maximize view ranges: Inspect up to 800 lines in a single call instead of performing sequential 100-line micro-reads across the same file.
- **`replace_file_content`**:
  - Group contiguous related changes into a single edit chunk rather than issuing multiple sequential microscopic edits.
  - Include 2–3 lines of unique surrounding context to guarantee chunk match uniqueness on the first try.

---

## 3. File Inspection Best Practice

- When viewing or inspecting parts of a file, prefer the built-in `view_file` tool with `StartLine` and `EndLine` parameters rather than running shell commands like `sed`, `cat`, `head`, or `tail`.

---

## 4. Task Completion Mobile Notification Protocol

To ensure the user is promptly alerted on their mobile phone when stepping away from their desk during long-running tasks:

### 1. Notification Trigger Conditions
The agent MUST send a push notification to the user's mobile phone via `scripts/notify_mobile.sh` (or `~/.gemini/config/scripts/notify_mobile.sh`) under any of the following conditions:
1. **Duration Threshold**: Any non-trivial task, feature implementation, refactor, debugging session, or multi-step workflow taking $> 45$ seconds of clock time or $> 2$ conversation turns to complete.
2. **Autonomous & Batch Execution**: Any run involving `/goal`, full test suite execution, multi-round subagent code review, or multi-interval market scrape/pipeline execution.
3. **Explicit User Request**: Whenever the user explicitly asks to be alerted, notified, or messaged when done.

**DO NOT** send notifications for quick, trivial single-turn questions (e.g., "where is X defined?", "show line 20", minor 5-second queries) when the user is actively at their desk.

### 2. Notification Execution
Before presenting the final response to the user, execute the notification script:
```bash
scripts/notify_mobile.sh \
  --title "Antigravity: Task Complete" \
  --message "✅ [Brief 1-2 line summary of what was accomplished and duration]. Ready for review at your desk." \
  --tags "white_check_mark,rocket"
```

### 3. Configuration & Delivery Channel
- **Service**: `ntfy.sh` (Free, open-source mobile push notification service).
- **Default Topic**: `ivan-str-advisor-xyz` (overridable via `NTFY_TOPIC`).
- **Client App**: Free `ntfy` app on Android or iOS subscribed to topic `ivan-str-advisor-xyz`.

