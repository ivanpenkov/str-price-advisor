# Product Requirements Document (PRD)
# Competitor Minimum Stay Tracking & Stratified Market Availability Intelligence

**Document Filename:** `docs/min_nights_comps_tracking_requirements.md`  
**Version:** 1.0.0  
**Status:** Approved for Implementation (Post-Grill Interview Alignment)  
**Target Asset:** Villa del Sol (Tempe, AZ — 6BR / 5BA Luxury Estate)  
**Author:** Antigravity (Pair Programming with Property Owner)  
**Date:** September 25, 2026  

---

## 1. Executive Summary & Context

The **STR Competitive Price Advisor** monitors 102 curated luxury competitors in the Phoenix East Valley / Scottsdale market to dynamically optimize pricing and detect market compression surges for Villa del Sol.

In the dashboard's **12-Month Competitor Market Availability & Absorption Trajectory** chart, a pronounced, recurring weekly sawtooth pattern was discovered:
- **Midweek intervals** (Sunday $\rightarrow$ Thursday, 4-night searches) average **62.1 available comps**.
- **Weekend intervals** (Thursday $\rightarrow$ Sunday, 3-night searches) average **50.5 available comps**.
- Across adjacent pairs, available inventory swings by **12 to 21 properties every single week**.

Empirical forensic analysis revealed that this swing is driven by two distinct forces:
1. **Search Length vs. Minimum Stay Rules**: Premier 6–8 bedroom luxury estates (e.g., the HÓZHÓ portfolio, large Scottsdale estates) enforce **4-night minimum stay requirements** on weekends. Because our scraper queries weekends as 3 nights (Thursday $\rightarrow$ Sunday), Airbnb's search engine automatically hides these properties from search results, treating them as unbooked/unavailable. When adjacent 4-night midweeks are queried, they satisfy the 4-night rule and reappear.
   - Example: `HÓZHÓ | Golf Simulator, Pool, Spa (8 BR)` appears in **0 out of 35 weekends (0.0%)**, but appears in **37 out of 45 midweeks (82.2%)**.
2. **Organic Weekend Demand**: Real leisure travelers book weekends at much higher rates than midweeks in the 0–90 day horizon.

This PRD defines the requirements to:
1. Scrape and capture weekend competitor rates for properties enforcing 4-night minimum stays.
2. Track minimum night stay availability depth (`3-night flexible` vs. `4-night extended`, with future expansion to `2-night`).
3. Stratify the 12-Month Trajectory chart into distinct visual availability layers.
4. Provide actionable competitive intelligence to guide Villa del Sol's own minimum stay strategy across different lead-time horizons.

---

## 2. Strategic Revenue Management Analysis (Minimum Stays)

### 2.1 The Revenue Management Dilemma: Are We Losing Money or Is This Our Edge?

For a 6-bedroom, 5-bathroom luxury estate accommodating up to 16 guests with a $500 cleaning fee, minimum stay rules represent a fundamental trade-off between **Yield Protection** and **Conversion Velocity**:

```
                       MINIMUM STAY STRATEGY TRADE-OFF MATRIX

          Horizon: >180 Days (Far Out)               Horizon: 0–90 Days (Near Term)
     ┌────────────────────────────────────┐     ┌────────────────────────────────────┐
     │  Risk: Calendar Fragmentation      │     │  Risk: Perishable Night Loss       │
     │  Goal: Maximize Total Booking $    │     │  Goal: Maximize Conversion Rate    │
     │                                    │     │                                    │
     │  Strategy: 4-NIGHT MINIMUM         │     │  Strategy: 3-NIGHT (or 2-NIGHT)    │
     │  • Forces high total stay revenue  │     │  • Captures weekend-only groups    │
     │  • Prevents orphaned midweeks      │     │  • Premium nightly rate offset     │
     │  • Minimizes turnover overhead     │     │  • High-converting competitive edge│
     └────────────────────────────────────┘     └────────────────────────────────────┘
```

#### Where 3-Night Stays Far in Advance (>180 Days) LOSE Money:
1. **Calendar Fragmentation ("Weekend Orphans")**: When an early planner books only Thursday–Sunday 9 months out for a peak high-demand weekend (e.g. WM Phoenix Open / Spring Training in Feb/March), they leave Sunday through Thursday (4 midweek nights) orphaned. Midweek nights in Phoenix are notoriously difficult to sell on their own.
2. **Turnover Overhead**: Cleaning and resetting a 6-bedroom luxury estate carries significant operational friction ($500 cleaning fee, 4–6 hours of professional housekeeping). Two 3-night stays generate double the turnover friction and wear-and-tear compared to one 6-night stay.
3. **Leaving Peak Yield on the Table**: During high-demand event windows (Super Bowl, WM Open, Spring Training, Thanksgiving, New Year's), guests *will* pay for 4 or 5 nights. Permitting a 3-night stay forfeits substantial booking value.

#### Where 3-Night Stays in the Near/Mid-Term (0–90 Days) ARE A MASSIVE EDGE:
1. **Conversion Advantage**: Standard leisure travelers (family reunions, celebrations, golf groups) only have Thursday evening through Sunday afternoon off from work. When competing luxury estates enforce rigid 4-night rules, Villa del Sol becomes one of the few high-end 6-bedroom estates available for a standard 3-night booking.
2. **Higher Nightly Yield**: Because 3-night inventory is scarce in the luxury segment, you can charge a **premium nightly rate** (e.g., $1,200/night for 3 nights = $3,600, rather than $900/night for 4 nights = $3,600). The guest gets the exact dates they want without paying for an unused Sunday or Monday night, and the property captures higher revenue per occupied night.

### 2.2 Recommended Minimum Stay Policy for Villa del Sol
Based on competitor minimum stay tracking, Villa del Sol should adopt a **Dynamic Horizon-Tiered Policy**:
- **Ultra-Advance (>180 days) & Major Event Windows**: Set minimum stay to **4 nights** on weekends (protecting calendar yield and preventing orphaned midweeks).
- **Prime Conversion Window (31–180 days)**: Allow **3 nights** on standard weekends (capturing high-converting 3-night groups at premium rates).
- **Near-Term Liquidation ($\le 30$ days)**: Maintain **2 or 3 nights** to secure occupancy above the $450 weekend / $300 midweek operational floor.
- **Orphan Gap Rule**: Automatically drop minimum stay to match any 2- or 3-night gap created between two confirmed reservations.

---

## 3. Problem Statement & User Need

### 3.1 The Weekend Comp Invisibility Problem
- **Problem**: In [`src/segmentation.py`](file:///Users/ivanpe/str-price-advisor/src/segmentation.py), weekends are segmented as 3 nights (`Thursday -> Sunday`). Competitors enforcing a 4-night minimum are filtered out by Airbnb and recorded as "0 comps available" or "booked".
- **Impact**:
  - The pricing engine evaluates weekend percentiles against a smaller, artificially skewed pool of comps that happens to allow 3-night stays (often lower-tier or less restrictive properties).
  - Premier 8-bedroom comps (like HÓZHÓ) that set high market price anchors are excluded from weekend pricing evaluations.
  - Market compression algorithms trigger false scarcity alerts because 15–20 comps appear "booked" when they are merely enforcing 4-night minimums.

### 3.2 Monolithic Trajectory Visualization
- **Problem**: The existing 12-Month Trajectory chart renders a single cyan band for "Available Inventory".
- **User Need**: The property owner cannot distinguish whether an open property is truly bookable for a standard 3-night weekend or requires an extended 4-night stay.
- **Objective**: Stratify the availability layer in the chart into **3-Night Flexible** vs. **4-Night Extended** inventory, clearly identifying competitive restrictions.

---

## 4. User Stories

- **US-1**: *As a property owner, I want the scraper to capture weekend competitor rates for properties requiring 4-night minimum stays so that our weekend pricing percentiles include our top luxury competitors.*
- **US-2**: *As a property owner, I want each scraped comp to be tagged with its observed minimum stay availability (`3-night` vs. `4-night only`) so that I know which competitors enforce extended stays.*
- **US-3**: *As a property owner, I want the Proposed Prices table to display a `4n min` badge next to competitor listings that require 4 nights so that I can see when high-end rate anchors reflect extended-stay pricing.*
- **US-4**: *As a property owner, I want the 12-Month Trajectory chart to visually separate 3-night flexible availability (top cyan band) from 4-night extended availability (indigo band) so that I can see the true depth of weekend market capacity.*
- **US-5**: *As a property owner, I want the Available Inventory KPI card to display total available comps alongside a breakdown of flexible (3n) vs. extended (4n+) comps.*
- **US-6**: *As a revenue manager, I want the system to calculate the market share of competitors enforcing 4-night minimums for each upcoming month so that I can decide whether Villa del Sol should enforce 4 nights or offer 3 nights as a conversion edge.*
- **US-7**: *As a system operator, I want the companion 4-night sweep to execute efficiently without doubling scraping execution time or blowing proxy bandwidth budgets.*

---

## 5. Functional Requirements

### 5.1 Scraper Extension: Companion 4-Night Weekend Sweep
1. **Targeted Sweep Execution**:
   - For every weekend interval (check-in Thursday, checkout Sunday, 3 nights), the scraper must execute a companion search for **Thursday $\rightarrow$ Monday (4 nights)**.
   - The 4-night sweep queries the same corridor locations (Tempe, Scottsdale, Chandler, Mesa, Ahwatukee, East Phoenix).
2. **Result Reconciliation & Comp Tagging**:
   - Listings returned in the 3-night sweep are tagged:
     ```json
     {
       "min_nights_available": 3,
       "is_strict_4n": false
     }
     ```
   - Listings returned **only** in the 4-night sweep (and absent from the 3-night sweep) are tagged:
     ```json
     {
       "min_nights_available": 4,
       "is_strict_4n": true,
       "effective_nightly": round(total_price / 4, 2)
     }
     ```
   - If a listing appears in both sweeps, its 3-night rate is preserved as primary for weekend pricing evaluations.
3. **Midweek Interval Behavior**:
   - Midweek intervals already span 4 nights (`Sunday -> Thursday`).
   - Comps returned during midweek searches naturally meet 4-night rules and are tagged `min_nights_available: 4` (or `3` if confirmed in adjacent 3-night weekend cache).

### 5.2 Pricing Engine Integration ([`src/analytics.py`](file:///Users/ivanpe/str-price-advisor/src/analytics.py))
1. **Dual-Tier Percentile Benchmark**:
   - In `evaluate_segment()`:
     - **Direct Flexible Comps (`min_nights_available <= 3`)**: Form the primary direct comparison cohort.
     - **Extended Comps (`min_nights_available == 4`)**: Integrated into the full comp distribution using their normalized nightly rate (`total_price / 4`), ensuring upper luxury anchors are represented.
2. **Metadata Enrichment**:
   - Each item in `comps_list` must retain `min_nights_available` and `is_strict_4n`.
   - The segment evaluation dictionary must expose:
     - `comps_count`: Total valid available comps (3n + 4n).
     - `comps_3n_count`: Count of flexible 3-night comps.
     - `comps_4n_count`: Count of extended 4-night minimum comps.
     - `pct_4n_restricted`: Percentage of available market locked into 4-night minimums.

### 5.3 12-Month Trajectory Chart Stratification ([`src/html_generator.py`](file:///Users/ivanpe/str-price-advisor/src/html_generator.py))
1. **4-Layer Stacked Canvas Chart**:
   The Chart.js configuration in `marketTrajectoryChart` must render 4 distinct stacked datasets:
   - **Layer 1 (Top, Cyan `#38bdf8`)**: `3-Night Available Comps (Flexible)` — Immediately bookable for standard weekend getaways.
   - **Layer 2 (Upper-Middle, Indigo `#818cf8`)**: `4-Night Minimum Comps (Extended)` — Available only for extended 4-night bookings.
   - **Layer 3 (Middle, Emerald `#34d399`)**: `Recorded Comp Sales` — Confirmed competitor bookings from `competitor_sales`.
   - **Layer 4 (Bottom, Slate `#334155`)**: `Unavailable (Unknown)` — Owner holds, calendar closures, past sales.
2. **Daily Tooltip Details**:
   Hovering over any day on the chart must display:
   ```
   Date: 2027-02-12 (Fri, Weekend)
   ---------------------------------
   • Total Active Comps:     102
   • 3-Night Flexible Comps: 38 (37.3%)
   • 4-Night Minimum Comps:  17 (16.7%)
   • Total Available:        55 (53.9%)
   • Recorded Comp Sales:     2 (2.0%)
   • Unavailable / Blocked:  45 (44.1%)
   • Villa del Sol:          Available ($1,500/n)
   ```

### 5.4 KPI Strip Modernization
1. **Split Available Inventory Badge**:
   - Primary metric: Total average available comps (e.g. `56.2 comps avg`).
   - Secondary subtitle: `${avg_3n} flexible (3n) • ${avg_4n} extended (4n+)`.
2. **Market Flexibility Gauge**:
   - Display the cohort-wide percentage of weekend inventory requiring 4+ nights (e.g. `31.6% of weekend comps require 4+ nights`).

### 5.5 Proposed Prices Table & Audit Subtable
1. **Listing Badge**:
   - In the comp drawer table (`tr.proposed-audit-row` and comp breakdown), any comp with `is_strict_4n == True` must display an indigo pill badge:
     `<span class="badge" style="background:rgba(129,140,248,0.15); color:#818cf8; border:1px solid rgba(129,140,248,0.3);">4n min</span>`.
2. **Audit Subtable Footnote**:
   - Include a note in the weekend audit summary:
     `"Includes X flexible 3-night comps and Y extended 4-night minimum comps (normalized nightly)."`

---

## 6. Non-Functional Requirements

1. **Scraping Runtime Budget**:
   - The companion 4-night sweep must not increase weekly full scan execution time by more than **25%** (leveraging existing Playwright worker contexts and cached search cookies).
2. **Zero-Sleep Test Environment Invariant**:
   - Unit tests covering 3n/4n scraping, tagging, analytics, and HTML generation must execute in **< 1.0s** total against mocks without real wall-clock delays.
3. **Backward Compatibility**:
   - Legacy snapshots (`pricing_data_*.json`) that lack `min_nights_available` must default gracefully to `min_nights_available: 3` without throwing KeyError or breaking historical backfills.
4. **Proxy & Bandwidth Efficiency**:
   - Cache 4-night corridor search results under `data/cache/search_{cin}_{cout_plus_1}.json` following the standard 20-hour cache TTL policy.
