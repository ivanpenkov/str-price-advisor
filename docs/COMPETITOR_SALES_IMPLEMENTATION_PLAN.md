# Competitor Sales Tracking & Absorption Velocity Engine
**Comprehensive Implementation Plan & Codebase Alignment Specification**

---

## 1. Executive Summary & Context

This implementation plan bridges the gap between the Product Requirements Document ([`docs/COMPETITOR_SALES_REQUIREMENTS.md`](file:///Users/ivanpe/str-price-advisor/docs/COMPETITOR_SALES_REQUIREMENTS.md)), Technical Design Document ([`docs/COMPETITOR_SALES_TRACKER_DESIGN.md`](file:///Users/ivanpe/str-price-advisor/docs/COMPETITOR_SALES_TRACKER_DESIGN.md)), and the existing codebase.

While foundational structures (database schema, initial diff logic, and dashboard Tab 5 UI) exist, a comprehensive audit revealed 9 critical functional and architectural gaps that must be implemented to satisfy all PRD requirements and execute the design specification.

---

## 2. Audit Findings & Gap Analysis

| # | Feature Area | Design Document Specification | Current Codebase State | Alignment Action |
|---|---|---|---|---|
| **1** | **Bayesian Shrinkage Priors** | §3.4 specifies a tiered $4 \times 2$ horizon matrix (`>90d`, `31–90d`, `15–30d`, `≤14d`) across Weekend and Midweek ($k = 3.0$). When $n = 0$, recommended targets default to baseline priors with empirical $P_{50} = \text{None}$. | Flat static priors (65.0% Weekend / 45.5% Midweek) used across all horizons regardless of lead time. | Replace flat priors with `HORIZON_PRIORS` mapping table and update `compute_strategy_grid()`. |
| **2** | **Dynamic Target Percentile API** | §3.7.1 specifies `CompetitorSalesTracker.get_target_percentile(lead_days, segment_type)` with in-memory caching of the 2D grid. | Method is absent from `CompetitorSalesTracker`. Only static hardcoded curves exist in `PricingAnalyticsEngine`. | Implement `get_target_percentile()` with caching in `CompetitorSalesTracker`. |
| **3** | **Market Compression Scarcity Engine** | §3.6 specifies evaluating pure market scarcity: triggered whenever active available comps drop below $20\%$ of cohort ($N_{\text{avail}} / N_{\text{total}} < 0.20$), triggering $+15\%$ percentile boost and $1.30\times$ surge floor. | Implemented in `CompetitorSalesTracker`. | Streamline `detect_market_compression()` to evaluate pure scarcity, support `current_available_count`, and return scarcity alerts. |
| **4** | **Analytics Engine Wiring** | §3.7.2 specifies `PricingAnalyticsEngine` taking `sales_tracker`, querying dynamic targets, and elevating target percentiles by $+15\%$ (capped at 90%) upon compression. | `PricingAnalyticsEngine` does not accept or call `sales_tracker`. | Wire `sales_tracker` into `PricingAnalyticsEngine` and update `evaluate_segment()`. |
| **5** | **Consensus Policy Override** | §3.6.3 specifies that when `is_compression_surge == True`, `compute_interval_consensus()` overrides `CONFLICT_HOLD` / `NO_HISTORY_HOLD` and assigns non-compounding surge rate with status `"SURGE_INCREASE"`. | `compute_interval_consensus()` has no awareness of compression or surge overrides. | Implement scarcity surge override and non-compounding rate formula in `compute_interval_consensus()`. |
| **6** | **Asynchronous Verification Bridge** | §5.2.1 specifies `diff_and_verify_staged_comps()` bridging newly scraped staged comps against predecessors, running defensive guards (Stages 1–6), throttled Playwright PDP checks, and 3-state resolution. | Method is missing. Only synchronous file-to-file diffing exists without live staged comp verification. | Implement `diff_and_verify_staged_comps()` in `CompetitorSalesTracker`. |
| **7** | **Advisory Report Alerts** | §7.4 specifies embedding ⚡ Market Compression Alerts and 🎯 Recent Confirmed Sales Ledger into `data/latest_report.md`. | `PriceReportGenerator` has no sales tracker integration and emits no compression or sales alerts. | Wire `sales_tracker` into `PriceReportGenerator` and embed both markdown sections. |
| **8** | **Pipeline Decoupling in CLI** | §7.1 defines 5 sequential decoupled phases (3a Comp Staging $\to$ 3b Sales Diffing & Verification $\to$ 4 Pricing Analytics $\to$ 4b Reporting $\to$ 4c Platform Comparison $\to$ 5 Dashboard). | Sales tracking was positioned at Step 4c *after* pricing analytics and reports had already completed. | Reorder execution pipeline in `src/cli.py` to match the 5-phase specification. |
| **9** | **Test Verification Matrix** | §9 specifies a 14-test verification matrix covering all defensive guards, Bayesian shrinkage, three-state PDP verification, and consensus overrides. | Test suite lacks dropped corridor tests, consensus surge tests, dynamic target tests, and reporter alert tests. | Implement all 14 tests in `tests/test_competitor_sales_tracker.py`. |

---

## 3. Detailed Component Implementation Specifications

### 3.1 `src/competitor_sales_tracker.py`

#### 3.1.1 Tiered Baseline Priors Matrix (§3.4)
```python
HORIZON_PRIORS = {
    ">90d": {
        "weekend": {"target": 67.5, "p75": 75.0},
        "midweek": {"target": 47.5, "p75": 55.0},
    },
    "31–90d": {
        "weekend": {"target": 62.5, "p75": 70.0},
        "midweek": {"target": 45.5, "p75": 52.5},
    },
    "15–30d": {
        "weekend": {"target": 52.5, "p75": 60.0},
        "midweek": {"target": 38.5, "p75": 45.0},
    },
    "≤14d": {
        "weekend": {"target": 42.5, "p75": 50.0},
        "midweek": {"target": 31.5, "p75": 38.0},
    },
}
BAYESIAN_SHRINKAGE_K = 3.0
```

- In `compute_strategy_grid()`:
  - For each cell $(H, T)$, retrieve `prior_target = HORIZON_PRIORS[H][T]["target"]` and `prior_p75 = HORIZON_PRIORS[H][T]["p75"]`.
  - When $n > 0$:
    $$\hat{Y}_{\text{target}} = \frac{n \cdot P_{50} + k \cdot Y_{\text{prior}}}{n + k}, \quad \hat{Y}_{\text{aggressive}} = \frac{n \cdot P_{75} + k \cdot Y_{\text{prior, P75}}}{n + k}$$
    `empirical_p50 = round(p50, 1)`, `empirical_p75 = round(p75, 1)`.
  - When $n = 0$:
    `empirical_p50 = None`, `empirical_p75 = None`, `recommended_target = prior_target`, `recommended_aggressive = prior_p75`.

#### 3.1.2 Empirical Dynamic Target Lookup API (§3.7.1)
```python
def get_target_percentile(self, lead_time_days: int, segment_type: str = "weekend") -> float:
    """
    Retrieve empirical Bayesian-shrunk target percentile for a lead-time horizon.
    Caches 2D strategy grid in-memory to prevent redundant SQLite queries across segments.
    """
    if self._cached_strategy_grid is None:
        self._cached_strategy_grid = self.compute_strategy_grid()
    
    horizon = self._categorize_lead_horizon(lead_time_days)
    norm_seg = self._normalize_segment_type(segment_type)
    return float(self._cached_strategy_grid["grid"][horizon][norm_seg]["recommended_target"])
```

#### 3.1.3 Market Compression Scarcity Engine (§3.6)
```python
def detect_market_compression(
    self, 
    check_in: str, 
    check_out: Optional[str] = None, 
    total_cohort_count: Optional[int] = None,
    current_available_count: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Evaluate pure market scarcity for a check-in interval:
    Unified Definition: Triggered when available comps drop below 20% of the active cohort
    (<20% available, i.e. >80% market absorption / unavailable).
      - All Comps (N=97): <= 19 comps available
      - Tier A (N=49):    <= 9 comps available
      - Tier B (N=48):    <= 9 comps available
    """
```
- Resolves `current_available_count` (either passed directly or queried from latest snapshot).
- Computes $\text{avail\_ratio} = \frac{N_{\text{avail}}}{N_{\text{total}}}$.
- Triggers `is_compressed = True` when $\text{avail\_ratio} < 0.20$.
- Returns:
  ```python
  {
      "is_compressed": is_compressed,
      "available_count": current_available_count,
      "total_cohort_count": total_cohort_count,
      "available_ratio": avail_ratio,
      "surge_multiplier": 1.30 if is_compressed else 1.0,
      "reason": reason
  }
  ```

#### 3.1.4 Queries for Alerts & Recent Transactions (§3.7.1)
- `get_recent_sales(lookback_days: int = 7) -> List[Dict[str, Any]]`: Returns verified sales confirmed in the last 7 days.
- `get_active_compression_alerts(lookback_hours: int = 48) -> List[Dict[str, Any]]`: Returns upcoming intervals currently meeting pure scarcity compression (<20% available).

#### 3.1.5 Asynchronous Verification Bridge (§5.2.1, §5.2.2)
```python
async def diff_and_verify_staged_comps(
    self,
    staged_intervals: Union[Dict[Tuple[str, str], List[Dict[str, Any]]], Dict[Tuple[str, str], Dict[str, Any]]],
    prev_snapshot_path: Optional[Path] = None,
    max_concurrent_checks: int = 5,
) -> List[Dict[str, Any]]:
```
- Performs Stage 1 predecessor bridging across snapshots.
- Executes defensive detection guards:
  - **Stage 2**: Live scan invariant (`is_live_scan == True` on both).
  - **Stage 3**: Scrape degradation guard ($>80\%$ drop or $\le 3$ comps).
  - **Stage 4**: Dropped corridor guard (geographic submarket drops from $\ge 2$ to 0).
  - **Stage 5**: Local search cache check (`data/cache/search_*.json`).
  - **Stage 6**: Curated registry filter (`tier_a` / `tier_b`, active only).
- Dispatches Playwright PDP verification with concurrency semaphore (`max_concurrent_checks = 5`).
- Enforces strict 3-state resolution:
  - `CONFIRMED_BLOCKED`: Upsert into SQLite, return confirmed sale.
  - `CONFIRMED_AVAILABLE`: Update local cache, purge any premature sales record.
  - `UNVERIFIED_ERROR`: Strictly omit from SQLite to guarantee 100% ledger purity.

---

### 3.2 `src/analytics.py`

#### 3.2.1 Initialization & Dynamic Target Integration (§3.7.2)
- Update `PricingAnalyticsEngine.__init__` to accept `sales_tracker: Optional[Any] = None`.
- In `evaluate_segment()`:
  ```python
  if self.sales_tracker:
      target_pct = self.sales_tracker.get_target_percentile(lead_days, segment_type=seg_type)
      compression = self.sales_tracker.detect_market_compression(
          check_in=segment["check_in"],
          check_out=segment.get("check_out"),
          total_cohort_count=len(comp_effective_rates)
      )
      if compression.get("is_compressed"):
          target_pct = min(90.0, target_pct + 15.0)
          segment["is_compression_surge"] = True
          segment["compression_details"] = compression
  else:
      target_pct = self.get_target_percentile(lead_days, segment_type=seg_type)
  ```
- Store `segment["target_percentile"] = target_pct`.

---

### 3.3 `src/proposed_prices.py`

#### 3.3.1 Consensus Policy Override (§3.6.3)
In `compute_interval_consensus(segment)`:
- Check `segment.get("is_compression_surge") == True`.
- When active:
  - Market compression pure scarcity (<20% available comps) explicitly overrides `CONFLICT_HOLD` and `NO_HISTORY_HOLD`.
  - Computes non-compounding surge rate:
    $$P_{\text{proposed, surge}} = \max\left(\text{round}(B \times 1.30), P_{\text{rec, surge}}\right)$$
  - Sets `consensus_rate = proposed_surge` and `status = "SURGE_INCREASE"`.
  - Attaches `is_compression_surge = True` and `compression_details`.

---

### 3.4 `src/reporter.py`

#### 3.4.1 Markdown Advisory Sections (§7.4)
- Update `PriceReportGenerator.__init__` to accept `sales_tracker: Optional[Any] = None`.
- In `_build_markdown_report()`:
  - If `sales_tracker` is present:
    1. **⚡ Market Compression & Scarcity Alerts**:
       Outputs callout boxes for any interval where $\Delta \text{Sales}_{48\text{h}} \ge 3$ or Relative Cohort Depletion $\ge 25\%$.
    2. **🎯 Recent Confirmed Competitor Sales Ledger (Past 7 Days)**:
       Renders Markdown table: Property Name, Stay Dates, Nights, Lead Days, Realized Rate, Quality-Adjusted Rate, Market Percentile, Status.

---

### 3.5 `src/cli.py`

#### 3.5.1 Execution Pipeline Decoupling (§7.1)
Reorganize `run_pipeline` into the 5 decoupled phases:
1. **Phase 3a**: Scrape comp listings across active intervals into staged dictionary.
2. **Phase 3b**: Initialize `sales_tracker = CompetitorSalesTracker()`, run `reconcile_with_latest_snapshot()`, and execute `diff_and_verify_staged_comps()`.
3. **Phase 4**: Initialize `analytics = PricingAnalyticsEngine(sales_tracker=sales_tracker)` and evaluate segments. `generate_proposed_prices()` applies consensus surge overrides.
4. **Phase 4b**: Initialize `reporter = PriceReportGenerator(sales_tracker=sales_tracker)` and write `pricing_report_*.md`, `latest_report.md`, `pricing_sheet_*.csv`, and final snapshot JSON.
5. **Phase 4c**: Cross-platform comparison.
6. **Phase 5**: Generate HTML dashboard (`HTMLDashboardGenerator`).

---

## 4. Test Verification Plan (14-Point Matrix)

The test suite in [`tests/test_competitor_sales_tracker.py`](file:///Users/ivanpe/str-price-advisor/tests/test_competitor_sales_tracker.py) will implement all 14 test cases defined in Design Document §9:

1. `test_diff_snapshots_detects_sales`: Verifies listing present in snapshot $T-1$ and missing in $T$ generates a verified sales record.
2. `test_reconcile_purges_available_listing`: Verifies listing reappearing in current snapshot or cache purges premature sale records.
3. `test_synthetic_scan_skipped`: Verifies intervals with `is_live_scan=False` do not generate sales events.
4. `test_scrape_degradation_guard`: Verifies comp count dropping from $\ge 8$ to $\le 3$ skips disappearance detection.
5. `test_dropped_corridor_guard`: Verifies an entire geographic submarket dropping to 0 skips candidate disappearances.
6. `test_earliest_detected_date_preserved`: Verifies repeated detections preserve earliest `detected_date` and max `lead_time_days`.
7. `test_bayesian_shrinkage_tiered_priors`: Verifies cell with $n < 3$ blends empirical median with horizon-specific priors; verifies $n=0$ returns baseline prior directly.
8. `test_monthly_lead_time_quartiles`: Verifies $P_{25}, P_{50}, P_{75}$ calculations across 12 calendar months.
9. `test_verify_listing_availability_three_states`: Verifies parser sets `blocked=True` only on explicit unavailable signals; network timeouts return `UNVERIFIED_ERROR` without inserting into SQLite.
10. `test_diff_and_verify_bridge_flow`: Verifies asynchronous bridge queries PDP for candidates and commits only confirmed blocked listings.
11. `test_market_compression_surge_detection`: Verifies interval with $< 20\%$ available comps triggers surge directive ($+15\%$ target percentile boost, $1.30\times$ floor); normal availability ($\ge 20\%$) does not trigger.
12. `test_consensus_policy_surge_override`: Verifies `compute_interval_consensus` overrides `CONFLICT_HOLD` and `NO_HISTORY_HOLD` when `is_compression_surge=True`.
13. `test_get_target_percentile_empirical_lookup`: Verifies `get_target_percentile()` maps lead days to horizons, uses cached grid, and returns Bayesian targets.
14. `test_reporter_embeds_sales_and_surge_alerts`: Verifies `latest_report.md` includes recent sales table and scarcity warnings.

---

## 5. Rollout & Quality Assurance Workflow

When execution begins:
1. **Targeted Unit Testing**: Run `tests/test_competitor_sales_tracker.py`, `tests/test_analytics.py`, and `tests/test_proposed_prices.py` iteratively to verify each module with zero-sleep mock executions (<1s).
2. **Full Test Suite Milestone**: Run `.venv/bin/python -m unittest discover tests` once all modules are updated.
3. **Subagent Code Review**: Execute mandatory multi-round subagent code review with fresh `Role: "Code Reviewer Round 1"` agent using `invoke_subagent`. Fix any reported issues and iterate to convergence.
4. **Mobile Push Notification**: Alert host via `~/.gemini/config/scripts/notify_mobile.sh` upon final completion.

