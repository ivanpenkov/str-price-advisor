import json
import os
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

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
        self.assertTrue(first["start_dt"] <= first["end_dt"])

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
        """Verify that KivoyaClient distinguishes weekday vs weekend rates (e.g. Dec 4 Friday is $714, Dec 2 Wed is $494)."""
        dec_fri = date(2026, 12, 4)  # Friday (Thursday-Sunday interval: $714)
        dec_wed = date(2026, 12, 2)  # Wednesday (Monday-Wednesday interval: $494)
        self.assertEqual(self.client.get_rate_for_date(dec_fri), 714.0)
        self.assertEqual(self.client.get_rate_for_date(dec_wed), 494.0)

        jan_fri = date(2027, 1, 8)   # Friday ($964)
        jan_tue = date(2027, 1, 5)   # Tuesday ($578)
        self.assertEqual(self.client.get_rate_for_date(jan_fri), 964.0)
        self.assertEqual(self.client.get_rate_for_date(jan_tue), 578.0)

    def test_calendar_open_end_date_detection(self):
        """Verify KivoyaClient detects calendar open end date and closed start date."""
        from datetime import timedelta
        open_end = self.client.get_calendar_open_end_date()
        self.assertIsInstance(open_end, date)
        self.assertGreaterEqual(open_end, date(2027, 5, 31))

        closed_start = self.client.get_calendar_closed_start_date()
        self.assertEqual(closed_start, open_end + timedelta(days=1))

    def test_segments_calendar_open_tagging(self):
        """Verify CalendarSegmenter tags intervals in open calendar as open, and beyond as closed."""
        from datetime import timedelta
        segmenter = CalendarSegmenter(kivoya_client=self.client, lookahead_days=400)
        segments = segmenter.generate_unbooked_segments()

        open_segments = [s for s in segments if s.get("is_calendar_open")]
        closed_segments = [s for s in segments if not s.get("is_calendar_open")]

        self.assertGreater(len(open_segments), 0)
        open_end = self.client.get_calendar_open_end_date()
        cutoff_str = (open_end + timedelta(days=1)).strftime("%Y-%m-%d")

        # Check that open segments end on or before cutoff date
        self.assertTrue(all(s["check_out"] <= cutoff_str for s in open_segments))
        if closed_segments:
            self.assertTrue(all(s["check_out"] > cutoff_str for s in closed_segments))

    def test_kivoya_api_retry_and_backoff(self):
        """Verify _call_api retries on transient network errors with configurable backoff."""
        client = KivoyaClient()
        with patch.dict(os.environ, {"KIVOYA_RETRY_DELAY": "1.0"}):
            # Test case 1: Fails twice with TimeoutError, succeeds on 3rd attempt with backoff
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps({"data": {"result": "success"}}).encode("utf-8")
            mock_resp.__enter__.return_value = mock_resp
            mock_resp.__exit__.return_value = None

            with patch("urllib.request.urlopen", side_effect=[TimeoutError("Read timed out"), ConnectionResetError("Reset"), mock_resp]) as mock_urlopen:
                with patch("time.sleep") as mock_sleep:
                    res = client._call_api("DummyMethod", {})
                    self.assertEqual(res, {"result": "success"})
                    self.assertEqual(mock_urlopen.call_count, 3)
                    self.assertEqual(mock_sleep.call_count, 2)
                    mock_sleep.assert_any_call(1.0)
                    mock_sleep.assert_any_call(2.0)

            # Test case 2: Fails 3 times, raises last error
            with patch("urllib.request.urlopen", side_effect=TimeoutError("Persistent timeout")) as mock_urlopen:
                with patch("time.sleep"):
                    with self.assertRaises(TimeoutError):
                        client._call_api("DummyMethod", {})
                    self.assertEqual(mock_urlopen.call_count, 3)

        # Test case 3: Zero-sleep bypass when KIVOYA_RETRY_DELAY=0.0
        with patch.dict(os.environ, {"KIVOYA_RETRY_DELAY": "0.0"}):
            with patch("urllib.request.urlopen", side_effect=[TimeoutError("Timeout"), mock_resp]):
                with patch("time.sleep") as mock_sleep:
                    res = client._call_api("DummyMethod", {})
                    self.assertEqual(res, {"result": "success"})
                    self.assertEqual(mock_sleep.call_count, 0)

    def test_kivoya_blocked_periods_cache_fallback(self):
        """Verify get_blocked_periods falls back to local cache file when API fails."""
        client = KivoyaClient()
        cached_data = [
            {"startdate": "10/01/2026", "enddate": "10/05/2026", "reason": "Cached Res #1"}
        ]
        KivoyaClient._cache_blocked_periods = None
        try:
            with patch.object(client, "_call_api", side_effect=Exception("API offline")), \
                 patch.object(Path, "exists", return_value=True), \
                 patch.object(Path, "read_text", return_value=json.dumps(cached_data)):
                blocked = client.get_blocked_periods(force_refresh=True)
                self.assertEqual(len(blocked), 1)
                self.assertEqual(blocked[0]["reason"], "Cached Res #1")
                self.assertEqual(blocked[0]["start_dt"], date(2026, 10, 1))
                self.assertEqual(blocked[0]["end_dt"], date(2026, 10, 5))
        finally:
            KivoyaClient._cache_blocked_periods = self.blocked

    def test_kivoya_blocked_periods_reservations_store_fallback(self):
        """Verify get_blocked_periods falls back to reservations.json if cache is missing and API fails."""
        client = KivoyaClient()
        res_store = {
            "reservations": [
                {
                    "confirmation_id": "RES_CANCELLED",
                    "start_date": "2026-10-10",
                    "end_date": "2026-10-15",
                    "status_name": "Cancelled",
                },
                {
                    "confirmation_id": "RES999",
                    "start_date": "2026-11-10",
                    "end_date": "2026-11-15",
                    "status_name": "Confirmed",
                }
            ]
        }
        KivoyaClient._cache_blocked_periods = None
        try:
            def fake_exists(path_obj):
                return "reservations.json" in str(path_obj)

            def fake_read_text(path_obj, encoding="utf-8"):
                if "reservations.json" in str(path_obj):
                    return json.dumps(res_store)
                raise FileNotFoundError()

            with patch.object(client, "_call_api", side_effect=Exception("API offline")), \
                 patch.object(Path, "exists", fake_exists), \
                 patch.object(Path, "read_text", fake_read_text):
                blocked = client.get_blocked_periods(force_refresh=True)
                self.assertEqual(len(blocked), 1)
                self.assertEqual(blocked[0]["reason"], "Reservation #RES999")
                self.assertEqual(blocked[0]["start_dt"], date(2026, 11, 10))
                # Streamline checkout date is 2026-11-15; last occupied night is 2026-11-14
                self.assertEqual(blocked[0]["end_dt"], date(2026, 11, 14))
                self.assertEqual(blocked[0]["enddate"], "11/14/2026")
        finally:
            KivoyaClient._cache_blocked_periods = self.blocked

    def test_kivoya_daily_availability_reconstruction_fallback(self):
        """Verify get_daily_availability reconstructs from blocked periods when raw API fails."""
        client = KivoyaClient()
        KivoyaClient._cache_daily_availability = None
        fake_blocked = [
            {
                "startdate": "10/01/2026",
                "enddate": "10/03/2026",
                "reason": "Test Block",
                "start_dt": date(2026, 10, 1),
                "end_dt": date(2026, 10, 3),
            }
        ]
        try:
            with patch.object(client, "_call_api", side_effect=Exception("API down")), \
                 patch.object(client, "get_blocked_periods", return_value=fake_blocked):
                avail = client.get_daily_availability(force_refresh=True)
                self.assertIn(date(2026, 10, 1), avail)
                self.assertFalse(avail[date(2026, 10, 1)]["available"])
                self.assertFalse(avail[date(2026, 10, 2)]["available"])
                self.assertFalse(avail[date(2026, 10, 3)]["available"])
                self.assertTrue(avail[date(2026, 10, 4)]["available"])
        finally:
            KivoyaClient._cache_daily_availability = None

    def test_kivoya_api_null_safety(self):
        """Verify _call_api, get_blocked_periods, and get_daily_availability handle null/malformed payloads safely."""
        client = KivoyaClient()
        # 1. _call_api returns {} when "data" is null
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"data": None}).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None
        with patch("urllib.request.urlopen", return_value=mock_resp):
            res = client._call_api("TestNull", {})
            self.assertEqual(res, {})

        # 2. get_blocked_periods handles {"blocked_period": None} without TypeError
        KivoyaClient._cache_blocked_periods = None
        try:
            with patch.object(client, "_call_api", return_value={"blocked_period": None}), \
                 patch.object(Path, "exists", return_value=False):
                res = client.get_blocked_periods(force_refresh=True)
                self.assertEqual(res, [])
        finally:
            KivoyaClient._cache_blocked_periods = self.blocked

        # 3. get_daily_availability handles {"range": None} or None response without AttributeError
        KivoyaClient._cache_daily_availability = None
        try:
            with patch.object(client, "_call_api", return_value={"range": None, "availability": None}), \
                 patch.object(client, "get_blocked_periods", return_value=[]):
                avail = client.get_daily_availability(force_refresh=True)
                self.assertIsInstance(avail, dict)
                self.assertGreater(len(avail), 300)
        finally:
            KivoyaClient._cache_daily_availability = None


if __name__ == "__main__":
    unittest.main()

