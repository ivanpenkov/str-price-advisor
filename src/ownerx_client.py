"""
Streamline OwnerX API Client.
Authenticates against Streamline OwnerX (https://ownerx.streamlinevrs.com)
and retrieves all past and future reservations, including full owner
commission breakdowns and blocked calendar periods.
"""

from datetime import datetime, date, timedelta
import http.cookiejar
import json
import os
from pathlib import Path
import ssl
from typing import Dict, List, Optional, Any
import urllib.parse
import urllib.request


try:
    SSL_CONTEXT = ssl._create_unverified_context()
except Exception:
    SSL_CONTEXT = None



def _load_env_file():
    env_path = Path('.env')
    if not env_path.exists():
        env_path = Path(__file__).resolve().parent.parent / '.env'
    if env_path.exists():
        try:
            with open(env_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        k, v = line.split('=', 1)
                        k = k.strip()
                        v = v.strip().strip("'\"")
                        if k not in os.environ:
                            os.environ[k] = v
        except Exception:
            pass

_load_env_file()

class OwnerXClient:
    """Client for Streamline OwnerX JSON API."""

    BASE_URL = "https://ownerx.streamlinevrs.com"

    def __init__(
        self,
        username: Optional[str] = None,
        password: Optional[str] = None,
        user_agent: Optional[str] = None,
    ):
        self.username = username or os.getenv("STREAMLINE_OWNER_USERNAME", "")
        self.password = password or os.getenv("STREAMLINE_OWNER_PASSWORD", "")
        self.user_agent = user_agent or (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        )
        self.cookie_jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=SSL_CONTEXT),
            urllib.request.HTTPCookieProcessor(self.cookie_jar),
        )
        self.processor_id: Optional[int] = None
        self.company_id: Optional[int] = None
        self.xsrf_token: Optional[str] = None
        self._authenticated = False

    def _get_cookie(self, name: str) -> Optional[str]:
        """Extract a cookie value by name from the active cookie jar."""
        for cookie in self.cookie_jar:
            if cookie.name == name:
                return cookie.value
        return None

    def _fetch_csrf_token(self) -> str:
        """Acquire the initial CSRF token and cookie from /csrf-token."""
        url = f"{self.BASE_URL}/csrf-token"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "*/*",
            },
        )
        with self.opener.open(req, timeout=15) as resp:
            token = resp.read().decode("utf-8")

        self.xsrf_token = self._get_cookie("XSRF_TOKEN") or token.strip()
        return self.xsrf_token

    def authenticate(self) -> Dict[str, Any]:
        """
        Authenticate against OwnerX using username/password.
        Handles the CSRF handshake and session cookie persistence.
        """
        if not self.username or not self.password:
            raise ValueError(
                "Streamline OwnerX username and password must be provided "
                "or set in STREAMLINE_OWNER_USERNAME and STREAMLINE_OWNER_PASSWORD env variables."
            )

        self._fetch_csrf_token()

        url = f"{self.BASE_URL}/api/authenticateProcessor"
        payload = {
            "methodName": "AuthenticateProcessorMobile",
            "params": {
                "email": self.username,
                "password": self.password,
                "return_reservation_types_for_owner": "YES",
            },
        }

        headers = {
            "Content-Type": "application/json",
            "User-Agent": self.user_agent,
            "Accept": "application/json, text/plain, */*",
            "X-XSRF-TOKEN": self.xsrf_token or "",
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        with self.opener.open(req, timeout=20) as resp:
            raw_text = resp.read().decode("utf-8")
            data = json.loads(raw_text)

        rotated_xsrf = self._get_cookie("XSRF_TOKEN")
        if rotated_xsrf:
            self.xsrf_token = rotated_xsrf

        processor_info = data.get("data", {}).get("processor", {})
        self.processor_id = processor_info.get("id")
        self.company_id = processor_info.get("company_id")
        if not self.processor_id:
            msg = data.get("data", {}).get("message") or "Unknown authentication failure"
            raise RuntimeError(f"OwnerX authentication failed: {msg}")

        self._authenticated = True
        return data.get("data", {})

    def _api_call(self, method_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute an authenticated call against /api/streamline."""
        if not self._authenticated:
            self.authenticate()

        url = f"{self.BASE_URL}/api/streamline"
        payload = {
            "methodName": method_name,
            "params": params,
        }

        headers = {
            "Content-Type": "application/json",
            "User-Agent": self.user_agent,
            "Accept": "application/json, text/plain, */*",
            "X-XSRF-TOKEN": self.xsrf_token or "",
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        with self.opener.open(req, timeout=30) as resp:
            raw_text = resp.read().decode("utf-8")
            return json.loads(raw_text).get("data", {})

    def fetch_raw_reservations(
        self,
        arriving_after: Optional[str] = None,
        page_results_number: int = 100,
        max_pages: int = 20,
    ) -> List[Dict[str, Any]]:
        """
        Fetch reservations from Streamline OwnerX with automatic pagination.
        """
        if not self._authenticated:
            self.authenticate()

        arriving_str: Optional[str] = None
        if arriving_after:
            if "-" in arriving_after:
                try:
                    dt = datetime.strptime(arriving_after, "%Y-%m-%d")
                    arriving_str = dt.strftime("%m/%d/%Y")
                except ValueError:
                    arriving_str = arriving_after
            else:
                arriving_str = arriving_after

        all_reservations: List[Dict[str, Any]] = []
        page = 1

        while page <= max_pages:
            params: Dict[str, Any] = {
                "processor_id": self.processor_id,
                "show_all": 1,
                "show_cancelled": 1,
                "show_commission_information": 1,
                "all_units": 1,
                "page_number": page,
                "page_results_number": page_results_number,
            }
            if arriving_str:
                params["arriving_after"] = arriving_str
                params["return_full"] = "yes"

            data = self._api_call("GetReservationsForOwner", params)
            pagination = data.get("pagination", {})
            reservations = data.get("reservations", [])
            if isinstance(reservations, dict) and "reservation" in reservations:
                reservations = reservations["reservation"]

            if not isinstance(reservations, list):
                reservations = [reservations] if reservations else []

            all_reservations.extend(reservations)

            total_pages = pagination.get("total_pages", 1)
            if page >= total_pages or not reservations:
                break
            page += 1

        if arriving_after:
            # Client-side filter: include reservations ending on or after arriving_after,
            # or created on or after arriving_after (catches recent corrections and new bookings)
            try:
                if "-" in arriving_after:
                    cutoff_dt = datetime.strptime(arriving_after, "%Y-%m-%d").date()
                else:
                    cutoff_dt = datetime.strptime(arriving_after, "%m/%d/%Y").date()
            except ValueError:
                cutoff_dt = None

            if cutoff_dt:
                filtered = []
                for r in all_reservations:
                    end_str = r.get("enddate")
                    try:
                        end_dt = datetime.strptime(end_str, "%m/%d/%Y").date() if end_str else None
                    except ValueError:
                        end_dt = None
                    if end_dt and end_dt >= cutoff_dt:
                        filtered.append(r)
                return filtered

        return all_reservations

    @staticmethod
    def normalize_reservation(raw: Dict[str, Any], today: Optional[date] = None) -> Dict[str, Any]:
        """
        Normalize a raw Streamline reservation dict into a clean, structured schema.
        Classifies 'is_future' as 1 if end_date > today, else 0 (past).
        """
        if today is None:
            today = date.today()

        res_id = raw.get("id")
        confirmation_id = raw.get("confirmation_id")
        creation_date_str = raw.get("creation_date", "")

        start_str = raw.get("startdate", "")
        end_str = raw.get("enddate", "")

        start_date: Optional[str] = None
        end_date: Optional[str] = None
        start_dt: Optional[date] = None
        end_dt: Optional[date] = None

        if start_str:
            try:
                start_dt = datetime.strptime(start_str, "%m/%d/%Y").date()
                start_date = start_dt.strftime("%Y-%m-%d")
            except ValueError:
                start_date = start_str

        if end_str:
            try:
                end_dt = datetime.strptime(end_str, "%m/%d/%Y").date()
                end_date = end_dt.strftime("%Y-%m-%d")
            except ValueError:
                end_date = end_str

        days_number = raw.get("days_number")
        if days_number is None and start_dt and end_dt:
            days_number = (end_dt - start_dt).days
        days_number = int(days_number or 0)

        comm = raw.get("commission_information") or {}
        owner_payout = float(comm.get("owner_commission_amount") or 0.0)
        mgmt_fee = float(comm.get("management_commission_amount") or 0.0)
        gross_rent = round(owner_payout + mgmt_fee, 2)

        # 'past' if stay completed (end_date <= today), 'future' if end_date > today
        if end_dt:
            is_future = 1 if end_dt > today else 0
        else:
            is_future = 1

        return {
            "id": res_id,
            "confirmation_id": confirmation_id,
            "creation_date": creation_date_str,
            "start_date": start_date,
            "end_date": end_date,
            "days_number": days_number,
            "type_id": raw.get("type_id"),
            "type_name": raw.get("type_name", ""),
            "type_description": raw.get("type_description", ""),
            "status_name": raw.get("status_name", "Booked"),
            "occupants": raw.get("occupants", 0),
            "occupants_small": raw.get("occupants_small", 0),
            "pets": raw.get("pets", 0),
            "unit_id": raw.get("unit_id"),
            "unit_name": raw.get("unit_name", ""),
            "owner_payout": owner_payout,
            "management_fee": mgmt_fee,
            "gross_rent": gross_rent,
            "is_future": is_future,
            "raw_json": json.dumps(raw),
        }
