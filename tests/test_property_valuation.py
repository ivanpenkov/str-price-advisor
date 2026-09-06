"""Unit tests for PropertyValuator (house sqft, lot acres, STR permit, and hedonic pricing)."""

import unittest
from src.property_valuation import PropertyValuator


class TestPropertyValuator(unittest.TestCase):

    def test_extract_house_sqft_explicit(self):
        """Extract explicit interior living area from listing text."""
        t1 = "Spacious 6,200 sq ft luxury estate with heated pool and sports court."
        sqft, src = PropertyValuator.extract_house_sqft(t1)
        self.assertEqual(sqft, 6200)
        self.assertEqual(src, "Listing Disclosed")

        t2 = "This 5,400 sqft home features 6 bedrooms and 6 baths."
        sqft, src = PropertyValuator.extract_house_sqft(t2)
        self.assertEqual(sqft, 5400)
        self.assertEqual(src, "Listing Disclosed")

        t3 = "A stunning retreat offering over 7,500 square feet of luxury living."
        sqft, src = PropertyValuator.extract_house_sqft(t3)
        self.assertEqual(sqft, 7500)
        self.assertEqual(src, "Listing Disclosed")

    def test_extract_house_sqft_patio_exclusion(self):
        """Ignore patio or deck square footage when living area is not explicitly given or disambiguated."""
        t_patio = "Enjoy a 1,500 sq ft covered patio and barbecue pavilion in the backyard."
        sqft, src = PropertyValuator.extract_house_sqft(t_patio)
        # Should NOT match patio as house sqft
        self.assertIsNone(sqft)

        t_both = "Featuring 5,200 sq ft of interior living space plus an 800 sq ft covered patio."
        sqft, src = PropertyValuator.extract_house_sqft(t_both)
        self.assertEqual(sqft, 5200)

    def test_extract_lot_acres_fractions_and_decimals(self):
        """Extract lot size expressed as fractions, decimals, or square feet."""
        t1 = "Situated on a 3/4 acre gated estate with mountain views."
        lot, src = PropertyValuator.extract_lot_acres(t1)
        self.assertAlmostEqual(lot, 0.75, places=2)
        self.assertEqual(src, "Listing Disclosed")

        t2 = "Spread across a sprawling 1.25 acres of lush desert grounds."
        lot, src = PropertyValuator.extract_lot_acres(t2)
        self.assertAlmostEqual(lot, 1.25, places=2)
        self.assertEqual(src, "Listing Disclosed")

        t3 = "Private half-acre resort backyard with sparkling pool."
        lot, src = PropertyValuator.extract_lot_acres(t3)
        self.assertAlmostEqual(lot, 0.50, places=2)
        self.assertEqual(src, "Listing Disclosed")

        t4 = "Gated compound on a 43,560 sq ft lot."
        lot, src = PropertyValuator.extract_lot_acres(t4)
        self.assertAlmostEqual(lot, 1.00, places=2)
        self.assertEqual(src, "Listing Disclosed")

    def test_extract_license_number(self):
        """Extract short term rental permit / license numbers."""
        t1 = "City of Scottsdale License # STR-2023-00456 | TPT 21456789"
        lic = PropertyValuator.extract_license_number(t1)
        self.assertIsNotNone(lic)
        self.assertTrue("STR-2023-00456" in lic or "21456789" in lic or "2023" in lic)

        t2 = "SHORT TERM RENTAL LICENSE: 2037435"
        lic2 = PropertyValuator.extract_license_number(t2)
        self.assertEqual(lic2, "2037435")

    def test_impute_specs_hedonic_model(self):
        """Hedonic model computes realistic specs based on bedroom count and corridor."""
        # 6BR / 6BA in Scottsdale
        sqft, lot, val = PropertyValuator.impute_specs(
            br=6, ba=6.0, guests=16, location="Scottsdale", all_text="Luxury estate"
        )
        self.assertGreaterEqual(sqft, 4800)
        self.assertLessEqual(sqft, 6000)
        self.assertGreaterEqual(lot, 0.40)
        # Scottsdale rate ($550/sqft) * ~5400 * lot_mult -> ~$3M+
        self.assertGreaterEqual(val, 2500000.0)

        # 5BR / 4BA in Mesa
        sqft_m, lot_m, val_m = PropertyValuator.impute_specs(
            br=5, ba=4.0, guests=12, location="Mesa", all_text="Suburban home"
        )
        self.assertLess(val_m, val)  # Mesa asset value must be lower than Scottsdale

    def test_villa_del_sol_ground_truth(self):
        """Villa del Sol benchmark property specs must match verified assessor ground truth."""
        specs = PropertyValuator.evaluate_property_specs(
            listing_id="573857947793833342",
            title="Villa del Sol",
            description="",
            location="Tempe",
            br=6,
            ba=6.0,
            guests=16,
        )
        self.assertEqual(specs["sqft"], 5400)
        self.assertEqual(specs["sqft_source"], "Assessor Verified")
        self.assertEqual(specs["lot_acres"], 0.75)
        self.assertEqual(specs["lot_source"], "Assessor Verified")
        self.assertEqual(specs["est_property_value"], 2000000.0)
        self.assertEqual(specs["property_value_source"], "Assessor Verified")
        self.assertEqual(specs["str_license"], "STR-000055")


if __name__ == "__main__":
    unittest.main()

