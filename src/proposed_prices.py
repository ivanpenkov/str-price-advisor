"""
Proposed Prices Engine for Villa del Sol.
Synthesizes market comp recommendations with historical track record benchmarks to produce
actionable, PMS-ready rates aligned directly with Kivoya's seasonal rate periods.
"""

from datetime import date, datetime, timedelta
import json
import logging
from pathlib import Path
import re
import statistics
from typing import Any, Dict, List, Optional, Tuple

from src.config import COMP_PRICE_WEIGHT, HISTORICAL_PRICE_WEIGHT, FALLBACK_INTERVALS
from src.reservation_intelligence import clean_holiday_name

logger = logging.getLogger("proposed_prices")

DEFAULT_HOLIDAYS_CONFIG = Path("config/holidays.json")


def load_holidays_registry(config_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Load holiday and event rules from config/holidays.json."""
    path = config_path or DEFAULT_HOLIDAYS_CONFIG
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def find_holiday_config(
    period_name: str,
    holiday_label: str,
    b_dt: date,
    e_dt: date,
    registry: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Find matching holiday rule from registry."""
    for item in registry:
        if item.get("kivoya_period_name") and item["kivoya_period_name"].strip().lower() == period_name.strip().lower():
            return item
        if item.get("holiday_name") and item["holiday_name"].strip().lower() == holiday_label.strip().lower():
            if item.get("from_date") and item.get("to_date"):
                try:
                    f_d = datetime.strptime(item["from_date"], "%m/%d/%Y").date()
                    t_d = datetime.strptime(item["to_date"], "%m/%d/%Y").date()
                    if not (t_d < b_dt or f_d > e_dt):
                        return item
                except ValueError:
                    return item
            else:
                return item
    return None


def detect_orphan_slots(
    b_dt: date,
    e_dt: date,
    blocked_periods: Optional[List[Dict[str, Any]]],
) -> Optional[str]:
    """
    Detect if an orphan slot condition exists within or overlapping [b_dt, e_dt]:
    1. Gap of exactly 2 nights between reservations.
    2. Any weekend (Thursday-Saturday nights) with only 2 nights left unbooked.
    Returns an explanation string if an orphan slot is detected, else None.
    """
    if not blocked_periods:
        return None

    parsed_blocks = []
    booked_nights = set()
    for b in blocked_periods:
        s_raw = b.get("start_dt") or b.get("startdate") or b.get("start_date")
        e_raw = b.get("end_dt") or b.get("enddate") or b.get("end_date")
        if isinstance(s_raw, str):
            s_dt = datetime.strptime(s_raw, "%Y-%m-%d" if "-" in s_raw else "%m/%d/%Y").date()
        else:
            s_dt = s_raw
        if isinstance(e_raw, str):
            e_dt_val = datetime.strptime(e_raw, "%Y-%m-%d" if "-" in e_raw else "%m/%d/%Y").date()
        else:
            e_dt_val = e_raw
        if s_dt and e_dt_val:
            parsed_blocks.append((s_dt, e_dt_val))
            cur = s_dt
            while cur <= e_dt_val:
                booked_nights.add(cur)
                cur += timedelta(days=1)

    parsed_blocks.sort(key=lambda x: x[0])

    # 1. Check 2-night gap between consecutive reservations overlapping [b_dt, e_dt]
    for i in range(len(parsed_blocks) - 1):
        b1_start, b1_end = parsed_blocks[i]
        b2_start, b2_end = parsed_blocks[i + 1]
        gap_nights = (b2_start - b1_end).days - 1
        if gap_nights == 2:
            gap_start = b1_end + timedelta(days=1)
            gap_end = b2_start - timedelta(days=1)
            if not (gap_end < b_dt or gap_start > e_dt):
                return f"2-night gap between reservations ({gap_start.strftime('%m/%d')}–{gap_end.strftime('%m/%d')})"

    # 2. Check weekend (Thu-Sat) with only 2 nights left unbooked
    cur = b_dt - timedelta(days=3)
    while cur <= e_dt:
        if cur.weekday() == 3:  # Thursday
            thu = cur
            fri = cur + timedelta(days=1)
            sat = cur + timedelta(days=2)
            wkd = [thu, fri, sat]
            if any(b_dt <= d <= e_dt for d in wkd):
                avail = [d for d in wkd if d not in booked_nights]
                if len(avail) == 2 and any(b_dt <= d <= e_dt for d in avail):
                    day_names = " & ".join(d.strftime("%a %m/%d") for d in avail)
                    return f"Weekend of {thu.strftime('%m/%d')} has only 2 nights left ({day_names})"
        cur += timedelta(days=1)

    return None

MIN_PRICE_CHANGE_PCT = 0.05  # 5% deadband to reduce PMS update churn


def apply_churn_threshold(proposed: int, base: int, threshold_pct: float = MIN_PRICE_CHANGE_PCT) -> int:
    """
    If the relative price change is less than threshold_pct (default 5%),
    hold the current base price to prevent unnecessary PMS update churn.
    """
    if base <= 0:
        return proposed
    diff = abs(proposed - base)
    if (diff / base) < threshold_pct:
        return base
    return proposed


def parse_rate_periods_for_lookup(
    seasonal_rates: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Pre-parse seasonal rates into a normalized lookup list for fast parent interval matching.
    """
    parsed = []
    for r in seasonal_rates:
        b_raw = r.get("begin_dt")
        e_raw = r.get("end_dt")
        b_dt = datetime.strptime(b_raw, "%Y-%m-%d" if "-" in b_raw else "%m/%d/%Y").date() if isinstance(b_raw, str) else b_raw
        e_dt = datetime.strptime(e_raw, "%Y-%m-%d" if "-" in e_raw else "%m/%d/%Y").date() if isinstance(e_raw, str) else e_raw
        if isinstance(b_dt, datetime):
            b_dt = b_dt.date()
        if isinstance(e_dt, datetime):
            e_dt = e_dt.date()
        pname = r.get("period_name", "")
        clean_h = clean_holiday_name(pname)
        is_hol = bool(
            (r.get("second_price") is None)
            or r.get("is_holiday")
            or (clean_h in [
                "4th of July",
                "Labor Day",
                "Columbus Day",
                "Thanksgiving",
                "Christmas & New Year",
                "Holy Week",
                "Memorial Day",
            ])
        )
        parsed.append({
            "pname": pname,
            "period_name": pname,
            "b_dt": b_dt,
            "e_dt": e_dt,
            "is_holiday": is_hol,
        })
    return parsed


def find_parent_period_for_segment(
    segment: Dict[str, Any],
    parsed_rates: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """
    Resolve parent seasonal rate period for an evaluated market segment:
    Pass 1: Priority check for holiday/event periods.
    Pass 2: Check regular monthly periods.
    """
    c_raw = segment.get("check_in_dt") or segment.get("check_in")
    if not c_raw:
        return None
    if isinstance(c_raw, str):
        c_in = datetime.strptime(c_raw, "%Y-%m-%d" if "-" in c_raw else "%m/%d/%Y").date()
    elif isinstance(c_raw, datetime):
        c_in = c_raw.date()
    else:
        c_in = c_raw

    # Pass 1: Holiday priority
    for p in parsed_rates:
        b_dt = p.get("b_dt") or p.get("begin_dt")
        e_dt = p.get("e_dt") or p.get("end_dt")
        if isinstance(b_dt, str):
            b_dt = datetime.strptime(b_dt, "%Y-%m-%d" if "-" in b_dt else "%m/%d/%Y").date()
        elif isinstance(b_dt, datetime):
            b_dt = b_dt.date()
        if isinstance(e_dt, str):
            e_dt = datetime.strptime(e_dt, "%Y-%m-%d" if "-" in e_dt else "%m/%d/%Y").date()
        elif isinstance(e_dt, datetime):
            e_dt = e_dt.date()
        if p.get("is_holiday") and b_dt and e_dt and (b_dt <= c_in <= e_dt):
            return p

    # Pass 2: Regular period
    for p in parsed_rates:
        b_dt = p.get("b_dt") or p.get("begin_dt")
        e_dt = p.get("e_dt") or p.get("end_dt")
        if isinstance(b_dt, str):
            b_dt = datetime.strptime(b_dt, "%Y-%m-%d" if "-" in b_dt else "%m/%d/%Y").date()
        elif isinstance(b_dt, datetime):
            b_dt = b_dt.date()
        if isinstance(e_dt, str):
            e_dt = datetime.strptime(e_dt, "%Y-%m-%d" if "-" in e_dt else "%m/%d/%Y").date()
        elif isinstance(e_dt, datetime):
            e_dt = e_dt.date()
        if (not p.get("is_holiday")) and b_dt and e_dt and (b_dt <= c_in <= e_dt):
            return p

    return None


def compute_interval_consensus(
    segment: Dict[str, Any],
    comp_weight: Optional[float] = None,
    historical_weight: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Evaluate consensus for an individual interval segment using 2:1 weighted pricing:
    - Base rate B (our_base_nightly)
    - Competitor market recommendation M (recommended_base_nightly_adj or recommended_base_nightly)
    - Historical interval benchmark H (avg_rate or median_rate from historical_benchmark)

    Policy:
    - Compression surge override: if is_compression_surge=True -> max(round(B * 1.30), round(M)), SURGE_INCREASE
    - Standard synthesis: if M > 0 and hist_cnt > 0 and H > 0:
        W_comp = cw / (cw + hw), W_hist = hw / (cw + hw)
        consensus = round(W_comp * M + W_hist * H)
    - Zero-history fallback: if M > 0 and (hist_cnt == 0 or H <= 0):
        consensus = round(M), zero_history = True
    - Missing comp price fallback: if M <= 0 and hist_cnt > 0 and H > 0:
        consensus = round(H)
    - Missing both:
        consensus = round(B), status = "HOLD"

    Status classification (non-surge):
    - INCREASE if consensus > B
    - DECREASE if consensus < B
    - HOLD if consensus == B
    """
    base = float(segment.get("our_base_nightly") or 0.0)
    rec_val = segment.get("recommended_base_nightly_adj")
    if rec_val is None:
        rec_val = segment.get("recommended_base_nightly")
    rec = float(rec_val) if (rec_val is not None and float(rec_val) > 0) else 0.0

    hist = segment.get("historical_benchmark", {}) or {}
    hist_cnt = hist.get("sample_count", 0)
    hist_val = hist.get("avg_rate") if hist.get("avg_rate") is not None else hist.get("median_rate", 0.0)
    hist_r = round(float(hist_val or 0.0))

    base_r = round(base)
    rec_r = round(rec)
    market_rec_out = rec_r if rec > 0 else None

    # Resolve and defensively validate weights
    cw = comp_weight if comp_weight is not None else COMP_PRICE_WEIGHT
    hw = historical_weight if historical_weight is not None else HISTORICAL_PRICE_WEIGHT

    if cw < 0 or hw < 0 or (cw + hw) <= 0:
        logger.warning(
            f"Invalid weights: comp_weight={cw}, historical_weight={hw}. Falling back to defaults 0.67 / 0.33."
        )
        cw, hw = 0.67, 0.33

    w_comp = cw / (cw + hw)
    w_hist = hw / (cw + hw)

    is_surge = bool(segment.get("is_compression_surge"))

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
        "market_rec": market_rec_out,
        "hist_med": hist_r if (hist_cnt > 0 and hist_r > 0) else None,
        "consensus_rate": consensus,
        "status": status,
        "zero_history": bool(hist_cnt == 0 or hist_r <= 0),
    }
    if is_surge:
        res["is_compression_surge"] = True
        res["compression_details"] = segment.get("compression_details")
    return res


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
    """
    Map evaluated interval consensus rates into Kivoya's seasonal rate schedule.
    Returns a list of period dictionaries matching Kivoya PMS rate structure.
    """
    if not seasonal_rates:
        return []

    if reference_date is None:
        reference_date = date.today()

    if holidays_registry is None:
        holidays_registry = load_holidays_registry()

    if blocked_periods is None:
        try:
            from src.kivoya_client import KivoyaClient
            blocked_periods = KivoyaClient().get_blocked_periods()
        except Exception:
            blocked_periods = []

    if open_end_date is None:
        try:
            from src.kivoya_client import KivoyaClient
            open_end_date = KivoyaClient().get_calendar_open_end_date()
        except Exception:
            open_end_date = date(2027, 5, 31)

    if interval_benchmarks is None and res_intel is not None:
        interval_benchmarks = res_intel.compute_interval_historical_benchmarks(seasonal_rates)

    # Attach interval benchmarks to segments if available and not pre-populated in test fixtures
    if interval_benchmarks:
        temp_parsed_for_lookup = parse_rate_periods_for_lookup(seasonal_rates)

        for s in evaluated_segments:
            existing_bm = s.get("historical_benchmark")
            if existing_bm and existing_bm.get("sample_count", 0) > 0:
                continue
            stype = s.get("segment_type", "midweek").lower()
            parent = find_parent_period_for_segment(s, temp_parsed_for_lookup)
            if parent:
                pname = parent.get("pname") or parent.get("period_name")
                clean_h = clean_holiday_name(pname)
                bm = interval_benchmarks.get((pname, stype)) or interval_benchmarks.get((clean_h, stype))
                if bm:
                    s["historical_benchmark"] = bm

    # Pre-calculate interval consensus for all segments
    interval_consensus_map: List[Dict[str, Any]] = []
    for s in evaluated_segments:
        c_item = compute_interval_consensus(s, comp_weight=comp_weight, historical_weight=historical_weight)
        try:
            c_in = datetime.strptime(s["check_in"], "%Y-%m-%d" if "-" in s["check_in"] else "%m/%d/%Y").date()
            c_out = datetime.strptime(s["check_out"], "%Y-%m-%d" if "-" in s["check_out"] else "%m/%d/%Y").date()
            c_item["start_dt"] = c_in
            c_item["end_dt"] = c_out
            interval_consensus_map.append(c_item)
        except Exception:
            continue

    # 1. Expand rates with custom events if defined in holidays_registry
    import calendar
    # Determine the latest allowable date: the end of the last full calendar month strictly within 12 months of reference_date
    # For reference_date in September 2026 -> August 31, 2027
    cutoff_year = reference_date.year if reference_date.month == 1 else reference_date.year + 1
    cutoff_month = 12 if reference_date.month == 1 else reference_date.month - 1
    _, last_day_of_cutoff_month = calendar.monthrange(cutoff_year, cutoff_month)
    max_horizon_cutoff = date(cutoff_year, cutoff_month, last_day_of_cutoff_month)

    expanded_rates: List[Dict[str, Any]] = []
    temp_rates = [dict(r) for r in seasonal_rates]

    custom_events = [
        item for item in holidays_registry
        if item.get("custom_event") and item.get("from_date") and item.get("to_date")
    ]

    for item in custom_events:
        c_from_str = item["from_date"]
        c_to_str = item["to_date"]
        try:
            cf = datetime.strptime(c_from_str, "%m/%d/%Y").date()
            ct = datetime.strptime(c_to_str, "%m/%d/%Y").date()
        except ValueError:
            continue

        # Skip events beyond the 12-month horizon cutoff (e.g. 2028 WM Phoenix Open when reference_date is in 2026)
        if cf > max_horizon_cutoff:
            continue

        is_split = bool(item.get("split_pricing", False))
        cfg_floor = float(item.get("floor_rate", 0.0))
        cfg_mid_floor = float(item.get("floor_midweek") or cfg_floor)
        cfg_wkd_floor = float(item.get("floor_weekend") or cfg_floor)
        base_mid = float(item.get("base_midweek") or cfg_mid_floor)
        base_wkd = float(item.get("base_weekend") or cfg_wkd_floor or base_mid)
        event_rate = {
            "period_name": item.get("holiday_name", "Special Event"),
            "begin_dt": cf,
            "end_dt": ct,
            "nightly_rate": base_wkd,
            "first_interval": "Monday-Wednesday" if is_split else "All Days",
            "first_price": base_mid if is_split else base_wkd,
            "second_interval": "Thursday-Sunday" if is_split else None,
            "second_price": base_wkd if is_split else None,
            "currency": "USD",
            "min_days": item.get("min_nights", 3),
            "is_custom_event": True,
        }

        split_done = False
        new_temp = []
        for rate in temp_rates:
            b_val = rate.get("begin_dt")
            e_val = rate.get("end_dt")
            if isinstance(b_val, str):
                b_dt_val = datetime.strptime(b_val, "%Y-%m-%d" if "-" in b_val else "%m/%d/%Y").date()
            else:
                b_dt_val = b_val
            if isinstance(e_val, str):
                e_dt_val = datetime.strptime(e_val, "%Y-%m-%d" if "-" in e_val else "%m/%d/%Y").date()
            else:
                e_dt_val = e_val

            if not split_done and b_dt_val and e_dt_val and b_dt_val <= cf and ct <= e_dt_val:
                # Split existing period into pre, event, post
                if cf > b_dt_val:
                    pre_rate = dict(rate)
                    pre_rate["begin_dt"] = b_dt_val
                    pre_rate["end_dt"] = cf - timedelta(days=1)
                    new_temp.append(pre_rate)

                event_rate["currency"] = rate.get("currency", "USD")
                if not item.get("base_midweek"):
                    p_mid = float(rate.get("first_price") or item.get("floor_midweek") or item.get("floor_rate", 0.0))
                    event_rate["first_price"] = p_mid if is_split else float(rate.get("second_price") or p_mid)
                if not item.get("base_weekend"):
                    p_wkd = float(rate.get("second_price") or rate.get("first_price") or item.get("floor_weekend") or item.get("floor_rate", 0.0))
                    event_rate["second_price"] = p_wkd if is_split else None
                    event_rate["nightly_rate"] = p_wkd

                new_temp.append(event_rate)

                if ct < e_dt_val:
                    post_rate = dict(rate)
                    post_rate["begin_dt"] = ct + timedelta(days=1)
                    post_rate["end_dt"] = e_dt_val
                    new_temp.append(post_rate)

                split_done = True
            else:
                new_temp.append(rate)

        temp_rates = new_temp

        if not split_done:
            # Standalone custom event (outside existing rate periods, e.g. within horizon)
            temp_rates.append(event_rate)

    # 2. Check if Kivoya regular rate periods end before max_horizon_cutoff
    # If so, synthesize closed-calendar monthly periods up to max_horizon_cutoff (e.g. June, July, August 2027)
    def _parse_rate_date(val):
        if isinstance(val, str):
            return datetime.strptime(val, "%Y-%m-%d" if "-" in val else "%m/%d/%Y").date()
        return val

    regular_ends = [
        _parse_rate_date(r.get("end_dt")) for r in temp_rates
        if not r.get("is_custom_event") and r.get("end_dt")
    ]
    max_kivoya_end = max(regular_ends) if regular_ends else reference_date

    if extend_to_horizon and max_kivoya_end < max_horizon_cutoff:
        cur_start = max_kivoya_end + timedelta(days=1)
        while cur_start <= max_horizon_cutoff:
            _, last_day = calendar.monthrange(cur_start.year, cur_start.month)
            month_end = date(cur_start.year, cur_start.month, last_day)
            cur_end = min(month_end, max_horizon_cutoff)

            yy = cur_start.strftime("%y")
            pname = f"Sep. {yy}" if cur_start.month == 9 else f"{cur_start.strftime('%B')} {yy}"

            # Baseline catalog rates for closed months from FALLBACK_INTERVALS
            monthly_defaults = FALLBACK_INTERVALS.get("monthly_defaults", {})
            base_mid = 599.0
            base_wkd = 749.0
            def_min_nights = 3
            for cat, data in monthly_defaults.items():
                if cur_start.month in data.get("months", []):
                    base_mid = float(data.get("base_midweek", base_mid))
                    base_wkd = float(data.get("base_weekend", base_wkd))
                    def_min_nights = int(data.get("min_nights", def_min_nights))
                    break

            synthetic_rate = {
                "period_name": pname,
                "begin_dt": cur_start,
                "end_dt": cur_end,
                "nightly_rate": base_mid,
                "first_interval": "Monday-Wednesday",
                "first_price": base_mid,
                "second_interval": "Thursday-Sunday",
                "second_price": base_wkd,
                "currency": "USD",
                "min_days": 2 if cur_start.month in (7, 8) else def_min_nights,
                "is_synthetic": True,
            }
            temp_rates.append(synthetic_rate)
            cur_start = cur_end + timedelta(days=1)

    def _get_begin_date(r):
        b = r.get("begin_dt")
        if isinstance(b, str):
            return datetime.strptime(b, "%Y-%m-%d" if "-" in b else "%m/%d/%Y").date()
        return b or date.min

    # Exclude any periods that start after the maximum horizon cutoff
    temp_rates = [r for r in temp_rates if _get_begin_date(r) <= max_horizon_cutoff]
    temp_rates.sort(key=_get_begin_date)
    expanded_rates = temp_rates

    # 3. Parse all rate periods
    parsed_rates: List[Dict[str, Any]] = []
    for rate in expanded_rates:
        b_dt_raw = rate.get("begin_dt")
        e_dt_raw = rate.get("end_dt")
        if isinstance(b_dt_raw, str):
            b_dt = datetime.strptime(b_dt_raw, "%Y-%m-%d" if "-" in b_dt_raw else "%m/%d/%Y").date()
        else:
            b_dt = b_dt_raw
        if isinstance(e_dt_raw, str):
            e_dt = datetime.strptime(e_dt_raw, "%Y-%m-%d" if "-" in e_dt_raw else "%m/%d/%Y").date()
        else:
            e_dt = e_dt_raw

        if not b_dt or not e_dt:
            continue

        pname = rate.get("period_name", "")
        is_custom_event = bool(rate.get("is_custom_event", False))
        h_cfg_check = find_holiday_config(pname, clean_holiday_name(pname), b_dt, e_dt, holidays_registry)
        is_holiday = (rate.get("second_price") is None) or is_custom_event or (h_cfg_check is not None)
        holiday_label = h_cfg_check["holiday_name"] if (h_cfg_check and h_cfg_check.get("holiday_name")) else (pname if is_custom_event else (clean_holiday_name(pname) if is_holiday else ""))

        # Min nights rule: 2 nights for next 90 days, 3 nights if 90+ days in the future
        days_out = (b_dt - reference_date).days
        proposed_min_nights = 2 if days_out <= 90 else 3
        base_min_nights = int(rate.get("min_days", 2))

        # Hot summer months protection: never recommend 3 nights for July and August, stick to 2 nights minimum
        if b_dt.month in (7, 8) or e_dt.month in (7, 8):
            proposed_min_nights = 2

        # Orphan slot protection: never recommend 3 nights if a 2-night gap exists or weekend has only 2 nights left
        orphan_reason = detect_orphan_slots(b_dt, e_dt, blocked_periods)
        if orphan_reason:
            proposed_min_nights = min(proposed_min_nights, 2)

        # Calendar open status: Kivoya booking calendar is open through open_end_date
        is_cal_open = (b_dt <= open_end_date) if open_end_date else True

        parsed_rates.append({
            "rate_obj": rate,
            "b_dt": b_dt,
            "e_dt": e_dt,
            "pname": pname,
            "is_holiday": is_holiday,
            "holiday_label": holiday_label,
            "is_custom_event": is_custom_event,
            "min_nights": proposed_min_nights,
            "min_nights_base": base_min_nights,
            "orphan_slot_reason": orphan_reason,
            "is_calendar_open": is_cal_open,
        })

    # 3. First pass: pre-calculate regular periods to establish standard seasonal benchmarks
    reg_benchmarks: List[Dict[str, Any]] = []
    for p in parsed_rates:
        if not p["is_holiday"]:
            rate = p["rate_obj"]
            b_dt = p["b_dt"]
            e_dt = p["e_dt"]
            cur_mid = round(float(rate.get("first_price") or rate.get("nightly_rate") or 0.0))
            cur_wkd = round(float(rate.get("second_price") or cur_mid))

            # Strictly overlapping intervals for regular period
            overlapping = [
                item for item in interval_consensus_map
                if item["start_dt"] < e_dt and item["end_dt"] > b_dt
            ]
            mid_rates = [item["consensus_rate"] for item in overlapping if item["segment_type"] == "midweek"]
            mid_avg = round(statistics.mean(mid_rates)) if mid_rates else cur_mid
            mid_med = round(statistics.median(mid_rates)) if mid_rates else cur_mid

            wkd_rates = [item["consensus_rate"] for item in overlapping if item["segment_type"] == "weekend"]
            wkd_avg = round(statistics.mean(wkd_rates)) if wkd_rates else cur_wkd
            wkd_med = round(statistics.median(wkd_rates)) if wkd_rates else cur_wkd

            reg_benchmarks.append({
                "b_dt": b_dt,
                "e_dt": e_dt,
                "mid_base": cur_mid,
                "mid_avg": mid_avg,
                "mid_med": mid_med,
                "wkd_base": cur_wkd,
                "wkd_avg": wkd_avg,
                "wkd_med": wkd_med,
            })

    # 4. Second pass: construct proposed periods
    proposed_periods: List[Dict[str, Any]] = []

    for p in parsed_rates:
        rate = p["rate_obj"]
        b_dt = p["b_dt"]
        e_dt = p["e_dt"]
        pname = p["pname"]
        is_holiday = p["is_holiday"]
        holiday_label = p["holiday_label"]
        proposed_min_nights = p["min_nights"]
        base_min_nights = p["min_nights_base"]
        is_cal_open = p.get("is_calendar_open", True)

        # Strictly overlapping intervals (nights stayed in the period)
        overlapping_intervals = [
            item for item in interval_consensus_map
            if item["start_dt"] < e_dt and item["end_dt"] > b_dt
        ]

        if is_holiday:
            cur_special = round(float(rate.get("first_price") or rate.get("nightly_rate") or 0.0))

            # Match holiday rule from registry
            h_cfg = find_holiday_config(pname, holiday_label, b_dt, e_dt, holidays_registry)
            if h_cfg and h_cfg.get("holiday_name"):
                holiday_label = h_cfg["holiday_name"]
            prem_pct = h_cfg.get("premium_pct", 0) if h_cfg else 0
            cfg_floor = h_cfg.get("floor_rate", 0) if h_cfg else 0
            cfg_mid_floor = int(h_cfg.get("floor_midweek") or cfg_floor) if h_cfg else 0
            cfg_wkd_floor = int(h_cfg.get("floor_weekend") or cfg_floor) if h_cfg else 0
            if h_cfg and h_cfg.get("min_nights"):
                proposed_min_nights = h_cfg["min_nights"]

            # Hot summer months protection: never recommend 3 nights for July and August, stick to 2 nights minimum
            if b_dt.month in (7, 8) or e_dt.month in (7, 8):
                proposed_min_nights = 2

            orphan_reason = p.get("orphan_slot_reason")
            if orphan_reason:
                proposed_min_nights = min(proposed_min_nights, 2)

            is_split_pricing = bool(h_cfg and h_cfg.get("split_pricing"))

            # Find matching regular periods in the same or adjacent month to get standard benchmarks
            matching_regs = [
                r for r in reg_benchmarks
                if (r["b_dt"].month == b_dt.month and r["b_dt"].year == b_dt.year)
                or (r["e_dt"].month == e_dt.month and r["e_dt"].year == e_dt.year)
            ]
            if not matching_regs and reg_benchmarks:
                matching_regs = sorted(
                    reg_benchmarks,
                    key=lambda r: min(abs((r["b_dt"] - b_dt).days), abs((r["e_dt"] - e_dt).days))
                )[:1]

            if is_split_pricing:
                cur_mid = round(float(rate.get("first_price") or rate.get("nightly_rate") or 0.0))
                cur_wkd = round(float(rate.get("second_price") or cur_mid))

                # Midweek consensus rates from overlapping unbooked intervals
                mid_rates = [
                    item["consensus_rate"] for item in overlapping_intervals
                    if item["segment_type"] == "midweek"
                ]
                # Weekend consensus rates from overlapping unbooked intervals
                wkd_rates = [
                    item["consensus_rate"] for item in overlapping_intervals
                    if item["segment_type"] == "weekend"
                ]

                # Base consensus rates for this interval
                mid_avg = round(statistics.mean(mid_rates)) if mid_rates else cur_mid
                mid_med = round(statistics.median(mid_rates)) if mid_rates else cur_mid

                wkd_avg = round(statistics.mean(wkd_rates)) if wkd_rates else cur_wkd
                wkd_med = round(statistics.median(wkd_rates)) if wkd_rates else cur_wkd

                # Standard benchmarks for regular periods in the same or adjacent month
                if matching_regs:
                    std_mid_avg = max(max(r["mid_base"], r["mid_avg"]) for r in matching_regs)
                    std_mid_med = max(max(r["mid_base"], r["mid_med"]) for r in matching_regs)
                    std_wkd_avg = max(max(r["wkd_base"], r["wkd_avg"]) for r in matching_regs)
                    std_wkd_med = max(max(r["wkd_base"], r["wkd_med"]) for r in matching_regs)
                else:
                    std_mid_avg = cur_mid
                    std_mid_med = cur_mid
                    std_wkd_avg = cur_wkd
                    std_wkd_med = cur_wkd

                # Calculate separate, independent protective floors:
                # 1. Midweek floor: strictly based on configured floor_midweek (or floor_rate) and standard midweek rate + premium %
                if prem_pct > 0:
                    mid_floor_avg = max(cfg_mid_floor, round(std_mid_avg * (1.0 + prem_pct / 100.0)))
                    mid_floor_med = max(cfg_mid_floor, round(std_mid_med * (1.0 + prem_pct / 100.0)))
                else:
                    mid_floor_avg = cfg_mid_floor
                    mid_floor_med = cfg_mid_floor

                # 2. Weekend floor: strictly based on configured floor_weekend (or floor_rate) and standard weekend rate + premium %
                if prem_pct > 0:
                    wkd_floor_avg = max(cfg_wkd_floor, round(std_wkd_avg * (1.0 + prem_pct / 100.0)))
                    wkd_floor_med = max(cfg_wkd_floor, round(std_wkd_med * (1.0 + prem_pct / 100.0)))
                else:
                    wkd_floor_avg = cfg_wkd_floor
                    wkd_floor_med = cfg_wkd_floor

                # Apply protective floors to the interval's consensus rates
                if mid_floor_avg > 0:
                    mid_avg = max(mid_avg, mid_floor_avg)
                if mid_floor_med > 0:
                    mid_med = max(mid_med, mid_floor_med)
                if wkd_floor_avg > 0:
                    wkd_avg = max(wkd_avg, wkd_floor_avg)
                if wkd_floor_med > 0:
                    wkd_med = max(wkd_med, wkd_floor_med)

                # Apply 5% churn threshold: hold current base price if proposed change is < 5%
                mid_avg = apply_churn_threshold(mid_avg, cur_mid, churn_threshold_pct)
                mid_med = apply_churn_threshold(mid_med, cur_mid, churn_threshold_pct)
                wkd_avg = apply_churn_threshold(wkd_avg, cur_wkd, churn_threshold_pct)
                wkd_med = apply_churn_threshold(wkd_med, cur_wkd, churn_threshold_pct)

                proposed_periods.append({
                    "from_date": b_dt.strftime("%m/%d/%Y"),
                    "to_date": e_dt.strftime("%m/%d/%Y"),
                    "period_name": pname,
                    "is_holiday": True,
                    "is_custom_event": p.get("is_custom_event", False),
                    "split_pricing": True,
                    "holiday_name": holiday_label,
                    "min_nights": proposed_min_nights,
                    "min_nights_base": base_min_nights,
                    "min_nights_proposed": proposed_min_nights,
                    "orphan_slot_reason": orphan_reason,
                    "is_calendar_open": is_cal_open,
                    "midweek_base": cur_mid,
                    "midweek_avg": mid_avg,
                    "midweek_med": mid_med,
                    "weekend_base": cur_wkd,
                    "weekend_avg": wkd_avg,
                    "weekend_med": wkd_med,
                    "special_base": None,
                    "special_avg": None,
                    "special_med": None,
                    "midweek_count": len(mid_rates),
                    "weekend_count": len(wkd_rates),
                    "special_count": 0,
                })
            else:
                # Find matching regular periods in the same or adjacent month to get standard weekend benchmark
                if matching_regs:
                    std_wkd_base = max(r["wkd_base"] for r in matching_regs)
                    std_wkd_avg = max(max(r["wkd_base"], r["wkd_avg"]) for r in matching_regs)
                    std_wkd_med = max(max(r["wkd_base"], r["wkd_med"]) for r in matching_regs)
                else:
                    std_wkd_base = cur_special
                    std_wkd_avg = cur_special
                    std_wkd_med = cur_special

                # Special fallback floor: standard weekend rate + premium %, protected by floor_rate
                holiday_floor_avg = max(cfg_floor, round(std_wkd_avg * (1.0 + prem_pct / 100.0)), cur_special)
                holiday_floor_med = max(cfg_floor, round(std_wkd_med * (1.0 + prem_pct / 100.0)), cur_special)

                # Holidays only evaluate weekend consensus rates; midweek shoulder nights do not dilute holiday rates
                wkd_rates = [
                    item["consensus_rate"] for item in overlapping_intervals
                    if item["segment_type"] == "weekend"
                ]
                if wkd_rates:
                    calc_avg = round(statistics.mean(wkd_rates))
                    calc_med = round(statistics.median(wkd_rates))
                else:
                    calc_avg = cur_special
                    calc_med = cur_special

                # Primary: Market analysis drives pricing.
                # Fallback: When market analysis produces a rate lower than standard weekend + holiday premium or floor,
                # apply the special protective holiday floor.
                spec_avg = max(calc_avg, holiday_floor_avg)
                spec_med = max(calc_med, holiday_floor_med)

                # Apply 5% churn threshold: hold current base price if proposed change is < 5%
                spec_avg = apply_churn_threshold(spec_avg, cur_special, churn_threshold_pct)
                spec_med = apply_churn_threshold(spec_med, cur_special, churn_threshold_pct)

                proposed_periods.append({
                    "from_date": b_dt.strftime("%m/%d/%Y"),
                    "to_date": e_dt.strftime("%m/%d/%Y"),
                    "period_name": pname,
                    "is_holiday": True,
                    "is_custom_event": p.get("is_custom_event", False),
                    "split_pricing": False,
                    "holiday_name": holiday_label,
                    "min_nights": proposed_min_nights,
                    "min_nights_base": base_min_nights,
                    "min_nights_proposed": proposed_min_nights,
                    "orphan_slot_reason": orphan_reason,
                    "is_calendar_open": is_cal_open,
                    "midweek_base": None,
                    "midweek_avg": None,
                    "midweek_med": None,
                    "weekend_base": None,
                    "weekend_avg": None,
                    "weekend_med": None,
                    "special_base": cur_special,
                    "special_avg": spec_avg,
                    "special_med": spec_med,
                    "midweek_count": 0,
                    "weekend_count": 0,
                    "special_count": len(wkd_rates),
                })
        else:
            cur_mid = round(float(rate.get("first_price") or rate.get("nightly_rate") or 0.0))
            cur_wkd = round(float(rate.get("second_price") or cur_mid))

            mid_rates = [
                item["consensus_rate"] for item in overlapping_intervals
                if item["segment_type"] == "midweek"
            ]
            wkd_rates = [
                item["consensus_rate"] for item in overlapping_intervals
                if item["segment_type"] == "weekend"
            ]

            mid_avg = round(statistics.mean(mid_rates)) if mid_rates else cur_mid
            mid_med = round(statistics.median(mid_rates)) if mid_rates else cur_mid

            wkd_avg = round(statistics.mean(wkd_rates)) if wkd_rates else cur_wkd
            wkd_med = round(statistics.median(wkd_rates)) if wkd_rates else cur_wkd

            # Apply 5% churn threshold: hold current base price if proposed change is < 5%
            mid_avg = apply_churn_threshold(mid_avg, cur_mid, churn_threshold_pct)
            mid_med = apply_churn_threshold(mid_med, cur_mid, churn_threshold_pct)
            wkd_avg = apply_churn_threshold(wkd_avg, cur_wkd, churn_threshold_pct)
            wkd_med = apply_churn_threshold(wkd_med, cur_wkd, churn_threshold_pct)

            orphan_reason = p.get("orphan_slot_reason")

            proposed_periods.append({
                "from_date": b_dt.strftime("%m/%d/%Y"),
                "to_date": e_dt.strftime("%m/%d/%Y"),
                "period_name": pname,
                "is_holiday": False,
                "is_custom_event": p.get("is_custom_event", False),
                "split_pricing": False,
                "holiday_name": "",
                "min_nights": proposed_min_nights,
                "min_nights_base": base_min_nights,
                "min_nights_proposed": proposed_min_nights,
                "orphan_slot_reason": orphan_reason,
                "is_calendar_open": is_cal_open,
                "midweek_base": cur_mid,
                "midweek_avg": mid_avg,
                "midweek_med": mid_med,
                "weekend_base": cur_wkd,
                "weekend_avg": wkd_avg,
                "weekend_med": wkd_med,
                "special_base": None,
                "special_avg": None,
                "special_med": None,
                "midweek_count": len(mid_rates),
                "weekend_count": len(wkd_rates),
                "special_count": 0,
            })

    return proposed_periods

