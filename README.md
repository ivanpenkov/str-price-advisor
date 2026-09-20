# 🏷️ STR Competitive Price Advisor: Villa del Sol

An autonomous AI agent system designed for **Villa del Sol** (920 E Carver Rd, Tempe, AZ) to track open reservations, monitor competitive luxury short-term rental properties ("comps") across the Tempe/Scottsdale/Chandler/Mesa/Gilbert corridor, calculate lead-time-adjusted percentile pricing, and generate prioritized weekly rate reports for your property manager (Kivoya).

---

## 🌟 Key Features

1. **Direct Kivoya / Streamline VRS API Integration (Zero Scraping for your property)**:
   - Queries `GetPropertyAvailabilityCalendarRawData` to retrieve exact, real-time unbooked and booked dates for Unit `503802` across the next 12 months.
   - Queries `GetPropertyRatesRawData` to extract current seasonal and holiday base rates directly from Kivoya's PMS.
2. **Intelligent Date Segmentation**:
   - Automatically splits open calendar periods into standard STR booking chunks:
     - **Weekends**: Thursday to Sunday (3 nights) or Friday to Sunday (2 nights).
     - **Mid-weeks**: Sunday to Thursday (4 nights) or open sub-intervals.
   - Computes days until check-in (lead time) and our effective guest cost (Base x Nights + \$500 Cleaning Fee).
3. **Multi-Tier Luxury Comp Intelligence**:
   - **Tier A (Direct Comps)**: 16+ guests, 6+ bedrooms, heated pool/spa, resort yards.
   - **Tier B (Secondary Comps)**: 12–15 guests, 5+ bedrooms, luxury estates.
   - Uses Playwright stealth automation with a **10-Worker Multi-IP NordVPN Stealth Connection Pool** (`src/stealth_connection.py`), routing requests across out-of-state feeder hubs with RFC 1928/1929 SOCKS5 authentication, dynamic candidate hot-swapping, zero Phoenix IP leakage, randomized human delays (3–7s), and local disk caching to prevent redundant requests and protect IP reputation.
4. **Dynamic Lead-Time Pricing Engine**:
   - Benchmarks against the luxury comps with dynamic lead-time and day-of-week tapering.
   - **Lead-time tapering curve**:
     - `> 180 days`: 70th percentile (capture early high-intent bookers).
     - `60 – 180 days`: 65th percentile (standard prime booking window).
     - `30 – 60 days`: 55th percentile (tapering to encourage booking).
     - `< 30 days`: 45th percentile (last-minute booking conversion).
     - *Midweek*: 30% discount curve (49th, 45.5th, 38.5th, and 31.5th percentiles).
   - Translates competitive effective total guest cost back into the **recommended base nightly rate** (accounting for our $500 cleaning fee).
5. **Actionable 3-Tier Reporting**:
   - **Section 1: Urgent Action Required (Weekly)**: Intervals where current price is >25% off market or imminent arrival (<60 days).
   - **Section 2: Moderate Adjustments (Monthly Review)**: Intervals 10%–25% off target for intermediate dates (60–180 days).
   - **Section 3: Informational Monitoring**: Complete 12-month calendar and benchmarks.
   - **Outputs**: Formatted Markdown summary (for Email/Google Doc) + CSV table (for Google Sheets/Excel).

---

## 🚀 Quickstart & Usage

### 1. Environment Setup
```bash
# Navigate to directory and activate virtual environment
cd /Users/ivanpe/str-price-advisor
source .venv/bin/activate

# Install dependencies and Playwright browser binaries
pip install -r requirements.txt
playwright install chromium
```

### 2. Audit Stealth Multi-IP NordVPN Connection Pool
```bash
# Verify 10-node out-of-state proxy fleet and latency
python -m src.cli test-stealth --count 10
```

### 3. Verify Kivoya & Streamline API Connection
```bash
python -m src.cli test-kivoya
```

### 4. Bootstrap / Evaluate Comp Registry
```bash
# Discover initial luxury competitors in Phoenix East Valley
python -m src.cli bootstrap-comps --limit 30

# Evaluate comps using the 6-factor luxury rubric ($2.0M baseline valuation)
python -m src.cli evaluate-comps
```

### 5. Run Weekly Price Advisory Audit
```bash
# Run quick audit on upcoming intervals (e.g. next 12 intervals)
python -m src.cli run --quick --limit 12

# Run full 12-month annual scan with cross-platform channel comparison
python -m src.cli run --weekly --compare-platforms
```

### 6. Track Competitor Sales & Absorption Velocity
```bash
# Detect comp bookings via snapshot diffing and compute 2D strategy grid
python -m src.cli track-competitor-sales --dashboard
```

### 7. Re-generate or Refresh HTML Dashboard
```bash
python -m src.cli generate-html
```

---

## 📁 Output Reports & Static Dashboard

Every run updates both static reporting documents and a responsive 9-tab web dashboard:
- **Interactive Web Dashboard (`docs/index.html`)**:
  - **Tab 1 (📊 Pricing)**: 🚨 Urgent Actions (Weekly) &rarr; ⚠️ Moderate Adjustments (Monthly) &rarr; ℹ️ Full 12-Month Calendar with lead-time percentiles and historical realized benchmarks.
  - **Tab 2 (⭐ Reviews)**: Cross-platform reputation surveillance across Airbnb, VRBO, Booking.com, and Kivoya Direct with multi-interval date filters and side-by-side team responses.
  - **Tab 3 (⚙️ Streamline PMS)**: Rate management diagnostics, published base rates, and direct channel markup auditing.
  - **Tab 4 (🏡 Comps)**: 109 curated competitors (97 active valid comps + 12 disqualified) with search, tier filter pills, and direct **"Open on Airbnb ↗"** links.
  - **Tab 5 (🎯 Comps Sales)**: 2D empirical strategy matrix (Lead Horizon $\times$ Stay Type), absorption velocity KPIs, and live competitor booking transaction feed.
  - **Tab 6 (📅 Calendar)**: 12-month interactive rolling calendar grid with dual color modes (Status vs. Channel) and modal reservation inspection.
  - **Tab 7 (📑 Reservations)**: Complete 2022–2027 historical and advance reservation ledger with search, filtering, and sorting.
  - **Tab 8 (📈 Revenue)**: Cumulative annual revenue pacing curves (2022–2027) with YoY comparisons and executive KPIs.
  - **Tab 9 (🌐 Channels)**: Real-time price parity comparison matrix across Airbnb, VRBO, Booking.com, and Kivoya Direct with full fee and tax itemization.
- **`data/latest_report.md`**: Executive markdown summary with warning badges.
- **`data/latest_sheet.csv`**: Structured spreadsheet for importing directly into Google Sheets or Kivoya.

---

## 🌐 Hosting on GitHub Pages

The dashboard is generated into `docs/index.html` and is designed for direct GitHub Pages hosting:
1. On GitHub, go to your repository: [github.com/ivanpenkov/str-price-advisor](https://github.com/ivanpenkov/str-price-advisor)
2. Click **Settings** &rarr; **Pages** (on the left sidebar).
3. Under **Build and deployment**:
   - **Source**: Select **Deploy from a branch**.
   - **Branch**: Select **`main`** and choose the folder **`/docs`**.
   - Click **Save**.
4. GitHub Pages will publish your dashboard at your personal GitHub URL!
5. You can also view it locally anytime by opening `docs/index.html` on your Mac.

---

## 🤝 Contributing & Multi-Device Setup

Setting up a new development machine or collaborating with another developer?
Check out our comprehensive **[Contributing & Multi-Device Setup Guide](CONTRIBUTING.md)**, which covers:
- **System Architecture & Machine Roles**: Understanding the boundary between the 24/7 production host (Mac Mini running automated `launchd` daemons) and contributor laptops.
- **Initial Device Setup**: Step-by-step repository cloning, Python virtual environment configuration, and Playwright Chromium installation.
- **Secure Secret Bootstrapping**: Safely transferring `.env` credentials (NordVPN & Streamline OwnerX PMS) peer-to-peer via AirDrop, SCP, or password managers using the tracked [`.env.example`](.env.example) template.
- **Local Database Initialization**: Initializing `data/reservations.db` from Streamline PMS (and our roadmap toward a centralized cloud database).
- **Smoke Testing & Verification**: Inner-loop unit tests, stealth proxy fleet audits, and dry run checks.
- **Two-Contributor Git Workflow**: Short-lived feature branches, rebasing against automated Mac Mini commits, solo merge protocols, and merge conflict resolution recipes for generated artifacts (`docs/index.html`).


