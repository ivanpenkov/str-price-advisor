"""
Unit Tests for Ratings & Reviews Ingestion Engine (src/ratings_collector.py).
Verifies parsing for Airbnb, VRBO, and Booking.com (native 10.0 scale),
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

        mock_payload["propertyReviewSummary"]["badgeName"] = "Loved by Guests"
        mock_payload["propertyReviewSummary"]["badgeSubtitle"] = "Top 10% of guest reviews in this area"

        rating, count, sub_scores, reviews, badge = self.collector.parse_vrbo_data(payload=mock_payload)
        self.assertEqual(rating, 4.90)
        self.assertEqual(count, 34)
        self.assertEqual(sub_scores.get("cleanliness"), 4.9)
        self.assertEqual(len(reviews), 1)
        self.assertIsNotNone(badge)
        self.assertEqual(badge["name"], "Loved by Guests")
        self.assertEqual(badge["subtitle"], "Top 10% of guest reviews in this area")

        rev = reviews[0]
        self.assertEqual(rev["platform"], "vrbo")
        self.assertEqual(rev["title"], "Perfect summer getaway with friends")
        self.assertEqual(rev["date"], "2026-06-15")
        self.assertEqual(rev["rating"], 5.0)
        self.assertEqual(rev["rating_max"], 10.0)
        self.assertIsNotNone(rev["host_response"])
        self.assertEqual(rev["host_response"]["body"], "Thank you Rachel! We've replaced the blender pitcher.")

    def test_vrbo_html_parsing_native_10_scale_and_badge(self):
        """VRBO HTML parsing extracts 10.0 scale rating and Loved by Guests badge."""
        html_content = """
        <div class="property-summary">
            <span class="badge">Loved by Guests</span>
            <span class="badge-subtitle">Top 10% of guest reviews in this area</span>
            <div class="rating-box">9.6 / 10 Exceptional (34 reviews)</div>
        </div>
        """
        rating, count, sub_scores, reviews, badge = self.collector.parse_vrbo_data(html_content=html_content)
        self.assertEqual(rating, 9.6)
        self.assertEqual(count, 34)
        self.assertIsNotNone(badge)
        self.assertEqual(badge["name"], "Loved by Guests")
        self.assertEqual(badge["subtitle"], "Top 10% of guest reviews in this area")
        self.assertEqual(badge["badge_type"], "top_percentile")

        # Also test combined format "9.6 Loved by Guests (34 reviews)"
        html_alt = """
        <div>
            <span>Loved by Guests</span>
            <span>9.6 Loved by Guests (42 reviews)</span>
        </div>
        """
        r2, c2, _, _, b2 = self.collector.parse_vrbo_data(html_content=html_alt)
        self.assertEqual(r2, 9.6)
        self.assertEqual(c2, 42)
        self.assertIsNotNone(b2)
        self.assertEqual(b2["name"], "Loved by Guests")

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
    def test_corridor_proxy_mapping_and_failover(self):
        """Resolves designated corridor proxies (SF, Dallas, Chicago) and tests transparent failover."""
        from unittest.mock import MagicMock
        self.collector.proxy_mgr = MagicMock()
        ep_sf = MagicMock()
        ep_sf.name = "feeder-sf-1"
        ep_sf.url = "http://127.0.0.1:56303"
        ep_dal = MagicMock()
        ep_dal.name = "feeder-dal-1"
        ep_dal.url = "http://127.0.0.1:56302"
        ep_chi = MagicMock()
        ep_chi.name = "feeder-chi-1"
        ep_chi.url = "http://127.0.0.1:56304"

        self.collector.proxy_mgr.endpoints = [ep_sf, ep_dal, ep_chi]

        self.assertEqual(self.collector._get_corridor_proxy("airbnb"), {"server": "http://127.0.0.1:56303"})
        self.assertEqual(self.collector._get_corridor_proxy("vrbo"), {"server": "http://127.0.0.1:56302"})
        self.assertEqual(self.collector._get_corridor_proxy("booking"), {"server": "http://127.0.0.1:56304"})
        self.assertIsNone(self.collector._get_corridor_proxy("unknown_platform"))

        # Test transparent failover when Dallas corridor is offline
        self.collector.proxy_mgr.endpoints = [ep_sf, ep_chi]
        fallback_proxy = self.collector._get_corridor_proxy("vrbo")
        self.assertIsNotNone(fallback_proxy)
        self.assertIn(fallback_proxy["server"], ["http://127.0.0.1:56303", "http://127.0.0.1:56304"])

    def test_scrape_airbnb_live_mocked(self):
        """Airbnb live scraper intercepts GraphQL payload and extracts ratings/reviews."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        fixture_path = Path("tests/fixtures/mock_airbnb_graphql.json")
        fixture_data = json.loads(fixture_path.read_text(encoding="utf-8"))

        mock_page = AsyncMock()
        mock_resp = AsyncMock()
        mock_resp.url = "https://www.airbnb.com/api/v3/StayProductDetailPage"
        mock_resp.headers = {"content-type": "application/json"}
        mock_resp.json = AsyncMock(return_value=fixture_data)

        # Trigger on('response') callback when goto is awaited
        callbacks = []
        def fake_on(event, cb):
            if event == "response":
                callbacks.append(cb)
        mock_page.on = MagicMock(side_effect=fake_on)

        async def fake_goto(*args, **kwargs):
            for cb in callbacks:
                await cb(mock_resp)
            return None

        mock_page.goto.side_effect = fake_goto
        mock_page.query_selector = AsyncMock(return_value=None)
        mock_page.content = AsyncMock(return_value="<html></html>")

        mock_context = MagicMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)

        res = asyncio.run(self.collector._scrape_airbnb_live(mock_context, backfill=False))
        rating, count, sub_scores, reviews = res

        self.assertEqual(rating, 4.83)
        self.assertEqual(count, 77)
        self.assertEqual(sub_scores.get("cleanliness"), 4.98)
        self.assertEqual(len(reviews), 2)
        self.assertEqual(reviews[0]["reviewer_name"], "Sarah M.")

    def test_scrape_vrbo_live_mocked(self):
        """VRBO live scraper intercepts Apollo payload and extracts native 10.0 scale and Loved by Guests badge."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        fixture_path = Path("tests/fixtures/mock_vrbo_apollo.json")
        fixture_data = json.loads(fixture_path.read_text(encoding="utf-8"))

        mock_page = AsyncMock()
        mock_resp = AsyncMock()
        mock_resp.url = "https://www.vrbo.com/graphql/propertyReviewSummary"
        mock_resp.headers = {"content-type": "application/json"}
        mock_resp.json = AsyncMock(return_value=fixture_data)

        callbacks = []
        mock_page.on = MagicMock(side_effect=lambda ev, cb: callbacks.append(cb) if ev == "response" else None)

        async def fake_goto(*args, **kwargs):
            for cb in callbacks:
                await cb(mock_resp)
            return None

        mock_page.goto.side_effect = fake_goto
        mock_page.query_selector = AsyncMock(return_value=None)
        mock_page.content = AsyncMock(return_value="<html></html>")

        mock_context = MagicMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)

        res = asyncio.run(self.collector._scrape_vrbo_live(mock_context, backfill=False))
        rating, count, sub_scores, reviews, badge = res

        self.assertEqual(rating, 9.6)
        self.assertEqual(count, 34)
        self.assertIsNotNone(badge)
        self.assertEqual(badge["name"], "Loved by Guests")
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0]["reviewer_name"], "Rachel W.")

    def test_scrape_booking_live_mocked(self):
        """Booking.com live scraper parses DOM review blocks, pairs pros/cons, and preserves 10.0 scale."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        html_content = Path("tests/fixtures/mock_booking_dom.html").read_text(encoding="utf-8")

        mock_page = AsyncMock()
        mock_page.goto = AsyncMock(return_value=None)
        mock_page.content = AsyncMock(return_value=html_content)
        mock_page.query_selector = AsyncMock(return_value=None)

        mock_context = MagicMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)

        res = asyncio.run(self.collector._scrape_booking_live(mock_context, backfill=False))
        rating, count, sub_scores, reviews = res

        self.assertEqual(rating, 9.4)
        self.assertEqual(count, 18)
        self.assertEqual(len(reviews), 1)
        rev = reviews[0]
        self.assertEqual(rev["reviewer_name"], "David K.")
        self.assertIn("Exceptional property in a quiet Tempe neighborhood", rev["body"])
        self.assertIn("A minor issue with one of the secondary bathroom door locks", rev["body"])
        self.assertIsNotNone(rev["host_response"])
        self.assertEqual(rev["host_response"]["responder_name"], "Villa del Sol Host")

    def test_vrbo_apollo_wrapped_data_parsing(self):
        """VRBO parser unwraps Apollo GraphQL response nested inside {'data': ...}."""
        wrapped_payload = {
            "data": {
                "propertyReviewSummary": {
                    "rating": 9.8,
                    "totalCount": 42,
                    "badgeName": "Loved by Guests",
                    "badgeSubtitle": "Top 1% of homes",
                    "categoryRatings": [
                        {"name": "Cleanliness", "rating": 9.9},
                        {"name": "Location", "rating": 9.7},
                    ],
                },
                "reviewList": [
                    {
                        "reviewer": {"name": "Jessica T."},
                        "submissionDate": "2026-07-15",
                        "rating": 10.0,
                        "title": "Unbelievable oasis!",
                        "text": "The backyard pool and putting green made our trip unforgettable.",
                    }
                ],
            }
        }
        rating, count, sub_scores, reviews, badge = self.collector.parse_vrbo_data(payload=wrapped_payload)
        self.assertEqual(rating, 9.8)
        self.assertEqual(count, 42)
        self.assertEqual(sub_scores.get("cleanliness"), 9.9)
        self.assertEqual(sub_scores.get("location"), 9.7)
        self.assertIsNotNone(badge)
        self.assertEqual(badge["name"], "Loved by Guests")
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0]["reviewer_name"], "Jessica T.")
        self.assertEqual(reviews[0]["date"], "2026-07-15")

    def test_airbnb_list_sections_and_pdp_reviews(self):
        """Airbnb parser handles sections formatted as a list and pdpReviews pagination payloads."""
        # 1. Sections as list
        list_sections_payload = {
            "data": {
                "presentation": {
                    "stayProductDetailPage": {
                        "sections": [
                            {
                                "sectionComponentType": "REVIEWS_DEFAULT",
                                "section": {
                                    "metadata": {"overallRating": 4.97, "reviewCount": 85},
                                    "reviewComponent": {
                                        "reviewCategorySubscores": [{"categoryType": "Accuracy", "rating": 4.95}],
                                        "reviews": [
                                            {"author": {"firstName": "Carlos"}, "createdAt": "August 2026", "comments": "Great villa!"}
                                        ],
                                    },
                                },
                            }
                        ]
                    }
                }
            }
        }
        rating, count, sub_scores, reviews = self.collector.parse_airbnb_data(payload=list_sections_payload)
        self.assertEqual(rating, 4.97)
        self.assertEqual(count, 85)
        self.assertEqual(sub_scores.get("accuracy"), 4.95)
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0]["reviewer_name"], "Carlos")
        self.assertEqual(reviews[0]["date"], "2026-08-01")

        # 2. PdpReviews pagination payload
        pdp_reviews_payload = {
            "data": {
                "pdpReviews": {
                    "reviews": [
                        {"author": {"firstName": "Maya"}, "createdAt": "2026-09-01T12:00:00Z", "comments": "Loved the pool!"}
                    ]
                }
            }
        }
        _, _, _, p_reviews = self.collector.parse_airbnb_data(payload=pdp_reviews_payload)
        self.assertEqual(len(p_reviews), 1)
        self.assertEqual(p_reviews[0]["reviewer_name"], "Maya")
        self.assertEqual(p_reviews[0]["date"], "2026-09-01")

    def test_zero_phoenix_proxy_guard_isolation(self):
        """If proxy corridor is missing for an external channel, Zero Phoenix Policy triggers error isolation."""
        import asyncio
        from unittest.mock import MagicMock

        mock_mgr = MagicMock()
        mock_mgr.endpoints = []
        mock_mgr.get_proxy_configs.return_value = []

        collector = RatingsCollector(
            db_path=self.db_path,
            proxy_mgr=mock_mgr,
            stealth_delay=0.0,
        )

        res = asyncio.run(collector.sync_channel("airbnb"))
        self.assertEqual(res["status"], "stale_error")
        self.assertIn("Zero Phoenix Policy", res.get("error", ""))

    def test_airbnb_listing_rating_stats_and_pdp_reviews_container(self):
        """Airbnb parser extracts rating stats and reviews from modern node/reviews GraphQL structures."""
        payload_stats = {
            "data": {
                "node": {
                    "listingRatingStats": {
                        "overallRatingStats": {
                            "ratingAverage": 4.83,
                            "ratingCount": "77",
                        },
                        "categoryRatingStats": [
                            {"categoryTypeA": "ACCURACY", "value": {"ratingAverage": 4.75}},
                            {"categoryTypeA": "CLEANLINESS", "value": {"ratingAverage": 4.87}},
                            {"categoryTypeA": "CHECKIN", "value": {"ratingAverage": 4.92}},
                            {"categoryTypeA": "LOCATION", "value": {"ratingAverage": 4.94}},
                            {"categoryTypeA": "COMMUNICATION", "value": {"ratingAverage": 4.86}},
                            {"categoryTypeA": "VALUE", "value": {"ratingAverage": 4.73}},
                        ],
                    }
                }
            }
        }
        rating, count, sub_scores, _ = self.collector.parse_airbnb_data(payload=payload_stats)
        self.assertEqual(rating, 4.83)
        self.assertEqual(count, 77)
        self.assertEqual(sub_scores.get("cleanliness"), 4.87)
        self.assertEqual(sub_scores.get("accuracy"), 4.75)
        self.assertEqual(sub_scores.get("value"), 4.73)

        payload_reviews = {
            "data": {
                "presentation": {
                    "stayProductDetailPage": {
                        "reviews": {
                            "metadata": {
                                "reviewsCount": 77,
                            },
                            "reviews": [
                                {
                                    "id": "1768951224693044213",
                                    "createdAt": "2026-09-06T16:30:18Z",
                                    "comments": "It was a beautiful space and experience, we had a great get together.",
                                    "rating": 5,
                                    "reviewer": {
                                        "firstName": "Karter",
                                    },
                                    "localizedReviewerLocation": "Chandler, AZ",
                                    "response": "Thank you so much for your kind words! We are delighted.",
                                }
                            ]
                        }
                    }
                }
            }
        }
        _, rev_count, _, revs = self.collector.parse_airbnb_data(payload=payload_reviews)
        self.assertEqual(rev_count, 77)
        self.assertEqual(len(revs), 1)
        r0 = revs[0]
        self.assertEqual(r0["reviewer_name"], "Karter")
        self.assertEqual(r0["reviewer_location"], "Chandler, AZ")
        self.assertEqual(r0["date"], "2026-09-06")
        self.assertEqual(r0["rating"], 5.0)
        self.assertEqual(r0["rating_max"], 5.0)
        self.assertIsNotNone(r0["host_response"])
        self.assertEqual(r0["host_response"]["body"], "Thank you so much for your kind words! We are delighted.")

    def test_vrbo_modern_html_layout(self):
        """VRBO parser extracts rating, verified review count, and badge from modern layout text."""
        modern_html = """
        <div class="reviews-header">
            <span>Loved by Guests</span>
            <span>9.6 out of 10, Loved by Guests</span>
            <span class="subtitle">Top 10% of guest reviews in this area</span>
            <button data-stid="reviews-link">See all 24 verified reviews</button>
        </div>
        """
        rating, count, _, _, badge = self.collector.parse_vrbo_data(html_content=modern_html)
        self.assertEqual(rating, 9.6)
        self.assertEqual(count, 24)
        self.assertIsNotNone(badge)
        self.assertEqual(badge["name"], "Loved by Guests")

    def test_vrbo_html_review_card_parsing_isolation(self):
        """VRBO HTML parser extracts valid review cards and ignores non-review marketing/amenity cards."""
        html_content = """
        <!-- Non-review amenity card (must be ignored) -->
        <div class="uitk-card">
            <h3>Arcade/game room</h3>
            <p>A rare find - fun for all ages with on-site arcade/game room.</p>
        </div>

        <!-- Non-review marketing highlight (must be ignored) -->
        <article class="uitk-card">
            <h4>Room to spread out</h4>
            <p>Enjoy spacious layout that give everyone more room to relax.</p>
        </article>

        <!-- Genuine verified review card inside modal sheet -->
        <div data-testid="review-card">
            <div data-testid="author">Rachel W.</div>
            <div data-testid="date">Jun 15, 2026</div>
            <div data-testid="review-body">Spacious layout, beautiful pool, and very responsive host. Beyond perfection for our retreat!</div>
            <span>10/10 Excellent</span>
        </div>
        """
        _, _, _, reviews, _ = self.collector.parse_vrbo_data(html_content=html_content)
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0]["reviewer_name"], "Rachel W.")
        self.assertEqual(reviews[0]["date"], "2026-06-15")
        self.assertEqual(reviews[0]["rating"], 10.0)
        self.assertIn("Spacious layout, beautiful pool", reviews[0]["body"])

    def test_booking_avatar_badge_initial_stripping(self):
        """parse_booking_data strips duplicate leading initials from avatar badge and parses name/country."""
        booking_html = """
        <div data-testid="featuredreviewcard">
            <div data-testid="featuredreviewcard-avatar">DDediUnited States</div>
            <div data-testid="featuredreviewcard-text">“We really enjoyed the heated pool! The ability for everybody to stay comfortably was great.”</div>
        </div>
        """
        _, _, _, reviews = self.collector.parse_booking_data(html_content=booking_html)
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0]["reviewer_name"], "Dedi")
        self.assertEqual(reviews[0]["reviewer_location"], "United States")

    def test_parse_date_relative_formats(self):
        """_parse_date_str correctly resolves relative date strings into ISO YYYY-MM-DD format."""
        from src.ratings_collector import _parse_date_str
        today_iso = date.today().isoformat()
        yesterday_iso = (date.today() - timedelta(days=1)).isoformat()
        week_ago_iso = (date.today() - timedelta(days=7)).isoformat()

        self.assertEqual(_parse_date_str("today"), today_iso)
        self.assertEqual(_parse_date_str("yesterday"), yesterday_iso)
        self.assertEqual(_parse_date_str("1 week ago"), week_ago_iso)
        self.assertEqual(_parse_date_str("3 days ago"), (date.today() - timedelta(days=3)).isoformat())

    def test_check_cross_border_redirect(self):
        """check_cross_border_redirect raises CrossBorderRedirectError for foreign ccTLDs and permits valid US domains."""
        from src.ratings_collector import check_cross_border_redirect, CrossBorderRedirectError

        # Valid US domains must not raise
        check_cross_border_redirect("https://www.airbnb.com/rooms/1149021672332148784")
        check_cross_border_redirect("https://www.vrbo.com/2685684")
        check_cross_border_redirect("https://www.booking.com/hotel/us/villa-del-sol.html")
        check_cross_border_redirect(None)
        check_cross_border_redirect("")

        # Foreign ccTLDs must raise CrossBorderRedirectError (including when port is present)
        foreign_urls = [
            "https://www.airbnb.co.za/rooms/1149021672332148784",
            "https://www.airbnb.co.za:443/rooms/1149021672332148784",
            "https://www.airbnb.ru/rooms/123",
            "https://www.vrbo.co.uk/p2685684",
            "https://www.booking.de/hotel/us/villa-del-sol.html",
            "https://www.airbnb.com.br/rooms/123",
            "https://www.airbnb.ca/rooms/123",
        ]
        for bad_url in foreign_urls:
            with self.subTest(url=bad_url):
                with self.assertRaises(CrossBorderRedirectError):
                    check_cross_border_redirect(bad_url)

    def test_harvest_completeness_ratio_and_status_assignment(self):
        """merge_channel_data correctly calculates harvest_ratio and assigns granular status."""
        # 1. Complete harvest (>= 90%) -> 'ok'
        revs = [{"id": f"rev_{i}", "platform": "airbnb", "date": "2026-08-01"} for i in range(10)]
        self.collector.merge_channel_data("airbnb", 4.9, 10, {}, revs, status="ok")
        plat = self.collector.data["platforms"]["airbnb"]
        self.assertEqual(plat["harvested_count"], 10)
        self.assertEqual(plat["harvest_ratio"], 1.0)
        self.assertEqual(plat["status"], "ok")

        # 2. Complete harvest with 0 announced reviews and 0 stored reviews -> 'ok'
        self.collector.merge_channel_data("custom", 5.0, 0, {}, [], status="ok")
        plat_custom = self.collector.data["platforms"]["custom"]
        self.assertEqual(plat_custom["harvested_count"], 0)
        self.assertEqual(plat_custom["harvest_ratio"], 1.0)
        self.assertEqual(plat_custom["status"], "ok")

        # 3. Partial harvest (0 < ratio < 0.90) -> 'partial_harvest'
        vrbo_revs = [{"id": f"vrbo_{i}", "platform": "vrbo", "date": "2026-08-01"} for i in range(2)]
        self.collector.merge_channel_data("vrbo", 9.6, 34, {}, vrbo_revs, status="ok")
        plat_vrbo = self.collector.data["platforms"]["vrbo"]
        self.assertEqual(plat_vrbo["harvested_count"], 2)
        self.assertAlmostEqual(plat_vrbo["harvest_ratio"], 2 / 34, places=2)
        self.assertEqual(plat_vrbo["status"], "partial_harvest")

        # 4. Metadata only (announced > 0, stored == 0) -> 'metadata_only'
        self.collector.merge_channel_data("booking", 9.4, 18, {}, [], status="ok")
        plat_bk = self.collector.data["platforms"]["booking"]
        self.assertEqual(plat_bk["harvested_count"], 0)
        self.assertEqual(plat_bk["harvest_ratio"], 0.0)
        self.assertEqual(plat_bk["status"], "metadata_only")

        # 5. Stale error preserved -> 'stale_error'
        self.collector.merge_channel_data("airbnb", 4.9, 10, {}, [], status="stale_error")
        plat_airbnb = self.collector.data["platforms"]["airbnb"]
        self.assertEqual(plat_airbnb["status"], "stale_error")


if __name__ == "__main__":
    unittest.main()
