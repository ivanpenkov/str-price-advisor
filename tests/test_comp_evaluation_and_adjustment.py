"""Unit tests for CompEvaluator and dual adjusted/raw pricing analytics."""

import unittest
from src.comp_evaluator import CompEvaluator
from src.analytics import PricingAnalyticsEngine


class TestCompEvaluationAndAdjustment(unittest.TestCase):

    def setUp(self):
        self.evaluator = CompEvaluator()
        self.engine = PricingAnalyticsEngine(
            base_percentile=65.0,
            cleaning_fee=500.0,
            urgent_pct_diff=35.0,
            urgent_lead_days=60,
            moderate_pct_diff=10.0,
        )

    def test_comp_evaluator_valid_comp(self):
        """Standard 6-bedroom luxury compound with pool and games should be valid."""
        comp = {
            "listing_id": "123456",
            "name": "Resort Compound in Tempe",
            "bedrooms": 6,
            "bathrooms": 5.0,
            "accommodates": 16,
            "rating": 4.90,
            "reviews": 45,
            "location": "Tempe, AZ",
            "amenities": ["Pool", "Hot tub", "Billiards table", "BBQ grill", "Fire pit"],
            "description": "Stunning private estate with resort pool and game room.",
        }
        res = self.evaluator.evaluate_comp(comp)
        self.assertTrue(res["is_valid_comp"])
        delta = (res["composite_score"] - 88.0) / 88.0
        expected_ratio = round(max(0.65, min(1.35, 1.0 + 2.0 * delta)), 2)
        self.assertEqual(res["desirability_ratio"], expected_ratio)
        self.assertGreater(res["composite_score"], 70.0)
        self.assertIn("outdoor", res["category_scores"])
        self.assertIn("capacity", res["category_scores"])

    def test_comp_evaluator_disqualify_no_pool(self):
        """A property in Tempe with no pool must be disqualified when full amenities are known."""
        comp = {
            "listing_id": "999999",
            "name": "Suburban House with No Pool",
            "bedrooms": 5,
            "bathrooms": 3.0,
            "accommodates": 12,
            "rating": 4.80,
            "reviews": 30,
            "location": "Tempe, AZ",
            "amenities_count": 45,
            "amenities": ["Wifi", "Kitchen", "Air conditioning", "Washer", "Dryer"],
            "description": "Lovely 5 bedroom house in quiet neighborhood. Does not have a pool.",
        }
        res = self.evaluator.evaluate_comp(comp)
        self.assertFalse(res["is_valid_comp"])
        self.assertIn("swimming pool", res["validity_reason"].lower())

    def test_comp_evaluator_disqualify_few_bedrooms(self):
        """A property with fewer than 4 bedrooms is not a comp for a 6BR luxury villa."""
        comp = {
            "listing_id": "333333",
            "name": "Cozy 3BR Bungalow",
            "bedrooms": 3,
            "bathrooms": 2.0,
            "accommodates": 6,
            "rating": 4.95,
            "reviews": 60,
            "location": "Scottsdale, AZ",
            "amenities": ["Pool", "Wifi"],
            "description": "Charming 3 bedroom home.",
        }
        res = self.evaluator.evaluate_comp(comp)
        self.assertFalse(res["is_valid_comp"])
        self.assertIn("bedroom", res["validity_reason"].lower())

    def test_adjustment_ratio_math(self):
        """Verify adjusted rate formula: adjusted_price = raw_price / ratio."""
        # 10% less desirable comp (ratio = 0.90) asking $900 -> adjusted price $1000
        raw_price_inferior = 900.0
        ratio_inferior = 0.90
        adj_inferior = raw_price_inferior / ratio_inferior
        self.assertAlmostEqual(adj_inferior, 1000.0, places=2)

        # 15% superior comp (ratio = 1.15) asking $1150 -> adjusted price $1000
        raw_price_superior = 1150.0
        ratio_superior = 1.15
        adj_superior = raw_price_superior / ratio_superior
        self.assertAlmostEqual(adj_superior, 1000.0, places=2)

    def test_dual_analytics_with_adjusted_and_disqualified_comps(self):
        """Dual analytics should compute separate raw and adjusted stats, excluding disqualified comps from adjusted."""
        segment = {
            "check_in": "2026-10-09",
            "check_out": "2026-10-12",
            "nights": 3,
            "lead_time_days": 35,
            "our_base_nightly": 699.0,
            "our_cleaning_fee": 500.0,
            "our_total_price": 2597.0,
            "our_effective_nightly": 865.67,
        }

        # 3 comps:
        # 1) Valid, ratio 0.90, raw $900 -> adj $1000
        # 2) Valid, ratio 1.15, raw $1150 -> adj $1000
        # 3) Disqualified, raw $300 (budget house with no pool) -> excluded from adj
        comp_metadata = [
            {
                "listing_id": "comp_1",
                "name": "Comp 1 (Inferior)",
                "effective_nightly": 900.0,
                "total_price": 2700.0,
                "desirability_ratio": 0.90,
                "is_valid_comp": True,
            },
            {
                "listing_id": "comp_2",
                "name": "Comp 2 (Superior)",
                "effective_nightly": 1150.0,
                "total_price": 3450.0,
                "desirability_ratio": 1.15,
                "is_valid_comp": True,
            },
            {
                "listing_id": "comp_disqualified",
                "name": "Comp Disqualified",
                "effective_nightly": 300.0,
                "total_price": 900.0,
                "desirability_ratio": 0.50,
                "is_valid_comp": False,
                "validity_reason": "No swimming pool",
            },
        ]
        raw_rates = [900.0, 1150.0, 300.0]

        eval_res = self.engine.evaluate_segment(segment, raw_rates, comp_metadata=comp_metadata)

        # Raw count includes all 3 comps
        self.assertEqual(eval_res["n_comps"], 3)
        # Adjusted count only includes the 2 valid comps
        self.assertEqual(eval_res["n_comps_adj"], 2)

        # In adjusted rates: both valid comps adjust to $1000
        self.assertAlmostEqual(eval_res["comp_p50_adj"], 1000.0, places=1)
        self.assertAlmostEqual(eval_res["comp_target_adj"], 1000.0, places=1)

        # In raw rates: includes the $300 outlier/disqualified comp
        self.assertLess(eval_res["comp_p50_eff"], 1000.0)

        # Verify action summary contains dollar amounts and no percentage string
        action_summary = eval_res.get("action_summary", "")
        self.assertNotIn("%", action_summary)
        self.assertTrue("$" in action_summary or action_summary == "")

    def test_comp_evaluator_square_footage_scaling(self):
        """Properties with >=6,500 sq ft receive an estate capacity bonus; <4,000 sq ft receive a penalty."""
        comp_large = {
            "listing_id": "70001",
            "name": "Expansive Estate",
            "bedrooms": 6,
            "baths": 6.0,
            "guests": 16,
            "location": "Scottsdale",
            "description": "Stunning 7,000 sq ft luxury estate with heated pool.",
        }
        res_large = self.evaluator.evaluate_comp(comp_large)

        comp_compact = {
            "listing_id": "70002",
            "name": "Compact House",
            "bedrooms": 6,
            "baths": 6.0,
            "guests": 16,
            "location": "Scottsdale",
            "description": "Lovely 3,200 sq ft house with pool.",
        }
        res_compact = self.evaluator.evaluate_comp(comp_compact)

        self.assertGreater(res_large["category_scores"]["capacity"], res_compact["category_scores"]["capacity"])
        self.assertIn("7,000 sq ft estate", res_large["rationale"])

    def test_comp_evaluator_wellness_and_tennis_court(self):
        """Properties with private sauna and full tennis court score higher in outdoor category."""
        comp_tennis_sauna = {
            "listing_id": "80001",
            "name": "Wellness & Tennis Compound",
            "bedrooms": 7,
            "baths": 6.5,
            "rating": 4.97,
            "reviews": 30,
            "location": "Scottsdale",
            "raw_snippet": "Home in Scottsdale | 7,000 sq ft with courts and pool",
            "description": "Features full private tennis court, pickleball, private sauna, movie theater, billiards table, heated pool, and hot tub.",
            "amenities": ["Pool", "Private hot tub", "Private tennis court", "Pickleball", "Private sauna", "Movie theater", "Pool table", "SubZero refrigerator"],
        }
        res = self.evaluator.evaluate_comp(comp_tennis_sauna)
        self.assertTrue(res["is_valid_comp"])
        self.assertGreaterEqual(res["category_scores"]["outdoor"], 95)
        self.assertGreaterEqual(res["desirability_ratio"], 1.20)
        self.assertIn("private tennis court", res["rationale"])
        self.assertIn("private sauna", res["rationale"])

    def test_html_generator_fallback_evaluates_unregistered_comp(self):
        """HTMLDashboardGenerator should dynamically evaluate unregistered comps on the fly instead of defaulting to 1.0."""
        from src.html_generator import HTMLDashboardGenerator
        gen = HTMLDashboardGenerator()

        # Listing not in comps_registry.json
        unregistered_comp = {
            "listing_id": "999999999999999",
            "name": "Hidden Gem Estate",
            "location": "Scottsdale",
            "bedrooms": 7,
            "beds": 10,
            "baths": 6.5,
            "rating": 4.98,
            "reviews": 35,
            "effective_nightly": 1200.0,
            "total_price": 4800.0,
            "description": "7,000 sq ft estate with tennis court, sauna, movie theater, billiards, and heated pool.",
            "amenities": ["Pool", "Private hot tub", "Private tennis court", "Private sauna", "Movie theater", "Pool table"],
        }
        eval_res = gen.evaluator.evaluate_comp(unregistered_comp)
        self.assertTrue(eval_res["is_valid_comp"])
        self.assertGreaterEqual(eval_res["desirability_ratio"], 1.15)


if __name__ == "__main__":
    unittest.main()
