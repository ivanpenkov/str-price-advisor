"""
Unit tests for Proposed Prices Engine (src.proposed_prices).
"""

from datetime import date
import unittest

from src.proposed_prices import (
    clean_holiday_name,
    compute_interval_consensus,
    generate_proposed_prices,
)


class TestProposedPrices(unittest.TestCase):
    """Test suite for consensus pricing and Kivoya rate period mapping."""

    def test_clean_holiday_name(self):
        self.assertEqual(clean_holiday_name("Labor day 26"), "Labor Day")
        self.assertEqual(clean_holiday_name("Columbus Day 24"), "Columbus Day")
        self.assertEqual(clean_holiday_name("Thanksgiving 26"), "Thanksgiving")
        self.assertEqual(clean_holiday_name("Christmas & New Year 2026"), "Christmas & New Year")
        self.assertEqual(clean_holiday_name("Holy Week 27"), "Holy Week")
        self.assertEqual(clean_holiday_name("Memorial Day 27"), "Memorial Day")

    def test_compute_interval_consensus_agreed_increase(self):
        segment = {
            "check_in": "2026-10-15",
            "check_out": "2026-10-18",
            "segment_type": "weekend",
            "our_base_nightly": 400.0,
            "recommended_base_nightly_adj": 500.0,
            "historical_benchmark": {
                "sample_count": 3,
                "median_rate": 600.0,
            },
        }
        res = compute_interval_consensus(segment)
        self.assertEqual(res["base_rate"], 400)
        self.assertEqual(res["consensus_rate"], 550)  # round((500 + 600)/2)
        self.assertEqual(res["status"], "AGREED_INCREASE")

    def test_compute_interval_consensus_agreed_decrease(self):
        segment = {
            "check_in": "2026-09-06",
            "check_out": "2026-09-10",
            "segment_type": "midweek",
            "our_base_nightly": 500.0,
            "recommended_base_nightly_adj": 380.0,
            "historical_benchmark": {
                "sample_count": 2,
                "median_rate": 420.0,
            },
        }
        res = compute_interval_consensus(segment)
        self.assertEqual(res["base_rate"], 500)
        self.assertEqual(res["consensus_rate"], 400)  # round((380 + 420)/2)
        self.assertEqual(res["status"], "AGREED_DECREASE")

    def test_compute_interval_consensus_conflict_hold(self):
        # Market says reduce (380 < 500), history says increase (550 > 500)
        segment = {
            "check_in": "2026-09-13",
            "check_out": "2026-09-17",
            "segment_type": "midweek",
            "our_base_nightly": 500.0,
            "recommended_base_nightly_adj": 380.0,
            "historical_benchmark": {
                "sample_count": 2,
                "median_rate": 550.0,
            },
        }
        res = compute_interval_consensus(segment)
        self.assertEqual(res["base_rate"], 500)
        self.assertEqual(res["consensus_rate"], 500)  # Holds base
        self.assertEqual(res["status"], "CONFLICT_HOLD")

    def test_compute_interval_consensus_no_history(self):
        segment = {
            "check_in": "2026-09-13",
            "check_out": "2026-09-17",
            "segment_type": "midweek",
            "our_base_nightly": 500.0,
            "recommended_base_nightly_adj": 450.0,
            "historical_benchmark": {
                "sample_count": 0,
            },
        }
        res = compute_interval_consensus(segment)
        self.assertEqual(res["consensus_rate"], 500)
        self.assertEqual(res["status"], "NO_HISTORY_HOLD")

    def test_generate_proposed_prices_aggregation(self):
        seasonal_rates = [
            # Holiday period
            {
                "period_name": "Thanksgiving 26",
                "begin_dt": date(2026, 11, 26),
                "end_dt": date(2026, 11, 30),
                "first_price": 949.0,
                "second_price": None,
                "min_days": 3,
            },
            # Regular period
            {
                "period_name": "Dec 2026",
                "begin_dt": date(2026, 12, 1),
                "end_dt": date(2026, 12, 22),
                "first_price": 399.0,
                "second_price": 599.0,
                "min_days": 2,
            },
        ]

        evaluated_segments = [
            # Fall inside Dec 2026 (Midweek 1: agreed increase from 399 to 500)
            {
                "check_in": "2026-12-06",
                "check_out": "2026-12-10",
                "segment_type": "midweek",
                "our_base_nightly": 399.0,
                "recommended_base_nightly_adj": 480.0,
                "historical_benchmark": {"sample_count": 2, "median_rate": 520.0},
            },
            # Midweek 2: conflict, holds 399
            {
                "check_in": "2026-12-13",
                "check_out": "2026-12-17",
                "segment_type": "midweek",
                "our_base_nightly": 399.0,
                "recommended_base_nightly_adj": 350.0,
                "historical_benchmark": {"sample_count": 2, "median_rate": 450.0},
            },
            # Weekend in Dec 2026: agreed increase from 599 to 700
            {
                "check_in": "2026-12-03",
                "check_out": "2026-12-06",
                "segment_type": "weekend",
                "our_base_nightly": 599.0,
                "recommended_base_nightly_adj": 680.0,
                "historical_benchmark": {"sample_count": 3, "median_rate": 720.0},
            },
        ]

        periods = generate_proposed_prices(seasonal_rates, evaluated_segments)
        self.assertEqual(len(periods), 2)

        # 1. Thanksgiving (Holiday)
        p_hol = periods[0]
        self.assertTrue(p_hol["is_holiday"])
        self.assertEqual(p_hol["holiday_name"], "Thanksgiving")
        self.assertEqual(p_hol["min_nights"], 3)
        self.assertIsNone(p_hol["midweek_avg"])
        self.assertIsNone(p_hol["weekend_avg"])
        self.assertEqual(p_hol["special_base"], 949)
        self.assertEqual(p_hol["special_avg"], 949)

        # 2. Dec 2026 (Regular)
        p_dec = periods[1]
        self.assertFalse(p_dec["is_holiday"])
        self.assertEqual(p_dec["holiday_name"], "")
        self.assertEqual(p_dec["min_nights"], 2)
        self.assertIsNone(p_dec["special_avg"])
        # Midweek 1 consensus = 500, Midweek 2 consensus = 399
        # Avg = round((500 + 399)/2) = 450, Median = round(median([500, 399])) = 450
        self.assertEqual(p_dec["midweek_base"], 399)
        self.assertEqual(p_dec["midweek_avg"], 450)
        self.assertEqual(p_dec["midweek_med"], 450)
        # Weekend 1 consensus = 700
        self.assertEqual(p_dec["weekend_base"], 599)
        self.assertEqual(p_dec["weekend_avg"], 700)
        self.assertEqual(p_dec["weekend_med"], 700)


if __name__ == "__main__":
    unittest.main()
