"""Unit and integration tests for KivoyaClient and CalendarSegmenter."""

import unittest
from datetime import date
from src.kivoya_client import KivoyaClient
from src.segmentation import CalendarSegmenter


class TestKivoyaAndSegmentation(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.client = KivoyaClient()
        cls.blocked = cls.client.get_blocked_periods()
        cls.rates = cls.client.get_seasonal_rates()

    def test_kivoya_blocked_periods(self):
        """Should retrieve parsed blocked periods from Kivoya API."""
        self.assertGreater(len(self.blocked), 0)
        first = self.blocked[0]
        self.assertIn("start_dt", first)
        self.assertIn("end_dt", first)
        self.assertIsInstance(first["start_dt"], date)
        self.assertIsInstance(first["end_dt"], date)
        self.assertTrue(first["start_dt"] < first["end_dt"])

    def test_kivoya_seasonal_rates(self):
        """Should retrieve parsed seasonal base rates from Kivoya API."""
        self.assertGreater(len(self.rates), 0)
        first = self.rates[0]
        self.assertIn("nightly_rate", first)
        self.assertGreater(first["nightly_rate"], 100.0)
        self.assertIn("begin_dt", first)
        self.assertIn("end_dt", first)

    def test_segmentation_generation(self):
        """CalendarSegmenter should generate unbooked intervals over 12 months."""
        segmenter = CalendarSegmenter(kivoya_client=self.client, lookahead_days=365)
        segments = segmenter.generate_unbooked_segments()
        self.assertGreater(len(segments), 20)

        for s in segments[:10]:
            self.assertIn(s["segment_type"], ["weekend", "midweek"])
            self.assertGreater(s["nights"], 0)
            self.assertGreater(s["our_base_nightly"], 0)
            self.assertGreater(s["our_effective_nightly"], s["our_base_nightly"])

    def test_daily_availability_api(self):
        """Should retrieve parsed daily availability from Kivoya GetPropertyAvailabilityRawData."""
        daily = self.client.get_daily_availability()
        self.assertGreater(len(daily), 100)
        # October 25, 2026 is occupied night (checkout Oct 26 morning)
        oct_25 = date(2026, 10, 25)
        oct_26 = date(2026, 10, 26)
        if oct_25 in daily:
            self.assertFalse(daily[oct_25]["available"])
        if oct_26 in daily:
            self.assertTrue(daily[oct_26]["available"])

    def test_october_25_booked_exclusion(self):
        """Verify October 25 is recognized as occupied, producing Oct 26-29 (3 nights) midweek."""
        segmenter = CalendarSegmenter(kivoya_client=self.client, lookahead_days=365)
        booked = segmenter.get_booked_dates_set(self.blocked)
        self.assertIn(date(2026, 10, 25), booked)
        self.assertNotIn(date(2026, 10, 26), booked)

        segments = segmenter.generate_unbooked_segments()
        check_ins = [s["check_in"] for s in segments]
        self.assertNotIn("2026-10-25", check_ins, "2026-10-25 must not be an unbooked check-in date")
        self.assertIn("2026-10-26", check_ins, "2026-10-26 should be the unbooked midweek check-in date")

        oct_seg = next(s for s in segments if s["check_in"] == "2026-10-26")
        self.assertEqual(oct_seg["check_out"], "2026-10-29")
        self.assertEqual(oct_seg["nights"], 3)


if __name__ == "__main__":
    unittest.main()

