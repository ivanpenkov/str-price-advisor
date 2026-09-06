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
        self.assertIn("Calendar", cal_html)
        self.assertIn("calMonthsGrid", cal_html)
        self.assertIn("calTooltip", cal_html)
        self.assertIn("calColorStatusBtn", cal_html)
        self.assertIn("calColorChannelBtn", cal_html)
        self.assertIn("calLegendStatus", cal_html)
        self.assertIn("calLegendChannel", cal_html)

        modal_html = crv.render_reservation_modal()
        self.assertIn("resModalOverlay", modal_html)

        res_tab_html = crv.render_reservations_tab(reservations, today=date(2026, 9, 6))
        self.assertIn("resTable", res_tab_html)
        self.assertIn("res-filter-toolbar", res_tab_html)
        self.assertIn("Total on Channel (Est.)", res_tab_html)
        self.assertIn("Guest Checkout Price (Est.)", res_tab_html)
        self.assertIn("Weekend", res_tab_html)

        rev_html = crv.render_revenue_tab(rev_data)
        self.assertIn("Cumulative Annual Owner Net Revenue", rev_html)
        self.assertIn("revenueChart", rev_html)
        self.assertIn("$64,000.00", rev_html)

        js = crv.get_calendar_revenue_js(reservations, rev_data, today=date(2026, 9, 6))
        self.assertIn("CAL_RESERVATIONS", js)
        self.assertIn("REVENUE_DATA", js)
        self.assertIn("renderCalendar", js)
        self.assertIn("setCalColorMode", js)
        self.assertIn("getEventColor", js)
        self.assertIn("initRevenueChart", js)
        self.assertIn("openResModalFromDate", js)
        self.assertIn("openResModalById", js)
        self.assertIn("applyReservationFilters", js)
        self.assertIn("sortResTable", js)
        self.assertIn("buildReservationHtmlSnippet", js)

    def test_classify_stay_type(self):
        """Verify Thu-Sat nights = Weekend, Sun-Wed nights = Midweek, Mix = both."""
        # Weekend stays (Thu night, Fri night, Sat night)
        self.assertEqual(crv.classify_stay_type("2026-09-03", "2026-09-06"), "Weekend")  # Thu-Sun
        self.assertEqual(crv.classify_stay_type("2026-09-04", "2026-09-06"), "Weekend")  # Fri-Sun
        self.assertEqual(crv.classify_stay_type("2026-09-03", "2026-09-05"), "Weekend")  # Thu-Sat

        # Midweek stays (Sun night, Mon night, Tue night, Wed night)
        self.assertEqual(crv.classify_stay_type("2026-09-06", "2026-09-10"), "Midweek")  # Sun-Thu
        self.assertEqual(crv.classify_stay_type("2026-09-07", "2026-09-09"), "Midweek")  # Mon-Wed

        # Mix stays (crosses both weekend and midweek nights)
        self.assertEqual(crv.classify_stay_type("2026-09-02", "2026-09-06"), "Mix")  # Wed-Sun
        self.assertEqual(crv.classify_stay_type("2026-09-04", "2026-09-07"), "Mix")  # Fri-Mon
        self.assertEqual(crv.classify_stay_type("2026-09-06", "2026-09-12"), "Mix")  # Sun-Sat

    def test_estimate_reservation_channel_pricing(self):
        """Verify formula estimates for Airbnb, VRBO, Direct, Admin, and Owner."""
        # Airbnb booking ($2000 gross rent)
        abnb_res = {
            "gross_rent": 2000.0,
            "madetype_name": "WSR",
            "type_description": "Standard",
            "raw_json": json.dumps({"hear_about_name": "Airbnb"}),
        }
        abnb_est = crv.estimate_reservation_channel_pricing(abnb_res)
        self.assertEqual(abnb_est["channel_name"], "Airbnb")
        # 2000 + 550 = 2550 lodging; + 14.15% service fee ($360.82) = 2910.82 total on channel
        self.assertEqual(abnb_est["total_on_channel"], 2910.82)
        # 2910.82 + 12.52% taxes ($364.43) = 3275.25
        self.assertEqual(abnb_est["guest_checkout_price"], 3275.25)
        self.assertIn("14.15%", abnb_est["tot_channel_formula"])
        self.assertIn("2,000.00", abnb_est["tot_channel_calc"])
        self.assertIn("2,910.82", abnb_est["tot_channel_calc"])
        self.assertIn("3,275.25", abnb_est["guest_price_calc"])

        # User screenshot example: $1,165.30 gross rent -> $1,958.01 search total -> $2,203.15 checkout
        shot_res = {
            "gross_rent": 1165.30,
            "madetype_name": "WSR",
            "type_description": "Standard",
            "raw_json": json.dumps({"hear_about_name": "Airbnb"}),
        }
        shot_est = crv.estimate_reservation_channel_pricing(shot_res)
        self.assertEqual(shot_est["total_on_channel"], 1958.01)  # $1,958 on Airbnb search
        self.assertEqual(shot_est["guest_checkout_price"], 2203.15)  # $2,203.15 at Reserve checkout

        # VRBO booking ($2000 gross rent)
        vrbo_res = {
            "gross_rent": 2000.0,
            "madetype_name": "PDWTA",
            "type_description": "Standard",
            "raw_json": json.dumps({"hear_about_name": "HA-OLB", "travelagent_name": "Vrbo"}),
        }
        vrbo_est = crv.estimate_reservation_channel_pricing(vrbo_res)
        self.assertEqual(vrbo_est["channel_name"], "Vrbo")
        expected_vrbo_base = round(2000.0 * 1.1448, 2)
        expected_vrbo_tot = expected_vrbo_base + 550.0
        self.assertEqual(vrbo_est["total_on_channel"], expected_vrbo_tot)
        self.assertEqual(vrbo_est["guest_checkout_price"], round(expected_vrbo_tot * 1.2557, 2))
        self.assertIn("1.1448", vrbo_est["tot_channel_formula"])
        self.assertIn("2,000.00", vrbo_est["tot_channel_calc"])
        self.assertIn("1.2557", vrbo_est["guest_price_formula"])

        # Booking.com booking ($1,746.00 gross rent)
        booking_res = {
            "gross_rent": 1746.00,
            "madetype_name": "PDWTA",
            "type_description": "Standard",
            "raw_json": json.dumps({"hear_about_name": "Booking.com"}),
        }
        booking_est = crv.estimate_reservation_channel_pricing(booking_res)
        self.assertEqual(booking_est["channel_name"], "Booking.com")
        # 1746 + 550 = 2296; + 6% service charge ($137.76) = 2433.76 total on channel
        self.assertEqual(booking_est["total_on_channel"], 2433.76)
        # 2433.76 + 16% VAT ($389.40) + 14.5% Tax ($352.90) = 3176.06
        self.assertEqual(booking_est["guest_checkout_price"], 3176.06)
        self.assertIn("6%", booking_est["tot_channel_formula"])
        self.assertIn("16% VAT", booking_est["guest_price_formula"])
        self.assertIn("2,433.76", booking_est["tot_channel_calc"])
        self.assertIn("3,176.06", booking_est["guest_price_calc"])

        # Direct Website booking ($2000 gross rent)
        direct_res = {
            "gross_rent": 2000.0,
            "madetype_name": "NET",
            "type_description": "Standard",
            "raw_json": json.dumps({"hear_about_name": "Website - kivoya.com"}),
        }
        direct_est = crv.estimate_reservation_channel_pricing(direct_res)
        self.assertEqual(direct_est["channel_name"], "Direct Website")
        self.assertEqual(direct_est["total_on_channel"], 2750.10)
        self.assertEqual(direct_est["guest_checkout_price"], 3125.77)
        self.assertIn("550.00", direct_est["tot_channel_formula"])
        self.assertIn("6%", direct_est["tot_channel_formula"])
        self.assertIn("14.07%", direct_est["guest_price_formula"])
        self.assertIn("2,000.00", direct_est["tot_channel_calc"])
        self.assertIn("3,125.77", direct_est["guest_price_calc"])

        # Owner block
        owner_res = {
            "gross_rent": 0.0,
            "madetype_name": "OWN",
            "type_description": "Owner Block",
            "raw_json": json.dumps({}),
        }
        owner_est = crv.estimate_reservation_channel_pricing(owner_res)
        self.assertEqual(owner_est["channel_name"], "Owner Block")
        self.assertEqual(owner_est["total_on_channel"], 0.0)
        self.assertEqual(owner_est["guest_checkout_price"], 0.0)
        self.assertIn("0.00", owner_est["tot_channel_calc"])
        self.assertIn("0.00", owner_est["guest_price_calc"])


if __name__ == "__main__":
    unittest.main()
