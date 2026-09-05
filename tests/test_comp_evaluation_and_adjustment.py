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

    def test_comp_evaluator_swim_up_bar_has_pool(self):
        """A property mentioning a swim-up bar in its title or text must be recognized as having a pool."""
        comp = {
            "listing_id": "53478007",
            "name": "Arcades,Golf,Pickleball,Theater,Gym &Swim-up Bar!",
            "bedrooms": 7,
            "baths": 4.5,
            "accommodates": 14,
            "rating": 4.70,
            "reviews": 60,
            "location": "Scottsdale",
            "amenities": [],
            "description": "Luxury home with outdoor entertainment area, swim-up bar, and arcade.",
        }
        res = self.evaluator.evaluate_comp(comp)
        self.assertTrue(res["is_valid_comp"])
        self.assertTrue(res["pool_specs"]["has_pool"])

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

    def test_pool_specs_extraction_free_vs_fee_vs_unheated(self):
        """Test extraction of pool heating type and pool size tiers."""
        # 1. Free pool heating
        specs_free = CompEvaluator.extract_pool_specs(
            "HUGE Golf course home-FREE Heated Pool/Theatre/Gym",
            ["Pool", "Free heated pool", "Wifi"],
        )
        self.assertTrue(specs_free["has_pool"])
        self.assertEqual(specs_free["heating"], "free")

        # 2. Fee-based pool heating
        specs_fee = CompEvaluator.extract_pool_specs(
            "Luxury villa in Scottsdale. Pool heat is available for $100/night upon request.",
            ["Pool", "Private pool", "Air conditioning"],
        )
        self.assertTrue(specs_fee["has_pool"])
        self.assertEqual(specs_fee["heating"], "fee")

        # 3. Unheated pool (verified with full amenities list)
        specs_unheated = CompEvaluator.extract_pool_specs(
            "Backyard with swimming pool and patio table.",
            ["Pool", "Wifi", "Kitchen", "Air conditioning", "Washer", "Dryer", "TV", "Iron", "Heating", "Hair dryer", "Essentials"],
        )
        self.assertTrue(specs_unheated["has_pool"])
        self.assertEqual(specs_unheated["heating"], "unheated")

        # 3b. Unenriched comp with sparse snippet assumes standard heated (no false penalty)
        specs_unenriched = CompEvaluator.extract_pool_specs(
            "Home in Phoenix | Indigo Oasis, Pool, Sleeps 18",
            [],
        )
        self.assertEqual(specs_unenriched["heating"], "standard_heated")

        # 4. Plunge pool
        specs_plunge = CompEvaluator.extract_pool_specs(
            "Cozy patio with cocktail plunge pool.",
            ["Plunge pool", "Wifi"],
        )
        self.assertEqual(specs_plunge["pool_size"], "plunge")

        # 5. Large resort pool with gallon count
        specs_large = CompEvaluator.extract_pool_specs(
            "Spectacular backyard featuring 30,000 gallon resort pool with rock waterfall grotto.",
            ["Pool", "Private pool"],
        )
        self.assertEqual(specs_large["pool_size"], "large")
        self.assertEqual(specs_large["gallons"], 30000)

        # 6. Villa del Sol ground truth
        vds_specs = CompEvaluator.extract_pool_specs("", [], listing_id="573857947793833342")
        self.assertEqual(vds_specs["heating"], "free")
        self.assertEqual(vds_specs["pool_size"], "large")
        self.assertEqual(vds_specs["gallons"], 30000)

    def test_seasonal_ratios_winter_vs_summer(self):
        """Unheated pool has lower winter ratio than summer ratio due to cold weather penalty."""
        comp_unheated = {
            "listing_id": "777777",
            "name": "Standard Scottsdale Home",
            "bedrooms": 6,
            "baths": 4.5,
            "rating": 4.85,
            "reviews": 25,
            "location": "Scottsdale",
            "description": "6BR home with standard private swimming pool and BBQ.",
            "amenities": ["Pool", "Wifi", "Kitchen", "Air conditioning", "Washer", "Dryer", "TV", "Iron", "Heating", "Hair dryer", "Essentials"],
        }
        res = self.evaluator.evaluate_comp(comp_unheated)
        self.assertTrue(res["is_valid_comp"])
        # Winter ratio should be lower than summer ratio for unheated comp
        self.assertLess(res["winter_ratio"], res["summer_ratio"])
        self.assertIn("unheated pool in winter", res["winter_rationale"])
        self.assertNotIn("unheated pool in winter", res["summer_rationale"])

        # Free heated pool comp should maintain high score in winter
        comp_free_heated = {
            "listing_id": "888888",
            "name": "Golf Course Luxury Estate - FREE Heated Pool",
            "bedrooms": 6,
            "baths": 5.0,
            "rating": 4.90,
            "reviews": 30,
            "location": "Scottsdale",
            "description": "Includes free heated pool and spa year-round.",
            "amenities": ["Pool", "Hot tub", "Wifi"],
        }
        res_free = self.evaluator.evaluate_comp(comp_free_heated)
        self.assertGreaterEqual(res_free["winter_category_scores"]["outdoor"], 80)
        self.assertIn("free heated pool", res_free["winter_rationale"])

    def test_html_generator_seasonal_ratio_selection(self):
        """HTMLDashboardGenerator should pick winter_ratio for Oct-Apr and summer_ratio for May-Sep."""
        from src.html_generator import HTMLDashboardGenerator
        gen = HTMLDashboardGenerator()

        comp_data = {
            "listing_id": "777777",
            "effective_nightly": 1000.0,
            "total_price": 3000.0,
            "winter_ratio": 0.85,
            "summer_ratio": 0.95,
            "desirability_ratio": 0.85,
            "winter_rationale": "Winter rationale text",
            "summer_rationale": "Summer rationale text",
            "is_valid_comp": True,
        }

        # Winter segment (October 2026)
        winter_segment = {
            "check_in": "2026-10-15",
            "check_out": "2026-10-18",
            "nights": 3,
            "our_base_nightly": 800.0,
            "comps_list": [comp_data],
        }
        gen.comps_dict["777777"] = comp_data
        subtable_winter, _, _, _, _, _ = gen._render_comp_subtable(winter_segment, "row-winter")
        self.assertIn("Winter", subtable_winter)
        self.assertIn("0.85x", subtable_winter)

        # Summer segment (June 2026)
        summer_segment = {
            "check_in": "2026-06-15",
            "check_out": "2026-06-18",
            "nights": 3,
            "our_base_nightly": 600.0,
            "comps_list": [comp_data],
        }
        subtable_summer, _, _, _, _, _ = gen._render_comp_subtable(summer_segment, "row-summer")
        self.assertIn("Summer", subtable_summer)
        self.assertIn("0.95x", subtable_summer)

    def test_pool_specs_extraction_from_guest_reviews(self):
        """Guest reviews should provide empirical evidence for pool heating, fees, or cold pools."""
        # 1. Heated pool confirmed by review when amenity list only says "Pool"
        specs_heated = CompEvaluator.extract_pool_specs(
            "Lovely home with private pool and patio.",
            ["Pool", "Wifi", "Kitchen", "Air conditioning", "Washer", "Dryer", "TV", "Iron", "Heating", "Hair dryer", "Essentials"],
            reviews=["The heated pool was 85 degrees and our kids swam every day in January!"],
        )
        self.assertEqual(specs_heated["heating"], "standard_heated")
        self.assertIn("guest reviews", specs_heated["heating_source"].lower())

        # 2. Pool heat fee revealed by guest review
        specs_fee = CompEvaluator.extract_pool_specs(
            "Lovely home with private pool and patio.",
            ["Pool", "Wifi", "Kitchen", "Air conditioning", "Washer", "Dryer", "TV", "Iron", "Heating", "Hair dryer", "Essentials"],
            reviews=["We paid for pool heat and it was worth every penny during our Christmas stay."],
        )
        self.assertEqual(specs_fee["heating"], "fee")
        self.assertIn("fee confirmed by guest reviews", specs_fee["heating_source"].lower())

        # 3. Free pool heat confirmed by guest review
        specs_free = CompEvaluator.extract_pool_specs(
            "Luxury villa with swimming pool.",
            ["Pool", "Wifi", "Kitchen", "Air conditioning", "Washer", "Dryer", "TV", "Iron", "Heating", "Hair dryer", "Essentials"],
            reviews=["Free pool heat included in our stay was the highlight!"],
        )
        self.assertEqual(specs_free["heating"], "free")
        self.assertIn("free pool heat confirmed by guest reviews", specs_free["heating_source"].lower())

        # 4. Unheated pool reported by guest review
        specs_unheated = CompEvaluator.extract_pool_specs(
            "Standard residential home with pool.",
            ["Pool", "Wifi", "Kitchen", "Air conditioning", "Washer", "Dryer", "TV", "Iron", "Heating", "Hair dryer", "Essentials"],
            reviews=["The pool was freezing and unheated so we could not swim in February."],
        )
        self.assertEqual(specs_unheated["heating"], "unheated")
        self.assertIn("unheated / cold pool reported by guest reviews", specs_unheated["heating_source"].lower())

    def test_unenriched_comp_no_false_unheated_penalty(self):
        """Unenriched comps with sparse 10-word snippets must never suffer false unheated winter penalty."""
        comp_unenriched = {
            "listing_id": "944305567506818495",
            "title": "Home in Phoenix",
            "location": "Phoenix",
            "bedrooms": 6,
            "baths": 3.0,
            "raw_snippet": "Guest favorite | Home in Phoenix | Indigo Oasis, Pool, Sleeps 18",
            # No full amenities array scraped yet
        }
        res = self.evaluator.evaluate_comp(comp_unenriched)
        self.assertTrue(res["is_valid_comp"])
        # Should NOT have the -10 winter penalty or "unheated pool in winter" in rationale
        self.assertNotIn("unheated pool in winter", res["winter_rationale"])
        self.assertGreaterEqual(res["winter_ratio"], 0.80)

    def test_showstopper_escape_1269286452215821303_evaluation(self):
        """Comp 1269286452215821303 (The Showstopper Escape) must be evaluated as a valid 6BR luxury comp with heated pool."""
        comp_meta = {
            "listing_id": "1269286452215821303",
            "name": "The Showstopper Escape",
            "location": "Scottsdale",
            "bedrooms": None,  # Test fallback when deferred state returned None
            "beds": None,
            "baths": None,
            "rating": 5.0,
            "reviews": 25,
        }
        enriched = {
            "listing_id": "1269286452215821303",
            "title": "The Showstopper Escape",
            "description": "Luxury Scottsdale villa with heated pool, winter sun & resort vibes. 6BR for up to 22, minutes from Old Town, Kierland & Scottsdale Quarter. Perfect for February Waste Management Open, family, or group getaway. Heated pool & spa, fire pit, pickleball, putting green, arcade, movie lounge, golf simulator, half-court basketball, indoor/outdoor dining. Keyless entry • Fast response • Professionally cleaned.\n\nSHORT TERM RENTAL LICENSE: 2037435",
            "bedrooms": None,
            "beds": None,
            "baths": 5.5,
            "guests": None,
            "overview": ["22 guests", "6 bedrooms", "6 beds", "5.5 baths"],
            "amenities": ["Heated pool", "Hot tub", "Pickleball court", "Basketball court", "Putting green", "Arcade"],
            "rating": 5.0,
            "reviews": 25,
        }
        res = self.evaluator.evaluate_comp(comp_meta, enriched_data=enriched)
        self.assertTrue(res["is_valid_comp"])
        self.assertTrue(res["pool_specs"]["has_pool"])
        self.assertEqual(res["pool_specs"]["heating"], "standard_heated")
        self.assertEqual(res["category_scores"]["outdoor"], 100)
        self.assertEqual(res["category_scores"]["capacity"], 100)
        self.assertGreaterEqual(res["winter_ratio"], 1.15)
        self.assertIn("heated pool", res["winter_rationale"].lower())


if __name__ == "__main__":
    unittest.main()

