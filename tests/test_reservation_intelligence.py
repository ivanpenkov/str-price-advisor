"""
Unit tests for Reservation Intelligence & Booking Pace Analytics Engine.
"""

from datetime import date
from pathlib import Path
import tempfile
import unittest

from src.reservation_store import ReservationStore
from src.reservation_intelligence import ReservationIntelligence


class TestReservationIntelligence(unittest.TestCase):
    """Test suite for lead-time windows, annual weekend/midweek shifts, and historical rate benchmarks."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "reservations.db"
        self.store = ReservationStore(db_path=self.db_path)

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

        # In 2024: Res 101 (3 wkd), Res 102 (1 wkd, 2 mid) -> Tot=6, Wkd=4 (66.7%), Mid=2 (33.3%)
        y2024 = years[2024]
        self.assertEqual(y2024["total_nights"], 6)
        self.assertEqual(y2024["weekend_nights"], 4)
        self.assertEqual(y2024["midweek_nights"], 2)

        # In 2025: Res 201 (3 mid), Res 202 (3 wkd) -> Tot=6, Wkd=3 (50.0%), Mid=3 (50.0%)
        y2025 = years[2025]
        self.assertEqual(y2025["total_nights"], 6)
        self.assertEqual(y2025["weekend_nights"], 3)
        self.assertEqual(y2025["midweek_nights"], 3)
        self.assertEqual(y2025["weekend_pct"], 50.0)
        self.assertEqual(y2025["midweek_pct"], 50.0)

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


if __name__ == "__main__":
    unittest.main()

