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
        self.assertIn("Actual Gross Rent", res_tab_html)
        self.assertIn("Expected Gross Rent", res_tab_html)
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

        # Future SQLite reservation where only raw_json has madetype_name (no top-level field, no hear_about)
        future_abnb_sqlite = {
            "id": 901,
            "gross_rent": 1500.0,
            "type_description": "Standard",
            "raw_json": json.dumps({"madetype_name": "WSR"}),
        }
        future_abnb_est = crv.estimate_reservation_channel_pricing(future_abnb_sqlite)
        self.assertEqual(future_abnb_est["channel_name"], "Airbnb")
        self.assertEqual(future_abnb_est["channel_color"], "#FF5A5F")

        future_vrbo_sqlite = {
            "id": 902,
            "gross_rent": 1500.0,
            "type_description": "Standard",
            "raw_json": json.dumps({"madetype_name": "PDWTA"}),
        }
        future_vrbo_est = crv.estimate_reservation_channel_pricing(future_vrbo_sqlite)
        self.assertEqual(future_vrbo_est["channel_name"], "Vrbo")
        self.assertEqual(future_vrbo_est["channel_color"], "#2563EB")

    def test_reservation_store_unpacks_raw_json_channel_fields(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "test_reservations.db"
            json_path = Path(tmp_dir) / "test_reservations.json"
            store = ReservationStore(db_path=db_path, json_path=json_path)

            records = [
                {
                    "id": 501,
                    "confirmation_id": 5001,
                    "start_date": "2026-10-01",
                    "end_date": "2026-10-05",
                    "gross_rent": 2000.0,
                    "raw_json": json.dumps({
                        "madetype_name": "WSR",
                        "hear_about_name": "Airbnb",
                        "travelagent_name": "",
                    }),
                },
                {
                    "id": 502,
                    "confirmation_id": 5002,
                    "start_date": "2026-11-01",
                    "end_date": "2026-11-05",
                    "gross_rent": 2200.0,
                    "raw_json": json.dumps({
                        "madetype_name": "PDWTA",
                        "hear_about_name": "",
                        "travelagent_name": "Vrbo",
                    }),
                },
            ]
            store.upsert_reservations(records)

            # Check get_all_reservations unpacks
            all_res = store.get_all_reservations()
            self.assertEqual(len(all_res), 2)
            self.assertEqual(all_res[0]["madetype_name"], "WSR")
            self.assertEqual(all_res[0]["hear_about_name"], "Airbnb")
            self.assertEqual(all_res[1]["madetype_name"], "PDWTA")
            self.assertEqual(all_res[1]["travelagent_name"], "Vrbo")

            # Check export_to_json unpacks
            store.export_to_json()
            with open(json_path) as f:
                data = json.load(f)
            self.assertEqual(len(data["reservations"]), 2)
            res_by_id = {r["id"]: r for r in data["reservations"]}
            self.assertEqual(res_by_id[501]["madetype_name"], "WSR")
            self.assertEqual(res_by_id[502]["madetype_name"], "PDWTA")

    def test_property_rate_snapshots_and_calendar_rates(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "test_reservations.db"
            json_path = Path(tmp_dir) / "test_reservations.json"
            store = ReservationStore(db_path=db_path, json_path=json_path)

            # Record early snapshot
            early_rates = {
                "2026-10-15": {"nightly_rate": 450.0, "interval_type": "weekend", "season_name": "Fall", "period_name": "Oct"},
                "2026-10-16": {"nightly_rate": 450.0, "interval_type": "weekend", "season_name": "Fall", "period_name": "Oct"},
            }
            store.record_rate_snapshots("2026-08-01", early_rates)

            # Record revised snapshot later
            revised_rates = {
                "2026-10-15": {"nightly_rate": 550.0, "interval_type": "weekend", "season_name": "Fall", "period_name": "Oct"},
                "2026-10-16": {"nightly_rate": 550.0, "interval_type": "weekend", "season_name": "Fall", "period_name": "Oct"},
            }
            store.record_rate_snapshots("2026-09-01", revised_rates)

            # Rate lookup as of 2026-08-15 should return 450.0
            snap_aug = store.get_rate_at_time("2026-10-15", "2026-08-15")
            self.assertIsNotNone(snap_aug)
            self.assertEqual(snap_aug["nightly_rate"], 450.0)

            # Rate lookup as of 2026-09-05 should return 550.0
            snap_sep = store.get_rate_at_time("2026-10-15", "2026-09-05")
            self.assertIsNotNone(snap_sep)
            self.assertEqual(snap_sep["nightly_rate"], 550.0)

            # Test daily calendar rate mapping
            mock_res = [
                {
                    "id": 1,
                    "creation_date": "08/10/2026 14:00:00",
                    "start_date": "2026-10-15",
                    "end_date": "2026-10-17",
                    "status_name": "Booked",
                    "is_future": 1,
                }
            ]
            rates_map = store.get_daily_calendar_rates(mock_res, current_kivoya_rates=[])
            # For 2026-10-15, booked on 08/10 -> should have rate 450
            self.assertEqual(rates_map["2026-10-15"]["rate"], 450)
            self.assertEqual(rates_map["2026-10-15"]["type"], "booked_snapshot")

            # Check JS generation contains CAL_DAILY_RATES and toggle button
            js = crv.get_calendar_revenue_js(mock_res, {"by_year": {}}, rates_map=rates_map)
            self.assertIn("CAL_DAILY_RATES", js)
            self.assertIn("setCalRatesVisible", js)

            # Check calendar tab HTML includes Rates toggle
            tab_html = crv.render_calendar_tab(mock_res)
            self.assertIn("calRatesOnBtn", tab_html)
            self.assertIn("calRatesOffBtn", tab_html)

    def test_parse_reservation_creation_date(self):
        from src.reservation_store import parse_reservation_creation_date
        self.assertEqual(parse_reservation_creation_date("03/01/2026 13:02:45"), "2026-03-01")
        self.assertEqual(parse_reservation_creation_date("2026-05-15T14:04:10"), "2026-05-15")
        self.assertEqual(parse_reservation_creation_date("2026-07-20 09:30:00"), "2026-07-20")
        self.assertEqual(parse_reservation_creation_date("11/18/2025 09:44:56"), "2025-11-18")
        self.assertEqual(parse_reservation_creation_date(""), None)
        self.assertEqual(parse_reservation_creation_date(None), None)

    def test_audit_reservation_payout(self):
        from src.reservation_store import audit_reservation_payout

        # Mock rate snapshots
        snapshots = {
            "2026-06-01": [{"snapshot_date": "2026-02-01", "nightly_rate": 500.0}],
            "2026-06-02": [{"snapshot_date": "2026-02-01", "nightly_rate": 500.0}],
            "2026-06-03": [{"snapshot_date": "2026-02-01", "nightly_rate": 500.0}],
            "2026-11-01": [{"snapshot_date": "2026-02-01", "nightly_rate": 500.0}],
            "2026-11-02": [{"snapshot_date": "2026-02-01", "nightly_rate": 500.0}],
            "2026-11-03": [{"snapshot_date": "2026-02-01", "nightly_rate": 500.0}],
            "2026-10-23": [{"snapshot_date": "2026-02-01", "nightly_rate": 532.33}],
            "2026-10-24": [{"snapshot_date": "2026-02-01", "nightly_rate": 532.33}],
            "2026-10-25": [{"snapshot_date": "2026-02-01", "nightly_rate": 532.34}],
            "2026-10-04": [{"snapshot_date": "2026-02-01", "nightly_rate": 500.0}],
            "2026-10-05": [{"snapshot_date": "2026-02-01", "nightly_rate": 500.0}],
            "2026-10-06": [{"snapshot_date": "2026-02-01", "nightly_rate": 500.0}],
            "2026-10-07": [{"snapshot_date": "2026-02-01", "nightly_rate": 500.0}],
            "2026-10-08": [{"snapshot_date": "2026-02-01", "nightly_rate": 500.0}],
            "2026-10-09": [{"snapshot_date": "2026-02-01", "nightly_rate": 500.0}],
            "2026-10-10": [{"snapshot_date": "2026-02-01", "nightly_rate": 500.0}],
        }
        # 3 nights * $500 = $1,500 expected gross -> expected owner (82%) = $1,230.00

        # Case 1: Verified booking (matches exactly)
        res_verified = {
            "id": 201,
            "confirmation_id": 8001,
            "creation_date": "03/10/2026 10:00:00",
            "start_date": "2026-06-01",
            "end_date": "2026-06-04",
            "days_number": 3,
            "gross_rent": 1500.0,
            "owner_payout": 1230.0,
            "status_name": "Booked",
            "type_name": "STA",
        }
        audit_v = audit_reservation_payout(res_verified, all_snapshots=snapshots)
        self.assertEqual(audit_v["audit_status"], "verified")
        self.assertFalse(audit_v["audit_failed"])
        self.assertEqual(audit_v["expected_gross_rent"], 1500.0)
        self.assertEqual(audit_v["expected_owner_payout"], 1230.0)
        self.assertAlmostEqual(audit_v["discrepancy"], 0.0, places=2)

        # Case 2: Shortfall booking (underpaid by > $5, non-summer, non-routine promo)
        res_shortfall = {
            "id": 202,
            "confirmation_id": 8002,
            "creation_date": "03/15/2026 12:00:00",
            "start_date": "2026-11-01",
            "end_date": "2026-11-04",
            "days_number": 3,
            "gross_rent": 1200.0,
            "owner_payout": 984.0,  # Shortfall: 984 - 1230 = -246
            "status_name": "Booked",
            "type_name": "STA",
            "hear_about_name": "Airbnb",
        }
        audit_s = audit_reservation_payout(res_shortfall, all_snapshots=snapshots)
        self.assertEqual(audit_s["audit_status"], "shortfall")
        self.assertTrue(audit_s["audit_failed"])
        self.assertAlmostEqual(audit_s["audit_shortfall"], 246.0, places=2)
        self.assertIn("Shortfall", audit_s["status_label"])
        self.assertIn("Airbnb", audit_s["diagnostic_text"])
        self.assertEqual(len(audit_s["nightly_breakdown"]), 3)

        # Case 3: Legacy booking (created prior to Feb 1, 2026)
        res_legacy = {
            "id": 203,
            "confirmation_id": 8003,
            "creation_date": "01/15/2026 08:00:00",
            "start_date": "2026-06-01",
            "end_date": "2026-06-04",
            "days_number": 3,
            "gross_rent": 1000.0,
            "owner_payout": 820.0,
            "status_name": "Booked",
            "type_name": "STA",
        }
        audit_l = audit_reservation_payout(res_legacy, all_snapshots=snapshots)
        self.assertEqual(audit_l["audit_status"], "legacy")
        self.assertTrue(audit_l["is_legacy"])
        self.assertFalse(audit_l["audit_failed"])

        # Case 4: Owner / Maintenance Stay (Exempt)
        res_exempt = {
            "id": 204,
            "confirmation_id": 8004,
            "creation_date": "04/01/2026 09:00:00",
            "start_date": "2026-06-01",
            "end_date": "2026-06-04",
            "days_number": 3,
            "gross_rent": 0.0,
            "owner_payout": 0.0,
            "status_name": "Booked",
            "type_name": "OWN",
            "type_description": "Owner Block",
        }
        audit_e = audit_reservation_payout(res_exempt, all_snapshots=snapshots)
        self.assertEqual(audit_e["audit_status"], "exempt")
        self.assertTrue(audit_e["is_exempt"])
        self.assertFalse(audit_e["audit_failed"])

        # Case 5: Summer Low-Season Markdown (May–Aug, 22%–38% discount)
        res_summer = {
            "id": 205,
            "confirmation_id": 8005,
            "creation_date": "02/07/2026 14:00:00",
            "start_date": "2026-06-01",
            "end_date": "2026-06-04",
            "days_number": 3,
            "gross_rent": 1080.0,  # 1500 -> 1080 is 28.0% discount
            "owner_payout": 885.6,
            "status_name": "Booked",
            "type_name": "STA",
        }
        audit_summer = audit_reservation_payout(res_summer, all_snapshots=snapshots)
        self.assertEqual(audit_summer["audit_status"], "discount_summer")
        self.assertTrue(audit_summer["is_known_discount"])
        self.assertFalse(audit_summer["audit_failed"])
        self.assertEqual(audit_summer["discount_category"], "summer")
        self.assertIn("Summer", audit_summer["status_label"])
        self.assertIn("Low-Season Summer Markdown", audit_summer["diagnostic_text"])
        self.assertEqual(audit_summer["rule_title"], "☀️ Summer Low-Season Markdown")

        # Case 6: Weekly Stay Discount (7+ nights, 14%–26% discount)
        res_weekly = {
            "id": 206,
            "confirmation_id": 8006,
            "creation_date": "03/15/2026 10:00:00",
            "start_date": "2026-10-04",
            "end_date": "2026-10-11",
            "days_number": 7,
            "gross_rent": 2800.0,  # e.g. Catalog 3393 -> 2800 is ~17.5% discount
            "owner_payout": 2296.0,
            "status_name": "Booked",
            "type_name": "STA",
        }
        audit_weekly = audit_reservation_payout(res_weekly, all_snapshots=snapshots)
        self.assertEqual(audit_weekly["audit_status"], "discount_weekly")
        self.assertTrue(audit_weekly["is_known_discount"])
        self.assertFalse(audit_weekly["audit_failed"])
        self.assertEqual(audit_weekly["discount_category"], "weekly")
        self.assertIn("Weekly", audit_weekly["status_label"])

        # Case 7: Standard 10% Channel Promotion (6%–15% discount)
        res_promo = {
            "id": 207,
            "confirmation_id": 8007,
            "creation_date": "04/01/2026 10:00:00",
            "start_date": "2026-10-23",
            "end_date": "2026-10-26",
            "days_number": 3,
            "gross_rent": 1440.0,  # Catalog 1597 -> 1440 is ~9.8% discount
            "owner_payout": 1180.8,
            "status_name": "Booked",
            "type_name": "STA",
        }
        audit_promo = audit_reservation_payout(res_promo, all_snapshots=snapshots)
        self.assertEqual(audit_promo["audit_status"], "discount_promo")
        self.assertTrue(audit_promo["is_known_discount"])
        self.assertFalse(audit_promo["audit_failed"])
        self.assertEqual(audit_promo["discount_category"], "promo")
        self.assertIn("Promo", audit_promo["status_label"])

    def test_reservations_tab_and_calendar_audit_ui(self):
        mock_reservations = [
            {
                "id": 301,
                "confirmation_id": 9101,
                "creation_date": "03/15/2026 10:00:00",
                "start_date": "2026-09-10",
                "end_date": "2026-09-13",
                "days_number": 3,
                "gross_rent": 1000.0,
                "owner_payout": 820.0,
                "status_name": "Booked",
                "type_name": "STA",
                "type_description": "Standard",
                "is_future": 1,
            }
        ]
        # Check Reservations tab rendering contains audit KPI card, column, and filter
        tab_html = crv.render_reservations_tab(mock_reservations, today=date(2026, 9, 6))
        self.assertIn("Rate Audit Shortfalls", tab_html)
        self.assertIn("Platform Discounts Applied", tab_html)
        self.assertIn("resFilterShortfall", tab_html)
        self.assertIn("resFilterDiscounts", tab_html)
        self.assertIn("resFilterPromo", tab_html)
        self.assertIn("resFilterSummer", tab_html)
        self.assertIn("resFilterWeekly", tab_html)
        self.assertIn("Rate Audit", tab_html)
        self.assertIn("Expected Gross Rent", tab_html)
        self.assertIn("data-expectedgross", tab_html)
        self.assertIn("data-audit-status", tab_html)

        # Check JS rendering contains audit markers and dialog section
        js = crv.get_calendar_revenue_js(mock_reservations, {"by_year": {}}, today=date(2026, 9, 6))
        self.assertIn("cal-day-audit-warn", js)
        self.assertIn("Owner Payout & Published Rate Audit", js)
        self.assertIn("Night-by-Night Rate Breakdown", js)

    def test_recent_rate_changes_and_affected_intervals(self):
        """Verify get_recent_rate_change_dates and get_intervals_with_recent_rate_changes."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "test_reservations.db"
            json_path = Path(tmp_dir) / "test_reservations.json"
            store = ReservationStore(db_path=db_path, json_path=json_path)

            # Snapshot 1: 2026-08-01 (Baseline)
            snap_1 = {
                "2026-09-13": {"nightly_rate": 549.0, "interval_type": "weekend"},
                "2026-09-14": {"nightly_rate": 399.0, "interval_type": "midweek"},
                "2026-09-15": {"nightly_rate": 399.0, "interval_type": "midweek"},
                "2026-09-16": {"nightly_rate": 399.0, "interval_type": "midweek"},
                "2026-09-20": {"nightly_rate": 549.0, "interval_type": "weekend"},
                "2026-10-15": {"nightly_rate": 450.0, "interval_type": "midweek"},
            }
            store.record_rate_snapshots("2026-08-01", snap_1)

            # Snapshot 2: 2026-09-01 (Old change, >7 days before 2026-09-10)
            # 2026-10-15 changed from 450 to 500 on 2026-09-01 (9 days ago)
            snap_2 = dict(snap_1)
            snap_2["2026-10-15"] = {"nightly_rate": 500.0, "interval_type": "midweek"}
            store.record_rate_snapshots("2026-09-01", snap_2)

            # Snapshot 3: 2026-09-08 (Recent change, 2 days before 2026-09-10)
            # 2026-09-13 lowered from 549 to 399
            snap_3 = dict(snap_2)
            snap_3["2026-09-13"] = {"nightly_rate": 399.0, "interval_type": "midweek"}
            store.record_rate_snapshots("2026-09-08", snap_3)

            as_of = date(2026, 9, 10)

            # Query recent rate changes within 7 days
            changes_7d = store.get_recent_rate_change_dates(days_back=7, as_of_date=as_of)
            # 2026-09-13 should be detected (changed on 2026-09-08, 2 days ago)
            self.assertIn("2026-09-13", changes_7d)
            self.assertEqual(len(changes_7d["2026-09-13"]), 1)
            self.assertEqual(changes_7d["2026-09-13"][0]["old_rate"], 549.0)
            self.assertEqual(changes_7d["2026-09-13"][0]["new_rate"], 399.0)
            self.assertEqual(changes_7d["2026-09-13"][0]["change_date"], "2026-09-08")

            # 2026-10-15 should NOT be detected (changed on 2026-09-01, 9 days ago, >7 days)
            self.assertNotIn("2026-10-15", changes_7d)
            # Unchanged dates like 2026-09-14 should NOT be in changes
            self.assertNotIn("2026-09-14", changes_7d)

            # If days_back=10, 2026-10-15 SHOULD be included
            changes_10d = store.get_recent_rate_change_dates(days_back=10, as_of_date=as_of)
            self.assertIn("2026-10-15", changes_10d)
            self.assertEqual(changes_10d["2026-10-15"][0]["old_rate"], 450.0)
            self.assertEqual(changes_10d["2026-10-15"][0]["new_rate"], 500.0)

            # Test get_intervals_with_recent_rate_changes
            intervals = [
                {"check_in": "2026-09-13", "check_out": "2026-09-17", "nights": 4, "segment_type": "midweek"},
                {"check_in": "2026-09-20", "check_out": "2026-09-24", "nights": 4, "segment_type": "midweek"},
                {"check_in": "2026-10-15", "check_out": "2026-10-18", "nights": 3, "segment_type": "weekend"},
            ]

            affected = store.get_intervals_with_recent_rate_changes(intervals, days_back=7, as_of_date=as_of)
            self.assertEqual(len(affected), 1)
            self.assertEqual(affected[0]["check_in"], "2026-09-13")
            self.assertEqual(affected[0]["check_out"], "2026-09-17")
            self.assertIn("affected_rate_changes", affected[0])
            self.assertIn("2026-09-13", affected[0]["affected_rate_changes"])

            # A full week after (e.g. 2026-09-16, 8 days after 2026-09-08), 0 intervals are affected
            as_of_future = date(2026, 9, 16)
            affected_future = store.get_intervals_with_recent_rate_changes(intervals, days_back=7, as_of_date=as_of_future)
            self.assertEqual(len(affected_future), 0)


if __name__ == "__main__":
    unittest.main()

