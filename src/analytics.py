"""
Pricing Analytics & Revenue Management Engine.
Implements:
- Outlier filtering (IQR method)
- Dynamic lead-time percentile targeting (70th percentile far out, tapering to 45th close in)
- Total price vs effective nightly rate translation
- Recommended base nightly rate generation (factoring in $500 cleaning fee)
- 3-tier priority classification (Urgent weekly, Moderate monthly, Informational)
"""

import json
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import numpy as np


class PricingAnalyticsEngine:
    """Analyzes competitive price distributions and produces prioritized recommendations."""

    def __init__(
        self,
        base_percentile: float = 65.0,
        cleaning_fee: float = 500.0,
        urgent_pct_diff: float = 35.0,
        urgent_lead_days: int = 60,
        moderate_pct_diff: float = 10.0,
        registry_path: str = "config/comps_registry.json",
    ):
        self.base_percentile = base_percentile
        self.cleaning_fee = cleaning_fee
        self.urgent_pct_diff = urgent_pct_diff
        self.urgent_lead_days = urgent_lead_days
        self.moderate_pct_diff = moderate_pct_diff
        self.registry_path = Path(registry_path)
        self.comp_registry: Dict[str, Dict[str, Any]] = self._load_registry()

    def _load_registry(self) -> Dict[str, Dict[str, Any]]:
        """Load and index comps from registry by listing_id."""
        comps = {}
        if self.registry_path.exists():
            try:
                data = json.loads(self.registry_path.read_text(encoding="utf-8"))
                for tier in ("tier_a", "tier_b"):
                    for cid, comp in data.get(tier, {}).items():
                        comps[str(cid)] = comp
            except Exception:
                pass
        return comps

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

        # Enrich comp metadata with quality evaluation from registry and compute adjusted rates
        enriched_comps_list = []
        adj_comp_effective_rates = []
        for c in (comp_metadata or []):
            comp_dict = dict(c)
            cid = str(comp_dict.get("listing_id") or "")
            reg_comp = self.comp_registry.get(cid, {})
            is_valid = reg_comp.get("is_valid_comp", comp_dict.get("is_valid_comp", True))
            ratio = float(reg_comp.get("desirability_ratio", comp_dict.get("desirability_ratio", 1.0)))
            eff_rate = float(comp_dict.get("effective_nightly") or 0.0)

            comp_dict["is_valid_comp"] = is_valid
            comp_dict["desirability_ratio"] = ratio
            comp_dict["validity_reason"] = reg_comp.get("validity_reason", comp_dict.get("validity_reason", ""))
            comp_dict["rationale"] = reg_comp.get("rationale", comp_dict.get("rationale", ""))
            comp_dict["category_scores"] = reg_comp.get("category_scores", comp_dict.get("category_scores", {}))
            comp_dict["composite_score"] = reg_comp.get("composite_score", comp_dict.get("composite_score", 88.0))

            if eff_rate > 0 and is_valid and ratio > 0:
                adj_rate = round(eff_rate / ratio, 2)
                comp_dict["adjusted_effective_nightly"] = adj_rate
                adj_comp_effective_rates.append(adj_rate)
            else:
                comp_dict["adjusted_effective_nightly"] = eff_rate
                if is_valid and eff_rate > 0:
                    adj_comp_effective_rates.append(eff_rate)

            enriched_comps_list.append(comp_dict)

        if not adj_comp_effective_rates and comp_effective_rates:
            adj_comp_effective_rates = comp_effective_rates

        clean_comps = self.remove_outliers(comp_effective_rates)
        seg_type = segment.get("segment_type", "weekend")
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

        rec_base = (
            self.translate_to_recommended_base_rate(target_eff, nights, channel_factor=channel_factor)
            if target_eff > 0 else our_base
        )
        rec_diff = round(rec_base - our_base, 0)

        adj_rec_base = (
            self.translate_to_recommended_base_rate(adj_target_eff, nights, channel_factor=channel_factor)
            if adj_target_eff > 0 else our_base
        )
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

        if abs_diff < 10.0 or rec_diff == 0:
            action_summary = ""
        else:
            if rec_diff < 0:
                action_summary = f"↓ Reduce ${our_base:.0f} → ${rec_base:.0f}"
            else:
                action_summary = f"↑ Increase ${our_base:.0f} → ${rec_base:.0f}"

        if action_summary and sample_significance in ["SOLD_OUT", "VERY_LOW"]:
            action_summary += " • High compression"

        if adj_abs_diff < 10.0 or adj_rec_diff == 0:
            adj_action_summary = ""
        else:
            if adj_rec_diff < 0:
                adj_action_summary = f"↓ Reduce ${our_base:.0f} → ${adj_rec_base:.0f}"
            else:
                adj_action_summary = f"↑ Increase ${our_base:.0f} → ${adj_rec_base:.0f}"

        if adj_action_summary and len(clean_adj_comps) <= 4 and len(clean_adj_comps) > 0:
            adj_action_summary += " • High compression"

        return {
            **segment,
            "n_comps": n_comps,
            "comps_count": n_comps,
            "comps_raw_count": len(comp_effective_rates),
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
        }
