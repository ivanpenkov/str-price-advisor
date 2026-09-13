# Antigravity Two-Way Mobile ntfy Bridge
**Product Requirements Document (PRD) & Functional Specification**

---

## 1. Executive Summary & Purpose

### 1.1 The Operational Problem
Managing luxury short-term rentals (STR) like Villa del Sol requires rapid situational awareness and decision-making. Market conditions change dynamically: competitors drop rates, calendars compress during high-demand event weekends, and guest booking inquiries require immediate validation against comps.

However, the primary analytical and automation tools—the STR Price Advisor pricing engine, competitive scrapers, Playwright calendar verification, and the Google Antigravity AI Agent (`agy`)—have historically run **exclusively at a physical desktop workstation**. 

When an operator steps away from their desk (e.g. running errands, traveling, or attending to property maintenance), they have traditionally had:
1. **One-Way Alerting Only**: The desktop could send push notifications (via `notify_mobile.sh`), but the operator could not reply or issue instructions.
2. **Execution Blindness**: No quick way to query live occupancy, recent competitor sales, or current pricing consensus without physically returning to the terminal or laptop.
3. **Delayed Action**: Inability to initiate calendar verification sweeps, trigger reservation syncs, or ask the AI agent to investigate market anomalies while on the move.

### 1.2 The Solution: Two-Way Mobile Control
The **Antigravity Two-Way Mobile ntfy Bridge** establishes a lightweight, bi-directional command and notification pipeline connecting the operator's mobile phone (Android / iOS) directly to the Antigravity AI developer engine and the STR Price Advisor CLI on macOS.

Using the free, open-source `ntfy` mobile client, the operator can send simple text messages or dictated voice notes to a private, secure topic. A continuous background daemon on macOS intercepts the message, classifies it via a **Hybrid Smart Router**, executes either instantaneous deterministic CLI commands or the autonomous Antigravity AI Agent (`agy`), and streams rich markdown responses back to the mobile notification tray.

$$\text{Mobile Phone (ntfy)} \quad \underset{\text{Direct Responses}}{\overset{\text{Inbound Prompts}}{\rightleftharpoons}} \quad \text{macOS launchd Daemon} \quad \underset{\text{Agent Reasoning}}{\overset{\text{CLI Execution}}{\rightleftharpoons}} \quad \text{STR Advisor \& agy}$$

---

## 2. Target User Persona & Core User Stories

### 2.1 Persona
- **Role**: Villa del Sol Property Owner & STR Revenue Manager.
- **Context**: Often mobile or away from the workstation, managing multiple responsibilities while monitoring luxury booking pace.
- **Preferences**:
  - Zero-latency responses for frequent status and sanity checks.
  - Zero-maintenance background service (survives Mac reboots, sleeps, and network drops).
  - High signal-to-noise mobile notifications (instant receipt acknowledgment + clean, non-flooding summaries).
  - Natural language flexibility to instruct the AI agent when unusual questions arise.

### 2.2 User Stories

1. **Instant Operational Health Check**:
   > *"As an owner stepping away from my desk, I want to type `/status` on my phone and receive an instant summary of active comps, confirmed competitor sales, upcoming guest arrivals, and the latest pricing snapshot within 2 seconds."*

2. **Autonomous Anomaly Investigation**:
   > *"As an owner noticing an unbooked weekend, I want to dictate a voice message like 'Why did comp 1400243019671565638 get booked while Villa del Sol was open?' and have the Antigravity agent inspect the database, compute the lead time and quality adjustment, and text me the answer."*

3. **Remote Automation Triggers**:
   > *"As an owner monitoring market compression, I want to trigger a live calendar verification sweep (`sales-verify`) or rebuild the static web dashboard (`dashboard`) directly from my phone."*

4. **Instant Receipt Peace of Mind**:
   > *"As an owner sending an instruction while in transit, I want an immediate push notification confirming the Mac received my request, followed by a final completion alert when the task is done."*

---

## 3. High-Level System Architecture

```mermaid
flowchart TD
    subgraph MobileDevice ["Mobile Device (Android / iOS)"]
        User["Owner / Operator"]
        NtfyApp["ntfy App"]
        CmdInput["Command Input Topic<br/>(ivan-str-cmd-8e2b4f91)"]
        AlertTray["Notification Alert Tray<br/>(ivan-str-advisor-xyz)"]
    end

    subgraph CloudRelay ["Cloud Messaging (ntfy.sh)"]
        InboundSSE["Inbound Streaming Endpoint<br/>(https://ntfy.sh/.../json)"]
        OutboundPOST["Outbound Publish Endpoint<br/>(https://ntfy.sh/...)"]
    end

    subgraph HostMac ["Local macOS Host"]
        Launchd["macOS launchd Service<br/>(com.villasol.mobile-ntfy-bridge)"]
        Daemon["mobile_ntfy_bridge.py<br/>(Listener & Stream Manager)"]
        
        subgraph Router ["Hybrid Smart Router"]
            Classifier{"Keyword / Slash<br/>Match?"}
            FastCLI["Deterministic CLI Engine<br/>(.venv/bin/python -m src.cli ...)"]
            AgyAgent["Autonomous Antigravity Agent<br/>(agy -c -p '...')"]
        end
        
        DataStore[("SQLite & Registries<br/>reservations.db | comps_registry.json")]
    end

    User -->|Type or Dictate| NtfyApp
    NtfyApp -->|HTTP POST| CmdInput
    CmdInput --> InboundSSE
    InboundSSE -->|Persistent SSE Stream| Daemon
    Launchd -.->|KeepAlive / Auto-Restart| Daemon
    
    Daemon -->|1. Immediate ACK| OutboundPOST
    Daemon -->|2. Route Message| Classifier
    
    Classifier -->|Shortcuts (status, audit, sales)| FastCLI
    Classifier -->|Natural Language Prompts| AgyAgent
    
    FastCLI <--> DataStore
    AgyAgent <--> DataStore
    
    FastCLI -->|Command Output| Daemon
    AgyAgent -->|Agent Response| Daemon
    
    Daemon -->|3. Completed Payload| OutboundPOST
    OutboundPOST --> AlertTray
    AlertTray -->|Instant Push Notification| User
```

---

## 4. Functional Requirements

### 4.1 Inbound Communication & Private Topic Gating
- **FR-1.1 Private Command Topic**: The system must listen on a high-entropy, unguessable inbound topic name (default: `ivan-str-cmd-8e2b4f91`) separate from the public or alert topics.
- **FR-1.2 Configuration & Environment Precedence**:
  - The topic name must be configurable via `config/mobile_bridge.json`.
  - Environment variable `NTFY_INBOUND_TOPIC` in `.env` or the system environment must take precedence over configuration files.
  - Sourcing `.env` must occur automatically on service startup without requiring manual export.
- **FR-1.3 Event Deduplication**: The bridge must track message IDs in an in-memory set to prevent duplicate execution of re-transmitted payloads.
- **FR-1.4 Historical Event Discard**: The daemon must record its startup timestamp and ignore any messages buffered in ntfy queues from before daemon initialization (with a 5-second tolerance).

### 4.2 Hybrid Smart Router
- **FR-2.1 Keyword Normalization**:
  - Input tokens must be case-insensitive.
  - Leading slashes must be automatically stripped (e.g. `/status` and `status` must behave identically).
- **FR-2.2 Deterministic CLI Shortcut Dispatching**:
  - Pre-registered keyword commands must execute immediately via direct subprocess calls to the repository's virtual environment (`.venv/bin/python -m src.cli ...`).
  - Required core shortcuts:
    - `status`: Comprehensive portfolio, reservations, sales, and snapshot summary.
    - `audit`: Competitor portfolio luxury rubric audit.
    - `sales`: Competitor sales ledger and absorption velocity summary.
    - `sales-verify`: Full live NordVPN Playwright calendar verification sweep across candidate dropouts.
    - `dashboard`: Static HTML dashboard generation (`docs/index.html`).
    - `sync-reservations`: Streamline OwnerX PMS reservation scrape and sync.
    - `test-kivoya`: Kivoya PMS API and rate sync validation.
    - `test-stealth`: NordVPN stealth SOCKS5 proxy connection pool health audit.
    - `help`: Formatted command reference table.
  - Any extra arguments passed after a shortcut keyword (e.g. `audit --no-save`) must be forwarded to the CLI subprocess.
- **FR-2.3 Autonomous Antigravity AI Agent Dispatching**:
  - Any message that does not match a registered shortcut must be automatically routed to the Antigravity AI Agent (`/Users/ivanpe/.local/bin/agy`).
  - Invocations must include:
    - `--continue` (`-c`): Maintain multi-turn conversational context with previous sessions.
    - `--print` (`-p`): Run non-interactively in headless mode.
    - `--dangerously-skip-permissions`: Auto-approve tool execution (file inspection, test execution, database querying) while the user is away from the physical keyboard.
- **FR-2.4 Timeout & Process Safety**:
  - All subprocesses (CLI and agent) must enforce a configurable timeout (default: 300 seconds) to prevent frozen headless processes.
  - Non-zero process exit codes must capture and preserve `stderr` diagnostic traces in the final response.

### 4.3 Mobile Feedback Loop & Push Notifications
- **FR-3.1 Immediate Acknowledgment (ACK)**:
  - Within 1.0 second of message ingestion, the daemon must fire an ACK push notification to the user's alert topic (`ivan-str-advisor-xyz`).
  - ACK must display a truncated preview of the incoming command: `⏳ Processing: "<prompt>"...`.
  - Tags: `hourglass_flowing_sand,gear`.
- **FR-3.2 Completed Result Delivery**:
  - Upon process completion, the daemon must dispatch the full output payload to `ivan-str-advisor-xyz`.
  - Header: `Antigravity: <Title> (<duration>s)`.
  - Success Tags: `white_check_mark,rocket`, Priority: `default`.
  - Failure Tags: `warning,x`, Priority: `high`.
- **FR-3.3 Safe Output Truncation**:
  - The notification message must be bounded to a safe threshold (3,800 characters, guarded against negative offsets) to prevent rejection by the `ntfy.sh` server (4,096-byte limit).
  - Truncated payloads must clearly append `\n\n... [Output truncated to 3,800 characters]`.

### 4.4 Service Lifecycle & Host Integration
- **FR-4.1 macOS launchd Service**:
  - Must run as a persistent user LaunchAgent (`~/Library/LaunchAgents/com.villasol.mobile-ntfy-bridge.plist`).
  - Configured with `RunAtLoad: true` and `KeepAlive: true`.
  - Must auto-recover and reconnect seamlessly after system sleep, lock screen, or network interruptions.
- **FR-4.2 Unbuffered Logging**:
  - Python execution must enforce unbuffered stdout (`PYTHONUNBUFFERED=1` and `python3 -u`).
  - Standard output and error must log to `~/Library/Logs/str-price-advisor/mobile_bridge.log` and `.err`.
- **FR-4.3 Service Manager CLI**:
  - Provide a management script (`scripts/install_mobile_bridge.sh`) supporting `--install`, `--start`, `--stop`, `--status`, `--logs`, and `--uninstall`.

---

## 5. Non-Functional Requirements

### 5.1 Latency & Performance
- **Deterministic CLI Shortcuts**: Execution latency must be $< 0.5$ seconds for status queries, and $< 3.0$ seconds for full PMS audits.
- **ACK Delivery**: Must arrive on the mobile device within 1.0 second of sending the prompt.
- **Autonomous Agent**: Simple queries should return in $< 8.0$ seconds; complex multi-tool workflows within the 300-second timeout.

### 5.2 Reliability & Fault Tolerance
- **Resilient Reconnection**: The streaming listener must catch socket disconnects, HTTP errors, and timeouts, applying exponential backoff (`delay = min(delay * 1.5, 30.0)`) and resetting backoff upon successful connection.
- **Zero-Sleep Test Environment Invariant**: Unit test suites must never execute real wall-clock sleeps or external HTTP calls; all mock responses must execute in $< 0.1$ seconds.

### 5.3 Security & Topic Isolation
- Inbound topic names must possess high entropy ($>32$ bits of randomness) to guarantee security through obscurity on public ntfy relays.
- Local command injection risks must be eliminated by passing argument lists rather than raw shell strings (`shell=False` in all `subprocess.run` calls).
- Sensitive environment variables (`KIVOYA_SECRET`, `NORDVPN_PASS`, `STREAMLINE_API_KEY`) must remain isolated within `.env` and never leaked into notification payloads.

---

## 6. Success Metrics & Acceptance Criteria

| Metric | Target Goal | Validation Method |
| :--- | :--- | :--- |
| **Shortcut Response Time** | $\le 0.5\text{s}$ for `status` / `help` | Verified via timestamped bridge logs |
| **ACK Notification Latency** | $\le 1.0\text{s}$ from prompt dispatch | End-to-end mobile receipt audit |
| **Service Uptime & KeepAlive** | 100% auto-recovery on crash/sleep | `launchctl list \| grep mobile-ntfy-bridge` |
| **Unit Test Suite Speed** | $< 0.15\text{s}$ across 21+ unit tests | `python -m unittest tests/test_mobile_ntfy_bridge.py` |
| **Full Regression Suite** | 324 / 324 passing tests (0 failures) | Milestone test discovery execution |
| **End-to-End Success Rate** | 100% delivery for valid prompts | Verified across CLI shortcuts and LLM agent queries |

