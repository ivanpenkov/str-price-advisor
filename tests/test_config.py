"""Unit tests for configuration loading and market threshold constants."""

import unittest
import tempfile
from pathlib import Path
import yaml

from src.config import (
    load_settings,
    get_settings,
    reload_settings,
    URGENT_PCT_DIFF,
    MODERATE_PCT_DIFF,
    URGENT_LEAD_DAYS,
    BASE_PERCENTILE,
    CLEANING_FEE,
)


class TestConfig(unittest.TestCase):

    def test_default_constants(self):
        """Default constants should reflect settings.yaml values."""
        self.assertEqual(URGENT_PCT_DIFF, 25.0)
        self.assertEqual(MODERATE_PCT_DIFF, 10.0)
        self.assertEqual(URGENT_LEAD_DAYS, 60)
        self.assertEqual(BASE_PERCENTILE, 65.0)
        self.assertEqual(CLEANING_FEE, 500.0)

    def test_load_settings(self):
        """load_settings should load dictionary correctly."""
        settings = load_settings()
        self.assertIn("property", settings)
        self.assertIn("strategy", settings)
        anomaly = settings["strategy"]["anomaly_thresholds"]
        self.assertEqual(anomaly["urgent_percent_diff"], 25.0)
        self.assertEqual(anomaly["moderate_percent_diff"], 10.0)

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
        finally:
            Path(temp_path).unlink(missing_ok=True)
            # Restore defaults
            reload_settings()
            from src import config
            self.assertEqual(config.URGENT_PCT_DIFF, 25.0)
            self.assertEqual(config.MODERATE_PCT_DIFF, 10.0)


if __name__ == "__main__":
    unittest.main()
