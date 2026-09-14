# Product Requirements Document (PRD)
# Weighted Competitive Pricing Synthesis & Historical Interval Benchmark Calibration

**Document Filename:** `docs/weighted_pricing_requirements.md`  
**Version:** 1.3.0  
**Status:** Updated with Interview Decisions (Approved for Implementation)  
**Target Asset:** Villa del Sol (Tempe, AZ — 6BR / 5BA Luxury Estate)  
**Author:** Antigravity (Pair Programming with Property Owner)  
**Date:** September 2026  

---

## 1. Executive Summary & Context

Villa del Sol is a premier 6-bedroom, 5-bathroom luxury vacation rental estate in Tempe, Arizona. The property utilizes an automated pricing advisory engine (`str-price-advisor`) to evaluate competitive luxury properties, analyze historical booking realizations, and synthesize proposed nightly rates aligned directly with Kivoya / Streamline PMS seasonal rate periods.

Under the legacy pricing consensus algorithm:
1. **Agreement Requirement**: The engine requires directional consensus between competitive market recommendations ($P_{\text{comp}}$) and historical performance benchmarks ($P_{\text{hist}}$). If market data suggests an increase but historical booking data suggests a decrease (or is flat), the system triggers a `CONFLICT_HOLD` and holds the price at the existing base rate.
2. **Missing History Penalty**: If an interval has zero historical bookings, the system triggers a `NO_HISTORY_HOLD`, freezing rates even when market comps show clear upward or downward pricing opportunities.
3. **Equal 50/50 Weighting**: When both indicators agree, the rate is calculated as a simple arithmetic mean:
   $$\text{suggested\_price} = \text{round}\left(\frac{P_{\text{comp}} + P_{\text{hist}}}{2}\right)$$
   This gives equal weight to backward-looking historical realizations (which may reflect past booking conditions from 1–2 years ago) and live forward-looking competitive market demand.
4. **Ambiguity in Historical Benchmarks**: Historical benchmarks were previously calculated as a rolling $\pm 15$-day window around an individual stay date rather than tying directly to the seasonal rate periods defined in Streamline PMS.

This PRD defines the requirements to:
1. Transition the engine to an uninhibited, market-dominant **2:1 weighted pricing model** (67% competitor price / 33% historical benchmark).
2. Eliminate directional agreement bottlenecks and assign actionable status codes (`INCREASE`, `DECREASE`, `HOLD`, `SURGE_INCREASE`).
3. Gracefully handle missing historical data with 100% comp pricing and explicit `zero_history: true` reporting flag.
4. **Formally define Historical Prices by Streamline Seasonal Intervals**:
   - Interval date ranges originate directly from the Streamline PMS API (`GetPropertyRatesRawData`).
   - For closed-calendar periods where Streamline does not provide intervals (e.g. beyond calendar open end date), utilize a configurable fallback catalog (`config/fallback_intervals.yaml`) providing monthly baseline rates and comprehensive coverage for all 6 recognized holidays.
   - Segment stay types strictly into:
     - **Midweek**: Sunday through Wednesday nights (Sun, Mon, Tue, Wed).
     - **Weekend**: Thursday through Saturday nights (Thu, Fri, Sat).
   - Match historical bookings across prior years (2022–present) by calendar month for regular periods with strict holiday night exclusion, and by holiday identity for shifting/floating holidays (including Columbus Day, Thanksgiving, Memorial Day, Labor Day, Holy Week/Easter, Christmas & New Year).
   - Attribute multi-day stays crossing month/interval boundaries strictly via **exact night-level attribution**, allocating revenue using a 1.50 weekend premium factor.
   - Calculate the **historical weighted average nightly price** ($\sum \text{Rent} / \sum \text{Nights}$) for each interval and stay type.
5. Calibrate target percentiles dynamically via Bayesian shrinkage ($k=3.0$) based on 26 recorded competitor sales without prematurely hardcoding static small-sample priors.
6. Standardize HTML dashboard metrics to consume precomputed interval benchmarks matching parent rate periods.

---

## 2. Business Objectives & Problem Statement

### 2.1 The Agreement Bottleneck
- **Problem**: Forward-looking luxury travel demand is highly dynamic. For instance, when high regional demand or major events push competitor rates from \$500 to \$700, but historical median for that calendar interval was \$480, the legacy engine triggers `CONFLICT_HOLD` and keeps the rate at \$500.
- **Business Impact**: Villa del Sol leaves substantial revenue on the table during demand surges and fails to adjust rates downward during market softness when historical benchmarks are artificially high.
- **Objective**: Decouple proposed rate synthesis from directional agreement. Live competitor pricing combined with historical benchmarks must determine the rate regardless of direction.

### 2.2 Sub-Optimal Weighting
- **Problem**: Forward-looking market rates reflect active supply, demand, and booking pacing. Historical benchmarks reflect trailing conditions. A 50/50 weighting over-indexes on the past.
- **Objective**: Prioritize competitive market rates with double the weight of historical prices (67% comp / 33% historical), while making the weights easily configurable via YAML for future experimentation.

### 2.3 Stagnation on Missing Data
- **Problem**: For freshly configured seasons, holidays, or shoulder intervals where historical booking data is sparse ($n=0$), the engine refuses to adjust rates (`NO_HISTORY_HOLD`).
- **Objective**: When historical data is unavailable, synthesize rates using 100% of the competitive market recommendation ($P_{\text{comp}}$) and provide clear transparent diagnostics (`zero_history: true`).

### 2.4 Interval-Level Historical Definition
- **Problem**: A simple $\pm 15$-day rolling window around check-in dates produces noisy, fluctuating benchmarks that do not correspond to the actual seasonal rate intervals managed in Streamline PMS. Furthermore, holidays shift dates annually (e.g., Thanksgiving moves between Nov 22 and Nov 28; Easter/Holy Week shifts between March and April), causing date-based matching to miss actual holiday bookings from prior years.
- **Objective**: Standardize historical prices on **Streamline seasonal intervals** and **named holidays**, computing a weighted average nightly price for Midweek (Sun–Wed) and Weekend (Thu–Sat) using night-level attribution.

---

## 3. User Stories

- **US-1**: *As a property owner/revenue manager, I want the suggested price to reflect current competitor rates with double the weight of historical prices so that our rates aggressively capture live market demand.*
- **US-2**: *As a property owner, I want price adjustments to be proposed even when market recommendations and historical medians point in opposite directions, eliminating artificial rate freezes.*
- **US-3**: *As a property owner, I want intervals with zero historical bookings to be priced at 100% of the competitor market recommendation rather than holding at an outdated base rate, with explicit zero-history flags in diagnostics.*
- **US-4**: *As a property owner, I want the comp vs. historical weights to be defined in `config/settings.yaml` so I can easily adjust the balance without modifying code.*
- **US-5**: *As a property owner, I want historical prices to be calculated as the weighted average nightly price for the corresponding Streamline seasonal interval (split into Sun–Wed Midweek and Thu–Sat Weekend), correctly matching floating holidays (including Columbus Day) across prior years via night-level attribution.*
- **US-6**: *As a property owner, I want a comprehensive fallback catalog in YAML to define intervals, baseline rates, and holiday floors for all 6 recognized holidays when our calendar is closed in Streamline.*
- **US-7**: *As a property owner, I want clear, simplified status indicators (`INCREASE`, `DECREASE`, `HOLD`, `SURGE_INCREASE`) in CLI reports and dashboards, with HTML dashboard displays 100% aligned to parent interval benchmarks.*

---

## 4. Detailed Functional Requirements

### FR-1: Weighted Rate Calculation
1. The pricing synthesis engine must calculate proposed rates using a normalized weighted combination of the competitive market recommendation ($P_{\text{comp}}$) and the historical interval average benchmark ($P_{\text{hist}}$).
2. Given raw weights $w_{\text{comp}}$ and $w_{\text{hist}}$:
   $$W_{\text{comp}} = \frac{w_{\text{comp}}}{w_{\text{comp}} + w_{\text{hist}}}, \quad W_{\text{hist}} = \frac{w_{\text{hist}}}{w_{\text{comp}} + w_{\text{hist}}}$$
3. When historical benchmarks exist ($\text{sample\_count} > 0$ and $P_{\text{hist}} > 0$):
   $$P_{\text{proposed}} = \text{round}\left(W_{\text{comp}} \cdot P_{\text{comp}} + W_{\text{hist}} \cdot P_{\text{hist}}\right)$$
4. Default weights must be $w_{\text{comp}} = 0.67$ and $w_{\text{hist}} = 0.33$.
5. The calculation must yield an integer dollar rate (`round(...)`).

### FR-2: Weight Normalization & Input Flexibility
1. The engine must automatically normalize weights. Ratios such as `2.0` and `1.0` or percentage representations such as `67` and `33` or `0.67` and `0.33` must produce identical mathematical results.
2. If either weight is negative or the sum of weights is zero/negative ($w_{\text{comp}} < 0 \text{ or } w_{\text{hist}} < 0 \text{ or } w_{\text{comp}} + w_{\text{hist}} \le 0$), the engine must log a warning and fall back safely to defaults ($0.67$ and $0.33$).

### FR-3: Streamline Seasonal Intervals & Fallback Catalog
1. **Primary Interval Source**: Rate period date ranges, names, and pricing structures originate from Streamline PMS via `KivoyaClient().get_seasonal_rates()`.
2. **Closed-Calendar Fallback Catalog**:
   - For dates beyond the Streamline open calendar cutoff (`open_end_date`), or when Streamline API is unavailable, the system must utilize a configurable YAML fallback catalog (`config/fallback_intervals.yaml` or `config/settings.yaml` under `strategy.fallback_intervals`).
   - The fallback catalog must provide comprehensive coverage:
     - Monthly baseline definitions: Summer (Jun, Jul, Aug), Fall Shoulder (Sep), and Winter/Spring (Oct–May).
     - Full holiday coverage across all 6 recognized holidays from `holidays.json`: `4th of July`, `Labor Day`, `Columbus Day`, `Thanksgiving`, `Christmas & New Year`, `Holy Week`, and `Memorial Day`, with minimum nights and floor rates.

### FR-4: Day-of-Week Segmentation
All pricing calculations, historical aggregations, and recommendations must strictly classify stay days into:
- **Midweek Nights**: Sunday, Monday, Tuesday, Wednesday nights (checkout Mon, Tue, Wed, Thu).
- **Weekend Nights**: Thursday, Friday, Saturday nights (checkout Fri, Sat, Sun).

### FR-5: Historical Interval Average Price Formulation
1. **Matched Reservations**: For each Streamline or fallback interval, query all historical confirmed guest bookings (`status_name IN ('Booked', 'Checked Out')` with `gross_rent > 0`) from `data/reservations.db` (2022–present).
2. **Exact Night-Level Attribution**:
   - For multi-day bookings crossing calendar month or interval boundaries (e.g. October 29 to November 3), each night stayed and its allocated nightly revenue must be attributed strictly to the specific calendar month or holiday interval in which that night occurred.
   - For regular non-holiday monthly periods, match historical stay nights occurring in that calendar month across prior years, strictly excluding nights that fall into identified holiday periods.
3. **Floating Holiday Mapping & Resolver**:
   - For named holidays, match historical stay nights that occurred during that specific holiday's stay date window in each past year.
   - Use a **Hybrid Holiday Resolver**: Read explicit historical date ranges from `config/holidays.json` when specified, or compute floating US holiday date rules:
     - **Thanksgiving**: 4th Thursday in November through Sunday night (4 nights: Thu, Fri, Sat, Sun).
     - **Memorial Day**: Friday preceding the last Monday in May through Monday night (4 nights: Fri, Sat, Sun, Mon).
     - **Labor Day**: Friday preceding the 1st Monday in September through Monday night (4 nights: Fri, Sat, Sun, Mon).
     - **Columbus Day**: Friday preceding the 2nd Monday in October through Monday night (4 nights: Fri, Sat, Sun, Mon).
     - **Holy Week / Easter**: Easter Sunday $- 9$ days (Friday before Palm Sunday) through Easter Sunday night (10 nights: Fri through following Sun), derived via Gregorian computus.
     - **Christmas & New Year**: December 24 through January 2 inclusive (10 nights).
     - **4th of July**: July 1 through July 5 inclusive (5 nights).
4. **Weighted Nightly Average Calculation**:
   - For multi-day bookings spanning both midweek and weekend nights, allocate nightly revenue assuming weekend nights carry a $1.50 \times$ premium over midweek nights:
     $$\text{denom} = 1.50 \cdot N_{\text{wkd}} + N_{\text{mid}}$$
     $$\text{rate}_{\text{mid}} = \frac{\text{gross\_rent}}{\text{denom}}, \quad \text{rate}_{\text{wkd}} = 1.50 \cdot \text{rate}_{\text{mid}}$$
   - For pure midweek bookings: $\text{rate}_{\text{mid}} = \frac{\text{gross\_rent}}{N_{\text{mid}}}, \text{rate}_{\text{wkd}} = 0$.
   - For pure weekend bookings: $\text{rate}_{\text{wkd}} = \frac{\text{gross\_rent}}{N_{\text{wkd}}}, \text{rate}_{\text{mid}} = 0$.
   - Compute the overall historical interval average by aggregating all matched nights:
     $$P_{\text{hist}}(\text{interval}, \text{type}) = \frac{\sum_{\text{nights} \in \text{interval}} \text{rate}_{\text{type}}}{\sum_{\text{nights} \in \text{interval}} 1}$$
   - This represents the exact aggregate realized revenue per night stayed in that interval and stay type.

### FR-6: Zero-History Fallback Policy
1. When an interval has no historical track record ($\text{sample\_count} = 0$ or $P_{\text{hist}} \le 0$):
   $$P_{\text{proposed}} = \text{round}(P_{\text{comp}})$$
2. The engine must NOT hold the rate at current base solely due to absent historical records.
3. The consensus result dictionary must include an explicit `"zero_history": bool` flag (`True` when historical records are absent, `False` otherwise) alongside `hist_med: None` for transparency in downstream reports and UI.

### FR-7: Elimination of Directional Agreement Requirement
1. Directional agreement between $P_{\text{comp}}$ and $P_{\text{hist}}$ is explicitly eliminated.
2. If $P_{\text{comp}} > B$ and $P_{\text{hist}} < B$, the weighted calculation proceeds normally. The final synthesized $P_{\text{proposed}}$ dictates whether an increase, decrease, or hold is proposed.

### FR-8: Standardized Action Status Codes
The engine must classify interval status strictly by comparing $P_{\text{proposed}}$ to current base rate $B$:
- `INCREASE`: If $P_{\text{proposed}} > B$
- `DECREASE`: If $P_{\text{proposed}} < B$
- `HOLD`: If $P_{\text{proposed}} == B$
- `SURGE_INCREASE`: Scarcity override active (see FR-9).

Legacy status codes (`AGREED_INCREASE`, `AGREED_DECREASE`, `CONFLICT_HOLD`, `NO_HISTORY_HOLD`) are deprecated and replaced.

### FR-9: Preservation of Scarcity Surge Override
1. Pure market compression scarcity ($< 20\%$ active comps available in the cohort) represents extraordinary market absorption.
2. When `is_compression_surge = True`:
   $$P_{\text{proposed}} = \max\left(\text{round}(B \cdot 1.30), \text{round}(P_{\text{comp}})\right)$$
   and status is set to `SURGE_INCREASE`.
3. This scarcity override takes precedence over weighted synthesis and zero-history fallback.

### FR-10: Preservation of 5% Churn Threshold (Deadband)
1. When mapping synthesized interval consensus rates into Kivoya seasonal rate periods in `generate_proposed_prices`:
   - If $\frac{|P_{\text{proposed}} - B|}{B} < 0.05$, the final rate holds at $B$.
2. This prevents excessive PMS rate updates for trivial micro-fluctuations ($<5\%$).

### FR-11: Configuration Storage
1. The weights must be stored in `config/settings.yaml` under `strategy.proposed_pricing`:
   ```yaml
   strategy:
     proposed_pricing:
       comp_weight: 0.67
       historical_weight: 0.33
       weekend_premium_factor: 1.50
   ```
2. Fallback intervals and baseline rates must be configurable via YAML (`config/fallback_intervals.yaml` or `config/settings.yaml` under `strategy.fallback_intervals`).
3. Configurations must be accessible via `src.config` and hot-reloadable via `reload_settings()`.

---

## 5. Statistical Significance & Competitor Sales Strategy

### 5.1 Comp Sales Analysis ($N = 26$)
The property tracking database (`data/reservations.db`) records 26 confirmed competitor bookings across lead-time horizons:

| Horizon | Stay Type | Prior Target | Sales ($n$) | Empirical Median ($p_{50}$) | Bayesian Target ($\hat{Y}$) | Variance / Analysis |
| :--- | :--- | :---: | :---: | :---: | :---: | :--- |
| **> 90 days** | **Weekend** | **67.5%** | 7 | 61.5% | **63.3%** | Moderate dip (-4.2%). Luxury guests locking in high-tier rates. |
| **> 90 days** | **Midweek** | **47.5%** | 8 | 60.6% | **57.0%** | Significant rise (+9.5%). High demand for advance luxury corporate blocks. |
| **31–90 days** | **Weekend** | **62.5%** | 2 | 69.2% | **65.2%** | Slight rise (+2.7%). Very small sample ($n=2$). |
| **31–90 days** | **Midweek** | **45.5%** | 2 | 17.8% | **34.4%** | Sharp drop (-11.1%). Low sample ($n=2$) distorted by 2 steep discounts. |
| **15–30 days** | **Weekend** | **52.5%** | 0 | *None* | **52.5%** | No sales recorded. Reverts to baseline prior. |
| **15–30 days** | **Midweek** | **38.5%** | 0 | *None* | **38.5%** | No sales recorded. Reverts to baseline prior. |
| **≤ 14 days** | **Weekend** | **42.5%** | 0 | *None* | **42.5%** | No sales recorded. Reverts to baseline prior. |
| **≤ 14 days** | **Midweek** | **31.5%** | 7 | 33.3% | **32.8%** | Highly aligned (+1.3%) with expected close-in discount curve. |

### 5.2 Statistical Significance Assessment
1. **Low Sample Risks ($n=2$)**:
   - In statistical inference, sample sizes of $n=2$ have high sampling error ($\sigma_{\bar{x}} > 25\%$).
   - For example, in the `31–90d Midweek` bucket, two discounted comp bookings produced an empirical median of 17.8%. Hardcoding this as a static target percentile would depress Villa del Sol's rates to the bottom 18th percentile of the luxury market, causing severe revenue dilution.
2. **Bayesian Shrinkage Framework**:
   The engine implements Bayesian shrinkage ($k=3.0$):
   $$\hat{Y} = \frac{n \cdot p_{50} + k \cdot \text{prior}}{n + k}$$
   - When $n=2$, the prior retains $\frac{3}{2 + 3} = 60\%$ weight, pulling the target up to $34.4\%$ rather than collapsing to $17.8\%$.
   - When $n=8$ (e.g. `>90d Midweek`), empirical sales command $\frac{8}{8 + 3} = 72.7\%$ weight, shifting the target up to $57.0\%$ in response to genuine demand.
3. **Strategic Conclusion**:
   - Do **NOT** hardcode static target percentiles from $N=26$ sales into config files.
   - Maintain the baseline priors in `HORIZON_PRIORS` as foundational anchors.
   - The Bayesian shrinkage engine automatically calculates and deploys the dynamic targets listed above in real time.

---

## 6. Non-Functional Requirements

1. **Deterministic Execution**: Given identical inputs and weights, rate outputs must be 100% reproducible.
2. **Zero-Sleep Test Performance**: All unit tests must execute against mock data without real wall-clock sleeps (`asyncio.sleep`, `time.sleep`).
3. **Precomputed Benchmark Efficiency**: Interval historical benchmarks must be precomputed once per pricing run, avoiding redundant database lookups during multi-segment iteration.
4. **Backward Compatibility**: Downstream callers of `compute_interval_consensus` and `generate_proposed_prices` must continue functioning without mandatory parameter additions.
5. **Dashboard Clarity**: HTML dashboard tooltips and badges must display updated terminology ("Proposed increase/decrease") without mentioning deprecated "Agreed consensus".
6. **Dashboard Metric Consistency**: The HTML dashboard generation in `src/html_generator.py` must populate interval historical benchmarks using the precomputed Streamline seasonal interval benchmarks (matched via parent rate periods) rather than the legacy rolling $\pm 15$-day window.

---

## 7. Acceptance Criteria

| Criteria ID | Requirement Description | Verification Method |
| :--- | :--- | :--- |
| **AC-1** | Suggested price uses 67% comp / 33% historical weighting when history is present. | Unit test verifying exact integer calculation ($0.67 \times 500 + 0.33 \times 600 = 533$). |
| **AC-2** | Suggests price change when comp and historical signals conflict. | Unit test verifying opposing signals yield weighted price and directional status rather than hold. |
| **AC-3** | Zero-history intervals price at 100% comp recommendation with actionable status and `zero_history: true`. | Unit test with `sample_count: 0` yielding $P_{\text{proposed}} = P_{\text{comp}}$, `zero_history=True`, and `INCREASE`/`DECREASE`. |
| **AC-4** | Historical price represents weighted average nightly rate ($\sum \text{Rent}/\sum \text{Nights}$) for the Streamline interval. | Unit test verifying exact weighted average for an interval with multiple mixed and pure stays. |
| **AC-5** | Day-of-week segmentation strictly treats Sun–Wed as Midweek and Thu–Sat as Weekend. | Unit test verifying day categorization for Sunday check-ins vs Thursday check-ins. |
| **AC-6** | Floating holidays correctly match historical stays across shifting calendar dates. | Unit test verifying Columbus Day, Thanksgiving, and Easter bookings from 2023–2025 match corresponding 2026–2027 periods. |
| **AC-7** | Closed calendar periods correctly load fallback intervals from YAML catalog. | Unit test verifying closed month and holiday generation using fallback catalog. |
| **AC-8** | Scarcity compression surge override produces `SURGE_INCREASE` with 30% premium. | Unit test verifying `is_compression_surge=True` overrides normal weighting. |
| **AC-9** | Status codes use strictly `INCREASE`, `DECREASE`, `HOLD`, `SURGE_INCREASE`. | Test assertions checking absence of `AGREED_*` and `*_HOLD` legacy strings. |
| **AC-10** | Weights configurable in `config/settings.yaml` and hot-reloadable. | Unit test reloading config with custom weights. |
| **AC-11** | Full test suite passes under Zero-Sleep invariant (< 35 seconds). | Full execution of `unittest discover tests`. |
| **AC-12** | Closed-calendar fallback catalog defines all 6 recognized holidays. | Test verifying `fallback_intervals.yaml` parses all 6 holidays with baseline floors. |
| **AC-13** | Exact night-level attribution for boundary-crossing historical reservations. | Unit test verifying a stay from Oct 29 to Nov 3 splits nightly revenue cleanly between October and November benchmarks. |
| **AC-14** | HTML dashboard attaches precomputed interval benchmarks to all segments. | Test verifying HTML generator uses seasonal interval benchmark map for segment benchmarks. |
