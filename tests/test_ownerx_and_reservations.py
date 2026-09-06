"""
Unit tests for Streamline OwnerX API client, reservation storage,
accrual revenue calculations, and calendar/revenue dashboard views.
"""

from datetime import date, datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest

from src.ownerx_client import OwnerXClient
from src.reservation_store import ReservationStore
import src.calendar_revenue_views as crv


class TestOwnerXAndReservations(unittest.TestCase):
    """Test suite for reservation ingestion, storage, accrual curves, and views."""

    def test_normalize_reservation_past_vs_future(self):
        today = date(2026, 9, 6)

        raw_past = {
            "id": 101,
            "confirmation_id": 9901,
            "creation_date": "04/01/2026 12:00:00",
            "startdate": "05/01/2026",
            "enddate": "05/05/2026",
            "days_number": 4,
            "type_id": 236,
            "type_name": "STA",
            "type_description": "Standard",
            "status_name": "Booked",
            "occupants": 12,
            "commission_information": {
                "management_commission_amount": 600.0,
                "owner_commission_amount": 2400.0,
            },
        }

        norm_past = OwnerXClient.normalize_reservation(raw_past, today=today)
        self.assertEqual(norm_past["id"], 101)
        self.assertEqual(norm_past["start_date"], "2026-05-01")
        self.assertEqual(norm_past["end_date"], "2026-05-05")
        self.assertEqual(norm_past["days_number"], 4)
        self.assertEqual(norm_past["owner_payout"], 2400.0)
        self.assertEqual(norm_past["management_fee"], 600.0)
        self.assertEqual(norm_past["gross_rent"], 3000.0)
        self.assertEqual(norm_past["is_future"], 0)

        raw_future = {
            "id": 102,
            "confirmation_id": 9902,
            "creation_date": "08/15/2026 10:00:00",
            "startdate": "10/10/2026",
            "enddate": "10/15/2026",
            "days_number": 5,
            "type_id": 236,
            "type_name": "STA",
            "type_description": "Standard",
            "status_name": "Booked",
            "occupants": 14,
            "commission_information": {
                "management_commission_amount": 800.0,
                "owner_commission_amount": 3200.0,
            },
        }

        norm_future = OwnerXClient.normalize_reservation(raw_future, today=today)
        self.assertEqual(norm_future["is_future"], 1)

    def test_reservation_store_crud_and_json_export(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "test_reservations.db"
            json_path = Path(tmp_dir) / "test_reservations.json"

            store = ReservationStore(db_path=db_path, json_path=json_path)
            today = date(2026, 9, 6)

            records = [
                {
                    "id": 1,
                    "confirmation_id": 1001,
                    "creation_date": "01/01/2026",
                    "start_date": "2026-02-01",
                    "end_date": "2026-02-05",
                    "days_number": 4,
                    "type_id": 236,
                    "type_name": "STA",
                    "type_description": "Standard",
                    "status_name": "Booked",
                    "occupants": 10,
                    "owner_payout": 2000.0,
                    "management_fee": 500.0,
                    "gross_rent": 2500.0,
                },
                {
                    "id": 2,
                    "confirmation_id": 1002,
                    "creation_date": "08/01/2026",
                    "start_date": "2026-11-01",
                    "end_date": "2026-11-04",
                    "days_number": 3,
                    "type_id": 236,
                    "type_name": "STA",
                    "type_description": "Standard",
                    "status_name": "Booked",
                    "occupants": 8,
                    "owner_payout": 1500.0,
                    "management_fee": 350.0,
                    "gross_rent": 1850.0,
                },
                {
                    "id": 3,
                    "confirmation_id": 1003,
                    "creation_date": "05/01/2026",
                    "start_date": "2026-06-01",
                    "end_date": "2026-06-03",
                    "days_number": 2,
                    "type_id": 236,
                    "type_name": "STA",
                    "type_description": "Standard",
                    "status_name": "Cancelled",
                    "occupants": 6,
                    "owner_payout": 1000.0,
                    "management_fee": 200.0,
                    "gross_rent": 1200.0,
                },
            ]

            stats = store.upsert_reservations(records, today=today)
            self.assertEqual(stats["upserted"], 3)
            self.assertEqual(stats["past"], 2)
            self.assertEqual(stats["future"], 1)

            # JSON export check
            self.assertTrue(json_path.exists())
            with open(json_path) as f:
                data = json.load(f)
            self.assertEqual(data["metadata"]["total_reservations"], 3)
            self.assertEqual(data["metadata"]["future_reservations"], 1)
            self.assertEqual(data["metadata"]["past_reservations"], 2)

            # Filter cancelled
            active = store.get_all_reservations(include_cancelled=False)
            self.assertEqual(len(active), 2)
            all_res = store.get_all_reservations(include_cancelled=True)
            self.assertEqual(len(all_res), 3)

    def test_daily_accrual_revenue_and_curves(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "test_rev.db"
            json_path = Path(tmp_dir) / "test_rev.json"
            store = ReservationStore(db_path=db_path, json_path=json_path)

            today = date(2026, 9, 6)

            # Cross-year reservation: Dec 30, 2025 -> Jan 3, 2026 (4 nights, $4,000 = $1,000/night)
            # Dec 30 & Dec 31 belong to 2025 ($2,000)
            # Jan 1 & Jan 2 belong to 2026 ($2,000)
            records = [
                {
                    "id": 10,
                    "start_date": "2025-12-30",
                    "end_date": "2026-01-03",
                    "days_number": 4,
                    "status_name": "Booked",
                    "owner_payout": 4000.0,
                },
                {
                    "id": 20,
                    "start_date": "2026-04-10",
                    "end_date": "2026-04-14",
                    "days_number": 4,
                    "status_name": "Booked",
                    "owner_payout": 3000.0,
                },
                {
                    "id": 30,
                    "start_date": "2026-11-20",
                    "end_date": "2026-11-24",
                    "days_number": 4,
                    "status_name": "Booked",
                    "owner_payout": 5000.0,
                },
            ]

            store.upsert_reservations(records, today=today)
            rev = store.calculate_cumulative_annual_revenue(start_year=2025, end_year=2026, today=today)

            s_2025 = rev["yearly_series"][2025]
            s_2026 = rev["yearly_series"][2026]

            # 2025 should have 2 nights ($2,000)
            self.assertEqual(s_2025["total_nights"], 2)
            self.assertAlmostEqual(s_2025["total_revenue"], 2000.0)

            # 2026 should have 2 nights from cross-year ($2,000) + 4 nights ($3,000) + 4 nights ($5,000) = 10 nights, $10,000
            self.assertEqual(s_2026["total_nights"], 10)
            self.assertAlmostEqual(s_2026["total_revenue"], 10000.0)

            # 2026 YTD should be $2,000 (Jan) + $3,000 (Apr) = $5,000
            self.assertAlmostEqual(s_2026["ytd_revenue"], 5000.0)

            # Points should be monotonically non-decreasing
            cum_vals = [p["cumulative"] for p in s_2026["points"]]
            for i in range(len(cum_vals) - 1):
                self.assertGreaterEqual(cum_vals[i + 1], cum_vals[i])

    def test_calendar_and_revenue_view_rendering(self):
        reservations = [
            {
                "id": 501,
                "confirmation_id": 1234,
                "start_date": "2026-09-10",
                "end_date": "2026-09-14",
                "days_number": 4,
                "type_name": "STA",
                "type_description": "Standard",
                "status_name": "Booked",
                "owner_payout": 3500.0,
            }
        ]
        rev_data = {
            "yearly_series": {
                2026: {
                    "year": 2026,
                    "label": "2026",
                    "color": "#FB8C00",
                    "dash": [],
                    "total_revenue": 89000.0,
                    "total_nights": 180,
                    "adr": 494.44,
                    "ytd_revenue": 64000.0,
                    "points": [{"day": 1, "cumulative": 0.0, "daily": 0.0}],
                }
            },
            "kpis": {
                "current_year": 2026,
                "ytd_revenue": 64000.0,
                "prior_ytd_revenue": 60000.0,
                "ytd_growth_pct": 6.7,
                "total_booked_revenue": 89000.0,
                "total_nights_booked": 180,
                "adr": 494.44,
                "by_year": {
                    2026: {
                        "total_revenue": 89000.0,
                        "total_nights": 180,
                        "adr": 494.44,
                        "ytd_revenue": 64000.0,
                    }
                },
            },
        }

        cal_html = crv.render_calendar_tab(reservations, current_date=date(2026, 9, 6))
        self.assertIn("Availability Calendar", cal_html)
        self.assertIn("calMonthsGrid", cal_html)
        self.assertIn("calTooltip", cal_html)

        rev_html = crv.render_revenue_tab(rev_data)
        self.assertIn("Cumulative Annual Owner Net Revenue", rev_html)
        self.assertIn("revenueChart", rev_html)
        self.assertIn("$64,000.00", rev_html)

        js = crv.get_calendar_revenue_js(reservations, rev_data, today=date(2026, 9, 6))
        self.assertIn("CAL_RESERVATIONS", js)
        self.assertIn("REVENUE_DATA", js)
        self.assertIn("renderCalendar", js)
        self.assertIn("initRevenueChart", js)


if __name__ == "__main__":
    unittest.main()
