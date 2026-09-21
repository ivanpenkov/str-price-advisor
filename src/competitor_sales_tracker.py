"""
Competitor Sales Tracking & Absorption Velocity Engine.

Detects when competitor listings stop being available across consecutive daily
pricing snapshots (data/pricing_data_YYYY-MM-DD.json), records verified bookings
into SQLite (data/reservations.db: competitor_sales), and calculates empirical
absorption velocity and 2D Strategy Grids (Lead Time Horizon x Stay Type).
"""

import asyncio
import concurrent.futures
from contextlib import contextmanager
from datetime import datetime, date, timedelta
import json
import logging
import math
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple, Set, Union

logger = logging.getLogger("competitor_sales_tracker")

DEFAULT_DB_PATH = Path("data/reservations.db")
DEFAULT_DATA_DIR = Path("data")
DEFAULT_REGISTRY_PATH = Path("config/comps_registry.json")

# Strategy Baseline Priors (used in Bayesian shrinkage and strategic floor clamping)
# 2D Strategy Matrix per docs/nightly_rate_targets_requirements.md and docs/nightly_rate_targets_design.md
DEFAULT_HORIZON_PRIORS = {
    ">180d": {
        "weekend": {"target": 85.0, "floor": 80.0, "p75": 90.0},
        "midweek": {"target": 50.0, "floor": 45.0, "p75": 60.0},
    },
    "91–180d": {
        "weekend": {"target": 67.5, "floor": 65.0, "p75": 75.0},
        "midweek": {"target": 47.5, "floor": 45.0, "p75": 55.0},
    },
    "31–90d": {
        "weekend": {"target": 62.5, "floor": 60.0, "p75": 70.0},
        "midweek": {"target": 32.5, "floor": 30.0, "p75": 45.0},
    },
    "≤30d": {
        "weekend": {"target": 42.5, "floor": 40.0, "p75": 50.0},
        "midweek": {"target": 30.0, "floor": 28.0, "p75": 38.0},
    },
}
# Keep HORIZON_PRIORS referencing DEFAULT_HORIZON_PRIORS for backward compatibility
HORIZON_PRIORS = DEFAULT_HORIZON_PRIORS

PRIOR_WEEKEND_TARGET_PCT = 65.0
PRIOR_WEEKEND_P75_PCT = 75.0
PRIOR_MIDWEEK_TARGET_PCT = 45.5
PRIOR_MIDWEEK_P75_PCT = 55.0
BAYESIAN_SHRINKAGE_K = 5.0  # Equivalent pseudo-observations of prior weight
MIN_SAMPLE_SIZE = 5         # Minimum verified comp bookings to qualify as empirical
OPERATIONAL_FLOORS = {"weekend": 450.0, "midweek": 300.0}
ENFORCE_MONOTONIC_TAPERING = True


def format_comp_sales_line(label: str, sales: List[Dict[str, Any]]) -> str:
    """
    Format a list of comp sale records into a concise single-line summary:
      '   - Comp sales recorded: $2,120 (74.1%), $2,000 (64.1%)'
      '   - Comp sales erased: $3,120 (84.1%)'
    Returns empty string if sales list is empty.
    """
    if not sales:
        return ""
    items = []
    for s in sales:
        raw_p = s.get("last_observed_rate")
        if raw_p is None:
            raw_p = s.get("realized_price") or 0.0
        p = round(float(raw_p))
        raw_pct = s.get("last_observed_percentile")
        if raw_pct is None:
            raw_pct = s.get("market_percentile")
        try:
            pct = float(raw_pct) if raw_pct is not None else 0.0
            if math.isnan(pct):
                pct = 0.0
        except (ValueError, TypeError):
            pct = 0.0
        items.append(f"${p:,} ({pct:.1f}%)")
    return f"   - {label}: {', '.join(items)}"


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
        self._cached_strategy_grid: Optional[Dict[str, Any]] = None
        self._cached_prev_intervals: Optional[Dict[Tuple[str, str], Dict[str, Any]]] = None
        self._init_db()

    @contextmanager
    def _get_connection(self):
        from src.database import get_db_connection
        conn = get_db_connection(self.db_path)
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
        """Load curated comps from config/comps_registry.json keyed by listing ID, strictly ignoring disqualified/excluded comps."""
        if not self.registry_path.exists():
            return {}
        try:
            data = json.loads(self.registry_path.read_text(encoding="utf-8"))
            excluded_ids = set(str(k) for k in data.get("excluded_comps", {}).keys())
            disqualified_ids = set(str(k) for k in data.get("disqualified", {}).keys())
            merged: Dict[str, Dict[str, Any]] = {}
            for tier in ("tier_a", "tier_b"):
                for cid, cinfo in data.get(tier, {}).items():
                    cid_str = str(cid)
                    if cid_str in excluded_ids or cid_str in disqualified_ids:
                        continue
                    if cinfo.get("is_valid_comp") is False:
                        continue
                    item = dict(cinfo)
                    item["tier"] = tier
                    merged[cid_str] = item
            return merged
        except Exception as e:
            logger.error(f"Error loading comps registry: {e}")
            return {}

    def purge_sales_for_disqualified_comps(self) -> int:
        """Purge competitor sales for any listings marked disqualified or in excluded_comps."""
        if not self.registry_path.exists():
            return 0
        try:
            data = json.loads(self.registry_path.read_text(encoding="utf-8"))
            bad_ids = set(str(k) for k in data.get("excluded_comps", {}).keys()) | set(str(k) for k in data.get("disqualified", {}).keys())
            for tier in ("tier_a", "tier_b"):
                for cid, cinfo in data.get(tier, {}).items():
                    if cinfo.get("is_valid_comp") is False:
                        bad_ids.add(str(cid))
            if not bad_ids:
                return 0
            with self._get_connection() as conn:
                cursor = conn.cursor()
                placeholders = ",".join("?" for _ in bad_ids)
                cursor.execute(f"DELETE FROM competitor_sales WHERE listing_id IN ({placeholders})", list(bad_ids))
                conn.commit()
                purged = cursor.rowcount
                if purged > 0:
                    self._cached_strategy_grid = None
                    logger.info(f"Purged {purged} sales records for disqualified comps from SQLite.")
                return purged
        except Exception as e:
            logger.error(f"Error purging sales for disqualified comps: {e}")
            return 0

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

                is_live = item.get("is_live_scan")
                if is_live is None:
                    # Legacy check: synthetic fallback cohorts always had 90+ listings
                    is_live = (len(comps_by_id) < 90)
                else:
                    is_live = bool(is_live)

                intervals[key] = {
                    "check_in": cin,
                    "check_out": cout,
                    "nights": item.get("nights", 3),
                    "segment_type": item.get("segment_type", "midweek"),
                    "lead_time_days": item.get("lead_time_days", 0),
                    "is_live_scan": is_live,
                    "comps": comps_by_id,
                }

        return report_date, intervals

    def prepare_predecessor_snapshot(
        self,
        prev_snapshot_path: Optional[Union[str, Path]] = None,
        curr_date: Optional[str] = None,
    ) -> Dict[Tuple[str, str], Dict[str, Any]]:
        """
        Pre-load and cache predecessor snapshot intervals in memory.
        If prev_snapshot_path is provided, loads that specific snapshot.
        Otherwise, scans backwards through existing pricing_data_*.json files
        to map every known interval to its latest prior observation.
        """
        if prev_snapshot_path is not None:
            _, p_ints = self.extract_intervals_from_snapshot(Path(prev_snapshot_path))
            self._cached_prev_intervals = p_ints
            return self._cached_prev_intervals

        all_snapshots = sorted(self.data_dir.glob("pricing_data_*.json"))
        if not all_snapshots:
            self._cached_prev_intervals = {}
            return self._cached_prev_intervals

        if curr_date is None:
            curr_date = date.today().isoformat()

        target_file_stem = f"pricing_data_{curr_date}"
        
        # Start backwards from the latest snapshot that is strictly prior to curr_date
        start_idx = len(all_snapshots) - 1
        while start_idx >= 0 and all_snapshots[start_idx].stem >= target_file_stem:
            start_idx -= 1

        if start_idx < 0:
            if len(all_snapshots) >= 2:
                start_idx = len(all_snapshots) - 2
            elif len(all_snapshots) == 1 and all_snapshots[0].stem < target_file_stem:
                start_idx = 0
            else:
                self._cached_prev_intervals = {}
                return self._cached_prev_intervals

        merged_intervals: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for i in range(start_idx, -1, -1):
            p = all_snapshots[i]
            try:
                _, p_ints = self.extract_intervals_from_snapshot(p)
                for key, data in p_ints.items():
                    if key not in merged_intervals:
                        merged_intervals[key] = data
            except Exception as e:
                logger.warning(f"Error loading snapshot {p} during predecessor prep: {e}")

        self._cached_prev_intervals = merged_intervals
        logger.info(f"Predecessor snapshot initialized with {len(merged_intervals)} cached intervals.")
        return self._cached_prev_intervals

    def diff_snapshots(
        self,
        prev_snapshot_path: Path,
        curr_snapshot_path: Path,
        verify_calendar: bool = False,
        target_interval_keys: Optional[Set[Tuple[str, str]]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Compare consecutive snapshots (prev -> curr).
        Identifies listings present in prev for interval (check_in, check_out) that are absent in curr.
        Records validated sales into SQLite.
        If target_interval_keys is specified, only diff those specific (check_in, check_out) intervals.
        """
        prev_date, prev_intervals = self.extract_intervals_from_snapshot(prev_snapshot_path)
        curr_date, curr_intervals = self.extract_intervals_from_snapshot(curr_snapshot_path)

        if not curr_date:
            curr_date = date.today().isoformat()

        registered_comps = self.load_registered_comps()
        detected_sales: List[Dict[str, Any]] = []
        purged_count = 0

        curr_d = datetime.strptime(curr_date, "%Y-%m-%d").date()

        # Active Reconciliation: If a listing is observed AVAILABLE in current snapshot,
        # it cannot be sold. Purge any previously recorded premature sales for these intervals.
        with self._get_connection() as conn:
            cursor = conn.cursor()
            for (c_in_k, c_out_k), c_data_k in curr_intervals.items():
                if not c_data_k.get("is_live_scan"):
                    continue
                for c_id_k in c_data_k["comps"].keys():
                    cursor.execute("""
                        DELETE FROM competitor_sales 
                        WHERE listing_id = ? AND check_in = ? AND check_out = ?
                    """, (c_id_k, c_in_k, c_out_k))
                    purged_count += cursor.rowcount
            conn.commit()

        for key, prev_data in prev_intervals.items():
            if target_interval_keys is not None and key not in target_interval_keys:
                continue
            if key not in curr_intervals:
                # Interval not scanned or disappeared entirely (e.g. booked for our own property)
                continue

            curr_data = curr_intervals[key]

            # CRITICAL: Both previous and current interval MUST be live platform scans!
            # If either snapshot used synthetic/fallback cohort items, any disappearance
            # is an artifact of transitioning between synthetic placeholders and real sweeps.
            if not prev_data.get("is_live_scan") or not curr_data.get("is_live_scan"):
                continue

            # Focus strictly on curated registered comps
            prev_reg_comps = {
                cid: c for cid, c in prev_data["comps"].items()
                if cid in registered_comps
            } if registered_comps else prev_data["comps"]
            curr_reg_comps = {
                cid: c for cid, c in curr_data["comps"].items()
                if cid in registered_comps
            } if registered_comps else curr_data["comps"]

            # Guard against partial / degraded search scrapes:
            # If a market interval had >= 8 registered comps previously, but current scrape extracted
            # fewer than 4 comps (or dropped by > 80%), this indicates a transient search
            # extraction failure, blocked pagination, or network throttle, not a mass sellout.
            prev_comp_count = len(prev_reg_comps)
            curr_comp_count = len(curr_reg_comps)
            if prev_comp_count >= 8 and (curr_comp_count <= 3 or (curr_comp_count / prev_comp_count) < 0.20):
                logger.warning(
                    f"Scrape degradation detected for interval {key}: registered comps dropped from {prev_comp_count} to {curr_comp_count}. "
                    f"Skipping disappearance detection to prevent false sales."
                )
                continue

            curr_comp_ids = set(curr_data["comps"].keys())

            # Detect corridors/locations that completely dropped to 0 in curr (likely failed/empty scrape)
            prev_loc_counts: Dict[str, int] = {}
            for cid, c in prev_reg_comps.items():
                loc = c.get("location") or registered_comps.get(cid, {}).get("location") or "Unknown"
                prev_loc_counts[loc] = prev_loc_counts.get(loc, 0) + 1

            curr_loc_counts: Dict[str, int] = {}
            for cid, c in curr_reg_comps.items():
                loc = c.get("location") or registered_comps.get(cid, {}).get("location") or "Unknown"
                curr_loc_counts[loc] = curr_loc_counts.get(loc, 0) + 1

            dropped_corridors = {
                loc for loc, count in prev_loc_counts.items()
                if count >= 2 and curr_loc_counts.get(loc, 0) == 0
            }

            check_in_str = prev_data["check_in"]
            check_out_str = prev_data["check_out"]
            nights = prev_data["nights"]
            segment_type = prev_data["segment_type"]

            cin_d = datetime.strptime(check_in_str, "%Y-%m-%d").date()
            lead_time_days = max(0, (cin_d - curr_d).days)

            # Collect all prev comps' effective nightly rates to calculate percentiles (curated comps only)
            all_prev_comps = list(prev_reg_comps.values())

            # Pre-scan any local search cache files for this interval (corridor or single-comp)
            cached_available_ids = set()
            cache_dir = self.data_dir / "cache"
            if cache_dir.exists():
                for cf in cache_dir.glob(f"search_{check_in_str}_{check_out_str}_*.json"):
                    try:
                        citems = json.loads(cf.read_text(encoding="utf-8"))
                        if isinstance(citems, list):
                            for item in citems:
                                cid_item = str(item.get("listing_id") or "")
                                eff_rate = item.get("effective_nightly") or item.get("total_price")
                                if cid_item and eff_rate and float(eff_rate) > 0:
                                    cached_available_ids.add(cid_item)
                    except Exception:
                        pass

            for cid, cinfo in prev_reg_comps.items():
                if cid in curr_comp_ids:
                    continue  # Still available in current snapshot

                if cid in cached_available_ids:
                    continue  # Still available in local search/single-comp cache

                c_loc = cinfo.get("location") or registered_comps.get(cid, {}).get("location") or ""
                if any(dc.lower() in c_loc.lower() for dc in dropped_corridors):
                    # The entire corridor dropped to 0 (scrape failure / empty cache), skip false sale
                    continue

                # Disappeared from search! Strictly verify in registered comps cohort
                if cid not in registered_comps:
                    continue  # Focus strictly on curated comps; ignore unregistered organic search churn

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

                # If calendar verification is not enabled, do NOT record unverified search disappearances
                # as sales. Search dropouts without calendar verification are frequently ranking fluctuations.
                if not verify_calendar:
                    logger.info(
                        f"Comp {cid} missing from search results for {check_in_str} -> {check_out_str}, "
                        f"but verify_calendar=False. Skipping sales ledger entry."
                    )
                    continue

                # If calendar verification is enabled, execute verification check via Playwright bridge
                try:
                    accommodates = 16 if tier == "tier_a" else 12
                    res_or_coro = self.verify_listing_availability(
                        listing_id=cid,
                        check_in=check_in_str,
                        check_out=check_out_str,
                        accommodates=accommodates,
                    )
                    if asyncio.iscoroutine(res_or_coro) or hasattr(res_or_coro, "__await__"):
                        try:
                            loop = asyncio.get_running_loop()
                        except RuntimeError:
                            loop = None

                        if loop and loop.is_running():
                            pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                            try:
                                v_res = pool.submit(lambda: asyncio.run(res_or_coro)).result(timeout=35.0)
                            finally:
                                pool.shutdown(wait=False, cancel_futures=True)
                        else:
                            v_res = asyncio.run(res_or_coro)
                    else:
                        v_res = res_or_coro
                except Exception as ve:
                    logger.warning(f"Verification check failed for {cid}: {ve}")
                    v_res = {"unavail": False, "price": None, "available": False, "reason": str(ve)}

                if v_res.get("unavail") is not True:
                    if v_res.get("price") and not v_res.get("unavail"):
                        # Available on Airbnb! Auto-heal by deleting any premature sales and writing to cache
                        with self._get_connection() as conn:
                            cursor = conn.cursor()
                            cursor.execute(
                                "DELETE FROM competitor_sales WHERE listing_id = ? AND check_in = ? AND check_out = ?",
                                (cid, check_in_str, check_out_str),
                            )
                            purged_count += cursor.rowcount
                            conn.commit()
                        total_p = float(v_res["price"])
                        eff_nightly = round(total_p / max(1, nights), 2)
                        cache_dir = self.data_dir / "cache"
                        cache_dir.mkdir(parents=True, exist_ok=True)
                        try:
                            c_file = cache_dir / f"search_{check_in_str}_{check_out_str}_comp_{cid}.json"
                            c_file.write_text(json.dumps([{
                                "listing_id": cid,
                                "check_in": check_in_str,
                                "check_out": check_out_str,
                                "effective_nightly": eff_nightly,
                                "total_price": total_p,
                            }]), encoding="utf-8")
                        except Exception:
                            pass
                        logger.info(f"Comp {cid} missing from broad search but confirmed available on Airbnb (${eff_nightly:.0f}/nt, total ${total_p:.0f}). Discarding false sale.")
                    else:
                        logger.info(f"Comp {cid} calendar could not be confirmed blocked ({v_res.get('reason')}). Skipping sales ledger entry.")
                    continue

                # Confirmed blocked sale (calendar verified)
                verification_status = "CONFIRMED_BLOCKED"

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
                    "created_at": datetime.now().isoformat(),
                }

                # Check if already recorded and upsert into SQLite in an isolated write transaction
                with self._get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT verification_status, detected_date 
                        FROM competitor_sales 
                        WHERE listing_id = ? AND check_in = ? AND check_out = ?
                    """, (cid, check_in_str, check_out_str))
                    existing_row = cursor.fetchone()

                    cursor.execute("""
                        INSERT INTO competitor_sales (
                            listing_id, listing_name, tier, location,
                            check_in, check_out, nights, segment_type,
                            detected_date, lead_time_days,
                            last_observed_rate, last_observed_adj_rate, last_observed_percentile,
                            composite_score, desirability_ratio, verification_status, raw_snippet,
                            created_at
                        ) VALUES (
                            :listing_id, :listing_name, :tier, :location,
                            :check_in, :check_out, :nights, :segment_type,
                            :detected_date, :lead_time_days,
                            :last_observed_rate, :last_observed_adj_rate, :last_observed_percentile,
                            :composite_score, :desirability_ratio, :verification_status, :raw_snippet,
                            COALESCE(:created_at, CURRENT_TIMESTAMP)
                        )
                        ON CONFLICT(listing_id, check_in, check_out) DO UPDATE SET
                            detected_date = CASE 
                                WHEN excluded.detected_date < competitor_sales.detected_date 
                                THEN excluded.detected_date 
                                ELSE competitor_sales.detected_date 
                            END,
                            lead_time_days = CASE
                                WHEN excluded.detected_date < competitor_sales.detected_date
                                THEN excluded.lead_time_days
                                ELSE competitor_sales.lead_time_days
                            END,
                            verification_status = CASE
                                WHEN excluded.verification_status = 'CONFIRMED_BLOCKED'
                                THEN 'CONFIRMED_BLOCKED'
                                ELSE competitor_sales.verification_status
                            END
                    """, sale_record)
                    conn.commit()

                    if existing_row:
                        if existing_row["verification_status"] == "CONFIRMED_BLOCKED":
                            sale_record["verification_status"] = "CONFIRMED_BLOCKED"
                        earliest_date = min(existing_row["detected_date"], curr_date)
                        sale_record["detected_date"] = earliest_date
                        sale_record["lead_time_days"] = max(0, (cin_d - datetime.strptime(str(earliest_date)[:10], "%Y-%m-%d").date()).days)

                    if cursor.rowcount > 0:
                        detected_sales.append(sale_record)

        if detected_sales or purged_count > 0:
            self._cached_strategy_grid = None

        logger.info(f"Diff {prev_date} -> {curr_date}: Detected {len(detected_sales)} sales events.")
        return detected_sales

    def record_direct_sale(
        self,
        listing_id: str,
        check_in: str,
        check_out: str,
        nights: int,
        last_observed_rate: float,
        detected_date: Optional[str] = None,
        segment_type: Optional[str] = None,
        verification_status: str = "CONFIRMED_BLOCKED",
        raw_snippet: str = "",
    ) -> bool:
        """
        Directly record a confirmed competitor sale into SQLite.
        Used when a comp is directly verified as booked/unavailable via live checkout sweep.
        """
        registered_comps = self.load_registered_comps()
        if str(listing_id) not in registered_comps:
            logger.info(f"Skipping direct sale recording for unregistered or disqualified comp {listing_id}")
            return False
        reg_info = registered_comps[str(listing_id)]

        if not detected_date:
            detected_date = date.today().isoformat()

        cin_d = datetime.strptime(check_in, "%Y-%m-%d").date()
        curr_d = datetime.strptime(detected_date, "%Y-%m-%d").date()
        lead_time_days = max(0, (cin_d - curr_d).days)

        if not segment_type:
            segment_type = "weekend" if cin_d.weekday() in (3, 4, 5) else "midweek"

        desirability_ratio = float(reg_info.get("desirability_ratio") or 1.0)
        composite_score = float(reg_info.get("composite_score") or 85.0)
        tier = reg_info.get("tier") or "tier_a"
        location = reg_info.get("location") or "Scottsdale"
        listing_name = reg_info.get("name") or f"Listing {listing_id}"
        adj_rate = round(last_observed_rate / desirability_ratio, 2) if desirability_ratio > 0 else last_observed_rate

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO competitor_sales (
                    listing_id, listing_name, tier, location,
                    check_in, check_out, nights, segment_type,
                    detected_date, lead_time_days,
                    last_observed_rate, last_observed_adj_rate, last_observed_percentile,
                    composite_score, desirability_ratio, verification_status, raw_snippet,
                    created_at
                ) VALUES (
                    :listing_id, :listing_name, :tier, :location,
                    :check_in, :check_out, :nights, :segment_type,
                    :detected_date, :lead_time_days,
                    :last_observed_rate, :last_observed_adj_rate, :last_observed_percentile,
                    :composite_score, :desirability_ratio, :verification_status, :raw_snippet,
                    COALESCE(:created_at, CURRENT_TIMESTAMP)
                )
                ON CONFLICT(listing_id, check_in, check_out) DO UPDATE SET
                    detected_date = CASE 
                        WHEN excluded.detected_date < competitor_sales.detected_date 
                        THEN excluded.detected_date 
                        ELSE competitor_sales.detected_date 
                    END,
                    lead_time_days = CASE
                        WHEN excluded.detected_date < competitor_sales.detected_date 
                        THEN excluded.lead_time_days 
                        ELSE competitor_sales.lead_time_days 
                    END,
                    verification_status = CASE
                        WHEN excluded.verification_status = 'CONFIRMED_BLOCKED'
                        THEN 'CONFIRMED_BLOCKED'
                        ELSE competitor_sales.verification_status
                    END
            """, {
                "listing_id": str(listing_id),
                "listing_name": listing_name,
                "tier": tier,
                "location": location,
                "check_in": check_in,
                "check_out": check_out,
                "nights": nights,
                "segment_type": segment_type,
                "detected_date": detected_date,
                "lead_time_days": lead_time_days,
                "last_observed_rate": last_observed_rate,
                "last_observed_adj_rate": adj_rate,
                "last_observed_percentile": 50.0,
                "composite_score": composite_score,
                "desirability_ratio": desirability_ratio,
                "verification_status": verification_status,
                "raw_snippet": raw_snippet or f"Direct Verified Checkout Booking | {listing_name}",
                "created_at": datetime.now().isoformat(),
            })

            conn.commit()
            self._cached_strategy_grid = None
            return cursor.rowcount > 0

    def backfill_all_snapshots(self, data_dir: Optional[Path] = None, verify_calendar: bool = False) -> List[Dict[str, Any]]:
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
            sales = self.diff_snapshots(p1, p2, verify_calendar=verify_calendar)
            all_detected.extend(sales)

        return all_detected

    def process_latest_snapshot(self, curr_snapshot_path: Path, verify_calendar: bool = False) -> List[Dict[str, Any]]:
        """
        Given the newly created snapshot, find the most recent predecessor snapshot
        for each interval in curr_snapshot and run diffs. This properly bridges
        daily quickscans (12 intervals) and weekly fullscans (79 intervals) so
        no intervals are skipped or left unmonitored.
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

        curr_date, curr_intervals = self.extract_intervals_from_snapshot(curr_path)
        if not curr_intervals:
            return []

        # Find the latest predecessor snapshot for each interval in curr_intervals
        # Map: prev_path -> set of interval keys
        intervals_by_prev_path: Dict[Path, Set[Tuple[str, str]]] = {}
        prior_intervals_cache: Dict[Path, Dict[Tuple[str, str], Dict[str, Any]]] = {}

        for key in curr_intervals.keys():
            # Search backwards through prior snapshots
            for i in range(idx - 1, -1, -1):
                prev_p = all_snapshots[i]
                if prev_p not in prior_intervals_cache:
                    _, p_ints = self.extract_intervals_from_snapshot(prev_p)
                    prior_intervals_cache[prev_p] = p_ints

                if key in prior_intervals_cache[prev_p]:
                    intervals_by_prev_path.setdefault(prev_p, set()).add(key)
                    break

        if not intervals_by_prev_path:
            # Fallback to immediate predecessor if no matching intervals found in backwards lookup
            prev_path = all_snapshots[idx - 1]
            return self.diff_snapshots(prev_path, curr_path, verify_calendar=verify_calendar)

        all_detected: List[Dict[str, Any]] = []
        for prev_p, target_keys in intervals_by_prev_path.items():
            sales = self.diff_snapshots(
                prev_snapshot_path=prev_p,
                curr_snapshot_path=curr_path,
                verify_calendar=verify_calendar,
                target_interval_keys=target_keys,
            )
            all_detected.extend(sales)

        return all_detected

    def purge_sales_by_ids(self, sale_ids: List[int]) -> int:
        """Purge specific sale records by ID from competitor_sales table."""
        if not sale_ids:
            return 0
        with self._get_connection() as conn:
            cursor = conn.cursor()
            placeholders = ",".join("?" for _ in sale_ids)
            cursor.execute(f"DELETE FROM competitor_sales WHERE id IN ({placeholders})", sale_ids)
            conn.commit()
            purged = cursor.rowcount
            if purged > 0:
                self._cached_strategy_grid = None
            logger.info(f"Purged {purged} invalid sales by ID.")
            return purged

    def reconcile_with_latest_snapshot(self, snapshot_path: Optional[Any] = None) -> int:
        """
        Reconcile the competitor_sales database with the latest market snapshot,
        local search caches, and verified audit results.
        If a property/stay interval is observed to be AVAILABLE in the latest snapshot or cache,
        it cannot possibly be a confirmed booking. Any premature or erroneous sale record is purged.
        Returns the number of invalidated/purged records.
        """
        self.purge_sales_for_disqualified_comps()

        if snapshot_path is None:
            all_snaps = sorted(self.data_dir.glob("pricing_data_*.json"))
            if not all_snaps:
                return 0
            snapshot_path = all_snaps[-1]

        target_path = Path(snapshot_path)
        latest_available = set()

        if target_path.exists():
            try:
                d = json.loads(target_path.read_text(encoding="utf-8"))
                intervals = d.get("urgent_intervals", []) + d.get("moderate_intervals", []) + d.get("informational_intervals", [])
                for s in intervals:
                    cin = s.get("check_in")
                    cout = s.get("check_out")
                    for c in s.get("comps_list", []):
                        cid = str(c.get("listing_id") or "")
                        rate = c.get("effective_nightly")
                        if cid and rate and float(rate) > 0:
                            latest_available.add((cid, cin, cout))
            except Exception as e:
                logger.warning(f"Error reading snapshot {target_path}: {e}")

        # Check all local search cache files (both corridor searches and single-comp sweeps)
        cache_dir = self.data_dir / "cache"
        if cache_dir.exists():
            for cf in cache_dir.glob("search_*.json"):
                parts = cf.stem.split("_")
                if len(parts) >= 3:
                    cin = parts[1]
                    cout = parts[2]
                    try:
                        cdata = json.loads(cf.read_text(encoding="utf-8"))
                        if isinstance(cdata, list):
                            for item in cdata:
                                cid = str(item.get("listing_id") or "")
                                rate = item.get("effective_nightly") or item.get("total_price")
                                if cid and rate and float(rate) > 0:
                                    latest_available.add((cid, cin, cout))
                    except Exception:
                        pass

        # Check verified audit results file if present (e.g. from live checkout sweeps)
        audit_file = self.data_dir / "audit_sales_results.json"
        if audit_file.exists():
            try:
                audit_records = json.loads(audit_file.read_text(encoding="utf-8"))
                for ar in audit_records:
                    if ar.get("classification") == "WRONG_SALE_AVAILABLE" or ar.get("available") is True or (ar.get("price") and not ar.get("unavail")):
                        cid = str(ar.get("listing_id") or "")
                        cin = ar.get("check_in")
                        cout = ar.get("check_out")
                        if cid and cin and cout:
                            latest_available.add((cid, cin, cout))
            except Exception as e:
                logger.warning(f"Error reading audit file {audit_file}: {e}")

        if not latest_available:
            return 0

        purged_count = 0
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, listing_id, check_in, check_out FROM competitor_sales")
            rows = cursor.fetchall()
            to_delete = [r["id"] for r in rows if (str(r["listing_id"]), str(r["check_in"]), str(r["check_out"])) in latest_available]

        if to_delete:
            purged_count = self.purge_sales_by_ids(to_delete)
            logger.info(f"Reconciled competitor sales ledger: purged {purged_count} false sales currently active on market.")

        return purged_count

    async def verify_listing_availability(
        self,
        listing_id: str,
        check_in: str,
        check_out: str,
        accommodates: int = 16,
        pdp_timeout: float = 5.0,
    ) -> Dict[str, Any]:
        """
        Directly verify live availability of a comp for an interval on Airbnb
        via Playwright and NordVPN proxy by checking StaysPdpSections.
        """
        import asyncio
        from playwright.async_api import async_playwright
        from src.stealth_connection import StealthConnectionManager
        from src.comp_manager import CompManager

        result = {
            "listing_id": str(listing_id),
            "check_in": check_in,
            "check_out": check_out,
            "available": False,
            "unavail": False,
            "price": None,
            "reason": None,
        }

        pm = None
        try:
            pm = StealthConnectionManager(required=True)
            proxy_cfg = await pm.start()

            launch_kwargs = {
                "headless": True,
                "args": ["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            }
            if proxy_cfg:
                launch_kwargs["proxy"] = proxy_cfg

            async with async_playwright() as p:
                browser = await p.chromium.launch(**launch_kwargs)
                context = await browser.new_context(
                    viewport={"width": 1366, "height": 850},
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
                    ),
                )
                page = await context.new_page()

                pdp_event = asyncio.Event()

                async def on_resp(resp):
                    if "StaysPdpSections" in resp.url:
                        try:
                            body = await resp.text()
                            data = json.loads(body)
                            price, label, unavail, reason = CompManager.parse_stays_pdp_sections(data)
                            if price and not result["price"]:
                                result["price"] = price
                            if unavail:
                                result["unavail"] = True
                                result["available"] = False
                            if reason and not result["reason"]:
                                result["reason"] = reason
                            if price or unavail:
                                pdp_event.set()
                        except Exception:
                            pass

                page.on("response", on_resp)
                url = f"https://www.airbnb.com/rooms/{listing_id}?check_in={check_in}&check_out={check_out}&adults={accommodates}&locale=en&currency=USD"
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=25000)
                    try:
                        await page.evaluate("() => window.scrollTo(0, 1500)")
                    except Exception:
                        pass
                    try:
                        await asyncio.wait_for(pdp_event.wait(), timeout=pdp_timeout)
                    except asyncio.TimeoutError:
                        pass

                    # If StaysPdpSections did not resolve price or unavail, fallback to DOM inspection
                    if not result["price"] and not result.get("unavail"):
                        try:
                            body_text = await page.evaluate("() => document.body.innerText")
                        except Exception:
                            body_text = ""
                        lower_body = (body_text or "").lower()
                        if any(bot_phrase in lower_body for bot_phrase in [
                            "verify you are human", "press and hold", "access denied",
                            "please verify", "security check", "robot or human",
                        ]):
                            result["reason"] = "Bot challenge detected on listing page"
                        elif any(phrase in lower_body for phrase in [
                            "dates are not available", "dates aren't available",
                            "selected dates are unavailable", "these dates are unavailable",
                            "dates not available", "unavailable for these dates",
                            "minimum stay", "dates are unavailable",
                        ]):
                            result["unavail"] = True
                            result["available"] = False
                            result["reason"] = "Dates unavailable on listing page"
                        else:
                            current_url = getattr(page, "url", "")
                            if (
                                isinstance(current_url, str)
                                and current_url.startswith("http")
                                and f"/rooms/{listing_id}" in current_url
                                and f"check_in={check_in}" not in current_url
                            ):
                                result["unavail"] = True
                                result["available"] = False
                                result["reason"] = "Client-side SPA navigation reset: requested check_in stripped from URL"
                            else:
                                m = re.search(r"\$([0-9,]+(?:\.[0-9]{2})?)\s*for\s+\d+\s+nights", body_text, re.IGNORECASE)
                                if m:
                                    result["price"] = float(m.group(1).replace(",", ""))
                                else:
                                    m2 = re.search(r"\$([0-9,]+(?:\.[0-9]{2})?)\s*(?:before taxes|total)", body_text, re.IGNORECASE)
                                    if m2:
                                        result["price"] = float(m2.group(1).replace(",", ""))
                except Exception as e:
                    result["reason"] = str(e)
                finally:
                    page.remove_listener("response", on_resp)
                    await page.close()
                    await browser.close()
        except Exception as e:
            if not result.get("reason"):
                result["reason"] = f"Browser session failure: {e}"
        finally:
            if pm:
                await pm.stop()

        if result["price"] and not result.get("unavail") and not result.get("reason"):
            result["available"] = True

        return result

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

    def compute_monthly_lead_time_windows(self, verification_filter: Optional[str] = "CONFIRMED_BLOCKED") -> Dict[str, Any]:
        """
        Compute empirical advance booking windows (median, P25-P75, min, max)
        for competitor sales, broken down by month of stay (1 to 12) and overall.
        Filters by verification_filter (default: 'CONFIRMED_BLOCKED') to prevent
        unverified candidate disappearances from distorting the booking pace.
        """
        sales = self.get_all_sales(verification_filter=verification_filter)
        overall_lead_times: List[int] = []
        monthly_lead_times: Dict[int, List[int]] = {m: [] for m in range(1, 13)}

        for s in sales:
            cin = s.get("check_in")
            lead = s.get("lead_time_days")
            if cin and lead is not None:
                try:
                    m = datetime.strptime(cin, "%Y-%m-%d").month
                    overall_lead_times.append(int(lead))
                    monthly_lead_times[m].append(int(lead))
                except Exception:
                    pass

        def calc_quartiles(data: List[int]) -> Dict[str, Any]:
            if not data:
                return {"count": 0, "min": 0, "p25": 0, "median": 0, "p75": 0, "max": 0, "window_str": "—"}
            sorted_data = sorted(data)
            n = len(sorted_data)
            p25_idx = int(math.floor(0.25 * (n - 1)))
            p50_idx = int(math.floor(0.50 * (n - 1)))
            p75_idx = int(math.floor(0.75 * (n - 1)))

            p25 = sorted_data[p25_idx]
            median = sorted_data[p50_idx]
            p75 = sorted_data[p75_idx]

            return {
                "count": n,
                "min": sorted_data[0],
                "p25": p25,
                "median": median,
                "p75": p75,
                "max": sorted_data[-1],
                "window_str": f"{p25}–{p75} days out",
            }

        MONTH_METADATA = {
            1: {"name": "January", "season": "Peak Winter / Spring (Feb–Apr)", "guidance": "Winter escape & early conference corridor; competitors secure bookings 125–145 days in advance."},
            2: {"name": "February", "season": "Peak Winter / Spring (Feb–Apr)", "guidance": "Peak luxury compression (WM Phoenix Open & Super Weekend); competitors convert ~150–175 days ahead."},
            3: {"name": "March", "season": "Peak Winter / Spring (Feb–Apr)", "guidance": "Peak Cactus League Spring Training & Holy Week; robust 180–195 day advance competitor booking pace."},
            4: {"name": "April", "season": "Peak Winter / Spring (Feb–Apr)", "guidance": "Spring warm-up & golf group season; active competitor absorption at 215–225 days out."},
            5: {"name": "May", "season": "Fall / Shoulder Season (Sep–Jan, May)", "guidance": "ASU graduation & Memorial Day transition; competitor bookings cluster ~245–260 days out."},
            6: {"name": "June", "season": "Summer Value Season (Jun–Aug)", "guidance": "Summer low-season transition; early competitor summer bookings lock in 275–285 days ahead."},
            7: {"name": "July", "season": "Summer Value Season (Jun–Aug)", "guidance": "Mid-summer heat valley; competitors with long-horizon calendars book 300–315 days out."},
            8: {"name": "August", "season": "Summer Value Season (Jun–Aug)", "guidance": "Back-to-school & student move-in demand; long-range reservations placed ~350+ days in advance."},
            9: {"name": "September", "season": "Fall / Shoulder Season (Sep–Jan, May)", "guidance": "Fall season re-opening & football weekends; mix of long-lead annual dates and fresh sweeps."},
            10: {"name": "October", "season": "Fall / Shoulder Season (Sep–Jan, May)", "guidance": "Fall break & desert wedding peak; fast near-term conversion window observed at 35–50 days."},
            11: {"name": "November", "season": "Fall / Shoulder Season (Sep–Jan, May)", "guidance": "Thanksgiving & family gatherings; active competitor absorption centered at 70–85 days."},
            12: {"name": "December", "season": "Fall / Shoulder Season (Sep–Jan, May)", "guidance": "Holiday bowl games, Christmas & New Year celebrations; early holiday reservations secured 85–105 days ahead."},
        }

        months_summary = {}
        for m in range(1, 13):
            m_stat = calc_quartiles(monthly_lead_times[m])
            m_stat["month_num"] = m
            m_stat["month_name"] = MONTH_METADATA[m]["name"]
            m_stat["season"] = MONTH_METADATA[m]["season"]
            m_stat["guidance"] = MONTH_METADATA[m]["guidance"]
            months_summary[m] = m_stat

        return {
            "overall": calc_quartiles(overall_lead_times),
            "months": months_summary,
            "total_analyzed": len(overall_lead_times),
        }

    @staticmethod
    def _categorize_lead_horizon(days: int) -> str:
        """Categorize lead time days into strategic horizons."""
        if days > 180:
            return ">180d"
        elif days >= 91:
            return "91–180d"
        elif days >= 31:
            return "31–90d"
        else:
            return "≤30d"

    @staticmethod
    def _normalize_segment_type(seg: str) -> str:
        """Map segment type to weekend or midweek."""
        s = str(seg or "").lower()
        if "weekend" in s or "mix" in s:
            return "weekend"
        return "midweek"

    def _load_strategy_config(self) -> Dict[str, Any]:
        """
        Load 2D strategy matrix, shrinkage parameters, and operational floors from settings.yaml,
        falling back cleanly to embedded constants.
        """
        try:
            from src.config import get_settings
            settings = get_settings()
            strat = settings.get("strategy", {})
        except Exception:
            strat = {}

        k = float(strat.get("bayesian_shrinkage_k", BAYESIAN_SHRINKAGE_K))
        min_n = int(strat.get("min_empirical_sample_size", MIN_SAMPLE_SIZE))
        monotonic = bool(strat.get("enforce_monotonic_tapering", True)) and ENFORCE_MONOTONIC_TAPERING
        op_floors = dict(strat.get("operational_floors") or OPERATIONAL_FLOORS)

        raw_matrix = strat.get("lead_time_matrix") or {}
        matrix = {}
        for h, default_data in DEFAULT_HORIZON_PRIORS.items():
            matrix[h] = {}
            cfg_h = raw_matrix.get(h) or {}
            for t, default_t in default_data.items():
                cfg_t = cfg_h.get(t) or {}
                matrix[h][t] = {
                    "target": float(cfg_t.get("target") if cfg_t.get("target") is not None else default_t["target"]),
                    "floor": float(cfg_t.get("floor") if cfg_t.get("floor") is not None else default_t["floor"]),
                    "p75": float(cfg_t.get("p75") if cfg_t.get("p75") is not None else default_t["p75"]),
                }

        return {
            "matrix": matrix,
            "k": k,
            "min_sample_size": min_n,
            "enforce_monotonic_tapering": monotonic,
            "operational_floors": op_floors,
        }

    def compute_strategy_grid(self) -> Dict[str, Any]:
        """
        Aggregate recorded sales into the 2D Strategy Matrix (Horizon x Stay Type)
        and compute empirical percentiles with Bayesian shrinkage, strategic floor clamping,
        and monotonic tapering.
        """
        sales = self.get_all_sales(verification_filter="CONFIRMED_BLOCKED")
        strat_cfg = self._load_strategy_config()
        matrix = strat_cfg["matrix"]
        k = strat_cfg["k"]
        min_sample_size = strat_cfg["min_sample_size"]
        enforce_monotonic = strat_cfg["enforce_monotonic_tapering"]
        
        # Horizons ordered from far-out to last-minute
        horizons = [">180d", "91–180d", "31–90d", "≤30d"]
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

                # Determine baseline prior and floor from horizon-specific matrix
                h_priors = matrix.get(h, {}).get(t, {})
                prior_target = h_priors.get("target", PRIOR_WEEKEND_TARGET_PCT if t == "weekend" else PRIOR_MIDWEEK_TARGET_PCT)
                prior_floor = h_priors.get("floor", 40.0 if t == "weekend" else 28.0)
                prior_p75 = h_priors.get("p75", PRIOR_WEEKEND_P75_PCT if t == "weekend" else PRIOR_MIDWEEK_P75_PCT)

                if n > 0:
                    pcts = sorted(
                        float(cs["last_observed_percentile"]) if cs.get("last_observed_percentile") is not None else 50.0
                        for cs in cell_sales
                    )
                    rates = [
                        float(cs["last_observed_rate"]) if cs.get("last_observed_rate") is not None else 0.0
                        for cs in cell_sales
                    ]
                    
                    # Median (p50)
                    mid = n // 2
                    if n % 2 == 1:
                        p50 = pcts[mid]
                    else:
                        p50 = (pcts[mid - 1] + pcts[mid]) / 2.0

                    # 75th percentile
                    p75_idx = int(math.ceil(0.75 * n)) - 1
                    p75 = pcts[max(0, min(p75_idx, n - 1))]

                    avg_rate = round(sum(rates) / n, 2)
                    min_rate = min(rates)
                    max_rate = max(rates)
                    emp_p50 = round(p50, 1)
                    emp_p75 = round(p75, 1)

                    if n >= min_sample_size:
                        is_empirical = True
                        hat_y = (n * p50 + k * prior_target) / (n + k)
                        hat_y_p75 = (n * p75 + k * prior_p75) / (n + k)
                    else:
                        is_empirical = False
                        hat_y = prior_target
                        hat_y_p75 = prior_p75
                else:
                    emp_p50 = None
                    emp_p75 = None
                    avg_rate = 0.0
                    min_rate = 0.0
                    max_rate = 0.0
                    is_empirical = False
                    hat_y = prior_target
                    hat_y_p75 = prior_p75

                # Strategic Floor Clamping: tag is_floor_clamped if shrinkage result < floor
                is_floor_clamped = bool(round(hat_y, 1) < prior_floor or hat_y < prior_floor)
                recommended_target = round(max(prior_floor, hat_y), 1)
                recommended_aggressive = round(max(prior_floor, hat_y_p75), 1)

                grid[h][t] = {
                    "count": n,
                    "empirical_p50": emp_p50,
                    "empirical_p75": emp_p75,
                    "recommended_target": recommended_target,
                    "recommended_aggressive": recommended_aggressive,
                    "baseline_prior": prior_target,
                    "floor": prior_floor,
                    "is_empirical": is_empirical,
                    "is_floor_clamped": is_floor_clamped,
                    "is_monotonic_adjusted": False,
                    "avg_rate": avg_rate,
                    "min_rate": min_rate,
                    "max_rate": max_rate,
                }

        # Monotonic Tapering Pass across ordered horizons (>180d >= 91–180d >= 31–90d >= ≤30d)
        if enforce_monotonic:
            for t in stay_types:
                prev_target = None
                for h in horizons:
                    curr_rec = grid[h][t]["recommended_target"]
                    if prev_target is not None and curr_rec > prev_target:
                        grid[h][t]["recommended_target"] = prev_target
                        grid[h][t]["is_monotonic_adjusted"] = True
                    prev_target = grid[h][t]["recommended_target"]

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

        res_grid = {
            "total_sales": total_sales_count,
            "overall_median_percentile": round(overall_median_pct, 1),
            "avg_lead_time_days": avg_lead_time,
            "avg_rate": avg_rate_overall,
            "weekend_sales_count": sum(1 for s in sales if self._normalize_segment_type(s["segment_type"]) == "weekend"),
            "midweek_sales_count": sum(1 for s in sales if self._normalize_segment_type(s["segment_type"]) == "midweek"),
            "grid": grid,
            "horizons": horizons,
        }
        self._cached_strategy_grid = res_grid
        return res_grid

    def get_target_percentile(self, lead_time_days: int, segment_type: str = "weekend") -> float:
        """
        Retrieve empirical Bayesian-shrunk target percentile for a lead-time horizon.
        Smoothly falls back to horizon-specific priors (k = 5.0) if sample size n < 5.
        Caches the 2D strategy grid in memory to prevent redundant SQLite queries across segments.
        """
        if self._cached_strategy_grid is None:
            self._cached_strategy_grid = self.compute_strategy_grid()

        horizon = self._categorize_lead_horizon(lead_time_days)
        norm_seg = self._normalize_segment_type(segment_type)
        cell = self._cached_strategy_grid["grid"].get(horizon, {}).get(norm_seg, {})
        if "recommended_target" in cell and cell["recommended_target"] is not None:
            return float(cell["recommended_target"])
        strat_cfg = self._load_strategy_config()
        fallback = strat_cfg["matrix"].get(horizon, {}).get(norm_seg, {}).get("target", 65.0)
        return float(fallback)

    def detect_market_compression(
        self,
        check_in: str,
        check_out: Optional[str] = None,
        total_cohort_count: Optional[int] = None,
        current_available_count: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Evaluate pure market scarcity for an interval.
        Unified Definition: High Compression is triggered when active available comps
        drop below 20% of the active luxury cohort (<20% available, i.e. >80% market absorption).
          - All Comps (N=97): <= 19 comps available (19 / 97 = 19.58% < 20%)
          - Tier A (N=49):    <= 9 comps available (9 / 49 = 18.37% < 20%)
          - Tier B (N=48):    <= 9 comps available (9 / 48 = 18.75% < 20%)
        Returns:
          {
            'is_compressed': bool,
            'available_count': int,
            'total_cohort_count': int,
            'available_ratio': float,
            'surge_multiplier': 1.30,
            'reason': str
          }
        """
        reg_comps = self.load_registered_comps()
        reg_total = total_cohort_count if total_cohort_count is not None else (len(reg_comps) if reg_comps else 97)

        avail_count = current_available_count
        if avail_count is None:
            # Fallback: resolve available comps from latest snapshot if not directly passed
            all_snaps = sorted(self.data_dir.glob("pricing_data_*.json"))
            if all_snaps:
                try:
                    _, s_ints = self.extract_intervals_from_snapshot(all_snaps[-1])
                    for (cin, cout), v in s_ints.items():
                        if cin == check_in and (check_out is None or cout == check_out):
                            raw_comps = v.get("comps", {})
                            avail_count = sum(1 for cid in raw_comps.keys() if cid in reg_comps) if reg_comps else len(raw_comps)
                            break
                except Exception:
                    pass

        if avail_count is None:
            avail_count = reg_total

        avail_ratio = round(avail_count / reg_total, 3) if reg_total > 0 else 1.0
        is_compressed = bool(avail_count < 20 and avail_ratio < 0.20)

        if is_compressed:
            reason = f"High scarcity compression: only {avail_count}/{reg_total} ({avail_ratio * 100:.1f}%) comps available for check-in {check_in}"
        else:
            reason = f"Normal availability: {avail_count}/{reg_total} ({avail_ratio * 100:.1f}%) comps available"

        return {
            "is_compressed": is_compressed,
            "available_count": avail_count,
            "total_cohort_count": reg_total,
            "available_ratio": avail_ratio,
            "surge_multiplier": 1.30 if is_compressed else 1.0,
            "reason": reason,
        }

    def get_recent_sales(self, lookback_days: int = 7) -> List[Dict[str, Any]]:
        """Query verified sales confirmed in the last lookback_days for markdown audit reports."""
        cutoff_date = (date.today() - timedelta(days=lookback_days)).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM competitor_sales 
                WHERE verification_status = 'CONFIRMED_BLOCKED' AND detected_date >= ?
                ORDER BY detected_date DESC, check_in ASC
            """, (cutoff_date,))
            rows = cursor.fetchall()
            return [dict(r) for r in rows]

    def get_active_compression_alerts(self, lookback_hours: int = 48) -> List[Dict[str, Any]]:
        """Retrieve all upcoming intervals currently meeting pure scarcity compression (<20% available)."""
        today_iso = date.today().isoformat()
        reg_comps = self.load_registered_comps()
        reg_total = len(reg_comps) if reg_comps else 97

        cohort_counts = {}
        all_snaps = sorted(self.data_dir.glob("pricing_data_*.json"))
        if all_snaps:
            try:
                _, s_ints = self.extract_intervals_from_snapshot(all_snaps[-1])
                for k, v in s_ints.items():
                    raw_comps = v.get("comps", {})
                    cohort_counts[k] = sum(1 for cid in raw_comps.keys() if cid in reg_comps) if reg_comps else len(raw_comps)
            except Exception:
                pass

        alerts = []
        for (cin, cout), avail_count in sorted(cohort_counts.items(), key=lambda x: x[0][0]):
            if cin < today_iso:
                continue
            comp_info = self.detect_market_compression(
                check_in=cin,
                check_out=cout,
                total_cohort_count=reg_total,
                current_available_count=avail_count,
            )
            if comp_info["is_compressed"]:
                alerts.append({
                    "check_in": cin,
                    "check_out": cout,
                    **comp_info
                })
        return alerts

    def get_total_sales_count(self) -> int:
        """Return total active confirmed sales in the competitor_sales ledger."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM competitor_sales WHERE verification_status = 'CONFIRMED_BLOCKED'")
            row = cursor.fetchone()
            return int(row[0]) if row and row[0] is not None else 0

    async def reconcile_and_verify_interval(
        self,
        check_in: str,
        check_out: str,
        curr_comps: List[Dict[str, Any]],
        lead_time_days: Optional[int] = None,
        segment_type: Optional[str] = None,
        max_concurrent_checks: int = 5,
        pdp_timeout: float = 5.0,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Perform inline active reconciliation and calendar verification for a single interval:
        1. Active Reconciliation (Erased Sales):
           If any comp currently observed as available is present in competitor_sales for (check_in, check_out),
           it is purged from the database and reported in erased_sales.
        2. Candidate Dropout Verification (Recorded Sales):
           Compares curr_comps against the predecessor snapshot. If registered comps disappeared,
           runs defensive filters (scrape degradation, dropped corridor, local cache).
           Checks candidate availability via Playwright PDP sweep.
           - If unavail=True: confirmed blocked -> records sale in SQLite -> returns in recorded_sales.
           - If price and not unavail: confirmed available -> auto-heals SQLite (erases false sale if present)
             and writes single-comp cache.
        Returns: (recorded_sales, erased_sales)
        """
        cin_str = str(check_in)
        cout_str = str(check_out)
        try:
            cin_d = datetime.strptime(cin_str, "%Y-%m-%d").date()
            cout_d = datetime.strptime(cout_str, "%Y-%m-%d").date()
        except Exception:
            return [], []

        nights = max(1, (cout_d - cin_d).days)
        seg_type = segment_type or ("weekend" if cin_d.weekday() in (3, 4, 5) else "midweek")
        curr_d = date.today()
        curr_d_str = curr_d.isoformat()
        lead_days = lead_time_days if lead_time_days is not None else max(0, (cin_d - curr_d).days)

        registered_comps = self.load_registered_comps()

        # Step 1: Active Reconciliation (Erased Sales for currently available comps)
        erased_sales: List[Dict[str, Any]] = []
        curr_available_ids = set()
        for c in curr_comps:
            cid = str(c.get("listing_id") or "")
            rate = c.get("effective_nightly")
            if cid and rate is not None and float(rate) > 0:
                curr_available_ids.add(cid)

        if curr_available_ids:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                placeholders = ",".join("?" for _ in curr_available_ids)
                cursor.execute(f"""
                    SELECT id, listing_id, listing_name, last_observed_rate, last_observed_percentile, tier, location
                    FROM competitor_sales
                    WHERE check_in = ? AND check_out = ? AND listing_id IN ({placeholders})
                """, [cin_str, cout_str, *curr_available_ids])
                matching_rows = cursor.fetchall()
                if matching_rows:
                    to_delete_ids = []
                    for r in matching_rows:
                        to_delete_ids.append(r["id"])
                        erased_sales.append(dict(r))
                    placeholders_del = ",".join("?" for _ in to_delete_ids)
                    cursor.execute(f"DELETE FROM competitor_sales WHERE id IN ({placeholders_del})", to_delete_ids)
                    conn.commit()
                    self._cached_strategy_grid = None

        # Step 2: Disappearance Detection & Verification (Recorded Sales)
        if self._cached_prev_intervals is None:
            self.prepare_predecessor_snapshot()

        prev_data = self._cached_prev_intervals.get((cin_str, cout_str)) if self._cached_prev_intervals else None
        if not prev_data or not prev_data.get("is_live_scan", True):
            return [], erased_sales

        prev_comps_dict = prev_data.get("comps", {})
        prev_reg_comps = {
            cid: c for cid, c in prev_comps_dict.items()
            if cid in registered_comps
        } if registered_comps else prev_comps_dict

        curr_reg_comps = {
            str(c.get("listing_id")): c for c in curr_comps
            if str(c.get("listing_id")) in registered_comps
        } if registered_comps else {str(c.get("listing_id")): c for c in curr_comps}

        # Stage 3: Scrape Degradation Guard
        prev_comp_count = len(prev_reg_comps)
        curr_comp_count = len(curr_reg_comps)
        if prev_comp_count >= 8 and (curr_comp_count <= 3 or (curr_comp_count / prev_comp_count) < 0.20):
            logger.warning(
                f"Scrape degradation detected for interval ({cin_str}, {cout_str}): "
                f"registered comps dropped from {prev_comp_count} to {curr_comp_count}. Skipping verification."
            )
            return [], erased_sales

        # Stage 4: Dropped Corridors Guard
        prev_loc_counts: Dict[str, int] = {}
        for cid, c in prev_reg_comps.items():
            loc = c.get("location") or registered_comps.get(cid, {}).get("location") or "Unknown"
            prev_loc_counts[loc] = prev_loc_counts.get(loc, 0) + 1

        curr_loc_counts: Dict[str, int] = {}
        for cid, c in curr_reg_comps.items():
            loc = c.get("location") or registered_comps.get(cid, {}).get("location") or "Unknown"
            curr_loc_counts[loc] = curr_loc_counts.get(loc, 0) + 1

        dropped_corridors = {
            loc for loc, count in prev_loc_counts.items()
            if count >= 2 and curr_loc_counts.get(loc, 0) == 0
        }

        # Stage 5: Local Search Cache Check
        cached_available_ids = set()
        cache_dir = self.data_dir / "cache"
        if cache_dir.exists():
            for cf in cache_dir.glob(f"search_{cin_str}_{cout_str}_*.json"):
                try:
                    citems = json.loads(cf.read_text(encoding="utf-8"))
                    if isinstance(citems, list):
                        for item in citems:
                            cid_item = str(item.get("listing_id") or "")
                            eff_rate = item.get("effective_nightly") or item.get("total_price")
                            if cid_item and eff_rate and float(eff_rate) > 0:
                                cached_available_ids.add(cid_item)
                except Exception:
                    pass

        all_prev_comps = list(prev_reg_comps.values())
        curr_comp_ids = set(curr_reg_comps.keys()) | curr_available_ids

        candidates: List[Tuple[str, Dict[str, Any]]] = []
        for cid, cinfo in prev_reg_comps.items():
            if cid in curr_comp_ids or cid in cached_available_ids:
                continue
            c_loc = cinfo.get("location") or registered_comps.get(cid, {}).get("location") or ""
            if any(dc.lower() in c_loc.lower() for dc in dropped_corridors):
                continue
            if registered_comps and cid not in registered_comps:
                continue
            candidates.append((cid, cinfo))

        if not candidates:
            return [], erased_sales

        sem = asyncio.Semaphore(max_concurrent_checks)
        recorded_sales: List[Dict[str, Any]] = []

        async def _check_candidate(cid: str, cinfo: Dict[str, Any]):
            reg_info = registered_comps.get(cid, {})
            accommodates = 16 if reg_info.get("tier") == "tier_a" else 12
            async with sem:
                try:
                    res = await self.verify_listing_availability(
                        listing_id=cid,
                        check_in=cin_str,
                        check_out=cout_str,
                        accommodates=accommodates,
                        pdp_timeout=pdp_timeout,
                    )
                except Exception as e:
                    res = {"available": False, "unavail": False, "price": None, "reason": str(e)}

            if res.get("unavail") is True:
                # CONFIRMED_BLOCKED
                raw_eff = cinfo.get("effective_nightly")
                if raw_eff is None:
                    total_p = cinfo.get("total_price") or 0.0
                    raw_eff = round(total_p / max(1, nights), 2)
                else:
                    raw_eff = float(raw_eff)

                desirability_ratio = float(reg_info.get("desirability_ratio") or 1.0)
                composite_score = float(reg_info.get("composite_score") or 85.0)
                tier = reg_info.get("tier") or ("tier_a" if (cinfo.get("bedrooms") or 0) >= 6 else "tier_b")
                location = reg_info.get("location") or cinfo.get("location") or "Scottsdale"
                listing_name = reg_info.get("name") or cinfo.get("name") or cinfo.get("title") or f"Listing {cid}"
                adj_rate = round(raw_eff / desirability_ratio, 2) if desirability_ratio > 0 else raw_eff

                if all_prev_comps:
                    rates_lower_or_equal = sum(
                        1 for oc in all_prev_comps
                        if (oc.get("effective_nightly") or 0.0) <= raw_eff
                    )
                    percentile = round((rates_lower_or_equal / len(all_prev_comps)) * 100.0, 1)
                else:
                    percentile = 50.0

                sale_record = {
                    "listing_id": cid,
                    "listing_name": listing_name,
                    "tier": tier,
                    "location": location,
                    "check_in": cin_str,
                    "check_out": cout_str,
                    "nights": nights,
                    "segment_type": seg_type,
                    "detected_date": curr_d_str,
                    "lead_time_days": lead_days,
                    "last_observed_rate": raw_eff,
                    "last_observed_adj_rate": adj_rate,
                    "last_observed_percentile": percentile,
                    "composite_score": composite_score,
                    "desirability_ratio": desirability_ratio,
                    "verification_status": "CONFIRMED_BLOCKED",
                    "raw_snippet": cinfo.get("raw_snippet") or cinfo.get("price_snippet") or "Verified via Inline Playwright Bridge Sweep",
                    "created_at": datetime.now().isoformat(),
                }

                with self._get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT verification_status, detected_date 
                        FROM competitor_sales 
                        WHERE listing_id = ? AND check_in = ? AND check_out = ?
                    """, (cid, cin_str, cout_str))
                    existing_row = cursor.fetchone()

                    cursor.execute("""
                        INSERT INTO competitor_sales (
                            listing_id, listing_name, tier, location,
                            check_in, check_out, nights, segment_type,
                            detected_date, lead_time_days,
                            last_observed_rate, last_observed_adj_rate, last_observed_percentile,
                            composite_score, desirability_ratio, verification_status, raw_snippet,
                            created_at
                        ) VALUES (
                            :listing_id, :listing_name, :tier, :location,
                            :check_in, :check_out, :nights, :segment_type,
                            :detected_date, :lead_time_days,
                            :last_observed_rate, :last_observed_adj_rate, :last_observed_percentile,
                            :composite_score, :desirability_ratio, :verification_status, :raw_snippet,
                            COALESCE(:created_at, CURRENT_TIMESTAMP)
                        )
                        ON CONFLICT(listing_id, check_in, check_out) DO UPDATE SET
                            detected_date = CASE 
                                WHEN excluded.detected_date < competitor_sales.detected_date 
                                THEN excluded.detected_date 
                                ELSE competitor_sales.detected_date 
                            END,
                            lead_time_days = CASE
                                WHEN excluded.detected_date < competitor_sales.detected_date
                                THEN excluded.lead_time_days
                                ELSE competitor_sales.lead_time_days
                            END,
                            verification_status = CASE
                                WHEN excluded.verification_status = 'CONFIRMED_BLOCKED'
                                THEN 'CONFIRMED_BLOCKED'
                                ELSE competitor_sales.verification_status
                            END
                    """, sale_record)
                    conn.commit()

                    if existing_row:
                        if existing_row["verification_status"] == "CONFIRMED_BLOCKED":
                            sale_record["verification_status"] = "CONFIRMED_BLOCKED"
                        earliest_date = min(existing_row["detected_date"], curr_d_str)
                        sale_record["detected_date"] = earliest_date
                        sale_record["lead_time_days"] = max(0, (datetime.strptime(cin_str, "%Y-%m-%d").date() - datetime.strptime(str(earliest_date)[:10], "%Y-%m-%d").date()).days)

                recorded_sales.append(sale_record)

            elif res.get("price") and not res.get("unavail"):
                # Confirmed available: Auto-heal if previously recorded as sale
                with self._get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT id, listing_id, listing_name, last_observed_rate, last_observed_percentile, tier, location
                        FROM competitor_sales 
                        WHERE listing_id = ? AND check_in = ? AND check_out = ?
                    """, (cid, cin_str, cout_str))
                    row = cursor.fetchone()
                    if row:
                        cursor.execute("""
                            DELETE FROM competitor_sales 
                            WHERE id = ?
                        """, (row["id"],))
                        conn.commit()
                        erased_sales.append(dict(row))

                cache_dir = self.data_dir / "cache"
                cache_dir.mkdir(parents=True, exist_ok=True)
                try:
                    total_p = float(res["price"])
                    eff_nightly = round(total_p / max(1, nights), 2)
                    c_file = cache_dir / f"search_{cin_str}_{cout_str}_comp_{cid}.json"
                    c_file.write_text(json.dumps([{
                        "listing_id": cid,
                        "check_in": cin_str,
                        "check_out": cout_str,
                        "effective_nightly": eff_nightly,
                        "total_price": total_p,
                    }]), encoding="utf-8")
                except Exception:
                    pass

        await asyncio.gather(*[_check_candidate(cid, cinfo) for cid, cinfo in candidates])

        if recorded_sales or erased_sales:
            self._cached_strategy_grid = None

        return recorded_sales, erased_sales

    async def diff_and_verify_staged_comps(
        self,
        staged_intervals: Union[Dict[Tuple[str, str], List[Dict[str, Any]]], Dict[Tuple[str, str], Dict[str, Any]]],
        prev_snapshot_path: Optional[Path] = None,
        max_concurrent_checks: int = 5,
        pdp_timeout: float = 5.0,
    ) -> List[Dict[str, Any]]:
        """
        Detect candidate dropouts between predecessor snapshots and newly scraped staged comps.
        If prev_snapshot_path is omitted, automatically executes Stage 1 backwards search across data/.
        Verifies candidates via throttled Playwright + NordVPN PDP sweeps and persists confirmed sales.
        """
        import asyncio

        normalized_staged: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for k, v in staged_intervals.items():
            if isinstance(k, tuple):
                cin, cout = k
            elif isinstance(v, dict) and "check_in" in v and "check_out" in v:
                cin = v["check_in"]
                cout = v["check_out"]
            else:
                continue

            if isinstance(v, list):
                comps_list = v
                cin_d = datetime.strptime(cin, "%Y-%m-%d").date()
                seg_type = "weekend" if cin_d.weekday() in (3, 4, 5) else "midweek"
                cout_d = datetime.strptime(cout, "%Y-%m-%d").date()
                nights = max(1, (cout_d - cin_d).days)
                is_live = True
            elif isinstance(v, dict):
                comps_list = v.get("comps_list") or (list(v["comps"].values()) if "comps" in v else [])
                nights = v.get("nights", 3)
                seg_type = v.get("segment_type", "midweek")
                is_live = v.get("is_live_scan", True)
            else:
                comps_list = []
                nights = 3
                seg_type = "midweek"
                is_live = True

            comps_by_id = {}
            for c in comps_list:
                cid = str(c.get("listing_id") or "")
                if cid:
                    comps_by_id[cid] = c

            normalized_staged[(cin, cout)] = {
                "check_in": cin,
                "check_out": cout,
                "nights": nights,
                "segment_type": seg_type,
                "is_live_scan": is_live,
                "comps": comps_by_id,
            }

        if not normalized_staged:
            return []

        all_snapshots = sorted(self.data_dir.glob("pricing_data_*.json"))
        intervals_by_prev_path: Dict[Path, Set[Tuple[str, str]]] = {}
        prior_intervals_cache: Dict[Path, Dict[Tuple[str, str], Dict[str, Any]]] = {}

        if prev_snapshot_path is not None:
            intervals_by_prev_path[Path(prev_snapshot_path)] = set(normalized_staged.keys())
        else:
            if not all_snapshots:
                return []
            today_str = date.today().isoformat()
            start_idx = len(all_snapshots) - 1
            if start_idx >= 1 and all_snapshots[start_idx].stem == f"pricing_data_{today_str}":
                start_idx -= 1
            for key in normalized_staged.keys():
                for i in range(start_idx, -1, -1):
                    prev_p = all_snapshots[i]
                    if prev_p not in prior_intervals_cache:
                        _, p_ints = self.extract_intervals_from_snapshot(prev_p)
                        prior_intervals_cache[prev_p] = p_ints
                    if key in prior_intervals_cache[prev_p]:
                        intervals_by_prev_path.setdefault(prev_p, set()).add(key)
                        break

        registered_comps = self.load_registered_comps()
        curr_date = date.today().isoformat()
        curr_d = date.today()

        candidates_to_verify: List[Dict[str, Any]] = []

        for prev_p, target_keys in intervals_by_prev_path.items():
            if prev_p not in prior_intervals_cache:
                _, p_ints = self.extract_intervals_from_snapshot(prev_p)
                prior_intervals_cache[prev_p] = p_ints
            prev_intervals = prior_intervals_cache[prev_p]

            for key in target_keys:
                if key not in prev_intervals or key not in normalized_staged:
                    continue
                prev_data = prev_intervals[key]
                curr_data = normalized_staged[key]

                # Stage 2: Live Platform Scan Guard
                if not prev_data.get("is_live_scan") or not curr_data.get("is_live_scan"):
                    continue

                prev_reg_comps = {
                    cid: c for cid, c in prev_data["comps"].items()
                    if cid in registered_comps
                } if registered_comps else prev_data["comps"]
                curr_reg_comps = {
                    cid: c for cid, c in curr_data["comps"].items()
                    if cid in registered_comps
                } if registered_comps else curr_data["comps"]

                # Stage 3: Scrape Degradation Guard
                prev_comp_count = len(prev_reg_comps)
                curr_comp_count = len(curr_reg_comps)
                if prev_comp_count >= 8 and (curr_comp_count <= 3 or (curr_comp_count / prev_comp_count) < 0.20):
                    logger.warning(
                        f"Scrape degradation detected for interval {key}: comps dropped from {prev_comp_count} to {curr_comp_count}. "
                        f"Skipping verification."
                    )
                    continue

                # Stage 4: Dropped Corridors Guard
                prev_loc_counts: Dict[str, int] = {}
                for cid, c in prev_reg_comps.items():
                    loc = c.get("location") or registered_comps.get(cid, {}).get("location") or "Unknown"
                    prev_loc_counts[loc] = prev_loc_counts.get(loc, 0) + 1

                curr_loc_counts: Dict[str, int] = {}
                for cid, c in curr_reg_comps.items():
                    loc = c.get("location") or registered_comps.get(cid, {}).get("location") or "Unknown"
                    curr_loc_counts[loc] = curr_loc_counts.get(loc, 0) + 1

                dropped_corridors = {
                    loc for loc, count in prev_loc_counts.items()
                    if count >= 2 and curr_loc_counts.get(loc, 0) == 0
                }

                cin_str = prev_data["check_in"]
                cout_str = prev_data["check_out"]
                nights = prev_data["nights"]
                seg_type = prev_data["segment_type"]
                cin_d = datetime.strptime(cin_str, "%Y-%m-%d").date()
                lead_days = max(0, (cin_d - curr_d).days)

                # Stage 5: Local Search Cache Check
                cached_available_ids = set()
                cache_dir = self.data_dir / "cache"
                if cache_dir.exists():
                    for cf in cache_dir.glob(f"search_{cin_str}_{cout_str}_*.json"):
                        try:
                            citems = json.loads(cf.read_text(encoding="utf-8"))
                            if isinstance(citems, list):
                                for item in citems:
                                    cid_item = str(item.get("listing_id") or "")
                                    eff_rate = item.get("effective_nightly") or item.get("total_price")
                                    if cid_item and eff_rate and float(eff_rate) > 0:
                                        cached_available_ids.add(cid_item)
                        except Exception:
                            pass

                all_prev_comps = list(prev_reg_comps.values())
                curr_comp_ids = set(curr_data["comps"].keys())

                for cid, cinfo in prev_reg_comps.items():
                    if cid in curr_comp_ids:
                        continue
                    if cid in cached_available_ids:
                        continue
                    c_loc = cinfo.get("location") or registered_comps.get(cid, {}).get("location") or ""
                    if any(dc.lower() in c_loc.lower() for dc in dropped_corridors):
                        continue
                    # Stage 6: Registry filter
                    if cid not in registered_comps:
                        continue

                    candidates_to_verify.append({
                        "listing_id": cid,
                        "check_in": cin_str,
                        "check_out": cout_str,
                        "nights": nights,
                        "segment_type": seg_type,
                        "lead_time_days": lead_days,
                        "cinfo": cinfo,
                        "all_prev_comps": all_prev_comps,
                        "curr_date": curr_date,
                    })

        if not candidates_to_verify:
            return []

        sem = asyncio.Semaphore(max_concurrent_checks)
        confirmed_sales: List[Dict[str, Any]] = []
        purged_sales_count = 0

        async def _verify_candidate(cand: Dict[str, Any]):
            cid = cand["listing_id"]
            cin_str = cand["check_in"]
            cout_str = cand["check_out"]
            nights = cand["nights"]
            seg_type = cand["segment_type"]
            cinfo = cand["cinfo"]
            all_prev_comps = cand["all_prev_comps"]
            curr_d_str = cand["curr_date"]
            lead_days = cand["lead_time_days"]
            reg_info = registered_comps.get(cid, {})
            accommodates = 16 if reg_info.get("tier") == "tier_a" else 12

            async with sem:
                try:
                    res = await self.verify_listing_availability(
                        listing_id=cid,
                        check_in=cin_str,
                        check_out=cout_str,
                        accommodates=accommodates,
                        pdp_timeout=pdp_timeout,
                    )
                except Exception as e:
                    res = {"available": False, "unavail": False, "price": None, "reason": str(e)}

            if res.get("unavail") is True:
                # CONFIRMED_BLOCKED
                raw_eff = cinfo.get("effective_nightly")
                if raw_eff is None:
                    total_p = cinfo.get("total_price") or 0.0
                    raw_eff = round(total_p / max(1, nights), 2)
                else:
                    raw_eff = float(raw_eff)

                desirability_ratio = float(reg_info.get("desirability_ratio") or 1.0)
                composite_score = float(reg_info.get("composite_score") or 85.0)
                tier = reg_info.get("tier") or ("tier_a" if (cinfo.get("bedrooms") or 0) >= 6 else "tier_b")
                location = reg_info.get("location") or cinfo.get("location") or "Scottsdale"
                listing_name = reg_info.get("name") or cinfo.get("name") or cinfo.get("title") or f"Listing {cid}"
                adj_rate = round(raw_eff / desirability_ratio, 2) if desirability_ratio > 0 else raw_eff

                if all_prev_comps:
                    rates_lower_or_equal = sum(
                        1 for oc in all_prev_comps
                        if (oc.get("effective_nightly") or 0.0) <= raw_eff
                    )
                    percentile = round((rates_lower_or_equal / len(all_prev_comps)) * 100.0, 1)
                else:
                    percentile = 50.0

                sale_record = {
                    "listing_id": cid,
                    "listing_name": listing_name,
                    "tier": tier,
                    "location": location,
                    "check_in": cin_str,
                    "check_out": cout_str,
                    "nights": nights,
                    "segment_type": seg_type,
                    "detected_date": curr_d_str,
                    "lead_time_days": lead_days,
                    "last_observed_rate": raw_eff,
                    "last_observed_adj_rate": adj_rate,
                    "last_observed_percentile": percentile,
                    "composite_score": composite_score,
                    "desirability_ratio": desirability_ratio,
                    "verification_status": "CONFIRMED_BLOCKED",
                    "raw_snippet": cinfo.get("raw_snippet") or cinfo.get("price_snippet") or "Verified via Playwright Bridge Sweep",
                    "created_at": datetime.now().isoformat(),
                }

                with self._get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT verification_status, detected_date 
                        FROM competitor_sales 
                        WHERE listing_id = ? AND check_in = ? AND check_out = ?
                    """, (cid, cin_str, cout_str))
                    existing_row = cursor.fetchone()

                    cursor.execute("""
                        INSERT INTO competitor_sales (
                            listing_id, listing_name, tier, location,
                            check_in, check_out, nights, segment_type,
                            detected_date, lead_time_days,
                            last_observed_rate, last_observed_adj_rate, last_observed_percentile,
                            composite_score, desirability_ratio, verification_status, raw_snippet,
                            created_at
                        ) VALUES (
                            :listing_id, :listing_name, :tier, :location,
                            :check_in, :check_out, :nights, :segment_type,
                            :detected_date, :lead_time_days,
                            :last_observed_rate, :last_observed_adj_rate, :last_observed_percentile,
                            :composite_score, :desirability_ratio, :verification_status, :raw_snippet,
                            COALESCE(:created_at, CURRENT_TIMESTAMP)
                        )
                        ON CONFLICT(listing_id, check_in, check_out) DO UPDATE SET
                            detected_date = CASE 
                                WHEN excluded.detected_date < competitor_sales.detected_date 
                                THEN excluded.detected_date 
                                ELSE competitor_sales.detected_date 
                            END,
                            lead_time_days = CASE
                                WHEN excluded.detected_date < competitor_sales.detected_date
                                THEN excluded.lead_time_days
                                ELSE competitor_sales.lead_time_days
                            END,
                            verification_status = CASE
                                WHEN excluded.verification_status = 'CONFIRMED_BLOCKED'
                                THEN 'CONFIRMED_BLOCKED'
                                ELSE competitor_sales.verification_status
                            END
                    """, sale_record)
                    conn.commit()

                    if existing_row:
                        if existing_row["verification_status"] == "CONFIRMED_BLOCKED":
                            sale_record["verification_status"] = "CONFIRMED_BLOCKED"
                        earliest_date = min(existing_row["detected_date"], curr_d_str)
                        sale_record["detected_date"] = earliest_date
                        sale_record["lead_time_days"] = max(0, (datetime.strptime(cin_str, "%Y-%m-%d").date() - datetime.strptime(str(earliest_date)[:10], "%Y-%m-%d").date()).days)

                confirmed_sales.append(sale_record)

            elif res.get("price") and not res.get("unavail"):
                nonlocal purged_sales_count
                with self._get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        DELETE FROM competitor_sales 
                        WHERE listing_id = ? AND check_in = ? AND check_out = ?
                    """, (cid, cin_str, cout_str))
                    purged_sales_count += cursor.rowcount
                    conn.commit()

                cache_dir = self.data_dir / "cache"
                cache_dir.mkdir(parents=True, exist_ok=True)
                if res.get("price"):
                    try:
                        total_p = float(res["price"])
                        eff_nightly = round(total_p / max(1, nights), 2)
                        c_file = cache_dir / f"search_{cin_str}_{cout_str}_comp_{cid}.json"
                        c_file.write_text(json.dumps([{
                            "listing_id": cid,
                            "check_in": cin_str,
                            "check_out": cout_str,
                            "effective_nightly": eff_nightly,
                            "total_price": total_p,
                        }]), encoding="utf-8")
                    except Exception:
                        pass
            # UNVERIFIED_ERROR: strictly omit from SQLite

        await asyncio.gather(*[_verify_candidate(c) for c in candidates_to_verify])

        if confirmed_sales or purged_sales_count > 0:
            self._cached_strategy_grid = None
            logger.info(f"Verification Bridge: Confirmed {len(confirmed_sales)} sales (purged {purged_sales_count} available comps).")

        return confirmed_sales

    def compute_daily_market_inventory_timeline(
        self,
        snapshot_path: Optional[Union[str, Path]] = None,
        days_ahead: int = 365,
    ) -> Dict[str, Any]:
        """
        Computes a granular daily competitor market inventory trajectory over days_ahead (default 365):
        - Available comps: Observed active/bookable in the latest live scrape
        - Recorded comp sales: Confirmed competitor bookings from competitor_sales ledger
        - Unavailable (unknown): Unobserved, owner-blocked, long-term blocks, or past bookings
        
        Anchors the top line to total active registered comps (97 all, 49 Tier A, 48 Tier B).
        Smoothly fills unscanned/booked dates using the nearest adjacent scanned stay intervals.
        Returns complete JSON-serializable dataset with daily metadata, month tick flags,
        and cohort breakdowns for All Comps, Tier A, and Tier B.
        """
        registered_comps = self.load_registered_comps()
        if not registered_comps:
            return {"anchor_date": date.today().isoformat(), "dates": [], "month_labels": [], "days_meta": [], "cohorts": {}}

        tier_a_ids = {cid for cid, c in registered_comps.items() if c.get("tier") == "tier_a"}
        tier_b_ids = {cid for cid, c in registered_comps.items() if c.get("tier") == "tier_b"}
        all_ids = set(registered_comps.keys())

        # Determine snapshot to use
        target_snapshot = Path(snapshot_path) if snapshot_path else None
        if not target_snapshot or not target_snapshot.exists():
            all_snaps = sorted(self.data_dir.glob("pricing_data_*.json"))
            if all_snaps:
                target_snapshot = all_snaps[-1]

        if not target_snapshot or not target_snapshot.exists():
            anchor_date = date.today()
            report_date = anchor_date.isoformat()
            merged_intervals: Dict[Tuple[str, str], Dict[str, Any]] = {}
        else:
            report_date, curr_intervals = self.extract_intervals_from_snapshot(target_snapshot)
            merged_intervals = dict(curr_intervals)
            # Overlay prior snapshots backwards to cover any intervals missing from current quick scan
            all_snaps = sorted(self.data_dir.glob("pricing_data_*.json"))
            for p in reversed(all_snaps):
                if p == target_snapshot:
                    continue
                try:
                    _, p_ints = self.extract_intervals_from_snapshot(p)
                    for k, v in p_ints.items():
                        if k not in merged_intervals:
                            merged_intervals[k] = v
                except Exception:
                    pass

            try:
                anchor_date = datetime.strptime(report_date, "%Y-%m-%d").date() if report_date else date.today()
            except Exception:
                anchor_date = date.today()

        # Query recorded sales from SQLite
        sales_list: List[Tuple[str, date, date]] = []
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT listing_id, check_in, check_out FROM competitor_sales WHERE verification_status = 'CONFIRMED_BLOCKED'"
                )
                for lid, cin_s, cout_s in cursor.fetchall():
                    try:
                        sales_list.append((
                            str(lid),
                            datetime.strptime(str(cin_s)[:10], "%Y-%m-%d").date(),
                            datetime.strptime(str(cout_s)[:10], "%Y-%m-%d").date(),
                        ))
                    except Exception:
                        pass
        except Exception as e:
            logger.warning(f"Error querying competitor sales for timeline: {e}")

        # Query Kivoya PMS reservations & blocks for Villa del Sol
        villa_date_status: Dict[date, Tuple[str, str]] = {}  # date -> (status_code, status_label)
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT start_date, end_date, type_name FROM reservations WHERE status_name != 'Cancelled'"
                )
                for s_raw, e_raw, t_name in cursor.fetchall():
                    try:
                        s_dt = datetime.strptime(str(s_raw)[:10], "%Y-%m-%d").date()
                        e_dt = datetime.strptime(str(e_raw)[:10], "%Y-%m-%d").date()
                        t_upper = str(t_name or "").strip().upper()
                        if t_upper in ("OWN", "OWNER", "OWNER_HOLD"):
                            s_code = "OWNER_HOLD"
                            s_label = "Owner Block"
                        elif "MAINT" in t_upper:
                            s_code = "MAINTENANCE"
                            s_label = "Maintenance Hold"
                        else:
                            s_code = "BOOKED"
                            s_label = "Booked Guest Stay"

                        cur = s_dt
                        while cur < e_dt:
                            villa_date_status[cur] = (s_code, s_label)
                            cur += timedelta(days=1)
                    except Exception:
                        pass
        except Exception as e:
            logger.warning(f"Error querying reservations for Villa del Sol booked dates: {e}")

        # Determine open calendar cutoff date
        cutoff_date: Optional[date] = None
        cutoff_file = self.data_dir / "cache" / "calendar_cutoff.json"
        if not cutoff_file.exists():
            cutoff_file = Path("data/cache/calendar_cutoff.json")
        if cutoff_file.exists():
            try:
                c_data = json.loads(cutoff_file.read_text(encoding="utf-8"))
                c_end = c_data.get("open_end_date")
                if c_end:
                    cutoff_date = datetime.strptime(c_end, "%Y-%m-%d").date()
            except Exception:
                pass
        if cutoff_date is None:
            cutoff_date = date(anchor_date.year + 1, 5, 31)

        # Map scanned intervals to individual calendar days and pre-parse dates
        sorted_intervals = sorted(merged_intervals.values(), key=lambda x: x.get("check_in", ""))
        day_to_interval: Dict[date, Dict[str, Any]] = {}
        parsed_intervals: List[Tuple[date, date, Dict[str, Any]]] = []
        for item in sorted_intervals:
            try:
                c_in = datetime.strptime(item["check_in"], "%Y-%m-%d").date()
                c_out = datetime.strptime(item["check_out"], "%Y-%m-%d").date()
                parsed_intervals.append((c_in, c_out, item))
                cur = c_in
                while cur < c_out:
                    day_to_interval[cur] = item
                    cur += timedelta(days=1)
            except Exception:
                continue

        def get_nearest_interval(d: date) -> Optional[Dict[str, Any]]:
            if d in day_to_interval:
                return day_to_interval[d]
            if not parsed_intervals:
                return None
            best_int = None
            min_dist = 999999
            for c_in, c_out, item in parsed_intervals:
                if c_in <= d < c_out:
                    return item
                dist = (c_in - d).days if d < c_in else (d - c_out).days
                if dist < min_dist:
                    min_dist = dist
                    best_int = item
            return best_int

        # Build daily series
        days = [anchor_date + timedelta(days=i) for i in range(days_ahead)]
        dates_str = [d.isoformat() for d in days]
        month_labels: List[str] = []
        days_meta: List[Dict[str, Any]] = []

        all_avail: List[int] = []
        all_sales: List[int] = []
        all_unknown: List[int] = []

        a_avail_list: List[int] = []
        a_sales_list: List[int] = []
        a_unknown_list: List[int] = []

        b_avail_list: List[int] = []
        b_sales_list: List[int] = []
        b_unknown_list: List[int] = []

        for i, d in enumerate(days):
            # Month tick: show label on day 1 of each month, or on day 0 if not too close to month end (> 7 days remaining)
            if d.day == 1 or (i == 0 and d.day <= 22):
                month_labels.append(d.strftime("%b"))
            else:
                month_labels.append("")

            is_weekend = d.weekday() in (3, 4, 5)  # Thu, Fri, Sat nights

            # Villa del Sol availability determination
            if cutoff_date and d > cutoff_date:
                is_villa_available = False
                v_code = "CALENDAR_CLOSED"
                v_label = "Calendar Closed"
                v_color = "#475569"  # Slate Gray
            elif d in villa_date_status:
                is_villa_available = False
                v_code, v_label = villa_date_status[d]
                v_color = "#475569"  # Slate Gray
            else:
                is_villa_available = True
                v_code = "AVAILABLE"
                v_label = "Available & Open"
                v_color = "#facc15"  # Warm Gold

            is_villa_booked = not is_villa_available

            inv = get_nearest_interval(d)
            if inv and "comps" in inv:
                avail_on_day = {cid for cid in inv["comps"].keys() if cid in all_ids}
            else:
                avail_on_day = set()

            # Active sales overlapping this date (excluding any comp currently available)
            sales_on_day = {
                lid for lid, cin, cout in sales_list
                if cin <= d < cout and lid in all_ids and lid not in avail_on_day
            }

            # Unknown unavailable
            unknown_on_day = all_ids - avail_on_day - sales_on_day

            # Aggregate All Comps (97)
            all_avail.append(len(avail_on_day))
            all_sales.append(len(sales_on_day))
            all_unknown.append(len(unknown_on_day))

            # Aggregate Tier A (49)
            a_avail = avail_on_day & tier_a_ids
            a_sales = sales_on_day & tier_a_ids
            a_unknown = tier_a_ids - a_avail - a_sales
            a_avail_list.append(len(a_avail))
            a_sales_list.append(len(a_sales))
            a_unknown_list.append(len(a_unknown))

            # Aggregate Tier B (48)
            b_avail = avail_on_day & tier_b_ids
            b_sales = sales_on_day & tier_b_ids
            b_unknown = tier_b_ids - b_avail - b_sales
            b_avail_list.append(len(b_avail))
            b_sales_list.append(len(b_sales))
            b_unknown_list.append(len(b_unknown))

            tot_all = max(1, len(all_ids))
            all_is_compressed = bool(len(avail_on_day) < 0.20 * tot_all)

            days_meta.append({
                "date": d.isoformat(),
                "day_name": d.strftime("%a"),
                "is_weekend": is_weekend,
                "is_villa_booked": is_villa_booked,
                "is_villa_available": is_villa_available,
                "villa_status": v_code,
                "villa_status_label": v_label,
                "villa_color": v_color,
                "is_compressed": all_is_compressed,
                "compression_threshold": round(0.20 * tot_all, 1),
            })

        n_days = max(1, len(days))

        def _calc_cohort_summary(total_c: int, avail_series: List[int], sales_series: List[int], unk_series: List[int]):
            mean_avail = round(sum(avail_series) / n_days, 1)
            mean_sales = round(sum(sales_series) / n_days, 1)
            mean_unk = round(sum(unk_series) / n_days, 1)
            mean_absorbed = round(((mean_sales + mean_unk) / max(1, total_c)) * 100, 1)
            comp_thresh = round(0.20 * total_c, 1)
            comp_days = sum(1 for a in avail_series if a < 0.20 * total_c)
            comp_pct = round((comp_days / n_days) * 100, 1)
            is_comp_series = [bool(a < 0.20 * total_c) for a in avail_series]
            return {
                "total_comps": total_c,
                "available": avail_series,
                "recorded_sales": sales_series,
                "unknown_unavailable": unk_series,
                "avg_available": mean_avail,
                "avg_sales": mean_sales,
                "avg_unknown": mean_unk,
                "current_absorption_pct": mean_absorbed,
                "compression_threshold": comp_thresh,
                "compression_days_count": comp_days,
                "compression_days_pct": comp_pct,
                "is_compressed_series": is_comp_series,
            }

        v_avail_count = sum(1 for m in days_meta if m["is_villa_available"])
        v_unavail_count = n_days - v_avail_count
        v_avail_pct = round((v_avail_count / n_days) * 100, 1)
        v_unavail_pct = round((v_unavail_count / n_days) * 100, 1)

        return {
            "anchor_date": anchor_date.isoformat(),
            "days_count": len(days),
            "dates": dates_str,
            "month_labels": month_labels,
            "days_meta": days_meta,
            "villa_del_sol_summary": {
                "total_days": n_days,
                "available_days": v_avail_count,
                "unavailable_days": v_unavail_count,
                "availability_pct": v_avail_pct,
                "unavailability_pct": v_unavail_pct,
            },
            "cohorts": {
                "all": _calc_cohort_summary(len(all_ids), all_avail, all_sales, all_unknown),
                "tier_a": _calc_cohort_summary(len(tier_a_ids), a_avail_list, a_sales_list, a_unknown_list),
                "tier_b": _calc_cohort_summary(len(tier_b_ids), b_avail_list, b_sales_list, b_unknown_list),
            },
        }


