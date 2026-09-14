# Competitor Sales Tracking & Absorption Velocity Engine
**Product Requirements Document (PRD) & Business Specification**

---

## 1. Executive Summary & Purpose

### 1.1 The Business Problem
In short-term rental (STR) revenue management, traditional competitor analysis focuses almost exclusively on **asking prices** (what competitors are currently charging today). However, asking rates only describe **market supply**—they tell us what other hosts *hope* to earn, not what guests actually *pay*. 

A competitor asking $1,800/night for a stay 60 days out may never sell at that price. If Villa del Sol benchmarks its rates against unbooked competitor wishlists, we inherit their pricing mistakes and risk sharing their vacancies.

### 1.2 The Opportunity: Tracking Market Absorption
The **Competitor Sales Tracking & Absorption Velocity Engine** solves this problem by detecting when competitor listings cease to be available for specific stay intervals—signaling an empirical **market transaction (sale)**. 

By tracking competitor bookings over time, we transition from theoretical supply analysis to **empirical market absorption**:
$$\text{Market Supply (Asking Rates)} \quad \longrightarrow \quad \mathbf{\text{Market Absorption (Confirmed Clearing Sales)}}$$

---

## 2. Why We Need to Track Competitor Sales

1. **Discover True Market Clearing Rates**:
   - Asking prices are hypothetical; confirmed bookings are real financial commitments.
   - Tracking competitor sales reveals the exact price point at which money changed hands between a guest and a luxury host.

2. **Understand the Real Booking Pace (Lead Times)**:
   - Different traveler segments book at vastly different horizons:
     - 16-guest corporate retreats and multi-generational family reunions book 3–6 months in advance.
     - Golf trips and desert wedding parties book 1–3 months in advance.
     - Last-minute getaways book 7–14 days in advance.
   - Knowing the empirical booking pace prevents us from panicking and discounting prematurely when dates 90 days away are still unbooked.

3. **Detect Market Compression & Surge Events**:
   - High Compression is defined by **Pure Market Scarcity**: triggered whenever active available competitor inventory drops below **20%** of the active luxury cohort ($>80\%$ market absorption / unavailable).
   - Across our 97 active luxury comps, this triggers when $\le 19$ comps remain available ($\le 9$ for Tier A, $\le 9$ for Tier B).
   - Capturing this severe scarcity enables Villa del Sol to elevate target percentiles by $+15\%$ (capped at 90.0%) and enforce a 1.30x base rate surge floor before our own calendar is booked at an underpriced rate.

4. **Eliminate "Leave-Money-on-the-Table" vs. "Perishable Loss" Risk**:
   - Selling too early at low rates sacrifices thousands of dollars in high-season margin.
   - Holding rates too high into the last 14 days causes total perishable loss ($0 revenue for vacant nights).
   - Historical competitor sales velocity shows precisely where the clearing boundary lies across every booking horizon.

---

## 3. What Information From Sales Is Useful to Us

To make intelligent, data-driven pricing decisions, the system must capture seven core data dimensions for every detected competitor booking:

| Data Field | Description | Why It Is Useful to Villa del Sol |
|---|---|---|
| **Booking Lead Time ($N$)** | Days between booking detection date and check-in date: $N = D_{\text{in}} - D_{\text{detected}}$. | Reveals when guests commit to specific seasons and stay types. Informs when to hold rates firm vs. when to start promotional discounting. |
| **Realized Effective Rate ($R$)** | Last observed nightly price prior to booking: $R = \frac{\text{Total Stay Price}}{\text{Nights}}$. | The actual dollar rate at which competitor inventory cleared the market. |
| **Quality-Adjusted Rate ($R_{\text{adj}}$)** | Rate normalized against Villa del Sol using our 6-factor luxury rubric: $R_{\text{adj}} = \frac{R}{\text{Desirability Ratio}}$. | Apples-to-apples price comparison. If a superior competitor (1.15x ratio) sells for $1,150/night, our equivalent baseline value is $1,000/night. |
| **Market Percentile Rank ($Y$)** | Percentile position of the listing in the interval's comp rate distribution at the time of sale. | Answers: *"Did the guest book a budget comp (25th percentile), a mid-tier comp (50th percentile), or a premium comp (75th percentile)?"* |
| **Stay Segment Type** | Weekend (Thu, Fri, Sat) vs. Midweek (Sun, Mon, Tue, Wed). | Separates leisure luxury demand (which commands a high premium) from corporate/midweek demand. |
| **Property Tier & Scale** | Tier A (16+ guests, 6+ BR) vs. Tier B (12–15 guests, 5 BR). | Identifies whether group-size demand is shifting toward larger estate properties or mid-sized villas. |
| **Stay Dates & Length of Stay** | Check-in, check-out, and total stay nights ($D_{\text{in}} \to D_{\text{out}}$, $\text{Nights}$). | Identifies calendar reservation blocks, multi-night minimum commitment trends, and orphan slot boundaries. |

---

## 4. How We Want to Consume This Information

The data must be synthesized and consumed across three distinct operational layers:

### 4.1 Automated Algorithmic Consumption (Pricing Engine Input)
- **Direct Feed into Proposed Pricing**:
  - The empirical clearing percentiles from the sales engine feed directly into [`src/proposed_prices.py`](file:///Users/ivanpe/str-price-advisor/src/proposed_prices.py).
  - Replaces rigid, static pricing rules with dynamically adjusted targets based on actual competitor absorption velocity.
- **Bayesian Shrinkage Engine**:
  - When historical sales in a specific lead-time bucket are sparse ($n < 3$), the system smoothly blends empirical sales with revenue priors ($k = 3.0$) to avoid erratic rate recommendations.

### 4.2 Executive & Tactical Dashboard Consumption (`docs/index.html`)
The information is visually surfaced on **Tab 5: 🎯 Comps Sales** of the interactive web dashboard across four intuitive views:

1. **Executive KPI Strip**:
   - Total Competitor Sales.
   - Overall Market Median Clearing Percentile.
   - Average Booking Lead Time across all historical sales.
   - Average Realized Nightly Rate.
   - Weekend vs. Midweek Transaction Ratio.

2. **Monthly Advance Booking Windows (Booking Pace Table)**:
   - Displays all 12 calendar months with their historical booking paces:
     - Median Lead Time (e.g., March Cactus League converts at **180–195 days out**; October weddings convert at **35–50 days out**).
     - Interquartile Booking Window ($P_{25} \text{–} P_{75}$).
     - Min–Max Range.
     - Narrative Revenue Guidance for each month.

3. **2D Empirical Strategy Matrix (Heatmap Grid)**:
   - A $4 \times 2$ grid crossing **4 Lead-Time Horizons** with **Stay Type** (Weekend vs. Midweek):
     - **Far-Out (> 90 days)**
     - **Peak Booking Window (31–90 days)**
     - **Near-Term Compression (15–30 days)**
     - **Last-Minute Distress (≤ 14 days)**
   - Each cell displays:
     - Number of recorded sales ($n$).
     - Empirical Median ($P_{50}$) and Aggressive ($P_{75}$) percentiles.
     - Bayesian-recommended target percentile.
     - Realized average, minimum, and maximum nightly rates.

4. **Recent Confirmed Competitor Bookings Feed**:
   - A real-time, chronological transaction ledger showing individual competitor sales:
     - Property Name, Check-in / Check-out Dates, Stay Nights.
     - Days Booked in Advance.
     - Realized Nightly Rate & Quality-Adjusted Rate.
     - Market Percentile Rank at Sale.

### 4.3 Automated Alerts & Diagnostic Reporting
- Weekly price audit reports (`data/latest_report.md`) highlight newly detected competitor sales and flag sudden absorption accelerations.
- Automated daily reconciliation purges premature or erroneous sales if a competitor re-opens their calendar, ensuring zero database drift.

---

## 5. How This Information Informs Better Sales for Villa del Sol

| Strategic Objective | Without Competitor Sales Data (Blind Supply) | With Competitor Sales Engine (Empirical Absorption) |
|---|---|---|
| **Far-Out Pricing (> 90 Days)** | Host panics because calendar is empty 4 months ahead and offers early-bird discounts. | Host sees that competitors consistently clear at the **65th–75th percentile** far out; holds rates firm and protects profit margins. |
| **Peak Window Conversion (31–90 Days)** | Guesswork on whether rates are competitive. | Host aligns Villa del Sol to the **empirical median clearing percentile (60%–65%)**, capturing high-intent family vacationers at peak willingness-to-pay. |
| **Near-Term Demand Compression (15–30 Days)** | Host stubbornly holds high rates until it is too late. | Host recognizes that unbooked dates within 30 days face rising price elasticity; proactively trims to the **50th–55th percentile** to beat remaining competitors to checkout. |
| **Last-Minute Distress (≤ 14 Days)** | Property sits vacant, resulting in a **100% perishable loss ($0 revenue)**. | Host executes targeted liquidation at the **35th–45th percentile**, securing $2,500–$3,500 in booking revenue that would have otherwise vanished. |
| **Market Surge Harvesting** | Competitors sell out for a major concert or sporting event, but host doesn't notice until guest books Villa del Sol cheaply. | Sales engine detects pure market scarcity (<20% available comps); host **automatically boosts target percentile by +15% and enforces a 1.30x base rate floor** to capture the scarcity premium. |
| **Midweek vs. Weekend Optimization** | Midweek and Weekend rates are adjusted uniformly, dampening corporate demand. | Sales engine demonstrates that midweek travelers convert at the **45th percentile**, while weekends convert at the **65th percentile**; host decouples rates accordingly. |

---

## 6. Functional Requirements Checklist

To satisfy this specification, the implementation must meet the following mandatory requirements:

1. **FR-1: Automated Snapshot Diffing**:
   - Must compare consecutive market snapshots (`pricing_data_*.json`) and detect listings present in snapshot $T-1$ that disappear in snapshot $T$.
2. **FR-2: Curated Cohort Scoping**:
   - Must restrict tracking strictly to curated, active listings in `config/comps_registry.json`. Non-curated organic search churn must be discarded.
3. **FR-3: Scrape Degradation & False-Positive Defenses**:
   - Must reject intervals where comp counts drop by $> 80\%$ or to $\le 3$ comps due to network throttling, proxy failure, or search pagination truncation.
   - Must reject corridors where an entire sub-market drops to 0 comps.
4. **FR-4: Active Reconciliation (Auto-Healing)**:
   - If a listing is observed available in the current snapshot or in local search caches (`data/cache/search_*.json`), any premature sales record must be immediately deleted.
5. **FR-5: Ledger Purity & Calendar Verification**:
   - Unverified search dropouts must not be inserted into the database.
   - All stored records must be verified by visiting the property page on Airbnb and making sure the property is not available for some of the dates in the interval.
6. **FR-6: Idempotent Persistence**:
   - Database must enforce unique constraints on `(listing_id, check_in, check_out)`, preserving earliest detection date and maximum lead time on repeated diffs.
7. **FR-7: Analytics & Visualization**:
   - Must compute 2D Strategy Grids (Lead Horizon $\times$ Stay Type) with Bayesian shrinkage ($k = 3.0$).
   - Must compute Monthly Advance Booking Windows ($P_{25}, P_{50}, P_{75}$) across months 1–12.
   - Must render interactive UI on Tab 5 (`#tab-market-sales`) of `docs/index.html`.
8. **FR-8: Pure Scarcity Market Compression & Dynamic Percentile Boost**:
   - Must flag High Compression whenever active available comps drop below $20\%$ of the active cohort ($\le 19$ for 97 all comps, $\le 9$ for Tier A, $\le 9$ for Tier B).
   - Must boost pricing target percentile by $+15\%$ (capped at $90.0\%$) and apply `SURGE_INCREASE` with a 1.30x base rate floor in `compute_interval_consensus()`.
   - Must visually highlight compressed dates on the 12-month trajectory chart with vertical amber shading, a dashed threshold guide line, tooltip badges, and a dynamic 5th KPI card.

