"""
Unit tests for PlatformComparator: cross-channel price comparison, divergence thresholds,
TSV export, and fee derivation formulas.
"""

import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import asyncio
from datetime import date
from pathlib import Path
import json

from src.platform_comparator import PlatformComparator, PriceBreakdown, IntervalComparison


class TestPlatformComparator(unittest.TestCase):

    def setUp(self):
        self.comparator = PlatformComparator()
        self.comparator.retry_delay = 0.0
        self.comparator.sequential_delay = 0.0

    def tearDown(self):
        if hasattr(self, "comparator"):
            self.comparator.contexts.clear()
            self.comparator._browser = None

    def test_calc_divergence_parity(self):
        """Variance <= 5% should evaluate to 'ok' and green badge."""
        res = self.comparator.calc_divergence(1000.0, 1030.0)
        self.assertAlmostEqual(res["diff_percent"], 3.0, places=1)
        self.assertEqual(res["tier"], "ok")
        self.assertEqual(res["symbol"], "🟩")
        self.assertEqual(res["formatted"], "+3.0%")

        # Zero difference
        res_exact = self.comparator.calc_divergence(2500.0, 2500.0)
        self.assertAlmostEqual(res_exact["diff_percent"], 0.0, places=1)
        self.assertEqual(res_exact["tier"], "ok")
        self.assertEqual(res_exact["formatted"], "0.0%")

    def test_calc_divergence_moderate(self):
        """Variance between 5% and 10% should trigger 'moderate' and orange badge."""
        # +7.5% higher
        res_high = self.comparator.calc_divergence(2000.0, 2150.0)
        self.assertAlmostEqual(res_high["diff_percent"], 7.5, places=1)
        self.assertEqual(res_high["tier"], "moderate")
        self.assertEqual(res_high["symbol"], "🟧")
        self.assertEqual(res_high["formatted"], "+7.5%")

        # -6.0% lower
        res_low = self.comparator.calc_divergence(2000.0, 1880.0)
        self.assertAlmostEqual(res_low["diff_percent"], -6.0, places=1)
        self.assertEqual(res_low["tier"], "moderate")
        self.assertEqual(res_low["symbol"], "🟧")
        self.assertEqual(res_low["formatted"], "-6.0%")

    def test_calc_divergence_urgent(self):
        """Variance > 10% should trigger 'urgent' and red badge."""
        # +18.5% higher
        res_high = self.comparator.calc_divergence(1000.0, 1185.0)
        self.assertAlmostEqual(res_high["diff_percent"], 18.5, places=1)
        self.assertEqual(res_high["tier"], "urgent")
        self.assertEqual(res_high["symbol"], "🟥")
        self.assertEqual(res_high["formatted"], "+18.5%")

        # -15.0% lower
        res_low = self.comparator.calc_divergence(1000.0, 850.0)
        self.assertAlmostEqual(res_low["diff_percent"], -15.0, places=1)
        self.assertEqual(res_low["tier"], "urgent")
        self.assertEqual(res_low["symbol"], "🟥")
        self.assertEqual(res_low["formatted"], "-15.0%")

    def test_calc_divergence_missing_or_zero(self):
        """Missing or zero base price returns none tier gracefully."""
        res_none = self.comparator.calc_divergence(None, 1000.0)
        self.assertIsNone(res_none["diff_percent"])
        self.assertEqual(res_none["tier"], "none")

        res_zero = self.comparator.calc_divergence(0.0, 1000.0)
        self.assertIsNone(res_zero["diff_percent"])
        self.assertEqual(res_zero["tier"], "none")

    def test_kivoya_direct_fee_derivation(self):
        """Kivoya direct rates should include base + $550 clean + 6% proc + 3% admin + 14.07% STR taxes."""
        with patch.object(self.comparator.kivoya_client, "get_seasonal_rates", return_value=[]), \
             patch.object(self.comparator.kivoya_client, "get_rate_for_date", return_value=500.0), \
             patch.object(self.comparator.kivoya_client, "get_pre_reservation_quote", return_value=None):
            res = self.comparator.get_kivoya_quote(date(2026, 10, 1), date(2026, 10, 4), nights=3)
            self.assertEqual(res.base_subtotal, 1500.0)
            self.assertEqual(res.nightly_rate, 500.0)
            self.assertEqual(res.cleaning_fee, 550.0)
            self.assertEqual(res.service_fee, 154.20)
            self.assertAlmostEqual(res.taxes, 301.10, places=2)
            self.assertAlmostEqual(res.total_price, 2505.30, places=2)
            self.assertAlmostEqual(res.effective_nightly, round(2505.30 / 3, 2), places=2)

    def test_generate_tsv_formatting_dict(self):
        """TSV output must match expected columns and format for spreadsheet pasting with dict items."""
        mock_comparisons = [
            {
                "check_in": "2026-09-24",
                "check_out": "2026-09-27",
                "segment_type": "weekend",
                "nights": 3,
                "airbnb": {
                    "total_price": 2312.00,
                    "effective_nightly": 770.67,
                },
                "vrbo": {
                    "total_price": 2752.14,
                    "effective_nightly": 917.38,
                },
                "booking": {
                    "total_price": 3039.11,
                    "effective_nightly": 1013.04,
                },
                "kivoya": {
                    "total_price": 2456.17,
                    "effective_nightly": 818.72,
                },
                "max_divergence_pct": 31.4,
            }
        ]
        tsv = self.comparator.generate_tsv(mock_comparisons)
        lines = tsv.strip().split("\n")
        self.assertEqual(len(lines), 2)  # Header + 1 row

        headers = lines[0].split("\t")
        self.assertIn("Check-In", headers)
        self.assertIn("Airbnb Total ($)", headers)
        self.assertIn("VRBO vs Airbnb (%)", headers)
        self.assertIn("Booking vs Airbnb (%)", headers)
        self.assertIn("Kivoya vs Airbnb (%)", headers)

        data_row = lines[1].split("\t")
        self.assertEqual(data_row[0], "2026-09-24")
        self.assertEqual(data_row[1], "2026-09-27")
        self.assertEqual(data_row[4], "$2,312.00")
        self.assertEqual(data_row[8], "+19.0%")
        self.assertEqual(data_row[11], "+31.4%")
        self.assertEqual(data_row[14], "+6.2%")
        self.assertEqual(data_row[15], "31.4%")

    def test_generate_tsv_formatting_dataclass(self):
        """TSV output must match expected columns and format when using IntervalComparison dataclass."""
        comp = IntervalComparison(
            check_in="2026-10-01",
            check_out="2026-10-04",
            nights=3,
            segment_type="weekend",
            is_calendar_open=True,
            airbnb=PriceBreakdown(platform="airbnb", available=True, total_price=2000.0, effective_nightly=666.67),
            vrbo=PriceBreakdown(platform="vrbo", available=True, total_price=2200.0, effective_nightly=733.33),
            booking=PriceBreakdown(platform="booking", available=True, total_price=2400.0, effective_nightly=800.0),
            kivoya=PriceBreakdown(platform="kivoya", available=True, total_price=2100.0, effective_nightly=700.0),
            max_divergence_pct=20.0,
        )
        tsv = self.comparator.generate_tsv([comp])
        lines = tsv.strip().split("\n")
        self.assertEqual(len(lines), 2)

        data_row = lines[1].split("\t")
        self.assertEqual(data_row[0], "2026-10-01")
        self.assertEqual(data_row[4], "$2,000.00")
        self.assertEqual(data_row[8], "+10.0%")
        self.assertEqual(data_row[11], "+20.0%")
        self.assertEqual(data_row[14], "+5.0%")
        self.assertEqual(data_row[15], "20.0%")

    def test_airbnb_tax_derivation(self):
        """Airbnb taxes must equal 12.52% of pre-tax subtotal (Tempe Hotel 5% + AZ TPT 5.5% + Tempe TPT 1.8% + Maricopa 0.22%)."""
        pretax = 4823.00
        tax_rate = 0.1252
        taxes = round(pretax * tax_rate, 2)
        total = round(pretax + taxes, 2)
        self.assertEqual(taxes, 603.84)
        self.assertEqual(total, 5426.84)

    def test_vrbo_tax_derivation(self):
        """VRBO taxes must equal individual statutory tax lines (AZ 5.5% + Maricopa TPT 1.77% + Tempe Hotel 1.8% + Tempe Motel 5%)."""
        lodging_base = 4495.00
        az_tax = round(lodging_base * 0.055, 2)
        maricopa_tax = round(lodging_base * 0.0177, 2)
        tempe_hotel = round(lodging_base * 0.018, 2)
        tempe_motel = round(lodging_base * 0.05, 2)
        taxes = round(az_tax + maricopa_tax + tempe_hotel + tempe_motel, 2)
        total = round(5250.66 + taxes, 2)
        self.assertEqual(az_tax, 247.22)
        self.assertEqual(maricopa_tax, 79.56)
        self.assertEqual(tempe_hotel, 80.91)
        self.assertEqual(tempe_motel, 224.75)
        self.assertEqual(taxes, 632.44)
        self.assertEqual(total, 5883.10)

    def test_format_quote_price(self):
        """format_quote_price should safely return dollar amounts, Unavail, or N/A without throwing TypeError."""
        # Available with price
        pb_avail = PriceBreakdown(platform="airbnb", available=True, total_price=3053.0)
        self.assertEqual(PlatformComparator.format_quote_price(pb_avail), "$3,053")

        # Unavailable platform (e.g. min stay restriction)
        pb_unavail = PriceBreakdown(platform="airbnb", available=False, total_price=None, notes="Minimum stay is 3 nights")
        self.assertEqual(PlatformComparator.format_quote_price(pb_unavail), "Unavail")

        # None price on available breakdown
        pb_none_price = PriceBreakdown(platform="booking", available=True, total_price=None)
        self.assertEqual(PlatformComparator.format_quote_price(pb_none_price), "N/A")

        # None object
        self.assertEqual(PlatformComparator.format_quote_price(None), "N/A")

    def test_generate_tsv_with_unavailable_platform(self):
        """TSV formatting should handle unavailable platforms and None total_price gracefully."""
        comp = IntervalComparison(
            check_in="2027-01-19",
            check_out="2027-01-21",
            nights=2,
            segment_type="midweek",
            is_calendar_open=True,
            airbnb=PriceBreakdown(platform="airbnb", available=False, total_price=None, notes="Minimum stay is 3 nights"),
            vrbo=PriceBreakdown(platform="vrbo", available=True, total_price=2664.66, effective_nightly=1332.33),
            booking=PriceBreakdown(platform="booking", available=True, total_price=2556.34, effective_nightly=1278.17),
            kivoya=PriceBreakdown(platform="kivoya", available=True, total_price=2254.63, effective_nightly=1127.32),
            max_divergence_pct=0.0,
            notes="Airbnb unavailable",
        )
        tsv = self.comparator.generate_tsv([comp])
        lines = tsv.strip().split("\n")
        self.assertEqual(len(lines), 2)
        data_row = lines[1].split("\t")
        self.assertEqual(data_row[0], "2027-01-19")
        self.assertEqual(data_row[4], "N/A")  # Airbnb total
        self.assertEqual(data_row[8], "N/A")  # VRBO vs Airbnb
        self.assertEqual(data_row[11], "N/A")  # Booking vs Airbnb
        self.assertEqual(data_row[14], "N/A")  # Kivoya vs Airbnb

    def test_airbnb_est_no_dscnt_fee_derivation(self):
        """AIRBNB (est-no-dscnt) should derive from base nightly + $550 clean + 14.15% fee + 12.52% tax."""
        k_ref = PriceBreakdown(
            platform="kivoya",
            available=True,
            nightly_rate=500.0,
            base_subtotal=1500.0,
            nights=3,
        )
        est = self.comparator.get_airbnb_est_no_dscnt_quote(
            date(2026, 10, 1), date(2026, 10, 4), nights=3, kivoya_ref=k_ref
        )
        # Base: $1,500
        # Clean: $550
        # Pre-tax: round(($1500 + $550) * 1.1415, 2) = round(2340.075, 2) = 2340.07
        # Svc: 2340.07 - 2050 = 290.07
        # Tax: round(2340.07 * 0.1252, 2) = 292.98
        # Total: 2340.07 + 292.98 = 2633.05
        self.assertEqual(est.platform, "airbnb_est_no_dscnt")
        self.assertEqual(est.base_subtotal, 1500.0)
        self.assertEqual(est.cleaning_fee, 550.0)
        self.assertAlmostEqual(est.service_fee, 290.07, places=2)
        self.assertAlmostEqual(est.taxes, 292.98, places=2)
        self.assertAlmostEqual(est.total_price, 2633.05, places=2)
        self.assertAlmostEqual(est.effective_nightly, round(2633.05 / 3, 2), places=2)

    def test_generate_tsv_with_airbnb_est_no_dscnt(self):
        """TSV formatting with airbnb_est_no_dscnt present should include Benchmark and live columns."""
        comp = IntervalComparison(
            check_in="2026-10-01",
            check_out="2026-10-04",
            nights=3,
            segment_type="weekend",
            is_calendar_open=True,
            airbnb_est_no_dscnt=PriceBreakdown(
                platform="airbnb_est_no_dscnt",
                available=True,
                total_price=2633.06,
                effective_nightly=877.69,
            ),
            airbnb=PriceBreakdown(
                platform="airbnb",
                available=True,
                total_price=2369.75,  # -10.0% discounted live
                effective_nightly=789.92,
            ),
            vrbo=PriceBreakdown(
                platform="vrbo",
                available=True,
                total_price=2896.37,  # +10.0% higher
                effective_nightly=965.46,
            ),
            booking=PriceBreakdown(
                platform="booking",
                available=True,
                total_price=2633.06,
                effective_nightly=877.69,
            ),
            kivoya=PriceBreakdown(
                platform="kivoya",
                available=True,
                total_price=2505.30,
                effective_nightly=835.10,
            ),
            max_divergence_pct=10.0,
        )
        tsv = self.comparator.generate_tsv([comp])
        lines = tsv.strip().split("\n")
        self.assertEqual(len(lines), 2)
        headers = lines[0].split("\t")
        self.assertIn("AIRBNB (est-no-dscnt) Total ($)", headers)
        self.assertIn("Airbnb (live) Total ($)", headers)
        self.assertIn("Airbnb (live) vs Benchmark (%)", headers)
        self.assertIn("VRBO vs Benchmark (%)", headers)

        data_row = lines[1].split("\t")
        self.assertEqual(data_row[0], "2026-10-01")
        self.assertEqual(data_row[4], "$2,633.06")  # AIRBNB (est-no-dscnt) Total
        self.assertEqual(data_row[6], "$2,369.75")  # Airbnb (live) Total
        self.assertEqual(data_row[8], "-10.0%")     # Airbnb (live) vs Benchmark
        self.assertEqual(data_row[11], "+10.0%")    # VRBO vs Benchmark

    def test_compare_all_intervals_with_target_segments(self):
        """Verify compare_all_intervals only processes target_segments when provided."""
        target = [
            {"check_in": "2026-09-13", "check_out": "2026-09-17", "nights": 4, "segment_type": "midweek", "is_calendar_open": True}
        ]
        mock_comp = IntervalComparison(
            check_in="2026-09-13",
            check_out="2026-09-17",
            nights=4,
            segment_type="midweek",
            is_calendar_open=True,
            airbnb=PriceBreakdown(platform="airbnb", available=True, total_price=2842.0),
            vrbo=PriceBreakdown(platform="vrbo", available=True, total_price=3087.0),
            booking=PriceBreakdown(platform="booking", available=True, total_price=3016.0),
            kivoya=PriceBreakdown(platform="kivoya", available=True, total_price=2624.0),
        )

        with patch.object(self.comparator, "compare_interval", return_value=mock_comp) as mock_compare, \
             patch.object(self.comparator, "_regenerate_dashboard"):
            import asyncio
            results = asyncio.run(self.comparator.compare_all_intervals(target_segments=target, force=True))
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].check_in, "2026-09-13")
            mock_compare.assert_called_once_with(
                check_in="2026-09-13",
                check_out="2026-09-17",
                nights=4,
                segment_type="midweek",
                is_calendar_open=True,
                use_cache=False,
            )

    def test_track_response_bytes(self):
        """Verify _track_response_bytes accumulates bytes transferred from response content-length header."""
        self.assertEqual(self.comparator.total_bytes_transferred, 0)
        self.assertEqual(self.comparator._curr_interval_bytes, 0)

        # 1 MB response
        resp = MagicMock()
        resp.headers = {"content-length": "1048576"}
        self.comparator._track_response_bytes(resp)
        self.assertEqual(self.comparator.total_bytes_transferred, 1048576)
        self.assertEqual(self.comparator._curr_interval_bytes, 1048576)

        # Non-digit or missing
        resp_empty = MagicMock()
        resp_empty.headers = {}
        self.comparator._track_response_bytes(resp_empty)
        self.assertEqual(self.comparator.total_bytes_transferred, 1048576)

    def test_init_and_close_browser(self):
        """Verify init_browser creates dedicated contexts per feeder channel and close_browser cleans up."""
        from unittest.mock import AsyncMock
        import asyncio

        mock_p = MagicMock()
        mock_browser = MagicMock()
        mock_browser.close = AsyncMock()
        mock_ctx = MagicMock()
        mock_ctx.close = AsyncMock()
        mock_browser.new_context = AsyncMock(return_value=mock_ctx)
        mock_p.chromium.launch = AsyncMock(return_value=mock_browser)

        fake_configs = [
            {"server": "http://127.0.0.1:56001", "name": "feeder-la"},
            {"server": "http://127.0.0.1:56002", "name": "feeder-sf"},
            {"server": "http://127.0.0.1:56003", "name": "feeder-dal"},
        ]

        with patch.object(self.comparator.proxy_mgr, "start_pool", AsyncMock(return_value=fake_configs)), \
             patch.object(self.comparator.proxy_mgr, "stop", AsyncMock()):

            async def run_test():
                await self.comparator.init_browser(mock_p)
                self.assertEqual(len(self.comparator.contexts), 3)
                self.assertIn("airbnb", self.comparator.contexts)
                self.assertIn("booking", self.comparator.contexts)
                self.assertIn("vrbo", self.comparator.contexts)
                # Verify proxy arg was sanitized to only {"server": ...} without extraneous keys
                self.assertEqual(mock_browser.new_context.call_args[1]["proxy"], {"server": "http://127.0.0.1:56003"})

                await self.comparator.close_browser()
                self.assertEqual(len(self.comparator.contexts), 0)
                self.assertIsNone(self.comparator._browser)

            asyncio.run(run_test())

    def test_compare_interval_parallel_execution(self):
        """Verify compare_interval gathers Airbnb, Booking, and VRBO in parallel."""
        from unittest.mock import AsyncMock
        import asyncio

        mock_browser = MagicMock()
        mock_ctx = MagicMock()
        mock_page = MagicMock()
        mock_page.close = AsyncMock()
        mock_ctx.new_page = AsyncMock(return_value=mock_page)

        self.comparator._browser = mock_browser
        self.comparator.contexts = {
            "airbnb": mock_ctx,
            "booking": mock_ctx,
            "vrbo": mock_ctx,
        }

        quote_a = PriceBreakdown(platform="airbnb", available=True, total_price=2800.0, effective_nightly=700.0)
        quote_b = PriceBreakdown(platform="booking", available=True, total_price=3000.0, effective_nightly=750.0)
        quote_v = PriceBreakdown(platform="vrbo", available=True, total_price=3100.0, effective_nightly=775.0)

        with patch.object(self.comparator, "scrape_airbnb_quote", AsyncMock(return_value=quote_a)), \
             patch.object(self.comparator, "scrape_booking_quote", AsyncMock(return_value=quote_b)), \
             patch.object(self.comparator, "scrape_vrbo_quote", AsyncMock(return_value=quote_v)), \
             patch.object(self.comparator, "get_kivoya_quote", return_value=PriceBreakdown(platform="kivoya", available=True, total_price=2600.0)), \
             patch.object(self.comparator, "get_airbnb_est_no_dscnt_quote", return_value=PriceBreakdown(platform="airbnb_est_no_dscnt", available=True, total_price=2800.0)):

            async def run_test():
                res = await self.comparator.compare_interval(
                    check_in="2026-10-15",
                    check_out="2026-10-19",
                    nights=4,
                    use_cache=False,
                )
                self.assertEqual(res.airbnb.total_price, 2800.0)
                self.assertEqual(res.booking.total_price, 3000.0)
                self.assertEqual(res.vrbo.total_price, 3100.0)

            asyncio.run(run_test())

        self.comparator.contexts.clear()
        self.comparator._browser = None

    def test_compare_interval_error_isolation(self):
        """Verify a failure in one platform does not abort the other channels."""
        from unittest.mock import AsyncMock
        import asyncio

        mock_browser = MagicMock()
        mock_ctx = MagicMock()
        mock_page = MagicMock()
        mock_page.close = AsyncMock()
        mock_ctx.new_page = AsyncMock(return_value=mock_page)

        self.comparator._browser = mock_browser
        self.comparator.contexts = {
            "airbnb": mock_ctx,
            "booking": mock_ctx,
            "vrbo": mock_ctx,
        }

        quote_a = PriceBreakdown(platform="airbnb", available=True, total_price=2800.0)
        quote_b = PriceBreakdown(platform="booking", available=True, total_price=3000.0)

        with patch.object(self.comparator, "scrape_airbnb_quote", AsyncMock(return_value=quote_a)), \
             patch.object(self.comparator, "scrape_booking_quote", AsyncMock(return_value=quote_b)), \
             patch.object(self.comparator, "scrape_vrbo_quote", AsyncMock(side_effect=Exception("VRBO Network Timeout"))), \
             patch.object(self.comparator, "get_kivoya_quote", return_value=PriceBreakdown(platform="kivoya", available=True, total_price=2600.0)), \
             patch.object(self.comparator, "get_airbnb_est_no_dscnt_quote", return_value=PriceBreakdown(platform="airbnb_est_no_dscnt", available=True, total_price=2800.0)):

            async def run_test():
                res = await self.comparator.compare_interval(
                    check_in="2026-10-15",
                    check_out="2026-10-19",
                    nights=4,
                    use_cache=False,
                )
                self.assertTrue(res.airbnb.available)
                self.assertTrue(res.booking.available)
                self.assertFalse(res.vrbo.available)
                self.assertIn("VRBO Network Timeout", res.vrbo.notes)

            asyncio.run(run_test())

    def test_compare_interval_sequential_error_isolation(self):
        """Verify errors in sequential mode are caught and isolated safely."""
        from unittest.mock import AsyncMock
        import asyncio

        mock_browser = MagicMock()
        mock_ctx = MagicMock()
        mock_page = MagicMock()
        mock_page.close = AsyncMock()
        mock_ctx.new_page = AsyncMock(return_value=mock_page)

        self.comparator.parallel = False
        self.comparator._browser = mock_browser
        self.comparator.contexts = {
            "airbnb": mock_ctx,
            "booking": mock_ctx,
            "vrbo": mock_ctx,
        }

        quote_a = PriceBreakdown(platform="airbnb", available=True, total_price=2800.0)
        quote_v = PriceBreakdown(platform="vrbo", available=True, total_price=3100.0)

        with patch.object(self.comparator, "scrape_airbnb_quote", AsyncMock(return_value=quote_a)), \
             patch.object(self.comparator, "scrape_booking_quote", AsyncMock(side_effect=Exception("Booking 503"))), \
             patch.object(self.comparator, "scrape_vrbo_quote", AsyncMock(return_value=quote_v)), \
             patch.object(self.comparator, "get_kivoya_quote", return_value=PriceBreakdown(platform="kivoya", available=True, total_price=2600.0)), \
             patch.object(self.comparator, "get_airbnb_est_no_dscnt_quote", return_value=PriceBreakdown(platform="airbnb_est_no_dscnt", available=True, total_price=2800.0)):

            async def run_test():
                res = await self.comparator.compare_interval(
                    check_in="2026-10-15",
                    check_out="2026-10-19",
                    nights=4,
                    use_cache=False,
                )
                self.assertTrue(res.airbnb.available)
                self.assertFalse(res.booking.available)
                self.assertIn("Booking 503", res.booking.notes)
                self.assertTrue(res.vrbo.available)

            asyncio.run(run_test())

    def test_format_quote_price_distinguishes_error_vs_unavail(self):
        """Verify format_quote_price returns '❌ Error' on exceptions/timeouts and 'Unavail' on legitimate calendar closures."""
        # Available quote
        pb_ok = PriceBreakdown(platform="airbnb", available=True, total_price=2842.0)
        self.assertEqual(PlatformComparator.format_quote_price(pb_ok), "$2,842")

        # Legitimate unavailability
        pb_unavail = PriceBreakdown(platform="vrbo", available=False, notes="Sold out / No availability")
        self.assertEqual(PlatformComparator.format_quote_price(pb_unavail), "Unavail")

        # Scrape error: net::ERR_EMPTY_RESPONSE
        pb_err_empty = PriceBreakdown(platform="vrbo", available=False, notes="Error: Page.goto: net::ERR_EMPTY_RESPONSE")
        self.assertEqual(PlatformComparator.format_quote_price(pb_err_empty), "❌ Error")

        # Scrape error: timeout
        pb_timeout = PriceBreakdown(platform="booking", available=False, notes="Scrape error: Timeout 25000ms exceeded")
        self.assertEqual(PlatformComparator.format_quote_price(pb_timeout), "❌ Error")

        # Scrape error: generic exception
        pb_exc = PriceBreakdown(platform="airbnb", available=False, notes="Failed to launch page")
        self.assertEqual(PlatformComparator.format_quote_price(pb_exc), "❌ Error")

        # None input
        self.assertEqual(PlatformComparator.format_quote_price(None), "N/A")

    def test_is_scrape_error_predicate(self):
        """Verify is_scrape_error correctly identifies network/exception failures."""
        self.assertFalse(PlatformComparator.is_scrape_error(None))
        self.assertFalse(PlatformComparator.is_scrape_error(PriceBreakdown(platform="vrbo", available=True, total_price=1000.0)))
        self.assertFalse(PlatformComparator.is_scrape_error(PriceBreakdown(platform="vrbo", available=False, notes="Sold out")))
        self.assertTrue(PlatformComparator.is_scrape_error(PriceBreakdown(platform="vrbo", available=False, notes="Error: net::ERR_EMPTY_RESPONSE")))
        self.assertTrue(PlatformComparator.is_scrape_error(PriceBreakdown(platform="booking", available=False, notes="Timeout reached")))

    def test_compare_all_intervals_accumulates_last_scrape_errors(self):
        """Verify compare_all_intervals accumulates channel error counts in last_scrape_errors."""
        import asyncio
        from unittest.mock import AsyncMock

        dummy_segments = [
            {"check_in": "2026-11-01", "check_out": "2026-11-04", "nights": 3, "segment_type": "midweek", "is_calendar_open": True},
            {"check_in": "2026-11-05", "check_out": "2026-11-08", "nights": 3, "segment_type": "weekend", "is_calendar_open": True},
        ]

        dummy_comp = IntervalComparison(
            check_in="2026-11-01",
            check_out="2026-11-04",
            nights=3,
            segment_type="midweek",
            is_calendar_open=True,
            airbnb=PriceBreakdown(platform="airbnb", available=True, total_price=2500.0),
            vrbo=PriceBreakdown(platform="vrbo", available=False, notes="Error: Page.goto: net::ERR_EMPTY_RESPONSE"),
            booking=PriceBreakdown(platform="booking", available=False, notes="Scrape error: Timeout 25000ms exceeded"),
            kivoya=PriceBreakdown(platform="kivoya", available=True, total_price=2400.0),
        )

        with patch.object(self.comparator, "compare_interval", AsyncMock(return_value=dummy_comp)):
            res = asyncio.run(self.comparator.compare_all_intervals(target_segments=dummy_segments))
            self.assertEqual(len(res), 2)
            self.assertEqual(self.comparator.last_scrape_errors["airbnb"], 0)
            self.assertEqual(self.comparator.last_scrape_errors["vrbo"], 2)
            self.assertEqual(self.comparator.last_scrape_errors["booking"], 2)

    def test_compare_all_intervals_parallel_batch_execution(self):
        """Verify compare_all_intervals runs multi-interval batch parallelism when worker_contexts >= 3."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        dummy_segments = [
            {"check_in": f"2026-11-0{i}", "check_out": f"2026-11-0{i+3}", "nights": 3, "segment_type": "midweek", "is_calendar_open": True}
            for i in range(1, 7)
        ]

        dummy_comp = IntervalComparison(
            check_in="2026-11-01",
            check_out="2026-11-04",
            nights=3,
            segment_type="midweek",
            is_calendar_open=True,
            airbnb=PriceBreakdown(platform="airbnb", available=True, total_price=2500.0),
            vrbo=PriceBreakdown(platform="vrbo", available=True, total_price=2600.0),
            booking=PriceBreakdown(platform="booking", available=True, total_price=2550.0),
            kivoya=PriceBreakdown(platform="kivoya", available=True, total_price=2400.0),
        )

        # Set up 9 mock worker contexts to trigger batch_concurrency = 9 // 3 = 3
        mock_contexts = [MagicMock() for _ in range(9)]
        self.comparator.worker_contexts = mock_contexts
        self.comparator.parallel = True

        call_order = []
        async def fake_compare_interval(**kwargs):
            call_order.append(kwargs.get("check_in"))
            await asyncio.sleep(0.01)
            return dummy_comp

        with patch.object(self.comparator, "compare_interval", side_effect=fake_compare_interval):
            res = asyncio.run(self.comparator.compare_all_intervals(target_segments=dummy_segments))
            self.assertEqual(len(res), 6)
            self.assertEqual(len(call_order), 6)
            self.assertEqual(self.comparator.last_scrape_errors["airbnb"], 0)
            self.assertEqual(self.comparator.last_scrape_errors["vrbo"], 0)
            self.assertEqual(self.comparator.last_scrape_errors["booking"], 0)

    def test_airbnb_scrape_falls_back_to_cached_total_on_navigation_error(self):
        """Verify scrape_airbnb_quote uses cached_total from Step 3 when page.goto fails."""
        cache_file = Path("data/cache/our_property_2026-10-01_2026-10-04.json")
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps({
            "listing_id": "573857947793833342",
            "check_in": "2026-10-01",
            "check_out": "2026-10-04",
            "nights": 3,
            "airbnb_total": 2200.0,
            "airbnb_effective_nightly": 733.33,
        }), encoding="utf-8")

        mock_page = AsyncMock()
        mock_page.goto = AsyncMock(side_effect=Exception("Page.goto: net::ERR_EMPTY_RESPONSE"))
        mock_page.remove_listener = MagicMock()

        try:
            quote = asyncio.run(self.comparator.scrape_airbnb_quote(
                mock_page, "2026-10-01", "2026-10-04", 3
            ))
            self.assertTrue(quote.available)
            self.assertGreater(quote.total_price, 2200.0)  # 2200 + 12.52% tax
            self.assertIn("Step 3 cache", quote.notes)
            self.assertFalse(self.comparator.is_scrape_error(quote))
        finally:
            if cache_file.exists():
                cache_file.unlink()

    def test_booking_scrape_falls_back_to_kivoya_ref_on_exception(self):
        """Verify scrape_booking_quote projects price from kivoya_ref when page.goto fails with an exception."""
        mock_page = AsyncMock()
        mock_page.goto = AsyncMock(side_effect=Exception("Page.goto: net::ERR_EMPTY_RESPONSE"))

        kivoya_ref = PriceBreakdown(
            platform="kivoya",
            available=True,
            base_subtotal=2000.0,
            nightly_rate=500.0,
            nights=4,
        )

        quote = asyncio.run(self.comparator.scrape_booking_quote(
            mock_page, "2026-10-01", "2026-10-05", 4, kivoya_ref=kivoya_ref
        ))
        self.assertTrue(quote.available)
        self.assertGreater(quote.total_price, 2000.0)
        self.assertIn("Projected via Kivoya PMS rate feed", quote.notes)
        self.assertFalse(self.comparator.is_scrape_error(quote))

    def test_vrbo_scrape_falls_back_to_kivoya_ref_on_exception(self):
        """Verify scrape_vrbo_quote projects price from kivoya_ref when page.goto fails with an exception."""
        mock_page = AsyncMock()
        mock_page.goto = AsyncMock(side_effect=Exception("Page.goto: net::ERR_EMPTY_RESPONSE"))

        kivoya_ref = PriceBreakdown(
            platform="kivoya",
            available=True,
            base_subtotal=2000.0,
            nightly_rate=500.0,
            nights=4,
        )

        quote = asyncio.run(self.comparator.scrape_vrbo_quote(
            mock_page, "2026-10-01", "2026-10-05", 4, kivoya_ref=kivoya_ref
        ))
        self.assertTrue(quote.available)
        self.assertGreater(quote.total_price, 2000.0)
        self.assertIn("Projected via Kivoya PMS", quote.notes)
        self.assertFalse(self.comparator.is_scrape_error(quote))

    def test_scrape_channel_with_retry_rotates_contexts(self):
        """Verify compare_interval retries with a fresh proxy context when attempt 1 fails."""
        ctx1 = MagicMock(name="ctx1")
        ctx2 = MagicMock(name="ctx2")

        page1 = AsyncMock(name="page1")
        page2 = AsyncMock(name="page2")
        page1.close = AsyncMock()
        page2.close = AsyncMock()

        ctx1.new_page = AsyncMock(return_value=page1)
        ctx2.new_page = AsyncMock(return_value=page2)

        q = asyncio.Queue()
        q.put_nowait(ctx1)
        q.put_nowait(ctx2)
        self.comparator._context_queue = q
        self.comparator.worker_contexts = [ctx1, ctx2]
        self.comparator.contexts = {"airbnb": ctx1, "vrbo": ctx1, "booking": ctx1}
        self.comparator._browser = MagicMock()

        # On attempt 1 (ctx1), return error. On attempt 2 (ctx2), return success.
        attempt_contexts = []
        async def fake_scrape_airbnb(page, *args, **kwargs):
            if page == page1:
                attempt_contexts.append("ctx1")
                return PriceBreakdown(platform="airbnb", available=False, notes="Error: Page.goto: net::ERR_EMPTY_RESPONSE")
            else:
                attempt_contexts.append("ctx2")
                return PriceBreakdown(platform="airbnb", available=True, total_price=2800.0)

        dummy_vrbo = PriceBreakdown(platform="vrbo", available=True, total_price=3000.0)
        dummy_booking = PriceBreakdown(platform="booking", available=True, total_price=2900.0)

        with patch.object(self.comparator, "scrape_airbnb_quote", side_effect=fake_scrape_airbnb), \
             patch.object(self.comparator, "scrape_vrbo_quote", return_value=dummy_vrbo), \
             patch.object(self.comparator, "scrape_booking_quote", return_value=dummy_booking):
            res = asyncio.run(self.comparator.compare_interval(
                check_in="2026-10-01",
                check_out="2026-10-04",
                nights=3,
                use_cache=False,
            ))
            self.assertTrue(res.airbnb.available)
            self.assertEqual(res.airbnb.total_price, 2800.0)
            self.assertEqual(attempt_contexts, ["ctx1", "ctx2"], "Expected retry to lease ctx2 after ctx1 failed")

    def test_scrape_channel_retry_promotes_live_quote_over_fallback_projection(self):
        """Verify retry loop detects fallback projection on attempt 1, rotates proxy context, and captures live quote on attempt 2."""
        ctx1 = MagicMock(name="ctx1")
        ctx2 = MagicMock(name="ctx2")

        page1 = AsyncMock(name="page1")
        page2 = AsyncMock(name="page2")
        page1.close = AsyncMock()
        page2.close = AsyncMock()

        ctx1.new_page = AsyncMock(return_value=page1)
        ctx2.new_page = AsyncMock(return_value=page2)

        q = asyncio.Queue()
        q.put_nowait(ctx1)
        q.put_nowait(ctx2)
        self.comparator._context_queue = q
        self.comparator.worker_contexts = [ctx1, ctx2]
        self.comparator.contexts = {"airbnb": ctx1, "vrbo": ctx1, "booking": ctx1}
        self.comparator._browser = MagicMock()

        # On attempt 1, return fallback projection. On attempt 2, return genuine live quote.
        attempt_pages = []
        async def fake_scrape_booking(page, *args, **kwargs):
            attempt_pages.append(page)
            if len(attempt_pages) == 1:
                return PriceBreakdown(
                    platform="booking",
                    available=True,
                    total_price=2600.0,
                    notes="Projected via Kivoya PMS rate feed (Live sweep error: net::ERR_EMPTY_RESPONSE)",
                )
            else:
                return PriceBreakdown(
                    platform="booking",
                    available=True,
                    total_price=2750.0,
                    notes="Live quote from Booking.com",
                )

        dummy_airbnb = PriceBreakdown(platform="airbnb", available=True, total_price=2500.0)
        dummy_vrbo = PriceBreakdown(platform="vrbo", available=True, total_price=2650.0)

        with patch.object(self.comparator, "scrape_airbnb_quote", return_value=dummy_airbnb), \
             patch.object(self.comparator, "scrape_booking_quote", side_effect=fake_scrape_booking), \
             patch.object(self.comparator, "scrape_vrbo_quote", return_value=dummy_vrbo), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            res = asyncio.run(self.comparator.compare_interval(
                check_in="2026-10-01",
                check_out="2026-10-04",
                nights=3,
                use_cache=False,
            ))
            self.assertEqual(len(attempt_pages), 2, "Expected 2 attempts for Booking.com")
            self.assertEqual(res.booking.total_price, 2750.0, "Expected live quote from attempt 2 to replace attempt 1 fallback projection")
            self.assertEqual(res.booking.notes, "Live quote from Booking.com")

    def test_lease_channel_context_no_deadlock_when_unproxied(self):
        """Verify lease_channel_context yields single context without deadlocking when _context_queue is None."""
        mock_ctx = MagicMock(name="fallback_ctx")
        self.comparator._context = mock_ctx
        self.comparator._context_queue = None
        self.comparator.worker_contexts = []
        self.comparator.contexts = {"airbnb": mock_ctx, "vrbo": mock_ctx, "booking": mock_ctx}

        async def run_lease():
            async with self.comparator.lease_channel_context("airbnb") as ctx:
                return ctx

        result = asyncio.run(run_lease())
        self.assertEqual(result, mock_ctx)

    def test_booking_scrape_snippet_without_price_falls_back_cleanly(self):
        """Verify scrape_booking_quote does not raise unsupported format string error when snippet lacks subtotal."""
        mock_page = AsyncMock()
        mock_page.goto = AsyncMock()
        mock_page.wait_for_timeout = AsyncMock()
        # Simulate: body has text (not sold out), but price_snippets only contains a fee row without subtotal
        mock_page.evaluate = AsyncMock(side_effect=[
            "Some hotel details and amenities",  # body_text
            ["Cleaning fee $550 included in booking details"],  # price_snippets (no subtotal or nightly rate)
        ])

        kivoya_ref = PriceBreakdown(
            platform="kivoya",
            available=True,
            base_subtotal=2000.0,
            nightly_rate=500.0,
            nights=4,
        )

        # Must NOT raise TypeError: unsupported format string passed to NoneType.__format__
        quote = asyncio.run(self.comparator.scrape_booking_quote(
            mock_page, "2026-09-13", "2026-09-17", 4, kivoya_ref=kivoya_ref
        ))
        self.assertTrue(quote.available)
        self.assertGreater(quote.total_price, 2000.0)
        self.assertIn("Projected via Kivoya PMS rate feed", quote.notes)
        self.assertNotIn("unsupported format string", quote.notes)

        # When kivoya_ref is None, must return available=False cleanly without raising TypeError
        mock_page.evaluate = AsyncMock(side_effect=[
            "Some hotel details and amenities",
            ["Cleaning fee $550 included in booking details"],
        ])
        quote_no_ref = asyncio.run(self.comparator.scrape_booking_quote(
            mock_page, "2026-09-13", "2026-09-17", 4, kivoya_ref=None
        ))
        self.assertFalse(quote_no_ref.available)
        self.assertEqual(quote_no_ref.notes, "Scrape error: No rate rows found in room table")

    def test_booking_scrape_secondary_snippet_recovers_subtotal(self):
        """Verify secondary snippet scans recover subtotal >= $1000 and nightly rate when first snippet is fee-only."""
        mock_page = AsyncMock()
        mock_page.goto = AsyncMock()
        mock_page.wait_for_timeout = AsyncMock()
        # Snippet 0 is fee-only, Snippet 1 has nightly rate and total stay price
        mock_page.evaluate = AsyncMock(side_effect=[
            "Villa del Sol Tempe",
            [
                "Cleaning fee $550 included in booking details",
                "$650 per night Total stay: $2,600",
            ],
        ])

        quote = asyncio.run(self.comparator.scrape_booking_quote(
            mock_page, "2026-09-13", "2026-09-17", 4, kivoya_ref=None
        ))
        self.assertTrue(quote.available)
        self.assertEqual(quote.nightly_rate, 650.0)
        self.assertIn("Live Booking.com quote: Subtotal $2,600", quote.notes)
        self.assertGreater(quote.total_price, 2600.0)


    def test_closed_calendar_short_circuits_compare_interval(self):
        """Verify compare_interval short-circuits closed calendar dates without launching Playwright."""
        with patch.object(self.comparator, "init_browser", AsyncMock()) as mock_init:
            res = asyncio.run(self.comparator.compare_interval(
                check_in="2027-06-03",
                check_out="2027-06-06",
                nights=3,
                is_calendar_open=False,
                use_cache=False,
            ))
            mock_init.assert_not_called()
            self.assertFalse(res.is_calendar_open)
            self.assertFalse(res.airbnb.available)
            self.assertFalse(res.vrbo.available)
            self.assertFalse(res.booking.available)
            self.assertFalse(res.kivoya.available)
            self.assertIsNone(res.airbnb_est_no_dscnt)
            self.assertIsNone(res.max_divergence_pct)
            self.assertEqual(res.notes, "Calendar closed in Kivoya PMS")

            # Verify no false-positive scrape errors
            self.assertFalse(PlatformComparator.is_scrape_error(res.airbnb))
            self.assertFalse(PlatformComparator.is_scrape_error(res.vrbo))
            self.assertFalse(PlatformComparator.is_scrape_error(res.booking))
            self.assertEqual(PlatformComparator.format_quote_price(res.vrbo), "Unavail")
            self.assertEqual(PlatformComparator.format_quote_price(res.booking), "Unavail")

    def test_closed_calendar_detected_past_open_end_date(self):
        """Verify compare_interval auto-detects closed calendar when check-in is past open_end_date."""
        with patch.object(self.comparator.kivoya_client, "get_calendar_open_end_date", return_value=date(2027, 5, 31)):
            with patch.object(self.comparator, "init_browser", AsyncMock()) as mock_init:
                res = asyncio.run(self.comparator.compare_interval(
                    check_in="2027-06-10",
                    check_out="2027-06-13",
                    nights=3,
                    is_calendar_open=True,  # Passed True, but should be overridden by open_end_date
                    use_cache=False,
                ))
                mock_init.assert_not_called()
                self.assertFalse(res.is_calendar_open)
                self.assertFalse(res.airbnb.available)
                self.assertFalse(res.vrbo.available)
                self.assertFalse(res.booking.available)
                self.assertFalse(res.kivoya.available)

    def test_closed_calendar_detected_when_stay_crosses_open_end_date(self):
        """Verify compare_interval detects closed calendar when check-in is before cutoff but stay extends past open_end_date."""
        with patch.object(self.comparator.kivoya_client, "get_calendar_open_end_date", return_value=date(2027, 5, 31)):
            with patch.object(self.comparator, "init_browser", AsyncMock()) as mock_init:
                # Check-in May 30 (before May 31), check-out June 3 (stay crosses into June)
                res = asyncio.run(self.comparator.compare_interval(
                    check_in="2027-05-30",
                    check_out="2027-06-03",
                    nights=4,
                    is_calendar_open=True,
                    use_cache=False,
                ))
                mock_init.assert_not_called()
                self.assertFalse(res.is_calendar_open)
                self.assertFalse(res.airbnb.available)
                self.assertFalse(res.vrbo.available)
                self.assertFalse(res.booking.available)
                self.assertFalse(res.kivoya.available)
                self.assertEqual(res.notes, "Calendar closed in Kivoya PMS")

    def test_get_kivoya_quote_closed_dates(self):
        """Verify get_kivoya_quote returns available=False for closed dates rather than charging cleaning fee."""
        with patch.object(self.comparator.kivoya_client, "get_calendar_open_end_date", return_value=date(2027, 5, 31)):
            quote = self.comparator.get_kivoya_quote(date(2027, 6, 3), date(2027, 6, 6), 3)
            self.assertFalse(quote.available)
            self.assertIn("Calendar closed in Kivoya PMS", quote.notes)

            # Also verify cross-boundary stay (May 30 -> June 3) returns available=False
            boundary_quote = self.comparator.get_kivoya_quote(date(2027, 5, 30), date(2027, 6, 3), 4)
            self.assertFalse(boundary_quote.available)
            self.assertIn("Calendar closed in Kivoya PMS", boundary_quote.notes)

    def test_get_airbnb_est_no_dscnt_quote_closed_dates(self):
        """Verify get_airbnb_est_no_dscnt_quote returns available=False when calendar is closed."""
        with patch.object(self.comparator.kivoya_client, "get_calendar_open_end_date", return_value=date(2027, 5, 31)):
            quote = self.comparator.get_airbnb_est_no_dscnt_quote(date(2027, 6, 3), date(2027, 6, 6), 3)
            self.assertFalse(quote.available)
            self.assertIn("Calendar closed in Kivoya PMS", quote.notes)

            # Also verify cross-boundary stay (May 30 -> June 3) returns available=False
            boundary_quote = self.comparator.get_airbnb_est_no_dscnt_quote(date(2027, 5, 30), date(2027, 6, 3), 4)
            self.assertFalse(boundary_quote.available)
            self.assertIn("Calendar closed in Kivoya PMS", boundary_quote.notes)

    def test_request_route_filtering(self):
        """Verify _filter_request_routes aborts media, fonts, and trackers while continuing API and page requests."""
        # 1. Image aborted
        mock_route = AsyncMock()
        mock_req = MagicMock(resource_type="image", url="https://a0.muscache.com/pictures/1.jpg")
        asyncio.run(PlatformComparator._filter_request_routes(mock_route, mock_req))
        mock_route.abort.assert_awaited_once()

        # 2. Font aborted
        mock_route = AsyncMock()
        mock_req = MagicMock(resource_type="font", url="https://a0.muscache.com/fonts/airbnb-cereal.woff2")
        asyncio.run(PlatformComparator._filter_request_routes(mock_route, mock_req))
        mock_route.abort.assert_awaited_once()

        # 3. Tracker aborted
        mock_route = AsyncMock()
        mock_req = MagicMock(resource_type="script", url="https://www.google-analytics.com/analytics.js")
        asyncio.run(PlatformComparator._filter_request_routes(mock_route, mock_req))
        mock_route.abort.assert_awaited_once()

        # 4. API/Document continued
        mock_route = AsyncMock()
        mock_req = MagicMock(resource_type="fetch", url="https://www.airbnb.com/api/v3/StaysPdpSections")
        asyncio.run(PlatformComparator._filter_request_routes(mock_route, mock_req))
        mock_route.continue_.assert_awaited_once()

    def test_interval_comparison_and_price_breakdown_to_dict(self):
        """Verify to_dict returns a valid serializable dictionary with all expected keys."""
        pb = PriceBreakdown(platform="kivoya", available=False, notes="Calendar closed")
        d = pb.to_dict()
        self.assertIsInstance(d, dict)
        self.assertEqual(d["platform"], "kivoya")
        self.assertFalse(d["available"])

        comp = IntervalComparison(
            check_in="2027-06-03",
            check_out="2027-06-06",
            nights=3,
            segment_type="weekend",
            is_calendar_open=False,
            airbnb=pb,
            vrbo=pb,
            booking=pb,
            kivoya=pb,
            airbnb_est_no_dscnt=None,
            max_divergence_pct=None,
            notes="Closed",
        )
        comp_dict = comp.to_dict()
        self.assertIsInstance(comp_dict, dict)
        self.assertIsNone(comp_dict["airbnb_est_no_dscnt"])
        self.assertIsNone(comp_dict["max_divergence_pct"])
        self.assertEqual(comp_dict["check_in"], "2027-06-03")

    def test_closed_calendar_cache_write_and_read(self):
        """Verify compare_interval writes cache for closed calendar and reads back without error."""
        check_in = "2027-06-03"
        check_out = "2027-06-06"
        cache_path = self.comparator._get_cache_path(check_in, check_out)
        if cache_path.exists():
            cache_path.unlink()

        try:
            with patch.object(self.comparator, "init_browser", AsyncMock()) as mock_init:
                # 1. First call writes cache
                res1 = asyncio.run(self.comparator.compare_interval(
                    check_in=check_in,
                    check_out=check_out,
                    nights=3,
                    is_calendar_open=False,
                    use_cache=True,
                ))
                mock_init.assert_not_called()
                self.assertTrue(cache_path.exists())
                raw_json = json.loads(cache_path.read_text(encoding="utf-8"))
                self.assertFalse(raw_json["is_calendar_open"])
                self.assertIsNone(raw_json.get("airbnb_est_no_dscnt"))

                # 2. Second call reads cache
                res2 = asyncio.run(self.comparator.compare_interval(
                    check_in=check_in,
                    check_out=check_out,
                    nights=3,
                    is_calendar_open=False,
                    use_cache=True,
                ))
                mock_init.assert_not_called()
                self.assertFalse(res2.is_calendar_open)
                self.assertIsNone(res2.airbnb_est_no_dscnt)
                self.assertEqual(res2.notes, "Calendar closed in Kivoya PMS")
        finally:
            if cache_path.exists():
                cache_path.unlink()

    def test_shared_proxy_mgr_preserves_pool_on_close(self):
        """Verify PlatformComparator with shared proxy_mgr does not stop proxy pool on close_browser."""
        mock_mgr = MagicMock()
        mock_mgr.stop = AsyncMock()
        mock_mgr.endpoints = [MagicMock()]
        comparator = PlatformComparator(proxy_mgr=mock_mgr)
        self.assertFalse(comparator._owns_proxy_mgr)
        self.assertIs(comparator.proxy_mgr, mock_mgr)

        asyncio.run(comparator.close_browser())
        mock_mgr.stop.assert_not_called()

    def test_init_browser_reuses_running_proxy_endpoints(self):
        """Verify init_browser uses get_proxy_configs() when proxy_mgr already has active endpoints."""
        mock_mgr = MagicMock()
        mock_mgr.endpoints = [MagicMock()]
        mock_mgr.required = False
        mock_mgr.get_proxy_configs.return_value = [{"server": "http://127.0.0.1:56001"}]
        mock_mgr.start_pool = AsyncMock()

        comparator = PlatformComparator(parallel=True, proxy_mgr=mock_mgr)

        mock_playwright = MagicMock()
        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_context.on = MagicMock()
        mock_context.route = AsyncMock()
        mock_browser.new_context.return_value = mock_context
        mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)

        asyncio.run(comparator.init_browser(mock_playwright))
        mock_mgr.start_pool.assert_not_called()
        mock_mgr.get_proxy_configs.assert_called_once()
        self.assertEqual(len(comparator.worker_contexts), 1)
        asyncio.run(comparator.close_browser())


if __name__ == "__main__":
    unittest.main()

