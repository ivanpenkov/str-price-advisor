"""
Unit Tests for Ratings & Reviews Ingestion Engine (src/ratings_collector.py).
Verifies parsing for Airbnb, VRBO, Booking.com (native 10.0 scale), and Kivoya Direct,
MD5 deduplication, in-place host response updating, error isolation, and 30-day recency.
Complies with Fast Development and Zero-Sleep Test Environment Invariants.
"""

from datetime import date, timedelta
import json
from pathlib import Path
import tempfile
import unittest

from src.ratings_collector import RatingsCollector, compute_review_id


class TestRatingsCollector(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_ratings.json"
        self.collector = RatingsCollector(
            db_path=self.db_path,
            headless=True,
            recent_window_days=30,
            stealth_delay=0.0,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_compute_review_id_deterministic(self):
        """ReviewID must be deterministic and case/whitespace insensitive."""
        id1 = compute_review_id("Airbnb", "Sarah M.", "2026-08-28", "Wonderful stay at Villa del Sol!")
        id2 = compute_review_id("airbnb", "  sarah m.  ", "2026-08-28", "Wonderful stay at Villa del Sol!")
        self.assertEqual(id1, id2)
        self.assertEqual(len(id1), 32)

    def test_airbnb_parsing(self):
        """Airbnb parser extracts overall rating, review count, sub_scores, and reviews list."""
        mock_payload = {
            "data": {
                "presentation": {
                    "stayProductDetailPage": {
                        "sections": {
                            "metadata": {
                                "overallRating": 4.96,
                                "reviewCount": 76,
                            },
                            "reviewComponent": {
                                "reviewCategorySubscores": [
                                    {"title": "Cleanliness", "rating": 4.98},
                                    {"title": "Accuracy", "rating": 4.97},
                                    {"title": "Communication", "rating": 5.0},
                                    {"title": "Location", "rating": 4.93},
                                    {"title": "Check-in", "rating": 5.0},
                                    {"title": "Value", "rating": 4.88},
                                ],
                                "reviews": [
                                    {
                                        "author": {"firstName": "Sarah M.", "location": "Scottsdale, AZ"},
                                        "createdAt": "2026-08-28T18:00:00Z",
                                        "rating": 5,
                                        "comments": "Villa del Sol was beyond perfection for our executive retreat!",
                                        "response": {
                                            "authorName": "Villa del Sol Host",
                                            "createdAt": "2026-08-29T10:00:00Z",
                                            "comments": "Thank you so much Sarah!",
                                        },
                                    }
                                ],
                            },
                        }
                    }
                }
            }
        }

        rating, count, sub_scores, reviews = self.collector.parse_airbnb_data(payload=mock_payload)
        self.assertEqual(rating, 4.96)
        self.assertEqual(count, 76)
        self.assertEqual(sub_scores.get("cleanliness"), 4.98)
        self.assertEqual(sub_scores.get("accuracy"), 4.97)
        self.assertEqual(len(reviews), 1)

        rev = reviews[0]
        self.assertEqual(rev["platform"], "airbnb")
        self.assertEqual(rev["reviewer_name"], "Sarah M.")
        self.assertEqual(rev["date"], "2026-08-28")
        self.assertEqual(rev["rating"], 5.0)
        self.assertEqual(rev["rating_max"], 5.0)
        self.assertIsNotNone(rev["host_response"])
        self.assertEqual(rev["host_response"]["responder_name"], "Villa del Sol Host")
        self.assertEqual(rev["host_response"]["body"], "Thank you so much Sarah!")

    def test_vrbo_parsing(self):
        """VRBO parser extracts rating, review count, category sub_scores, and reviews list."""
        mock_payload = {
            "propertyReviewSummary": {
                "rating": 4.90,
                "totalCount": 34,
                "categoryRatings": [
                    {"name": "Cleanliness", "rating": 4.9},
                    {"name": "Accuracy", "rating": 4.9},
                    {"name": "Communication", "rating": 5.0},
                    {"name": "Location", "rating": 4.8},
                ],
            },
            "reviews": [
                {
                    "reviewer": {"name": "Rachel W.", "location": "Denver, CO"},
                    "submissionDate": "2026-06-15",
                    "title": "Perfect summer getaway with friends",
                    "rating": 5.0,
                    "text": "Spacious layout, beautiful pool, and very responsive host.",
                    "managementResponse": {
                        "text": "Thank you Rachel! We've replaced the blender pitcher.",
                        "date": "2026-06-16",
                    },
                }
            ],
        }

        rating, count, sub_scores, reviews = self.collector.parse_vrbo_data(payload=mock_payload)
        self.assertEqual(rating, 4.90)
        self.assertEqual(count, 34)
        self.assertEqual(sub_scores.get("cleanliness"), 4.9)
        self.assertEqual(len(reviews), 1)

        rev = reviews[0]
        self.assertEqual(rev["platform"], "vrbo")
        self.assertEqual(rev["title"], "Perfect summer getaway with friends")
        self.assertEqual(rev["date"], "2026-06-15")
        self.assertEqual(rev["rating"], 5.0)
        self.assertIsNotNone(rev["host_response"])
        self.assertEqual(rev["host_response"]["body"], "Thank you Rachel! We've replaced the blender pitcher.")

    def test_booking_parsing_native_10_scale(self):
        """Booking.com parser preserves native 10.0 scale and combines positive/negative comments."""
        mock_payload = {
            "score": 9.4,
            "count": 18,
            "sub_scores": {
                "staff": 9.6,
                "facilities": 9.5,
                "cleanliness": 9.8,
                "comfort": 9.4,
                "value_for_money": 9.1,
                "location": 9.3,
                "free_wifi": 10.0,
            },
            "reviews": [
                {
                    "author_name": "David K.",
                    "country": "Germany",
                    "title": "Exceptional luxury stay",
                    "date": "2026-08-19",
                    "rating": 9.0,
                    "positive_text": "Exceptional property in a quiet Tempe neighborhood. Kitchen was fully stocked.",
                    "negative_text": "A minor issue with one of the secondary bathroom door locks.",
                    "host_response": {
                        "responder_name": "Villa del Sol Host",
                        "date": "2026-08-20",
                        "body": "Thank you David! Latch adjusted right away.",
                    },
                }
            ],
        }

        rating, count, sub_scores, reviews = self.collector.parse_booking_data(payload=mock_payload)
        self.assertEqual(rating, 9.4)
        self.assertEqual(count, 18)
        self.assertEqual(sub_scores.get("staff"), 9.6)
        self.assertEqual(sub_scores.get("cleanliness"), 9.8)
        self.assertEqual(len(reviews), 1)

        rev = reviews[0]
        self.assertEqual(rev["platform"], "booking")
        self.assertEqual(rev["rating"], 9.0)
        self.assertEqual(rev["rating_max"], 10.0)
        self.assertIn("Exceptional property", rev["body"])
        self.assertIn("secondary bathroom door locks", rev["body"])
        self.assertIsNotNone(rev["host_response"])

    def test_kivoya_parsing(self):
        """Kivoya parser extracts rating and reviews."""
        mock_payload = {
            "rating": 5.0,
            "review_count": 12,
            "reviews": [
                {
                    "author_name": "Michael B.",
                    "date": "2026-05-30",
                    "rating": 5.0,
                    "body": "Direct booking through Kivoya was seamless.",
                }
            ],
        }

        rating, count, sub_scores, reviews = self.collector.parse_kivoya_data(payload=mock_payload)
        self.assertEqual(rating, 5.0)
        self.assertEqual(count, 12)
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0]["platform"], "kivoya")

    def test_deduplication_and_in_place_update(self):
        """Re-ingesting an existing review updates host response in-place without duplicate entries."""
        initial_reviews = [
            {
                "id": compute_review_id("airbnb", "Sarah M.", "2026-08-28", "Wonderful stay!"),
                "platform": "airbnb",
                "reviewer_name": "Sarah M.",
                "date": "2026-08-28",
                "rating": 5.0,
                "rating_max": 5.0,
                "body": "Wonderful stay!",
                "host_response": None,
                "is_recent": False,
            }
        ]

        added = self.collector.merge_channel_data(
            platform_id="airbnb",
            rating=4.96,
            review_count=76,
            sub_scores={"cleanliness": 4.98},
            new_reviews=initial_reviews,
        )
        self.assertEqual(added, 1)
        self.assertEqual(len(self.collector.data["reviews"]), 1)
        self.assertIsNone(self.collector.data["reviews"][0]["host_response"])

        # Second merge with identical review ID but newly added host response
        updated_reviews = [
            {
                "id": compute_review_id("airbnb", "Sarah M.", "2026-08-28", "Wonderful stay!"),
                "platform": "airbnb",
                "reviewer_name": "Sarah M.",
                "date": "2026-08-28",
                "rating": 5.0,
                "rating_max": 5.0,
                "body": "Wonderful stay!",
                "host_response": {
                    "responder_name": "Host",
                    "date": "2026-08-29",
                    "body": "Thank you Sarah!",
                },
                "is_recent": False,
            }
        ]

        added_2 = self.collector.merge_channel_data(
            platform_id="airbnb",
            rating=4.96,
            review_count=76,
            sub_scores={"cleanliness": 4.98},
            new_reviews=updated_reviews,
        )
        self.assertEqual(added_2, 0, "No duplicate review should be appended")
        self.assertEqual(len(self.collector.data["reviews"]), 1)
        self.assertIsNotNone(self.collector.data["reviews"][0]["host_response"])
        self.assertEqual(self.collector.data["reviews"][0]["host_response"]["body"], "Thank you Sarah!")

    def test_recency_window_calculation(self):
        """Reviews within 30 days of reference date are flagged is_recent=True."""
        ref_date = date(2026, 9, 14)
        recent_date = (ref_date - timedelta(days=10)).isoformat()
        older_date = (ref_date - timedelta(days=45)).isoformat()

        self.collector.data["reviews"] = [
            {"id": "rev1", "date": recent_date, "platform": "airbnb", "body": "Recent", "is_recent": False},
            {"id": "rev2", "date": older_date, "platform": "vrbo", "body": "Older", "is_recent": False},
        ]

        count = self.collector.recalculate_recency(reference_date=ref_date)
        self.assertEqual(count, 1)
        self.assertTrue(self.collector.data["reviews"][0]["is_recent"])
        self.assertFalse(self.collector.data["reviews"][1]["is_recent"])
        self.assertEqual(self.collector.data["recent_reviews_count"], 1)

    def test_atomic_persistence(self):
        """Atomic persistence properly saves JSON to disk and reloads cleanly."""
        self.collector.data["platforms"]["airbnb"]["rating"] = 4.96
        self.collector.data["platforms"]["airbnb"]["review_count"] = 76
        self.collector.save_database()

        self.assertTrue(self.db_path.exists())
        loaded = json.loads(self.db_path.read_text(encoding="utf-8"))
        self.assertEqual(loaded["platforms"]["airbnb"]["rating"], 4.96)
        self.assertEqual(loaded["platforms"]["airbnb"]["review_count"], 76)

    def test_error_isolation(self):
        """Channel failure marks status stale_error but preserves prior rating and reviews data."""
        # Seed prior good state
        self.collector.data["platforms"]["vrbo"]["rating"] = 4.90
        self.collector.data["platforms"]["vrbo"]["review_count"] = 34
        self.collector.data["platforms"]["vrbo"]["status"] = "ok"
        self.collector.data["reviews"] = [
            {"id": "vrbo_1", "platform": "vrbo", "reviewer_name": "Alice", "date": "2026-08-01", "body": "Great"}
        ]

        # Simulate exception in sync_channel by passing invalid payload causing parser exception
        import asyncio
        async def run_failing_sync():
            # mock invalid payload that raises exception during parse
            res = await self.collector.sync_channel("vrbo", mock_html="<invalid>")
            return res

        res = asyncio.run(run_failing_sync())
        # The channel data must preserve previous values
        self.assertEqual(self.collector.data["platforms"]["vrbo"]["rating"], 4.90)
        self.assertEqual(self.collector.data["platforms"]["vrbo"]["review_count"], 34)
        self.assertEqual(len(self.collector.data["reviews"]), 1)


if __name__ == "__main__":
    unittest.main()

