"""
Unit tests for feature scraping and spec extraction (beds, bedrooms, baths, guests, amenities).
Validates that ListingEnricher and AirbnbCollector accurately extract property features
from Airbnb Apollo deferred state, JSON-LD schemas, and card text snippets.
"""

import json
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

from src.listing_enricher import ListingEnricher
from src.airbnb_collector import AirbnbCollector


class TestFeatureScraping(unittest.TestCase):
    """Test suite for beds, bedrooms, bathrooms, and capacity extraction."""

    def setUp(self):
        self.enricher = ListingEnricher(headless=True)
        self.collector = AirbnbCollector()

    def test_parse_deferred_state_listing_16363441(self):
        """
        Verify that listing 16363441 extracts 6 bedrooms, 14 beds, 4.0 baths,
        16+ guests, and verified amenities from Apollo deferred state.
        """
        sample_deferred = json.dumps({
            "niobeMinimalClientData": [
                [
                    "ROOT_QUERY",
                    {
                        "presentation": {
                            "stayProductDetailPage": {
                                "sections": {
                                    "metadata": {
                                        "sharingConfig": {
                                            "title": "Home in Chandler · ★4.89 · 6 bedrooms · 14 beds · 4 baths"
                                        }
                                    }
                                }
                            }
                        }
                    }
                ],
                [
                    "amenity-1",
                    {"__typename": "AmenityItem", "title": "Private heated pool", "available": True}
                ],
                [
                    "amenity-2",
                    {"__typename": "AmenityItem", "title": "Billiards table", "available": True}
                ],
                [
                    "amenity-3",
                    {"__typename": "AmenityItem", "title": "Hot tub", "available": True}
                ],
                [
                    "guest-token",
                    {"label": "16+ guests"}
                ]
            ]
        })

        parsed = self.enricher.parse_deferred_state(sample_deferred)
        self.assertEqual(parsed["bedrooms"], 6)
        self.assertEqual(parsed["beds"], 14)
        self.assertEqual(parsed["baths"], 4.0)
        self.assertEqual(parsed["guests"], "16+")
        self.assertEqual(parsed["amenities_count"], 3)
        self.assertIn("Private heated pool", parsed["amenities"])
        self.assertIn("Billiards table", parsed["amenities"])
        self.assertIn("Hot tub", parsed["amenities"])

    def test_parse_deferred_state_decimal_baths(self):
        """Verify extraction of half baths (e.g. 5.5 baths) and multi-bed combinations."""
        sample_deferred = json.dumps({
            "data": {
                "subtitle": "Entire villa in Scottsdale · ★4.95 · 7 bedrooms · 10 beds · 5.5 baths",
                "capacity": "14 guests"
            }
        })
        parsed = self.enricher.parse_deferred_state(sample_deferred)
        self.assertEqual(parsed["bedrooms"], 7)
        self.assertEqual(parsed["beds"], 10)
        self.assertEqual(parsed["baths"], 5.5)
        self.assertEqual(parsed["guests"], "14")

    def test_parse_deferred_state_fallback_tokens(self):
        """Verify extraction when subtitle string is absent but separate tokens exist in JSON."""
        sample_deferred = json.dumps({
            "items": [
                {"text": "8 bedrooms"},
                {"text": "12 beds"},
                {"text": "6 baths"},
                {"text": "16 guests"}
            ]
        })
        parsed = self.enricher.parse_deferred_state(sample_deferred)
        self.assertEqual(parsed["bedrooms"], 8)
        self.assertEqual(parsed["beds"], 12)
        self.assertEqual(parsed["baths"], 6.0)
        self.assertEqual(parsed["guests"], "16")

    def test_parse_deferred_state_singular_units(self):
        """Verify singular '1 bedroom', '1 bed', '1 bath' are parsed correctly."""
        sample_deferred = json.dumps({
            "header": "Guesthouse in Tempe · 1 bedroom · 1 bed · 1 bath",
            "capacity": "2 guests"
        })
        parsed = self.enricher.parse_deferred_state(sample_deferred)
        self.assertEqual(parsed["bedrooms"], 1)
        self.assertEqual(parsed["beds"], 1)
        self.assertEqual(parsed["baths"], 1.0)
        self.assertEqual(parsed["guests"], "2")

    def test_parse_json_ld_metadata(self):
        """Verify Schema.org JSON-LD parsing extracts title, rating, reviews, and photo URL."""
        sample_ld = {
            "name": "Family, Golf, Friends 6BR/4BA, heated pool/spa.",
            "description": "Luxurious 6BR estate with heated pool, putting green, and spa.",
            "image": [
                "https://a0.muscache.com/im/pictures/test_photo.jpg?im_w=720"
            ],
            "aggregateRating": {
                "ratingValue": 4.89,
                "ratingCount": 101
            },
            "address": {
                "addressLocality": "Chandler",
                "addressRegion": "AZ"
            }
        }
        parsed = self.enricher.parse_json_ld(sample_ld)
        self.assertEqual(parsed["title"], "Family, Golf, Friends 6BR/4BA, heated pool/spa.")
        self.assertEqual(parsed["rating"], 4.89)
        self.assertEqual(parsed["reviews"], 101)
        self.assertEqual(parsed["photo_url"], "https://a0.muscache.com/im/pictures/test_photo.jpg?im_w=720")

    def test_503_service_unavailable_title_rejected(self):
        """Verify that '503 Service Unavailable' title from rate limits is discarded."""
        sample_ld = {
            "name": "503 Service Unavailable - Airbnb",
            "description": ""
        }
        parsed = self.enricher.parse_json_ld(sample_ld)
        self.assertIsNone(parsed["title"])

        # In parse_page_content, it should fall back to page title if clean
        integrated = self.enricher.parse_page_content(
            deferred_text="",
            ld_data=sample_ld,
            page_title="Stunning 6BR Resort Estate - Airbnb",
            listing_id="16363441"
        )
        self.assertEqual(integrated["title"], "Stunning 6BR Resort Estate")

    def test_parse_page_content_complete(self):
        """Verify complete end-to-end page content assembly."""
        sample_deferred = json.dumps({
            "banner": "Home in Chandler · ★4.89 · 6 bedrooms · 14 beds · 4 baths",
            "guest": "16+ guests"
        })
        sample_ld = {
            "name": "Family, Golf, Friends 6BR/4BA, heated pool/spa.",
            "description": "Spacious family retreat.",
            "aggregateRating": {"ratingValue": 4.89, "ratingCount": 101}
        }
        profile = self.enricher.parse_page_content(
            deferred_text=sample_deferred,
            ld_data=sample_ld,
            listing_id="16363441"
        )
        self.assertEqual(profile["listing_id"], "16363441")
        self.assertEqual(profile["title"], "Family, Golf, Friends 6BR/4BA, heated pool/spa.")
        self.assertEqual(profile["bedrooms"], 6)
        self.assertEqual(profile["beds"], 14)
        self.assertEqual(profile["baths"], 4.0)
        self.assertEqual(profile["guests"], "16+")
        self.assertEqual(profile["rating"], 4.89)
        self.assertEqual(profile["reviews"], 101)
        self.assertIn("14 beds", profile["overview"])

    def test_card_text_feature_extraction_in_collector(self):
        """Verify search card text parsing extracts bedrooms, beds, and baths accurately."""
        card_text = (
            "Top guest favorite\n"
            "Home in Chandler\n"
            "Family, Golf, Friends 6BR/4BA, heated pool/spa.\n"
            "4.89 (101)\n"
            "6 bedrooms · 14 beds · 4 baths\n"
            "$739 night · $2,218 before taxes"
        )
        parsed = self.collector._parse_card_text("16363441", card_text, nights=3)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["bedrooms"], 6)
        self.assertEqual(parsed["beds"], 14)
        self.assertEqual(parsed["baths"], 4.0)
        self.assertEqual(parsed["rating"], 4.89)
        self.assertEqual(parsed["reviews"], 101)
        self.assertEqual(parsed["total_price"], 2218.0)

    def test_clean_profile_title_from_h1(self):
        """Verify clean_profile_title returns clean host marketing headline intact."""
        title = "HUGE Golf course home-FREE Heated Pool/Theatre/Gym"
        self.assertEqual(self.enricher.clean_profile_title(title), title)

    def test_clean_profile_title_strips_airbnb_suffixes(self):
        """Verify Airbnb document title and location suffixes are cleanly stripped."""
        cases = [
            (
                "HUGE Golf course home-FREE Heated Pool/Theatre/Gym - Houses for Rent in Tempe, Arizona, United States - Airbnb",
                "HUGE Golf course home-FREE Heated Pool/Theatre/Gym",
            ),
            (
                "The Desert Diamond - LUXE Desert GOLD - Old Town - Houses for Rent in Scottsdale, Arizona, United States - Airbnb",
                "The Desert Diamond - LUXE Desert GOLD - Old Town",
            ),
            (
                "Stunning 6BR Resort Estate - Airbnb",
                "Stunning 6BR Resort Estate",
            ),
            (
                "Luxe Oasis! Pool, Spa, Golf, Theater & Games! - Entire home in Mesa, Arizona, United States - Airbnb",
                "Luxe Oasis! Pool, Spa, Golf, Theater & Games!",
            ),
        ]
        for raw, expected in cases:
            self.assertEqual(self.enricher.clean_profile_title(raw), expected)

    def test_clean_profile_title_rejects_generic_location_placeholders(self):
        """Verify generic location headers and error strings are disqualified."""
        generics = [
            "Home in Tempe",
            "Entire home in Scottsdale, Arizona",
            "Villa in Paradise Valley",
            "Guesthouse in Gilbert",
            "503 Service Unavailable - Airbnb",
            "6 bedrooms",
            "14 beds",
            "Luxury Estate",
            "",
            None,
        ]
        for item in generics:
            self.assertIsNone(self.enricher.clean_profile_title(item))

    def test_parse_page_content_title_resolution_precedence(self):
        """
        Verify title resolution hierarchy in parse_page_content:
        dom_h1 > og_title > cleaned page_title > valid JSON-LD name,
        and verify generic JSON-LD ('Home in Tempe') is ignored in favor of real marketing title.
        """
        # Scenario 1: Comp 790605881020674983 with generic JSON-LD but explicit DOM h1
        profile = self.enricher.parse_page_content(
            deferred_text="",
            ld_data={"name": "Home in Tempe", "description": "Spacious golf retreat."},
            page_title="HUGE Golf course home-FREE Heated Pool/Theatre/Gym - Houses for Rent in Tempe, Arizona, United States - Airbnb",
            dom_h1="HUGE Golf course home-FREE Heated Pool/Theatre/Gym",
            listing_id="790605881020674983",
        )
        self.assertEqual(profile["title"], "HUGE Golf course home-FREE Heated Pool/Theatre/Gym")

        # Scenario 2: dom_h1 absent, falls back to cleaned page_title rather than generic JSON-LD
        profile2 = self.enricher.parse_page_content(
            deferred_text="",
            ld_data={"name": "Home in Tempe", "description": "Spacious golf retreat."},
            page_title="The Desert Diamond - LUXE Desert GOLD - Old Town - Houses for Rent in Scottsdale, Arizona, United States - Airbnb",
            listing_id="1493069124077219890",
        )
        self.assertEqual(profile2["title"], "The Desert Diamond - LUXE Desert GOLD - Old Town")

        # Scenario 3: og:title used when dom_h1 is absent
        profile3 = self.enricher.parse_page_content(
            deferred_text="",
            ld_data={"name": "Home in Mesa"},
            og_title="Game Time - 6 Bedroom Elite Vacation Paradise",
            page_title="Home in Mesa - Airbnb",
            listing_id="1143202699620728397",
        )
        self.assertEqual(profile3["title"], "Game Time - 6 Bedroom Elite Vacation Paradise")


if __name__ == "__main__":
    unittest.main()

