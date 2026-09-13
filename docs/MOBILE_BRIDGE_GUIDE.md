# Antigravity Mobile Controller — User Guide
**How to Control Antigravity & the STR Price Advisor from Your Phone**

---

## 1. Introduction

The **Antigravity Mobile Controller** lets you interact with Google Antigravity and the Villa del Sol STR Price Advisor directly from your mobile phone (Android or iOS) whenever you step away from your desk.

Whether you are running errands, traveling, or checking on property maintenance, you can:
- ⚡ **Run instant health checks** (`/status`) in under 1 second.
- 🏷️ **Inspect recent competitor bookings and sales** (`/sales`).
- 🔄 **Trigger remote automations** like live calendar verification (`sales-verify`), reservation syncing, or dashboard rebuilds.
- 🧠 **Ask complex natural language questions** to the Antigravity AI Agent (`agy`) with full access to the project database and code.

---

## 2. One-Time Setup (2 Minutes)

You will need the free, open-source **ntfy** app installed on your mobile device.

### Step 1: Install the `ntfy` App
- **Android**: Install [ntfy from Google Play](https://play.google.com/store/apps/details?id=io.heckel.ntfy) or F-Droid.
- **iOS**: Install [ntfy from the Apple App Store](https://apps.apple.com/app/ntfy/id1625396347).

### Step 2: Subscribe to Your 2 Topics
In the `ntfy` app, you will configure two topics:

```
┌────────────────────────────────────────────────────────┐
│ 1. Inbound Command Topic (Where you type commands)      │
│    ivan-str-cmd-8e2b4f91                               │
├────────────────────────────────────────────────────────┤
│ 2. Outbound Alert Topic (Where you receive answers)    │
│    ivan-str-advisor-xyz                                │
└────────────────────────────────────────────────────────┘
```

1. Open the `ntfy` app.
2. Tap the **`+`** button (bottom right) to add a topic.
3. Enter `ivan-str-cmd-8e2b4f91` and tap **Subscribe**.
   - *This is your private remote control channel.*
4. If you have not already done so, tap **`+`** again and subscribe to `ivan-str-advisor-xyz`.
   - *This is your notification and results tray.*

---

## 3. How to Send Commands & Prompts

### Typing a Message
1. Open the `ntfy` app.
2. Tap on the **`ivan-str-cmd-8e2b4f91`** topic.
3. Tap the text box at the bottom, type your command (e.g. `status` or `/status`), and tap **Send**.

### Dictating Voice Commands
You can dictate requests hands-free using your phone's keyboard microphone:
1. Tap the text box in `ivan-str-cmd-8e2b4f91`.
2. Tap the **Microphone** icon on your Google Gboard / iOS keyboard.
3. Speak your prompt: *"What were our last three competitor sales recorded?"*
4. Tap **Send**.

---

## 4. Understanding the Feedback Loop

Every time you send a request, you will receive two notifications in your **`ivan-str-advisor-xyz`** tray:

```
[1. Instant Acknowledgment — < 1.0s]
⏳ Antigravity: Command Received
Processing: "/status"...
------------------------------------------------------------
[2. Final Result Delivery — 0.2s to 10s]
✅ Antigravity: CLI: status (0.22s)
🏰 Villa del Sol — STR Advisor Status [2026-09-13]
📊 Comps Registry: 97 active comps...
🏷️ Competitor Sales: 19 confirmed sales...
```

- **Tag Icons**:
  - `⏳` `⚙️` : Command received, Mac is actively processing.
  - `✅` `🚀` : Task completed successfully.
  - `⚠️` `❌` : Error occurred (includes failure traceback).

---

## 5. Direct CLI Shortcuts Cheat Sheet (Instant Execution)

Shortcuts execute directly on your Mac in **0.1s – 3.0s** without waiting for AI reasoning. You can type them with or without a leading slash (`/`):

| Shortcut | What It Does | Average Speed |
| :--- | :--- | :---: |
| **`status`** or **`/status`** | Complete system health: active comps, confirmed sales count, median absorption percentile, upcoming reservations, next arrival, and latest snapshot. | ~0.2s |
| **`sales`** or **`/sales`** | Competitor sales ledger and absorption velocity summary across all lead-time horizons. | ~0.3s |
| **`sales-verify`** | Runs a live NordVPN Playwright calendar verification sweep across all candidate competitor sales and refreshes the dashboard. | ~60s |
| **`audit`** or **`/audit`** | Audits active competitor listings against the 6-factor luxury rubric (flags low ratings, high capacity, or missing pool amenities). | ~0.5s |
| **`dashboard`** | Recompiles and publishes the interactive static dashboard (`docs/index.html`). | ~1.5s |
| **`sync-reservations`** | Connects to Streamline OwnerX PMS and synchronizes newly booked intervals. | ~3.0s |
| **`test-kivoya`** | Tests Kivoya PMS API connectivity and audits published nightly rates. | ~2.0s |
| **`test-stealth`** | Audits health and latency of the parallel NordVPN stealth SOCKS5 proxy pool. | ~3.0s |
| **`help`** or **`/help`** | Displays the interactive command menu directly on your phone. | ~0.0s |

### Example: Running `/status`
Send:
```text
/status
```
Receive:
```text
🏰 Villa del Sol — STR Advisor Status [2026-09-13]
=======================================================
📊 Comps Registry: 97 active comps (Tier A: 49, Tier B: 48 | Disqualified: 6, Excluded: 6)
🏷️ Competitor Sales: 19 confirmed sales in DB (Median Pct: 61.5%)
   Recent (30d): 19 sales detected
📅 Reservations: 21 upcoming booked intervals in DB
   Next Booking: 2026-09-10 -> 2026-09-13 (3n, $1,647.00)
📸 Latest Snapshot: pricing_data_2026-09-13.json
=======================================================
```

---

## 6. Talking to the Antigravity AI Agent (`agy`)

Any message that does **not** match a shortcut keyword is automatically forwarded to the **Antigravity AI Agent**. 

The agent runs with **full repository context**, autonomous tool access (reading/writing files, querying SQLite, running git), and **multi-turn conversation continuity** (it remembers what you discussed earlier).

### Example Prompts You Can Send

#### 1. Competitor Analysis & Inquiries
- *"Why was comp 1520493073616950985 excluded from the sales ledger?"*
- *"Show me all Tier A competitor sales booked more than 90 days in advance."*
- *"Which competitor in Chandler had the highest realized nightly rate this season?"*

#### 2. Pricing & Consensus Questions
- *"What is our consensus recommended price for the first weekend of November?"*
- *"Did the market compression surge trigger for Spring Training 2027?"*

#### 3. Quick Calculations & Mathematical Checks
- *"If our average cleaning fee is $500 and Kivoya takes 18%, what is our net payout on a $4,500 booking?"*
- *"Calculate the quality-adjusted rate for a comp selling at $1,800 with desirability ratio 1.12."*

#### 4. Git & Codebase Inquiries
- *"What were the last 2 git commits on main?"*
- *"Are there any untracked files or unstaged changes in the repo?"*

---

## 7. Managing the Service on Your Mac

The bridge daemon runs quietly in the background via macOS `launchd`. It starts automatically when you log into your Mac and stays alive 24/7.

You can manage it using the built-in management script from your Mac terminal:

```bash
# Check service health and view the last 15 log entries
bash scripts/install_mobile_bridge.sh --status

# Stream live bridge activity in real time
bash scripts/install_mobile_bridge.sh --logs

# Restart the service (e.g. after updating code)
bash scripts/install_mobile_bridge.sh --start

# Stop the service
bash scripts/install_mobile_bridge.sh --stop

# Completely uninstall the LaunchAgent
bash scripts/install_mobile_bridge.sh --uninstall
```

### Log File Locations
- **Standard Output**: `~/Library/Logs/str-price-advisor/mobile_bridge.log`
- **Error Output**: `~/Library/Logs/str-price-advisor/mobile_bridge.err`

---

## 8. Troubleshooting & FAQs

### Q: I sent a command, but I didn't get an ACK notification on my phone.
1. **Check if the service is running on your Mac**:
   ```bash
   bash scripts/install_mobile_bridge.sh --status
   ```
   If it is not running, start it:
   ```bash
   bash scripts/install_mobile_bridge.sh --start
   ```
2. **Check your Mac's internet connection**: Ensure your Mac is online and connected to Wi-Fi.
3. **Verify topic spelling**: Confirm that your app is sending to `ivan-str-cmd-8e2b4f91` and subscribed to `ivan-str-advisor-xyz`.

### Q: Does this work when my Mac screen is locked or in sleep mode?
- **Lock Screen**: Yes! As long as the Mac is powered on and logged in, `launchd` keeps the daemon running.
- **System Sleep**: If your Mac enters deep sleep (hibernation), macOS suspends background network processes. When you wake the Mac (or if you configure *System Settings > Energy Saver > Prevent automatic sleeping when display is off*), the bridge automatically reconnects to the stream within 3 seconds.

### Q: How do I change my private command topic name?
If you ever want to change your private inbound topic:
1. Open `.env` in the project root:
   ```env
   NTFY_INBOUND_TOPIC=my-new-secret-topic-12345
   ```
2. Restart the service:
   ```bash
   bash scripts/install_mobile_bridge.sh --start
   ```
3. Subscribe to `my-new-secret-topic-12345` in your mobile `ntfy` app.

### Q: Will my mobile device get an echo notification for its own commands?
No! Commands are published to `ivan-str-cmd-8e2b4f91` (inbound only). Responses are posted to `ivan-str-advisor-xyz` (outbound alerts). Because the two topics are separate, your phone will never receive an echo alert for typing a command.

