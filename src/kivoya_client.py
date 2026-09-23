"""
Kivoya / Streamline VRS API Client.
Retrieves real-time calendar reservations (blocked periods) and seasonal base rates
for Villa del Sol directly from Kivoya's property management endpoint.
"""

from datetime import datetime, date, timedelta
import json
import logging
import os
from pathlib import Path
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import http.client
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

try:
    SSL_CONTEXT = ssl._create_unverified_context()
except Exception:
    SSL_CONTEXT = None


class KivoyaClient:
    """Client for interacting with Kivoya (Streamline VRS WordPress AJAX API)."""

    BASE_URL = "https://www.kivoya.com/wp-admin/admin-ajax.php"
    DEFAULT_UNIT_ID = 503802

    # Class-level in-memory cache to prevent redundant HTTP requests within the same process
    _cache_seasonal_rates: Optional[List[Dict[str, Any]]] = None
    _cache_blocked_periods: Optional[List[Dict[str, Any]]] = None
    _cache_daily_availability: Optional[Dict[date, Dict[str, Any]]] = None
    _cache_calendar_open_end_date: Optional[date] = None

    @classmethod
    def clear_cache(cls):
        """Clear all in-memory caches."""
        cls._cache_seasonal_rates = None
        cls._cache_blocked_periods = None
        cls._cache_daily_availability = None
        cls._cache_calendar_open_end_date = None

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

        retry_delay = float(os.getenv("KIVOYA_RETRY_DELAY", "2.0"))
        last_error: Optional[Exception] = None

        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=30, context=SSL_CONTEXT) as response:
                    body = response.read().decode("utf-8")
                    data = json.loads(body)
                    res = data.get("data")
                    return res if isinstance(res, dict) else {}
            except (urllib.error.URLError, TimeoutError, socket.timeout, http.client.HTTPException, ConnectionResetError, OSError) as e:
                last_error = e
                if attempt < 2:
                    sleep_time = retry_delay * (attempt + 1)
                    logger.warning(
                        f"Kivoya API {method_name} error: {e} (attempt {attempt + 1}/3). "
                        f"Retrying in {sleep_time:.1f}s..."
                    )
                    if sleep_time > 0:
                        time.sleep(sleep_time)

        if last_error:
            raise last_error
        return {}

    def get_blocked_periods(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """
        Fetch all blocked dates and reservations.
        Falls back to local cache (data/cache/kivoya_blocked_periods.json) or
        synced reservations store (data/reservations.json) if the API call fails.
        """
        if not force_refresh and KivoyaClient._cache_blocked_periods is not None:
            return KivoyaClient._cache_blocked_periods

        cache_path = Path("data/cache/kivoya_blocked_periods.json")
        blocked = []
        try:
            raw_data = self._call_api(
                "GetPropertyAvailabilityCalendarRawData",
                {"unit_id": self.unit_id}
            )
            raw_blocked = raw_data.get("blocked_period") if isinstance(raw_data, dict) else []
            if isinstance(raw_blocked, dict):
                blocked = [raw_blocked]
            elif isinstance(raw_blocked, list):
                blocked = raw_blocked
            else:
                blocked = []
        except Exception as e:
            logger.warning(f"Failed to fetch blocked periods from Kivoya API: {e}. Attempting fallback...")
            blocked = []

        # If API call returned no blocked periods or failed, attempt fallbacks
        if not blocked:
            # Fallback 1: Local cache file
            if cache_path.exists():
                try:
                    cached_items = json.loads(cache_path.read_text(encoding="utf-8"))
                    reconstituted = []
                    for item in cached_items:
                        r = dict(item)
                        r["start_dt"] = datetime.strptime(r["startdate"], "%m/%d/%Y").date()
                        r["end_dt"] = datetime.strptime(r["enddate"], "%m/%d/%Y").date()
                        reconstituted.append(r)
                    if reconstituted:
                        sorted_reconstituted = sorted(reconstituted, key=lambda x: x["start_dt"])
                        KivoyaClient._cache_blocked_periods = sorted_reconstituted
                        logger.info(f"Reconstituted {len(sorted_reconstituted)} blocked periods from local cache file.")
                        return sorted_reconstituted
                except Exception:
                    pass

            # Fallback 2: Streamline reservations store (data/reservations.json)
            res_json_path = Path("data/reservations.json")
            if res_json_path.exists():
                try:
                    res_data = json.loads(res_json_path.read_text(encoding="utf-8"))
                    reservations = res_data.get("reservations", [])
                    reconstituted = []
                    for res in reservations:
                        if str(res.get("status_name", "")).strip().lower() in ("cancelled", "canceled"):
                            continue
                        s_iso = res.get("start_date")
                        e_iso = res.get("end_date")
                        if s_iso and e_iso:
                            s_dt = datetime.strptime(s_iso, "%Y-%m-%d").date()
                            e_dt = datetime.strptime(e_iso, "%Y-%m-%d").date()
                            # Streamline end_date is checkout day; end_dt is the last occupied night
                            last_night = max(s_dt, e_dt - timedelta(days=1))
                            conf_id = res.get("confirmation_id") or res.get("id") or ""
                            reconstituted.append({
                                "startdate": s_dt.strftime("%m/%d/%Y"),
                                "enddate": last_night.strftime("%m/%d/%Y"),
                                "reason": f"Reservation #{conf_id}" if conf_id else "Reservation",
                                "start_dt": s_dt,
                                "end_dt": last_night,
                            })
                    if reconstituted:
                        sorted_reconstituted = sorted(reconstituted, key=lambda x: x["start_dt"])
                        KivoyaClient._cache_blocked_periods = sorted_reconstituted
                        logger.info(f"Reconstituted {len(sorted_reconstituted)} blocked periods from reservations store.")
                        return sorted_reconstituted
                except Exception:
                    pass

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
        sorted_blocked = sorted(parsed, key=lambda x: x["start_dt"])
        KivoyaClient._cache_blocked_periods = sorted_blocked

        # Cache valid blocked periods to disk for future resilient fallbacks
        if sorted_blocked:
            try:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                serializable = [
                    {
                        "startdate": p["startdate"],
                        "enddate": p["enddate"],
                        "reason": p["reason"],
                    }
                    for p in sorted_blocked
                ]
                cache_path.write_text(json.dumps(serializable, indent=2), encoding="utf-8")
            except Exception:
                pass

        return sorted_blocked

    def get_daily_availability(self, force_refresh: bool = False) -> Dict[date, Dict[str, Any]]:
        """
        Fetch daily availability directly from Kivoya Streamline API (GetPropertyAvailabilityRawData).
        Maps date -> {"available": bool, "change_over": str}
        """
        if not force_refresh and KivoyaClient._cache_daily_availability is not None:
            return KivoyaClient._cache_daily_availability

        raw_data = {}
        begin_str = None
        avail_str = ""
        change_str = ""
        result: Dict[date, Dict[str, Any]] = {}
        try:
            raw_data = self._call_api(
                "GetPropertyAvailabilityRawData",
                {"unit_id": self.unit_id}
            ) or {}
            if isinstance(raw_data, dict):
                range_info = raw_data.get("range") or {}
                if isinstance(range_info, dict):
                    begin_str = range_info.get("beginDate")
                avail_str = raw_data.get("availability") or ""
                change_str = raw_data.get("changeOver") or ""

            if begin_str and avail_str:
                begin_dt = datetime.strptime(begin_str, "%m/%d/%Y").date()
                for idx, char in enumerate(avail_str):
                    cur_dt = begin_dt + timedelta(days=idx)
                    co = change_str[idx] if idx < len(change_str) else ""
                    result[cur_dt] = {
                        "available": (char == "Y"),
                        "change_over": co,
                    }
        except Exception as e:
            logger.warning(f"Failed to fetch daily availability from Kivoya API: {e}. Reconstructing from blocked periods...")

        # Fallback: if raw_data was empty or failed, construct from get_blocked_periods
        if not result:
            try:
                blocked_periods = self.get_blocked_periods()
                booked_dates = set()
                for bp in blocked_periods:
                    cur = bp["start_dt"]
                    while cur <= bp["end_dt"]:
                        booked_dates.add(cur)
                        cur += timedelta(days=1)
                today = date.today()
                for i in range(365):
                    cur_dt = today + timedelta(days=i)
                    result[cur_dt] = {
                        "available": (cur_dt not in booked_dates),
                        "change_over": "",
                    }
            except Exception:
                pass

        KivoyaClient._cache_daily_availability = result
        return result

    def get_calendar_open_end_date(self, force_refresh: bool = False) -> Optional[date]:
        """
        Detect the date until which the booking calendar is open in Kivoya / Streamline VRS.
        Normally the calendar is open until a given month and closed after that.
        Checks:
        1. Range 'endDate' in GetPropertyAvailabilityRawData (e.g. '05/31/2027')
        2. Daily availability: last available date before calendar closure
        3. Local cache fallback in data/cache/calendar_cutoff.json
        """
        if not force_refresh and KivoyaClient._cache_calendar_open_end_date is not None:
            return KivoyaClient._cache_calendar_open_end_date

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
                KivoyaClient._cache_calendar_open_end_date = end_dt
                return end_dt
        except Exception:
            pass

        # Check local cache fallback
        if cache_path.exists():
            try:
                data = json.loads(cache_path.read_text(encoding="utf-8"))
                if data.get("open_end_date"):
                    cached_dt = datetime.strptime(data["open_end_date"], "%Y-%m-%d").date()
                    KivoyaClient._cache_calendar_open_end_date = cached_dt
                    return cached_dt
            except Exception:
                pass

        # Safe fallback: calendar is open through end of May 2027
        KivoyaClient._cache_calendar_open_end_date = date(2027, 5, 31)
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

    def get_seasonal_rates(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
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
        if not force_refresh and KivoyaClient._cache_seasonal_rates is not None:
            return KivoyaClient._cache_seasonal_rates

        cache_path = Path("data/cache/kivoya_seasonal_rates.json")
        rates = []
        try:
            raw_data = self._call_api(
                "GetPropertyRatesRawData",
                {"unit_id": self.unit_id}
            )
            rates = raw_data.get("rates", [])
            if isinstance(rates, dict):
                rates = [rates]
        except Exception:
            rates = []

        # If API call returned no rates or failed, try loading from local cache
        if not rates and cache_path.exists():
            try:
                cached_items = json.loads(cache_path.read_text(encoding="utf-8"))
                reconstituted = []
                for item in cached_items:
                    r = dict(item)
                    r["begin_dt"] = datetime.strptime(r["begin_dt"], "%Y-%m-%d").date()
                    r["end_dt"] = datetime.strptime(r["end_dt"], "%Y-%m-%d").date()
                    r["first_days"] = set(r.get("first_days") or [])
                    r["second_days"] = set(r.get("second_days") or [])
                    reconstituted.append(r)
                if reconstituted:
                    sorted_reconstituted = sorted(reconstituted, key=lambda x: x["begin_dt"])
                    KivoyaClient._cache_seasonal_rates = sorted_reconstituted
                    return sorted_reconstituted
            except Exception:
                pass

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

        sorted_rates = sorted(parsed, key=lambda x: x["begin_dt"])
        if sorted_rates:
            try:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                serializable = []
                for r in sorted_rates:
                    item = dict(r)
                    item["begin_dt"] = item["begin_dt"].isoformat()
                    item["end_dt"] = item["end_dt"].isoformat()
                    item["first_days"] = list(item["first_days"]) if item["first_days"] else []
                    item["second_days"] = list(item["second_days"]) if item["second_days"] else []
                    serializable.append(item)
                cache_path.write_text(json.dumps(serializable, indent=2), encoding="utf-8")
            except Exception:
                pass

        KivoyaClient._cache_seasonal_rates = sorted_rates
        return sorted_rates

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

        # Check if rate exists in rate snapshots (for backfilled historical dates)
        try:
            if not hasattr(self, "_snapshot_rates_cache") or self._snapshot_rates_cache is None:
                from src.database import get_db_connection
                conn = get_db_connection()
                try:
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT calendar_date, nightly_rate, MAX(snapshot_date)
                        FROM property_rate_snapshots
                        GROUP BY calendar_date
                    """)
                    cache = {}
                    for row in cursor.fetchall():
                        c_date = row[0] if isinstance(row, (list, tuple)) else row["calendar_date"]
                        n_rate = row[1] if isinstance(row, (list, tuple)) else row["nightly_rate"]
                        if c_date and n_rate is not None:
                            cache[str(c_date)] = float(n_rate)
                    self._snapshot_rates_cache = cache
                finally:
                    conn.close()

            if getattr(self, "_snapshot_rates_cache", None):
                t_str = target_date.strftime("%Y-%m-%d")
                if t_str in self._snapshot_rates_cache:
                    return self._snapshot_rates_cache[t_str]
        except Exception:
            pass



        # Default fallback if outside defined periods and not in snapshots
        return 599.0

    def get_pre_reservation_quote(
        self,
        start_date: date,
        end_date: date,
        occupants: int = 1,
        occupants_small: int = 0,
        pets: int = 0,
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch real-time quote breakdown from Kivoya Streamline VRS (GetPreReservationPrice).
        Returns raw API response dict with required_fees, taxes_details, price, total.
        """
        try:
            res = self._call_api(
                "GetPreReservationPrice",
                {
                    "unit_id": self.unit_id,
                    "startdate": start_date.strftime("%m/%d/%Y"),
                    "enddate": end_date.strftime("%m/%d/%Y"),
                    "occupants": occupants,
                    "occupants_small": occupants_small,
                    "pets": pets,
                },
            )
            if isinstance(res, dict) and res.get("total"):
                return res
        except Exception:
            pass
        return None

    @staticmethod
    def calculate_direct_quote(base_subtotal: float) -> Dict[str, float]:
        """
        Calculate Kivoya Direct quote breakdown matching Kivoya / Streamline VRS exact fee engine:
        - Base: accommodation subtotal
        - Cleaning Fee: $550.00
        - Processing Fee: 6.0% of Base
        - Administrative Fee: 3.0% of (Base + Processing Fee + Cleaning Fee)
        - Service Fee (Platform Fees): Processing Fee + Admin Fee
        - Total before taxes (Pre-tax total): Base + Cleaning Fee + Service Fee
        - Statutory Taxes (14.07% on Base + Cleaning + Processing Fee):
            * Arizona State TPT: 5.5%
            * Maricopa County TPT: 1.77%
            * Tempe Hotel Tax: 1.8%
            * Tempe Hotel/Motel Transient Lodging Tax: 5.0%
        - Total Guest Checkout Price: Total before taxes + Taxes
        """
        clean_fee = 550.0
        proc_fee = round(base_subtotal * 0.06, 2)
        admin_fee = round((base_subtotal + proc_fee + clean_fee) * 0.03, 2)
        service_fee = round(proc_fee + admin_fee, 2)
        pretax_total = round(base_subtotal + clean_fee + service_fee, 2)

        tax_base = base_subtotal + clean_fee + proc_fee
        tax_az = round(tax_base * 0.055, 2)
        tax_maricopa = round(tax_base * 0.0177, 2)
        tax_tempe_hotel = round(tax_base * 0.018, 2)
        tax_tempe_motel = round(tax_base * 0.05, 2)
        taxes = round(tax_az + tax_maricopa + tax_tempe_hotel + tax_tempe_motel, 2)
        total_guest = round(pretax_total + taxes, 2)

        return {
            "base_subtotal": base_subtotal,
            "cleaning_fee": clean_fee,
            "processing_fee": proc_fee,
            "admin_fee": admin_fee,
            "service_fee": service_fee,
            "pretax_total": pretax_total,
            "tax_base": tax_base,
            "tax_az": tax_az,
            "tax_maricopa": tax_maricopa,
            "tax_tempe_hotel": tax_tempe_hotel,
            "tax_tempe_motel": tax_tempe_motel,
            "taxes": taxes,
            "total_price": total_guest,
        }
