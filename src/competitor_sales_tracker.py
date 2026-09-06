"""
Competitor Sales Tracking & Absorption Velocity Engine.

Detects when competitor listings stop being available across consecutive daily
pricing snapshots (data/pricing_data_YYYY-MM-DD.json), records verified bookings
into SQLite (data/reservations.db: competitor_sales), and calculates empirical
absorption velocity and 2D Strategy Grids (Lead Time Horizon x Stay Type).
"""

from contextlib import contextmanager
from datetime import datetime, date
import json
import logging
import math
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional, Tuple, Set

logger = logging.getLogger("competitor_sales_tracker")

DEFAULT_DB_PATH = Path("data/reservations.db")
DEFAULT_DATA_DIR = Path("data")
DEFAULT_REGISTRY_PATH = Path("config/comps_registry.json")

# Strategy Baseline Priors (used in Bayesian shrinkage when empirical sample size < 3)
PRIOR_WEEKEND_TARGET_PCT = 65.0
PRIOR_WEEKEND_P75_PCT = 75.0
PRIOR_MIDWEEK_TARGET_PCT = 45.5
PRIOR_MIDWEEK_P75_PCT = 55.0
BAYESIAN_SHRINKAGE_K = 3.0  # Equivalent pseudo-observations of prior weight


class CompetitorSalesTracker:
    """Tracks competitor booking events across daily snapshots and computes absorption strategy."""

    def __init__(
        self,
        db_path: Path = DEFAULT_DB_PATH,
        data_dir: Path = DEFAULT_DATA_DIR,
        registry_path: Path = DEFAULT_REGISTRY_PATH,
    ):
        self.db_path = Path(db_path)
        self.data_dir = Path(data_dir)
        self.registry_path = Path(registry_path)
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
        """Create competitor_sales table and indices if not present."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS competitor_sales (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    listing_id TEXT NOT NULL,
                    listing_name TEXT,
                    tier TEXT,
                    location TEXT,
                    check_in TEXT NOT NULL,
                    check_out TEXT NOT NULL,
                    nights INTEGER NOT NULL,
                    segment_type TEXT NOT NULL,
                    detected_date TEXT NOT NULL,
                    lead_time_days INTEGER NOT NULL,
                    last_observed_rate REAL NOT NULL,
                    last_observed_adj_rate REAL,
                    last_observed_percentile REAL,
                    composite_score REAL,
                    desirability_ratio REAL,
                    verification_status TEXT NOT NULL,
                    raw_snippet TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(listing_id, check_in, check_out)
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_comp_sales_lead 
                ON competitor_sales(lead_time_days)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_comp_sales_seg 
                ON competitor_sales(segment_type)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_comp_sales_detected 
                ON competitor_sales(detected_date)
            """)
            conn.commit()

    def load_registered_comps(self) -> Dict[str, Dict[str, Any]]:
        """Load curated comps from config/comps_registry.json keyed by listing ID."""
        if not self.registry_path.exists():
            return {}
        try:
            data = json.loads(self.registry_path.read_text(encoding="utf-8"))
            merged: Dict[str, Dict[str, Any]] = {}
            for tier in ("tier_a", "tier_b"):
                for cid, cinfo in data.get(tier, {}).items():
                    item = dict(cinfo)
                    item["tier"] = tier
                    merged[str(cid)] = item
            return merged
        except Exception as e:
            logger.error(f"Error loading comps registry: {e}")
            return {}

    def extract_intervals_from_snapshot(self, snapshot_path: Path) -> Tuple[str, Dict[Tuple[str, str], Dict[str, Any]]]:
        """
        Extract report date and intervals mapping from a pricing snapshot.
        Returns (report_date, { (check_in, check_out): interval_data })
        """
        data = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))
        report_date = data.get("report_date") or ""
        if not report_date:
            # Fallback parse from filename: pricing_data_YYYY-MM-DD.json
            stem = Path(snapshot_path).stem
            if stem.startswith("pricing_data_"):
                report_date = stem.replace("pricing_data_", "")

        intervals: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for group_name in ("urgent_intervals", "moderate_intervals", "informational_intervals"):
            for item in data.get(group_name, []):
                cin = item.get("check_in")
                cout = item.get("check_out")
                if not cin or not cout:
                    continue
                key = (cin, cout)
                # Map comps by string listing ID
                comps_by_id: Dict[str, Dict[str, Any]] = {}
                for c in item.get("comps_list", []):
                    cid = str(c.get("listing_id") or "")
                    if cid:
                        comps_by_id[cid] = c

                intervals[key] = {
                    "check_in": cin,
                    "check_out": cout,
                    "nights": item.get("nights", 3),
                    "segment_type": item.get("segment_type", "midweek"),
                    "lead_time_days": item.get("lead_time_days", 0),
                    "comps": comps_by_id,
                }

        return report_date, intervals

    def diff_snapshots(
        self,
        prev_snapshot_path: Path,
        curr_snapshot_path: Path,
        verify_calendar: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Compare consecutive snapshots (prev -> curr).
        Identifies listings present in prev for interval (check_in, check_out) that are absent in curr.
        Records validated sales into SQLite.
        """
        prev_date, prev_intervals = self.extract_intervals_from_snapshot(prev_snapshot_path)
        curr_date, curr_intervals = self.extract_intervals_from_snapshot(curr_snapshot_path)

        if not curr_date:
            curr_date = date.today().isoformat()

        registered_comps = self.load_registered_comps()
        detected_sales: List[Dict[str, Any]] = []

        curr_d = datetime.strptime(curr_date, "%Y-%m-%d").date()

        with self._get_connection() as conn:
            cursor = conn.cursor()

            for key, prev_data in prev_intervals.items():
                if key not in curr_intervals:
                    # Interval not scanned or disappeared entirely (e.g. booked for our own property)
                    continue

                curr_data = curr_intervals[key]
                curr_comp_ids = set(curr_data["comps"].keys())

                check_in_str = prev_data["check_in"]
                check_out_str = prev_data["check_out"]
                nights = prev_data["nights"]
                segment_type = prev_data["segment_type"]

                cin_d = datetime.strptime(check_in_str, "%Y-%m-%d").date()
                lead_time_days = max(0, (cin_d - curr_d).days)

                # Collect all prev comps' effective nightly rates to calculate percentiles
                all_prev_comps = list(prev_data["comps"].values())

                for cid, cinfo in prev_data["comps"].items():
                    if cid in curr_comp_ids:
                        continue  # Still available

                    # Disappeared from search! Verify if in registered comps cohort
                    is_registered = cid in registered_comps
                    reg_info = registered_comps.get(cid, {})

                    # Extract price & specs
                    raw_eff = cinfo.get("effective_nightly")
                    if raw_eff is None:
                        total_p = cinfo.get("total_price") or 0.0
                        raw_eff = round(total_p / max(1, nights), 2)
                    else:
                        raw_eff = float(raw_eff)

                    # Quality adjustments
                    desirability_ratio = float(reg_info.get("desirability_ratio") or 1.0)
                    composite_score = float(reg_info.get("composite_score") or 85.0)
                    tier = reg_info.get("tier") or ("tier_a" if (cinfo.get("bedrooms") or 0) >= 6 else "tier_b")
                    location = reg_info.get("location") or cinfo.get("location") or "Scottsdale"
                    listing_name = reg_info.get("name") or cinfo.get("name") or cinfo.get("title") or f"Listing {cid}"

                    adj_rate = round(raw_eff / desirability_ratio, 2) if desirability_ratio > 0 else raw_eff

                    # Calculate market percentile rank in the previous snapshot's distribution
                    # Percentile = (Number of comps with rate <= comp_rate / total comps) * 100
                    if all_prev_comps:
                        rates_lower_or_equal = sum(
                            1 for oc in all_prev_comps
                            if (oc.get("effective_nightly") or 0.0) <= raw_eff
                        )
                        percentile = round((rates_lower_or_equal / len(all_prev_comps)) * 100.0, 1)
                    else:
                        percentile = 50.0

                    # Status classification
                    # For registered comps, absence from interval search after being present is strong sale signal.
                    verification_status = "CONFIRMED_BLOCKED" if is_registered else "SEARCH_ABSENT"

                    sale_record = {
                        "listing_id": cid,
                        "listing_name": listing_name,
                        "tier": tier,
                        "location": location,
                        "check_in": check_in_str,
                        "check_out": check_out_str,
                        "nights": nights,
                        "segment_type": segment_type,
                        "detected_date": curr_date,
                        "lead_time_days": lead_time_days,
                        "last_observed_rate": raw_eff,
                        "last_observed_adj_rate": adj_rate,
                        "last_observed_percentile": percentile,
                        "composite_score": composite_score,
                        "desirability_ratio": desirability_ratio,
                        "verification_status": verification_status,
                        "raw_snippet": cinfo.get("raw_snippet") or cinfo.get("price_snippet") or "",
                    }

                    # Upsert into SQLite (preserves earliest detection if already recorded)
                    cursor.execute("""
                        INSERT INTO competitor_sales (
                            listing_id, listing_name, tier, location,
                            check_in, check_out, nights, segment_type,
                            detected_date, lead_time_days,
                            last_observed_rate, last_observed_adj_rate, last_observed_percentile,
                            composite_score, desirability_ratio, verification_status, raw_snippet
                        ) VALUES (
                            :listing_id, :listing_name, :tier, :location,
                            :check_in, :check_out, :nights, :segment_type,
                            :detected_date, :lead_time_days,
                            :last_observed_rate, :last_observed_adj_rate, :last_observed_percentile,
                            :composite_score, :desirability_ratio, :verification_status, :raw_snippet
                        )
                        ON CONFLICT(listing_id, check_in, check_out) DO UPDATE SET
                            detected_date = CASE 
                                WHEN excluded.detected_date < competitor_sales.detected_date 
                                THEN excluded.detected_date 
                                ELSE competitor_sales.detected_date 
                            END
                    """, sale_record)

                    if cursor.rowcount > 0:
                        detected_sales.append(sale_record)

            conn.commit()

        logger.info(f"Diff {prev_date} -> {curr_date}: Detected {len(detected_sales)} sales events.")
        return detected_sales

    def backfill_all_snapshots(self, data_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
        """
        Scan all pricing_data_YYYY-MM-DD.json files in data_dir, sort chronologically,
        and diff consecutive pairs to populate the database.
        """
        target_dir = Path(data_dir or self.data_dir)
        snapshot_files = sorted(target_dir.glob("pricing_data_*.json"))
        if len(snapshot_files) < 2:
            logger.info(f"Fewer than 2 snapshots found in {target_dir}; nothing to backfill.")
            return []

        all_detected: List[Dict[str, Any]] = []
        for i in range(len(snapshot_files) - 1):
            p1 = snapshot_files[i]
            p2 = snapshot_files[i + 1]
            sales = self.diff_snapshots(p1, p2)
            all_detected.extend(sales)

        return all_detected

    def process_latest_snapshot(self, curr_snapshot_path: Path) -> List[Dict[str, Any]]:
        """
        Given the newly created snapshot, find the immediate predecessor snapshot
        in data/ and run diff.
        """
        curr_path = Path(curr_snapshot_path)
        all_snapshots = sorted(self.data_dir.glob("pricing_data_*.json"))
        
        # Find index of curr_path in all_snapshots
        idx = None
        for i, p in enumerate(all_snapshots):
            if p.resolve() == curr_path.resolve() or p.name == curr_path.name:
                idx = i
                break

        if idx is None or idx == 0:
            logger.info(f"No previous snapshot found before {curr_path.name}.")
            return []

        prev_path = all_snapshots[idx - 1]
        return self.diff_snapshots(prev_path, curr_path)

    def get_all_sales(self, verification_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch recorded competitor sales from database."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if verification_filter:
                cursor.execute("""
                    SELECT * FROM competitor_sales 
                    WHERE verification_status = ? 
                    ORDER BY detected_date DESC, check_in ASC
                """, (verification_filter,))
            else:
                cursor.execute("""
                    SELECT * FROM competitor_sales 
                    ORDER BY detected_date DESC, check_in ASC
                """)
            rows = cursor.fetchall()
            return [dict(r) for r in rows]

    @staticmethod
    def _categorize_lead_horizon(days: int) -> str:
        """Categorize lead time days into strategic horizons."""
        if days > 90:
            return ">90d"
        elif days >= 31:
            return "31–90d"
        elif days >= 15:
            return "15–30d"
        else:
            return "≤14d"

    @staticmethod
    def _normalize_segment_type(seg: str) -> str:
        """Map segment type to weekend or midweek."""
        s = str(seg or "").lower()
        if "weekend" in s or "mix" in s:
            return "weekend"
        return "midweek"

    def compute_strategy_grid(self) -> Dict[str, Any]:
        """
        Aggregate recorded sales into the 2D Strategy Matrix (Horizon x Stay Type)
        and compute empirical percentiles with Bayesian shrinkage.
        """
        sales = self.get_all_sales(verification_filter="CONFIRMED_BLOCKED")
        
        # Horizons ordered from far-out to last-minute
        horizons = [">90d", "31–90d", "15–30d", "≤14d"]
        stay_types = ["weekend", "midweek"]

        # Cell buckets: (horizon, stay_type) -> list of sales
        buckets: Dict[Tuple[str, str], List[Dict[str, Any]]] = {
            (h, t): [] for h in horizons for t in stay_types
        }

        total_lead_days = 0
        total_rates = 0.0
        all_pcts: List[float] = []

        for s in sales:
            h = self._categorize_lead_horizon(s["lead_time_days"])
            t = self._normalize_segment_type(s["segment_type"])
            buckets[(h, t)].append(s)

            total_lead_days += s["lead_time_days"]
            total_rates += (s.get("last_observed_rate") or 0.0)
            if s.get("last_observed_percentile") is not None:
                all_pcts.append(float(s["last_observed_percentile"]))

        grid: Dict[str, Any] = {}
        for h in horizons:
            grid[h] = {}
            for t in stay_types:
                cell_sales = buckets[(h, t)]
                n = len(cell_sales)

                # Determine baseline prior
                prior_target = PRIOR_WEEKEND_TARGET_PCT if t == "weekend" else PRIOR_MIDWEEK_TARGET_PCT
                prior_p75 = PRIOR_WEEKEND_P75_PCT if t == "weekend" else PRIOR_MIDWEEK_P75_PCT

                if n > 0:
                    pcts = sorted(float(cs["last_observed_percentile"]) for cs in cell_sales)
                    rates = [float(cs["last_observed_rate"]) for cs in cell_sales]
                    
                    # Median (p50)
                    mid = n // 2
                    if n % 2 == 1:
                        p50 = pcts[mid]
                    else:
                        p50 = (pcts[mid - 1] + pcts[mid]) / 2.0

                    # 75th percentile
                    p75_idx = int(math.ceil(0.75 * n)) - 1
                    p75 = pcts[max(0, min(p75_idx, n - 1))]

                    # Bayesian shrinkage target
                    # hat_Y = (n * p50 + k * prior) / (n + k)
                    recommended_target = round((n * p50 + BAYESIAN_SHRINKAGE_K * prior_target) / (n + BAYESIAN_SHRINKAGE_K), 1)
                    recommended_aggressive = round((n * p75 + BAYESIAN_SHRINKAGE_K * prior_p75) / (n + BAYESIAN_SHRINKAGE_K), 1)
                    avg_rate = round(sum(rates) / n, 2)
                    min_rate = min(rates)
                    max_rate = max(rates)
                else:
                    p50 = prior_target
                    p75 = prior_p75
                    recommended_target = prior_target
                    recommended_aggressive = prior_p75
                    avg_rate = 0.0
                    min_rate = 0.0
                    max_rate = 0.0

                grid[h][t] = {
                    "count": n,
                    "empirical_p50": round(p50, 1),
                    "empirical_p75": round(p75, 1),
                    "recommended_target": recommended_target,
                    "recommended_aggressive": recommended_aggressive,
                    "baseline_prior": prior_target,
                    "avg_rate": avg_rate,
                    "min_rate": min_rate,
                    "max_rate": max_rate,
                    "is_empirical": n >= 3,
                }

        # Overall summary KPIs
        total_sales_count = len(sales)
        all_pcts.sort()
        if all_pcts:
            mid = len(all_pcts) // 2
            overall_median_pct = all_pcts[mid] if len(all_pcts) % 2 == 1 else (all_pcts[mid - 1] + all_pcts[mid]) / 2.0
        else:
            overall_median_pct = 55.0

        avg_lead_time = round(total_lead_days / total_sales_count, 1) if total_sales_count > 0 else 0.0
        avg_rate_overall = round(total_rates / total_sales_count, 2) if total_sales_count > 0 else 0.0

        return {
            "total_sales": total_sales_count,
            "overall_median_percentile": round(overall_median_pct, 1),
            "avg_lead_time_days": avg_lead_time,
            "avg_rate": avg_rate_overall,
            "weekend_sales_count": sum(1 for s in sales if self._normalize_segment_type(s["segment_type"]) == "weekend"),
            "midweek_sales_count": sum(1 for s in sales if self._normalize_segment_type(s["segment_type"]) == "midweek"),
            "grid": grid,
            "horizons": horizons,
        }

