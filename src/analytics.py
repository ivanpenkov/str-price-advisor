"""
Pricing Analytics & Revenue Management Engine.
Implements:
- Outlier filtering (IQR method)
- Dynamic lead-time percentile targeting (82nd percentile far out, tapering to 65th close in)
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
        base_percentile: float = 78.0,
        cleaning_fee: float = 500.0,
        urgent_pct_diff: float = 25.0,
        urgent_lead_days: int = 60,
        moderate_pct_diff: float = 10.0,
    ):
        self.base_percentile = base_percentile
        self.cleaning_fee = cleaning_fee
        self.urgent_pct_diff = urgent_pct_diff
        self.urgent_lead_days = urgent_lead_days
        self.moderate_pct_diff = moderate_pct_diff

    def get_target_percentile(self, lead_time_days: int) -> float:
        """
        Dynamically adjust target percentile by lead time:
        - > 180 days: 82nd percentile (capture early high-intent bookers)
        - 60 to 180 days: 78th percentile (prime booking window)
        - 30 to 60 days: 72nd percentile (tapering to protect occupancy)
        - < 30 days: 65th percentile (last-minute booking capture)
        """
        if lead_time_days > 180:
            return 82.0
        elif lead_time_days >= 60:
            return 78.0
        elif lead_time_days >= 30:
            return 72.0
        else:
            return 65.0

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
    ) -> float:
        """
        Convert target effective nightly guest cost back into recommended base nightly rate,
        subtracting our $500 cleaning fee.
        target_total = target_effective_nightly * nights
        recommended_base_total = target_total - cleaning_fee
        recommended_base_nightly = recommended_base_total / nights
        """
        target_total = target_effective_nightly * nights
        rec_base_total = max(0.0, target_total - self.cleaning_fee)
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
        our_eff = segment["our_effective_nightly"]
        our_base = segment["our_base_nightly"]

        clean_comps = self.remove_outliers(comp_effective_rates)
        target_pct = self.get_target_percentile(lead_days)
        pct_stats = self.calculate_percentiles(clean_comps, target_pct)
        target_eff = pct_stats["target_val"]

        our_rank = self.compute_our_percentile_rank(our_eff, clean_comps)

        # Percent discrepancy relative to target
        if target_eff > 0:
            pct_diff = round(((our_eff - target_eff) / target_eff) * 100.0, 1)
        else:
            pct_diff = 0.0

        rec_base = self.translate_to_recommended_base_rate(target_eff, nights) if target_eff > 0 else our_base
        rec_diff = round(rec_base - our_base, 0)

        # Priority classification
        abs_diff = abs(pct_diff)
        is_urgent = (
            abs_diff >= self.urgent_pct_diff
            or (lead_days <= self.urgent_lead_days and abs_diff >= 15.0)
        )
        is_moderate = (
            not is_urgent
            and abs_diff >= self.moderate_pct_diff
            and lead_days <= 180
        )

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

        action_summary = (
            f"Adjust base from ${our_base:.0f} to ${rec_base:.0f} ({'+' if rec_diff > 0 else ''}{rec_diff:.0f})"
            if abs(rec_diff) >= 20
            else "Keep current price"
        )

        return {
            **segment,
            "comps_count": len(clean_comps),
            "comps_raw_count": len(comp_effective_rates),
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
