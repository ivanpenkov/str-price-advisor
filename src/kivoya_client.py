"""
Kivoya / Streamline VRS API Client.
Retrieves real-time calendar reservations (blocked periods) and seasonal base rates
for Villa del Sol directly from Kivoya's property management endpoint.
"""

from datetime import datetime, date
import json
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
                "nightly_rate": 599.0,
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
            price_str = rate.get("daily_first_interval_price", "$0.00")
            cleaned_price = float(price_str.replace("$", "").replace(",", "").strip() or 0)

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
                        "currency": rate.get("currency", "USD"),
                        "min_days": int(rate.get("narrow_defined_days", 2)),
                        "begin_dt": b_dt,
                        "end_dt": e_dt,
                    })
                except ValueError:
                    continue
        return sorted(parsed, key=lambda x: x["begin_dt"])

    def get_rate_for_date(self, target_date: date, rates: Optional[List[Dict[str, Any]]] = None) -> float:
        """Find our base nightly rate for a given date from the seasonal schedule."""
        if rates is None:
            rates = self.get_seasonal_rates()

        for r in rates:
            if r["begin_dt"] <= target_date <= r["end_dt"]:
                return r["nightly_rate"]

        # Default fallback if outside defined periods
        return 599.0
