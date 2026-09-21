# Product Requirements Document (PRD)
# Dynamic Nightly Rate Percentile Targets & Luxury Revenue Strategy

**Document Filename:** `docs/nightly_rate_targets_requrirements.md`  
**Also Referenced As:** `docs/nightly_rate_targets_requirements.md`  
**Version:** 1.1.0  
**Status:** Approved via `/grill-me` Alignment (Ready for Implementation)  
**Target Asset:** Villa del Sol (Tempe, AZ — 8BR / 5BA Luxury Estate, Max 16 Guests)  
**Author:** Antigravity (Pair Programming with Property Owner)  
**Date:** September 18, 2026  

---

## 1. Executive Summary & Background

Villa del Sol is an 8-bedroom, 5-bathroom premier luxury vacation rental estate in Tempe, Arizona, featuring private heated resort pool, spa, putting green, billiards, and multi-game entertainment amenities. The property uses the `str-price-advisor` platform to monitor competitive market pricing, track competitor booking absorption events, and synthesize nightly rate recommendations aligned with Kivoya PMS / Streamline seasonal rate periods.

In September 2026, an audit of the competitor sales tracking database in Turso Cloud (`competitor_sales` table; local SQLite for testing) was conducted across 75 verified competitor bookings. The audit revealed that competitor bookings exhibited distinct percentile distributions across lead-time horizons, highlighting an urgent need to re-align the pricing engine's target percentiles.

Specifically, unconstrained Bayesian shrinkage had begun pulling far-out weekend targets down to **43.9%**, directly contradicting luxury revenue management principles ("Hold firm at 65%–70%"), while peak midweek bookings converted only when deeply discounted ($P_{21}$–$P_{33}$).

Through a structured `/grill-me` interview between the property owner and the AI advisor, an exhaustive strategic and architectural consensus was established. This PRD formally documents the empirical findings, business goals, user stories, detailed functional requirements, and verification protocols governing the new dynamic nightly rate target matrix.

---

## 2. Empirical Database Audit Findings

The sales tracking engine (`CompetitorSalesTracker.compute_strategy_grid()`) evaluated 75 verified competitor bookings (`verification_status = 'CONFIRMED_BLOCKED'`) partitioned into a 2D matrix: **Lead-Time Horizon** $\times$ **Stay Type** (Weekend vs. Midweek).

### 2.1 Verified Competitor Sales Matrix

| Lead Time Horizon | Wkd ($n$) | Wkd $P_{50}$ / $P_{75}$ | Wkd Target Rec. | Mid ($n$) | Mid $P_{50}$ / $P_{75}$ | Mid Target Rec. | Strategic Guidance (Legacy Dashboard) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **Far-Out Horizon (> 90 Days)**<br>Early booking window with high willingness-to-pay. Anchor at premium percentiles. | **24** | **41.0% / 75.0%** | **43.9%**<br>*(Empirical)* | **20** | **39.8% / 66.7%** | **40.8%**<br>*(Empirical)* | Hold firm at 65%–70% (Weekend) / 45%–50% (Midweek). Do not discount far-out inventory. |
| **Peak Booking Window (31–90 Days)**<br>Prime conversion period for family vacations and luxury group travel. | **7** | **61.0% / 72.7%** | **61.5%**<br>*(Empirical)* | **11** | **21.2% / 44.8%** | **26.4%**<br>*(Empirical)* | Target 60%–65% (Weekend) / 45%–50% (Midweek). Maintain competitive positioning. |
| **Near-Term Compression (15–30 Days)**<br>Demand curve compresses and price elasticity rises rapidly. | **0** | **— / —** | **52.5%**<br>*(Blended $k=3$)* | **2** | **87.0% / 87.5%** | **57.9%**<br>*(Blended $k=3$)* | Trim to 50%–55% (Weekend) / 38%–42% (Midweek) if unbooked to accelerate conversion. |
| **Last-Minute Distress ($\le$ 14 Days)**<br>Distress inventory liquidation window where unbooked nights risk perishable loss. | **2** | **70.7% / 70.8%** | **53.8%**<br>*(Blended $k=3$)* | **9** | **33.3% / 66.7%** | **32.9%**<br>*(Empirical)* | Aggressive liquidation: 40%–45% (Weekend) / 30%–35% (Midweek) to secure occupancy. |

*Database totals: 75 total sales (33 Weekend, 42 Midweek); Average lead time: 132.1 days; Overall median percentile: 41.2%; Average rate: $1,117.09.*

---

## 3. Root Cause Analysis & Revenue Management Principles

### 3.1 The Far-Out "Bargain Hunter" Distortion
A detailed inspection of the 24 far-out (>90d) weekend bookings revealed that **13 out of 24 (over 54%)** booked between **$608 and $1,100/night** ($P_{7.7}$ to $P_{41.2}$) for 6- to 8-bedroom estates:
- *Old Town 6BD/5BA*: booked at $608/night (17.0th percentile) for August 2027 ($343$ days out), $927/night (27.8th percentile) for January 2027 ($126$ days out), and $1,100/night (28.8th percentile) for April 2027 ($203$ days out).
- *Spacious Fun Retreat 8BR/4.5BA*: booked at $997/night (7.7th percentile) for February 2027 ($149$ days out)—during prime Super Bowl / WM Phoenix Open season.
- *Brand new! Heated Pool*: booked at $1,290/night (22.0th percentile) for March 2027 ($168$ days out).

**Root Cause**: These bookings do not reflect normal equilibrium market value for luxury homes. They represent **host calendar mispricing errors** where hosts opened calendars 6–12 months in advance with generic low base rates. High-intent bargain hunters immediately sniped these underpriced dates.

**Business Risk**: Because $n=24$ heavily outvoted the prior ($k=3.0$), Bayesian shrinkage dragged the recommended target down to **43.9%**. If Villa del Sol adopts a 43.9th percentile far-out target, it will sell prime peak-season 2027 weekends at bargain-basement rates, cannibalizing high-paying luxury groups that book during the peak window ($31$–$90$ days) where empirical conversions occur at **61.0%–72.7%** (\$1,079–\$4,121/night).

### 3.2 Midweek Structural Demand Softness
In the Phoenix/Tempe market, leisure demand for large 8-bedroom compounds on Monday–Wednesday is fundamentally soft outside of major conventions and holiday weeks:
- In the peak booking window (31–90d), 11 midweek competitor sales had a median percentile of **21.2%** (Rec: 26.4%, average rate: \$611/night).
- In the last-minute window ($\le 14$d), 9 midweek competitor sales had a median of **33.3%** (Rec: 32.9%, average rate: \$475/night).
- Attempting to hold midweek at 45%–50% results in zero bookings. Midweek inventory requires competitive value pricing ($P_{30}$–$P_{35}$ in peak; $P_{28}$–$P_{32}$ last-minute) paired with an absolute dollar floor to cover turnover and operating expenses.

### 3.3 Small-Sample Volatility & Inverted Spikes
In horizons with thin sample sizes ($n \le 2$):
- **15–30d Midweek ($n=2$)**: Two luxury bookings at $1,252 (P87.0) and $1,264 (P87.5) pulled the recommendation from prior 38.5% up to **57.9%**. This created an inverted price spike (57.9% near-term vs 26.4% peak window).
- **$\le 14$d Weekend ($n=2$)**: Two bookings at $663 (P70.7) and $748 (P70.8) pulled the recommendation up to **53.8%**, contradicting last-minute liquidation policy.

---

## 4. User Stories & /grill-me Strategic Alignments

During the `/grill-me` alignment interviews, the property owner made 9 authoritative decisions resolving all strategic dependencies and implementation specifications:

- **US-1 (Far-Out Luxury Protection)**: *As the property owner, I want the pricing engine to enforce a strict strategic floor (65.0% Weekend / 45.0% Midweek) for Far-Out (>90d) dates, so that early underpriced competitor sales cannot pull our luxury rates down.*
- **US-2 (Midweek Empirical Realignment & Operational Floor)**: *As the property owner, I want Midweek targets realigned to an empirical value band (30%–35% in 31–90d, 28%–32% in $\le 14$d), backed by an absolute operational rate floor of **$300/night** for Midweek (and $450/night for Weekend) so that we drive weekday conversion without selling below operating cost or attracting low-quality guests.*
- **US-3 (Monotonic Tapering Invariant)**: *As the property owner, I want target percentiles to taper monotonically as check-in approaches (Far-Out $\ge$ Peak $\ge$ Near-Term $\ge$ Last-Minute), preventing inverted near-term price spikes.*
- **US-4 (Small-Sample Robustness)**: *As the property owner, I want the engine to require at least $n \ge 5$ verified comp sales before allowing empirical data to influence target percentiles, preventing 1–2 outlier sales from distorting recommendations.*
- **US-5 (Scarcity Surge Preservation)**: *As the property owner, I want the +15% scarcity compression surge to remain active across all horizons (capped at 90.0%) whenever available comps drop below 20%, capturing high-urgency market-wide compression.*
- **US-6 (Clean Deprecation & Single Source of Truth in `settings.yaml`)**: *As the property owner, I want `strategy.lead_time_tiers` completely removed from `config/settings.yaml` and replaced entirely by `strategy.lead_time_matrix` and `strategy.operational_floors`. All pricing and analytics modules must read exclusively from the new 2D matrix, eliminating legacy configuration duality.*
- **US-7 (Absolute Post-Churn Operational Floor Enforcement)**: *As the property owner, I want the operational price floors ($300 Midweek / $450 Weekend) enforced as an absolute hard floor on all finalized proposed rates (post-churn threshold in `src/proposed_prices.py`), guaranteeing that no proposed price ever falls below operating costs even if historical base rates were low or within the 5% churn tolerance window.*
- **US-8 (Transparent Strategic Floor Clamping Indicator)**: *As the property owner, I want any matrix cell where Bayesian shrinkage $\hat{Y}$ is strictly below the strategic floor ($\hat{Y} < \text{floor}$) to be flagged as `is_floor_clamped = True` and rendered with a distinct amber `Floor Clamped` badge in the dashboard and CLI audits, showing exactly where luxury yield protection intervened.*
- **US-9 (Automated Live Dashboard & Report Regeneration)**: *As the property owner, I want `docs/index.html`, `data/latest_sheet.csv`, and `data/latest_report.md` automatically regenerated upon completing the implementation so that our live dashboard and reports immediately present the new rates and strategic guidance without manual intervention.*

---

## 5. Detailed Functional Requirements

### FR-1: 2D Strategy Matrix Definition & Baseline Priors
1. The pricing system must define a 4-tier lead time horizon segmentation for both Weekend (Thu–Sat) and Midweek (Sun–Wed) stays:
   - **Far-Out Horizon (> 90 Days)**
   - **Peak Booking Window (31–90 Days)**
   - **Near-Term Compression (15–30 Days)**
   - **Last-Minute Distress ($\le 14$ Days)**
2. Each cell in the matrix must define:
   - `target`: The baseline Bayesian prior percentile.
   - `floor`: The mandatory minimum percentile floor.
   - `p75`: The aggressive/upper quartile reference target.
3. The baseline matrix parameters must be:
   - `>90d`:
     - Weekend: `target = 67.5`, `floor = 65.0`, `p75 = 75.0`
     - Midweek: `target = 47.5`, `floor = 45.0`, `p75 = 55.0`
   - `31–90d`:
     - Weekend: `target = 62.5`, `floor = 60.0`, `p75 = 70.0`
     - Midweek: `target = 32.5`, `floor = 30.0`, `p75 = 45.0`
   - `15–30d`:
     - Weekend: `target = 52.5`, `floor = 50.0`, `p75 = 60.0`
     - Midweek: `target = 32.5`, `floor = 30.0`, `p75 = 40.0`
   - `≤14d`:
     - Weekend: `target = 42.5`, `floor = 40.0`, `p75 = 50.0`
     - Midweek: `target = 30.0`, `floor = 28.0`, `p75 = 38.0`

### FR-2: Sample Size Threshold ($n \ge 5$) & Bayesian Shrinkage
1. The system must raise the minimum empirical qualification threshold from $n \ge 3$ to **$n \ge 5$**.
2. If cell count $n < 5$:
   - The cell is flagged as `is_empirical = False`.
   - The unconstrained recommended target $\hat{Y}$ must remain firmly anchored to the baseline prior (`prior_target`), or use high-inertia shrinkage with $k = 5.0$.
3. If cell count $n \ge 5$:
   - The cell is flagged as `is_empirical = True`.
   - The raw empirical target $\hat{Y}$ is calculated via Bayesian shrinkage:
     $$\hat{Y} = \frac{n \cdot p_{50} + k \cdot \text{prior\_target}}{n + k} \quad (k = 5.0)$$

### FR-3: Strategic Floor Clamping
1. Regardless of sample size $n$, the recommended target for any cell $(h, t)$ must never fall below the configured strategic floor:
   $$\text{Target}_{\text{clamped}}(h, t) = \max\left(\text{floor}(h, t),\, \hat{Y}(h, t)\right)$$
2. Specifically, for Far-Out (>90d) Weekends, the target must never drop below **65.0%**, even if empirical bookings occur at 41.0%.
3. For Far-Out (>90d) Midweek, the target must never drop below **45.0%**.
4. **Floor Clamping Status**: A cell is explicitly flagged as `is_floor_clamped = True` if and only if $\hat{Y}(h, t) < \text{floor}(h, t)$.

### FR-4: Monotonic Tapering Invariant
1. Target percentiles must decrease monotonically as check-in nears. A nearer horizon can never recommend a higher target percentile than a further-out horizon for the same stay type:
   $$\text{Target}(h_i, t) \le \text{Target}(h_{i-1}, t) \quad \text{for } h \in [>90\text{d}, 31\text{--}90\text{d}, 15\text{--}30\text{d}, \le14\text{d}]$$
2. After floor clamping, the system must execute a monotonic smoothing pass:
   - $\text{Target}(\text{31–90d}, t) = \min\left(\text{Target}(\text{31–90d}, t),\, \text{Target}(>90\text{d}, t)\right)$
   - $\text{Target}(\text{15–30d}, t) = \min\left(\text{Target}(\text{15–30d}, t),\, \text{Target}(\text{31–90d}, t)\right)$
   - $\text{Target}(\le14\text{d}, t) = \min\left(\text{Target}(\le14\text{d}, t),\, \text{Target}(\text{15–30d}, t)\right)$
3. **Mathematical Safety**: Because the configured floors are monotonically non-increasing across all horizons ($\text{floor}(h_i, t) \le \text{floor}(h_{i-1}, t)$), monotonic smoothing is guaranteed never to breach a horizon's own configured floor.

### FR-5: Operational Rate Floors ($300 Midweek / $450 Weekend)
1. In `PricingAnalyticsEngine` and `ProposedPricesEngine`, the absolute nightly rate floor must be updated:
   - **Midweek Absolute Floor**: **$300.00 / night** (increased from legacy $249.00).
   - **Weekend Absolute Floor**: **$450.00 / night** (matching turnover cleaning expense and base operating threshold).
2. For any evaluated segment in `PricingAnalyticsEngine`, the recommended base nightly rate must satisfy:
   $$R_{\text{recommended}} \ge \begin{cases} \$450.00 & \text{if stay includes Thursday, Friday, or Saturday} \\ \$300.00 & \text{if stay is Sunday through Wednesday} \end{cases}$$
3. **Absolute Post-Churn Clamping in `ProposedPricesEngine`**:
   In `src/proposed_prices.py`, operational floors must be enforced as a hard lower bound **after** applying the 5% churn threshold:
   $$R_{\text{final}} = \max\left(R_{\text{floor}}(t),\, \text{apply\_churn\_threshold}(R_{\text{proposed}}, R_{\text{current}}, \tau)\right)$$
   guaranteeing that even if a period's current Kivoya rate is below $300/$450, or churn logic would dampen the change, the proposed rate is clamped at $\ge \$300$ for Midweek and $\ge \$450$ for Weekend.

### FR-6: Scarcity Compression Surge
1. Whenever active available comps drop below 20% of the active cohort ($N_{\text{avail}} / N_{\text{total}} < 0.20$), scarcity compression is triggered.
2. The target percentile is boosted by **$+15.0\%$**, capped at **$90.0\%$**:
   $$\text{Target}_{\text{final}} = \min\left(90.0\%,\, \text{Target}_{\text{monotonic}} + 15.0\%\right)$$
3. Compression surge applies across all horizons.

### FR-7: Externalized Configuration (`config/settings.yaml`)
1. All parameters (horizons, priors, floors, sample size threshold $n=5$, monotonic tapering toggle, and dollar rate floors) must reside in `config/settings.yaml` under `strategy.lead_time_matrix` and `strategy.operational_floors`.
2. **Clean Deprecation**: The legacy 1D `strategy.lead_time_tiers` ladder is completely removed from `config/settings.yaml`.
3. All pricing and analytics modules (`CompetitorSalesTracker`, `PricingAnalyticsEngine`, `ProposedPricesEngine`) must read exclusively from `strategy.lead_time_matrix`.
4. In-code fallbacks in `PricingAnalyticsEngine` and `CompetitorSalesTracker` must map cleanly to the 4 horizons (`>90d`, `31–90d`, `15–30d`, `≤14d`) without relying on the old 1D `lead_time_tiers` ladder.

### FR-8: Dashboard Presentation Alignment
1. The HTML dashboard (`src/html_generator.py`) must display:
   - Badge indication for each cell:
     - **Floor Clamped** (Amber badge): when `is_floor_clamped == True` ($\hat{Y} < \text{floor}$).
     - **Empirical** (Green badge): when `is_empirical == True` ($n \ge 5$).
     - **Blended (k=5)** (Purple/Blue badge): when $n < 5$ and not floor-clamped.
   - Strategic guidance copy that mirrors the strategic decisions (e.g., highlighting that far-out weekends are held firm at $\ge 65\%$ to reject adverse-selection bargain hunters).

### FR-9: Automated Live Dashboard & Report Regeneration
1. Upon completing the pricing engine implementation and test verification, the system must execute an automated regeneration pass:
   - Re-generate `docs/index.html` via `HTMLDashboardGenerator`.
   - Re-generate `data/latest_sheet.csv` and `data/latest_report.md` via `PriceReportGenerator`.
   - Copy the fresh reports into `docs/` for GitHub Pages hosting.

---

## 6. Non-Functional Requirements

1. **Performance**: Strategy grid computation and lead-time lookup must execute in $< 50\text{ms}$ utilizing in-memory caching (`_cached_strategy_grid`).
2. **Robustness & Fallbacks**: If `config/settings.yaml` lacks the `lead_time_matrix` section, the code must default cleanly to the approved matrix constants without crashing.
3. **Auditability**: CLI commands (`str-advisor track-sales --audit`, `str-advisor audit`) must log whether a target was driven by empirical sales, Bayesian blending, strategic floor clamping, or monotonic adjustment.
4. **Testing Rigor**: All changes must pass targeted unit tests, full repository test suite (`unittest discover tests`), and a strict minimum of 2 clean-context subagent review rounds per `code_review.md` before completion.
