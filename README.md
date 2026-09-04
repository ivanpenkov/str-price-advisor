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
   - Uses Playwright stealth automation with randomized human delays (3–7s) and local disk caching to prevent redundant requests and protect IP reputation without third-party proxy subscriptions.
4. **Dynamic Lead-Time Pricing Engine**:
   - Benchmarks against the **75th–80th percentile** of luxury comps.
   - **Lead-time tapering curve**:
     - `> 180 days`: 82nd percentile (capture early high-intent bookers).
     - `60 – 180 days`: 78th percentile (standard prime booking window).
     - `30 – 60 days`: 72nd percentile (tapering to protect occupancy).
     - `< 30 days`: 65th percentile (last-minute booking conversion).
   - Translates competitive effective total guest cost back into the **recommended base nightly rate** (accounting for our \$500 cleaning fee).
5. **Actionable 3-Tier Reporting**:
   - **Section 1: Urgent Action Required (Weekly)**: Intervals where current price is >25% off market or imminent arrival (<60 days).
   - **Section 2: Moderate Adjustments (Monthly Review)**: Intervals 10%–25% off target for intermediate dates (60–180 days).
   - **Section 3: Informational Monitoring**: Complete 12-month calendar and benchmarks.
   - **Outputs**: Formatted Markdown summary (for Email/Google Doc) + CSV table (for Google Sheets/Excel).

---

## 🚀 Quickstart & Usage

### 1. Environment Setup
```bash
# Clone or navigate to the directory
cd /Users/ivanpe/str-price-advisor

# Activate virtual environment
source .venv/bin/activate

# Install dependencies (already installed)
pip install -r requirements.txt
playwright install chromium
```

### 2. Verify Kivoya Connection
```bash
python -m src.cli test-kivoya
```

### 3. Bootstrap / Refresh Comp Registry
```bash
python -m src.cli bootstrap-comps --limit 30
```
*(Curates verified listings into `config/comps_registry.json`)*.

### 4. Run Weekly Price Advisory Audit
```bash
# Run quick audit on upcoming intervals (e.g. next 12 intervals)
python -m src.cli run --quick --limit 12

# Run full 12-month annual scan
python -m src.cli run --weekly
```

---

## 📁 Output Reports

Every run generates dated and latest reports in the `data/` directory:
- `data/latest_report.md`: Formatted executive markdown summary with warning badges and actionable recommendations.
- `data/latest_sheet.csv`: Structured spreadsheet for importing directly into Google Sheets or Kivoya.
- `data/latest_report.json`: Machine-readable historical snapshot.

---

## ⚙️ Configuration (`config/settings.yaml`)

You can easily adjust:
- `strategy.base_percentile`: Target percentile (default: 78).
- `strategy.lead_time_tiers`: Custom percentile tapering tiers.
- `strategy.anomaly_thresholds.urgent_percent_diff`: Urgency threshold (default: 25.0%).
- `market.corridors`: Target geographical corridors (Tempe, Scottsdale, Chandler, Mesa, Gilbert).

