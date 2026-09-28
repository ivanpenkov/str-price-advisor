"""Unit tests for competitor sales 4-night minimum verification logic."""
import unittest
from datetime import date, timedelta
from unittest.mock import MagicMock, AsyncMock, patch

from scripts.audit_comp_sales_min_nights import compute_test_intervals, verify_comp_sale


class TestAuditCompSalesMinNights(unittest.IsolatedAsyncioTestCase):
    """Test suite for audit_comp_sales_min_nights pure logic and verification workflows."""

    def test_compute_test_intervals_3_nights(self):
        """A 3-night stay should extend 1 day before (Check 1) and 1 day after (Check 2)."""
        (cin_1, cout_1), (cin_2, cout_2) = compute_test_intervals("2026-10-26", "2026-10-29")
        # Original: Oct 26 to Oct 29 (3 nights)
        # Check 1: Oct 25 to Oct 29 (4 nights, covering Oct 25, 26, 27, 28)
        self.assertEqual(cin_1, "2026-10-25")
        self.assertEqual(cout_1, "2026-10-29")

        # Check 2: Oct 26 to Oct 30 (4 nights, covering Oct 26, 27, 28, 29)
        self.assertEqual(cin_2, "2026-10-26")
        self.assertEqual(cout_2, "2026-10-30")

    def test_compute_test_intervals_2_nights(self):
        """A 2-night stay should extend 2 days before (Check 1) and 2 days after (Check 2) to reach 4 nights."""
        (cin_1, cout_1), (cin_2, cout_2) = compute_test_intervals("2026-10-26", "2026-10-28")
        # Original: Oct 26 to Oct 28 (2 nights)
        # delta_days = max(1, 4 - 2) = 2
        # Check 1: Oct 24 to Oct 28 (4 nights)
        self.assertEqual(cin_1, "2026-10-24")
        self.assertEqual(cout_1, "2026-10-28")

        # Check 2: Oct 26 to Oct 30 (4 nights)
        self.assertEqual(cin_2, "2026-10-26")
        self.assertEqual(cout_2, "2026-10-30")

    def test_compute_test_intervals_1_night(self):
        """A 1-night stay should extend 3 days before (Check 1) and 3 days after (Check 2) to reach 4 nights."""
        (cin_1, cout_1), (cin_2, cout_2) = compute_test_intervals("2026-10-26", "2026-10-27")
        # delta_days = max(1, 4 - 1) = 3
        self.assertEqual(cin_1, "2026-10-23")
        self.assertEqual(cout_1, "2026-10-27")
        self.assertEqual(cin_2, "2026-10-26")
        self.assertEqual(cout_2, "2026-10-30")

    async def test_verify_comp_sale_short_circuits_nights_gte_4(self):
        """Stays with >= 4 nights already meet the 4-night minimum and must be confirmed real immediately."""
        sale = {
            "id": 101,
            "listing_id": "12345678",
            "listing_name": "Desert Oasis",
            "check_in": "2026-11-05",
            "check_out": "2026-11-09",
            "nights": 4,
            "detected_date": "2026-09-20",
            "last_observed_rate": 550.0,
        }
        mock_worker_mgr = MagicMock()
        res = await verify_comp_sale(mock_worker_mgr, sale)

        self.assertEqual(res["verdict"], "CONFIRMED_REAL")
        self.assertIn("already satisfies a 4-night minimum", res["verdict_reason"])
        mock_worker_mgr.lease_worker.assert_not_called()

    async def test_verify_comp_sale_defensive_nights_none_calculates_from_dates(self):
        """If nights is None, it should calculate from check_in and check_out without TypeError."""
        sale = {
            "id": 105,
            "listing_id": "12345678",
            "listing_name": "Desert Oasis",
            "check_in": "2026-11-05",
            "check_out": "2026-11-09",
            "nights": None,
            "detected_date": "2026-09-20",
            "last_observed_rate": 550.0,
        }
        mock_worker_mgr = MagicMock()
        res = await verify_comp_sale(mock_worker_mgr, sale)
        self.assertEqual(res["verdict"], "CONFIRMED_REAL")
        self.assertEqual(res["original_nights"], 4)

    @patch("scripts.audit_comp_sales_min_nights.check_single_interval")
    @patch("scripts.audit_comp_sales_min_nights.async_playwright")
    async def test_verify_comp_sale_false_sale_on_check_1(self, mock_playwright_fn, mock_check_single):
        """If Check 1 is available with a price, the stay is a FALSE_SALE."""
        sale = {
            "id": 102,
            "listing_id": "12345678",
            "listing_name": "Desert Oasis",
            "check_in": "2027-01-10",
            "check_out": "2027-01-13",
            "nights": 3,
            "detected_date": "2026-09-20",
            "last_observed_rate": 550.0,
        }

        mock_p = MagicMock()
        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()
        mock_p.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_browser.close = AsyncMock()
        mock_page.close = AsyncMock()
        mock_playwright_ctx = MagicMock()
        mock_playwright_ctx.__aenter__ = AsyncMock(return_value=mock_p)
        mock_playwright_ctx.__aexit__ = AsyncMock()
        mock_playwright_fn.return_value = mock_playwright_ctx

        mock_worker = MagicMock()
        mock_worker.url = "http://fake-proxy:8080"
        mock_worker_mgr = MagicMock()
        mock_lease_ctx = MagicMock()
        mock_lease_ctx.__aenter__ = AsyncMock(return_value=mock_worker)
        mock_lease_ctx.__aexit__ = AsyncMock()
        mock_worker_mgr.lease_worker.return_value = mock_lease_ctx

        mock_check_single.return_value = {
            "available": True,
            "unavail": False,
            "price": 2000.0,
            "reason": "Available",
        }

        res = await verify_comp_sale(mock_worker_mgr, sale)
        self.assertEqual(res["verdict"], "FALSE_SALE")
        self.assertIn("Check 1", res["verdict_reason"])
        self.assertEqual(res["check1_result"]["price"], 2000.0)

    @patch("scripts.audit_comp_sales_min_nights.check_single_interval")
    @patch("scripts.audit_comp_sales_min_nights.async_playwright")
    async def test_verify_comp_sale_false_sale_on_check_2(self, mock_playwright_fn, mock_check_single):
        """If Check 1 is unavailable but Check 2 is available with price, stay is a FALSE_SALE."""
        sale = {
            "id": 106,
            "listing_id": "12345678",
            "listing_name": "Desert Oasis",
            "check_in": "2027-01-10",
            "check_out": "2027-01-13",
            "nights": 3,
            "detected_date": "2026-09-20",
            "last_observed_rate": 550.0,
        }

        mock_p = MagicMock()
        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()
        mock_p.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_browser.close = AsyncMock()
        mock_page.close = AsyncMock()
        mock_playwright_ctx = MagicMock()
        mock_playwright_ctx.__aenter__ = AsyncMock(return_value=mock_p)
        mock_playwright_ctx.__aexit__ = AsyncMock()
        mock_playwright_fn.return_value = mock_playwright_ctx

        mock_worker = MagicMock()
        mock_worker.url = "http://fake-proxy:8080"
        mock_worker_mgr = MagicMock()
        mock_lease_ctx = MagicMock()
        mock_lease_ctx.__aenter__ = AsyncMock(return_value=mock_worker)
        mock_lease_ctx.__aexit__ = AsyncMock()
        mock_worker_mgr.lease_worker.return_value = mock_lease_ctx

        # Check 1 unavailable, Check 2 available
        mock_check_single.side_effect = [
            {"available": False, "unavail": True, "price": None, "reason": "Dates unavailable"},
            {"available": True, "unavail": False, "price": 2400.0, "reason": "Available"},
        ]

        res = await verify_comp_sale(mock_worker_mgr, sale)
        self.assertEqual(res["verdict"], "FALSE_SALE")
        self.assertIn("Check 2", res["verdict_reason"])
        self.assertEqual(res["check2_result"]["price"], 2400.0)

    @patch("scripts.audit_comp_sales_min_nights.check_single_interval")
    @patch("scripts.audit_comp_sales_min_nights.async_playwright")
    async def test_verify_comp_sale_stricter_min_stay(self, mock_playwright_fn, mock_check_single):
        """If PDP detects a 5+ night minimum stay notice, verdict should be STRICTER_MIN_STAY."""
        sale = {
            "id": 107,
            "listing_id": "12345678",
            "listing_name": "Desert Oasis",
            "check_in": "2027-01-10",
            "check_out": "2027-01-13",
            "nights": 3,
            "detected_date": "2026-09-20",
            "last_observed_rate": 550.0,
        }

        mock_p = MagicMock()
        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()
        mock_p.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_browser.close = AsyncMock()
        mock_page.close = AsyncMock()
        mock_playwright_ctx = MagicMock()
        mock_playwright_ctx.__aenter__ = AsyncMock(return_value=mock_p)
        mock_playwright_ctx.__aexit__ = AsyncMock()
        mock_playwright_fn.return_value = mock_playwright_ctx

        mock_worker = MagicMock()
        mock_worker.url = "http://fake-proxy:8080"
        mock_worker_mgr = MagicMock()
        mock_lease_ctx = MagicMock()
        mock_lease_ctx.__aenter__ = AsyncMock(return_value=mock_worker)
        mock_lease_ctx.__aexit__ = AsyncMock()
        mock_worker_mgr.lease_worker.return_value = mock_lease_ctx

        mock_check_single.side_effect = [
            {"available": False, "unavail": True, "price": None, "reason": "Requires 5-night minimum stay"},
            {"available": False, "unavail": True, "price": None, "reason": "Requires 5-night minimum stay"},
        ]

        res = await verify_comp_sale(mock_worker_mgr, sale)
        self.assertEqual(res["verdict"], "STRICTER_MIN_STAY")
        self.assertIn("stricter minimum stay rule", res["verdict_reason"])

    @patch("scripts.audit_comp_sales_min_nights.check_single_interval")
    @patch("scripts.audit_comp_sales_min_nights.async_playwright")
    async def test_verify_comp_sale_past_check1_date_is_skipped(self, mock_playwright_fn, mock_check_single):
        """If Check 1 check-in date is in the past, it should be skipped without querying Airbnb."""
        today = date.today()
        sale = {
            "id": 104,
            "listing_id": "12345678",
            "listing_name": "Desert Oasis",
            "check_in": today.isoformat(),
            "check_out": (today + timedelta(days=3)).isoformat(),
            "nights": 3,
            "detected_date": "2026-09-20",
            "last_observed_rate": 550.0,
        }

        mock_p = MagicMock()
        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()
        mock_p.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_browser.close = AsyncMock()
        mock_page.close = AsyncMock()
        mock_playwright_ctx = MagicMock()
        mock_playwright_ctx.__aenter__ = AsyncMock(return_value=mock_p)
        mock_playwright_ctx.__aexit__ = AsyncMock()
        mock_playwright_fn.return_value = mock_playwright_ctx

        mock_worker = MagicMock()
        mock_worker.url = "http://fake-proxy:8080"
        mock_worker_mgr = MagicMock()
        mock_lease_ctx = MagicMock()
        mock_lease_ctx.__aenter__ = AsyncMock(return_value=mock_worker)
        mock_lease_ctx.__aexit__ = AsyncMock()
        mock_worker_mgr.lease_worker.return_value = mock_lease_ctx

        # Check 2 returns unavailable
        mock_check_single.return_value = {
            "available": False,
            "unavail": True,
            "price": None,
            "reason": "Dates unavailable",
        }

        res = await verify_comp_sale(mock_worker_mgr, sale)
        self.assertTrue(res["check1_result"].get("skipped"))
        self.assertEqual(res["verdict"], "CONFIRMED_REAL")
        self.assertEqual(mock_check_single.call_count, 1)


if __name__ == "__main__":
    unittest.main()
