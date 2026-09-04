"""
Pricing Analytics & Revenue Management Engine.
Implements:
- Outlier filtering (IQR method)
- Dynamic lead-time percentile targeting (70th percentile far out, tapering to 45th close in)
- Total price vs effective nightly rate translation
- Recommended base nightly rate generation (factoring in $500 cleaning fee)
- 3-tier priority classification (Urgent weekly, Moderate monthly, Informational)
"""

import numpy as np
from typing import List, Dict, Any, Optional, Tuple


class PricingAnalyticsEngine:
    """Analyzes competitive price distributions and produces prioritized recommendations."""

    def __init__(
        self,
        base_percentile: float = 65.0,
        cleaning_fee: float = 500.0,
        urgent_pct_diff: float = 35.0,
        urgent_lead_days: int = 60,
        moderate_pct_diff: float = 10.0,
    ):
        self.base_percentile = base_percentile
        self.cleaning_fee = cleaning_fee
        self.urgent_pct_diff = urgent_pct_diff
        self.urgent_lead_days = urgent_lead_days
        self.moderate_pct_diff = moderate_pct_diff

    def get_target_percentile(self, lead_time_days: int, segment_type: str = "weekend") -> float:
        """
        Dynamically adjust target percentile by lead time and segment type:
        Weekend (Base Curve):
        - > 180 days: 70th percentile (capture early high-intent bookers)
        - 60 to 180 days: 65th percentile (standard booking window)
        - 30 to 60 days: 55th percentile (tapering to encourage booking)
        - < 30 days: 45th percentile (protect occupancy for near-term dates)

        Midweek (30% Lower Target Curve):
        - > 180 days: 49.0th percentile (70 * 0.70)
        - 60 to 180 days: 45.5th percentile (65 * 0.70)
        - 30 to 60 days: 38.5th percentile (55 * 0.70)
        - < 30 days: 31.5th percentile (45 * 0.70)
        """
        if lead_time_days > 180:
            base = 70.0
        elif lead_time_days >= 60:
            base = 65.0
        elif lead_time_days >= 30:
            base = 55.0
        else:
            base = 45.0

        if str(segment_type).lower() in ["midweek", "mid-week", "weekday"]:
            return round(base * 0.7, 1)
        return base

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
        floor_rate: float = 249.0,
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

        clean_comps = self.remove_outliers(comp_effective_rates)
        seg_type = segment.get("segment_type", "weekend")
        target_pct = self.get_target_percentile(lead_days, segment_type=seg_type)
        pct_stats = self.calculate_percentiles(clean_comps, target_pct)
        target_eff = pct_stats["target_val"]

        our_rank = self.compute_our_percentile_rank(our_eff, clean_comps)

        # Percent discrepancy relative to target
        if target_eff > 0:
            pct_diff = round(((our_eff - target_eff) / target_eff) * 100.0, 1)
        else:
            pct_diff = 0.0

        rec_base = (
            self.translate_to_recommended_base_rate(target_eff, nights, channel_factor=channel_factor)
            if target_eff > 0 else our_base
        )
        rec_diff = round(rec_base - our_base, 0)

        # Priority classification: Normal (<10%), Review (10-35%), Urgent (>35%)
        abs_diff = abs(pct_diff)
        is_urgent = abs_diff >= self.urgent_pct_diff
        is_moderate = not is_urgent and abs_diff >= self.moderate_pct_diff

        # Sample size & statistical significance analysis
        n_comps = len(clean_comps)
        if n_comps == 0:
            sample_significance = "SOLD_OUT"
            sample_label = "🔥 Sold Out (N=0)"
            sample_note = "Zero available luxury comps in market (100% booked)."
        elif n_comps <= 4:
            sample_significance = "VERY_LOW"
            sample_label = f"🔥 Near Sold Out (N={n_comps})"
            sample_note = f"Only {n_comps} comps available. Extreme market compression."
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

        if abs_diff < 10.0 or rec_diff == 0:
            action_summary = ""
        else:
            base_pct = round((rec_diff / our_base) * 100.0) if our_base > 0 else 0
            if rec_diff < 0:
                action_summary = f"↓ Reduce base ${our_base:.0f} → ${rec_base:.0f} ({base_pct:.0f}%)"
            else:
                action_summary = f"↑ Increase base ${our_base:.0f} → ${rec_base:.0f} (+{base_pct:.0f}%)"

        if action_summary and sample_significance in ["SOLD_OUT", "VERY_LOW"]:
            action_summary += " • High compression"

        return {
            **segment,
            "n_comps": n_comps,
            "comps_count": n_comps,
            "comps_raw_count": len(comp_effective_rates),
            "comps_list": comp_metadata or [],
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
            "recommended_base_nightly": rec_base,
            "base_diff": rec_diff,
            "priority_tier": tier,
            "priority_label": tier_label,
            "status": status,
            "action_summary": action_summary,
        }
