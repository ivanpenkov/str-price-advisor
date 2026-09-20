"""
Unit Tests for Ratings CLI Interface (show-ratings, sync-ratings, audit-reviews).
Tests mobile formatting, JSON export, zero-recent fallback, and length constraints.
Complies with Fast Development and Zero-Sleep Test Environment Invariants.
"""

from datetime import date, timedelta
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from src.cli import print_ratings_summary, print_reviews_audit, run_sync_ratings


class TestCLIRatings(unittest.TestCase):

    def test_show_ratings_mobile_formatting_with_recent(self):
        """Mobile push format contains scorecards, up to 3 recent reviews, and obeys 3800 char limit."""
        f = io.StringIO()
        with patch("sys.stdout", f):
            out = print_ratings_summary(mobile=True)

        self.assertIn("⭐ Villa del Sol — Ratings & Recent Reviews", out)
        self.assertIn("Airbnb", out)
        self.assertIn("4.83 / 5.0", out)
        self.assertIn("Booking.com", out)
        self.assertIn("9.0 / 10.0", out)
        self.assertIn("VRBO", out)
        self.assertIn("Recent Reviews in Last 30 Days", out)
        self.assertLessEqual(len(out), 3800)

    def test_show_ratings_mobile_zero_recent_fallback(self):
        """When 0 reviews exist in the 30-day window, mobile format falls back to the most recent review."""
        mock_data = {
            "last_updated": "2026-09-14T10:00:00Z",
            "recent_window_days": 30,
            "recent_reviews_count": 0,
            "platforms": {
                "airbnb": {"display_name": "Airbnb", "scale": "5.0", "rating": 4.96, "review_count": 76},
                "vrbo": {"display_name": "VRBO", "scale": "10.0", "rating": 9.6, "review_count": 34},
                "booking": {"display_name": "Booking.com", "scale": "10.0", "rating": 9.4, "review_count": 18},
            },
            "reviews": [
                {
                    "id": "old1",
                    "platform": "airbnb",
                    "reviewer_name": "James T.",
                    "date": "2026-05-12",
                    "rating": 5.0,
                    "rating_max": 5.0,
                    "body": "Outstanding stay! The heated pool and outdoor gazebo were magnificent.",
                    "host_response": None,
                    "is_recent": False,
                }
            ],
        }

        with patch("src.ratings_collector.RatingsCollector.load_database", return_value=mock_data):
            f = io.StringIO()
            with patch("sys.stdout", f):
                out = print_ratings_summary(mobile=True)

            self.assertIn("No new reviews in the last 30 days.", out)
            self.assertIn("Most Recent Review on Record:", out)
            self.assertIn("James T.", out)
            self.assertIn("5.0", out)
            self.assertLessEqual(len(out), 3800)

    def test_show_ratings_json(self):
        """--json flag emits valid, parseable JSON dictionary."""
        f = io.StringIO()
        with patch("sys.stdout", f):
            print_ratings_summary(as_json=True)

        output_str = f.getvalue()
        parsed = json.loads(output_str)
        self.assertIn("platforms", parsed)
        self.assertIn("reviews", parsed)
        self.assertEqual(parsed["platforms"]["airbnb"]["rating"], 4.83)

    def test_show_ratings_terminal(self):
        """Default invocation prints structured terminal overview."""
        f = io.StringIO()
        with patch("sys.stdout", f):
            print_ratings_summary(mobile=False, as_json=False)

        output_str = f.getvalue()
        self.assertIn("Cross-Platform Ratings Summary", output_str)
        self.assertIn("Airbnb", output_str)
        self.assertIn("Booking.com", output_str)

    def test_audit_reviews_viewer(self):
        """audit-reviews displays content of data/reviews_analysis.md."""
        f = io.StringIO()
        with patch("sys.stdout", f):
            print_reviews_audit()

        out = f.getvalue()
        self.assertIn("Villa del Sol — Guest Reviews Operational Audit", out)
        self.assertIn("Executive Summary", out)

    def test_audit_reviews_missing_file_warning(self):
        """audit-reviews prints warning if report has not been generated."""
        with patch("pathlib.Path.exists", return_value=False):
            f = io.StringIO()
            with patch("sys.stdout", f):
                print_reviews_audit()
            out = f.getvalue()
            self.assertIn("No review analysis report found", out)

    def test_sync_ratings_cli_execution(self):
        """sync-ratings CLI subcommand executes and respects --no-dashboard flag."""
        from unittest.mock import AsyncMock

        mock_args = MagicMock()
        mock_args.platform = "vrbo"
        mock_args.headless = True
        mock_args.force = False
        mock_args.backfill = False
        mock_args.dry_run = False
        mock_args.no_dashboard = True

        mock_summary = {
            "channels": {
                "vrbo": {"status": "ok", "rating": 9.6, "review_count": 24, "new_reviews_added": 0}
            },
            "successful": 1,
            "total_new_reviews": 0,
        }

        f = io.StringIO()
        with patch("src.stealth_connection.StealthConnectionManager.start_pool", new_callable=AsyncMock), \
             patch("src.stealth_connection.StealthConnectionManager.stop_pool", new_callable=AsyncMock), \
             patch("src.ratings_collector.RatingsCollector.sync_all", new_callable=AsyncMock, return_value=mock_summary), \
             patch("src.ratings_collector.RatingsCollector.save_database"), \
             patch("src.html_generator.HTMLDashboardGenerator.generate") as mock_html_gen, \
             patch("sys.stdout", f):
            run_sync_ratings(mock_args)

        out = f.getvalue()
        self.assertIn("Synchronizing ratings and reviews for: vrbo", out)
        self.assertIn("Sync complete", out)
        mock_html_gen.assert_not_called()

    def test_sync_ratings_cli_dry_run(self):
        """sync-ratings CLI --dry-run prints rich preview and skips HTML dashboard regeneration."""
        from unittest.mock import AsyncMock

        mock_args = MagicMock()
        mock_args.platform = "airbnb"
        mock_args.headless = True
        mock_args.force = False
        mock_args.backfill = False
        mock_args.dry_run = True
        mock_args.no_dashboard = False

        mock_summary = {
            "channels": {
                "airbnb": {"status": "ok", "rating": 4.83, "review_count": 77, "new_reviews_added": 2}
            },
            "successful": 1,
            "total_new_reviews": 2,
        }

        with patch("src.stealth_connection.StealthConnectionManager.start_pool", new_callable=AsyncMock) as mock_start, \
             patch("src.stealth_connection.StealthConnectionManager.stop_pool", new_callable=AsyncMock) as mock_stop, \
             patch("src.ratings_collector.RatingsCollector.sync_all", new_callable=AsyncMock, return_value=mock_summary), \
             patch("src.html_generator.HTMLDashboardGenerator.generate") as mock_html_gen:

            f = io.StringIO()
            with patch("sys.stdout", f):
                run_sync_ratings(mock_args)

            out = f.getvalue()
            self.assertIn("DRY RUN: Previewed extracted metrics", out)
            self.assertIn("Airbnb", out)
            self.assertIn("Rating=4.83", out)
            mock_html_gen.assert_not_called()

    def test_sync_ratings_cli_proxy_lifecycle(self):
        """sync-ratings CLI manages proxy leasing and clean teardown in finally block."""
        from unittest.mock import AsyncMock

        mock_args = MagicMock()
        mock_args.platform = "all"
        mock_args.headless = True
        mock_args.force = False
        mock_args.backfill = False
        mock_args.dry_run = False
        mock_args.no_dashboard = True

        mock_summary = {
            "channels": {
                "airbnb": {"status": "ok", "rating": 4.83, "review_count": 77, "new_reviews_added": 0}
            },
            "successful": 1,
            "total_new_reviews": 0,
        }

        with patch("src.stealth_connection.StealthConnectionManager.start_pool", new_callable=AsyncMock) as mock_start, \
             patch("src.stealth_connection.StealthConnectionManager.stop_pool", new_callable=AsyncMock) as mock_stop, \
             patch("src.stealth_connection.StealthConnectionManager.is_configured", True), \
             patch("src.ratings_collector.RatingsCollector.sync_all", new_callable=AsyncMock, return_value=mock_summary):

            f = io.StringIO()
            with patch("sys.stdout", f):
                run_sync_ratings(mock_args)

            out = f.getvalue()
            mock_start.assert_called_once()
            mock_stop.assert_called_once()
            self.assertIn("Sync complete", out)

    def test_sync_comp_ratings_dry_run_and_harvest(self):
        """sync_comp_ratings dry run previews updates from search sweeps without modifying disk."""
        import asyncio
        from src.cli import sync_comp_ratings

        mock_registry = {
            "tier_a": {
                "comp123": {
                    "listing_id": "comp123",
                    "name": "Luxury Estate",
                    "location": "Scottsdale",
                    "bedrooms": 6,
                    "beds": 8,
                    "baths": 5.0,
                    "rating": 4.80,
                    "reviews": 10,
                    "desirability_ratio": 1.00,
                }
            }
        }
        mock_specs = {
            "comp123": {"listing_id": "comp123", "rating": 4.80, "reviews": 10}
        }
        mock_pricing = {
            "urgent_intervals": [
                {
                    "comps_list": [
                        {
                            "listing_id": "comp123",
                            "rating": 4.95,
                            "reviews": 25,
                            "is_guest_favorite": True,
                            "raw_snippet": "Guest favorite · 4.95 (25)",
                        }
                    ]
                }
            ]
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            cfg_dir = tmp_path / "config"
            cfg_dir.mkdir()
            data_dir = tmp_path / "data"
            data_dir.mkdir()

            reg_file = cfg_dir / "comps_registry.json"
            reg_file.write_text(json.dumps(mock_registry), encoding="utf-8")
            specs_file = cfg_dir / "listing_specs.json"
            specs_file.write_text(json.dumps(mock_specs), encoding="utf-8")

            pricing_file = data_dir / "pricing_data_2026-09-14.json"
            pricing_file.write_text(json.dumps(mock_pricing), encoding="utf-8")

            with patch("src.cli.Path") as mock_path_cls:
                def fake_path(arg):
                    if "comps_registry.json" in str(arg):
                        return reg_file
                    if "listing_specs.json" in str(arg):
                        return specs_file
                    if str(arg) == "data":
                        return data_dir
                    return Path(arg)
                mock_path_cls.side_effect = fake_path

                res = asyncio.run(sync_comp_ratings(tier="tier_a", dry_run=True))
                self.assertEqual(res["total_comps"], 1)
                self.assertEqual(res["updated_comps"], 1)
                self.assertEqual(res["guest_favorites_count"], 1)

                # Verify file was not modified in dry_run
                saved_reg = json.loads(reg_file.read_text())
                self.assertEqual(saved_reg["tier_a"]["comp123"]["rating"], 4.80)

    def test_sync_comp_ratings_persistence_and_ratio_boost(self):
        """sync_comp_ratings non-dry-run persists updates and awards +7 reputation bonus to ratio."""
        import asyncio
        from src.cli import sync_comp_ratings

        mock_registry = {
            "tier_a": {
                "comp123": {
                    "listing_id": "comp123",
                    "name": "Luxury Estate",
                    "location": "Scottsdale",
                    "bedrooms": 6,
                    "beds": 8,
                    "baths": 5.0,
                    "rating": 4.80,
                    "reviews": 10,
                    "desirability_ratio": 1.00,
                }
            }
        }
        mock_specs = {
            "comp123": {"listing_id": "comp123", "rating": 4.80, "reviews": 10}
        }
        mock_pricing = {
            "urgent_intervals": [
                {
                    "comps_list": [
                        {
                            "listing_id": "comp123",
                            "rating": 4.95,
                            "reviews": 25,
                            "is_guest_favorite": True,
                            "raw_snippet": "Guest favorite · 4.95 (25)",
                        }
                    ]
                }
            ]
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            cfg_dir = tmp_path / "config"
            cfg_dir.mkdir()
            data_dir = tmp_path / "data"
            data_dir.mkdir()

            reg_file = cfg_dir / "comps_registry.json"
            reg_file.write_text(json.dumps(mock_registry), encoding="utf-8")
            specs_file = cfg_dir / "listing_specs.json"
            specs_file.write_text(json.dumps(mock_specs), encoding="utf-8")

            pricing_file = data_dir / "pricing_data_2026-09-14.json"
            pricing_file.write_text(json.dumps(mock_pricing), encoding="utf-8")

            enriched_dir = data_dir / "enriched_comps"
            enriched_dir.mkdir()
            (enriched_dir / "comp123.json").write_text(json.dumps({
                "listing_id": "comp123",
                "title": "Luxury Estate",
                "description": "6BR luxury compound with heated pool, hot tub, outdoor kitchen, and private sports court.",
                "amenities": ["Pool", "Heated pool", "Hot tub", "Wifi", "Kitchen", "Air conditioning", "BBQ grill", "Fire pit"],
                "bedrooms": 6,
                "beds": 8,
                "baths": 5.0,
            }), encoding="utf-8")

            with patch("src.cli.Path") as mock_path_cls:
                def fake_path(arg):
                    if "comps_registry.json" in str(arg):
                        return reg_file
                    if "listing_specs.json" in str(arg):
                        return specs_file
                    if "enriched_comps" in str(arg):
                        return enriched_dir
                    if str(arg) == "data":
                        return data_dir
                    return Path(arg)
                mock_path_cls.side_effect = fake_path

                res = asyncio.run(sync_comp_ratings(tier="tier_a", dry_run=False))
                self.assertEqual(res["updated_comps"], 1)

                # Verify file was updated on disk
                saved_reg = json.loads(reg_file.read_text())
                c_data = saved_reg["tier_a"]["comp123"]
                self.assertEqual(c_data["rating"], 4.95)
                self.assertEqual(c_data["reviews"], 25)
                self.assertTrue(c_data["is_guest_favorite"])
                self.assertEqual(c_data["guest_favorite_badge"], "Guest Favorite")
                self.assertGreater(c_data["desirability_ratio"], 1.00)

                # Check listing_specs.json was updated
                saved_specs = json.loads(specs_file.read_text())
                self.assertTrue(saved_specs["comp123"]["is_guest_favorite"])
                self.assertEqual(saved_specs["comp123"]["guest_favorite_badge"], "Guest Favorite")

    def test_sync_comp_ratings_cli_command(self):
        """CLI main correctly parses and routes sync-comp-ratings command."""
        from src.cli import main
        import sys

        with patch.object(sys, "argv", ["src.cli", "sync-comp-ratings", "--tier", "tier_a", "--dry-run"]):
            with patch("src.cli.sync_comp_ratings") as mock_sync:
                async def fake_coro(**kwargs):
                    return {}
                mock_sync.side_effect = fake_coro
                main()
                mock_sync.assert_called_once_with(
                    tier="tier_a",
                    force=False,
                    dry_run=True,
                    generate_html_after=False,
                )

    def test_sync_ratings_strict_flag_failure(self):
        """sync-ratings --strict exits with code 1 when any channel has partial_harvest or stale_error."""
        from unittest.mock import AsyncMock

        mock_args = MagicMock()
        mock_args.platform = "all"
        mock_args.headless = True
        mock_args.force = False
        mock_args.backfill = False
        mock_args.dry_run = True
        mock_args.no_dashboard = True
        mock_args.strict = True

        mock_summary = {
            "channels": {
                "airbnb": {"status": "ok", "rating": 4.83, "review_count": 77, "harvested_count": 77, "harvest_ratio": 1.0, "new_reviews_added": 0},
                "vrbo": {"status": "partial_harvest", "rating": 9.6, "review_count": 34, "harvested_count": 2, "harvest_ratio": 0.0588, "new_reviews_added": 0},
            },
            "successful": 2,
            "total_new_reviews": 0,
        }

        with patch("src.stealth_connection.StealthConnectionManager.start_pool", new_callable=AsyncMock), \
             patch("src.stealth_connection.StealthConnectionManager.stop_pool", new_callable=AsyncMock), \
             patch("src.ratings_collector.RatingsCollector.sync_all", new_callable=AsyncMock, return_value=mock_summary), \
             patch("src.ratings_collector.RatingsCollector.load_database", return_value={"reviews": [{"id": "r1"}]}):

            with self.assertRaises(SystemExit) as cm:
                f = io.StringIO()
                with patch("sys.stdout", f):
                    run_sync_ratings(mock_args)

            self.assertEqual(cm.exception.code, 1)

    def test_sync_ratings_strict_flag_success(self):
        """sync-ratings --strict completes without exit when all channels are ok."""
        from unittest.mock import AsyncMock

        mock_args = MagicMock()
        mock_args.platform = "all"
        mock_args.headless = True
        mock_args.force = False
        mock_args.backfill = False
        mock_args.dry_run = True
        mock_args.no_dashboard = True
        mock_args.strict = True

        mock_summary = {
            "channels": {
                "airbnb": {"status": "ok", "rating": 4.83, "review_count": 77, "harvested_count": 77, "harvest_ratio": 1.0, "new_reviews_added": 0},
                "vrbo": {"status": "ok", "rating": 9.6, "review_count": 34, "harvested_count": 34, "harvest_ratio": 1.0, "new_reviews_added": 0},
            },
            "successful": 2,
            "total_new_reviews": 0,
        }

        with patch("src.stealth_connection.StealthConnectionManager.start_pool", new_callable=AsyncMock), \
             patch("src.stealth_connection.StealthConnectionManager.stop_pool", new_callable=AsyncMock), \
             patch("src.ratings_collector.RatingsCollector.sync_all", new_callable=AsyncMock, return_value=mock_summary), \
             patch("src.ratings_collector.RatingsCollector.load_database", return_value={"reviews": [{"id": "r1"}]}):

            f = io.StringIO()
            with patch("sys.stdout", f):
                run_sync_ratings(mock_args)

            out = f.getvalue()
            self.assertIn("✅ Full Sync complete", out)
            self.assertIn("Verified database volume", out)

    def test_sync_ratings_summary_formatting(self):
        """sync-ratings prints truthful headlines and partial harvest missing counts."""
        from unittest.mock import AsyncMock

        mock_args = MagicMock()
        mock_args.platform = "all"
        mock_args.headless = True
        mock_args.force = False
        mock_args.backfill = False
        mock_args.dry_run = False
        mock_args.no_dashboard = True
        mock_args.strict = False

        mock_summary = {
            "channels": {
                "airbnb": {"status": "ok", "rating": 4.83, "review_count": 77, "harvested_count": 77, "harvest_ratio": 1.0, "new_reviews_added": 0},
                "vrbo": {"status": "partial_harvest", "rating": 9.6, "review_count": 34, "harvested_count": 2, "harvest_ratio": 0.0588, "new_reviews_added": 0},
            },
            "successful": 2,
            "total_new_reviews": 0,
        }

        with patch("src.stealth_connection.StealthConnectionManager.start_pool", new_callable=AsyncMock), \
             patch("src.stealth_connection.StealthConnectionManager.stop_pool", new_callable=AsyncMock), \
             patch("src.ratings_collector.RatingsCollector.sync_all", new_callable=AsyncMock, return_value=mock_summary), \
             patch("src.ratings_collector.RatingsCollector.load_database", return_value={"reviews": [{"id": "r1"}, {"id": "r2"}]}):

            f = io.StringIO()
            with patch("sys.stdout", f):
                run_sync_ratings(mock_args)

            out = f.getvalue()
            self.assertIn("⚠️ Partial Sync complete", out)
            self.assertIn("partial_harvest: 32 missing", out)
            self.assertIn("Verified database volume: 2 total reviews", out)

    def test_sync_ratings_summary_error_headline(self):
        """sync-ratings displays '❌ Sync failed' headline when a channel encounters an error."""
        from unittest.mock import AsyncMock

        mock_args = MagicMock()
        mock_args.platform = "all"
        mock_args.headless = True
        mock_args.force = False
        mock_args.backfill = False
        mock_args.dry_run = False
        mock_args.no_dashboard = True
        mock_args.strict = False

        mock_summary = {
            "channels": {
                "airbnb": {"status": "error", "error": "Connection timed out"},
            },
            "successful": 0,
            "total_new_reviews": 0,
        }

        with patch("src.stealth_connection.StealthConnectionManager.start_pool", new_callable=AsyncMock), \
             patch("src.stealth_connection.StealthConnectionManager.stop_pool", new_callable=AsyncMock), \
             patch("src.ratings_collector.RatingsCollector.sync_all", new_callable=AsyncMock, return_value=mock_summary), \
             patch("src.ratings_collector.RatingsCollector.load_database", return_value={"reviews": [{"id": "r1"}]}):

            f = io.StringIO()
            with patch("sys.stdout", f):
                run_sync_ratings(mock_args)

            out = f.getvalue()
            self.assertIn("❌ Sync failed", out)
            self.assertIn("[❌ error]", out)


if __name__ == "__main__":
    unittest.main()


