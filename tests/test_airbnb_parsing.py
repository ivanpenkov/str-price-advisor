"""Unit tests for deterministic Airbnb card text price parsing."""

import unittest
from src.airbnb_collector import AirbnbCollector


class TestAirbnbParsing(unittest.TestCase):

    def setUp(self):
        self.collector = AirbnbCollector()

    def test_discounted_nightly_and_total_before_taxes(self):
        """Card with strikethrough original nightly, discounted nightly, and total before taxes."""
        card_text = (
            "Guest favorite\n"
            "Home in Scottsdale\n"
            "Heated Pool & Spa | 6BR Golf Estate\n"
            "4.95 (19)\n"
            "6 bedrooms · 6 beds · 4.5 baths\n"
            "$379.75 $354 night · $1,417 before taxes"
        )
        parsed = self.collector._parse_card_text("1350253802489041827", card_text, nights=4)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["total_price"], 1417.0)
        self.assertEqual(parsed["effective_nightly"], 354.25)
        self.assertEqual(parsed["confidence"], "CONFIRMED")
        self.assertEqual(parsed["bedrooms"], 6)
        self.assertEqual(parsed["baths"], 4.5)

    def test_regular_nightly_and_total_before_taxes(self):
        """Card with standard nightly and total before taxes."""
        card_text = (
            "Guest favorite\n"
            "Entire home in Scottsdale\n"
            "Speakeasy mansion with a sports court\n"
            "5.0 (45)\n"
            "7 bedrooms · 7 beds · 4 baths\n"
            "$815 night · $3,259 before taxes"
        )
        parsed = self.collector._parse_card_text("1260105010563602721", card_text, nights=4)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["total_price"], 3259.0)
        self.assertEqual(parsed["effective_nightly"], 814.75)
        self.assertEqual(parsed["confidence"], "CONFIRMED")
        self.assertEqual(parsed["bedrooms"], 7)
        self.assertEqual(parsed["baths"], 4.0)

    def test_slash_night_format(self):
        """Card with '$650 / night' format."""
        card_text = (
            "Home in Mesa\n"
            "Resort Compound\n"
            "5.0 (10)\n"
            "6 bedrooms · 6 beds · 4 baths\n"
            "$650 / night"
        )
        parsed = self.collector._parse_card_text("999001", card_text, nights=3)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["effective_nightly"], 650.0)
        self.assertEqual(parsed["total_price"], 1950.0)
        self.assertEqual(parsed["confidence"], "CONFIRMED")

    def test_total_for_nights_format(self):
        """Card with '$3,000 for 3 nights' format."""
        card_text = (
            "Home in Chandler\n"
            "Desert Compound\n"
            "4.9 (20)\n"
            "6 bedrooms · 6 beds · 4 baths\n"
            "$3,000 for 3 nights"
        )
        parsed = self.collector._parse_card_text("999002", card_text, nights=3)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["total_price"], 3000.0)
        self.assertEqual(parsed["effective_nightly"], 1000.0)
        self.assertEqual(parsed["confidence"], "CONFIRMED")

    def test_conflicting_prices_flagged_ambiguous(self):
        """Card with mathematically contradictory nightly and total should be flagged AMBIGUOUS."""
        card_text = (
            "Home in Scottsdale\n"
            "Glitchy Villa\n"
            "4.8 (5)\n"
            "6 bedrooms · 6 beds · 4 baths\n"
            "$900 night · $1,200 before taxes"
        )
        parsed = self.collector._parse_card_text("999003", card_text, nights=4)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["confidence"], "AMBIGUOUS")
        self.assertIn("Conflict", parsed["confidence_reason"])

    def test_unlabeled_price_flagged_ambiguous(self):
        """Card with bare dollar number without 'night' or 'total'/'before taxes' label is flagged AMBIGUOUS."""
        card_text = (
            "Home in Scottsdale\n"
            "Bare Price Estate\n"
            "4.9 (15)\n"
            "6 bedrooms · 6 beds · 4 baths\n"
            "$1,200"
        )
        parsed = self.collector._parse_card_text("999004", card_text, nights=3)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["confidence"], "AMBIGUOUS")
    def test_alternative_date_card_rejected(self):
        """Card with alternative date recommendation (e.g. 'Sep 7 to 9' for a Sep 6-10 search) must be rejected."""
        card_text = (
            "Sep 7 to 9\n"
            "Sep 7–9\n"
            "Home in Scottsdale\n"
            "Modern 6,000 sqft resort with pool and spa\n"
            "5.0 (24)\n"
            "7 bedrooms · 7 beds · 6.5 baths\n"
            "$1,270 night · $2,540 before taxes"
        )
        parsed = self.collector._parse_card_text("1565568181308368429", card_text, nights=4)
        self.assertIsNone(parsed)


    def test_rating_and_reviews_extraction(self):
        """Extract rating and review counts accurately."""
        card_text = (
            "Home in Scottsdale\n"
            "Heated Pool & Spa | 6BR Golf Estate\n"
            "Rating 4.95 out of 5; 19 reviews\n"
            "4.95 (19)\n"
            "6 bedrooms · 6 beds · 4.5 baths\n"
            "$354 night · $1,417 before taxes"
        )
        parsed = self.collector._parse_card_text("1350253802489041827", card_text, nights=4)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["rating"], 4.95)
        self.assertEqual(parsed["reviews"], 19)

    def test_rating_5_point_0_two_reviews(self):
        """User example: rated 5.0 with 2 reviews."""
        card_text = (
            "Home in Paradise Valley\n"
            "Luxury Villa\n"
            "5.0 (2)\n"
            "6 bedrooms · 6 beds · 5 baths\n"
            "$950 night · $2,850 before taxes"
        )
        parsed = self.collector._parse_card_text("999123", card_text, nights=3)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["rating"], 5.0)
        self.assertEqual(parsed["reviews"], 2)

    def test_unrated_new_listing(self):
        """Brand new listing with no rating shows None rating and 0 reviews."""
        card_text = (
            "Home in Gilbert\n"
            "New! 6br Gilbert Retreat: Pool, Hot Tub, Game Room\n"
            "6 bedrooms · 6 beds · 4 baths\n"
            "$450 night · $1,350 before taxes"
        )
        parsed = self.collector._parse_card_text("1746855567446910912", card_text, nights=3)
        self.assertIsNotNone(parsed)
        self.assertIsNone(parsed["rating"])
        self.assertEqual(parsed["reviews"], 0)


    def test_bed_extraction_compound_title(self):
        """Extract beds when formatted as '8King Beds' in title/snippet."""
        card_text = (
            "Guest favorite\n"
            "Home in Paradise Valley\n"
            "Casa Nuda 7BR 8King Beds\n"
            "4.89 (55)\n"
            "7 bedrooms · 6 baths\n"
            "$492 night · $1,477 before taxes"
        )
        parsed = self.collector._parse_card_text("1081331699304657121", card_text, nights=3)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["bedrooms"], 7)
        self.assertEqual(parsed["beds"], 8)
        self.assertEqual(parsed["baths"], 6.0)

    def test_bed_extraction_from_listing_specs_fallback(self):
        """When card snippet omits bed count (e.g. AI subtitle), fall back to canonical listing specs."""
        card_text = (
            "Guest favorite\n"
            "Home in Paradise Valley\n"
            "Desert contemporary with 20-foot ceilings\n"
            "4.89 (55)\n"
            "7 bedrooms · 6 baths\n"
            "$492 night · $1,477 before taxes"
        )
        parsed = self.collector._parse_card_text("1081331699304657121", card_text, nights=3)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["bedrooms"], 7)
        self.assertEqual(parsed["beds"], 8)
        self.assertEqual(parsed["baths"], 6.0)

    def test_bed_extraction_explicit_beds(self):
        """Listing with 'Exhale@Yale 8 Br 12beds' extracts 8 bedrooms and 12 beds."""
        card_text = (
            "Top guest favorite\n"
            "Home in Phoenix\n"
            "Exhale@Yale 8 Br 12beds\n"
            "4.95 (40)\n"
            "8 bedrooms · 4 baths\n"
            "$600 night · $1,800 before taxes"
        )
        parsed = self.collector._parse_card_text("12141154", card_text, nights=3)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["bedrooms"], 8)
        self.assertEqual(parsed["beds"], 12)


if __name__ == "__main__":
    unittest.main()
