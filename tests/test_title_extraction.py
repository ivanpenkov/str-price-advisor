"""Unit tests for proper extraction of titles from competitor profiles, snippets, and cards."""

import unittest
from src.html_generator import extract_clean_listing_title, HTMLDashboardGenerator
from src.airbnb_collector import AirbnbCollector


class TestTitleExtraction(unittest.TestCase):

    def test_curated_name_from_comps_registry_has_highest_precedence(self):
        """Curated registry profile name takes precedence over raw snippet and generic spec."""
        snippet = "Home in Mesa | Game room, diving pool, and putting green | 6 bedrooms | 6 bedrooms"
        curated = "Game Time - 6 Bedroom Elite Vacation Paradise"
        title = extract_clean_listing_title(
            raw_snippet=snippet,
            default_title="6 bedrooms",
            registered_name=curated,
        )
        self.assertEqual(title, "Game Time - 6 Bedroom Elite Vacation Paradise")

    def test_single_comp_sweep_snippet_rejects_price_and_sweep_labels(self):
        """Single comp sweep format rejects '$2,195 for 4 nights' and 'Single Comp Sweep'."""
        snippet = "Single Comp Sweep | The Desert Diamond - LUXE Desert GOLD - Old Town | $2,195 for 4 nights"
        title = extract_clean_listing_title(raw_snippet=snippet)
        self.assertEqual(title, "The Desert Diamond - LUXE Desert GOLD - Old Town")

    def test_marketing_headline_extracted_when_not_registered(self):
        """When not in comps registry, extract the marketing headline and reject repeated '6 bedrooms'."""
        snippet = "Home in Mesa | Outdoor bowling alley and a movie theater | 6 bedrooms | 6 bedrooms"
        title = extract_clean_listing_title(raw_snippet=snippet, default_title="6 bedrooms")
        self.assertEqual(title, "Outdoor bowling alley and a movie theater")

    def test_rejection_of_bedroom_and_bathroom_specs(self):
        """Spec lines must be skipped in favor of real property titles."""
        test_cases = [
            ("Home in Scottsdale | 6 bedrooms | 6 beds", "6 bedrooms"),
            ("Home in Scottsdale | 8 Bedroom 4 Bathroom 15+ Beds", "8 Bedroom 4 Bathroom 15+ Beds"),
            ("Home in Phoenix | 13 bedrooms", "13 bedrooms"),
            ("Home in Mesa | 7 bedrooms · 7 beds · 4 baths", "7 bedrooms · 7 beds · 4 baths"),
        ]
        for snippet, spec in test_cases:
            res = extract_clean_listing_title(raw_snippet=snippet, default_title=spec)
            self.assertNotEqual(res, spec)
            self.assertNotIn("bedroom", res.lower())

    def test_rejection_of_price_strings(self):
        """Price lines must never be extracted as titles."""
        price_snippets = [
            "Home in Scottsdale | Luxe Villa | $2,195 for 4 nights",
            "Home in Scottsdale | Luxe Villa | $354 night · $1,417 before taxes",
            "Home in Scottsdale | Luxe Villa | $650 / night",
            "Home in Scottsdale | Luxe Villa | $3,000 for 3 nights",
        ]
        for snippet in price_snippets:
            res = extract_clean_listing_title(raw_snippet=snippet)
            self.assertEqual(res, "Luxe Villa")

    def test_rejection_of_badges_and_location_prefixes(self):
        """Badges and location prefixes must not be used as title."""
        snippet = (
            "Top guest favorite | Guest favorite | Superhost | Rare find | "
            "Home in Scottsdale | Camelback Mountain Retreat"
        )
        res = extract_clean_listing_title(raw_snippet=snippet)
        self.assertEqual(res, "Camelback Mountain Retreat")

    def test_fallback_when_only_generic_available(self):
        """Fallback gracefully to registered name or Luxury Estate if no headline is found."""
        res = extract_clean_listing_title(
            raw_snippet="Home in Scottsdale | 6 bedrooms",
            default_title="6 bedrooms",
            registered_name="Scottsdale Haven",
        )
        self.assertEqual(res, "Scottsdale Haven")

        res_none = extract_clean_listing_title(
            raw_snippet="Home in Scottsdale | 6 bedrooms",
            default_title="6 bedrooms",
            registered_name="",
        )
        self.assertEqual(res_none, "Luxury Estate")

    def test_airbnb_collector_parses_card_with_spec_cleanly(self):
        """AirbnbCollector._parse_card_text does not pick '6 bedrooms' when descriptive headline exists."""
        collector = AirbnbCollector()
        card_text = (
            "Guest favorite\n"
            "Home in Mesa\n"
            "6 bedrooms\n"
            "Game room, diving pool, and putting green\n"
            "4.8 (44)\n"
            "6 bedrooms · 6 beds · 3 baths\n"
            "$515 night · $2,061 before taxes"
        )
        parsed = collector._parse_card_text("1143202699620728397", card_text, nights=4)
        self.assertIsNotNone(parsed)
        self.assertNotEqual(parsed["title"], "6 bedrooms")
        self.assertIn(
            parsed["title"],
            [
                "Game Time - 6 Bedroom Elite Vacation Paradise",
                "Game room, diving pool, and putting green",
            ],
        )

    def test_dashboard_subtable_renders_real_titles_for_known_comps(self):
        """HTMLDashboardGenerator renders proper titles for 1143202699620728397 and 1493069124077219890."""
        generator = HTMLDashboardGenerator()
        cached_comps = generator._load_cached_comps_by_key()
        target_key = "2026-09-13_2026-09-17"
        self.assertIn(target_key, cached_comps)

        segment = {
            "check_in": "2026-09-13",
            "check_out": "2026-09-17",
            "nights": 4,
            "our_base_nightly": 436.5,
            "our_effective_nightly": 678.25,
            "is_live_scan": True,
            "comps_list": list(cached_comps[target_key].values()),
        }

        subtable_html, our_rank, total_comps, our_pct, our_eff, is_live = generator._render_comp_subtable(
            segment, row_id="test-row-1"
        )

        # Listing 1143202699620728397 should display its real name, NOT "6 bedrooms ↗"
        self.assertIn("Game Time - 6 Bedroom Elite Vacation Paradise", subtable_html)
        self.assertNotIn("6 bedrooms ↗", subtable_html)

        # Listing 1493069124077219890 should display its real name, NOT "$2,195 for 4 nights ↗"
        self.assertIn("The Desert Diamond - LUXE Desert GOLD - Old Town", subtable_html)
        self.assertNotIn("$2,195 for 4 nights ↗", subtable_html)

        # Listing 806022522654917324 should display its real name or headline, NOT "6 bedrooms ↗"
        self.assertTrue(
            "Lux Fun Zone Home" in subtable_html or "Outdoor bowling alley and a movie theater" in subtable_html
        )
        self.assertNotIn("6 bedrooms ↗", subtable_html)

        # Listing 1077813310260513265 should display its real title, NOT "7,000 sq ft with courts and pool ↗"
        self.assertIn("Desert Diamond HTD Pool/Hot Tub/Tennis/BBall/Sauna", subtable_html)
        self.assertNotIn("7,000 sq ft with courts and pool ↗", subtable_html)

    def test_comp_1077813310260513265_specs_title_takes_precedence_over_tagline(self):
        """extract_clean_listing_title prefers registered/specs title over search snippet amenity taglines."""
        snippet = "Guest favorite | Guest favorite | Home in Scottsdale | 7,000 sq ft with courts and pool"
        real_title = "Desert Diamond HTD Pool/Hot Tub/Tennis/BBall/Sauna"
        res = extract_clean_listing_title(
            raw_snippet=snippet,
            default_title="Home in Scottsdale",
            registered_name=real_title,
        )
        self.assertEqual(res, real_title)

    def test_clean_profile_title_handles_abbreviations_and_slashes(self):
        """Verify clean_profile_title correctly handles complex titles with slashes, abbreviations, and amenities."""
        from src.listing_enricher import ListingEnricher
        enricher = ListingEnricher()
        title = "Desert Diamond HTD Pool/Hot Tub/Tennis/BBall/Sauna"
        cleaned = enricher.clean_profile_title(title)
        self.assertEqual(cleaned, "Desert Diamond HTD Pool/Hot Tub/Tennis/BBall/Sauna")

        with_suffix = f"{title} - Houses for Rent in Scottsdale, Arizona, United States - Airbnb"
        cleaned_suffix = enricher.clean_profile_title(with_suffix)
        self.assertEqual(cleaned_suffix, title)

    def test_generic_airbnb_page_title_rejected_in_favor_of_snippet(self):
        """clean_profile_title and extract_clean_listing_title must reject generic Airbnb page titles."""
        from src.listing_enricher import ListingEnricher
        generic_airbnb = "Airbnb: Vacation Rentals, Cabins, Beach Houses, Unique Homes & Experiences"
        self.assertIsNone(ListingEnricher.clean_profile_title(generic_airbnb))

        snippet = "Guest favorite | Guest favorite | Home in Scottsdale | The Showstopper Escape"
        res = extract_clean_listing_title(
            raw_snippet=snippet,
            default_title=generic_airbnb,
            registered_name=generic_airbnb,
        )
        self.assertEqual(res, "The Showstopper Escape")

    def test_marketing_headline_with_property_valuation_preserved(self):
        """Property valuation marketing titles like '$6M Estate' must not be rejected as night prices."""
        snippet = "Luxe | Luxe | Home in Paradise Valley | Entertainers Paradise! $6M Estate with Lagoon Pool"
        res = extract_clean_listing_title(
            raw_snippet=snippet,
            default_title="Home in Paradise Valley",
        )
        self.assertEqual(res, "Entertainers Paradise! $6M Estate with Lagoon Pool")

    def test_parse_deferred_state_extracts_full_description(self):
        """ListingEnricher.parse_deferred_state extracts longDescriptionHtml from deferred client state."""
        import json
        from src.listing_enricher import ListingEnricher

        mock_deferred = {
            "niobeClientData": [
                [
                    "ROOT_QUERY",
                    {
                        "data": {
                            "node": {
                                "pdpPresentation": {
                                    "descriptions": {
                                        "longDescriptionHtml": {
                                            "source": "<b>The space</b><br />6 bedrooms<br /><br /><b>Other things to note</b><br />Pool Heat (Optional):<br />We offer pool heating at $100/night with a 3-night minimum."
                                        }
                                    }
                                }
                            }
                        }
                    },
                ]
            ]
        }
        res = ListingEnricher.parse_deferred_state(json.dumps(mock_deferred))
        self.assertIsNotNone(res["description"])
        self.assertIn("pool heating at $100/night", res["description"].lower())
        self.assertIn("other things to note", res["description"].lower())



if __name__ == "__main__":
    unittest.main()
