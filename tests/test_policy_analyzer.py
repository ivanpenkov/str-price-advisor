"""
Unit tests for PolicyAnalyzer.
Tests extraction and bucketing across all 8 short-term rental policy dimensions:
Check-in, Check-out, Security Deposit, Noise Monitoring, Pool Heating, Minimum Age, Pets, and Events.
"""

import unittest
from pathlib import Path
from src.policy_analyzer import PolicyAnalyzer


class TestPolicyAnalyzer(unittest.TestCase):
    """Test suite for PolicyAnalyzer regex, structured overrides, and cohort distributions."""

    def test_extract_check_in_text(self):
        # 4 PM variations
        b, s = PolicyAnalyzer.extract_check_in("Guests must note that Check-in: 4:00 PM sharp.")
        self.assertEqual(b, "4:00 PM")
        self.assertIn("Check-in: 4:00 PM", s)

        b, s = PolicyAnalyzer.extract_check_in("Check-in time is 4 PM on day of arrival.")
        self.assertEqual(b, "4:00 PM")

        # 3 PM variations
        b, s = PolicyAnalyzer.extract_check_in("Self check-in after 3:00 PM with smart lock.")
        self.assertEqual(b, "3:00 PM")

        # 5 PM variations
        b, s = PolicyAnalyzer.extract_check_in("Check in: 5:00pm Check out: 10:00am")
        self.assertEqual(b, "5:00 PM")

        # Undisclosed
        b, s = PolicyAnalyzer.extract_check_in("Beautiful luxury compound in Scottsdale with private pool.")
        self.assertEqual(b, "Undisclosed")

    def test_extract_check_in_structured_override(self):
        rules = {"check_in_time": "After 4:00 PM"}
        b, s = PolicyAnalyzer.extract_check_in("Check-in: 3:00 PM in text", structured_rules=rules)
        self.assertEqual(b, "4:00 PM")
        self.assertIn("House Rules", s)

    def test_extract_check_out_text(self):
        # 10 AM
        b, s = PolicyAnalyzer.extract_check_out("Check-out: 10:00 AM. Please lock all patio doors.")
        self.assertEqual(b, "10:00 AM")
        self.assertIn("Check-out: 10:00 AM", s)

        # 11 AM
        b, s = PolicyAnalyzer.extract_check_out("Check out time is 11:00 AM on Sunday.")
        self.assertEqual(b, "11:00 AM")

        # Undisclosed
        b, s = PolicyAnalyzer.extract_check_out("Welcome to our desert paradise!")
        self.assertEqual(b, "Undisclosed")

    def test_extract_check_out_structured_override(self):
        rules = {"check_out_time": "11:00 AM"}
        b, s = PolicyAnalyzer.extract_check_out("Check-out is 10:00 AM in text", structured_rules=rules)
        self.assertEqual(b, "11:00 AM")

    def test_extract_deposit(self):
        # $1000+
        b, s = PolicyAnalyzer.extract_deposit("A refundable $1,000 security deposit is required prior to check-in.")
        self.assertEqual(b, "$1,000+ Deposit Required")
        self.assertIn("1,000", s)

        # $500-$999
        b, s = PolicyAnalyzer.extract_deposit("Requires $500 damage deposit held on card.")
        self.assertEqual(b, "$500–$999 Deposit Required")

        # Unspecified
        b, s = PolicyAnalyzer.extract_deposit("Guests must provide a refundable security deposit.")
        self.assertEqual(b, "Deposit Required (Unspecified)")

        # None mentioned / platform
        b, s = PolicyAnalyzer.extract_deposit("Enjoy your stay in our Scottsdale estate.")
        self.assertEqual(b, "None Mentioned / Platform Only")

    def test_extract_noise(self):
        # Hardware decibel monitor
        b, s = PolicyAnalyzer.extract_noise("Minut noise sensor is active in the backyard patio.")
        self.assertEqual(b, "Active Decibel Sensor (Minut / NoiseAware)")
        self.assertIn("Minut", s)

        b, s = PolicyAnalyzer.extract_noise("NoiseAware decibel monitoring device installed.")
        self.assertEqual(b, "Active Decibel Sensor (Minut / NoiseAware)")

        # Strict quiet hours
        b, s = PolicyAnalyzer.extract_noise("Quiet hours are from 10:00 PM to 7:00 AM every day.")
        self.assertEqual(b, "Strict Quiet Hours Declared")

        # City ordinance
        b, s = PolicyAnalyzer.extract_noise("Strict compliance with City of Scottsdale noise ordinance.")
        self.assertEqual(b, "City Noise Ordinance Warning")

        # Undisclosed
        b, s = PolicyAnalyzer.extract_noise("Enjoy the tranquil mountain views.")
        self.assertEqual(b, "Undisclosed / Standard")

    def test_extract_pool_heating(self):
        # Free via comp specs heating='free' (CompEvaluator format)
        b, s = PolicyAnalyzer.extract_pool_heating("Luxury villa with pool.", pool_specs={"has_pool": True, "heating": "free", "heating_source": "Free heated pool included"})
        self.assertEqual(b, "Free / Included in Rate")
        self.assertIn("Free heated pool", s)

        # Free via legacy heating='free_heated'
        b, s = PolicyAnalyzer.extract_pool_heating("Complimentary pool heat included for all winter stays.", pool_specs={"has_pool": True, "heating": "free_heated"})
        self.assertEqual(b, "Free / Included in Rate")

        # Free via hyphenated text 'Free-heated pool'
        b, s = PolicyAnalyzer.extract_pool_heating("Backyard Paradise – Free-heated pool, oversized hot tub, putting green.")
        self.assertEqual(b, "Free / Included in Rate")

        # Free via 'pool heat is always included in your rate'
        b, s = PolicyAnalyzer.extract_pool_heating("Pool heat is always included in your rate, no surprises.")
        self.assertEqual(b, "Free / Included in Rate")

        # Free via 'heated pool included'
        b, s = PolicyAnalyzer.extract_pool_heating("6 bedrooms / 3 bathrooms • HEATED POOL INCLUDED")
        self.assertEqual(b, "Free / Included in Rate")

        # Free via 'pool is heated at no extra charge'
        b, s = PolicyAnalyzer.extract_pool_heating("Pool is heated at no extra charge. Weekly discount automatically applied.")
        self.assertEqual(b, "Free / Included in Rate")

        # Free via title/text 'Lakefront|FREE heated POOL|SPA'
        b, s = PolicyAnalyzer.extract_pool_heating("Lakefront|FREE heated POOL|SPA|Paddleboat|Ocotillo")
        self.assertEqual(b, "Free / Included in Rate")

        # Paid extra
        b, s = PolicyAnalyzer.extract_pool_heating("Pool can be heated for an additional fee of $100/night.", pool_specs={"has_pool": True, "heating": "standard_heated"})
        self.assertEqual(b, "Paid Extra Daily Fee")

        # Unheated
        b, s = PolicyAnalyzer.extract_pool_heating("Private backyard pool.", pool_specs={"has_pool": True, "heating": "unheated"})
        self.assertEqual(b, "Unheated Pool")

        # No pool
        b, s = PolicyAnalyzer.extract_pool_heating("Luxury villa near golf course.", pool_specs={"has_pool": False, "heating": "no_pool"})
        self.assertEqual(b, "No Pool")

    def test_extract_min_age(self):
        b, s = PolicyAnalyzer.extract_min_age("Primary renter must be at least 25 years of age.")
        self.assertEqual(b, "25+ Years")

        b, s = PolicyAnalyzer.extract_min_age("Minimum age requirement of 21 years.")
        self.assertEqual(b, "21+ Years")

        b, s = PolicyAnalyzer.extract_min_age("Age minimum 26.")
        self.assertEqual(b, "26+ Years")

        b, s = PolicyAnalyzer.extract_min_age("Welcome to all families and groups.")
        self.assertEqual(b, "Undisclosed")

    def test_extract_pets(self):
        b, s = PolicyAnalyzer.extract_pets("Strict no pets allowed on premises.")
        self.assertEqual(b, "Strict No Pets")

        b, s = PolicyAnalyzer.extract_pets("Pet friendly home! Pet fee of $150 per dog.")
        self.assertEqual(b, "Pets Allowed (w/ Fee)")

        b, s = PolicyAnalyzer.extract_pets("Modern luxury desert estate.")
        self.assertEqual(b, "Undisclosed")

    def test_extract_events(self):
        b, s = PolicyAnalyzer.extract_events("Strictly no parties or events allowed.")
        self.assertEqual(b, "Strictly Prohibited")

        b, s = PolicyAnalyzer.extract_events("Small gatherings permitted with prior written host approval and event fee.")
        self.assertEqual(b, "Permitted w/ Approval or Fee")

        b, s = PolicyAnalyzer.extract_events("Spacious private retreat.")
        self.assertEqual(b, "Undisclosed")

    def test_compute_distributions_and_samples(self):
        mock_policies = {
            "comp1": {
                "listing_id": "comp1",
                "title": "Comp One",
                "url": "https://airbnb.com/rooms/comp1",
                "tier": "tier_a",
                "location": "Scottsdale",
                "policies": {
                    "check_in": {"bucket": "4:00 PM", "snippet": "Check-in: 4:00 PM"},
                    "check_out": {"bucket": "10:00 AM", "snippet": "Check-out: 10:00 AM"},
                    "deposit": {"bucket": "$1,000+ Deposit Required", "snippet": "$1,000 deposit"},
                    "noise": {"bucket": "Active Decibel Sensor (Minut / NoiseAware)", "snippet": "Minut"},
                    "pool_heating": {"bucket": "Paid Extra Daily Fee", "snippet": "$75/day"},
                    "min_age": {"bucket": "25+ Years", "snippet": "Age 25+"},
                    "pets": {"bucket": "Strict No Pets", "snippet": "No pets"},
                    "events": {"bucket": "Strictly Prohibited", "snippet": "No parties"},
                },
            },
            "comp2": {
                "listing_id": "comp2",
                "title": "Comp Two",
                "url": "https://airbnb.com/rooms/comp2",
                "tier": "tier_a",
                "location": "Tempe",
                "policies": {
                    "check_in": {"bucket": "4:00 PM", "snippet": "Check in 4pm"},
                    "check_out": {"bucket": "11:00 AM", "snippet": "Check-out 11am"},
                    "deposit": {"bucket": "None Mentioned / Platform Only", "snippet": ""},
                    "noise": {"bucket": "Strict Quiet Hours Declared", "snippet": "Quiet 10pm-7am"},
                    "pool_heating": {"bucket": "Free / Included in Rate", "snippet": "Free heat"},
                    "min_age": {"bucket": "Undisclosed", "snippet": ""},
                    "pets": {"bucket": "Pets Allowed (w/ Fee)", "snippet": "Dogs welcome"},
                    "events": {"bucket": "Strictly Prohibited", "snippet": "No events"},
                },
            },
            "comp3": {
                "listing_id": "comp3",
                "title": "Comp Three",
                "url": "https://airbnb.com/rooms/comp3",
                "tier": "tier_b",
                "location": "Mesa",
                "policies": {
                    "check_in": {"bucket": "Undisclosed", "snippet": ""},
                    "check_out": {"bucket": "Undisclosed", "snippet": ""},
                    "deposit": {"bucket": "None Mentioned / Platform Only", "snippet": ""},
                    "noise": {"bucket": "Undisclosed / Standard", "snippet": ""},
                    "pool_heating": {"bucket": "Paid Extra Daily Fee", "snippet": ""},
                    "min_age": {"bucket": "Undisclosed", "snippet": ""},
                    "pets": {"bucket": "Undisclosed", "snippet": ""},
                    "events": {"bucket": "Undisclosed", "snippet": ""},
                },
            },
        }

        # Full cohort (3 comps)
        dists = PolicyAnalyzer.compute_distributions(mock_policies)
        ci_dist = dists["check_in"]
        self.assertEqual(ci_dist["total_cohort"], 3)
        rows = {r["bucket"]: r for r in ci_dist["rows"]}
        self.assertEqual(rows["4:00 PM"]["count"], 2)
        self.assertAlmostEqual(rows["4:00 PM"]["percent"], 66.7, places=1)
        self.assertEqual(rows["4:00 PM"]["sample"]["listing_id"], "comp1")
        self.assertEqual(rows["Undisclosed"]["count"], 1)
        self.assertAlmostEqual(rows["Undisclosed"]["percent"], 33.3, places=1)

        # Filtered cohort (comp1, comp2 only)
        dists_filtered = PolicyAnalyzer.compute_distributions(mock_policies, filter_ids=["comp1", "comp2"])
        ci_filt = dists_filtered["check_in"]
        self.assertEqual(ci_filt["total_cohort"], 2)
        filt_rows = {r["bucket"]: r for r in ci_filt["rows"]}
        self.assertEqual(filt_rows["4:00 PM"]["count"], 2)
        self.assertEqual(filt_rows["4:00 PM"]["percent"], 100.0)
        self.assertNotIn("Undisclosed", filt_rows)

    def test_load_all_registry_comp_policies_live(self):
        policies = PolicyAnalyzer.load_all_registry_comp_policies()
        self.assertGreaterEqual(len(policies), 95)
        # Check that each comp has all 8 policy dimensions
        for lid, comp in list(policies.items())[:10]:
            self.assertIn("policies", comp)
            for dim in PolicyAnalyzer.DIMENSIONS:
                self.assertIn(dim["id"], comp["policies"])
                self.assertIn("bucket", comp["policies"][dim["id"]])
                self.assertIn("snippet", comp["policies"][dim["id"]])


if __name__ == "__main__":
    unittest.main()
