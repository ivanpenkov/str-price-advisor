"""Unit tests for configuration loading and market threshold constants."""

import unittest
import tempfile
from pathlib import Path
import yaml

from src.config import (
    load_settings,
    load_fallback_intervals,
    get_settings,
    reload_settings,
    URGENT_PCT_DIFF,
    MODERATE_PCT_DIFF,
    URGENT_LEAD_DAYS,
    BASE_PERCENTILE,
    CLEANING_FEE,
    COMP_PRICE_WEIGHT,
    HISTORICAL_PRICE_WEIGHT,
    WEEKEND_PREMIUM_FACTOR,
    FALLBACK_INTERVALS,
)


class TestConfig(unittest.TestCase):

    def test_default_constants(self):
        """Default constants should reflect settings.yaml values."""
        self.assertEqual(URGENT_PCT_DIFF, 25.0)
        self.assertEqual(MODERATE_PCT_DIFF, 10.0)
        self.assertEqual(URGENT_LEAD_DAYS, 60)
        self.assertEqual(BASE_PERCENTILE, 65.0)
        self.assertEqual(CLEANING_FEE, 500.0)
        self.assertEqual(COMP_PRICE_WEIGHT, 0.67)
        self.assertEqual(HISTORICAL_PRICE_WEIGHT, 0.33)
        self.assertEqual(WEEKEND_PREMIUM_FACTOR, 1.50)

    def test_load_settings(self):
        """load_settings should load dictionary correctly."""
        settings = load_settings()
        self.assertIn("property", settings)
        self.assertIn("strategy", settings)
        anomaly = settings["strategy"]["anomaly_thresholds"]
        self.assertEqual(anomaly["urgent_percent_diff"], 25.0)
        self.assertEqual(anomaly["moderate_percent_diff"], 10.0)

    def test_load_fallback_intervals(self):
        """load_fallback_intervals should parse monthly defaults and all recognized holidays."""
        fallback = load_fallback_intervals()
        self.assertIn("monthly_defaults", fallback)
        self.assertIn("summer", fallback["monthly_defaults"])
        self.assertIn("fall_shoulder", fallback["monthly_defaults"])
        self.assertIn("winter_spring", fallback["monthly_defaults"])

        self.assertIn("holidays", fallback)
        holidays = fallback["holidays"]
        hol_names = {h["name"] for h in holidays}
        expected_holidays = {
            "4th of July",
            "Labor Day",
            "Columbus Day",
            "Thanksgiving",
            "Christmas & New Year",
            "Holy Week",
            "Memorial Day",
        }
        self.assertTrue(expected_holidays.issubset(hol_names))

    def test_reload_settings_custom_file(self):
        """reload_settings should update constants from a modified settings file."""
        custom_data = {
            "property": {"name": "Test Villa", "cleaning_fee": 450.0},
            "strategy": {
                "base_percentile": 70.0,
                "anomaly_thresholds": {
                    "urgent_percent_diff": 30.0,
                    "moderate_percent_diff": 15.0,
                    "urgent_lead_days": 45,
                },
                "proposed_pricing": {
                    "comp_weight": 0.75,
                    "historical_weight": 0.25,
                    "weekend_premium_factor": 1.60,
                },
            },
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(custom_data, f)
            temp_path = f.name

        try:
            reloaded = reload_settings(temp_path)
            self.assertEqual(reloaded["strategy"]["anomaly_thresholds"]["urgent_percent_diff"], 30.0)
            from src import config
            self.assertEqual(config.URGENT_PCT_DIFF, 30.0)
            self.assertEqual(config.MODERATE_PCT_DIFF, 15.0)
            self.assertEqual(config.URGENT_LEAD_DAYS, 45)
            self.assertEqual(config.BASE_PERCENTILE, 70.0)
            self.assertEqual(config.CLEANING_FEE, 450.0)
            self.assertEqual(config.COMP_PRICE_WEIGHT, 0.75)
            self.assertEqual(config.HISTORICAL_PRICE_WEIGHT, 0.25)
            self.assertEqual(config.WEEKEND_PREMIUM_FACTOR, 1.60)
        finally:
            Path(temp_path).unlink(missing_ok=True)
            # Restore defaults
            reload_settings()
            from src import config
            self.assertEqual(config.URGENT_PCT_DIFF, 25.0)
            self.assertEqual(config.MODERATE_PCT_DIFF, 10.0)
            self.assertEqual(config.COMP_PRICE_WEIGHT, 0.67)
            self.assertEqual(config.HISTORICAL_PRICE_WEIGHT, 0.33)
            self.assertEqual(config.WEEKEND_PREMIUM_FACTOR, 1.50)


if __name__ == "__main__":
    unittest.main()
