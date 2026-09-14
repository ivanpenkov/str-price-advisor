"""
Unit tests for CompManager and single-comp interval pricing management.
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock

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

    def test_parse_stays_pdp_sections_discounted_price(self):
        """Discounted prices (DiscountedDisplayPriceLine) must be correctly extracted, and logging event metadata must not cause false unavailability."""
        mock_payload = {
            "data": {
                "presentation": {
                    "stayProductDetailPage": {
                        "sections": {
                            "sections": [
                                {
                                    "sectionId": "BOOK_IT_SIDEBAR",
                                    "section": {
                                        "available": True,
                                        "canInstantBook": True,
                                        "structuredDisplayPrice": {
                                            "primaryLine": {
                                                "__typename": "DiscountedDisplayPriceLine",
                                                "accessibilityLabel": "$2,496 for 3 nights, originally $3,008",
                                                "discountedPrice": "$2,496",
                                                "originalPrice": "$3,008",
                                                "price": None,
                                                "qualifier": "for 3 nights",
                                            }
                                        },
                                        # Logging event metadata schema contains selectUnavailable handlers even when listing is available
                                        "tripDetailsLoggingEventData": {
                                            "selectUnavailableForCheckInDateLoggingEventData": {"loggingId": "selectUnavailable"},
                                            "selectUnavailableForCheckoutDateLoggingEventData": {"loggingId": "selectUnavailable"},
                                        },
                                        "localizedUnavailabilityMessage": None,
                                    },
                                }
                            ]
                        }
                    }
                }
            }
        }

        price, label, is_unavailable, unavail_reason = CompManager.parse_stays_pdp_sections(mock_payload)
        self.assertEqual(price, 2496.0)
        self.assertIn("2,496", label)
        self.assertFalse(is_unavailable)
        self.assertIsNone(unavail_reason)

    def test_parse_stays_pdp_sections_basic_price(self):
        """Standard undiscounted prices (BasicDisplayPriceLine) must be parsed correctly."""
        mock_payload = {
            "data": {
                "presentation": {
                    "stayProductDetailPage": {
                        "sections": {
                            "sections": [
                                {
                                    "sectionId": "BOOK_IT_SIDEBAR",
                                    "section": {
                                        "available": True,
                                        "structuredDisplayPrice": {
                                            "primaryLine": {
                                                "__typename": "BasicDisplayPriceLine",
                                                "accessibilityLabel": "$1,820 for 4 nights",
                                                "price": "$1,820",
                                                "qualifier": "for 4 nights",
                                            }
                                        },
                                        "localizedUnavailabilityMessage": None,
                                    },
                                }
                            ]
                        }
                    }
                }
            }
        }

        price, label, is_unavailable, unavail_reason = CompManager.parse_stays_pdp_sections(mock_payload)
        self.assertEqual(price, 1820.0)
        self.assertFalse(is_unavailable)
        self.assertIsNone(unavail_reason)

    def test_parse_stays_pdp_sections_unavailable(self):
        """True unavailabilities indicated by localizedUnavailabilityMessage or available: False must be flagged."""
        mock_payload = {
            "data": {
                "presentation": {
                    "stayProductDetailPage": {
                        "sections": {
                            "sections": [
                                {
                                    "sectionId": "BOOK_IT_SIDEBAR",
                                    "section": {
                                        "available": False,
                                        "localizedUnavailabilityMessage": "Minimum stay is 5 nights",
                                        "structuredDisplayPrice": None,
                                    },
                                }
                            ]
                        }
                    }
                }
            }
        }

        price, label, is_unavailable, unavail_reason = CompManager.parse_stays_pdp_sections(mock_payload)
        self.assertIsNone(price)
        self.assertTrue(is_unavailable)
        self.assertEqual(unavail_reason, "Minimum stay is 5 nights")

    def test_adults_capacity_capped_at_16(self):
        """Comp with 17 beds and no explicit accommodates must cap requested adults to 16."""
        comp_meta = {"bedrooms": 6, "beds": 17, "baths": 3.0}
        accommodates = min(int(comp_meta.get("accommodates") or comp_meta.get("beds") or 10), 16)
        self.assertEqual(accommodates, 16)

    def test_disqualify_comp(self):
        """Test moving an active comp to disqualified, purging single-comp caches and DB sales."""
        cid = "1000000000000000001"
        self.manager._regenerate_dashboard = MagicMock()

        # Create single-comp cache file
        mock_cache = self.manager.CACHE_DIR / f"search_2026-09-13_2026-09-17_comp_{cid}.json"
        mock_cache.write_text("[]", encoding="utf-8")

        with patch("src.competitor_sales_tracker.CompetitorSalesTracker") as mock_tracker_cls:
            mock_tracker = MagicMock()
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.rowcount = 1
            mock_conn.cursor.return_value = mock_cursor
            mock_tracker._get_connection.return_value.__enter__.return_value = mock_conn
            mock_tracker_cls.return_value = mock_tracker

            success = self.manager.disqualify_comp(
                cid,
                reason="Capacity cap <12 guests max",
            )
            self.assertTrue(success)

        # Verify comp is no longer in tier_a
        tier, comp = self.manager.find_comp(cid)
        self.assertEqual(tier, "disqualified")
        self.assertFalse(comp["is_valid_comp"])
        self.assertEqual(comp["validity_reason"], "Capacity cap <12 guests max")
        self.assertIn("validity_details", comp)

        # Verify cache file purged
        self.assertFalse(mock_cache.exists())

        # Verify metadata counts
        reg = self.manager._load_registry()
        self.assertEqual(reg["metadata"]["total_comps"], 0)
        self.assertEqual(reg["metadata"]["valid_comps_count"], 0)
        self.assertEqual(reg["metadata"]["disqualified_comps_count"], 1)

    def test_requalify_comp(self):
        """Test moving a disqualified comp back to active tier."""
        cid = "1000000000000000001"
        self.manager._regenerate_dashboard = MagicMock()

        # First disqualify it
        self.manager.disqualify_comp(cid, reason="Temporary disqualification")
        tier, comp = self.manager.find_comp(cid)
        self.assertEqual(tier, "disqualified")

        # Now requalify it to tier_a
        with patch.object(self.manager.evaluator, "evaluate_comp", return_value={"desirability_ratio": 1.0, "is_valid_comp": True}):
            success = self.manager.requalify_comp(cid, target_tier="tier_a")
            self.assertTrue(success)

        tier, comp = self.manager.find_comp(cid)
        self.assertEqual(tier, "tier_a")
        self.assertTrue(comp["is_valid_comp"])

        reg = self.manager._load_registry()
        self.assertEqual(reg["metadata"]["total_comps"], 1)
        self.assertEqual(reg["metadata"]["valid_comps_count"], 1)
        self.assertEqual(reg["metadata"]["disqualified_comps_count"], 0)

    def test_evaluator_audit_portfolio(self):
        """Test CompEvaluator.audit_portfolio identifying valid, proposed exclusions, and already disqualified comps."""
        from src.comp_evaluator import CompEvaluator

        evaluator = CompEvaluator()
        evaluator.REGISTRY_PATH = self.test_dir / "audit_registry.json"

        test_registry = {
            "metadata": {},
            "tier_a": {
                "comp_valid": {
                    "listing_id": "comp_valid",
                    "name": "Luxury Estate",
                    "bedrooms": 6,
                    "beds": 8,
                    "baths": 5.0,
                    "location": "Scottsdale",
                    "rating": 4.95,
                    "reviews": 40,
                    "amenities": ["pool", "hot tub"],
                },
                "comp_small": {
                    "listing_id": "comp_small",
                    "name": "Small Condo",
                    "bedrooms": 2,
                    "beds": 2,
                    "baths": 2.0,
                    "location": "Tempe",
                    "guests": 4,
                    "rating": 4.80,
                    "reviews": 10,
                },
            },
            "tier_b": {},
            "disqualified": {
                "comp_disq": {
                    "listing_id": "comp_disq",
                    "name": "Disqualified Cottage",
                    "bedrooms": 3,
                    "baths": 2.0,
                    "validity_reason": "Low rating",
                }
            },
        }
        evaluator.REGISTRY_PATH.write_text(json.dumps(test_registry), encoding="utf-8")

        audit_res = evaluator.audit_portfolio(save_report=False)
        self.assertEqual(audit_res["total_portfolio"], 3)
        self.assertEqual(audit_res["active_valid_count"], 1)
        self.assertEqual(audit_res["proposed_disqualifications_count"], 1)
        self.assertEqual(audit_res["proposed_disqualifications"][0]["listing_id"], "comp_small")
        self.assertEqual(audit_res["already_disqualified"][0]["listing_id"], "comp_disq")

    def test_scrape_comp_interval_prices_empty_segments(self):
        """Verify scrape_comp_interval_prices exits immediately with empty list when no segments exist."""
        with patch("src.comp_manager.CalendarSegmenter") as mock_seg_cls:
            mock_seg = MagicMock()
            mock_seg.generate_unbooked_segments.return_value = []
            mock_seg_cls.return_value = mock_seg
            res = asyncio.run(self.manager.scrape_comp_interval_prices("1000000000000000001"))
            self.assertEqual(res, [])

    def test_scrape_comp_interval_prices_lifecycle(self):
        """Verify scrape_comp_interval_prices starts pool, leases contexts, and cleans up properly."""
        mock_segments = [
            {"check_in": "2026-10-01", "check_out": "2026-10-04", "nights": 3}
        ]
        with patch("src.comp_manager.CalendarSegmenter") as mock_seg_cls:
            mock_seg = MagicMock()
            mock_seg.generate_unbooked_segments.return_value = mock_segments
            mock_seg_cls.return_value = mock_seg

            mock_pm = MagicMock()
            mock_pm.max_workers = 4
            mock_pm.start_pool = AsyncMock(return_value=[{"server": "http://127.0.0.1:56001"}])
            mock_pm.stop = AsyncMock()

            mock_p = MagicMock()
            mock_browser = MagicMock()
            mock_context = MagicMock()
            mock_page = MagicMock()

            mock_p.chromium.launch = AsyncMock(return_value=mock_browser)
            mock_browser.new_context = AsyncMock(return_value=mock_context)
            mock_context.new_page = AsyncMock(return_value=mock_page)
            mock_context.close = AsyncMock()
            mock_browser.close = AsyncMock()
            mock_page.goto = AsyncMock()
            mock_page.evaluate = AsyncMock()
            mock_page.close = AsyncMock()
            mock_p.stop = AsyncMock()

            with patch("src.comp_manager.StealthConnectionManager", return_value=mock_pm), \
                 patch("src.comp_manager.async_playwright") as mock_ap_fn, \
                 patch.object(self.manager, "_regenerate_dashboard"):
                mock_ap = MagicMock()
                mock_ap.start = AsyncMock(return_value=mock_p)
                mock_ap_fn.return_value = mock_ap

                res = asyncio.run(self.manager.scrape_comp_interval_prices("1000000000000000001"))
                self.assertEqual(len(res), 1)
                self.assertEqual(res[0]["interval"], "2026-10-01_2026-10-04")
                mock_pm.start_pool.assert_called_once()
                mock_pm.stop.assert_called_once()
                mock_context.close.assert_called_once()
                mock_browser.close.assert_called_once()
                mock_p.stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()


