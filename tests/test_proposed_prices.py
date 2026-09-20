"""
Unit tests for Proposed Prices Engine (src.proposed_prices).
"""

from datetime import date
import unittest

from src.proposed_prices import (
    clean_holiday_name,
    compute_interval_consensus,
    generate_proposed_prices,
    find_parent_period_for_segment,
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
        self.assertEqual(res["consensus_rate"], 533)  # round(0.67*500 + 0.33*600) = 533
        self.assertEqual(res["status"], "INCREASE")
        self.assertFalse(res["zero_history"])

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
        self.assertEqual(res["consensus_rate"], 393)  # round(0.67*380 + 0.33*420) = 393
        self.assertEqual(res["status"], "DECREASE")
        self.assertFalse(res["zero_history"])

    def test_compute_interval_consensus_conflict_weighted(self):
        # Market says reduce (380 < 500), history says increase (550 > 500)
        # In 67/33, directional agreement is NOT required -> produces weighted rate 436 (DECREASE)
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
        self.assertEqual(res["consensus_rate"], 436)  # round(0.67*380 + 0.33*550) = 436
        self.assertEqual(res["status"], "DECREASE")
        self.assertFalse(res["zero_history"])

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
        self.assertEqual(res["consensus_rate"], 450)  # 100% comp price fallback
        self.assertEqual(res["status"], "DECREASE")
        self.assertTrue(res["zero_history"])
        self.assertEqual(res["market_rec"], 450)

    def test_compute_interval_consensus_missing_comp_price(self):
        # Case 4: Missing comp price (comp None or <= 0), history present -> consensus = round(P_hist)
        segment = {
            "check_in": "2026-09-13",
            "check_out": "2026-09-17",
            "segment_type": "midweek",
            "our_base_nightly": 500.0,
            "recommended_base_nightly_adj": None,
            "recommended_base_nightly": None,
            "historical_benchmark": {
                "sample_count": 3,
                "avg_rate": 650.0,
            },
        }
        res = compute_interval_consensus(segment)
        self.assertEqual(res["consensus_rate"], 650)
        self.assertEqual(res["status"], "INCREASE")
        self.assertIsNone(res["market_rec"])
        self.assertFalse(res["zero_history"])

        # Case 5: Missing both comp and history -> consensus = base, status = HOLD
        segment_both_missing = {
            "check_in": "2026-09-13",
            "check_out": "2026-09-17",
            "segment_type": "midweek",
            "our_base_nightly": 500.0,
            "recommended_base_nightly_adj": None,
            "recommended_base_nightly": None,
            "historical_benchmark": None,
        }
        res_both = compute_interval_consensus(segment_both_missing)
        self.assertEqual(res_both["consensus_rate"], 500)
        self.assertEqual(res_both["status"], "HOLD")
        self.assertIsNone(res_both["market_rec"])
        self.assertTrue(res_both["zero_history"])

    def test_compute_interval_consensus_custom_and_defensive_weights(self):
        segment = {
            "our_base_nightly": 500.0,
            "recommended_base_nightly": 600.0,
            "historical_benchmark": {"sample_count": 2, "median_rate": 400.0},
        }
        # 50/50 custom weight: (600 + 400)/2 = 500 -> HOLD
        res_5050 = compute_interval_consensus(segment, comp_weight=0.5, historical_weight=0.5)
        self.assertEqual(res_5050["consensus_rate"], 500)
        self.assertEqual(res_5050["status"], "HOLD")

        # Invalid weights (negative) -> falls back to default 67/33
        res_invalid = compute_interval_consensus(segment, comp_weight=-1.0, historical_weight=0.33)
        self.assertEqual(res_invalid["consensus_rate"], 534)  # 0.67*600 + 0.33*400 = 402 + 132 = 534
        self.assertEqual(res_invalid["status"], "INCREASE")

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
        # Pass holidays_registry=[] to test base fallback without external config overrides
        periods = generate_proposed_prices(seasonal_rates, evaluated_segments, reference_date=ref_date, holidays_registry=[])
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
        # Midweek 1: round(0.67*480 + 0.33*520) = 493
        # Midweek 2: round(0.67*350 + 0.33*450) = 383
        # Avg = round((493 + 383)/2) = 438, Median = 438
        self.assertEqual(p_dec["midweek_base"], 399)
        self.assertEqual(p_dec["midweek_avg"], 438)
        self.assertEqual(p_dec["midweek_med"], 438)
        # Weekend 1: round(0.67*680 + 0.33*720) = 693
        self.assertEqual(p_dec["weekend_base"], 599)
        self.assertEqual(p_dec["weekend_avg"], 693)
        self.assertEqual(p_dec["weekend_med"], 693)

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
        test_registry = [
            {
                "holiday_name": "Columbus Day",
                "kivoya_period_name": "Columbus Day 24",
                "premium_pct": 10,
                "floor_rate": 599,
                "min_nights": 3,
            }
        ]
        periods = generate_proposed_prices(
            seasonal_rates,
            evaluated_segments,
            reference_date=date(2026, 9, 6),
            holidays_registry=test_registry,
        )
        p_columbus = [p for p in periods if p["is_holiday"]][0]
        # Standard October weekend is $599. Holiday floor (+10%) is round(599 * 1.10) = $659.
        # It must NOT drop to the midweek rate of $389!
        self.assertEqual(p_columbus["special_base"], 599)
        self.assertEqual(p_columbus["special_avg"], 659)
        self.assertEqual(p_columbus["special_med"], 659)

    def test_churn_threshold_suppresses_minor_changes(self):
        """Verify that price adjustments under 5% are held at base price to prevent PMS churn."""
        from src.proposed_prices import apply_churn_threshold

        # Base = 500. 4% change = 520 (diff=20, 20/500=0.04 < 0.05) -> should hold 500
        self.assertEqual(apply_churn_threshold(520, 500, 0.05), 500)
        # Base = 500. 3% decrease = 485 (diff=15, 15/500=0.03 < 0.05) -> should hold 500
        self.assertEqual(apply_churn_threshold(485, 500, 0.05), 500)
        # Base = 500. 6% increase = 530 (diff=30, 30/500=0.06 >= 0.05) -> should propose 530
        self.assertEqual(apply_churn_threshold(530, 500, 0.05), 530)
        # Base = 500. 10% decrease = 450 (diff=50, 50/500=0.10 >= 0.05) -> should propose 450
        self.assertEqual(apply_churn_threshold(450, 500, 0.05), 450)

    def test_holiday_split_pricing(self):
        """Verify that holidays with split_pricing: true generate separate Midweek and Weekend rates."""
        seasonal_rates = [
            {
                "period_name": "Holy Week 27",
                "begin_dt": date(2027, 3, 19),
                "end_dt": date(2027, 3, 28),
                "first_price": 1099.0,
                "second_price": None,
                "min_days": 3,
            }
        ]
        registry = [
            {
                "holiday_name": "Holy Week",
                "kivoya_period_name": "Holy Week 27",
                "split_pricing": True,
                "floor_rate": 1000,
                "min_nights": 3,
            }
        ]
        evaluated_segments = [
            # Midweek segment: agreed decrease to 1000
            {
                "check_in": "2027-03-21",
                "check_out": "2027-03-25",
                "segment_type": "midweek",
                "our_base_nightly": 1099.0,
                "recommended_base_nightly_adj": 934.0,
                "historical_benchmark": {"sample_count": 2, "median_rate": 1066.0},
            },
            # Weekend segment: agreed increase to 1508
            {
                "check_in": "2027-03-25",
                "check_out": "2027-03-28",
                "segment_type": "weekend",
                "our_base_nightly": 1099.0,
                "recommended_base_nightly_adj": 1682.0,
                "historical_benchmark": {"sample_count": 2, "median_rate": 1335.0},
            },
        ]
        periods = generate_proposed_prices(
            seasonal_rates,
            evaluated_segments,
            reference_date=date(2026, 9, 6),
            holidays_registry=registry,
        )
        self.assertEqual(len(periods), 1)
        p = periods[0]
        self.assertTrue(p["is_holiday"])
        self.assertTrue(p["split_pricing"])
        self.assertEqual(p["holiday_name"], "Holy Week")
        self.assertEqual(p["min_nights"], 3)
        self.assertEqual(p["midweek_base"], 1099)
        self.assertEqual(p["midweek_avg"], 1000)
        self.assertEqual(p["midweek_med"], 1000)
        self.assertEqual(p["weekend_base"], 1099)
        self.assertEqual(p["weekend_avg"], 1567)
        self.assertEqual(p["weekend_med"], 1567)
        self.assertIsNone(p["special_base"])
        self.assertIsNone(p["special_avg"])
        self.assertIsNone(p["special_med"])

    def test_holiday_split_pricing_independent_floors(self):
        """Verify that split events use independent floors: midweek floor applies to midweek only, weekend floor to weekend only."""
        seasonal_rates = [
            {
                "period_name": "Holy Week 27",
                "begin_dt": date(2027, 3, 19),
                "end_dt": date(2027, 3, 28),
                "first_price": 1000.0,
                "second_price": 1500.0,
                "min_days": 3,
            }
        ]
        registry = [
            {
                "holiday_name": "Holy Week",
                "kivoya_period_name": "Holy Week 27",
                "split_pricing": True,
                "floor_midweek": 850,
                "floor_weekend": 1650,
                "min_nights": 3,
            }
        ]
        evaluated_segments = [
            # Midweek segment consensus: 700 (below floor_midweek 850 -> clamped to 850)
            {
                "check_in": "2027-03-21",
                "check_out": "2027-03-25",
                "segment_type": "midweek",
                "our_base_nightly": 1000.0,
                "recommended_base_nightly_adj": 700.0,
                "historical_benchmark": {"sample_count": 2, "median_rate": 700.0},
            },
            # Weekend segment consensus: 1800 (above floor_weekend 1650 -> stays 1800)
            {
                "check_in": "2027-03-25",
                "check_out": "2027-03-28",
                "segment_type": "weekend",
                "our_base_nightly": 1500.0,
                "recommended_base_nightly_adj": 1800.0,
                "historical_benchmark": {"sample_count": 2, "median_rate": 1800.0},
            },
        ]
        periods = generate_proposed_prices(
            seasonal_rates,
            evaluated_segments,
            reference_date=date(2026, 9, 6),
            holidays_registry=registry,
        )
        self.assertEqual(len(periods), 1)
        p = periods[0]
        # Midweek rate clamped to floor_midweek (850), NOT influenced by weekend floor (1650)
        self.assertEqual(p["midweek_avg"], 850)
        self.assertEqual(p["midweek_med"], 850)
        # Weekend rate exceeds floor_weekend (1650), evaluated at 1800
        self.assertEqual(p["weekend_avg"], 1800)
        self.assertEqual(p["weekend_med"], 1800)

    def test_holiday_split_pricing_weekend_floor_binding(self):
        """Verify that weekend floor binds independently when weekend consensus is below floor_weekend."""
        seasonal_rates = [
            {
                "period_name": "WM Phoenix Open 2028",
                "begin_dt": date(2028, 2, 7),
                "end_dt": date(2028, 2, 13),
                "first_price": 750.0,
                "second_price": 1100.0,
                "min_days": 3,
            }
        ]
        registry = [
            {
                "holiday_name": "WM Phoenix Open",
                "kivoya_period_name": "WM Phoenix Open 2028",
                "split_pricing": True,
                "floor_midweek": 650,
                "floor_weekend": 1250,
                "min_nights": 3,
            }
        ]
        evaluated_segments = [
            # Midweek consensus: 720 (above floor_midweek 650; change is < 5% from 750, churn keeps 750)
            {
                "check_in": "2028-02-07",
                "check_out": "2028-02-10",
                "segment_type": "midweek",
                "our_base_nightly": 750.0,
                "recommended_base_nightly_adj": 720.0,
                "historical_benchmark": {"sample_count": 2, "median_rate": 720.0},
            },
            # Weekend consensus: 1000 (below floor_weekend 1250 -> clamped to 1250)
            {
                "check_in": "2028-02-10",
                "check_out": "2028-02-13",
                "segment_type": "weekend",
                "our_base_nightly": 1100.0,
                "recommended_base_nightly_adj": 1000.0,
                "historical_benchmark": {"sample_count": 2, "median_rate": 1000.0},
            },
        ]
        periods = generate_proposed_prices(
            seasonal_rates,
            evaluated_segments,
            reference_date=date(2027, 3, 1),
            holidays_registry=registry,
        )
        self.assertEqual(len(periods), 1)
        p = periods[0]
        self.assertEqual(p["midweek_avg"], 750)  # churn held at 750
        self.assertEqual(p["weekend_avg"], 1250) # clamped to floor_weekend (1250)

    def test_custom_event_standalone_injection_2028(self):
        """Verify that a 2028 custom event outside existing Kivoya rate periods is appended as standalone period."""
        seasonal_rates = [
            {
                "period_name": "February 2027",
                "begin_dt": date(2027, 2, 1),
                "end_dt": date(2027, 2, 28),
                "first_price": 749.0,
                "second_price": 1099.0,
                "min_days": 2,
            }
        ]
        registry = [
            {
                "holiday_name": "WM Phoenix Open",
                "kivoya_period_name": None,
                "from_date": "02/07/2028",
                "to_date": "02/13/2028",
                "split_pricing": True,
                "premium_pct": 0,
                "floor_rate": 699,
                "base_midweek": 749,
                "base_weekend": 1099,
                "min_nights": 3,
                "custom_event": True,
            }
        ]
        periods = generate_proposed_prices(
            seasonal_rates,
            [],
            reference_date=date(2027, 3, 1),
            holidays_registry=registry,
            blocked_periods=[],
        )
        # 2028 WM Phoenix Open (02/07/2028) is within 12 months of 2027-03-01 and appended standalone
        self.assertEqual(len(periods), 2)
        feb27 = periods[0]
        self.assertEqual(feb27["period_name"], "February 2027")
        self.assertFalse(feb27["is_holiday"])
        self.assertFalse(feb27.get("is_custom_event", False))

        open28 = periods[1]
        self.assertEqual(open28["from_date"], "02/07/2028")
        self.assertEqual(open28["to_date"], "02/13/2028")
        self.assertTrue(open28["is_holiday"])
        self.assertTrue(open28["is_custom_event"])
        self.assertTrue(open28["split_pricing"])
        self.assertEqual(open28["holiday_name"], "WM Phoenix Open")
        self.assertEqual(open28["midweek_base"], 749)
        self.assertEqual(open28["weekend_base"], 1099)
        self.assertEqual(open28["min_nights"], 3)

    def test_orphan_slot_protection_2_night_gap(self):
        """Verify that a 2-night gap between reservations prevents recommending 3 nights minimum."""
        seasonal_rates = [
            {
                "period_name": "May 27",
                "begin_dt": date(2027, 5, 1),
                "end_dt": date(2027, 5, 27),
                "first_price": 659.0,
                "second_price": 749.0,
                "min_days": 2,
            }
        ]
        # May 2027 is >90 days out from 2026-09-06, so normal rule would propose min_nights = 3.
        # Add two reservations leaving an unbooked 2-night gap (May 10 & May 11).
        blocked_periods = [
            {"start_dt": date(2027, 5, 5), "end_dt": date(2027, 5, 9)},
            {"start_dt": date(2027, 5, 12), "end_dt": date(2027, 5, 16)},
        ]
        periods = generate_proposed_prices(
            seasonal_rates,
            [],
            reference_date=date(2026, 9, 6),
            holidays_registry=[],
            blocked_periods=blocked_periods,
        )
        self.assertEqual(len(periods), 1)
        p = periods[0]
        # Without orphan protection, min_nights would be 3. With orphan protection, it must be 2.
        self.assertEqual(p["min_nights_proposed"], 2)
        self.assertIsNotNone(p["orphan_slot_reason"])
        self.assertIn("2-night gap", p["orphan_slot_reason"])

    def test_orphan_slot_protection_weekend_two_days_left(self):
        """Verify that a weekend with only 2 nights left unbooked prevents recommending 3 nights minimum."""
        seasonal_rates = [
            {
                "period_name": "January 27",
                "begin_dt": date(2027, 1, 4),
                "end_dt": date(2027, 1, 31),
                "first_price": 649.0,
                "second_price": 899.0,
                "min_days": 3,
            }
        ]
        # Weekend of Thursday Jan 7, 2027:
        # Thu Jan 7 (open), Fri Jan 8 (open), Sat Jan 9 (booked by reservation)
        blocked_periods = [
            {"start_dt": date(2027, 1, 9), "end_dt": date(2027, 1, 12)},
        ]
        periods = generate_proposed_prices(
            seasonal_rates,
            [],
            reference_date=date(2026, 9, 6),
            holidays_registry=[],
            blocked_periods=blocked_periods,
        )
        self.assertEqual(len(periods), 1)
        p = periods[0]
        self.assertEqual(p["min_nights_proposed"], 2)
        self.assertIsNotNone(p["orphan_slot_reason"])
        self.assertIn("Weekend of 01/07 has only 2 nights left", p["orphan_slot_reason"])


    def test_12_month_synthetic_extension_and_is_calendar_open(self):
        """Verify that seasonal rates ending on 2027-05-31 synthesize 12-month periods capped at August 2027."""
        seasonal_rates = [
            {
                "period_name": "Memorial Day 27",
                "begin_dt": date(2027, 5, 28),
                "end_dt": date(2027, 5, 31),
                "first_price": 599.0,
                "second_price": None,
                "min_days": 3,
            }
        ]
        # Intervals in June 2027 (weekend)
        evaluated_segments = [
            {
                "check_in": "2027-06-10",
                "check_out": "2027-06-13",
                "segment_type": "weekend",
                "our_base_nightly": 599.0,
                "recommended_base_nightly_adj": 1298.0,
                "historical_benchmark": {"sample_count": 2, "median_rate": 870.0},
            }
        ]
        registry = [
            {
                "holiday_name": "WM Phoenix Open",
                "kivoya_period_name": None,
                "from_date": "02/07/2028",
                "to_date": "02/13/2028",
                "split_pricing": True,
                "floor_rate": 699,
                "base_midweek": 749,
                "base_weekend": 1099,
                "min_nights": 3,
                "custom_event": True,
            }
        ]
        open_end = date(2027, 5, 31)
        ref_date = date(2026, 9, 6)

        periods = generate_proposed_prices(
            seasonal_rates,
            evaluated_segments,
            reference_date=ref_date,
            holidays_registry=registry,
            open_end_date=open_end,
            blocked_periods=[],
            extend_to_horizon=True,
        )

        # Should contain Memorial Day 27, June 27, July 27, August 27.
        # Should NOT contain anything beyond August 2027 (no Sep. 27, no 2028 WM Phoenix Open).
        period_names = [p["period_name"] for p in periods]
        self.assertEqual(len(period_names), 4)
        self.assertIn("Memorial Day 27", period_names)
        self.assertIn("June 27", period_names)
        self.assertIn("July 27", period_names)
        self.assertIn("August 27", period_names)
        self.assertNotIn("Sep. 27", period_names)
        self.assertNotIn("WM Phoenix Open", period_names)

        # Memorial Day is on or before 2027-05-31 -> is_calendar_open: True
        mem = next(p for p in periods if p["period_name"] == "Memorial Day 27")
        self.assertTrue(mem["is_calendar_open"])

        # June 27, July 27, August 27 are after 2027-05-31 -> is_calendar_open: False
        for name in ["June 27", "July 27", "August 27"]:
            p = next(x for x in periods if x["period_name"] == name)
            self.assertFalse(p["is_calendar_open"], f"Period {name} should have is_calendar_open=False")

        # June 27 consensus weekend rate should reflect the June interval
        june = next(p for p in periods if p["period_name"] == "June 27")
        self.assertEqual(june["weekend_base"], 449)
        self.assertEqual(june["weekend_med"], 1157)
        self.assertEqual(june["midweek_base"], 389)
        self.assertEqual(june["midweek_med"], 389)
        self.assertEqual(june["min_nights"], 3)

        # July 27 and August 27 are summer months -> never 3 nights, always 2 nights minimum
        july = next(p for p in periods if p["period_name"] == "July 27")
        aug = next(p for p in periods if p["period_name"] == "August 27")
        self.assertEqual(july["min_nights"], 2)
        self.assertEqual(aug["min_nights"], 2)

    def test_summer_months_two_night_minimum(self):
        """Verify that July and August periods never recommend 3 nights, sticking strictly to 2 nights minimum."""
        seasonal_rates = [
            {
                "period_name": "July 27",
                "begin_dt": date(2027, 7, 1),
                "end_dt": date(2027, 7, 31),
                "first_price": 599.0,
                "second_price": 599.0,
                "min_days": 3,
            },
            {
                "period_name": "August 27",
                "begin_dt": date(2027, 8, 1),
                "end_dt": date(2027, 8, 31),
                "first_price": 599.0,
                "second_price": 599.0,
                "min_days": 3,
            },
        ]
        # Reference date in Sept 2026 -> July/August 2027 are 300+ days away (>90 days rule would normally propose 3)
        periods = generate_proposed_prices(
            seasonal_rates,
            [],
            reference_date=date(2026, 9, 6),
            holidays_registry=[],
            blocked_periods=[],
        )
        self.assertEqual(len(periods), 2)
        self.assertEqual(periods[0]["min_nights_proposed"], 2)
        self.assertEqual(periods[1]["min_nights_proposed"], 2)

    def test_find_parent_period_for_segment(self):
        """Verify 2-pass parent period resolution: holiday priority in pass 1, regular period in pass 2."""
        parsed_rates = [
            {
                "pname": "Oct 26",
                "b_dt": date(2026, 10, 1),
                "e_dt": date(2026, 10, 31),
                "is_holiday": False,
            },
            {
                "pname": "Columbus Day 24",
                "b_dt": date(2026, 10, 8),
                "e_dt": date(2026, 10, 12),
                "is_holiday": True,
            },
        ]
        # Check date during Columbus Day -> resolves holiday period (Pass 1)
        seg_hol = {"check_in": "2026-10-09", "check_out": "2026-10-12"}
        parent_hol = find_parent_period_for_segment(seg_hol, parsed_rates)
        self.assertIsNotNone(parent_hol)
        self.assertEqual(parent_hol["pname"], "Columbus Day 24")

        # Check date outside holiday -> resolves regular month (Pass 2)
        seg_reg = {"check_in": "2026-10-03", "check_out": "2026-10-06"}
        parent_reg = find_parent_period_for_segment(seg_reg, parsed_rates)
        self.assertIsNotNone(parent_reg)
        self.assertEqual(parent_reg["pname"], "Oct 26")

    def test_post_churn_operational_floors(self):
        """Verify proposed rates cannot be suppressed below $300 Midweek / $450 Weekend by 5% churn tolerance."""
        seasonal_rates = [
            {
                "period_name": "Oct 26",
                "begin_dt": date(2026, 10, 1),
                "end_dt": date(2026, 10, 31),
                "nightly_rate": 400.0,  # Below $450 weekend floor
                "first_interval": "Monday-Wednesday",
                "first_price": 250.0,   # Below $300 midweek floor
                "second_interval": "Thursday-Sunday",
                "second_price": 400.0,
                "min_days": 3,
            }
        ]
        # Segments producing consensus very close to base (within 5% churn tolerance)
        evaluated_segments = [
            {
                "check_in": "2026-10-05",
                "check_out": "2026-10-08",
                "segment_type": "midweek",
                "our_base_nightly": 250.0,
                "recommended_base_nightly_adj": 255.0,  # +2.0% (within 5% churn window)
                "historical_benchmark": None,
            },
            {
                "check_in": "2026-10-08",
                "check_out": "2026-10-11",
                "segment_type": "weekend",
                "our_base_nightly": 400.0,
                "recommended_base_nightly_adj": 410.0,  # +2.5% (within 5% churn window)
                "historical_benchmark": None,
            },
        ]
        proposed = generate_proposed_prices(
            seasonal_rates=seasonal_rates,
            evaluated_segments=evaluated_segments,
            reference_date=date(2026, 9, 1),
            holidays_registry=[],
        )
        self.assertEqual(len(proposed), 1)
        p = proposed[0]
        # Churn threshold would have kept 250 and 400, but operational floors enforce 300 and 450
        self.assertEqual(p["midweek_avg"], 300)
        self.assertEqual(p["midweek_med"], 300)
        self.assertEqual(p["weekend_avg"], 450)
        self.assertEqual(p["weekend_med"], 450)

        # Verify holiday period with low base rate is clamped to operational floor
        holiday_rates = [
            {
                "period_name": "Thanksgiving 26",
                "begin_dt": date(2026, 11, 26),
                "end_dt": date(2026, 11, 30),
                "nightly_rate": 350.0,  # Below $450 weekend operational floor
                "first_interval": "Nightly Rate",
                "first_price": 350.0,
                "second_interval": None,
                "second_price": None,
                "min_days": 4,
            }
        ]
        holiday_segments = [
            {
                "check_in": "2026-11-26",
                "check_out": "2026-11-30",
                "segment_type": "weekend",
                "our_base_nightly": 350.0,
                "recommended_base_nightly_adj": 355.0,  # within 5% churn
                "historical_benchmark": None,
            }
        ]
        h_proposed = generate_proposed_prices(
            seasonal_rates=holiday_rates,
            evaluated_segments=holiday_segments,
            reference_date=date(2026, 9, 1),
            holidays_registry=[],
            operational_floors={"midweek": 300.0, "weekend": 450.0},
        )
        self.assertEqual(len(h_proposed), 1)
        hp = h_proposed[0]
        self.assertEqual(hp["special_avg"], 450)
        self.assertEqual(hp["special_med"], 450)


if __name__ == "__main__":
    unittest.main()



