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
from typing import Dict, List, Optional, Any, Tuple


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
                       last_scraped_at
                FROM reservations
                ORDER BY start_date DESC
            """)
            rows = [dict(row) for row in cursor.fetchall()]

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
            return [dict(r) for r in cursor.fetchall()]

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

                if curr_date <= today:
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
