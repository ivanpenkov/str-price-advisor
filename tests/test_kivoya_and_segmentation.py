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


if __name__ == "__main__":
    unittest.main()

