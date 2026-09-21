# Technical Design & Implementation Plan
# Weighted Competitive Pricing Engine & Historical Interval Benchmark Calibration

**Document Filename:** `docs/weighted_pricing_design.md`  
**Version:** 1.4.0  
**Status:** Grill-Me Interview Decisions Integrated (Approved for Implementation)  
**Target Asset:** Villa del Sol (Tempe, AZ — 6BR / 5BA Luxury Estate)  
**Author:** Antigravity (Pair Programming with Property Owner)  
**Reviewer:** Design Reviewer Subagent (Rounds 1 & 2) & Owner Interview  
**Date:** September 2026  

---

## 1. Executive Summary & System Architecture

### 1.1 Objective
This technical design transitions the proposed pricing engine for Villa del Sol from an agreement-gated 50/50 averaging model to a **2:1 weighted formula** ($67\%$ competitor-derived price / $33\%$ historical benchmark).

It formally defines and standardizes **Historical Prices**:
1. **Streamline Seasonal Intervals**: Historical benchmarks are calculated for the exact seasonal rate intervals provided by the Streamline PMS API (`GetPropertyRatesRawData`).
2. **Closed-Calendar Fallback Catalog**: For periods beyond the Streamline open calendar cutoff, a clean YAML fallback catalog (`config/fallback_intervals.yaml`) defines monthly intervals and comprehensive holiday coverage for all 6 recognized holidays.
3. **Day-of-Week Partitioning**: Stay nights are strictly classified into:
   - **Midweek**: Sunday, Monday, Tuesday, Wednesday nights (Sun–Wed, indices 6, 0, 1, 2).
   - **Weekend**: Thursday, Friday, Saturday nights (Thu–Sat, indices 3, 4, 5).
4. **Holiday Resolution & Isolation**:
   - Stays are matched by calendar month for regular periods, with historical holiday dates **strictly excluded** from regular month calculations to prevent benchmark contamination.
   - Moving holidays (including Columbus Day, Thanksgiving, Memorial Day, Labor Day, Holy Week/Easter, Christmas & New Year) are resolved across all years (2022–present) via an exact **Hybrid Holiday Resolver** utilizing the Gregorian computus algorithm and floating rules.
5. **Exact Night-Level Attribution & Weighted Nightly Average**: Realized revenue is computed as the weighted nightly average ($\sum \text{Rent} / \sum \text{Nights}$), allocating mixed-stay revenue with a $1.50 \times$ weekend premium factor, and attributing multi-day stays crossing boundaries strictly night-by-night.
6. **Segment-to-Period Resolution**: An explicit 2-pass date-matching algorithm maps evaluated calendar segments to their parent Streamline or fallback interval with holiday priority.
7. **Transparent Diagnostics & Dashboard Consistency**: `compute_interval_consensus()` outputs an explicit `zero_history: bool` flag, and `src/html_generator.py` is upgraded to consume precomputed interval benchmarks matching parent rate periods.

### 1.2 End-to-End System Flowchart

```mermaid
flowchart TD
    subgraph StreamlineLayer["PMS & Interval Discovery Layer"]
        PMS["Streamline API / KivoyaClient<br/>(get_seasonal_rates)"]
        FALLBACK_YAML["config/fallback_intervals.yaml<br/>(Closed-calendar months & all 6 holidays)"]
        PMS --> |Open Calendar Periods| INTERVALS["Unified Rate Intervals List"]
        FALLBACK_YAML --> |Closed Calendar Periods| INTERVALS
    end

    subgraph ResIntel["Historical Interval Benchmark Engine (src/reservation_intelligence.py)"]
        DB[(Turso Cloud: reservations<br/>Confirmed Stays: Booked + Checked Out<br/>[SQLite: Testing Only])]
        HOL_RESOLV["Hybrid Holiday Resolver<br/>(Gregorian Computus + US Floating Rules incl. Columbus Day)"]
        AGG_SRV["compute_interval_historical_benchmarks()<br/>Midweek: Sun-Wed | Weekend: Thu-Sat<br/>Night-level boundary attribution<br/>Weighted Nightly Avg: sum(Rent) / sum(Nights)<br/>Holiday exclusion in regular months"]
        DB --> AGG_SRV
        HOL_RESOLV --> AGG_SRV
        INTERVALS --> AGG_SRV
        AGG_SRV --> BENCH_MAP["Precomputed Interval Benchmark Map<br/>(period_name, stay_type) -> {avg_rate, median_rate, sample_count, total_nights...}"]
    end

    subgraph PricingEngine["Pricing Engine (src/proposed_prices.py)"]
        SEG["Evaluated Market Segment<br/>(check_in_dt, check_out_dt, P_comp, Base B, stay_type)"]
        PARENT_RES["find_parent_period_for_segment()<br/>Pass 1: Holiday Period Match<br/>Pass 2: Regular Period Match"]
        BENCH_LOOKUP["Lookup Interval Hist Avg<br/>P_hist = bench_map.get((parent_pname, stay_type))"]
        WEIGHTED_CALC["Weighted Synthesis (67/33):<br/>W_c = 0.67, W_h = 0.33<br/>P_prop = round(W_c * P_comp + W_h * P_hist)"]
        ZERO_HIST["Zero-History Fallback:<br/>P_prop = round(P_comp)<br/>zero_history = True"]
        SURGE_CHK{"is_compression_surge?"}
        SURGE_OVERRIDE["Surge Override:<br/>max(round(B * 1.30), round(P_comp))"]

        SEG --> PARENT_RES
        INTERVALS --> PARENT_RES
        PARENT_RES --> BENCH_LOOKUP
        BENCH_MAP --> BENCH_LOOKUP
        BENCH_LOOKUP --> SURGE_CHK
        SURGE_CHK -- Yes --> SURGE_OVERRIDE
        SURGE_CHK -- No --> DATA_CHK{"P_hist > 0 & n > 0?"}
        DATA_CHK -- Yes --> WEIGHTED_CALC
        DATA_CHK -- No --> ZERO_HIST
    end

    subgraph OutputLayer["Downstream Delivery"]
        SEASONAL["generate_proposed_prices()<br/>Applies 5% Churn Threshold & Maps to Kivoya Periods"]
        UI["HTML Dashboard & Tooltips<br/>(Consumes Precomputed Interval Benchmarks)"]
        CLI["CLI Commands & Diagnostics"]
        
        WEIGHTED_CALC --> SEASONAL
        ZERO_HIST --> SEASONAL
        SURGE_OVERRIDE --> SEASONAL
        SEASONAL --> UI
        SEASONAL --> CLI
    end
```

---

## 2. Historical Interval Benchmark Formulation

### 2.1 Day-of-Week Partitioning
Stay nights are strictly partitioned into:
- **Midweek Nights**: Sunday, Monday, Tuesday, Wednesday (weekday indices 6, 0, 1, 2).
- **Weekend Nights**: Thursday, Friday, Saturday (weekday indices 3, 4, 5).

### 2.2 Hybrid Holiday Date Resolution & Gregorian Computus
All holiday calculations return **inclusive stay dates** `[start_stay_night, end_stay_night]`.

1. **Anonymous Gregorian Computus Algorithm**:
   Computes the date of Easter Sunday for any year $Y$:
   ```python
   def compute_easter_date(year: int) -> date:
       a = year % 19
       b = year // 100
       c = year % 100
       d = b // 4
       e = b % 4
       f = (b + 8) // 25
       g = (b - f + 1) // 3
       h = (19 * a + b - d - g + 15) % 30
       i = c // 4
       k = c % 4
       l = (32 + 2 * e + 2 * i - h - k) % 7
       m = (a + 11 * h + 22 * l) // 451
       month = (h + l - 7 * m + 114) // 31
       day = ((h + l - 7 * m + 114) % 31) + 1
       return date(year, month, day)
   ```

2. **Algorithmic Floating Rules**:
   - **Thanksgiving**: 4th Thursday in November through the following Sunday night (4 nights: Thu, Fri, Sat, Sun).
   - **Memorial Day**: Friday preceding the last Monday in May through Monday night (4 nights: Fri, Sat, Sun, Mon).
   - **Labor Day**: Friday preceding the 1st Monday in September through Monday night (4 nights: Fri, Sat, Sun, Mon).
   - **Columbus Day**: Friday preceding the 2nd Monday in October through Monday night (4 nights: Fri, Sat, Sun, Mon).
   - **Holy Week / Easter**: Easter Sunday $- 9$ days (Friday before Palm Sunday) through Easter Sunday night (10 nights: Fri through following Sun), directly matching Kivoya PMS rate configuration.
   - **Christmas & New Year**: December 24 through January 2 inclusive (10 nights).
   - **4th of July**: July 1 through July 5 inclusive (5 nights).

### 2.3 Regular Month Holiday Exclusion
When computing historical benchmarks for a regular month $M$ (e.g. November or October):
1. For each historical year $Y \in [2022 \dots \text{present}]$:
   - Identify all floating/fixed holidays in that month via `resolve_holiday_dates(hol_name, Y)` (e.g. Thanksgiving in November, Columbus Day in October).
   - **Strictly exclude** all stay nights falling within those holiday dates from the regular month benchmark.
2. This guarantees that peak holiday compression bookings do not distort the base monthly benchmarks.

### 2.4 Exact Night-Level Attribution & Weighted Nightly Average Formulation
For an interval $I$ and stay type $T \in \{\text{midweek}, \text{weekend}\}$:

1. **Reservation Revenue Allocation**:
   For each historical reservation $r$:
   - Count midweek nights $N_{\text{mid}}$ and weekend nights $N_{\text{wkd}}$ within the entire stay.
   - If the stay contains both midweek and weekend nights:
     $$\text{denom} = 1.50 \cdot N_{\text{wkd}} + N_{\text{mid}}$$
     $$\text{rate}_{\text{mid}}(r) = \frac{\text{gross\_rent}(r)}{\text{denom}}, \quad \text{rate}_{\text{wkd}}(r) = 1.50 \cdot \text{rate}_{\text{mid}}(r)$$
   - If purely midweek ($N_{\text{wkd}} = 0$): $\text{rate}_{\text{mid}}(r) = \frac{\text{gross\_rent}(r)}{N_{\text{mid}}}, \text{rate}_{\text{wkd}}(r) = 0$.
   - If purely weekend ($N_{\text{mid}} = 0$): $\text{rate}_{\text{wkd}}(r) = \frac{\text{gross\_rent}(r)}{N_{\text{wkd}}}, \text{rate}_{\text{mid}} = 0$.

2. **Exact Night-Level Attribution**:
   - Iterate night-by-night through each stay night $d \in [\text{start\_date}, \text{end\_date} - 1\text{ day}]$.
   - Multi-day stays crossing calendar month or interval boundaries (e.g. October 29 to November 3) have each individual night attributed strictly to the month/interval in which night $d$ was stayed:
     - If night $d$ falls within a recognized holiday window, it attributes to that holiday's benchmark.
     - If night $d$ falls outside holidays in month $M$, it attributes to month $M$'s regular benchmark.
   - Determine stay type of night $d$:
     - Weekday index $\in \{6, 0, 1, 2\}$ (Sun–Wed): assigns $\text{rate}_{\text{mid}}(r)$ to Midweek.
     - Weekday index $\in \{3, 4, 5\}$ (Thu–Sat): assigns $\text{rate}_{\text{wkd}}(r)$ to Weekend.

3. **Weighted Nightly Average**:
   Aggregate all matched nights and revenue across historical stays for interval $I$ and stay type $T$:
   $$P_{\text{hist}}(I, T) = \frac{\sum_{d \in \mathcal{D}(I, T)} \text{rate}_T(r(d))}{|\mathcal{D}(I, T)|}$$
   where $\mathcal{D}(I, T)$ is the set of all individual historical stay nights matching interval $I$ and stay type $T$.

4. **Complete Benchmark Dictionary Schema**:
   To ensure 100% downstream compatibility with `src/reporter.py` and `src/html_generator.py`:
   ```python
   {
       "avg_rate": round(avg_rate, 2),
       "median_rate": round(med_rate, 2),
       "min_rate": round(min_rate, 2),
       "max_rate": round(max_rate, 2),
       "sample_count": len(matched_stays),
       "stay_count": len(matched_stays),
       "total_nights": total_nights,
       "flag": "ON_TRACK",
       "flag_label": "Aligned With Track Record",
       "matched_stays": matched_stays,
   }
   ```

---

## 3. Rate Synthesis & Consensus Algorithm

### 3.1 Weight Normalization & Guards
Given raw weights $w_{\text{comp}}$ and $w_{\text{hist}}$:
1. **Defensive Non-Negative Convex Guard**:
   $$\text{if } w_{\text{comp}} < 0 \text{ or } w_{\text{hist}} < 0 \text{ or } (w_{\text{comp}} + w_{\text{hist}}) \le 0:$$
   Reset to safe default weights ($w_{\text{comp}} = 0.67, w_{\text{hist}} = 0.33$) and log a warning.
2. **Normalized Coefficients**:
   $$W_{\text{comp}} = \frac{w_{\text{comp}}}{w_{\text{comp}} + w_{\text{hist}}}, \quad W_{\text{hist}} = \frac{w_{\text{hist}}}{w_{\text{comp}} + w_{\text{hist}}}$$
   Ensuring $W_i \in [0, 1]$ and $\sum W_i = 1.0$.

### 3.2 Synthesis Scenarios & Fallbacks
For each evaluated segment with base rate $B$, comp recommendation $P_{\text{comp}}$, and parent interval historical average $P_{\text{hist}}$:

- **Case 1: Compression Scarcity Surge Active (`is_compression_surge == True`)**
  $$P_{\text{proposed}} = \max\left(\text{round}(B \cdot 1.30), \text{round}(P_{\text{comp}})\right), \quad \text{status} = \text{"SURGE\_INCREASE"}$$
- **Case 2: Standard Synthesis ($P_{\text{comp}} > 0$ and $P_{\text{hist}} > 0$)**
  $$P_{\text{proposed}} = \text{round}\left(W_{\text{comp}} \cdot P_{\text{comp}} + W_{\text{hist}} \cdot P_{\text{hist}}\right)$$
- **Case 3: Zero-History Fallback ($P_{\text{comp}} > 0$ and $P_{\text{hist}} \le 0$)**
  $$P_{\text{proposed}} = \text{round}(P_{\text{comp}}), \quad \text{zero\_history} = \text{True}$$
- **Case 4: Missing Comp Price Fallback ($P_{\text{comp}} \le 0$ and $P_{\text{hist}} > 0$)**
  $$P_{\text{proposed}} = \text{round}(P_{\text{hist}})$$
- **Case 5: Missing Both ($P_{\text{comp}} \le 0$ and $P_{\text{hist}} \le 0$)**
  $$P_{\text{proposed}} = B, \quad \text{status} = \text{"HOLD"}$$

The returned consensus dictionary schema includes an explicit `"zero_history": bool` flag (`True` when history is absent, `False` otherwise) alongside `hist_med: None`.

### 3.3 Status Classification
For non-surge cases, directional agreement is **not required**:
$$\text{status} = \begin{cases}
\text{"INCREASE"}, & \text{if } P_{\text{proposed}} > B \\
\text{"DECREASE"}, & \text{if } P_{\text{proposed}} < B \\
\text{"HOLD"}, & \text{if } P_{\text{proposed}} = B
\end{cases}$$

---

## 4. Implementation Plan & Detailed File Modifications

### 4.1 Component 1: Fallback Intervals Catalog & Settings

#### [NEW] `config/fallback_intervals.yaml`
```yaml
# Fallback Intervals Catalog for Closed-Calendar Periods
# Applied when Kivoya Streamline PMS calendar is closed (e.g. beyond May 31, 2027)

monthly_defaults:
  summer: # June, July, August
    months: [6, 7, 8]
    base_midweek: 389.0
    base_weekend: 449.0
    min_nights: 2
  fall_shoulder: # September
    months: [9]
    base_midweek: 399.0
    base_weekend: 549.0
    min_nights: 3
  winter_spring: # October through May
    months: [1, 2, 3, 4, 5, 10, 11, 12]
    base_midweek: 599.0
    base_weekend: 749.0
    min_nights: 3

holidays:
  - name: "4th of July"
    month: 7
    start_day: 1
    end_day: 5
    floor_rate: 699.0
    min_nights: 3
  - name: "Labor Day"
    floating: "first_monday_september"
    floor_rate: 499.0
    min_nights: 3
  - name: "Columbus Day"
    floating: "second_monday_october"
    floor_rate: 499.0
    min_nights: 3
  - name: "Thanksgiving"
    floating: "fourth_thursday_november"
    floor_rate: 499.0
    min_nights: 3
  - name: "Christmas & New Year"
    month: 12
    start_day: 24
    end_month: 1
    end_day: 2
    floor_rate: 499.0
    min_nights: 3
  - name: "Holy Week"
    floating: "easter_computus"
    floor_midweek: 699.0
    floor_weekend: 1049.0
    floor_rate: 699.0
    min_nights: 3
  - name: "Memorial Day"
    floating: "last_monday_may"
    floor_rate: 699.0
    min_nights: 3
```

#### [MODIFY] `config/settings.yaml`
```yaml
strategy:
  base_percentile: 65.0
  lead_time_tiers:
    urgent: 60
  anomaly_thresholds:
    urgent_percent_diff: 25.0
    moderate_percent_diff: 10.0
    urgent_lead_days: 60
  proposed_pricing:
    comp_weight: 0.67          # Double weight for comp-derived price (target percentile)
    historical_weight: 0.33    # Single weight for historical benchmark price
    weekend_premium_factor: 1.50 # Allocation factor for mixed stays
```

#### [MODIFY] `src/config.py`
Expose constants, fallback intervals loader, and dynamic reload support:
```python
DEFAULT_FALLBACK_INTERVALS_PATH = Path(__file__).resolve().parent.parent / "config" / "fallback_intervals.yaml"

def load_fallback_intervals(path: Path | str = DEFAULT_FALLBACK_INTERVALS_PATH) -> Dict[str, Any]:
    p = Path(path)
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}

FALLBACK_INTERVALS: Dict[str, Any] = load_fallback_intervals()

_pricing_cfg = _SETTINGS.get("strategy", {}).get("proposed_pricing", {})
COMP_PRICE_WEIGHT: float = float(_pricing_cfg.get("comp_weight", 0.67))
HISTORICAL_PRICE_WEIGHT: float = float(_pricing_cfg.get("historical_weight", 0.33))
WEEKEND_PREMIUM_FACTOR: float = float(_pricing_cfg.get("weekend_premium_factor", 1.50))

def reload_settings(config_path: Optional[str] = None) -> Dict[str, Any]:
    global _SETTINGS, URGENT_PCT_DIFF, MODERATE_PCT_DIFF, URGENT_LEAD_DAYS, BASE_PERCENTILE, CLEANING_FEE, COMP_PRICE_WEIGHT, HISTORICAL_PRICE_WEIGHT, WEEKEND_PREMIUM_FACTOR, FALLBACK_INTERVALS
    _SETTINGS = load_settings(config_path)
    FALLBACK_INTERVALS = load_fallback_intervals()
    ...
    _pricing_cfg = _SETTINGS.get("strategy", {}).get("proposed_pricing", {})
    COMP_PRICE_WEIGHT = float(_pricing_cfg.get("comp_weight", 0.67))
    HISTORICAL_PRICE_WEIGHT = float(_pricing_cfg.get("historical_weight", 0.33))
    WEEKEND_PREMIUM_FACTOR = float(_pricing_cfg.get("weekend_premium_factor", 1.50))
    return _SETTINGS
```

---

### 4.2 Component 2: Historical Interval Benchmark Service

#### [MODIFY] `src/reservation_intelligence.py`
1. **Query Update**:
   Update `get_confirmed_reservations()` query:
   ```sql
   SELECT * FROM reservations 
   WHERE (status_name IN ('Booked', 'Checked Out') OR LOWER(status_name) != 'cancelled')
     AND gross_rent > 0
     AND start_date IS NOT NULL 
     AND end_date IS NOT NULL
   ORDER BY start_date ASC
   ```
2. **Easter Computus & Holiday Date Resolver**:
   Add `compute_easter_date(year: int) -> date` and `resolve_holiday_dates(holiday_name: str, year: int) -> Tuple[date, date]`.
3. **Interval Benchmark Aggregation**:
   Implement `compute_interval_historical_benchmarks(seasonal_rates: List[Dict[str, Any]]) -> Dict[Tuple[str, str], Dict[str, Any]]`:
   - Categorize stay nights: Midweek (Sun–Wed, indices 6, 0, 1, 2) vs Weekend (Thu–Sat, indices 3, 4, 5).
   - Exclude floating/fixed holiday stay dates from regular month aggregations.
   - Allocate mixed-stay revenue via `WEEKEND_PREMIUM_FACTOR` ($1.50$).
   - Compute weighted average nightly rate: $\sum \text{Rent} / \sum \text{Nights}$.
   - Return complete benchmark dictionaries conforming to §2.4.

---

### 4.3 Component 3: Pricing Engine Core

#### [MODIFY] `src/proposed_prices.py`
1. **Segment-to-Parent Period Resolution**:
   ```python
   def find_parent_period_for_segment(
       segment: Dict[str, Any],
       parsed_rates: List[Dict[str, Any]]
   ) -> Optional[Dict[str, Any]]:
       """
       Resolve parent seasonal rate period for an evaluated market segment:
       Pass 1: Priority check for holiday/event periods.
       Pass 2: Check regular monthly periods.
       """
       c_raw = segment.get("check_in_dt") or segment.get("check_in")
       if isinstance(c_raw, str):
           c_in = datetime.strptime(c_raw, "%Y-%m-%d" if "-" in c_raw else "%m/%d/%Y").date()
       else:
           c_in = c_raw

       # Pass 1: Holiday priority
       for p in parsed_rates:
           if p.get("is_holiday") and (p["b_dt"] <= c_in <= p["e_dt"]):
               return p
       # Pass 2: Regular period
       for p in parsed_rates:
           if not p.get("is_holiday") and (p["b_dt"] <= c_in <= p["e_dt"]):
               return p
       return None
   ```
2. **Update `compute_interval_consensus`**:
   Accept `comp_weight` and `historical_weight`. Use simplified status evaluation and include `"zero_history": bool`:
   ```python
   if is_surge:
       consensus = max(round(base_r * 1.30), rec_r)
       status = "SURGE_INCREASE"
   elif rec_r > 0 and hist_cnt > 0 and hist_r > 0:
       consensus = round(w_comp * rec_r + w_hist * hist_r)
   elif rec_r > 0:
       consensus = rec_r
   elif hist_cnt > 0 and hist_r > 0:
       consensus = hist_r
   else:
       consensus = base_r

   if not is_surge:
       if consensus > base_r:
           status = "INCREASE"
       elif consensus < base_r:
           status = "DECREASE"
       else:
           status = "HOLD"

   res = {
       "check_in": segment.get("check_in"),
       "check_out": segment.get("check_out"),
       "segment_type": segment.get("segment_type", "midweek").lower(),
       "base_rate": base_r,
       "market_rec": rec_r,
       "hist_med": hist_r if (hist_cnt > 0 and hist_r > 0) else None,
       "consensus_rate": consensus,
       "status": status,
       "zero_history": bool(hist_cnt == 0 or hist_r <= 0),
   }
   if is_surge:
       res["is_compression_surge"] = True
       res["compression_details"] = segment.get("compression_details")
   return res
   ```
3. **Update `generate_proposed_prices` with Fallback Catalog & Test Isolation**:
   ```python
   def generate_proposed_prices(
       seasonal_rates: List[Dict[str, Any]],
       evaluated_segments: List[Dict[str, Any]],
       reference_date: Optional[date] = None,
       holidays_registry: Optional[List[Dict[str, Any]]] = None,
       churn_threshold_pct: float = MIN_PRICE_CHANGE_PCT,
       blocked_periods: Optional[List[Dict[str, Any]]] = None,
       open_end_date: Optional[date] = None,
       extend_to_horizon: bool = False,
       comp_weight: Optional[float] = None,
       historical_weight: Optional[float] = None,
       interval_benchmarks: Optional[Dict[Tuple[str, str], Dict[str, Any]]] = None,
       res_intel: Optional[Any] = None,
   ) -> List[Dict[str, Any]]:
   ```
   - For closed-calendar periods beyond `open_end_date`, synthetic rate generation consumes baseline rates dynamically from `FALLBACK_INTERVALS["monthly_defaults"]` rather than hardcoding numbers.
   - If `interval_benchmarks` is provided, use it.
   - If `interval_benchmarks` is None and segments do not already have non-empty `historical_benchmark` fixtures, compute benchmarks via `res_intel.compute_interval_historical_benchmarks()`.
   - Resolve parent period for each segment via `find_parent_period_for_segment` and attach interval benchmark only if not pre-populated in test mocks.

---

### 4.4 Component 4: UI & Dashboard Presentation

#### [MODIFY] `src/html_generator.py`
1. **Seasonal Interval Benchmark Attachment (Lines 362–373)**:
   Upgrade segment iteration to look up precomputed interval benchmarks matching parent seasonal rate periods, rather than calling the legacy rolling $\pm 15$-day window:
   ```python
   interval_benchmarks = res_intel.compute_interval_historical_benchmarks(seasonal_rates)
   parsed_rates = parse_rate_periods(seasonal_rates)
   for s in evaluated_segments:
       parent = find_parent_period_for_segment(s, parsed_rates)
       pname = parent.get("period_name") if parent else None
       stype = s.get("segment_type", "weekend").lower()
       bench = interval_benchmarks.get((pname, stype))
       if not bench:
           bench = res_intel.get_historical_benchmarks_for_interval(
               check_in_str=s.get("check_in", ""),
               segment_type=stype,
               proposed_rate=s.get("recommended_base_nightly_adj") or s.get("recommended_base_nightly"),
           )
       s["historical_benchmark"] = bench
   ```
2. **Tooltip & Badge Terminology**:
   Update tooltips in JavaScript (line ~2355) and Python template (lines ~3706, ~3709):
   `"Proposed increase from $X to $Y (+...)"` and `"Proposed decrease from $X to $Y (-...)"` (replacing deprecated "Agreed consensus").

---

### 4.5 Component 5: Test Suite Updates

#### [MODIFY] `tests/test_config.py`
1. Assert default constants: `COMP_PRICE_WEIGHT == 0.67`, `HISTORICAL_PRICE_WEIGHT == 0.33`, `WEEKEND_PREMIUM_FACTOR == 1.50`.
2. Test `load_fallback_intervals()` parsing all 6 recognized holidays and monthly defaults, and dynamic reload support.

#### [MODIFY] `tests/test_reservation_intelligence.py`
Add tests for:
1. `test_compute_easter_date_known_years`: Test Easter 2023 (April 9), 2024 (March 31), 2025 (April 20), 2026 (April 5), 2027 (March 28).
2. `test_resolve_holiday_dates_floating`: Test Columbus Day, Thanksgiving, Memorial Day, Labor Day across 2022–2027.
3. `test_compute_interval_historical_benchmarks_midweek_weekend`: Verify Sun–Wed classified as midweek and Thu–Sat as weekend.
4. `test_compute_interval_historical_benchmarks_night_attribution`: Verify reservation from Oct 29 to Nov 3 splits nightly revenue cleanly between October and November benchmarks.
5. `test_compute_interval_historical_benchmarks_holiday_exclusion`: Verify regular November excludes Thanksgiving, and regular October excludes Columbus Day.

#### [MODIFY] `tests/test_proposed_prices.py`
1. Update consensus tests for 67/33 weighting, new status codes (`INCREASE`, `DECREASE`, `HOLD`), and `"zero_history": True` flag.
2. Add test for `find_parent_period_for_segment` (holiday priority pass 1, regular period pass 2).
3. Update downstream aggregation tests with exact recalculated values:
   - Line 180: `self.assertEqual(p_dec["midweek_avg"], 438)` (was 450)
   - Line 181: `self.assertEqual(p_dec["midweek_med"], 438)` (was 450)
   - Line 184: `self.assertEqual(p_dec["weekend_avg"], 693)` (was 700)
   - Line 185: `self.assertEqual(p_dec["weekend_med"], 693)` (was 700)
   - Line 320: `self.assertEqual(p["weekend_avg"], 1567)` (was 1508)
   - Line 321: `self.assertEqual(p["weekend_med"], 1567)` (was 1508)
   - Line 624: `self.assertEqual(june["weekend_med"], 1157)` (was 1084)

#### [MODIFY] `tests/test_competitor_sales_tracker.py`
Update `test_consensus_policy_surge_override`:
Assert `res_normal["status"] == "DECREASE"` and `res_normal["consensus_rate"] == 932` (was `CONFLICT_HOLD` / 1000).

---

## 5. Verification & Validation Plan

### 5.1 Automated Testing
```bash
.venv/bin/python -m unittest tests/test_config.py
.venv/bin/python -m unittest tests/test_reservation_intelligence.py
.venv/bin/python -m unittest tests/test_proposed_prices.py
.venv/bin/python -m unittest tests/test_competitor_sales_tracker.py
```
Full test suite gating:
```bash
.venv/bin/python -m unittest discover tests
```
Must pass all 354+ tests under the Zero-Sleep Invariant (< 35 seconds).

---

## 6. Subagent Code Review Trajectory & Resolution Log

| ID | Severity | Category | Issue Description | Reviewer Proposal | Resolution in Design v1.4.0 |
| :---: | :---: | :---: | :--- | :--- | :--- |
| **REV-1** | `[BLOCKER]` | Architecture | `NameError: STRATEGY_CONFIG` in `src/config.py`. | Read from `_SETTINGS`. | **Resolved (§4.1)**: Uses `_SETTINGS.get("strategy", ...)`. |
| **REV-2** | `[BLOCKER]` | Test Suite | Downstream aggregation tests break under 67/33 weighting. | Document exact updated mathematical assertions. | **Resolved (§4.5)**: Documented exact new values (Dec mid 438, wkd 693; Holy Week 1567; June 1157). |
| **REV-3** | `[MAJOR]` | Logic Gap | Missing comp price $\le 0$ fallback omitted in code. | Explicit 5-case fallback logic. | **Resolved (§3.2 & §4.3)**: Full 5-case fallback implemented. |
| **REV-4** | `[MAJOR]` | Math Correctness | `cw + hw <= 0` bypassed if a weight is negative. | Non-negative convex guard `cw < 0 or hw < 0 or (cw + hw) <= 0`. | **Resolved (§3.1 & §4.3)**: Guard and warning log added. |
| **REV-5** | `[MAJOR]` | Robustness | `float(None)` crashes on explicit `None`. | Chained truthy access `rec_raw = ... or base`. | **Resolved (§4.3)**: Safe extraction implemented. |
| **REV-6** | `[MAJOR]` | Clarification | Definition of historical prices, Streamline intervals, and moving holidays. | Owner Q&A via `/grill-me`. | **Resolved (§1.1, §2.1–§2.3)**: Full interval benchmark service, Sun–Wed midweek, moving holiday resolver, weighted nightly average. |
| **REV-7** | `[BLOCKER]` | Architecture | Evaluated segments lack `interval`/`period_name`; no mapping to parent rate period. | Add `find_parent_period_for_segment` with 2-pass holiday priority. | **Resolved (§1.2 & §4.3)**: Added explicit date-overlap resolution algorithm. |
| **REV-8** | `[MAJOR]` | Test Isolation | Unconditional database query would overwrite mock fixtures in `test_proposed_prices.py`. | Add `interval_benchmarks` parameter and preserve pre-populated fixtures. | **Resolved (§4.3)**: Preserves mock fixtures and enables dependency injection. |
| **REV-9** | `[MAJOR]` | Schema Disconnect | Benchmark dict omitted legacy keys (`sample_count`, `median_rate`, etc.) used by `html_generator.py` and `reporter.py`. | Return full compatibility schema with both new and legacy keys. | **Resolved (§2.4)**: Standardized schema with all required reporting keys. |
| **REV-10**| `[MAJOR]` | Math Correctness | Regular month benchmarks contaminated by holiday bookings in prior years. | Exclude historical holiday stay nights from regular month calculations. | **Resolved (§2.3 & §4.2)**: Added strict holiday exclusion in regular months. |
| **REV-11**| `[MAJOR]` | Ambiguity | Ambiguous Holy Week dates and missing Easter computus algorithm. | Include Gregorian computus code and exact inclusive stay date rules. | **Resolved (§2.2 & §4.2)**: Anonymous Gregorian algorithm and exact night definitions added. |
| **REV-12**| `[MINOR]` | Architecture | `fallback_intervals.yaml` loader omitted from `src/config.py`. | Expose `FALLBACK_INTERVALS` and `load_fallback_intervals()`. | **Resolved (§4.1)**: Fallback loader and constant exposed. |
| **REV-13**| `[MINOR]` | Code Cleanliness | Redundant status condition `if not is_surge and (status != "HOLD" ...)`. | Simplify to direct comparison `if not is_surge: if consensus > base_r ...`. | **Resolved (§4.3)**: Simplified logic. |
| **REV-14**| `[NIT]` | Database Filter | `get_confirmed_reservations` filtered only `status_name = 'Booked'`, missing `Checked Out` stays. | Include `status_name IN ('Booked', 'Checked Out')`. | **Resolved (§4.2)**: Query updated. |
| **REV-15**| `[MAJOR]` | Architecture | Closed-calendar fallback catalog omitted holidays other than 4th of July and Labor Day. | Expand `fallback_intervals.yaml` to define all 6 recognized holidays with baseline rate floors. | **Resolved (§4.1)**: Added all 6 holidays to `config/fallback_intervals.yaml`. |
| **REV-16**| `[MAJOR]` | Math Correctness | Multi-day reservations crossing month boundaries attributed entirely to check-in date. | Exact night-level attribution: each night and its allocated revenue is attributed strictly to the month/interval it was stayed. | **Resolved (§1.1, §2.4)**: Night-level attribution algorithm implemented. |
| **REV-17**| `[MAJOR]` | Missing Rule | Columbus Day floating holiday rule omitted from algorithmic resolver. | Add floating rule for Columbus Day (Friday before 2nd Monday of October through Monday night, 4 nights). | **Resolved (§2.2, §4.2)**: Columbus Day rule added. |
| **REV-18**| `[MAJOR]` | Dashboard Metric Drift | HTML dashboard used legacy rolling $\pm 15$-day window while pricing engine used Streamline intervals. | Upgrade HTML dashboard segment loop in `src/html_generator.py` to use precomputed seasonal interval benchmarks matching parent rate periods. | **Resolved (§1.1, §4.4)**: HTML dashboard aligned to interval benchmark map. |
| **REV-19**| `[MINOR]` | Observability | Consensus dictionary lacked explicit boolean indicator for comp-only rates when history is absent. | Add explicit `"zero_history": bool` flag to result schema alongside `hist_med: None`. | **Resolved (§3.2, §4.3)**: `zero_history: bool` added. |
