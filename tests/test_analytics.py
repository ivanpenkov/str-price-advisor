"""Unit tests for PricingAnalyticsEngine."""

import unittest
from src.analytics import PricingAnalyticsEngine


class TestPricingAnalyticsEngine(unittest.TestCase):

    def setUp(self):
        self.engine = PricingAnalyticsEngine(
            base_percentile=78.0,
            cleaning_fee=500.0,
            urgent_pct_diff=25.0,
            urgent_lead_days=60,
            moderate_pct_diff=10.0,
        )

    def test_lead_time_tapering(self):
        """Lead time curves should taper from 82% far out to 65% close in."""
        self.assertEqual(self.engine.get_target_percentile(200), 82.0)
        self.assertEqual(self.engine.get_target_percentile(120), 78.0)
        self.assertEqual(self.engine.get_target_percentile(45), 72.0)
        self.assertEqual(self.engine.get_target_percentile(10), 65.0)

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
        """Urgent when >25% off target or imminent lead time."""
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
        # Comps average $1200/night effective
        comp_rates = [1000.0, 1100.0, 1150.0, 1200.0, 1250.0, 1300.0]
        eval_urgent = self.engine.evaluate_segment(segment_urgent, comp_rates)

        self.assertEqual(eval_urgent["priority_tier"], "URGENT_ACTION")
        self.assertEqual(eval_urgent["status"], "UNDERPRICED")
        self.assertTrue(eval_urgent["recommended_base_nightly"] > 399.0)


if __name__ == "__main__":
    unittest.main()

