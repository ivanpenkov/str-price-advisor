"""
Pricing Analytics & Revenue Management Engine.
Implements:
- Outlier filtering (IQR method)
- Dynamic lead-time percentile targeting (70th percentile far out, tapering to 45th close in)
- Total price vs effective nightly rate translation
- Recommended base nightly rate generation (factoring in $500 cleaning fee)
- 3-tier priority classification (Urgent weekly, Moderate monthly, Informational)
"""

from datetime import datetime, date, timedelta
import json
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple, Union
import numpy as np

from src.config import (
    URGENT_PCT_DIFF,
    MODERATE_PCT_DIFF,
    URGENT_LEAD_DAYS,
    BASE_PERCENTILE,
    CLEANING_FEE,
    OPERATIONAL_FLOORS,
    MIN_PRICE_CHANGE_PCT,
)


def _to_date(val: Any) -> Optional[date]:
    """Coerce string, date, or datetime into a date object."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    if isinstance(val, str) and val.strip():
        raw = val.strip()[:10]
        fmt = "%m/%d/%Y" if "/" in raw else "%Y-%m-%d"
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            return None
def _get_easter(year: int) -> date:
    """Calculate Western Easter Sunday using Anonymous Gregorian algorithm."""
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


class PricingAnalyticsEngine:
    """Analyzes competitive price distributions and produces prioritized recommendations."""

    def __init__(
        self,
        base_percentile: float = BASE_PERCENTILE,
        cleaning_fee: float = CLEANING_FEE,
        urgent_pct_diff: float = URGENT_PCT_DIFF,
        urgent_lead_days: int = URGENT_LEAD_DAYS,
        moderate_pct_diff: float = MODERATE_PCT_DIFF,
        registry_path: str = "config/comps_registry.json",
        holidays_config_path: str = "config/holidays.json",
        res_intel: Optional[Any] = None,
        sales_tracker: Optional[Any] = None,
        min_price_change_pct: Optional[float] = None,
    ):
        self.base_percentile = base_percentile
        self.cleaning_fee = cleaning_fee
        self.urgent_pct_diff = urgent_pct_diff
        self.urgent_lead_days = urgent_lead_days
        self.moderate_pct_diff = moderate_pct_diff
        self.registry_path = Path(registry_path)
        self.holidays_config_path = Path(holidays_config_path)
        self.sales_tracker = sales_tracker
        self.comp_registry: Dict[str, Dict[str, Any]] = self._load_registry()
        self.excluded_comps: set = self._load_excluded_comps()
        self.holidays_registry: List[Dict[str, Any]] = self._load_holidays(self.holidays_config_path)
        self.min_price_change_pct: float = float(MIN_PRICE_CHANGE_PCT if min_price_change_pct is None else min_price_change_pct)
        if res_intel is not None:
            self.res_intel = res_intel
        else:
            try:
                from src.reservation_intelligence import ReservationIntelligence
                self.res_intel = ReservationIntelligence()
            except Exception:
                self.res_intel = None

    def _load_excluded_comps(self) -> set:
        """Load set of excluded/blacklisted and disqualified listing IDs."""
        if self.registry_path.exists():
            try:
                data = json.loads(self.registry_path.read_text(encoding="utf-8"))
                excluded = {str(k) for k in data.get("excluded_comps", {}).keys()}
                excluded.update({str(k) for k in data.get("disqualified", {}).keys()})
                return excluded
            except Exception:
                pass
        return set()

    def _load_registry(self) -> Dict[str, Dict[str, Any]]:
        """Load and index comps from registry by listing_id."""
        comps = {}
        if self.registry_path.exists():
            try:
                data = json.loads(self.registry_path.read_text(encoding="utf-8"))
                for tier in ("tier_a", "tier_b"):
                    for cid, comp in data.get(tier, {}).items():
                        comps[str(cid)] = comp
                for cid, comp in data.get("disqualified", {}).items():
                    c_copy = dict(comp)
                    c_copy["is_valid_comp"] = False
                    comps[str(cid)] = c_copy
            except Exception:
                pass
        return comps

    def _load_holidays(self, config_path: Union[Path, str]) -> List[Dict[str, Any]]:
        """Load holiday rules from config/holidays.json."""
        p = Path(config_path)
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return []

    def _match_holiday(
        self,
        check_in_dt: date,
        check_out_dt: date,
        period_name: str = "",
    ) -> Optional[Dict[str, Any]]:
        """Match an interval against the holidays registry."""
        p_clean = (period_name or "").strip().lower()
        for item in self.holidays_registry:
            # 1. Match by Kivoya period name, holiday name, or synonyms
            k_pname = (item.get("kivoya_period_name") or "").strip().lower()
            h_name = (item.get("holiday_name") or "").strip().lower()
            if p_clean:
                if k_pname and p_clean == k_pname:
                    return item
                if h_name and (h_name in p_clean or p_clean in h_name):
                    return item
                for syn in item.get("synonyms", []):
                    if syn.lower() in p_clean:
                        return item

            # 2. Match by explicit from_date / to_date
            f_raw = item.get("from_date")
            t_raw = item.get("to_date")
            if f_raw and t_raw:
                f_dt = _to_date(f_raw)
                t_dt = _to_date(t_raw)
                if f_dt and t_dt and not (t_dt < check_in_dt or f_dt > (check_out_dt - timedelta(days=1))):
                    return item

            # 3. Match by standard calendar holiday dates
            year = check_in_dt.year
            # Christmas & New Year: Dec 23 -> Jan 2 night (checkout Jan 3)
            if "christmas" in h_name or "new year" in h_name:
                c_start = date(year if check_in_dt.month == 12 else year - 1, 12, 23)
                c_end = date(year + 1 if check_in_dt.month == 12 else year, 1, 2)
                if not (c_end < check_in_dt or c_start > (check_out_dt - timedelta(days=1))):
                    return item

            # Thanksgiving: 4th Thursday of November -> Sunday night (checkout Monday)
            elif "thanksgiving" in h_name:
                thursdays = [d for d in (date(year, 11, day) for day in range(1, 31)) if d.weekday() == 3]
                if len(thursdays) >= 4:
                    tg_start = thursdays[3]
                    tg_end = tg_start + timedelta(days=3)  # Sunday night
                    if not (tg_end < check_in_dt or tg_start > (check_out_dt - timedelta(days=1))):
                        return item

            # Memorial Day: Friday -> Monday night (checkout Tuesday)
            elif "memorial" in h_name:
                mondays = [d for d in (date(year, 5, day) for day in range(1, 32)) if d.weekday() == 0]
                if mondays:
                    mem_mon = mondays[-1]
                    mem_start = mem_mon - timedelta(days=3)
                    mem_end = mem_mon  # Monday night
                    if not (mem_end < check_in_dt or mem_start > (check_out_dt - timedelta(days=1))):
                        return item

            # Labor Day: Friday -> Monday night (checkout Tuesday)
            elif "labor" in h_name:
                mondays = [d for d in (date(year, 9, day) for day in range(1, 31)) if d.weekday() == 0]
                if mondays:
                    lab_mon = mondays[0]
                    lab_start = lab_mon - timedelta(days=3)
                    lab_end = lab_mon  # Monday night
                    if not (lab_end < check_in_dt or lab_start > (check_out_dt - timedelta(days=1))):
                        return item

            # Columbus Day: Friday -> Monday night (checkout Tuesday)
            elif "columbus" in h_name:
                mondays = [d for d in (date(year, 10, day) for day in range(1, 32)) if d.weekday() == 0]
                if len(mondays) >= 2:
                    col_mon = mondays[1]
                    col_start = col_mon - timedelta(days=3)
                    col_end = col_mon  # Monday night
                    if not (col_end < check_in_dt or col_start > (check_out_dt - timedelta(days=1))):
                        return item

            # Holy Week: Friday before Palm Sunday -> Easter Sunday night
            elif "holy week" in h_name or "easter" in h_name:
                easter = _get_easter(year)
                hw_start = easter - timedelta(days=9)  # Friday before Palm Sunday
                hw_end = easter                        # Easter Sunday night
                if not (hw_end < check_in_dt or hw_start > (check_out_dt - timedelta(days=1))):
                    return item

        return None

    def get_floor_for_interval(
        self,
        check_in: Any,
        check_out: Any,
        segment_type: str,
        period_name: Optional[str] = None,
    ) -> float:
        """
        Compute the effective minimum base nightly rate floor for an interval:
        - Base operational floors: $300 Midweek / $450 Weekend (from OPERATIONAL_FLOORS)
        - Overriding holiday floors: if interval overlaps any holiday in config/holidays.json,
          takes max(operational_floor, holiday_floor).
        """
        is_midweek = str(segment_type).lower() in ["midweek", "mid-week", "weekday"]
        op_floor = float(OPERATIONAL_FLOORS.get("midweek", 300.0) if is_midweek else OPERATIONAL_FLOORS.get("weekend", 450.0))

        c_in = _to_date(check_in)
        c_out = _to_date(check_out)
        if not c_in or not c_out:
            return op_floor

        h_match = self._match_holiday(c_in, c_out, period_name=period_name or "")
        if h_match:
            if h_match.get("split_pricing"):
                if is_midweek:
                    h_floor = float(h_match.get("floor_midweek") or h_match.get("floor_rate") or op_floor)
                else:
                    h_floor = float(h_match.get("floor_weekend") or h_match.get("floor_rate") or op_floor)
            else:
                h_floor = float(h_match.get("floor_rate") or op_floor)
            return max(op_floor, h_floor)

        return op_floor

    def get_target_percentile(self, lead_time_days: int, segment_type: str = "weekend") -> float:
        """
        Dynamically adjust target percentile by lead time and segment type (fallback curve):
        - > 90 days: Weekend 67.5%, Midweek 47.5%
        - 31 to 90 days: Weekend 62.5%, Midweek 32.5%
        - 15 to 30 days: Weekend 52.5%, Midweek 32.5%
        - <= 14 days: Weekend 42.5%, Midweek 30.0%
        """
        is_midweek = str(segment_type).lower() in ["midweek", "mid-week", "weekday"]
        if lead_time_days > 90:
            return 47.5 if is_midweek else 67.5
        elif lead_time_days >= 31:
            return 32.5 if is_midweek else 62.5
        elif lead_time_days >= 15:
            return 32.5 if is_midweek else 52.5
        else:
            return 30.0 if is_midweek else 42.5

    def remove_outliers(self, prices: List[float]) -> List[float]:
        """
        Remove statistical outliers using 1.5 * IQR method.
        Requires at least 4 data points; otherwise returns original list.
        """
        if len(prices) < 4:
            return sorted(prices)

        arr = np.array(prices)
        q25, q75 = np.percentile(arr, 25), np.percentile(arr, 75)
        iqr = q75 - q25
        lower_bound = max(100.0, q25 - 1.5 * iqr)
        upper_bound = q75 + 1.5 * iqr

        filtered = [p for p in prices if lower_bound <= p <= upper_bound]
        return sorted(filtered if filtered else prices)

    def calculate_percentiles(self, prices: List[float], target_pct: float) -> Dict[str, float]:
        """Compute key statistical percentiles for clean comp prices."""
        if not prices:
            return {
                "min": 0.0,
                "p25": 0.0,
                "p50": 0.0,
                "p75": 0.0,
                "p80": 0.0,
                "max": 0.0,
                "target_val": 0.0,
            }

        arr = np.array(prices)
        return {
            "min": round(float(np.min(arr)), 2),
            "p25": round(float(np.percentile(arr, 25)), 2),
            "p50": round(float(np.percentile(arr, 50)), 2),
            "p75": round(float(np.percentile(arr, 75)), 2),
            "p80": round(float(np.percentile(arr, 80)), 2),
            "max": round(float(np.max(arr)), 2),
            "target_val": round(float(np.percentile(arr, target_pct)), 2),
        }

    def compute_our_percentile_rank(self, our_price: float, comp_prices: List[float]) -> float:
        """Calculate where our current effective rate falls within the comp distribution (0-100)."""
        if not comp_prices:
            return 50.0
        arr = np.array(comp_prices)
        count_below = np.sum(arr < our_price)
        count_equal = np.sum(arr == our_price)
        rank = (count_below + 0.5 * count_equal) / len(arr) * 100.0
        return round(float(rank), 1)

    def translate_to_recommended_base_rate(
        self,
        target_effective_nightly: float,
        nights: int,
        floor_rate: float = 300.0,
        ceiling_rate: float = 2499.0,
        channel_factor: float = 1.0,
    ) -> float:
        """
        Convert target effective nightly guest cost back into recommended base nightly rate,
        subtracting our $500 cleaning fee and accounting for OTA channel distribution markup.
        target_total = target_effective_nightly * nights
        target_kivoya_total = target_total / channel_factor
        recommended_base_total = target_kivoya_total - cleaning_fee
        recommended_base_nightly = recommended_base_total / nights
        """
        target_total = target_effective_nightly * nights
        target_kivoya_total = target_total / max(0.5, channel_factor)
        rec_base_total = max(0.0, target_kivoya_total - self.cleaning_fee)
        rec_base_nightly = rec_base_total / nights
        clamped = max(floor_rate, min(ceiling_rate, rec_base_nightly))
        return round(clamped, 0)

    def evaluate_segment(
        self,
        segment: Dict[str, Any],
        comp_effective_rates: List[float],
        comp_metadata: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Full evaluation for a single segment (weekend or midweek):
        1. Filters comp outliers
        2. Determines target percentile based on lead time
        3. Computes percentiles (50th, 75th, 80th, target)
        4. Compares our effective price to target
        5. Computes recommended base nightly rate
        6. Assigns priority tier (urgent, moderate, informational)
        """
        lead_days = segment["lead_time_days"]
        nights = segment["nights"]
        our_base = segment["our_base_nightly"]

        # Prefer live Airbnb guest checkout rate if available for pure apples-to-apples comparison
        live_eff = segment.get("our_airbnb_effective_nightly")
        if live_eff and live_eff > 0:
            our_eff = float(live_eff)
            kivoya_eff = segment["our_effective_nightly"]
            channel_factor = our_eff / kivoya_eff if kivoya_eff > 0 else 1.0
        else:
            our_eff = segment["our_effective_nightly"]
            channel_factor = 1.0

        # Enrich comp metadata with quality evaluation from registry and compute adjusted rates
        enriched_comps_list = []
        adj_comp_effective_rates = []
        valid_comp_effective_rates = []
        for c in (comp_metadata or []):
            comp_dict = dict(c)
            cid = str(comp_dict.get("listing_id") or "")
            if cid and cid in self.excluded_comps:
                continue
            # If comp_registry is loaded, ignore uncurated listings unless explicitly flagged valid (in tests)
            if self.comp_registry and cid not in self.comp_registry and "is_valid_comp" not in comp_dict:
                continue
            reg_comp = self.comp_registry.get(cid, {})
            is_valid = reg_comp.get("is_valid_comp", comp_dict.get("is_valid_comp", True if not self.comp_registry else False))
            if cid in self.comp_registry and not self.comp_registry[cid].get("is_valid_comp", True):
                is_valid = False
            ratio = float(reg_comp.get("desirability_ratio", comp_dict.get("desirability_ratio", 1.0)))
            eff_rate = float(comp_dict.get("effective_nightly") or 0.0)

            comp_dict["is_valid_comp"] = is_valid
            comp_dict["desirability_ratio"] = ratio
            comp_dict["validity_reason"] = reg_comp.get("validity_reason", comp_dict.get("validity_reason", ""))
            comp_dict["rationale"] = reg_comp.get("rationale", comp_dict.get("rationale", ""))
            comp_dict["category_scores"] = reg_comp.get("category_scores", comp_dict.get("category_scores", {}))
            comp_dict["composite_score"] = reg_comp.get("composite_score", comp_dict.get("composite_score", 88.0))

            if eff_rate > 0 and is_valid:
                valid_comp_effective_rates.append(eff_rate)
                if ratio > 0:
                    adj_rate = round(eff_rate / ratio, 2)
                    comp_dict["adjusted_effective_nightly"] = adj_rate
                    adj_comp_effective_rates.append(adj_rate)
                else:
                    comp_dict["adjusted_effective_nightly"] = eff_rate
                    adj_comp_effective_rates.append(eff_rate)
            else:
                comp_dict["adjusted_effective_nightly"] = eff_rate

            enriched_comps_list.append(comp_dict)

        if not adj_comp_effective_rates and comp_effective_rates and comp_metadata is None:
            adj_comp_effective_rates = comp_effective_rates

        effective_rates_to_use = valid_comp_effective_rates if comp_metadata is not None else comp_effective_rates
        clean_comps = self.remove_outliers(effective_rates_to_use)
        seg_type = segment.get("segment_type", "weekend")
        if self.sales_tracker:
            target_pct = self.sales_tracker.get_target_percentile(lead_days, segment_type=seg_type)
            reg_comps = self.sales_tracker.load_registered_comps()
            tot_reg = len(reg_comps) if reg_comps else (len(self.comp_registry) if self.comp_registry else 97)
            compression = self.sales_tracker.detect_market_compression(
                check_in=segment.get("check_in", ""),
                check_out=segment.get("check_out"),
                total_cohort_count=tot_reg,
                current_available_count=len(effective_rates_to_use),
            )
            if compression.get("is_compressed"):
                target_pct = min(90.0, target_pct + 15.0)
                segment["is_compression_surge"] = True
                segment["compression_details"] = compression
            else:
                segment["is_compression_surge"] = False
                segment.pop("compression_details", None)
        else:
            target_pct = self.get_target_percentile(lead_days, segment_type=seg_type)
        pct_stats = self.calculate_percentiles(clean_comps, target_pct)
        target_eff = pct_stats["target_val"]

        our_rank = self.compute_our_percentile_rank(our_eff, clean_comps)

        # Adjusted statistics (for valid comps adjusted by desirability ratio)
        clean_adj_comps = self.remove_outliers(adj_comp_effective_rates)
        adj_pct_stats = self.calculate_percentiles(clean_adj_comps, target_pct)
        adj_target_eff = adj_pct_stats["target_val"]
        adj_p50_eff = adj_pct_stats["p50"]
        adj_rank = self.compute_our_percentile_rank(our_eff, clean_adj_comps)

        # Percent discrepancy relative to target
        if target_eff > 0:
            pct_diff = round(((our_eff - target_eff) / target_eff) * 100.0, 1)
        else:
            pct_diff = 0.0

        if adj_target_eff > 0:
            adj_pct_diff = round(((our_eff - adj_target_eff) / adj_target_eff) * 100.0, 1)
        else:
            adj_pct_diff = 0.0

        cin = segment.get("check_in", "")
        cout = segment.get("check_out", "")
        pname = segment.get("period_name", "")
        floor_rate = self.get_floor_for_interval(cin, cout, seg_type, period_name=pname)

        rec_base = (
            self.translate_to_recommended_base_rate(target_eff, nights, floor_rate=floor_rate, channel_factor=channel_factor)
            if target_eff > 0 else max(floor_rate, our_base)
        )
        # Apply 5% churn threshold: hold current base price if proposed change is < 5%
        if our_base > 0 and abs(rec_base - our_base) / our_base < self.min_price_change_pct:
            rec_base = our_base
        rec_base = max(floor_rate, rec_base)
        rec_diff = round(rec_base - our_base, 0)

        adj_rec_base = (
            self.translate_to_recommended_base_rate(adj_target_eff, nights, floor_rate=floor_rate, channel_factor=channel_factor)
            if adj_target_eff > 0 else max(floor_rate, our_base)
        )
        # Apply 5% churn threshold on adjusted recommendation as well
        if our_base > 0 and abs(adj_rec_base - our_base) / our_base < self.min_price_change_pct:
            adj_rec_base = our_base
        adj_rec_base = max(floor_rate, adj_rec_base)
        adj_rec_diff = round(adj_rec_base - our_base, 0)

        # Priority classification: Normal (<10%), Review (10-35%), Urgent (>35%)
        abs_diff = abs(pct_diff)
        is_urgent = abs_diff >= self.urgent_pct_diff
        is_moderate = not is_urgent and abs_diff >= self.moderate_pct_diff

        adj_abs_diff = abs(adj_pct_diff)
        is_adj_urgent = adj_abs_diff >= self.urgent_pct_diff
        is_adj_moderate = not is_adj_urgent and adj_abs_diff >= self.moderate_pct_diff

        # Sample size & statistical significance analysis
        n_comps = len(clean_comps)
        if n_comps == 0:
            sample_significance = "SOLD_OUT"
            sample_label = "🔥 Sold Out (N=0)"
            sample_note = "Zero available luxury comps in market (100% booked)."
        elif n_comps <= 4:
            sample_significance = "VERY_LOW"
            sample_label = f"🔥 Near Sold Out (N={n_comps})"
            sample_note = f"Only {n_comps} comps available. Very low sample size."
        elif n_comps < 10:
            sample_significance = "LOW"
            sample_label = f"⚠️ Low Sample (N={n_comps})"
            sample_note = f"{n_comps} comps available. Lower statistical confidence."
        else:
            sample_significance = "ROBUST"
            sample_label = f"✅ Robust (N={n_comps})"
            sample_note = f"{n_comps} comps analyzed."

        if is_urgent:
            tier = "URGENT_ACTION"
            tier_label = "🚨 Urgent Update (This Week)"
            status = "OVERPRICED" if pct_diff > 0 else "UNDERPRICED"
        elif is_moderate:
            tier = "MODERATE_ADJUSTMENT"
            tier_label = "⚠️ Moderate Adjustment (Monthly)"
            status = "SLIGHTLY HIGH" if pct_diff > 0 else "SLIGHTLY LOW"
        else:
            tier = "INFORMATIONAL"
            tier_label = "✅ Competitive / Long Range"
            status = "ON TARGET"

        if is_adj_urgent:
            adj_tier = "URGENT_ACTION"
            adj_tier_label = "🚨 Urgent Update (This Week)"
            adj_status = "OVERPRICED" if adj_pct_diff > 0 else "UNDERPRICED"
        elif is_adj_moderate:
            adj_tier = "MODERATE_ADJUSTMENT"
            adj_tier_label = "⚠️ Moderate Adjustment (Monthly)"
            adj_status = "SLIGHTLY HIGH" if adj_pct_diff > 0 else "SLIGHTLY LOW"
        else:
            adj_tier = "INFORMATIONAL"
            adj_tier_label = "✅ Competitive / Long Range"
            adj_status = "ON TARGET"

        show_action = (abs_diff >= self.moderate_pct_diff or our_base < floor_rate) and rec_diff != 0
        if not show_action:
            action_summary = ""
        else:
            if rec_diff < 0:
                action_summary = f"↓ Reduce ${our_base:.0f} → ${rec_base:.0f}"
            else:
                action_summary = f"↑ Increase ${our_base:.0f} → ${rec_base:.0f}"

        if action_summary and segment.get("is_compression_surge") and len(clean_comps) > 0:
            action_summary += " • High compression"

        adj_show_action = (adj_abs_diff >= self.moderate_pct_diff or our_base < floor_rate) and adj_rec_diff != 0
        if not adj_show_action:
            adj_action_summary = ""
        else:
            if adj_rec_diff < 0:
                adj_action_summary = f"↓ Reduce ${our_base:.0f} → ${adj_rec_base:.0f}"
            else:
                adj_action_summary = f"↑ Increase ${our_base:.0f} → ${adj_rec_base:.0f}"

        if adj_action_summary and segment.get("is_compression_surge") and len(clean_adj_comps) > 0:
            adj_action_summary += " • High compression"

        cin = segment.get("check_in", "")
        stype = segment.get("segment_type", "weekend")
        proposed_rate_to_check = adj_rec_base if adj_rec_base > 0 else rec_base

        if self.res_intel and cin:
            try:
                hist_benchmark = self.res_intel.get_historical_benchmarks_for_interval(
                    check_in_str=cin,
                    segment_type=stype,
                    proposed_rate=proposed_rate_to_check,
                )
                lead_status = self.res_intel.get_lead_time_status(cin)
            except Exception:
                hist_benchmark = {
                    "sample_count": 0, "min_rate": 0.0, "max_rate": 0.0, "median_rate": 0.0, "avg_rate": 0.0,
                    "range_str": "No prior sales", "variance_pct": None, "flag": "NO_HISTORICAL_DATA",
                    "flag_label": "No Prior Sales", "flag_color": "#94a3b8", "matched_stays": []
                }
                lead_status = {"lead_days": 0, "status": "UNKNOWN", "label": "Unknown", "season": "Unknown"}
        else:
            hist_benchmark = {
                "sample_count": 0, "min_rate": 0.0, "max_rate": 0.0, "median_rate": 0.0, "avg_rate": 0.0,
                "range_str": "No prior sales", "variance_pct": None, "flag": "NO_HISTORICAL_DATA",
                "flag_label": "No Prior Sales", "flag_color": "#94a3b8", "matched_stays": []
            }
            lead_status = {"lead_days": 0, "status": "UNKNOWN", "label": "Unknown", "season": "Unknown"}

        return {
            **segment,
            "n_comps": n_comps,
            "comps_count": n_comps,
            "comps_raw_count": len(valid_comp_effective_rates) if comp_metadata is not None else len(comp_effective_rates),
            "comps_list": enriched_comps_list,
            "sample_significance": sample_significance,
            "sample_label": sample_label,
            "sample_note": sample_note,
            "target_percentile": target_pct,
            "our_percentile_rank": our_rank,
            "comp_p50_eff": pct_stats["p50"],
            "comp_p75_eff": pct_stats["p75"],
            "comp_p80_eff": pct_stats["p80"],
            "comp_target_eff": target_eff,
            "comp_min_eff": pct_stats["min"],
            "comp_max_eff": pct_stats["max"],
            "price_diff_percent": pct_diff,
            "floor_rate": floor_rate,
            "recommended_base_nightly": rec_base,
            "base_diff": rec_diff,
            "priority_tier": tier,
            "priority_label": tier_label,
            "status": status,
            "action_summary": action_summary,
            # Adjusted metrics
            "n_comps_adj": len(clean_adj_comps),
            "comp_p50_adj": adj_p50_eff,
            "comp_target_adj": adj_target_eff,
            "price_diff_percent_adj": adj_pct_diff,
            "recommended_base_nightly_adj": adj_rec_base,
            "base_diff_adj": adj_rec_diff,
            "priority_tier_adj": adj_tier,
            "priority_label_adj": adj_tier_label,
            "status_adj": adj_status,
            "action_summary_adj": adj_action_summary,
            "our_percentile_rank_adj": adj_rank,
            # Historical intelligence & booking pace
            "historical_benchmark": hist_benchmark,
            "lead_time_status": lead_status,
        }
