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
        self.assertIn("9.4 / 10.0", out)
        self.assertIn("VRBO", out)
        self.assertIn("Kivoya Direct", out)
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
                "vrbo": {"display_name": "VRBO", "scale": "5.0", "rating": 4.90, "review_count": 34},
                "booking": {"display_name": "Booking.com", "scale": "10.0", "rating": 9.4, "review_count": 18},
                "kivoya": {"display_name": "Kivoya Direct", "scale": "5.0", "rating": 5.0, "review_count": 12},
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
        mock_args = MagicMock()
        mock_args.platform = "kivoya"
        mock_args.headless = True
        mock_args.force = False
        mock_args.backfill = False
        mock_args.no_dashboard = True

        f = io.StringIO()
        with patch("sys.stdout", f):
            run_sync_ratings(mock_args)

        out = f.getvalue()
        self.assertIn("Synchronizing ratings and reviews for: kivoya", out)
        self.assertIn("Sync complete", out)


if __name__ == "__main__":
    unittest.main()

