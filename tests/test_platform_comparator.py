"""
Unit tests for PlatformComparator: cross-channel price comparison, divergence thresholds,
TSV export, and fee derivation formulas.
"""

import unittest
from unittest.mock import patch, MagicMock
from datetime import date
from pathlib import Path
import json

from src.platform_comparator import PlatformComparator, PriceBreakdown, IntervalComparison


class TestPlatformComparator(unittest.TestCase):

    def setUp(self):
        self.comparator = PlatformComparator()

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
        """Kivoya direct rates should include base + $500 clean + 14.4% tax with 0% OTA fee."""
        with patch.object(self.comparator.kivoya_client, "get_seasonal_rates", return_value=[]), \
             patch.object(self.comparator.kivoya_client, "get_rate_for_date", return_value=500.0):
            res = self.comparator.get_kivoya_quote(date(2026, 10, 1), date(2026, 10, 4), nights=3)
            self.assertEqual(res.base_subtotal, 1500.0)
            self.assertEqual(res.nightly_rate, 500.0)
            self.assertEqual(res.cleaning_fee, 500.0)
            self.assertEqual(res.service_fee, 0.0)
            # Subtotal before tax = $2000; Tax 14.4% = $288.00; Total = $2288.00
            self.assertAlmostEqual(res.taxes, 288.0, places=2)
            self.assertAlmostEqual(res.total_price, 2288.0, places=2)
            self.assertAlmostEqual(res.effective_nightly, 2288.0 / 3, places=2)

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


if __name__ == "__main__":
    unittest.main()
