"""
Unit tests for Reservation Intelligence & Booking Pace Analytics Engine.
"""

from datetime import date
from pathlib import Path
import tempfile
import unittest

from src.reservation_store import ReservationStore
from src.reservation_intelligence import (
    ReservationIntelligence,
    compute_easter_date,
    resolve_holiday_dates,
    clean_holiday_name,
)


class TestReservationIntelligence(unittest.TestCase):
    """Test suite for lead-time windows, annual weekend/midweek shifts, and historical rate benchmarks."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "reservations.db"
        self.json_path = Path(self.temp_dir.name) / "reservations.json"
        self.store = ReservationStore(db_path=self.db_path, json_path=self.json_path)

        # Populate sample historical reservations
        sample_reservations = [
            # 2024 Stays (Mostly weekend)
            {
                "id": 101,
                "confirmation_id": 1001,
                "creation_date": "01/10/2024 10:00:00",
                "start_date": "2024-02-15",  # Thu check-in
                "end_date": "2024-02-18",    # Sun check-out (3n: Thu, Fri, Sat -> all weekend)
                "days_number": 3,
                "gross_rent": 3000.0,        # $1000/nt
                "owner_payout": 2400.0,
                "status_name": "Booked",
                "is_future": 0,
            },
            {
                "id": 102,
                "confirmation_id": 1002,
                "creation_date": "06/01/2024 12:00:00",
                "start_date": "2024-06-15",  # Sat check-in
                "end_date": "2024-06-18",    # Tue check-out (3n: Sat wkd, Sun mid, Mon mid)
                "days_number": 3,
                "gross_rent": 1500.0,        # $500/nt
                "owner_payout": 1200.0,
                "status_name": "Booked",
                "is_future": 0,
            },
            # 2025 Stays (More midweek)
            {
                "id": 201,
                "confirmation_id": 2001,
                "creation_date": "11/01/2024 09:00:00",
                "start_date": "2025-02-10",  # Mon check-in
                "end_date": "2025-02-13",    # Thu check-out (3n: Mon, Tue, Wed -> all midweek)
                "days_number": 3,
                "gross_rent": 2100.0,        # $700/nt
                "owner_payout": 1680.0,
                "status_name": "Booked",
                "is_future": 0,
            },
            {
                "id": 202,
                "confirmation_id": 2002,
                "creation_date": "01/15/2025 15:00:00",
                "start_date": "2025-02-20",  # Thu check-in
                "end_date": "2025-02-23",    # Sun check-out (3n: Thu, Fri, Sat -> all weekend)
                "days_number": 3,
                "gross_rent": 3300.0,        # $1100/nt
                "owner_payout": 2640.0,
                "status_name": "Booked",
                "is_future": 0,
            },
        ]

        self.store.upsert_reservations(sample_reservations, sync_mode="full")
        self.intel = ReservationIntelligence(db_path=self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_compute_lead_time_windows(self):
        """Verify lead time calculations and seasonal partitioning."""
        lead_data = self.intel.compute_lead_time_windows()
        self.assertEqual(lead_data["total_analyzed"], 4)

        overall = lead_data["overall"]
        self.assertGreaterEqual(overall["count"], 4)
        self.assertGreaterEqual(overall["max"], overall["min"])

        # Check season names exist
        seasons = lead_data["seasons"]
        self.assertIn("Peak Winter / Spring (Feb–Apr)", seasons)
        self.assertIn("Summer Value Season (Jun–Aug)", seasons)

        # Res 101: 2024-01-10 to 2024-02-15 = 36 days
        # Res 201: 2024-11-01 to 2025-02-10 = 101 days
        # Res 202: 2025-01-15 to 2025-02-20 = 36 days
        peak_stats = seasons["Peak Winter / Spring (Feb–Apr)"]
        self.assertEqual(peak_stats["count"], 3)
        self.assertEqual(peak_stats["min"], 36)
        self.assertEqual(peak_stats["max"], 101)

        # Check months breakdown
        self.assertIn("months", lead_data)
        self.assertEqual(len(lead_data["months"]), 12)
        feb_stats = lead_data["months"][2]
        self.assertEqual(feb_stats["month_name"], "February")
        self.assertEqual(feb_stats["count"], 3)

    def test_get_lead_time_status(self):
        """Verify lead time status classification for upcoming dates."""
        today = date(2026, 1, 1)

        # Far out check-in (150 days out)
        far_out = self.intel.get_lead_time_status("2026-06-01", today=today)
        self.assertEqual(far_out["status"], "PRE_WINDOW")

        # Last minute check-in (5 days out)
        last_min = self.intel.get_lead_time_status("2026-01-06", today=today)
        self.assertEqual(last_min["status"], "LAST_MINUTE")

    def test_compute_weekend_midweek_annual_shift(self):
        """Verify year-over-year night counts, percentages, and ADRs."""
        shift_data = self.intel.compute_weekend_midweek_annual_shift()
        years = {y["year"]: y for y in shift_data["years"]}

        self.assertIn(2024, years)
        self.assertIn(2025, years)

        # In 2024: Res 101 (3 wkd), Res 102 (1 wkd, 2 mid @ 50% wkd weighting)
        # Res 102: 1500 / (1.5*1 + 2) = $428.57/mid, $642.86/wkd
        y2024 = years[2024]
        self.assertEqual(y2024["total_nights"], 6)
        self.assertEqual(y2024["weekend_nights"], 4)
        self.assertEqual(y2024["midweek_nights"], 2)
        self.assertAlmostEqual(y2024["weekend_adr"], 910.71, places=2)
        self.assertAlmostEqual(y2024["midweek_adr"], 428.57, places=2)
        self.assertEqual(y2024["adr_premium_pct"], 112.5)

        # In 2025: Res 201 (3 mid), Res 202 (3 wkd) -> Tot=6, Wkd=3 (50.0%), Mid=3 (50.0%)
        y2025 = years[2025]
        self.assertEqual(y2025["total_nights"], 6)
        self.assertEqual(y2025["weekend_nights"], 3)
        self.assertEqual(y2025["midweek_nights"], 3)
        self.assertEqual(y2025["weekend_pct"], 50.0)
        self.assertEqual(y2025["midweek_pct"], 50.0)
        self.assertEqual(y2025["weekend_adr"], 1100.0)
        self.assertEqual(y2025["midweek_adr"], 700.0)
        self.assertEqual(y2025["adr_premium_pct"], 57.1)

    def test_get_historical_benchmarks_for_interval(self):
        """Verify rolling seasonal window rate benchmarks and variance flags."""
        # Benchmark mid-February weekend stay (matches Res 101 and Res 202)
        bm_feb_wkd = self.intel.get_historical_benchmarks_for_interval(
            "2026-02-18", segment_type="weekend", proposed_rate=1050.0
        )
        self.assertEqual(bm_feb_wkd["sample_count"], 2)
        # Rates were 1000.0 and 1100.0 -> min=1000.0, max=1100.0, median=1050.0
        self.assertEqual(bm_feb_wkd["min_rate"], 1000.0)
        self.assertEqual(bm_feb_wkd["max_rate"], 1100.0)
        self.assertEqual(bm_feb_wkd["median_rate"], 1050.0)
        self.assertEqual(bm_feb_wkd["flag"], "ON_TRACK")

        # Test aggressive premium flag (>25% above median)
        bm_high = self.intel.get_historical_benchmarks_for_interval(
            "2026-02-18", segment_type="weekend", proposed_rate=1500.0
        )
        self.assertEqual(bm_high["flag"], "AGGRESSIVE_PREMIUM")

        # Test deep discount flag (<-25% below median)
        bm_low = self.intel.get_historical_benchmarks_for_interval(
            "2026-02-18", segment_type="weekend", proposed_rate=700.0
        )
        self.assertEqual(bm_low["flag"], "DEEP_DISCOUNT")

    def test_compute_easter_date_known_years(self):
        """Verify Gregorian computus Easter Sunday derivation for 2023-2027."""
        self.assertEqual(compute_easter_date(2023), date(2023, 4, 9))
        self.assertEqual(compute_easter_date(2024), date(2024, 3, 31))
        self.assertEqual(compute_easter_date(2025), date(2025, 4, 20))
        self.assertEqual(compute_easter_date(2026), date(2026, 4, 5))
        self.assertEqual(compute_easter_date(2027), date(2027, 3, 28))

    def test_resolve_holiday_dates_floating(self):
        """Verify floating US holiday date ranges."""
        # Thanksgiving 2026: 4th Thu Nov 26 -> Sun Nov 29
        th_s, th_e = resolve_holiday_dates("Thanksgiving", 2026)
        self.assertEqual(th_s, date(2026, 11, 26))
        self.assertEqual(th_e, date(2026, 11, 29))

        # Memorial Day 2027: Last Mon May 31 -> Fri May 28 to Mon May 31
        mem_s, mem_e = resolve_holiday_dates("Memorial Day", 2027)
        self.assertEqual(mem_s, date(2027, 5, 28))
        self.assertEqual(mem_e, date(2027, 5, 31))

        # Labor Day 2026: 1st Mon Sep 7 -> Fri Sep 4 to Mon Sep 7
        lab_s, lab_e = resolve_holiday_dates("Labor Day", 2026)
        self.assertEqual(lab_s, date(2026, 9, 4))
        self.assertEqual(lab_e, date(2026, 9, 7))

        # Columbus Day 2026: 2nd Mon Oct 12 -> Fri Oct 9 to Mon Oct 12
        col_s, col_e = resolve_holiday_dates("Columbus Day", 2026)
        self.assertEqual(col_s, date(2026, 10, 9))
        self.assertEqual(col_e, date(2026, 10, 12))

        # Holy Week 2027: Easter March 28 -> Fri Mar 19 to Sun Mar 28
        hw_s, hw_e = resolve_holiday_dates("Holy Week", 2027)
        self.assertEqual(hw_s, date(2027, 3, 19))
        self.assertEqual(hw_e, date(2027, 3, 28))

    def test_compute_interval_historical_benchmarks_midweek_weekend(self):
        """Verify Sun-Wed classified as midweek and Thu-Sat as weekend."""
        # Clean setup with specific stays
        temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(temp_dir.name) / "test_int.db"
        json_path = Path(temp_dir.name) / "test_int.json"
        store = ReservationStore(db_path=db_path, json_path=json_path)

        stays = [
            # Sunday to Thursday (4 midweek nights: Sun, Mon, Tue, Wed)
            {
                "id": 301,
                "confirmation_id": 3001,
                "creation_date": "01/01/2024 10:00:00",
                "start_date": "2024-04-07",  # Sun
                "end_date": "2024-04-11",    # Thu
                "days_number": 4,
                "gross_rent": 2000.0,        # $500/nt
                "status_name": "Booked",
                "is_future": 0,
            },
            # Thursday to Sunday (3 weekend nights: Thu, Fri, Sat)
            {
                "id": 302,
                "confirmation_id": 3002,
                "creation_date": "01/01/2024 10:00:00",
                "start_date": "2024-04-11",  # Thu
                "end_date": "2024-04-14",    # Sun
                "days_number": 3,
                "gross_rent": 2400.0,        # $800/nt
                "status_name": "Booked",
                "is_future": 0,
            },
        ]
        store.upsert_reservations(stays, sync_mode="full")
        intel = ReservationIntelligence(db_path=db_path)

        seasonal_rates = [
            {"period_name": "April 26", "begin_dt": date(2026, 4, 1), "end_dt": date(2026, 4, 30), "first_price": 500.0, "second_price": 800.0}
        ]
        benchmarks = intel.compute_interval_historical_benchmarks(seasonal_rates)

        mid_bm = benchmarks[("April 26", "midweek")]
        wkd_bm = benchmarks[("April 26", "weekend")]

        self.assertEqual(mid_bm["total_nights"], 4)
        self.assertEqual(mid_bm["avg_rate"], 500.0)
        self.assertEqual(wkd_bm["total_nights"], 3)
        self.assertEqual(wkd_bm["avg_rate"], 800.0)
        temp_dir.cleanup()

    def test_compute_interval_historical_benchmarks_night_attribution(self):
        """Verify reservation crossing month boundary attributes nights and revenue strictly to each month."""
        temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(temp_dir.name) / "test_boundary.db"
        json_path = Path(temp_dir.name) / "test_boundary.json"
        store = ReservationStore(db_path=db_path, json_path=json_path)

        # Stay: Oct 31, 2024 (Thu) to Nov 3, 2024 (Sun)
        # Thu Oct 31 -> Oct Weekend (1 night)
        # Fri Nov 1, Sat Nov 2 -> Nov Weekend (2 nights)
        # Checkout Sun Nov 3
        # Gross: $3000 -> 3 weekend nights @ $1000/nt
        stays = [
            {
                "id": 401,
                "confirmation_id": 4001,
                "creation_date": "09/01/2024 10:00:00",
                "start_date": "2024-10-31",
                "end_date": "2024-11-03",
                "days_number": 3,
                "gross_rent": 3000.0,
                "status_name": "Booked",
                "is_future": 0,
            }
        ]
        store.upsert_reservations(stays, sync_mode="full")
        intel = ReservationIntelligence(db_path=db_path)

        seasonal_rates = [
            {"period_name": "Oct 26", "begin_dt": date(2026, 10, 1), "end_dt": date(2026, 10, 31), "first_price": 400.0, "second_price": 600.0},
            {"period_name": "Nov 26", "begin_dt": date(2026, 11, 1), "end_dt": date(2026, 11, 25), "first_price": 450.0, "second_price": 650.0},
        ]
        benchmarks = intel.compute_interval_historical_benchmarks(seasonal_rates)

        oct_wkd = benchmarks[("Oct 26", "weekend")]
        nov_wkd = benchmarks[("Nov 26", "weekend")]

        self.assertEqual(oct_wkd["total_nights"], 1)
        self.assertEqual(oct_wkd["avg_rate"], 1000.0)
        self.assertEqual(nov_wkd["total_nights"], 2)
        self.assertEqual(nov_wkd["avg_rate"], 1000.0)
        temp_dir.cleanup()

    def test_compute_interval_historical_benchmarks_holiday_exclusion(self):
        """Verify holiday stay nights are excluded from regular monthly periods."""
        temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(temp_dir.name) / "test_hol_excl.db"
        json_path = Path(temp_dir.name) / "test_hol_excl.json"
        store = ReservationStore(db_path=db_path, json_path=json_path)

        # In 2024, Thanksgiving was Nov 28. Stay Nov 28 to Dec 1 (3 weekend nights Thu, Fri, Sat)
        # And another stay Nov 10 to Nov 13 (3 midweek nights Sun, Mon, Tue)
        stays = [
            {
                "id": 501,
                "confirmation_id": 5001,
                "creation_date": "08/01/2024 10:00:00",
                "start_date": "2024-11-28",
                "end_date": "2024-12-01",
                "days_number": 3,
                "gross_rent": 4500.0,  # $1500/nt Thanksgiving surge
                "status_name": "Booked",
                "is_future": 0,
            },
            {
                "id": 502,
                "confirmation_id": 5002,
                "creation_date": "08/01/2024 10:00:00",
                "start_date": "2024-11-10",
                "end_date": "2024-11-13",
                "days_number": 3,
                "gross_rent": 1500.0,  # $500/nt regular midweek
                "status_name": "Booked",
                "is_future": 0,
            },
        ]
        store.upsert_reservations(stays, sync_mode="full")
        intel = ReservationIntelligence(db_path=db_path)

        seasonal_rates = [
            {"period_name": "Nov 26", "begin_dt": date(2026, 11, 1), "end_dt": date(2026, 11, 25), "first_price": 450.0, "second_price": 650.0},
            {"period_name": "Thanksgiving 26", "begin_dt": date(2026, 11, 26), "end_dt": date(2026, 11, 30), "first_price": 1200.0, "second_price": None},
        ]
        benchmarks = intel.compute_interval_historical_benchmarks(seasonal_rates)

        nov_mid = benchmarks[("Nov 26", "midweek")]
        nov_wkd = benchmarks[("Nov 26", "weekend")]
        th_wkd = benchmarks[("Thanksgiving 26", "weekend")]

        # Nov midweek should have the 3 nights from Res 502
        self.assertEqual(nov_mid["total_nights"], 3)
        self.assertEqual(nov_mid["avg_rate"], 500.0)

        # Nov regular weekend should be 0 because the Thanksgiving stay was excluded from regular Nov
        self.assertEqual(nov_wkd["total_nights"], 0)

        # Thanksgiving weekend benchmark should capture the 3 nights @ $1500
        self.assertEqual(th_wkd["total_nights"], 3)
        self.assertEqual(th_wkd["avg_rate"], 1500.0)

        temp_dir.cleanup()

    def test_compute_interval_historical_benchmarks_mixed_stay(self):
        """Verify 1.50 weekend premium allocation formula for mixed midweek/weekend stays."""
        temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(temp_dir.name) / "test_mixed.db"
        json_path = Path(temp_dir.name) / "test_mixed.json"
        store = ReservationStore(db_path=db_path, json_path=json_path)

        # Single stay: Wed Apr 10, 2024 to Mon Apr 15, 2024 (5 nights total)
        # Nights:
        #   Wed Apr 10 (Midweek) -> 1 nt
        #   Thu Apr 11 (Weekend) -> 1 nt
        #   Fri Apr 12 (Weekend) -> 1 nt
        #   Sat Apr 13 (Weekend) -> 1 nt
        #   Sun Apr 14 (Midweek) -> 1 nt
        # Total: 2 midweek nights, 3 weekend nights
        # Denominator = 1.50 * 3 + 2 = 6.5
        # Gross rent = $6500 -> rate_mid = $1000, rate_wkd = $1500
        stays = [
            {
                "id": 601,
                "confirmation_id": 6001,
                "creation_date": "02/01/2024 10:00:00",
                "start_date": "2024-04-10",
                "end_date": "2024-04-15",
                "days_number": 5,
                "gross_rent": 6500.0,
                "status_name": "Booked",
                "is_future": 0,
            }
        ]
        store.upsert_reservations(stays, sync_mode="full")
        intel = ReservationIntelligence(db_path=db_path)

        seasonal_rates = [
            {"period_name": "April 26", "begin_dt": date(2026, 4, 1), "end_dt": date(2026, 4, 30), "first_price": 600.0, "second_price": 900.0}
        ]
        benchmarks = intel.compute_interval_historical_benchmarks(seasonal_rates, weekend_premium_factor=1.50)

        mid_bm = benchmarks[("April 26", "midweek")]
        wkd_bm = benchmarks[("April 26", "weekend")]

        self.assertEqual(mid_bm["total_nights"], 2)
        self.assertAlmostEqual(mid_bm["avg_rate"], 1000.0, places=2)
        self.assertEqual(wkd_bm["total_nights"], 3)
        self.assertAlmostEqual(wkd_bm["avg_rate"], 1500.0, places=2)

        temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()

