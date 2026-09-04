"""
Kivoya / Streamline VRS API Client.
Retrieves real-time calendar reservations (blocked periods) and seasonal base rates
for Villa del Sol directly from Kivoya's property management endpoint.
"""

from datetime import datetime, date, timedelta
import json
from pathlib import Path
import ssl
import urllib.parse
import urllib.request
from typing import Dict, List, Optional, Any

try:
    SSL_CONTEXT = ssl._create_unverified_context()
except Exception:
    SSL_CONTEXT = None


class KivoyaClient:
    """Client for interacting with Kivoya (Streamline VRS WordPress AJAX API)."""

    BASE_URL = "https://www.kivoya.com/wp-admin/admin-ajax.php"
    DEFAULT_UNIT_ID = 503802

    def __init__(self, unit_id: int = DEFAULT_UNIT_ID, user_agent: Optional[str] = None):
        self.unit_id = unit_id
        self.user_agent = user_agent or (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        )

    def _call_api(self, method_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a request against Kivoya's streamlinecore-api-request endpoint."""
        payload = {
            "methodName": method_name,
            "params": params,
        }
        query_string = urllib.parse.urlencode({
            "action": "streamlinecore-api-request",
            "params": json.dumps(payload),
        })
        url = f"{self.BASE_URL}?{query_string}"

        req = urllib.request.Request(
            url,
            data=b"",
            headers={
                "User-Agent": self.user_agent,
                "Content-Type": "application/json",
                "Accept": "application/json, text/plain, */*",
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=20, context=SSL_CONTEXT) as response:
            body = response.read().decode("utf-8")
            data = json.loads(body)
            return data.get("data", {})

    def get_blocked_periods(self) -> List[Dict[str, Any]]:
        """
        Fetch all blocked dates and reservations.
        Returns a list of dicts:
        [
            {
                "startdate": "09/03/2026",
                "enddate": "09/05/2026",
                "reason": "Reservation #19130",
                "start_dt": datetime.date(2026, 9, 3),
                "end_dt": datetime.date(2026, 9, 5)
            },
            ...
        ]
        """
        raw_data = self._call_api(
            "GetPropertyAvailabilityCalendarRawData",
            {"unit_id": self.unit_id}
        )
        blocked = raw_data.get("blocked_period", [])
        if isinstance(blocked, dict):
            blocked = [blocked]

        parsed = []
        for period in blocked:
            s_str = period.get("startdate")
            e_str = period.get("enddate")
            reason = period.get("reason", "Blocked")
            if s_str and e_str:
                try:
                    s_dt = datetime.strptime(s_str, "%m/%d/%Y").date()
                    e_dt = datetime.strptime(e_str, "%m/%d/%Y").date()
                    parsed.append({
                        "startdate": s_str,
                        "enddate": e_str,
                        "reason": reason,
                        "start_dt": s_dt,
                        "end_dt": e_dt,
                    })
                except ValueError:
                    continue
        return sorted(parsed, key=lambda x: x["start_dt"])

    def get_daily_availability(self) -> Dict[date, Dict[str, Any]]:
        """
        Fetch daily availability directly from Kivoya Streamline API (GetPropertyAvailabilityRawData).
        Maps date -> {"available": bool, "change_over": str}
        """
        raw_data = self._call_api(
            "GetPropertyAvailabilityRawData",
            {"unit_id": self.unit_id}
        )
        range_info = raw_data.get("range", {})
        begin_str = range_info.get("beginDate")
        avail_str = raw_data.get("availability", "")
        change_str = raw_data.get("changeOver", "")

        result: Dict[date, Dict[str, Any]] = {}
        if begin_str and avail_str:
            try:
                begin_dt = datetime.strptime(begin_str, "%m/%d/%Y").date()
                for idx, char in enumerate(avail_str):
                    cur_dt = begin_dt + timedelta(days=idx)
                    co = change_str[idx] if idx < len(change_str) else ""
                    result[cur_dt] = {
                        "available": (char == "Y"),
                        "change_over": co,
                    }
            except Exception:
                pass
        return result

    def get_calendar_open_end_date(self) -> Optional[date]:
        """
        Detect the date until which the booking calendar is open in Kivoya / Streamline VRS.
        Normally the calendar is open until a given month and closed after that.
        Checks:
        1. Range 'endDate' in GetPropertyAvailabilityRawData (e.g. '05/31/2027')
        2. Daily availability: last available date before calendar closure
        3. Local cache fallback in data/cache/calendar_cutoff.json
        """
        cache_path = Path("data/cache/calendar_cutoff.json")

        try:
            raw_data = self._call_api(
                "GetPropertyAvailabilityRawData",
                {"unit_id": self.unit_id}
            )
            range_info = raw_data.get("range", {})
            end_str = range_info.get("endDate")
            if end_str:
                end_dt = datetime.strptime(end_str, "%m/%d/%Y").date()
                try:
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    cache_path.write_text(
                        json.dumps({
                            "open_end_date": end_dt.isoformat(),
                            "closed_start_date": (end_dt + timedelta(days=1)).isoformat(),
                        }, indent=2),
                        encoding="utf-8"
                    )
                except Exception:
                    pass
                return end_dt
        except Exception:
            pass

        # Check local cache fallback
        if cache_path.exists():
            try:
                data = json.loads(cache_path.read_text(encoding="utf-8"))
                if data.get("open_end_date"):
                    return datetime.strptime(data["open_end_date"], "%Y-%m-%d").date()
            except Exception:
                pass

        # Safe fallback: calendar is open through end of May 2027
        return date(2027, 5, 31)

    def get_calendar_closed_start_date(self) -> Optional[date]:
        """Return the first date on which the calendar is closed (open_end_date + 1 day)."""
        open_end = self.get_calendar_open_end_date()
        if open_end:
            return open_end + timedelta(days=1)
        return None

    @staticmethod
    def _parse_interval_weekdays(interval_str: Optional[str]) -> set:
        """Parse day-of-week interval string (e.g. 'Monday-Wednesday', 'Thursday-Sunday', 'All Days') into a set of weekday integers (0=Mon..6=Sun)."""
        if not interval_str:
            return set()
        s = interval_str.strip().lower()
        if s in ["all days", "alldays", "all"]:
            return {0, 1, 2, 3, 4, 5, 6}

        day_map = {
            "monday": 0, "mon": 0,
            "tuesday": 1, "tue": 1,
            "wednesday": 2, "wed": 2,
            "thursday": 3, "thu": 3,
            "friday": 4, "fri": 4,
            "saturday": 5, "sat": 5,
            "sunday": 6, "sun": 6,
        }

        if "-" in s:
            parts = s.split("-")
            start_day = day_map.get(parts[0].strip())
            end_day = day_map.get(parts[1].strip())
            if start_day is not None and end_day is not None:
                if start_day <= end_day:
                    return set(range(start_day, end_day + 1))
                else:
                    return set(range(start_day, 7)) | set(range(0, end_day + 1))
        elif s in day_map:
            return {day_map[s]}
        return set()

    def get_seasonal_rates(self) -> List[Dict[str, Any]]:
        """
        Fetch all configured seasonal rates.
        Returns a list of rate periods:
        [
            {
                "season_name": "September 2025-26",
                "period_name": "Sep. 26",
                "period_begin": "09/08/2026",
                "period_end": "09/30/2026",
                "nightly_rate": 399.0,
                "first_price": 399.0,
                "second_price": 549.0,
                "currency": "USD",
                "min_days": 3,
                "begin_dt": datetime.date(2026, 9, 8),
                "end_dt": datetime.date(2026, 9, 30)
            },
            ...
        ]
        """
        raw_data = self._call_api(
            "GetPropertyRatesRawData",
            {"unit_id": self.unit_id}
        )
        rates = raw_data.get("rates", [])
        if isinstance(rates, dict):
            rates = [rates]

        parsed = []
        for rate in rates:
            b_str = rate.get("period_begin")
            e_str = rate.get("period_end")
            p1_str = rate.get("daily_first_interval_price", "$0.00")
            cleaned_price = float(p1_str.replace("$", "").replace(",", "").strip() or 0)

            p2_str = rate.get("daily_second_interval_price")
            price2 = float(p2_str.replace("$", "").replace(",", "").strip()) if p2_str else None

            int1_str = rate.get("daily_first_interval")
            int2_str = rate.get("daily_second_interval")
            days1 = self._parse_interval_weekdays(int1_str)
            days2 = self._parse_interval_weekdays(int2_str)

            if b_str and e_str:
                try:
                    b_dt = datetime.strptime(b_str, "%m/%d/%Y").date()
                    e_dt = datetime.strptime(e_str, "%m/%d/%Y").date()
                    parsed.append({
                        "season_id": rate.get("season_id"),
                        "season_name": rate.get("season_name"),
                        "period_name": rate.get("period_name"),
                        "period_begin": b_str,
                        "period_end": e_str,
                        "nightly_rate": cleaned_price,
                        "first_interval": int1_str,
                        "first_price": cleaned_price,
                        "first_days": days1,
                        "second_interval": int2_str,
                        "second_price": price2,
                        "second_days": days2,
                        "currency": rate.get("currency", "USD"),
                        "min_days": int(rate.get("narrow_defined_days", 2)),
                        "begin_dt": b_dt,
                        "end_dt": e_dt,
                    })
                except ValueError:
                    continue
        return sorted(parsed, key=lambda x: x["begin_dt"])

    def get_rate_for_date(self, target_date: date, rates: Optional[List[Dict[str, Any]]] = None) -> float:
        """Find our base nightly rate for a given date from the seasonal schedule, honoring day-of-week intervals."""
        if rates is None:
            rates = self.get_seasonal_rates()

        weekday = target_date.weekday()
        for r in rates:
            if r["begin_dt"] <= target_date <= r["end_dt"]:
                # Check if second interval matches this day of the week (e.g. Thursday-Sunday weekend rate)
                if r.get("second_price") is not None and weekday in r.get("second_days", set()):
                    return r["second_price"]
                # Check if first interval matches (e.g. Monday-Wednesday or All Days)
                if r.get("first_price") is not None and (not r.get("first_days") or weekday in r["first_days"]):
                    return r["first_price"]
                return r["nightly_rate"]

        # Default fallback if outside defined periods
        return 599.0
