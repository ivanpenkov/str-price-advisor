"""Unit tests for PricingAnalyticsEngine."""

import unittest
from src.analytics import PricingAnalyticsEngine


class TestPricingAnalyticsEngine(unittest.TestCase):

    def setUp(self):
        self.engine = PricingAnalyticsEngine(
            base_percentile=65.0,
            cleaning_fee=500.0,
            urgent_pct_diff=35.0,
            urgent_lead_days=60,
            moderate_pct_diff=10.0,
        )

    def test_lead_time_tapering(self):
        """Lead time curves should taper from 70% far out to 45% close in for weekends."""
        self.assertEqual(self.engine.get_target_percentile(200), 70.0)
        self.assertEqual(self.engine.get_target_percentile(120), 65.0)
        self.assertEqual(self.engine.get_target_percentile(45), 55.0)
        self.assertEqual(self.engine.get_target_percentile(10), 45.0)

    def test_midweek_target_percentiles(self):
        """Midweek target percentiles should be 30% lower than weekend percentiles."""
        self.assertEqual(self.engine.get_target_percentile(200, segment_type="midweek"), 49.0)
        self.assertEqual(self.engine.get_target_percentile(120, segment_type="midweek"), 45.5)
        self.assertEqual(self.engine.get_target_percentile(45, segment_type="midweek"), 38.5)
        # September / near-term midweek example: 45 * 0.70 = 31.5%
        self.assertEqual(self.engine.get_target_percentile(10, segment_type="midweek"), 31.5)

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
        """Urgent when >35% off target, Review when 10-35%, On Target when <10%."""
        # Case 1: Urgent (>35% underpriced)
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
        self.assertIn("High compression", result["action_summary"])


if __name__ == "__main__":
    unittest.main()

