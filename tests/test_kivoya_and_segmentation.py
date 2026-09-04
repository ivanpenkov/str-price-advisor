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

    def test_weekday_weekend_rate_intervals(self):
        """Verify that KivoyaClient distinguishes weekday vs weekend rates (e.g. Dec 4 Friday is $599, Dec 2 Wed is $399)."""
        dec_fri = date(2026, 12, 4)  # Friday (Thursday-Sunday interval: $599)
        dec_wed = date(2026, 12, 2)  # Wednesday (Monday-Wednesday interval: $399)
        self.assertEqual(self.client.get_rate_for_date(dec_fri), 599.0)
        self.assertEqual(self.client.get_rate_for_date(dec_wed), 399.0)

        jan_fri = date(2027, 1, 8)   # Friday ($899)
        jan_tue = date(2027, 1, 5)   # Tuesday ($649)
        self.assertEqual(self.client.get_rate_for_date(jan_fri), 899.0)
        self.assertEqual(self.client.get_rate_for_date(jan_tue), 649.0)

    def test_calendar_open_end_date_detection(self):
        """Verify KivoyaClient detects calendar open through May 31, 2027 and closed starting June 1, 2027."""
        open_end = self.client.get_calendar_open_end_date()
        self.assertEqual(open_end, date(2027, 5, 31))

        closed_start = self.client.get_calendar_closed_start_date()
        self.assertEqual(closed_start, date(2027, 6, 1))

    def test_segments_calendar_open_tagging(self):
        """Verify CalendarSegmenter tags intervals in May 2027 or earlier as open, and June 2027 onwards as closed."""
        segmenter = CalendarSegmenter(kivoya_client=self.client, lookahead_days=365)
        segments = segmenter.generate_unbooked_segments()

        open_segments = [s for s in segments if s.get("is_calendar_open")]
        closed_segments = [s for s in segments if not s.get("is_calendar_open")]

        self.assertGreater(len(open_segments), 0)
        self.assertGreater(len(closed_segments), 0)
        self.assertEqual(len(open_segments) + len(closed_segments), len(segments))

        # Check that last open segment check-in is <= 2027-05-31
        self.assertTrue(all(s["check_in"] <= "2027-05-31" for s in open_segments))
        # Check that closed segments start after 2027-05-31 (on or after June 1, 2027)
        self.assertTrue(all(s["check_in"] >= "2027-06-01" for s in closed_segments))


if __name__ == "__main__":
    unittest.main()

