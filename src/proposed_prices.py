"""
Proposed Prices Engine for Villa del Sol.
Synthesizes market comp recommendations with historical track record benchmarks to produce
actionable, PMS-ready rates aligned directly with Kivoya's seasonal rate periods.
"""

from datetime import date, datetime
import re
import statistics
from typing import Any, Dict, List, Optional


def clean_holiday_name(period_name: str) -> str:
    """
    Extract a clean holiday/event name from a Kivoya period name.
    e.g.:
      'Labor day 26' -> 'Labor Day'
      'Columbus Day 24' -> 'Columbus Day'
      'Thanksgiving 26' -> 'Thanksgiving'
      'Christmas & New Year 2026' -> 'Christmas & New Year'
      'Holy Week 27' -> 'Holy Week'
      'Memorial Day 27' -> 'Memorial Day'
    """
    if not period_name:
        return ""
    name = period_name.strip()
    # Strip trailing 2 or 4 digit years
    cleaned = re.sub(r"\s+20\d{2}$", "", name)
    cleaned = re.sub(r"\s+\d{2}$", "", cleaned)
    cleaned = cleaned.strip()

    # Normalization map for common holidays
    lower = cleaned.lower()
    if "labor" in lower:
        return "Labor Day"
    elif "columbus" in lower:
        return "Columbus Day"
    elif "thanksgiving" in lower:
        return "Thanksgiving"
    elif "christmas" in lower or "new year" in lower:
        return "Christmas & New Year"
    elif "holy week" in lower:
        return "Holy Week"
    elif "memorial" in lower:
        return "Memorial Day"
    elif "july 4" in lower or "independence" in lower:
        return "4th of July"
    return cleaned.title()


def compute_interval_consensus(segment: Dict[str, Any]) -> Dict[str, Any]:
    """
    Evaluate consensus for an individual interval segment:
    - Current Kivoya base rate B (our_base_nightly)
    - Market recommendation M (recommended_base_nightly_adj or recommended_base_nightly)
    - Historical median rate H (median_rate from historical_benchmark)

    Consensus Policy:
    - If M > B and H > B (both agree increase): consensus = round((M + H) / 2)
    - If M < B and H < B (both agree decrease): consensus = round((M + H) / 2)
    - If they conflict / disagree or no history: hold current base rate B.
    """
    base = float(segment.get("our_base_nightly", 0.0))
    rec = float(segment.get("recommended_base_nightly_adj", segment.get("recommended_base_nightly", base)))

    hist = segment.get("historical_benchmark", {})
    hist_cnt = hist.get("sample_count", 0)
    hist_med = float(hist.get("median_rate", 0.0))

    base_r = round(base)
    rec_r = round(rec)
    hist_r = round(hist_med)

    if hist_cnt > 0 and hist_med > 0:
        market_dir = 1 if rec_r > base_r else (-1 if rec_r < base_r else 0)
        hist_dir = 1 if hist_r > base_r else (-1 if hist_r < base_r else 0)

        if market_dir == 1 and hist_dir == 1:
            consensus = round((rec_r + hist_r) / 2.0)
            status = "AGREED_INCREASE"
        elif market_dir == -1 and hist_dir == -1:
            consensus = round((rec_r + hist_r) / 2.0)
            status = "AGREED_DECREASE"
        else:
            consensus = base_r
            status = "CONFLICT_HOLD"
    else:
        consensus = base_r
        status = "NO_HISTORY_HOLD"

    return {
        "check_in": segment.get("check_in"),
        "check_out": segment.get("check_out"),
        "segment_type": segment.get("segment_type", "midweek").lower(),
        "base_rate": base_r,
        "market_rec": rec_r,
        "hist_med": hist_r if hist_cnt > 0 else None,
        "consensus_rate": consensus,
        "status": status,
    }


def generate_proposed_prices(
    seasonal_rates: List[Dict[str, Any]],
    evaluated_segments: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Map evaluated interval consensus rates into Kivoya's seasonal rate schedule.
    Returns a list of period dictionaries matching Kivoya PMS rate structure:
    [
        {
            "from_date": "09/08/2026",
            "to_date": "09/30/2026",
            "period_name": "Sep. 26",
            "is_holiday": False,
            "holiday_name": "",
            "min_nights": 2,
            "midweek_base": 399,
            "midweek_avg": 420,
            "midweek_med": 418,
            "weekend_base": 549,
            "weekend_avg": 541,
            "weekend_med": 541,
            "special_base": None,
            "special_avg": None,
            "special_med": None,
            ...
        },
        ...
    ]
    """
    if not seasonal_rates:
        return []

    # Pre-calculate interval consensus for all segments
    interval_consensus_map: List[Dict[str, Any]] = []
    for s in evaluated_segments:
        c_item = compute_interval_consensus(s)
        try:
            c_in = datetime.strptime(s["check_in"], "%Y-%m-%d").date()
            c_out = datetime.strptime(s["check_out"], "%Y-%m-%d").date()
            c_item["start_dt"] = c_in
            c_item["end_dt"] = c_out
            interval_consensus_map.append(c_item)
        except Exception:
            continue

    proposed_periods: List[Dict[str, Any]] = []

    for rate in seasonal_rates:
        b_dt = rate.get("begin_dt")
        e_dt = rate.get("end_dt")
        if isinstance(b_dt, str):
            b_dt = datetime.strptime(b_dt, "%Y-%m-%d" if "-" in b_dt else "%m/%d/%Y").date()
        if isinstance(e_dt, str):
            e_dt = datetime.strptime(e_dt, "%Y-%m-%d" if "-" in e_dt else "%m/%d/%Y").date()

        if not b_dt or not e_dt:
            continue

        pname = rate.get("period_name", "")
        # A period is treated as a holiday/special rate if second_price is None or if name denotes a known holiday
        is_holiday = (rate.get("second_price") is None)
        holiday_label = clean_holiday_name(pname) if is_holiday else ""
        min_nights = int(rate.get("min_days", 2))

        # Find all open intervals that overlap with this period
        overlapping_intervals = [
            item for item in interval_consensus_map
            if item["start_dt"] <= e_dt and item["end_dt"] >= b_dt
        ]

        if is_holiday:
            cur_special = round(float(rate.get("first_price") or rate.get("nightly_rate") or 0.0))
            spec_rates = [item["consensus_rate"] for item in overlapping_intervals]
            if spec_rates:
                spec_avg = round(statistics.mean(spec_rates))
                spec_med = round(statistics.median(spec_rates))
            else:
                spec_avg = cur_special
                spec_med = cur_special

            proposed_periods.append({
                "from_date": b_dt.strftime("%m/%d/%Y"),
                "to_date": e_dt.strftime("%m/%d/%Y"),
                "period_name": pname,
                "is_holiday": True,
                "holiday_name": holiday_label,
                "min_nights": min_nights,
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
                "special_count": len(spec_rates),
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

            proposed_periods.append({
                "from_date": b_dt.strftime("%m/%d/%Y"),
                "to_date": e_dt.strftime("%m/%d/%Y"),
                "period_name": pname,
                "is_holiday": False,
                "holiday_name": "",
                "min_nights": min_nights,
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
