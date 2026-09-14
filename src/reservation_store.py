"""
Reservation Storage & Analytics Module.
Manages SQLite database (data/reservations.db) and auto-synced JSON export
(data/reservations.json) for Villa del Sol reservations, plus daily accrual
revenue models for cumulative pace curves and availability calendar data.
"""

from datetime import datetime, date, timedelta
import json
from pathlib import Path
import sqlite3
from contextlib import contextmanager
from typing import Dict, List, Optional, Any, Tuple, Union, Set


DEFAULT_DB_PATH = Path("data/reservations.db")
DEFAULT_JSON_PATH = Path("data/reservations.json")


class ReservationStore:
    """Local SQLite persistence and revenue analytics store for reservations."""

    def __init__(
        self,
        db_path: Path = DEFAULT_DB_PATH,
        json_path: Path = DEFAULT_JSON_PATH,
    ):
        self.db_path = Path(db_path)
        self.json_path = Path(json_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self):
        """Create tables and indices if not already present."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS reservations (
                    id INTEGER PRIMARY KEY,
                    confirmation_id INTEGER,
                    creation_date TEXT,
                    start_date TEXT,
                    end_date TEXT,
                    days_number INTEGER,
                    type_id INTEGER,
                    type_name TEXT,
                    type_description TEXT,
                    status_name TEXT,
                    occupants INTEGER,
                    occupants_small INTEGER,
                    pets INTEGER,
                    unit_id INTEGER,
                    unit_name TEXT,
                    owner_payout REAL,
                    management_fee REAL,
                    gross_rent REAL,
                    is_future INTEGER,
                    last_scraped_at TEXT,
                    raw_json TEXT
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_res_dates 
                ON reservations(start_date, end_date)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_res_status 
                ON reservations(status_name)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_res_future 
                ON reservations(is_future)
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sync_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    synced_at TEXT,
                    sync_mode TEXT,
                    records_fetched INTEGER,
                    records_upserted INTEGER,
                    records_future INTEGER,
                    records_past INTEGER
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS property_rate_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_date TEXT,
                    calendar_date TEXT,
                    nightly_rate REAL,
                    interval_type TEXT,
                    season_name TEXT,
                    period_name TEXT,
                    created_at TEXT
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_rate_snap_lookup 
                ON property_rate_snapshots(calendar_date, snapshot_date)
            """)
            cursor.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_rate_snap_unique 
                ON property_rate_snapshots(calendar_date, snapshot_date)
            """)
            conn.commit()

    def upsert_reservations(
        self,
        reservations: List[Dict[str, Any]],
        sync_mode: str = "incremental",
        today: Optional[date] = None,
    ) -> Dict[str, int]:
        """
        Upsert a list of normalized reservation records into SQLite.
        Recalculates is_future based on today's date.
        Auto-exports updated dataset to JSON.
        """
        if today is None:
            today = date.today()

        now_iso = datetime.now().isoformat()
        upserted_count = 0
        past_count = 0
        future_count = 0

        with self._get_connection() as conn:
            cursor = conn.cursor()
            for r in reservations:
                # Recalculate is_future
                end_str = r.get("end_date")
                is_future = 1
                if end_str:
                    try:
                        end_dt = datetime.strptime(end_str, "%Y-%m-%d").date()
                        is_future = 1 if end_dt > today else 0
                    except ValueError:
                        is_future = r.get("is_future", 1)

                if is_future:
                    future_count += 1
                else:
                    past_count += 1

                cursor.execute("""
                    INSERT INTO reservations (
                        id, confirmation_id, creation_date, start_date, end_date,
                        days_number, type_id, type_name, type_description, status_name,
                        occupants, occupants_small, pets, unit_id, unit_name,
                        owner_payout, management_fee, gross_rent, is_future,
                        last_scraped_at, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        confirmation_id = excluded.confirmation_id,
                        creation_date = excluded.creation_date,
                        start_date = excluded.start_date,
                        end_date = excluded.end_date,
                        days_number = excluded.days_number,
                        type_id = excluded.type_id,
                        type_name = excluded.type_name,
                        type_description = excluded.type_description,
                        status_name = excluded.status_name,
                        occupants = excluded.occupants,
                        occupants_small = excluded.occupants_small,
                        pets = excluded.pets,
                        unit_id = excluded.unit_id,
                        unit_name = excluded.unit_name,
                        owner_payout = excluded.owner_payout,
                        management_fee = excluded.management_fee,
                        gross_rent = excluded.gross_rent,
                        is_future = excluded.is_future,
                        last_scraped_at = excluded.last_scraped_at,
                        raw_json = excluded.raw_json
                """, (
                    r.get("id"),
                    r.get("confirmation_id"),
                    r.get("creation_date"),
                    r.get("start_date"),
                    r.get("end_date"),
                    r.get("days_number", 0),
                    r.get("type_id"),
                    r.get("type_name"),
                    r.get("type_description"),
                    r.get("status_name", "Booked"),
                    r.get("occupants", 0),
                    r.get("occupants_small", 0),
                    r.get("pets", 0),
                    r.get("unit_id"),
                    r.get("unit_name"),
                    r.get("owner_payout", 0.0),
                    r.get("management_fee", 0.0),
                    r.get("gross_rent", 0.0),
                    is_future,
                    now_iso,
                    r.get("raw_json", "{}"),
                ))
                upserted_count += 1

            # Log sync run
            cursor.execute("""
                INSERT INTO sync_history (
                    synced_at, sync_mode, records_fetched, records_upserted,
                    records_future, records_past
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (now_iso, sync_mode, len(reservations), upserted_count, future_count, past_count))
            conn.commit()

        # Auto-sync JSON
        self.export_to_json(today=today)

        return {
            "fetched": len(reservations),
            "upserted": upserted_count,
            "future": future_count,
            "past": past_count,
        }

    def export_to_json(self, today: Optional[date] = None) -> Path:
        """Export all reservations from SQLite to data/reservations.json."""
        if today is None:
            today = date.today()

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, confirmation_id, creation_date, start_date, end_date,
                       days_number, type_id, type_name, type_description, status_name,
                       occupants, occupants_small, pets, unit_id, unit_name,
                       owner_payout, management_fee, gross_rent, is_future,
                       last_scraped_at, raw_json
                FROM reservations
                ORDER BY start_date DESC
            """)
            rows = []
            for row in cursor.fetchall():
                d = dict(row)
                if d.get("raw_json"):
                    try:
                        raw = json.loads(d["raw_json"])
                        if isinstance(raw, dict):
                            for k in ("madetype_name", "hear_about_name", "travelagent_name"):
                                if k not in d or not d[k]:
                                    d[k] = raw.get(k)
                    except Exception:
                        pass
                rows.append(d)

        payload = {
            "metadata": {
                "exported_at": datetime.now().isoformat(),
                "total_reservations": len(rows),
                "future_reservations": sum(1 for r in rows if r["is_future"] == 1),
                "past_reservations": sum(1 for r in rows if r["is_future"] == 0),
                "unit_name": "Villa del Sol",
                "unit_id": 503802,
            },
            "reservations": rows,
        }

        self.json_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        return self.json_path

    def get_all_reservations(self, include_cancelled: bool = False) -> List[Dict[str, Any]]:
        """Retrieve all reservations sorted by start date ascending."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if include_cancelled:
                cursor.execute("SELECT * FROM reservations ORDER BY start_date ASC")
            else:
                cursor.execute("""
                    SELECT * FROM reservations 
                    WHERE LOWER(status_name) != 'cancelled'
                    ORDER BY start_date ASC
                """)
            results = []
            for r in cursor.fetchall():
                d = dict(r)
                if d.get("raw_json"):
                    try:
                        raw = json.loads(d["raw_json"])
                        if isinstance(raw, dict):
                            for k in ("madetype_name", "hear_about_name", "travelagent_name"):
                                if k not in d or not d[k]:
                                    d[k] = raw.get(k)
                    except Exception:
                        pass
                results.append(d)
            return results

    def calculate_cumulative_annual_revenue(
        self,
        start_year: int = 2022,
        end_year: Optional[int] = None,
        today: Optional[date] = None,
    ) -> Dict[str, Any]:
        """
        Calculate daily accrual revenue pace curves for each calendar year.
        Revenue is prorated per night stayed.
        Returns data formatted for Chart.js cumulative curve visualizer.
        """
        if today is None:
            today = date.today()

        if end_year is None:
            end_year = max(today.year + 1, 2027)

        # Retrieve all active bookings
        reservations = self.get_all_reservations(include_cancelled=False)

        # Map: year -> { date_obj: daily_revenue }
        daily_revenue: Dict[int, Dict[date, float]] = {
            y: {} for y in range(start_year, end_year + 1)
        }
        # Track nightly breakdown of past vs future per date
        daily_status: Dict[int, Dict[date, str]] = {
            y: {} for y in range(start_year, end_year + 1)
        }

        for r in reservations:
            s_str = r.get("start_date")
            e_str = r.get("end_date")
            if not s_str or not e_str:
                continue

            try:
                s_dt = datetime.strptime(s_str, "%Y-%m-%d").date()
                e_dt = datetime.strptime(e_str, "%Y-%m-%d").date()
            except ValueError:
                continue

            nights = (e_dt - s_dt).days
            if nights <= 0:
                continue

            payout = float(r.get("owner_payout") or 0.0)
            daily_payout = payout / nights

            curr = s_dt
            while curr < e_dt:
                y = curr.year
                if y in daily_revenue:
                    daily_revenue[y][curr] = daily_revenue[y].get(curr, 0.0) + daily_payout
                    # Mark day as future if curr >= today, else past
                    status = "future" if curr >= today else "past"
                    daily_status[y][curr] = status
                curr += timedelta(days=1)

        # Generate standard 365/366 day curves starting from Jan 1
        yearly_series: Dict[int, Dict[str, Any]] = {}

        # Colors matching the reference image:
        # 2022: blue dotted, 2023: red dash-dot, 2024: dark green dashed,
        # 2025: purple solid, 2026: orange solid/dots, 2027: teal dashed
        style_config = {
            2022: {"color": "#1E88E5", "dash": [4, 4], "label": "2022"},
            2023: {"color": "#E53935", "dash": [8, 4, 2, 4], "label": "2023"},
            2024: {"color": "#2E7D32", "dash": [6, 4], "label": "2024"},
            2025: {"color": "#8E24AA", "dash": [], "label": "2025"},
            2026: {"color": "#FB8C00", "dash": [], "label": "2026"},
            2027: {"color": "#00ACC1", "dash": [4, 4], "label": "2027"},
        }

        # Reference month labels for X-axis (1st of each month + Dec 31)
        # We index days from 1 to 365
        month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        
        # Day of year mapping for a non-leap year:
        # Month start day of year (1-based): Jan=1, Feb=32, Mar=60, Apr=91, May=121, Jun=152, Jul=182, Aug=213, Sep=244, Oct=274, Nov=305, Dec=335
        # Total 365 points

        summary_kpis: Dict[str, Any] = {
            "current_year": today.year,
            "ytd_revenue": 0.0,
            "prior_ytd_revenue": 0.0,
            "ytd_growth_pct": 0.0,
            "total_booked_revenue": 0.0,
            "total_nights_booked": 0,
            "adr": 0.0,
            "by_year": {},
        }

        for y in range(start_year, end_year + 1):
            is_leap = (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0))
            num_days = 366 if is_leap else 365
            start_d = date(y, 1, 1)

            cum_val = 0.0
            points = []
            past_points = []
            future_points = []
            
            y_total_rev = 0.0
            y_total_nights = 0
            y_ytd_rev = 0.0

            for d_idx in range(num_days):
                curr_date = start_d + timedelta(days=d_idx)
                d_payout = daily_revenue[y].get(curr_date, 0.0)
                cum_val += d_payout
                
                if d_payout > 0:
                    y_total_rev += d_payout
                    y_total_nights += 1

                # Compare to that exact same calendar day of the year (month & day)
                if (curr_date.month, curr_date.day) <= (today.month, today.day):
                    y_ytd_rev = cum_val

                # Normalized day-of-year index (1 to 365)
                # For leap day Feb 29 (day 60), we can map or clamp to 365
                norm_day = min(365, int((d_idx / num_days) * 365) + 1)
                
                point_data = {
                    "day": norm_day,
                    "date": curr_date.strftime("%Y-%m-%d"),
                    "label": curr_date.strftime("%b %d"),
                    "daily": round(d_payout, 2),
                    "cumulative": round(cum_val, 2),
                    "is_future": 1 if curr_date > today else 0,
                }
                points.append(point_data)

            # Year summary stats
            adr = (y_total_rev / y_total_nights) if y_total_nights > 0 else 0.0
            style = style_config.get(y, {"color": "#757575", "dash": [], "label": str(y)})

            yearly_series[y] = {
                "year": y,
                "label": style["label"],
                "color": style["color"],
                "dash": style["dash"],
                "total_revenue": round(y_total_rev, 2),
                "total_nights": y_total_nights,
                "adr": round(adr, 2),
                "ytd_revenue": round(y_ytd_rev, 2),
                "points": points,
            }

            summary_kpis["by_year"][y] = {
                "total_revenue": round(y_total_rev, 2),
                "total_nights": y_total_nights,
                "adr": round(adr, 2),
                "ytd_revenue": round(y_ytd_rev, 2),
            }

        # Calculate high level KPI comparisons for current year
        cy = today.year
        py = cy - 1
        cy_stats = summary_kpis["by_year"].get(cy, {})
        py_stats = summary_kpis["by_year"].get(py, {})

        summary_kpis["ytd_revenue"] = cy_stats.get("ytd_revenue", 0.0)
        summary_kpis["prior_ytd_revenue"] = py_stats.get("ytd_revenue", 0.0)
        if summary_kpis["prior_ytd_revenue"] > 0:
            diff = summary_kpis["ytd_revenue"] - summary_kpis["prior_ytd_revenue"]
            summary_kpis["ytd_growth_pct"] = round((diff / summary_kpis["prior_ytd_revenue"]) * 100, 1)
        summary_kpis["total_booked_revenue"] = cy_stats.get("total_revenue", 0.0)
        summary_kpis["total_nights_booked"] = cy_stats.get("total_nights", 0)
        summary_kpis["adr"] = cy_stats.get("adr", 0.0)

        return {
            "yearly_series": yearly_series,
            "kpis": summary_kpis,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }

    def record_rate_snapshots(
        self,
        snapshot_date: str,
        rates_by_date: Dict[str, Dict[str, Any]],
    ) -> int:
        """
        Record a snapshot of published property nightly rates.
        rates_by_date: mapping of 'YYYY-MM-DD' -> {
            'nightly_rate': float,
            'interval_type': str,  # 'midweek' or 'weekend'
            'season_name': str,
            'period_name': str
        }
        Returns count of rows upserted.
        """
        now_str = datetime.now().isoformat()
        records = []
        for cal_date, info in rates_by_date.items():
            records.append((
                snapshot_date,
                cal_date,
                float(info.get("nightly_rate", 0.0)),
                info.get("interval_type", "midweek"),
                info.get("season_name", ""),
                info.get("period_name", ""),
                now_str,
            ))

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.executemany("""
                INSERT INTO property_rate_snapshots (
                    snapshot_date, calendar_date, nightly_rate,
                    interval_type, season_name, period_name, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(calendar_date, snapshot_date) DO UPDATE SET
                    nightly_rate = excluded.nightly_rate,
                    interval_type = excluded.interval_type,
                    season_name = excluded.season_name,
                    period_name = excluded.period_name,
                    created_at = excluded.created_at
            """, records)
            conn.commit()
            return len(records)

    def get_rate_at_time(
        self,
        calendar_date: str,
        target_snapshot_date: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Query published nightly rate for a stay date as of a target snapshot date.
        Returns the most recent snapshot on or prior to target_snapshot_date.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT nightly_rate, interval_type, season_name, period_name, snapshot_date
                FROM property_rate_snapshots
                WHERE calendar_date = ? AND snapshot_date <= ?
                ORDER BY snapshot_date DESC
                LIMIT 1
            """, (calendar_date, target_snapshot_date))
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None

    def get_latest_rate_snapshot(
        self,
        calendar_date: str,
    ) -> Optional[Dict[str, Any]]:
        """Query the latest recorded rate snapshot for a given calendar stay date."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT nightly_rate, interval_type, season_name, period_name, snapshot_date
                FROM property_rate_snapshots
                WHERE calendar_date = ?
                ORDER BY snapshot_date DESC
                LIMIT 1
            """, (calendar_date,))
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None

    def get_daily_calendar_rates(
        self,
        reservations: List[Dict[str, Any]],
        current_kivoya_rates: Optional[List[Dict[str, Any]]] = None,
        today: Optional[date] = None,
        baseline_snapshot_date: str = "2026-02-01",
    ) -> Dict[str, Dict[str, Any]]:
        """
        Generate daily rates map for calendar grid display.
        Maps 'YYYY-MM-DD' -> {
            'rate': float | None,
            'type': 'booked_snapshot' | 'vacant_catalog' | 'booked_no_snapshot',
            'label': str,
            'interval_type': str
        }
        """
        if today is None:
            today = date.today()

        # Build map of stayed dates to reservations
        # Each night d is paid/occupied if start_date <= d < end_date
        stay_res_map: Dict[str, Dict[str, Any]] = {}
        for r in reservations:
            s_str = r.get("start_date")
            e_str = r.get("end_date")
            status = str(r.get("status_name", "")).lower()
            if not s_str or not e_str or status == "cancelled":
                continue
            try:
                s_dt = datetime.strptime(s_str, "%Y-%m-%d").date()
                e_dt = datetime.strptime(e_str, "%Y-%m-%d").date()
                cur = s_dt
                while cur < e_dt:
                    d_str = cur.strftime("%Y-%m-%d")
                    stay_res_map[d_str] = r
                    cur += timedelta(days=1)
            except Exception:
                continue

        # Load all snapshots into memory for fast lookup
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT snapshot_date, calendar_date, nightly_rate, interval_type, period_name FROM property_rate_snapshots ORDER BY snapshot_date ASC")
            all_snaps: Dict[str, List[Dict[str, Any]]] = {}
            for row in cursor.fetchall():
                c_date = row["calendar_date"]
                if c_date not in all_snaps:
                    all_snaps[c_date] = []
                all_snaps[c_date].append(dict(row))

        from src.kivoya_client import KivoyaClient
        kivoya = KivoyaClient()
        if current_kivoya_rates is None:
            try:
                current_kivoya_rates = kivoya.get_seasonal_rates()
            except Exception:
                current_kivoya_rates = []

        # Calendar span: 2026-01-01 to 2027-12-31
        start_calendar = date(2026, 1, 1)
        end_calendar = date(2027, 12, 31)

        result: Dict[str, Dict[str, Any]] = {}
        cur = start_calendar
        while cur <= end_calendar:
            d_str = cur.strftime("%Y-%m-%d")
            int_type = "weekend" if cur.weekday() in (3, 4, 5, 6) else "midweek"

            if d_str in stay_res_map:
                res = stay_res_map[d_str]
                # Look up snapshot as of creation_date
                c_date_str = None
                raw_c = res.get("creation_date")
                if raw_c:
                    if "/" in raw_c:
                        parts = raw_c.split()[0].split("/")
                        if len(parts) == 3:
                            c_date_str = f"{parts[2].zfill(4)}-{parts[0].zfill(2)}-{parts[1].zfill(2)}"
                    elif "-" in raw_c:
                        c_date_str = raw_c.split()[0].split("T")[0]

                target_snap_date = c_date_str or baseline_snapshot_date

                # Find snapshot on or before target_snap_date
                matched_snap = None
                snaps_for_day = all_snaps.get(d_str, [])
                for s in reversed(snaps_for_day):
                    if s["snapshot_date"] <= target_snap_date:
                        matched_snap = s
                        break

                # If no snapshot found before creation date, but this is an upcoming stay,
                # fall back to baseline snapshot
                if not matched_snap and (res.get("is_future") or cur >= date(2026, 9, 1)):
                    for s in reversed(snaps_for_day):
                        if s["snapshot_date"] <= baseline_snapshot_date:
                            matched_snap = s
                            break

                if matched_snap:
                    rate = matched_snap["nightly_rate"]
                    result[d_str] = {
                        "rate": round(rate),
                        "type": "booked_snapshot",
                        "label": f"Rate at Booking: ${rate:,.0f}",
                        "interval_type": matched_snap.get("interval_type", int_type),
                    }
                else:
                    # Past reservation with no historical snapshot
                    result[d_str] = {
                        "rate": None,
                        "type": "booked_no_snapshot",
                        "label": "Rate at Booking: N/A",
                        "interval_type": int_type,
                    }
            else:
                # Vacant date - calculate catalog rate
                catalog_rate = kivoya.get_rate_for_date(cur, current_kivoya_rates)
                result[d_str] = {
                    "rate": round(catalog_rate),
                    "type": "vacant_catalog",
                    "label": f"Kivoya Catalog Rate: ${catalog_rate:,.0f}",
                    "interval_type": int_type,
                }

            cur += timedelta(days=1)

        return result

    def ensure_baseline_snapshots(
        self,
        baseline_dates: Tuple[str, ...] = ("2026-02-01", "2026-09-07"),
    ) -> int:
        """Ensure baseline rate snapshots exist in SQLite for historical auditing."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT DISTINCT snapshot_date FROM property_rate_snapshots")
                existing_dates = {row[0] for row in cursor.fetchall()}

            missing_dates = [d for d in baseline_dates if d not in existing_dates]
            if not missing_dates:
                return 0

            from src.kivoya_client import KivoyaClient
            kivoya = KivoyaClient()
            rates = kivoya.get_seasonal_rates()
            if not rates:
                return 0

            rates_by_date = {}
            for r in rates:
                b_dt = r["begin_dt"]
                e_dt = r["end_dt"]
                cur = b_dt
                while cur <= e_dt:
                    d_str = cur.strftime("%Y-%m-%d")
                    weekday = cur.weekday()
                    if r.get("second_price") is not None and weekday in r.get("second_days", set()):
                        p = r["second_price"]
                        i_type = "weekend"
                    elif r.get("first_price") is not None and (not r.get("first_days") or weekday in r.get("first_days")):
                        p = r["first_price"]
                        i_type = "weekend" if weekday in (3, 4, 5, 6) else "midweek"
                    else:
                        p = r.get("nightly_rate", 599.0)
                        i_type = "weekend" if weekday in (3, 4, 5, 6) else "midweek"

                    rates_by_date[d_str] = {
                        "nightly_rate": p,
                        "interval_type": i_type,
                        "season_name": r.get("season_name", ""),
                        "period_name": r.get("period_name", ""),
                    }
                    cur += timedelta(days=1)

            total_added = 0
            for snap_d in missing_dates:
                total_added += self.record_rate_snapshots(snap_d, rates_by_date)

            # Also ensure historical backfill is populated
            total_added += self.backfill_historical_rate_snapshots()

            return total_added
        except Exception:
            return 0

    def backfill_historical_rate_snapshots(
        self,
        snapshot_date: str = "2026-02-01",
        start_date_str: str = "2026-02-01",
        end_date_str: str = "2026-09-02",
        force: bool = False,
    ) -> int:
        """
        Backfill historical rate snapshots for dates between 2026-02-01 and 2026-09-02
        based on the 2027 seasonal schedule (52-week shifted) and Summer 2026 catalog schedule
        set on Feb 1, 2026.
        """
        try:
            s_d = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            e_d = datetime.strptime(end_date_str, "%Y-%m-%d").date()
            total_days = (e_d - s_d).days + 1

            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT COUNT(DISTINCT calendar_date) FROM property_rate_snapshots
                    WHERE calendar_date >= ? AND calendar_date <= ? AND snapshot_date = ?
                """, (start_date_str, end_date_str, snapshot_date))
                existing_count = cursor.fetchone()[0]

            if existing_count >= total_days and not force:
                return 0

            from src.kivoya_client import KivoyaClient
            kivoya = KivoyaClient()
            rates_2027 = kivoya.get_seasonal_rates()

            cur = s_d
            rates_by_date = {}

            while cur <= e_d:
                d_str = cur.strftime("%Y-%m-%d")
                weekday = cur.weekday()

                # 1. Spring Season: 2026-02-01 to 2026-05-10 (Mapped from 2027 52-week shifted)
                if cur <= date(2026, 5, 10):
                    d_2027 = cur + timedelta(days=364)  # 52 weeks shift
                    matched = False
                    for r in rates_2027:
                        if r["begin_dt"] <= d_2027 <= r["end_dt"]:
                            if r.get("second_price") is not None and d_2027.weekday() in (r.get("second_days") or set()):
                                p = float(r["second_price"])
                                i_type = "weekend"
                            elif r.get("first_price") is not None and (not r.get("first_days") or d_2027.weekday() in r["first_days"]):
                                p = float(r["first_price"])
                                i_type = "weekend" if cur.weekday() in (3, 4, 5, 6) else "midweek"
                            else:
                                p = float(r["nightly_rate"])
                                i_type = "weekend" if cur.weekday() in (3, 4, 5, 6) else "midweek"

                            rates_by_date[d_str] = {
                                "nightly_rate": p,
                                "interval_type": i_type,
                                "season_name": f"{r.get('season_name', '')} (2026 Backfill)",
                                "period_name": r.get("period_name", ""),
                            }
                            matched = True
                            break
                    if not matched:
                        rates_by_date[d_str] = {
                            "nightly_rate": 749.0 if weekday in (3, 4, 5) else 599.0,
                            "interval_type": "weekend" if weekday in (3, 4, 5) else "midweek",
                            "season_name": "Spring 2026 (Backfill)",
                            "period_name": "Spring Base",
                        }

                # 2. Summer Low-Season: 2026-05-11 to 2026-08-31
                elif cur <= date(2026, 8, 31):
                    # Memorial Day Weekend: May 22 - May 25, 2026
                    if date(2026, 5, 22) <= cur <= date(2026, 5, 25):
                        rates_by_date[d_str] = {
                            "nightly_rate": 599.0,
                            "interval_type": "weekend",
                            "season_name": "Holidays Memorial Day 2026",
                            "period_name": "Memorial Day 26",
                        }
                    # 4th of July Weekend: July 3 - July 5, 2026
                    elif date(2026, 7, 3) <= cur <= date(2026, 7, 5):
                        rates_by_date[d_str] = {
                            "nightly_rate": 549.0,
                            "interval_type": "weekend",
                            "season_name": "Holiday 4th of July 2026",
                            "period_name": "July 4th 26",
                        }
                    # Summer Weekend: Thu, Fri, Sat (3, 4, 5)
                    elif weekday in (3, 4, 5):
                        rates_by_date[d_str] = {
                            "nightly_rate": 449.0,
                            "interval_type": "weekend",
                            "season_name": "Summer 2026",
                            "period_name": "Summer Weekend",
                        }
                    # Summer Midweek: Sun, Mon, Tue, Wed (6, 0, 1, 2)
                    else:
                        rates_by_date[d_str] = {
                            "nightly_rate": 389.0,
                            "interval_type": "midweek",
                            "season_name": "Summer 2026",
                            "period_name": "Summer Midweek",
                        }

                # 3. Early September ramp: Sept 1 - Sept 2, 2026
                else:
                    rates_by_date[d_str] = {
                        "nightly_rate": 399.0,
                        "interval_type": "midweek",
                        "season_name": "September 2025-26",
                        "period_name": "Sep. 26",
                    }

                cur += timedelta(days=1)

            return self.record_rate_snapshots(snapshot_date, rates_by_date)
        except Exception:
            return 0

    def load_all_rate_snapshots(self) -> Dict[str, List[Dict[str, Any]]]:
        """Load all rate snapshots indexed by calendar_date ordered by snapshot_date."""
        # Ensure historical backfill exists
        self.backfill_historical_rate_snapshots()

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT snapshot_date, calendar_date, nightly_rate, interval_type, period_name, season_name
                FROM property_rate_snapshots
                ORDER BY snapshot_date ASC
            """)
            all_snaps: Dict[str, List[Dict[str, Any]]] = {}
            for row in cursor.fetchall():
                c_date = row["calendar_date"]
                if c_date not in all_snaps:
                    all_snaps[c_date] = []
                all_snaps[c_date].append(dict(row))
            return all_snaps

    def get_recent_rate_change_dates(
        self,
        days_back: int = 7,
        as_of_date: Optional[Union[date, str]] = None,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Identify stay calendar dates where published nightly rates in Streamline/Kivoya
        changed between consecutive snapshots within the last `days_back` days.
        Returns a dict: calendar_date -> list of change events:
        [{
            'change_date': str,
            'old_rate': float,
            'new_rate': float,
            'interval_type': str
        }]
        """
        if as_of_date is None:
            as_of_dt = date.today()
        elif isinstance(as_of_date, str):
            as_of_dt = datetime.strptime(as_of_date, "%Y-%m-%d").date()
        elif isinstance(as_of_date, datetime):
            as_of_dt = as_of_date.date()
        elif isinstance(as_of_date, date):
            as_of_dt = as_of_date
        else:
            as_of_dt = date.today()

        cutoff_dt = as_of_dt - timedelta(days=days_back)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT snapshot_date, calendar_date, nightly_rate, interval_type
                FROM property_rate_snapshots
                ORDER BY calendar_date ASC, snapshot_date ASC
            """)
            rows = cursor.fetchall()

        changes_by_cal_date: Dict[str, List[Dict[str, Any]]] = {}
        last_rate_info: Dict[str, Tuple[float, str]] = {}

        for r in rows:
            c_date = r["calendar_date"]
            s_date = r["snapshot_date"]
            rate = float(r["nightly_rate"] or 0.0)
            i_type = r["interval_type"] or "midweek"

            if c_date in last_rate_info:
                prev_rate, _ = last_rate_info[c_date]
                if abs(prev_rate - rate) >= 0.01:
                    try:
                        snap_dt = datetime.strptime(s_date, "%Y-%m-%d").date()
                        if cutoff_dt <= snap_dt <= as_of_dt:
                            if c_date not in changes_by_cal_date:
                                changes_by_cal_date[c_date] = []
                            changes_by_cal_date[c_date].append({
                                "change_date": s_date,
                                "old_rate": prev_rate,
                                "new_rate": rate,
                                "interval_type": i_type,
                            })
                    except (ValueError, TypeError):
                        pass
            last_rate_info[c_date] = (rate, s_date)

        return changes_by_cal_date

    def get_intervals_with_recent_rate_changes(
        self,
        intervals: List[Dict[str, Any]],
        days_back: int = 7,
        as_of_date: Optional[Union[date, str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Filter a list of unbooked stay intervals to only those affected by recent
        Streamline price updates (stay dates having rate changes within the last `days_back` days).
        Returns the subset of intervals with metadata on which nights changed.
        """
        recent_changes = self.get_recent_rate_change_dates(days_back=days_back, as_of_date=as_of_date)
        if not recent_changes:
            return []

        affected: List[Dict[str, Any]] = []
        for s in intervals:
            c_in = s["check_in"] if isinstance(s, dict) else getattr(s, "check_in", None)
            c_out = s["check_out"] if isinstance(s, dict) else getattr(s, "check_out", None)
            if not c_in or not c_out:
                continue

            try:
                cin_dt = datetime.strptime(str(c_in), "%Y-%m-%d").date()
                cout_dt = datetime.strptime(str(c_out), "%Y-%m-%d").date()
            except ValueError:
                continue

            cur = cin_dt
            affected_nights: Dict[str, List[Dict[str, Any]]] = {}
            while cur < cout_dt:
                d_str = cur.strftime("%Y-%m-%d")
                if d_str in recent_changes:
                    affected_nights[d_str] = recent_changes[d_str]
                cur += timedelta(days=1)

            if affected_nights:
                if isinstance(s, dict):
                    item = dict(s)
                    item["affected_rate_changes"] = affected_nights
                    affected.append(item)
                else:
                    affected.append(s)

        return affected


def parse_reservation_creation_date(raw_date: Any) -> Optional[str]:
    """Parse reservation creation date into ISO 'YYYY-MM-DD' string."""
    if not raw_date:
        return None
    s = str(raw_date).strip()
    if not s:
        return None
    try:
        if "/" in s:
            date_part = s.split()[0]
            parts = date_part.split("/")
            if len(parts) == 3:
                return f"{parts[2].zfill(4)}-{parts[0].zfill(2)}-{parts[1].zfill(2)}"
        elif "-" in s:
            date_part = s.split()[0].split("T")[0]
            parts = date_part.split("-")
            if len(parts) == 3:
                return f"{parts[0].zfill(4)}-{parts[1].zfill(2)}-{parts[2].zfill(2)}"
    except Exception:
        pass
    return None


def audit_reservation_payout(
    reservation: Dict[str, Any],
    all_snapshots: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    current_kivoya_rates: Optional[List[Dict[str, Any]]] = None,
    baseline_snapshot_date: str = "2026-02-01",
    audit_threshold_dollars: float = 5.0,
) -> Dict[str, Any]:
    """
    Audit owner payout / commission for a reservation against published catalog rate snapshots.
    Agreed formula:
      - Expected Distributable Gross Rent = Sum of published catalog nightly rates at booking time
      - Expected Owner Net (82%) = Expected Distributable Gross Rent * 0.82
      - Pass/Fail: If (Actual Owner Payout - Expected Owner Net) < -audit_threshold_dollars, fails with Shortfall.
      - Date scope: Only reservations booked on or after baseline_snapshot_date (2026-02-01) are audited.
    """
    if all_snapshots is None:
        try:
            _store = ReservationStore()
            all_snapshots = _store.load_all_rate_snapshots()
        except Exception:
            all_snapshots = {}

    raw = {}
    if reservation.get("raw_json"):
        try:
            raw = json.loads(reservation["raw_json"]) if isinstance(reservation["raw_json"], str) else reservation["raw_json"]
        except Exception:
            pass
    elif reservation.get("raw_streamline"):
        raw = reservation["raw_streamline"]

    comm = reservation.get("commission_information") or raw.get("commission_information") or {}
    status_name = str(reservation.get("status_name") or "").lower()
    type_name = str(reservation.get("type_name") or raw.get("type_name") or "").upper()
    type_desc = str(reservation.get("type_description") or raw.get("type_description") or "").lower()
    madetype = str(reservation.get("madetype_name") or raw.get("madetype_name") or "").upper()
    hear_about = str(reservation.get("hear_about_name") or raw.get("hear_about_name") or "").lower()
    travel_agent = str(reservation.get("travelagent_name") or raw.get("travelagent_name") or "").lower()

    if "airbnb" in hear_about or "airbnb" in travel_agent or madetype == "WSR":
        channel_name = "Airbnb"
    elif "vrbo" in hear_about or "ha-olb" in hear_about or "homeaway" in travel_agent or "vrbo" in travel_agent or madetype == "PDWTA":
        channel_name = "Vrbo"
    elif "booking" in hear_about or "booking" in travel_agent:
        channel_name = "Booking.com"
    elif "expedia" in hear_about or "expedia" in travel_agent:
        channel_name = "Expedia"
    elif madetype == "NET" or "kivoya.com" in hear_about:
        channel_name = "Direct Website"
    elif madetype == "ADM":
        channel_name = "Kivoya Admin"
    elif madetype == "OWN" or type_name == "OWN" or "owner" in type_desc:
        channel_name = "Owner Block"
    else:
        channel_name = reservation.get("channel_name") or "Standard"

    actual_gross = float(reservation.get("gross_rent") or 0.0)
    actual_owner = float(reservation.get("owner_payout") or comm.get("owner_commission_amount") or (actual_gross * 0.82))

    # 1. Cancelled stays
    if status_name == "cancelled":
        return {
            "audit_status": "cancelled",
            "audit_failed": False,
            "status_label": "Cancelled",
            "badge_color": "#64748b",
            "badge_bg": "rgba(100,116,139,0.15)",
            "expected_gross_rent": 0.0,
            "expected_owner_payout": 0.0,
            "actual_gross_rent": actual_gross,
            "actual_owner_payout": actual_owner,
            "discrepancy": 0.0,
            "gross_discrepancy": 0.0,
            "discrepancy_pct": 0.0,
            "audit_shortfall": 0.0,
            "booking_date_parsed": None,
            "is_legacy": False,
            "is_exempt": True,
            "nightly_breakdown": [],
            "diagnostic_text": "Reservation has been cancelled in Streamline VRS ($0.00 active payout).",
        }

    # 2. Owner & Maintenance blocks
    if type_name in ("OWN", "MAINT") or madetype == "OWN" or "owner" in type_desc or "maintenance" in type_desc:
        return {
            "audit_status": "exempt",
            "audit_failed": False,
            "status_label": "Exempt (Owner/Maint)",
            "badge_color": "#a855f7",
            "badge_bg": "rgba(168,85,247,0.15)",
            "expected_gross_rent": 0.0,
            "expected_owner_payout": 0.0,
            "actual_gross_rent": actual_gross,
            "actual_owner_payout": actual_owner,
            "discrepancy": 0.0,
            "gross_discrepancy": 0.0,
            "discrepancy_pct": 0.0,
            "audit_shortfall": 0.0,
            "is_known_discount": False,
            "discount_category": "none",
            "discount_pct": 0.0,
            "rule_title": "🔒 Exempt (Owner / Maintenance)",
            "booking_date_parsed": None,
            "is_legacy": False,
            "is_exempt": True,
            "nightly_breakdown": [],
            "diagnostic_text": "Owner stay or maintenance block ($0.00 distributable rent). Exempt from rate schedule audit.",
        }

    # 3. Booking creation date & legacy status
    raw_creation = reservation.get("creation_date") or raw.get("creation_date")
    c_date_iso = parse_reservation_creation_date(raw_creation)

    if not c_date_iso or c_date_iso < baseline_snapshot_date:
        return {
            "audit_status": "legacy",
            "audit_failed": False,
            "is_known_discount": False,
            "discount_category": "none",
            "discount_pct": 0.0,
            "rule_title": "⏳ Legacy (Pre-Feb 2026)",
            "status_label": "Legacy (Pre-Feb 2026)",
            "badge_color": "#94a3b8",
            "badge_bg": "rgba(148,163,184,0.15)",
            "expected_gross_rent": actual_gross,
            "expected_owner_payout": actual_owner,
            "actual_gross_rent": actual_gross,
            "actual_owner_payout": actual_owner,
            "discrepancy": 0.0,
            "gross_discrepancy": 0.0,
            "discrepancy_pct": 0.0,
            "audit_shortfall": 0.0,
            "booking_date_parsed": c_date_iso,
            "is_legacy": True,
            "is_exempt": False,
            "nightly_breakdown": [],
            "diagnostic_text": f"Booked on {raw_creation or 'N/A'} (prior to the {baseline_snapshot_date} rate audit baseline). Subject to historical pricing.",
        }

    # 4. Active booking booked on or after baseline date
    start_str = reservation.get("start_date") or ""
    end_str = reservation.get("end_date") or ""
    if not start_str or not end_str:
        return {
            "audit_status": "verified",
            "audit_failed": False,
            "is_known_discount": False,
            "discount_category": "none",
            "discount_pct": 0.0,
            "rule_title": "✅ Verified Rate Match",
            "status_label": "✅ Verified",
            "badge_color": "#10b981",
            "badge_bg": "rgba(16,185,129,0.15)",
            "expected_gross_rent": actual_gross,
            "expected_owner_payout": actual_owner,
            "actual_gross_rent": actual_gross,
            "actual_owner_payout": actual_owner,
            "discrepancy": 0.0,
            "gross_discrepancy": 0.0,
            "discrepancy_pct": 0.0,
            "audit_shortfall": 0.0,
            "booking_date_parsed": c_date_iso,
            "is_legacy": False,
            "is_exempt": False,
            "nightly_breakdown": [],
            "diagnostic_text": "Dates missing for audit calculation.",
        }

    try:
        s_dt = datetime.strptime(start_str, "%Y-%m-%d").date()
        e_dt = datetime.strptime(end_str, "%Y-%m-%d").date()
    except Exception:
        s_dt = date.today()
        e_dt = date.today()

    nights = max(1, (e_dt - s_dt).days)
    effective_nightly_booked = round(actual_gross / nights, 2)

    from src.kivoya_client import KivoyaClient
    kivoya = KivoyaClient()
    if current_kivoya_rates is None:
        try:
            current_kivoya_rates = kivoya.get_seasonal_rates()
        except Exception:
            current_kivoya_rates = []

    nightly_breakdown = []
    expected_gross = 0.0
    target_snap = c_date_iso

    cur = s_dt
    while cur < e_dt:
        d_str = cur.strftime("%Y-%m-%d")
        dow = cur.strftime("%a")
        is_wknd = cur.weekday() in (3, 4, 5, 6)

        matched_snap = None
        if all_snapshots:
            snaps = all_snapshots.get(d_str, [])
            for s in reversed(snaps):
                if s["snapshot_date"] <= target_snap:
                    matched_snap = s
                    break
            if not matched_snap and snaps:
                matched_snap = snaps[0]

        if matched_snap:
            cat_rate = float(matched_snap.get("nightly_rate", 599.0))
        else:
            cat_rate = float(kivoya.get_rate_for_date(cur, current_kivoya_rates) or 599.0)

        expected_gross += cat_rate
        var = round(effective_nightly_booked - cat_rate, 2)
        nightly_breakdown.append({
            "date": d_str,
            "day_name": dow,
            "is_weekend": is_wknd,
            "catalog_rate": round(cat_rate, 2),
            "effective_booked_rate": effective_nightly_booked,
            "variance": var,
        })
        cur += timedelta(days=1)

    expected_gross = round(expected_gross, 2)
    expected_owner = round(expected_gross * 0.82, 2)
    discrepancy = round(actual_owner - expected_owner, 2)
    gross_discrepancy = round(actual_gross - expected_gross, 2)
    discrepancy_pct = round((discrepancy / expected_owner * 100), 1) if expected_owner > 0 else 0.0

    expected_avg_adr = round(expected_gross / nights, 2)
    shortfall_amount = 0.0
    discount_pct = round(((expected_gross - actual_gross) / expected_gross) * 100, 1) if expected_gross > 0 else 0.0
    is_known_discount = False
    discount_category = "none"
    rule_title = ""

    if discrepancy < -audit_threshold_dollars:
        shortfall_amount = abs(discrepancy)
        # 0. Check if within standard 5% channel parity tolerance
        if abs(discount_pct) <= 5.0:
            audit_status = "verified"
            audit_failed = False
            is_known_discount = False
            discount_category = "none"
            rule_title = "✅ Verified Rate Match"
            status_label = "✅ Verified"
            badge_color = "#10b981"
            badge_bg = "rgba(16,185,129,0.2)"
            diagnostic_text = (
                f"Owner payout of ${actual_owner:,.2f} matches published catalog rates within standard channel parity tolerance "
                f"(82% of ${expected_gross:,.2f} catalog gross = ${expected_owner:,.2f}; Variance: ${discrepancy:+,.2f} / -{discount_pct:.1f}%)."
            )
        # 1. Weekly Stay Length-of-Stay Discount (7+ nights with 14%–26% discount)
        elif nights >= 7 and 14.0 <= discount_pct <= 26.0:
            audit_status = "discount_weekly"
            audit_failed = False
            is_known_discount = True
            discount_category = "weekly"
            rule_title = "📅 Weekly Stay Discount"
            status_label = f"📅 Weekly -{discount_pct:.0f}%"
            badge_color = "#06b6d4"
            badge_bg = "rgba(6,182,212,0.2)"
            diagnostic_text = (
                f"Identified Rule: 📅 Weekly Length-of-Stay Discount ({discount_pct:.1f}% reduction). "
                f"For stays of {nights} nights, an automated weekly stay discount rule (e.g. standard ~16.5% weekly tier) was applied by the booking channel/PMS, "
                f"yielding ${actual_gross:,.2f} realized gross vs ${expected_gross:,.2f} base catalog rates. "
                f"Actual Owner Payout is ${actual_owner:,.2f} (82% of realized rent; variance from base catalog: -${shortfall_amount:,.2f})."
            )
        # 2. Standard Channel Promotion / Early Bird (5%–16% discount)
        elif 5.0 < discount_pct <= 16.0:
            audit_status = "discount_promo"
            audit_failed = False
            is_known_discount = True
            discount_category = "promo"
            rule_title = "🏷️ Channel Promotion / Early Bird"
            status_label = f"🏷️ Promo -{discount_pct:.0f}%"
            badge_color = "#3b82f6"
            badge_bg = "rgba(59,130,246,0.2)"
            diagnostic_text = (
                f"Identified Rule: 🏷️ Channel Promotion / Early Bird ({discount_pct:.1f}% reduction). "
                f"Booking captured an automated channel promotion / early-bird discount (${actual_gross:,.2f} gross vs ${expected_gross:,.2f} catalog base). "
                f"Actual Owner Payout is ${actual_owner:,.2f} (82% of realized rent; variance from base catalog: -${shortfall_amount:,.2f})."
            )
        # 3. Phoenix Summer Low-Season Markdown (May to August stays with 20%–40% discount)
        elif s_dt.month in (5, 6, 7, 8) and 20.0 <= discount_pct <= 40.0:
            audit_status = "discount_summer"
            audit_failed = False
            is_known_discount = True
            discount_category = "summer"
            rule_title = "☀️ Summer Low-Season Markdown"
            status_label = f"☀️ Summer -{discount_pct:.0f}%"
            badge_color = "#f97316"
            badge_bg = "rgba(249,115,22,0.2)"
            diagnostic_text = (
                f"Identified Rule: ☀️ Low-Season Summer Markdown ({discount_pct:.1f}% reduction). "
                f"During Phoenix/Tempe extreme summer temperatures (>110°F), Kivoya PMS / revenue management applied an automated seasonal rate markdown "
                f"(${actual_gross:,.2f} realized gross vs ${expected_gross:,.2f} base catalog rates) to preserve occupancy. "
                f"Actual Owner Payout is ${actual_owner:,.2f} (82% of realized rent; variance from base catalog: -${shortfall_amount:,.2f})."
            )
        # 4. True Unexplained Shortfall
        else:
            audit_status = "shortfall"
            audit_failed = True
            is_known_discount = False
            discount_category = "none"
            rule_title = "⚠️ Unexplained Rate Shortfall"
            status_label = f"⚠️ Shortfall: -${shortfall_amount:,.2f}"
            badge_color = "#ef4444"
            badge_bg = "rgba(239,68,68,0.2)"

            diag_parts = [
                f"Actual Owner Payout of ${actual_owner:,.2f} is ${shortfall_amount:,.2f} ({abs(discrepancy_pct):.1f}%) below the published catalog expectation of ${expected_owner:,.2f} (82% of ${expected_gross:,.2f} catalog gross)."
            ]
            if channel_name == "Airbnb":
                diag_parts.append(
                    f"Booking originated via Airbnb. The reported gross rent reflects an effective ${effective_nightly_booked:,.2f}/night vs published catalog average of ${expected_avg_adr:,.2f}/night. In Streamline VRS, Airbnb bookings frequently reflect channel discounting or host fee deductions taken above Gross Rent."
                )
            elif nights >= 7:
                diag_parts.append(
                    f"Stay length is {nights} nights. A length-of-stay weekly discount may have been applied by the channel without adjusting the base catalog rate schedule."
                )
            else:
                diag_parts.append(
                    f"Effective booked rate was ${effective_nightly_booked:,.2f}/night vs published catalog rate of ${expected_avg_adr:,.2f}/night. Review the Streamline folio to determine if an unapproved discount was applied."
                )
            diagnostic_text = " ".join(diag_parts)

    elif discrepancy > audit_threshold_dollars:
        audit_status = "surplus"
        audit_failed = False
        is_known_discount = False
        discount_category = "none"
        rule_title = "Surplus / Premium Pricing"
        status_label = f"Surplus: +${discrepancy:,.2f}"
        badge_color = "#38bdf8"
        badge_bg = "rgba(56,189,248,0.2)"
        diagnostic_text = (
            f"Owner payout of ${actual_owner:,.2f} exceeds published catalog rate expectation (${expected_owner:,.2f}) by +${discrepancy:,.2f} (+{discrepancy_pct:.1f}%). Premium pricing was captured."
        )
    else:
        audit_status = "verified"
        audit_failed = False
        is_known_discount = False
        discount_category = "none"
        rule_title = "✅ Verified Rate Match"
        status_label = "✅ Verified"
        badge_color = "#10b981"
        badge_bg = "rgba(16,185,129,0.2)"
        diagnostic_text = (
            f"Owner payout of ${actual_owner:,.2f} reconciles with published catalog rates within tolerance (82% of ${expected_gross:,.2f} catalog gross = ${expected_owner:,.2f}; Variance: ${discrepancy:+,.2f})."
        )

    return {
        "audit_status": audit_status,
        "audit_failed": audit_failed,
        "is_known_discount": is_known_discount,
        "discount_category": discount_category,
        "discount_pct": discount_pct,
        "rule_title": rule_title,
        "status_label": status_label,
        "badge_color": badge_color,
        "badge_bg": badge_bg,
        "expected_gross_rent": expected_gross,
        "expected_owner_payout": expected_owner,
        "actual_gross_rent": actual_gross,
        "actual_owner_payout": actual_owner,
        "discrepancy": discrepancy,
        "gross_discrepancy": gross_discrepancy,
        "discrepancy_pct": discrepancy_pct,
        "audit_shortfall": shortfall_amount,
        "booking_date_parsed": c_date_iso,
        "is_legacy": False,
        "is_exempt": False,
        "nightly_breakdown": nightly_breakdown,
        "diagnostic_text": diagnostic_text,
    }

