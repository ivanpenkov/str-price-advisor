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
            # Regular period (within 90 days)
            {
                "period_name": "Dec 2026",
                "begin_dt": date(2026, 12, 1),
                "end_dt": date(2026, 12, 22),
                "first_price": 399.0,
                "second_price": 599.0,
                "min_days": 2,
            },
            # Future period (90+ days out)
            {
                "period_name": "January 27",
                "begin_dt": date(2027, 1, 4),
                "end_dt": date(2027, 1, 31),
                "first_price": 599.0,
                "second_price": 799.0,
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

        ref_date = date(2026, 9, 6)
        periods = generate_proposed_prices(seasonal_rates, evaluated_segments, reference_date=ref_date)
        self.assertEqual(len(periods), 3)

        # 1. Thanksgiving (Holiday within 90 days: days_out=81 -> min_nights=2, base=3)
        p_hol = periods[0]
        self.assertTrue(p_hol["is_holiday"])
        self.assertEqual(p_hol["holiday_name"], "Thanksgiving")
        self.assertEqual(p_hol["min_nights"], 2)
        self.assertEqual(p_hol["min_nights_base"], 3)
        self.assertIsNone(p_hol["midweek_avg"])
        self.assertIsNone(p_hol["weekend_avg"])
        self.assertEqual(p_hol["special_base"], 949)
        self.assertEqual(p_hol["special_avg"], 949)

        # 2. Dec 2026 (Regular within 90 days: days_out=86 -> min_nights=2, base=2)
        p_dec = periods[1]
        self.assertFalse(p_dec["is_holiday"])
        self.assertEqual(p_dec["holiday_name"], "")
        self.assertEqual(p_dec["min_nights"], 2)
        self.assertEqual(p_dec["min_nights_base"], 2)
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

        # 3. Jan 2027 (Regular 90+ days in future: days_out=120 -> min_nights=3, base=2)
        p_jan = periods[2]
        self.assertFalse(p_jan["is_holiday"])
        self.assertEqual(p_jan["min_nights"], 3)
        self.assertEqual(p_jan["min_nights_base"], 2)
        self.assertEqual(p_jan["midweek_base"], 599)
        self.assertEqual(p_jan["weekend_base"], 799)

    def test_holiday_weekend_premium_rule(self):
        """Verify that holiday special rates are never cheaper than standard weekend rates and apply +10% premium floor."""
        seasonal_rates = [
            # Regular October: weekend base $599, proposed weekend $599
            {
                "period_name": "Oct 26",
                "begin_dt": date(2026, 10, 1),
                "end_dt": date(2026, 10, 7),
                "first_price": 399.0,
                "second_price": 599.0,
                "min_days": 2,
            },
            # Columbus Day: base $599, second_price None
            {
                "period_name": "Columbus Day 24",
                "begin_dt": date(2026, 10, 8),
                "end_dt": date(2026, 10, 12),
                "first_price": 599.0,
                "second_price": None,
                "min_days": 3,
            },
        ]
        # Midweek interval overlapping Columbus Day with a low rate of $389
        evaluated_segments = [
            {
                "check_in": "2026-10-11",
                "check_out": "2026-10-14",
                "segment_type": "midweek",
                "our_base_nightly": 532.0,
                "recommended_base_nightly_adj": 389.0,
                "historical_benchmark": {"sample_count": 2, "median_rate": 389.0},
            }
        ]
        periods = generate_proposed_prices(seasonal_rates, evaluated_segments, reference_date=date(2026, 9, 6))
        p_columbus = [p for p in periods if p["is_holiday"]][0]
        # Standard October weekend is $599. Holiday floor (+10%) is round(599 * 1.10) = $659.
        # It must NOT drop to the midweek rate of $389!
        self.assertEqual(p_columbus["special_base"], 599)
        self.assertEqual(p_columbus["special_avg"], 659)
        self.assertEqual(p_columbus["special_med"], 659)


if __name__ == "__main__":
    unittest.main()

