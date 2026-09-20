"""Unit tests for PricingAnalyticsEngine."""

import unittest
from src.analytics import PricingAnalyticsEngine


class TestPricingAnalyticsEngine(unittest.TestCase):

    def setUp(self):
        self.engine = PricingAnalyticsEngine(
            base_percentile=65.0,
            cleaning_fee=500.0,
            urgent_pct_diff=25.0,
            urgent_lead_days=60,
            moderate_pct_diff=10.0,
        )

    def test_lead_time_tapering(self):
        """Lead time curves should taper across the 4 horizons for weekends."""
        self.assertEqual(self.engine.get_target_percentile(200), 67.5)
        self.assertEqual(self.engine.get_target_percentile(60), 62.5)
        self.assertEqual(self.engine.get_target_percentile(20), 52.5)
        self.assertEqual(self.engine.get_target_percentile(10), 42.5)

    def test_midweek_target_percentiles(self):
        """Midweek target percentiles should follow the approved 4-tier matrix curve."""
        self.assertEqual(self.engine.get_target_percentile(200, segment_type="midweek"), 47.5)
        self.assertEqual(self.engine.get_target_percentile(60, segment_type="midweek"), 32.5)
        self.assertEqual(self.engine.get_target_percentile(20, segment_type="midweek"), 32.5)
        self.assertEqual(self.engine.get_target_percentile(10, segment_type="midweek"), 30.0)

    def test_operational_floors(self):
        """Recommended base rates must enforce operational floors: >= $300 Midweek, >= $450 Weekend."""
        # Low comp prices that would otherwise produce < $300 or < $450
        comp_rates = [200.0, 250.0, 280.0]
        
        # Midweek segment: recommended base must be clamped at $300
        mid_seg = {
            "check_in": "2026-10-05",
            "check_out": "2026-10-08",
            "nights": 3,
            "lead_time_days": 15,
            "segment_type": "midweek",
            "our_base_nightly": 350.0,
            "our_effective_nightly": 517.0,
        }
        res_mid = self.engine.evaluate_segment(mid_seg, comp_rates)
        self.assertGreaterEqual(res_mid["recommended_base_nightly"], 300.0)

        # Weekend segment: recommended base must be clamped at $450
        wkd_seg = {
            "check_in": "2026-10-09",
            "check_out": "2026-10-12",
            "nights": 3,
            "lead_time_days": 15,
            "segment_type": "weekend",
            "our_base_nightly": 500.0,
            "our_effective_nightly": 667.0,
        }
        res_wkd = self.engine.evaluate_segment(wkd_seg, comp_rates)
        self.assertGreaterEqual(res_wkd["recommended_base_nightly"], 450.0)

    def test_outlier_removal(self):
        """Should filter out extreme prices using IQR."""
        # Standard cluster around $800-$1200 with crazy outliers $50 and $99,999
        prices = [750.0, 800.0, 850.0, 900.0, 950.0, 1000.0, 1050.0, 1100.0, 1200.0, 99999.0]
        cleaned = self.engine.remove_outliers(prices)
        self.assertNotIn(99999.0, cleaned)
        self.assertEqual(len(cleaned), 9)

    def test_translate_to_base_rate(self):
        """Should properly translate effective total rate to base nightly rate with $500 cleaning fee."""
        # 3 nights stay at $1000/night target effective cost ($3000 total)
        # target_base_total = 3000 - 500 = 2500
        # recommended_base_nightly = 2500 / 3 = $833
        rec_base = self.engine.translate_to_recommended_base_rate(1000.0, nights=3)
        self.assertEqual(rec_base, 833.0)

    def test_priority_tier_classification(self):
        """Urgent when >25% off target, Review when 10-25%, On Target when <10%."""
        # Case 1: Urgent (>25% underpriced)
        segment_urgent = {
            "check_in": "2026-09-15",
            "check_out": "2026-09-18",
            "nights": 3,
            "lead_time_days": 12,
            "our_base_nightly": 399.0,
            "our_cleaning_fee": 500.0,
            "our_total_price": 1697.0,
            "our_effective_nightly": 565.67,
        }
        comp_rates = [1000.0, 1100.0, 1150.0, 1200.0, 1250.0, 1300.0]
        eval_urgent = self.engine.evaluate_segment(segment_urgent, comp_rates)

        self.assertEqual(eval_urgent["priority_tier"], "URGENT_ACTION")
        self.assertEqual(eval_urgent["status"], "UNDERPRICED")
        self.assertTrue(eval_urgent["recommended_base_nightly"] > 399.0)
        self.assertIn("↑ Increase", eval_urgent["action_summary"])

        # Case 2: Moderate / Review (10-35% overpriced)
        segment_review = {
            "check_in": "2026-10-15",
            "check_out": "2026-10-18",
            "nights": 3,
            "lead_time_days": 45,
            "our_base_nightly": 1000.0,
            "our_cleaning_fee": 500.0,
            "our_total_price": 3500.0,
            "our_effective_nightly": 1166.67,
        }
        # Target ~ $970 -> diff ~ +20%
        comp_rates_review = [850.0, 900.0, 950.0, 970.0, 1000.0]
        eval_review = self.engine.evaluate_segment(segment_review, comp_rates_review)
        self.assertEqual(eval_review["priority_tier"], "MODERATE_ADJUSTMENT")
        self.assertIn("↓ Reduce", eval_review["action_summary"])

        # Case 3: On Target (<10% difference)
        segment_ontarget = {
            "check_in": "2026-11-15",
            "check_out": "2026-11-18",
            "nights": 3,
            "lead_time_days": 75,
            "our_base_nightly": 800.0,
            "our_cleaning_fee": 500.0,
            "our_total_price": 2900.0,
            "our_effective_nightly": 966.67,
        }
        comp_rates_ontarget = [920.0, 940.0, 960.0, 970.0, 980.0]
        eval_ontarget = self.engine.evaluate_segment(segment_ontarget, comp_rates_ontarget)
        self.assertEqual(eval_ontarget["priority_tier"], "INFORMATIONAL")
        self.assertEqual(eval_ontarget["action_summary"], "")

    def test_market_compression_sold_out(self):
        """When N <= 4, should flag near sold out market compression."""
        segment = {
            "check_in": "2027-02-12",
            "check_out": "2027-02-15",
            "nights": 3,
            "lead_time_days": 160,
            "our_base_nightly": 599.0,
            "our_cleaning_fee": 500.0,
            "our_total_price": 2297.0,
            "our_effective_nightly": 765.67,
        }
        # Only 2 comps left
        comp_rates = [1800.0, 2200.0]
        result = self.engine.evaluate_segment(segment, comp_rates)
        self.assertEqual(result["n_comps"], 2)
        self.assertEqual(result["sample_significance"], "VERY_LOW")

    def test_evaluate_segment_strictly_ignores_unregistered_comps(self):
        """Curated registry should strictly discard unvetted organic search listings."""
        segment = {
            "check_in": "2026-11-20",
            "check_out": "2026-11-23",
            "nights": 3,
            "lead_time_days": 75,
            "our_base_nightly": 800.0,
            "our_cleaning_fee": 500.0,
            "our_total_price": 2900.0,
            "our_effective_nightly": 966.67,
        }
        comp_metadata = [
            {
                "listing_id": "1077813310260513265",  # Registered comp (Desert Diamond)
                "name": "Desert Diamond",
                "effective_nightly": 1250.0,
            },
            {
                "listing_id": "999999999999999",  # Unregistered organic search hit
                "name": "Random Organic House",
                "effective_nightly": 400.0,
            },
        ]
        eval_res = self.engine.evaluate_segment(segment, [1250.0, 400.0], comp_metadata=comp_metadata)
        # Unregistered listing must be completely excluded from enriched comps list
        comp_ids = [str(c["listing_id"]) for c in eval_res["comps_list"]]
        self.assertIn("1077813310260513265", comp_ids)
        self.assertNotIn("999999999999999", comp_ids)
        # Only the 1 registered comp is evaluated
        self.assertEqual(eval_res["n_comps_adj"], 1)

    def test_evaluate_segment_strictly_ignores_disqualified_comps(self):
        """Disqualified comps must not skew raw percentiles, target values, or comp counts."""
        segment = {
            "check_in": "2026-11-20",
            "check_out": "2026-11-23",
            "nights": 3,
            "lead_time_days": 75,
            "our_base_nightly": 800.0,
            "our_cleaning_fee": 500.0,
            "our_total_price": 2900.0,
            "our_effective_nightly": 966.67,
        }
        self.engine.comp_registry["valid_comp_1"] = {
            "listing_id": "valid_comp_1",
            "name": "Valid Luxury Estate",
            "is_valid_comp": True,
            "desirability_ratio": 1.0,
        }
        self.engine.comp_registry["disq_comp_1"] = {
            "listing_id": "disq_comp_1",
            "name": "Disqualified Listing",
            "is_valid_comp": False,
            "desirability_ratio": 0.5,
            "validity_reason": "Low rating",
        }
        comp_metadata = [
            {"listing_id": "valid_comp_1", "effective_nightly": 800.0},
            {"listing_id": "disq_comp_1", "effective_nightly": 2500.0},
        ]
        rates = [800.0, 2500.0]
        eval_res = self.engine.evaluate_segment(segment, rates, comp_metadata=comp_metadata)

        # Raw counts and percentiles must reflect only the valid comp
        self.assertEqual(eval_res["n_comps"], 1)
        self.assertEqual(eval_res["comps_count"], 1)
        self.assertEqual(eval_res["comps_raw_count"], 1)
        self.assertEqual(eval_res["comp_target_eff"], 800.0)
        self.assertEqual(eval_res["comp_p50_eff"], 800.0)
        self.assertEqual(eval_res["comp_min_eff"], 800.0)
        self.assertEqual(eval_res["comp_max_eff"], 800.0)

        # Disqualified comp is retained in comps_list for UI with is_valid_comp=False
        comps_by_id = {c["listing_id"]: c for c in eval_res["comps_list"]}
        self.assertTrue(comps_by_id["valid_comp_1"]["is_valid_comp"])
        self.assertFalse(comps_by_id["disq_comp_1"]["is_valid_comp"])

    def test_evaluate_segment_market_compression_boost(self):
        """When market compression triggers (scarcity or velocity), target_pct should be boosted by +15% (capped at 90%)."""
        class MockSalesTracker:
            def get_target_percentile(self, lead_days, segment_type="weekend"):
                return 65.0

            def load_registered_comps(self):
                return {f"comp_{i}": {} for i in range(100)}

            def detect_market_compression(self, check_in, check_out=None, total_cohort_count=None, current_available_count=None):
                is_comp = bool(current_available_count is not None and total_cohort_count and (current_available_count / total_cohort_count) < 0.20)
                return {
                    "is_compressed": is_comp,
                    "available_count": current_available_count,
                    "total_cohort_count": total_cohort_count,
                    "available_ratio": (current_available_count / total_cohort_count) if total_cohort_count else 1.0,
                    "surge_multiplier": 1.30 if is_comp else 1.0,
                    "reason": "High scarcity compression" if is_comp else "Normal availability",
                }

        self.engine.sales_tracker = MockSalesTracker()
        segment = {
            "check_in": "2026-11-01",
            "check_out": "2026-11-04",
            "nights": 3,
            "lead_time_days": 45,
            "segment_type": "weekend",
            "our_base_nightly": 900.0,
            "our_cleaning_fee": 500.0,
            "our_total_price": 3200.0,
            "our_effective_nightly": 1066.67,
        }
        # 15 comps available out of 100 registered comps -> 15% available (<20%) -> High Compression!
        rates = [700.0 + i * 20 for i in range(15)]
        eval_res = self.engine.evaluate_segment(segment, rates)
        self.assertTrue(eval_res["is_compression_surge"])
        self.assertEqual(eval_res["target_percentile"], 80.0)
        self.assertIn("compression_details", eval_res)
        self.assertIn("High compression", eval_res["action_summary"])


if __name__ == "__main__":
    unittest.main()

