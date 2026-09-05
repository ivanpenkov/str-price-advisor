"""
Unit tests for CompManager and single-comp interval pricing management.
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from src.comp_manager import CompManager, extract_listing_id
from src.html_generator import HTMLDashboardGenerator


class TestCompManagement(unittest.TestCase):

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.manager = CompManager()
        self.manager.REGISTRY_PATH = self.test_dir / "comps_registry.json"
        self.manager.SPECS_PATH = self.test_dir / "listing_specs.json"
        self.manager.CACHE_DIR = self.test_dir / "cache"
        self.manager.ENRICHED_DIR = self.test_dir / "enriched_comps"
        self.manager.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self.manager.ENRICHED_DIR.mkdir(parents=True, exist_ok=True)

        initial_registry = {
            "metadata": {"total_comps": 1},
            "tier_a": {
                "1000000000000000001": {
                    "listing_id": "1000000000000000001",
                    "name": "Initial Estate",
                    "bedrooms": 6,
                    "beds": 10,
                    "baths": 5.0,
                    "desirability_ratio": 1.0,
                }
            },
            "tier_b": {},
            "disqualified": {},
        }
        self.manager._save_registry(initial_registry)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_extract_listing_id(self):
        """Test URL parsing and numeric extraction."""
        # Simple ID
        self.assertEqual(extract_listing_id("1493069124077219890"), "1493069124077219890")
        # Standard URL
        self.assertEqual(
            extract_listing_id("https://www.airbnb.com/rooms/1493069124077219890"),
            "1493069124077219890",
        )
        # URL with query parameters
        self.assertEqual(
            extract_listing_id("https://www.airbnb.com/rooms/1493069124077219890?check_in=2026-10-15&check_out=2026-10-18&adults=14"),
            "1493069124077219890",
        )
        # ID with whitespace
        self.assertEqual(extract_listing_id("  1493069124077219890  "), "1493069124077219890")
        # Invalid input
        with self.assertRaises(ValueError):
            extract_listing_id("")
        with self.assertRaises(ValueError):
            extract_listing_id("https://www.airbnb.com/about")

    def test_find_comp(self):
        """Test looking up an existing comp."""
        tier, comp = self.manager.find_comp("1000000000000000001")
        self.assertEqual(tier, "tier_a")
        self.assertEqual(comp["name"], "Initial Estate")

        tier_none, comp_none = self.manager.find_comp("9999999999999999999")
        self.assertIsNone(tier_none)
        self.assertIsNone(comp_none)

    def test_remove_comp(self):
        """Test removing a comp and purging its cache artifacts."""
        cid = "1000000000000000001"

        # Create mock single-comp cache file and enriched file
        mock_cache = self.manager.CACHE_DIR / f"search_2026-09-13_2026-09-17_comp_{cid}.json"
        mock_cache.write_text("[]", encoding="utf-8")
        mock_enriched = self.manager.ENRICHED_DIR / f"{cid}.json"
        mock_enriched.write_text("{}", encoding="utf-8")

        # Mock dashboard regeneration
        self.manager._regenerate_dashboard = MagicMock()

        success = self.manager.remove_comp(cid)
        self.assertTrue(success)

        # Verify unregister
        tier, comp = self.manager.find_comp(cid)
        self.assertIsNone(tier)
        self.assertIsNone(comp)

        # Verify artifacts purged
        self.assertFalse(mock_cache.exists())
        self.assertFalse(mock_enriched.exists())

        # Test removing non-existent comp returns False
        self.assertFalse(self.manager.remove_comp("9999999999999999999"))

    def test_single_comp_cache_merging_into_html_generator(self):
        """Test that single-comp cache files seamlessly merge into HTML dashboard generator cache."""
        temp_cache_dir = self.test_dir / "cache"
        temp_cache_dir.mkdir(parents=True, exist_ok=True)

        # Create general cohort search file
        general_search = temp_cache_dir / "search_2026-09-13_2026-09-17_Scottsdale--AZ_tier_a_1234567890.json"
        general_search.write_text(json.dumps([
            {
                "listing_id": "111111",
                "title": "General Comp",
                "effective_nightly": 600.0,
                "nights": 4,
            }
        ]), encoding="utf-8")

        # Create single-comp sweep file
        single_comp_file = temp_cache_dir / "search_2026-09-13_2026-09-17_comp_1493069124077219890.json"
        single_comp_file.write_text(json.dumps([
            {
                "listing_id": "1493069124077219890",
                "title": "The Desert Diamond",
                "effective_nightly": 548.75,
                "nights": 4,
            }
        ]), encoding="utf-8")

        html_gen = HTMLDashboardGenerator()
        with patch("pathlib.Path.glob") as mock_glob:
            mock_glob.return_value = [general_search, single_comp_file]
            cached = html_gen._load_cached_comps_by_key()

        key = "2026-09-13_2026-09-17"
        self.assertIn(key, cached)
        self.assertIn("111111", cached[key])
        self.assertIn("1493069124077219890", cached[key])
        self.assertEqual(cached[key]["1493069124077219890"]["effective_nightly"], 548.75)


if __name__ == "__main__":
    unittest.main()

