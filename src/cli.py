"""
Unified Command-Line Interface for STR Competitive Price Advisor.

Usage:
  python -m src.cli run --weekly
  python -m src.cli run --quick
  python -m src.cli bootstrap-comps
  python -m src.cli test-kivoya
"""

import argparse
import asyncio
from datetime import date
import json
import logging
import os
from pathlib import Path
import shutil
import sys
import time
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)


from src.config import load_settings, URGENT_PCT_DIFF, MODERATE_PCT_DIFF
from src.kivoya_client import KivoyaClient
from src.segmentation import CalendarSegmenter
from src.airbnb_collector import AirbnbCollector
from src.analytics import PricingAnalyticsEngine
from src.reporter import PriceReportGenerator
from src.competitor_sales_tracker import CompetitorSalesTracker, format_comp_sales_line


def load_config(config_path: str = "config/settings.yaml") -> Dict[str, Any]:
    return load_settings(config_path)


def snapshot_kivoya_rates(store: Optional[Any] = None, snapshot_date: Optional[str] = None) -> int:
    """Snapshot current Kivoya seasonal rates into SQLite."""
    from src.reservation_store import ReservationStore
    from src.kivoya_client import KivoyaClient
    from datetime import date, timedelta

    if store is None:
        store = ReservationStore()

    if not snapshot_date:
        snapshot_date = date.today().strftime("%Y-%m-%d")

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

    count = store.record_rate_snapshots(snapshot_date, rates_by_date)
    return count


async def run_interval_evaluations(
    collector: AirbnbCollector,
    active_segments: List[Dict[str, Any]],
    analytics: PricingAnalyticsEngine,
    parallel: bool = True,
    force: bool = False,
    max_cache_age: float = 20.0,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Execute comp scraping and pricing evaluations across active calendar intervals concurrently."""
    total = len(active_segments)
    worker_count = len(getattr(collector, "worker_contexts", []))
    if parallel and worker_count >= 4:
        batch_concurrency = max(1, worker_count // 3)
    else:
        batch_concurrency = 1

    sem = asyncio.Semaphore(batch_concurrency)
    completed_count = 0
    count_lock = asyncio.Lock()

    sales_tracker = getattr(analytics, "sales_tracker", None)
    if sales_tracker and hasattr(sales_tracker, "prepare_predecessor_snapshot"):
        try:
            sales_tracker.prepare_predecessor_snapshot()
        except Exception as e:
            logger.debug(f"Failed to pre-load predecessor snapshot: {e}")

    total_recorded_sales: List[Dict[str, Any]] = []
    total_erased_sales: List[Dict[str, Any]] = []

    async def _process_interval(idx_seg):
        nonlocal completed_count
        idx, seg = idx_seg
        c_in = seg["check_in"]
        c_out = seg["check_out"]
        nights = seg["nights"]
        lead = seg["lead_time_days"]
        seg_type = seg.get("segment_type", "midweek")

        async with sem:
            inv_start_time = time.perf_counter()
            inv_start_bytes = getattr(collector, "total_bytes_transferred", 0)

            async def _run_scrape():
                # 1. Run corridor sweeps for Tier A and Tier B with randomized 2-3 pages
                if parallel:
                    scrape_results = await asyncio.gather(
                        collector.fetch_comps_for_dates(
                            check_in=c_in,
                            check_out=c_out,
                            nights=nights,
                            tier="tier_a",
                            use_cache=(not force),
                            max_cache_age_hours=max_cache_age,
                        ),
                        collector.fetch_comps_for_dates(
                            check_in=c_in,
                            check_out=c_out,
                            nights=nights,
                            tier="tier_b",
                            use_cache=(not force),
                            max_cache_age_hours=max_cache_age,
                        ),
                        return_exceptions=True,
                    )
                else:
                    try:
                        res_a = await collector.fetch_comps_for_dates(
                            check_in=c_in,
                            check_out=c_out,
                            nights=nights,
                            tier="tier_a",
                            use_cache=(not force),
                            max_cache_age_hours=max_cache_age,
                        )
                    except Exception as e:
                        res_a = e
                    try:
                        res_b = await collector.fetch_comps_for_dates(
                            check_in=c_in,
                            check_out=c_out,
                            nights=nights,
                            tier="tier_b",
                            use_cache=(not force),
                            max_cache_age_hours=max_cache_age,
                        )
                    except Exception as e:
                        res_b = e
                    scrape_results = [res_a, res_b]
                raw_comps = []
                for res in scrape_results:
                    if isinstance(res, list):
                        raw_comps.extend(res)
                    elif isinstance(res, Exception):
                        print(f"Warning: corridor sweep error for {c_in}: {res}")

                # Strictly filter to curated registered comps
                seen_ids = set()
                comps = []
                active_registry = {}
                if analytics.comp_registry:
                    active_registry = {
                        cid: meta for cid, meta in analytics.comp_registry.items()
                        if meta.get("is_valid_comp", True) is not False
                        and cid not in analytics.excluded_comps
                    }
                    for c in raw_comps:
                        cid = str(c.get("listing_id") or "")
                        if cid and cid in active_registry and cid not in seen_ids:
                            seen_ids.add(cid)
                            comps.append(c)
                else:
                    for c in raw_comps:
                        cid = str(c.get("listing_id") or "")
                        if cid and cid not in seen_ids and cid not in analytics.excluded_comps:
                            seen_ids.add(cid)
                            comps.append(c)

                # 2. Targeted Direct Fallback for all registered comps not found in corridor sweeps
                missing_comps = {
                    cid: meta for cid, meta in active_registry.items()
                    if cid not in seen_ids
                }

                if missing_comps and hasattr(collector, "fetch_missing_comps_fallback"):
                    fallback_comps = await collector.fetch_missing_comps_fallback(
                        missing_comps=missing_comps,
                        check_in=c_in,
                        check_out=c_out,
                        nights=nights,
                        use_cache=(not force),
                        max_cache_age_hours=max_cache_age,
                        max_concurrency=(None if parallel else 1),
                    )
                    for fc in fallback_comps:
                        fc_id = str(fc.get("listing_id") or "")
                        if fc_id and fc_id in active_registry and fc_id not in seen_ids:
                            seen_ids.add(fc_id)
                            comps.append(fc)

                comp_rates = [c["effective_nightly"] for c in comps]

                # Fetch Villa del Sol live guest checkout rate from Airbnb for apples-to-apples comparison
                our_rate_data = await collector.fetch_our_listing_price(
                    check_in=c_in,
                    check_out=c_out,
                    nights=nights,
                    use_cache=True,
                )
                if our_rate_data and our_rate_data.get("airbnb_effective_nightly"):
                    seg["our_airbnb_effective_nightly"] = our_rate_data["airbnb_effective_nightly"]
                    seg["our_airbnb_total"] = our_rate_data["airbnb_total"]
                    seg["is_our_airbnb_live"] = True

                evaluated = analytics.evaluate_segment(seg, comp_rates, comp_metadata=comps)
                return evaluated, comps, len(active_registry)

            if hasattr(collector, "track_interval_bytes"):
                async with collector.track_interval_bytes() as byte_tracker:
                    evaluated, comps, n_active = await _run_scrape()
                    inv_bytes = byte_tracker.bytes
            else:
                evaluated, comps, n_active = await _run_scrape()
                inv_bytes = max(0, getattr(collector, "total_bytes_transferred", 0) - inv_start_bytes)

            # If sales_tracker is configured, perform inline active reconciliation & calendar verification
            recorded_sales = []
            erased_sales = []
            if sales_tracker and hasattr(sales_tracker, "reconcile_and_verify_interval"):
                try:
                    recorded_sales, erased_sales = await sales_tracker.reconcile_and_verify_interval(
                        check_in=c_in,
                        check_out=c_out,
                        curr_comps=comps,
                        lead_time_days=lead,
                        segment_type=seg_type,
                    )
                except Exception as e:
                    logger.debug(f"Inline sales verification bypassed for {c_in} -> {c_out}: {e}")

            inv_duration = time.perf_counter() - inv_start_time
            inv_mb = inv_bytes / (1024 * 1024)

            comp_cnt = evaluated.get("comps_count", len(comps))
            booked_cnt = max(0, n_active - comp_cnt) if n_active > 0 else 0
            booked_str = f" ({booked_cnt} booked)" if n_active > 0 else ""
            if comp_cnt > 0:
                status_str = f"✓ [OK] {comp_cnt} comps available{booked_str} in {inv_duration:.1f}s ({inv_mb:.2f} MB)"
            else:
                status_str = f"⚠️ [0 Comps] 0 comps available{booked_str} in {inv_duration:.1f}s ({inv_mb:.2f} MB)"

            async with count_lock:
                completed_count += 1
                curr_completed = completed_count
                print(f"  [{curr_completed}/{total}] Scanning {c_in} -> {c_out} ({seg_type}, {nights}n, lead={lead}d)... {status_str}")
                if recorded_sales:
                    total_recorded_sales.extend(recorded_sales)
                    rec_line = format_comp_sales_line("Comp sales recorded", recorded_sales)
                    if rec_line:
                        print(rec_line)
                if erased_sales:
                    total_erased_sales.extend(erased_sales)
                    era_line = format_comp_sales_line("Comp sales erased", erased_sales)
                    if era_line:
                        print(era_line)

            metric = {
                "label": f"{c_in} -> {c_out} ({seg_type}, {nights}n)",
                "duration": inv_duration,
                "bytes": inv_bytes,
                "mb": inv_mb,
            }
            return evaluated, metric

    interval_tasks = [_process_interval((idx, seg)) for idx, seg in enumerate(active_segments, 1)]
    interval_results = await asyncio.gather(*interval_tasks)

    # Phase 3b: If sales_tracker is configured, output consolidated summary and refresh evaluations
    if sales_tracker:
        total_rec_count = len(total_recorded_sales)
        total_era_count = len(total_erased_sales)
        total_ledger = sales_tracker.get_total_sales_count() if hasattr(sales_tracker, "get_total_sales_count") else 0
        print(f"\n  ✓ Competitor Sales Engine: Recorded {total_rec_count} new sales, erased {total_era_count} sales (Total active sales in ledger: {total_ledger}).")
        # Re-evaluate segments with updated sales ledger and market compression surge flags
        re_evaluated = []
        for res in [r[0] for r in interval_results]:
            c_rates = [c.get("effective_nightly") for c in res.get("comps_list", []) if c.get("effective_nightly") is not None]
            c_meta = res.get("comps_list", [])
            re_evaluated.append(analytics.evaluate_segment(res, c_rates, comp_metadata=c_meta))
        evaluated_results = re_evaluated
    else:
        evaluated_results = [res[0] for res in interval_results]

    interval_metrics = [res[1] for res in interval_results]
    return evaluated_results, interval_metrics


async def run_weekly_advisory(
    quick: bool = False,
    max_segments: int = 12,
    start_date: str = None,
    end_date: str = None,
    push: bool = False,
    compare_platforms: bool = False,
    no_compare_platforms: bool = False,
    force: bool = False,
    max_cache_age: float = 20.0,
    parallel: bool = True,
):
    """
    Execute full weekly pricing audit:
    1. Fetch Kivoya calendar & base seasonal rates
    2. Segment open dates into weekends and midweeks
    3. Gather comp rates via Airbnb collector
    4. Compute percentiles, fee normalization, and prioritized recommendations
    5. Output Markdown, Google Sheets CSV, and JSON
    """
    total_start = time.perf_counter()
    config = load_config()
    print("=" * 70)
    print(f"🏠 STR Competitive Price Advisor: {config['property']['name']}")
    print(f"📍 Location: {config['property']['address']}")
    print("=" * 70)

    # 1. Kivoya Ingestion
    print("\n[Step 1/5] Querying Kivoya / Streamline VRS API...")
    step1_start = time.perf_counter()
    kivoya = KivoyaClient(unit_id=config["property"]["kivoya_unit_id"])
    blocked = kivoya.get_blocked_periods()
    rates = kivoya.get_seasonal_rates()
    print(f"  ✓ Found {len(blocked)} active reservations / blocked periods.")
    print(f"  ✓ Found {len(rates)} seasonal rate periods configured in Kivoya.")

    from src.reservation_store import ReservationStore
    res_store = ReservationStore()
    snap_count = snapshot_kivoya_rates(res_store)
    if snap_count > 0:
        print(f"  ✓ Snapshotted {snap_count} daily published rates in SQLite.")
    step1_time = time.perf_counter() - step1_start

    # 2. Date Segmentation
    print("\n[Step 2/5] Segmenting open calendar over next 12 months...")
    step2_start = time.perf_counter()
    segmenter = CalendarSegmenter(
        kivoya_client=kivoya,
        cleaning_fee=config["property"]["cleaning_fee"],
    )
    segments = segmenter.generate_unbooked_segments()
    print(f"  ✓ Generated {len(segments)} unbooked intervals (weekends & midweeks).")

    if start_date:
        segments = [s for s in segments if s["check_in"] >= start_date]
        print(f"  📅 Filtered to intervals starting >= {start_date}: {len(segments)} intervals")
    if end_date:
        segments = [s for s in segments if s["check_out"] <= end_date]
        print(f"  📅 Filtered to intervals ending <= {end_date}: {len(segments)} intervals")

    if quick:
        print(f"  ⚡ Quick mode: Evaluating first {max_segments} upcoming intervals.")
        active_segments = segments[:max_segments]
    else:
        active_segments = segments
    step2_time = time.perf_counter() - step2_start

    # 3. Comp Data Collection & Verification Bridge
    print("\n[Step 3/5] Collecting luxury competitive listings from Airbnb...")
    step3_start = time.perf_counter()
    from src.competitor_sales_tracker import CompetitorSalesTracker
    sales_tracker = CompetitorSalesTracker()
    sales_tracker.reconcile_with_latest_snapshot()

    from src.stealth_connection import StealthConnectionManager
    shared_proxy_mgr = StealthConnectionManager(required=True)
    try:
        num_workers = shared_proxy_mgr.max_workers if parallel else 1
        min_healthy = StealthConnectionManager.calculate_min_healthy(num_workers) if parallel else 1
        await shared_proxy_mgr.start_pool(
            num_workers=num_workers,
            min_healthy=min_healthy,
            wait_for_full_pool=False,
        )

        collector = AirbnbCollector(parallel=parallel, proxy_mgr=shared_proxy_mgr)
        evaluated_results: List[Dict[str, Any]] = []
        interval_metrics: List[Dict[str, Any]] = []
        analytics = PricingAnalyticsEngine(
            base_percentile=config["strategy"]["base_percentile"],
            cleaning_fee=config["property"]["cleaning_fee"],
            urgent_pct_diff=config["strategy"]["anomaly_thresholds"]["urgent_percent_diff"],
            urgent_lead_days=config["strategy"]["anomaly_thresholds"]["urgent_lead_days"],
            moderate_pct_diff=config["strategy"]["anomaly_thresholds"]["moderate_percent_diff"],
            sales_tracker=sales_tracker,
        )

        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            try:
                await collector.init_browser(p)
                evaluated_results, interval_metrics = await run_interval_evaluations(
                    collector=collector,
                    active_segments=active_segments,
                    analytics=analytics,
                    parallel=parallel,
                    force=force,
                    max_cache_age=max_cache_age,
                )

            finally:
                await collector.close_browser()

        step3_time = time.perf_counter() - step3_start
        step3_bytes = collector.total_bytes_transferred

        # 4. Reporting
        print("\n[Step 4/5] Generating multi-format advisory reports...")
        step4_start = time.perf_counter()
        urgent_pct = config.get("strategy", {}).get("anomaly_thresholds", {}).get("urgent_percent_diff", URGENT_PCT_DIFF)
        mod_pct = config.get("strategy", {}).get("anomaly_thresholds", {}).get("moderate_percent_diff", MODERATE_PCT_DIFF)
        reporter = PriceReportGenerator(
            output_dir="data",
            urgent_pct_diff=urgent_pct,
            moderate_pct_diff=mod_pct,
            sales_tracker=sales_tracker,
        )
        outputs = reporter.generate_all(
            evaluated_segments=evaluated_results,
            property_name=config["property"]["name"],
        )
        step4_time = time.perf_counter() - step4_start

        # 4b. Cross-Platform Comparison
        step4b_duration = 0.0
        step4b_bytes = 0
        step4b_errors = {}
        if compare_platforms:
            print("\n[Step 4b] Scraping cross-platform prices across Airbnb, VRBO, Booking.com, and Kivoya...")
            t4b_start = time.perf_counter()
            from src.platform_comparator import PlatformComparator
            comparator = PlatformComparator(parallel=parallel, proxy_mgr=shared_proxy_mgr)
            await comparator.compare_all_intervals(
                limit=max_segments if quick else None,
                start_date=start_date,
                end_date=end_date,
                force=force,
            )
            step4b_duration = time.perf_counter() - t4b_start
            step4b_bytes = comparator.total_bytes_transferred
            step4b_errors = getattr(comparator, "last_scrape_errors", {})
        elif quick and not no_compare_platforms:
            # Smart platform comparison for quick scan: only compare intervals affected by Streamline price updates in last 7 days
            from src.platform_comparator import PlatformComparator
            affected = res_store.get_intervals_with_recent_rate_changes(active_segments, days_back=7)
            if affected:
                print(f"\n[Step 4b] ⚡ Detected Streamline price changes in the last 7 days affecting {len(affected)} quick-scan interval(s).")
                for a in affected:
                    ch_dates = list(a.get("affected_rate_changes", {}).keys())
                    print(f"   • {a['check_in']} -> {a['check_out']} ({a['nights']}n): rates modified for {ch_dates}")
                print("  Scraping live cross-platform prices across Airbnb, VRBO, Booking.com, and Kivoya for affected intervals...")
                t4b_start = time.perf_counter()
                comparator = PlatformComparator(parallel=parallel, proxy_mgr=shared_proxy_mgr)
                await comparator.compare_all_intervals(
                    target_segments=affected,
                    force=True,
                )
                step4b_duration = time.perf_counter() - t4b_start
                step4b_bytes = comparator.total_bytes_transferred
                step4b_errors = getattr(comparator, "last_scrape_errors", {})
            else:
                print("\n[Step 4b] ℹ️ No Streamline price changes detected in the last 7 days for quick scan intervals.")
                print("  Skipping platform comparison (handled by weekly full scan).")
    finally:
        await shared_proxy_mgr.stop()

    # 4c. Competitor Sales Tracking & Absorption Velocity Summary
    print("\n[Step 4c] Empirical Absorption Velocity Summary...")
    step4c_start = time.perf_counter()
    grid = sales_tracker.compute_strategy_grid()
    active_alerts = sales_tracker.get_active_compression_alerts()
    print(f"  - Total Confirmed Sales:        {grid['total_sales']}")
    print(f"  - Overall Median Percentile:    {grid['overall_median_percentile']:.1f}%")
    print(f"  - Average Lead Time at Sale:    {grid['avg_lead_time_days']:.1f} days")
    print(f"  - Average Realized Nightly:     ${grid['avg_rate']:,.2f}")
    if active_alerts:
        print(f"  - ⚡ Active Market Compression: {len(active_alerts)} interval(s) surging!")
        for a in active_alerts:
            print(f"      • {a['check_in']} -> {a['check_out']}: {a['reason']}")
    step4c_time = time.perf_counter() - step4c_start

    # 5. HTML Dashboard Generation (docs/index.html)
    print("\n[Step 5/5] Generating interactive static HTML dashboard in docs/...")
    step5_start = time.perf_counter()
    from src.html_generator import HTMLDashboardGenerator
    html_gen = HTMLDashboardGenerator(
        output_path="docs/index.html",
        urgent_pct_diff=urgent_pct,
        moderate_pct_diff=mod_pct,
    )
    html_file = html_gen.generate()
    shutil.copy("data/latest_sheet.csv", "docs/latest_sheet.csv")
    shutil.copy("data/latest_report.md", "docs/latest_report.md")
    step5_time = time.perf_counter() - step5_start

    urgent_count = sum(1 for s in evaluated_results if s["priority_tier"] == "URGENT_ACTION")
    mod_count = sum(1 for s in evaluated_results if s["priority_tier"] == "MODERATE_ADJUSTMENT")
    info_count = sum(1 for s in evaluated_results if s["priority_tier"] == "INFORMATIONAL")

    print("\n" + "=" * 70)
    print("🎉 ADVISORY REPORT GENERATION COMPLETE")
    print("=" * 70)
    print(f"  🚨 Urgent Adjustments Required (This Week): {urgent_count}")
    print(f"  ⚠️  Moderate Adjustments (Monthly Review):   {mod_count}")
    print(f"  ✅  Competitive / On Target:                 {info_count}")
    print(f"\n📁 Output Files:")
    print(f"  - Markdown Summary: {outputs['markdown']}")
    print(f"  - Google Sheets CSV: {outputs['csv']}")
    print(f"  - Raw JSON Archive:  {outputs['json']}")
    print("=" * 70)

    total_duration = time.perf_counter() - total_start
    total_bytes = step3_bytes + step4b_bytes

    def _format_time(seconds: float) -> str:
        if seconds < 60:
            return f"{seconds:.1f}s"
        elif seconds < 3600:
            m = int(seconds // 60)
            s = seconds % 60
            return f"{m}m {s:04.1f}s"
        else:
            h = int(seconds // 3600)
            m = int((seconds % 3600) // 60)
            s = seconds % 60
            return f"{h}h {m:02d}m {s:04.1f}s"

    def _format_mb(byte_count: int) -> str:
        return f"{byte_count / (1024 * 1024):.2f} MB"

    print("\n" + "=" * 78)
    print("📊 RUNTIME & DATA TRANSFER PERFORMANCE AUDIT")
    print("=" * 78)
    mode_str = f"Stealth Multi-IP Feeder Pool (up to {collector.proxy_mgr.max_workers} hubs)" if parallel else "Sequential Single Proxy"
    print(f"Scrape Architecture: {mode_str}")
    print(f"{'Step / Sub-Interval':<48} {'Duration':>12} {'Network Transfer':>16}")
    print("-" * 78)
    print(f"{'[Step 1] Kivoya & SQLite Ingestion':<48} {_format_time(step1_time):>12} {_format_mb(0):>16}")
    print(f"{'[Step 2] Calendar Segmentation':<48} {_format_time(step2_time):>12} {_format_mb(0):>16}")
    print(f"{f'[Step 3] Luxury Comp Scraping ({len(active_segments)} intervals)':<48} {_format_time(step3_time):>12} {_format_mb(step3_bytes):>16}")
    for im in interval_metrics:
        print(f"  • {im['label']:<44} {_format_time(im['duration']):>12} {_format_mb(im['bytes']):>16}")
    print(f"{'[Step 4] Advisory & Anomaly Analytics':<48} {_format_time(step4_time):>12} {_format_mb(0):>16}")
    if step4b_duration > 0 or compare_platforms:
        s4b_err_cnt = sum(step4b_errors.values()) if step4b_errors else 0
        s4b_label = "[Step 4b] Cross-Platform Comparison"
        if s4b_err_cnt > 0:
            s4b_label += " (⚠️ Scrape Errors)"
        print(f"{s4b_label:<48} {_format_time(step4b_duration):>12} {_format_mb(step4b_bytes):>16}")
    print(f"{'[Step 4c] Sales Detection & Absorption':<48} {_format_time(step4c_time):>12} {_format_mb(0):>16}")
    print(f"{'[Step 5] HTML Dashboard & Asset Sync':<48} {_format_time(step5_time):>12} {_format_mb(0):>16}")
    print("-" * 78)
    print(f"{'Total End-to-End Execution':<48} {_format_time(total_duration):>12} {_format_mb(total_bytes):>16}")
    print("=" * 78)

    s4b_total_err = sum(step4b_errors.values()) if step4b_errors else 0
    if s4b_total_err > 0:
        err_msg = ", ".join(f"{ch.capitalize()}: {cnt} failed" for ch, cnt in step4b_errors.items() if cnt > 0)
        print(f"\n⚠️  [ALERT] Cross-platform comparison encountered scrape errors: {err_msg}.")
        print("   Examine the logs above or verify NordVPN proxy health.\n")

    if push:
        push_to_github(commit_msg=f"Update pricing dashboard and reports ({date.today().isoformat()})")


def push_to_github(commit_msg: str = "Update STR pricing dashboard and reports"):
    """Stage docs/ and data/, commit, and push to origin/main."""
    import subprocess
    print("\n🚀 Pushing updates to GitHub (GitHub Pages)...")
    try:
        subprocess.run(["git", "add", "docs/", "data/"], check=True)
        res = subprocess.run(["git", "diff", "--staged", "--quiet"])
        if res.returncode != 0:
            subprocess.run(["git", "commit", "-m", commit_msg], check=True)
            subprocess.run(["git", "push", "origin", "main"], check=True)
            print("  ✓ Successfully pushed to origin/main! Live dashboard will update in ~30–60 seconds.")
        else:
            print("  ✓ No new changes to push (already up to date with remote).")
    except Exception as e:
        print(f"  ❌ Git push error: {e}")


def test_kivoya_only():
    """Diagnostic tool to inspect Kivoya connectivity."""
    config = load_config()
    kivoya = KivoyaClient(unit_id=config["property"]["kivoya_unit_id"])
    print("Testing Kivoya connection...")
    blocked = kivoya.get_blocked_periods()
    rates = kivoya.get_seasonal_rates()
    print(f"Successfully retrieved {len(blocked)} reservations and {len(rates)} rate periods.")
    print("\nUpcoming 3 bookings:")
    for b in blocked[:3]:
        print(f"  {b['startdate']} to {b['enddate']}: {b['reason']}")
    print("\nSeasonal Rates:")
    for r in rates[:5]:
        print(f"  {r['period_begin']} to {r['period_end']}: {r['season_name']} = ${r['nightly_rate']}")


def main():
    parser = argparse.ArgumentParser(description="STR Competitive Price Advisor CLI")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Run price advisor")
    run_parser.add_argument("--weekly", action="store_true", help="Run full 12-month weekly scan")
    run_parser.add_argument("--quick", action="store_true", help="Run quick scan on first 10-12 intervals")
    run_parser.add_argument("--limit", type=int, default=12, help="Number of intervals for quick mode")
    run_parser.add_argument("--start-date", type=str, default=None, help="Filter intervals starting on or after YYYY-MM-DD")
    run_parser.add_argument("--end-date", type=str, default=None, help="Filter intervals ending on or before YYYY-MM-DD")
    run_parser.add_argument("--push", action="store_true", help="Automatically commit and push updated docs and data to GitHub")
    run_parser.add_argument("--compare-platforms", action="store_true", help="Scrape and compare prices across Airbnb, VRBO, Booking.com, and Kivoya")
    run_parser.add_argument("--no-compare-platforms", action="store_true", help="Explicitly skip platform comparison in quick mode even if recent Streamline rate changes occurred")
    run_parser.add_argument("--force", action="store_true", help="Force fresh live scraping from Airbnb even if cached")
    run_parser.add_argument("--max-cache-age", type=float, default=20.0, help="Max cache age in hours before refreshing (default: 20h)")
    run_parser.add_argument("--sequential", action="store_true", help="Scrape corridors sequentially instead of concurrently across feeder proxy pool")

    subparsers.add_parser("test-kivoya", help="Verify Kivoya API connectivity and rates")

    test_stealth_parser = subparsers.add_parser("test-stealth", help="Audit health and latency of parallel stealth VPN connections")
    test_stealth_parser.add_argument("--count", type=int, default=None, help="Number of connections to test (default: 8 or STEALTH_MAX_CONNECTIONS)")
    test_stealth_parser.add_argument("--target", type=str, default="https://www.google.com", help="Target URL to test (default: https://www.google.com)")

    gen_parser = subparsers.add_parser("generate-html", help="Re-generate docs/index.html from existing data")
    gen_parser.add_argument("--push", action="store_true", help="Automatically commit and push docs to GitHub")

    compare_parser = subparsers.add_parser("compare-platforms", help="Compare Villa del Sol pricing across Airbnb, VRBO, Booking.com, and Kivoya")
    compare_parser.add_argument("--limit", type=int, default=None, help="Limit number of intervals to compare")
    compare_parser.add_argument("--start-date", type=str, default=None, help="Filter intervals starting on or after YYYY-MM-DD")
    compare_parser.add_argument("--end-date", type=str, default=None, help="Filter intervals ending on or before YYYY-MM-DD")
    compare_parser.add_argument("--force", action="store_true", help="Force re-scraping even if cached")
    compare_parser.add_argument("--only-recent-changes", action="store_true", help="Only compare intervals affected by Streamline price updates in the last week")
    compare_parser.add_argument("--days-back", type=int, default=7, help="Days of rate history to check for price updates (default: 7)")
    compare_parser.add_argument("--sequential", action="store_true", help="Run platform comparisons sequentially instead of parallel multi-IP feeder pool")
    compare_parser.add_argument("--push", action="store_true", help="Automatically commit and push updated dashboard to GitHub")

    bootstrap_parser = subparsers.add_parser("bootstrap-comps", help="Bootstrap and curate comp registry")
    bootstrap_parser.add_argument("--limit", type=int, default=40, help="Max listings per tier")

    eval_parser = subparsers.add_parser("evaluate-comps", help="Evaluate all comps in registry with 6-factor quality & valuation rubric")
    eval_parser.add_argument("--no-save", action="store_true", help="Do not write results back to comps_registry.json")

    enrich_parser = subparsers.add_parser("enrich-comps", help="Deep scrape amenities, photos, and specs for comps")
    enrich_parser.add_argument("ids", nargs="*", default=[], help="Optional specific Airbnb listing ID(s) or URLs to enrich")
    enrich_parser.add_argument("--unenriched", action="store_true", help="Target discovered comps in config/listing_specs.json that lack deep descriptions/amenities (189 comps)")
    enrich_parser.add_argument("--all-discovered", action="store_true", dest="unenriched", help="Alias for --unenriched")
    enrich_parser.add_argument("--concurrency", type=int, default=2, help="Number of concurrent browser pages")
    enrich_parser.add_argument("--sequential", action="store_true", help="Run comp enrichment sequentially instead of concurrently (sets concurrency=1)")
    enrich_parser.add_argument("--limit", type=int, default=None, help="Limit number of comps to enrich")
    enrich_parser.add_argument("--force", action="store_true", help="Force re-scraping cached comps")
    enrich_parser.add_argument("--our-property", action="store_true", help="Enrich Villa del Sol property profile specifically")
    enrich_parser.add_argument("--sync-cached", action="store_true", help="Sync existing cached profiles under data/enriched_comps/ to registry and listing specs without scraping")
    enrich_parser.add_argument("--rules-only", action="store_true", help="Targeted scrape to backfill house rules (check-in/out, deposit, noise) for comps")

    add_comp_parser = subparsers.add_parser("add-comp", help="Deep scrape, evaluate, and register a new competitor listing")
    add_comp_parser.add_argument("identifier", type=str, help="Airbnb listing ID or URL")
    add_comp_parser.add_argument("--tier", choices=["tier_a", "tier_b"], default=None, help="Force assign to specific tier (default: auto-detect based on BR/capacity)")
    add_comp_parser.add_argument("--scrape-prices", action="store_true", help="Automatically scrape prices across open intervals after adding")
    add_comp_parser.add_argument("--limit", type=int, default=None, help="Limit number of intervals to price scrape")
    add_comp_parser.add_argument("--force", action="store_true", help="Force refresh listing profile even if cached")
    add_comp_parser.add_argument("--push", action="store_true", help="Automatically commit and push changes to GitHub")

    sync_res_parser = subparsers.add_parser("sync-reservations", help="Scrape and sync Streamline OwnerX reservations to SQLite and JSON")
    sync_res_parser.add_argument("--full", action="store_true", help="Sync complete historical reservations since 2022")
    sync_res_parser.add_argument("--days-back", type=int, default=60, help="Days of past reservations to include in incremental sync (default: 60)")
    sync_res_parser.add_argument("--dashboard", action="store_true", help="Re-generate HTML dashboard after syncing")
    sync_res_parser.add_argument("--push", action="store_true", help="Automatically commit and push updated data/docs to GitHub")

    remove_comp_parser = subparsers.add_parser("remove-comp", help="Remove competitor listing from registry, purge cache, and update dashboard")
    remove_comp_parser.add_argument("identifier", type=str, help="Airbnb listing ID or URL")
    remove_comp_parser.add_argument("--push", action="store_true", help="Automatically commit and push changes to GitHub")

    scrape_prices_parser = subparsers.add_parser("scrape-comp-prices", help="Scrape live checkout prices for a specific comp across open intervals")
    scrape_prices_parser.add_argument("identifier", type=str, help="Airbnb listing ID or URL")
    scrape_prices_parser.add_argument("--limit", type=int, default=None, help="Limit number of intervals to scrape")
    scrape_prices_parser.add_argument("--start-date", type=str, default=None, help="Filter intervals starting on or after YYYY-MM-DD")
    scrape_prices_parser.add_argument("--end-date", type=str, default=None, help="Filter intervals ending on or before YYYY-MM-DD")
    scrape_prices_parser.add_argument("--push", action="store_true", help="Automatically commit and push changes to GitHub")

    track_sales_parser = subparsers.add_parser("track-competitor-sales", help="Track competitor sales & absorption velocity from daily snapshots")
    track_sales_parser.add_argument("--backfill", action="store_true", help="Backfill historical competitor sales across all snapshots in data/")
    track_sales_parser.add_argument("--verify", action="store_true", help="Commit search disappearance diffs to sales ledger as CONFIRMED_BLOCKED (omits dropouts if unset)")
    track_sales_parser.add_argument("--dashboard", action="store_true", help="Re-generate HTML dashboard with updated sales velocity metrics")
    track_sales_parser.add_argument("--push", action="store_true", help="Automatically commit and push updated data/docs to GitHub")

    snapshot_rates_parser = subparsers.add_parser("snapshot-rates", help="Record snapshot of current Kivoya published nightly rates into SQLite")
    snapshot_rates_parser.add_argument("--date", type=str, default=None, help="Snapshot date (YYYY-MM-DD), defaults to today")
    snapshot_rates_parser.add_argument("--backfill", action="store_true", help="Backfill baseline snapshot to 2026-02-01")

    audit_parser = subparsers.add_parser("audit-comps", help="Audit active competitor portfolio against 6-factor luxury rubric and propose exclusions")
    audit_parser.add_argument("--save-report", action="store_true", help="Export full audit markdown report to data/comp_portfolio_audit.md")
    audit_parser.add_argument("--output", type=str, default=None, help="Custom output path for audit markdown report")
    audit_parser.add_argument("--json", action="store_true", help="Output audit results as JSON to stdout")

    disq_parser = subparsers.add_parser("disqualify-comp", help="Move competitor listing to disqualified section, purge cache and sales, and refresh dashboard")
    disq_parser.add_argument("identifier", type=str, help="Airbnb listing ID or URL")
    disq_parser.add_argument("--reason", type=str, default="Disqualified via competitor portfolio audit", help="Reason for disqualification")
    disq_parser.add_argument("--push", action="store_true", help="Automatically commit and push changes to GitHub")

    req_parser = subparsers.add_parser("requalify-comp", help="Restore disqualified competitor listing to active tiers, re-evaluate, and refresh dashboard")
    req_parser.add_argument("identifier", type=str, help="Airbnb listing ID or URL")
    req_parser.add_argument("--tier", choices=["tier_a", "tier_b"], default=None, help="Target tier (default: auto-detected based on bedrooms and capacity)")
    req_parser.add_argument("--push", action="store_true", help="Automatically commit and push changes to GitHub")

    discover_parser = subparsers.add_parser("discover-comps", help="Discover nearby luxury comp candidates matching Villa del Sol profile")
    discover_parser.add_argument("--corridors", type=str, default="tempe,chandler,ahwatukee,scottsdale", help="Comma-separated corridor names (default: tempe,chandler,ahwatukee,scottsdale)")
    discover_parser.add_argument("--limit", type=int, default=20, help="Maximum number of candidate comps to return (default: 20)")
    discover_parser.add_argument("--tier", choices=["tier_a", "tier_b", "both"], default="both", help="Target tier to discover (default: both)")
    discover_parser.add_argument("--min-rating", type=float, default=4.85, help="Minimum guest rating threshold (default: 4.85)")
    discover_parser.add_argument("--json", action="store_true", help="Output candidates as JSON to stdout")

    # sync-ratings
    sync_ratings_parser = subparsers.add_parser(
        "sync-ratings",
        help="Scrape and synchronize ratings and reviews across all booking channels",
    )
    sync_ratings_parser.add_argument(
        "--platform",
        choices=["all", "airbnb", "vrbo", "booking", "kivoya"],
        default="all",
        help="Platform to sync (default: all)",
    )
    sync_ratings_parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore cache and force re-scrape",
    )
    sync_ratings_parser.add_argument(
        "--backfill",
        action="store_true",
        help="Traverse all historical pages instead of incremental stop",
    )
    sync_ratings_parser.add_argument(
        "--headless",
        type=lambda x: (str(x).lower() == "true"),
        default=True,
        help="Run browser in headless mode (default: true)",
    )
    sync_ratings_parser.add_argument(
        "--no-dashboard",
        action="store_true",
        help="Do not re-generate docs/index.html after sync",
    )

    # show-ratings
    show_ratings_parser = subparsers.add_parser(
        "show-ratings",
        help="Display platform ratings, sub-scores, and recent reviews",
    )
    show_ratings_parser.add_argument(
        "--mobile",
        action="store_true",
        help="Format output for mobile ntfy push notification",
    )
    show_ratings_parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON data",
    )

    # audit-reviews
    audit_reviews_parser = subparsers.add_parser(
        "audit-reviews",
        help="Display AI reviews triage analysis and action plan from data/reviews_analysis.md",
    )

    subparsers.add_parser("status", help="Show system, comps, sales, and reservations status summary")

    args = parser.parse_args()

    if args.command == "run":
        parallel = not getattr(args, "sequential", False)
        if args.quick:
            asyncio.run(run_weekly_advisory(
                quick=True,
                max_segments=args.limit,
                start_date=args.start_date,
                end_date=args.end_date,
                push=args.push,
                compare_platforms=args.compare_platforms,
                no_compare_platforms=getattr(args, "no_compare_platforms", False),
                force=args.force,
                max_cache_age=args.max_cache_age,
                parallel=parallel,
            ))
        else:
            asyncio.run(run_weekly_advisory(
                quick=False,
                start_date=args.start_date,
                end_date=args.end_date,
                push=args.push,
                compare_platforms=args.compare_platforms,
                no_compare_platforms=getattr(args, "no_compare_platforms", False),
                force=args.force,
                max_cache_age=args.max_cache_age,
                parallel=parallel,
            ))
    elif args.command == "compare-platforms":
        from src.platform_comparator import PlatformComparator
        from src.html_generator import HTMLDashboardGenerator
        from src.reporter import PriceReportGenerator
        from src.reservation_store import ReservationStore
        from src.segmentation import CalendarSegmenter
        import shutil
        config = load_config()
        parallel = not getattr(args, "sequential", False)
        comparator = PlatformComparator(parallel=parallel)

        target_segs = None
        if getattr(args, "only_recent_changes", False):
            store = ReservationStore()
            snapshot_kivoya_rates(store)
            segmenter = CalendarSegmenter(kivoya_client=comparator.kivoya_client)
            all_segs = segmenter.generate_unbooked_segments()
            target_segs = store.get_intervals_with_recent_rate_changes(all_segs, days_back=args.days_back)
            print(f"⚡ Filtered to {len(target_segs)} intervals affected by Streamline price changes in the last {args.days_back} days.")

        results = asyncio.run(comparator.compare_all_intervals(
            limit=args.limit,
            start_date=args.start_date,
            end_date=args.end_date,
            force=args.force,
            target_segments=target_segs,
        ))
        print(f"\n📊 Successfully processed {len(results)} intervals across platforms.")
        urgent_pct = config.get("strategy", {}).get("anomaly_thresholds", {}).get("urgent_percent_diff", URGENT_PCT_DIFF)
        mod_pct = config.get("strategy", {}).get("anomaly_thresholds", {}).get("moderate_percent_diff", MODERATE_PCT_DIFF)
        html_gen = HTMLDashboardGenerator(
            output_path="docs/index.html",
            urgent_pct_diff=urgent_pct,
            moderate_pct_diff=mod_pct,
        )
        evaluated_segments = html_gen.generate_full_12_month_evaluation()
        out = html_gen.generate(evaluated_segments)
        reporter = PriceReportGenerator(
            output_dir="data",
            urgent_pct_diff=urgent_pct,
            moderate_pct_diff=mod_pct,
        )
        reporter.generate_all(evaluated_segments=evaluated_segments, property_name=config.get("property", {}).get("name", "Villa del Sol"))
        if Path("data/latest_sheet.csv").exists():
            shutil.copy("data/latest_sheet.csv", "docs/latest_sheet.csv")
        if Path("data/latest_report.md").exists():
            shutil.copy("data/latest_report.md", "docs/latest_report.md")
        print(f"✅ Dashboard regenerated successfully at: {out}")
        if args.push:
            push_to_github(commit_msg="Update cross-platform price comparisons")
    elif args.command == "generate-html":
        config = load_config()
        from src.html_generator import HTMLDashboardGenerator
        from src.reporter import PriceReportGenerator
        import shutil
        urgent_pct = config.get("strategy", {}).get("anomaly_thresholds", {}).get("urgent_percent_diff", URGENT_PCT_DIFF)
        mod_pct = config.get("strategy", {}).get("anomaly_thresholds", {}).get("moderate_percent_diff", MODERATE_PCT_DIFF)
        html_gen = HTMLDashboardGenerator(
            output_path="docs/index.html",
            urgent_pct_diff=urgent_pct,
            moderate_pct_diff=mod_pct,
        )
        evaluated_segments = html_gen.generate_full_12_month_evaluation()
        out = html_gen.generate(evaluated_segments)
        reporter = PriceReportGenerator(
            output_dir="data",
            urgent_pct_diff=urgent_pct,
            moderate_pct_diff=mod_pct,
        )
        reporter.generate_all(evaluated_segments=evaluated_segments, property_name=config.get("property", {}).get("name", "Villa del Sol"))
        if Path("data/latest_sheet.csv").exists():
            shutil.copy("data/latest_sheet.csv", "docs/latest_sheet.csv")
        if Path("data/latest_report.md").exists():
            shutil.copy("data/latest_report.md", "docs/latest_report.md")
        print(f"✅ Dashboard generated successfully at: {out}")
        if args.push:
            push_to_github(commit_msg="Update static HTML dashboard and reports")
    elif args.command == "bootstrap-comps":
        from src.comp_curator import CompCurator
        curator = CompCurator()
        asyncio.run(curator.bootstrap_market(limit_per_tier=args.limit))
    elif args.command == "evaluate-comps":
        from src.comp_evaluator import CompEvaluator
        evaluator = CompEvaluator()
        evaluator.evaluate_all_in_registry(save=not args.no_save)
    elif args.command == "enrich-comps":
        from src.listing_enricher import ListingEnricher
        concurrency = 1 if getattr(args, "sequential", False) else args.concurrency
        enricher = ListingEnricher(headless=True)
        if args.sync_cached:
            enricher.sync_cached_to_registry()
        elif args.our_property:
            asyncio.run(enricher.enrich_our_property(force_refresh=args.force))
        elif args.rules_only:
            ids = [i.strip() for i in args.ids if i.strip()] if getattr(args, "ids", None) else None
            asyncio.run(enricher.enrich_house_rules_only(
                concurrency=concurrency,
                limit=args.limit,
                listing_ids=ids,
            ))
        else:
            ids = [i.strip() for i in args.ids if i.strip()] if getattr(args, "ids", None) else None
            asyncio.run(enricher.enrich_all_comps(
                concurrency=concurrency,
                limit=args.limit,
                force_refresh=args.force,
                unenriched_only=bool(args.unenriched),
                listing_ids=ids,
            ))
    elif args.command == "test-kivoya":
        test_kivoya_only()
    elif args.command == "test-stealth":
        from src.stealth_connection import StealthConnectionManager, DEFAULT_MAX_STEALTH_CONNECTIONS

        env_max = os.getenv("STEALTH_MAX_CONNECTIONS")
        if env_max:
            try:
                default_count = int(env_max)
            except ValueError:
                default_count = DEFAULT_MAX_STEALTH_CONNECTIONS
        else:
            default_count = DEFAULT_MAX_STEALTH_CONNECTIONS
        count = args.count if args.count is not None else default_count
        if count < 1:
            print(f"❌ [ERROR] --count must be >= 1 (got {count})")
            sys.exit(1)

        min_healthy = StealthConnectionManager.calculate_min_healthy(count)

        async def _run_test_stealth():
            mgr = StealthConnectionManager(max_workers=count)
            print("=" * 82)
            print(f"🛡️  STEALTH CONNECTION POOL HEALTH & LATENCY AUDIT ({count} Workers, Quorum: {min_healthy})")
            print(f"🎯 Target Health Probe: {args.target}")
            print("=" * 82)
            try:
                await mgr.start_pool(num_workers=count, min_healthy=min_healthy, wait_for_full_pool=False, test_target=args.target)
            except Exception as e:
                print(f"⚠️ Warning during pool initialization: {e}")
            print("\n" + "-" * 82)
            print(f"{'#':<3} {'City Hub':<18} {'Local Port':<12} {'Remote NordVPN Node':<32} {'Latency':>10} {'Status':<10}")
            print("-" * 82)
            online_count = 0
            for idx, ep in enumerate(mgr.endpoints, 1):
                lat_str = f"{ep.latency_ms:.0f}ms" if ep.latency_ms is not None else "N/A"
                badge = "✅ ONLINE" if ep.status == "ONLINE" else f"❌ {ep.status}"
                if ep.status == "ONLINE":
                    online_count += 1
                city_short = ep.city.split(",")[0] if ep.city else ep.name
                print(f"{idx:<3} {city_short:<18} {ep.local_port:<12} {ep.remote_host:<32} {lat_str:>10} {badge:<10}")
            print("-" * 82)
            print(f"📊 Summary: {online_count}/{count} endpoints healthy and passing end-to-end HTTP test (minimum quorum: {min_healthy}).")
            print("=" * 82)
            await mgr.stop()

            if online_count >= count:
                print(f"✅ [SUCCESS] All {online_count}/{count} stealth endpoints are healthy and operational.")
                sys.exit(0)
            elif online_count >= min_healthy:
                print(f"\n⚠️  [QUORUM MET] {online_count}/{count} healthy nodes verified (minimum {min_healthy} required). Pipeline ready to proceed with degraded capacity.\n")
                sys.exit(0)
            else:
                print(f"❌ [STEALTH ERROR] Pre-flight audit failed: only {online_count}/{count} stealth endpoints are healthy (minimum quorum: {min_healthy} required). Refusing to proceed with under-provisioned pool.")
                sys.exit(1)

        asyncio.run(_run_test_stealth())
    elif args.command == "add-comp":
        from src.comp_manager import CompManager
        manager = CompManager()
        asyncio.run(manager.add_comp(
            identifier=args.identifier,
            tier=args.tier,
            scrape_prices=args.scrape_prices,
            limit_intervals=args.limit,
            force_refresh=args.force,
        ))
        if args.push:
            push_to_github(commit_msg=f"Add comp {args.identifier} and update dashboard")
    elif args.command == "remove-comp":
        from src.comp_manager import CompManager
        manager = CompManager()
        success = manager.remove_comp(identifier=args.identifier)
        if success and args.push:
            push_to_github(commit_msg=f"Remove comp {args.identifier} and update dashboard")
    elif args.command == "scrape-comp-prices":
        from src.comp_manager import CompManager
        manager = CompManager()
        asyncio.run(manager.scrape_comp_interval_prices(
            identifier=args.identifier,
            limit=args.limit,
            start_date=args.start_date,
            end_date=args.end_date,
        ))
        if args.push:
            push_to_github(commit_msg=f"Scrape interval prices for comp {args.identifier}")
    elif args.command == "sync-reservations":
        from src.ownerx_client import OwnerXClient
        from src.reservation_store import ReservationStore
        from datetime import date, timedelta
        import shutil

        print("=" * 70)
        print("📥 Streamline OwnerX Reservation Sync")
        print("=" * 70)

        client = OwnerXClient()
        print("🔑 Authenticating with Streamline OwnerX...")
        client.authenticate()
        print(f"  ✓ Authenticated as processor #{client.processor_id}")

        if args.full:
            print("📦 Full Sync: Fetching all historical and future reservations (2022+)...")
            raw_res = client.fetch_raw_reservations()
            mode = "full"
        else:
            days = args.days_back or 60
            cutoff = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
            print(f"⚡ Incremental Sync: Fetching reservations ending >= {cutoff} (past {days} days + all future)...")
            raw_res = client.fetch_raw_reservations(arriving_after=cutoff)
            mode = "incremental"

        print(f"  ✓ Fetched {len(raw_res)} reservation records.")
        normalized = [OwnerXClient.normalize_reservation(r) for r in raw_res]
        store = ReservationStore()
        stats = store.upsert_reservations(normalized, sync_mode=mode)
        print(f"  ✓ Upserted into SQLite: {stats['upserted']} records ({stats['future']} future, {stats['past']} past).")
        print(f"  ✓ Synced JSON database at: {store.json_path}")

        # Snapshot current Kivoya published nightly rates
        snap_count = snapshot_kivoya_rates(store)
        print(f"  ✓ Recorded {snap_count} published nightly rate snapshots for today ({date.today().isoformat()}).")

        if args.dashboard:
            print("\n🎨 Re-generating dashboard with updated reservations...")
            config = load_config()
            from src.html_generator import HTMLDashboardGenerator
            from src.reporter import PriceReportGenerator
            urgent_pct = config.get("strategy", {}).get("anomaly_thresholds", {}).get("urgent_percent_diff", URGENT_PCT_DIFF)
            mod_pct = config.get("strategy", {}).get("anomaly_thresholds", {}).get("moderate_percent_diff", MODERATE_PCT_DIFF)
            html_gen = HTMLDashboardGenerator(
                output_path="docs/index.html",
                urgent_pct_diff=urgent_pct,
                moderate_pct_diff=mod_pct,
            )
            evaluated_segments = html_gen.generate_full_12_month_evaluation()
            out = html_gen.generate(evaluated_segments)
            reporter = PriceReportGenerator(
                output_dir="data",
                urgent_pct_diff=urgent_pct,
                moderate_pct_diff=mod_pct,
            )
            reporter.generate_all(evaluated_segments=evaluated_segments, property_name=config.get("property", {}).get("name", "Villa del Sol"))
            if Path("data/latest_sheet.csv").exists():
                shutil.copy("data/latest_sheet.csv", "docs/latest_sheet.csv")
            if Path("data/latest_report.md").exists():
                shutil.copy("data/latest_report.md", "docs/latest_report.md")
            print(f"✅ Dashboard generated successfully at: {out}")

        if args.push:
            push_to_github(commit_msg="Sync Streamline OwnerX reservations and update dashboard")
    elif args.command == "track-competitor-sales":
        from src.competitor_sales_tracker import CompetitorSalesTracker
        from src.html_generator import HTMLDashboardGenerator
        from src.reporter import PriceReportGenerator
        import shutil

        tracker = CompetitorSalesTracker()
        print("=" * 70)
        print("🎯 Competitor Sales Tracking & Absorption Velocity Engine")
        print("=" * 70)

        if args.backfill:
            print("📦 Backfilling historical sales across all pricing_data_*.json snapshots...")
            sales = tracker.backfill_all_snapshots(verify_calendar=args.verify)
            confirmed = [s for s in sales if s.get("verification_status") == "CONFIRMED_BLOCKED"]
            print(f"  ✓ Total sales events recorded: {len(sales)} ({len(confirmed)} confirmed registered comp bookings).")
        else:
            print("🔍 Diffing latest snapshot with historical interval bridging...")
            snapshots = sorted(Path("data").glob("pricing_data_*.json"))
            if snapshots:
                curr_snap = snapshots[-1]
                if args.verify:
                    print(f"🛡️  Starting concurrent live verification via Playwright & NordVPN...")
                    _, curr_intervals = tracker.extract_intervals_from_snapshot(curr_snap)
                    sales = asyncio.run(tracker.diff_and_verify_staged_comps(
                        staged_intervals=curr_intervals,
                        max_concurrent_checks=5,
                    ))
                else:
                    sales = tracker.process_latest_snapshot(curr_snap, verify_calendar=False)
                confirmed = [s for s in sales if s.get("verification_status") == "CONFIRMED_BLOCKED"]
                print(f"  ✓ Processed diff for {curr_snap.name}.")
                print(f"  ✓ Detected {len(sales)} sales events ({len(confirmed)} confirmed registered comp bookings).")
            else:
                print("  ⚠️ Need at least 1 daily snapshot in data/ to perform diff.")

        grid = tracker.compute_strategy_grid()
        print(f"\n📊 Current Empirical Absorption Metrics:")
        print(f"  - Total Confirmed Sales:        {grid['total_sales']}")
        print(f"  - Overall Median Percentile:    {grid['overall_median_percentile']:.1f}%")
        print(f"  - Average Lead Time at Sale:    {grid['avg_lead_time_days']:.1f} days")
        print(f"  - Average Realized Nightly:     ${grid['avg_rate']:,.2f}")
        active_alerts = tracker.get_active_compression_alerts()
        if active_alerts:
            print(f"  - ⚡ Active Market Compression: {len(active_alerts)} interval(s) surging!")
            for a in active_alerts:
                print(f"      • {a['check_in']} -> {a['check_out']}: {a['reason']}")

        if args.dashboard:
            print("\n🎨 Re-generating dashboard with updated sales velocity metrics...")
            config = load_config()
            urgent_pct = config.get("strategy", {}).get("anomaly_thresholds", {}).get("urgent_percent_diff", URGENT_PCT_DIFF)
            mod_pct = config.get("strategy", {}).get("anomaly_thresholds", {}).get("moderate_percent_diff", MODERATE_PCT_DIFF)
            html_gen = HTMLDashboardGenerator(
                output_path="docs/index.html",
                urgent_pct_diff=urgent_pct,
                moderate_pct_diff=mod_pct,
            )
            evaluated_segments = html_gen.generate_full_12_month_evaluation()
            out = html_gen.generate(evaluated_segments)
            reporter = PriceReportGenerator(
                output_dir="data",
                urgent_pct_diff=urgent_pct,
                moderate_pct_diff=mod_pct,
                sales_tracker=tracker,
            )
            reporter.generate_all(evaluated_segments=evaluated_segments, property_name=config.get("property", {}).get("name", "Villa del Sol"))
            if Path("data/latest_sheet.csv").exists():
                shutil.copy("data/latest_sheet.csv", "docs/latest_sheet.csv")
            if Path("data/latest_report.md").exists():
                shutil.copy("data/latest_report.md", "docs/latest_report.md")
            print(f"✅ Dashboard generated successfully at: {out}")

        if args.push:
            push_to_github(commit_msg="Update competitor sales tracking and absorption velocity metrics")
    elif args.command == "snapshot-rates":
        from src.reservation_store import ReservationStore
        from datetime import date
        snap_d = "2026-02-01" if args.backfill else (args.date or date.today().strftime("%Y-%m-%d"))
        store = ReservationStore()
        count = snapshot_kivoya_rates(store, snapshot_date=snap_d)
        print(f"✅ Successfully recorded {count} daily published rate snapshots for {snap_d}")
    elif args.command == "audit-comps":
        from src.comp_evaluator import CompEvaluator
        evaluator = CompEvaluator()
        audit_res = evaluator.audit_portfolio(save_report=args.save_report, output_path=args.output)
        if args.json:
            import json
            print(json.dumps(audit_res, indent=2))
    elif args.command == "disqualify-comp":
        from src.comp_manager import CompManager
        manager = CompManager()
        success = manager.disqualify_comp(identifier=args.identifier, reason=args.reason)
        if success and args.push:
            push_to_github(commit_msg=f"Disqualify comp {args.identifier}: {args.reason}")
    elif args.command == "requalify-comp":
        from src.comp_manager import CompManager
        manager = CompManager()
        success = manager.requalify_comp(identifier=args.identifier, target_tier=args.tier)
        if success and args.push:
            push_to_github(commit_msg=f"Requalify comp {args.identifier}")
    elif args.command == "discover-comps":
        from src.comp_curator import CompCurator
        curator = CompCurator()
        corridors = [c.strip() for c in args.corridors.split(",") if c.strip()]
        asyncio.run(curator.discover_comps(
            corridors=corridors,
            limit=args.limit,
            tier=args.tier,
            min_rating=args.min_rating,
            output_json=args.json,
        ))
    elif args.command == "status":
        print_system_status()
    elif args.command == "sync-ratings":
        run_sync_ratings(args)
    elif args.command == "show-ratings":
        print_ratings_summary(mobile=args.mobile, as_json=args.json)
    elif args.command == "audit-reviews":
        print_reviews_audit()
    else:
        parser.print_help()


def run_sync_ratings(args):
    """Scrape and synchronize ratings and reviews across channels."""
    from src.ratings_collector import RatingsCollector
    from src.html_generator import HTMLDashboardGenerator

    collector = RatingsCollector(headless=args.headless)
    platforms = None if args.platform == "all" else [args.platform]

    print(f"⭐ Synchronizing ratings and reviews for: {args.platform}...")
    summary = asyncio.run(collector.sync_all(platforms=platforms, force=args.force, backfill=args.backfill))

    print(f"✅ Sync complete: {summary.get('successful', 0)} platforms updated, {summary.get('total_new_reviews', 0)} new reviews added.")
    for p_id, res in summary.get("channels", {}).items():
        if isinstance(res, dict):
            status = res.get("status", "ok")
            rating = res.get("rating")
            cnt = res.get("review_count", 0)
            added = res.get("new_reviews_added", 0)
            print(f"  • {p_id.title():<12}: Rating={rating} ({cnt} reviews, +{added} new) [{status}]")

    if not args.no_dashboard:
        print("🎨 Regenerating HTML dashboard with updated reviews...")
        html_gen = HTMLDashboardGenerator(output_path="docs/index.html")
        html_gen.generate()
        print("✅ Dashboard updated at docs/index.html")


def print_ratings_summary(mobile: bool = False, as_json: bool = False):
    """Display platform ratings, category sub-scores, and recent reviews."""
    from src.ratings_collector import RatingsCollector
    from datetime import date, timedelta

    collector = RatingsCollector()
    data = collector.data

    if as_json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return

    platforms = data.get("platforms", {})
    reviews = data.get("reviews", [])
    window_days = int(data.get("recent_window_days", 30))
    cutoff = date.today() - timedelta(days=window_days)

    recent_reviews = []
    for r in reviews:
        d_str = r.get("date")
        if d_str:
            try:
                if date.fromisoformat(d_str[:10]) >= cutoff:
                    recent_reviews.append(r)
            except Exception:
                pass

    if mobile:
        lines = []
        lines.append("⭐ Villa del Sol — Ratings & Recent Reviews")
        lines.append("===========================================")
        lines.append("📊 Platform Ratings (Native Scales):")
        for p_id in ["airbnb", "vrbo", "booking", "kivoya"]:
            p = platforms.get(p_id, {})
            name = p.get("display_name", p_id.title())
            scale = p.get("scale", "5.0")
            rating = p.get("rating")
            try:
                r_num = float(rating) if rating is not None else None
            except (ValueError, TypeError):
                r_num = None
            r_str = f"{r_num:.2f}" if (r_num is not None and scale == "5.0") else (f"{r_num:.1f}" if r_num is not None else "N/A")
            cnt = p.get("review_count", 0)
            lines.append(f"  • {name:<12}: {r_str} / {scale} ({cnt} reviews)")
        lines.append("")

        if recent_reviews:
            lines.append(f"🔔 Recent Reviews in Last {window_days} Days ({len(recent_reviews)}):")
            lines.append("-------------------------------------------")
            for idx, r in enumerate(recent_reviews[:3], 1):
                p_id = (r.get("platform") or "unknown").title()
                raw_score = r.get("rating")
                if raw_score is not None:
                    try:
                        score = float(raw_score)
                        max_s = float(r.get("rating_max") or 5.0)
                        score_str = f"★ {score:.1f}/{max_s:.0f}" if max_s == 10.0 else f"★ {score:.1f}"
                    except (ValueError, TypeError):
                        score_str = "No Rating"
                else:
                    score_str = "No Rating"
                author = r.get("reviewer_name", "Guest")
                d_str = r.get("date", "")
                try:
                    d_fmt = date.fromisoformat(d_str[:10]).strftime("%b %d, %Y")
                except Exception:
                    d_fmt = d_str
                lines.append(f"{idx}. [{p_id}] {score_str} — {author} ({d_fmt})")
                body = r.get("body", "").strip()
                if len(body) > 140:
                    body = body[:137] + "..."
                lines.append(f'   "{body}"')
                lines.append("")
            if len(recent_reviews) > 3:
                lines.append(f"📌 + {len(recent_reviews) - 3} more recent reviews in web dashboard")
        else:
            lines.append(f"🔔 Recent Reviews: No new reviews in the last {window_days} days.")
            lines.append("")
            if reviews:
                latest_r = reviews[0]
                p_id = (latest_r.get("platform") or "unknown").title()
                raw_score = latest_r.get("rating")
                if raw_score is not None:
                    try:
                        score = float(raw_score)
                        max_s = float(latest_r.get("rating_max") or 5.0)
                        score_str = f"★ {score:.1f}/{max_s:.0f}" if max_s == 10.0 else f"★ {score:.1f}"
                    except (ValueError, TypeError):
                        score_str = "No Rating"
                else:
                    score_str = "No Rating"
                author = latest_r.get("reviewer_name", "Guest")
                d_str = latest_r.get("date", "")
                try:
                    d_fmt = date.fromisoformat(d_str[:10]).strftime("%b %d, %Y")
                except Exception:
                    d_fmt = d_str
                lines.append("📌 Most Recent Review on Record:")
                lines.append(f"  • [{p_id}] {score_str} — {author} ({d_fmt})")
                body = latest_r.get("body", "").strip()
                if len(body) > 160:
                    body = body[:157] + "..."
                lines.append(f'    "{body}"')

        output = "\n".join(lines).strip()
        if len(output) > 3800:
            output = output[:3790] + "...\n(truncated)"
        print(output)
        return output

    # Terminal output
    today_str = date.today().isoformat()
    print(f"\n🏰 Villa del Sol — Cross-Platform Ratings Summary [{today_str}]")
    print("=" * 60)
    for p_id in ["airbnb", "vrbo", "booking", "kivoya"]:
        p = platforms.get(p_id, {})
        name = p.get("display_name", p_id.title())
        scale = p.get("scale", "5.0")
        rating = p.get("rating")
        try:
            r_num = float(rating) if rating is not None else None
        except (ValueError, TypeError):
            r_num = None
        r_str = f"{r_num:.2f}" if (r_num is not None and scale == "5.0") else (f"{r_num:.1f}" if r_num is not None else "N/A")
        cnt = p.get("review_count", 0)
        status = p.get("status", "ok")
        print(f"  • {name:<14}: {r_str} / {scale} ({cnt} reviews) [status: {status}]")
        sub_scores = p.get("sub_scores", {})
        if sub_scores:
            sub_str = ", ".join(f"{k.replace('_', ' ').title()}: {v}" for k, v in list(sub_scores.items())[:4])
            print(f"    Sub-scores: {sub_str}")
    print("-" * 60)
    print(f"Total Reviews Stored: {len(reviews)} | Recent in {window_days}d: {len(recent_reviews)}")
    if recent_reviews:
        print("\nRecent Reviews:")
        for r in recent_reviews[:3]:
            plat_name = (r.get("platform") or "unknown").title()
            r_rating = r.get("rating")
            r_rating_str = f"{r_rating}★" if r_rating is not None else "N/A"
            r_author = r.get("reviewer_name") or "Guest"
            r_date = r.get("date") or "Unknown"
            print(f"  - [{plat_name}] {r_rating_str} by {r_author} on {r_date}")
            print(f"    \"{r.get('body', '')[:100]}...\"")
    print("=" * 60 + "\n")


def print_reviews_audit():
    """Display operational review analysis report with ANSI color highlights."""
    analysis_path = Path("data/reviews_analysis.md")
    if not analysis_path.exists():
        print("⚠️ No review analysis report found at data/reviews_analysis.md.")
        print("Run the Antigravity review analysis skill (analyze-reviews) to generate the operational triage report.")
        return

    content = analysis_path.read_text(encoding="utf-8")
    # Apply ANSI highlights to operational triage tags
    content_colored = (
        content
        .replace("[SEVERE]", "\033[91m[SEVERE]\033[0m")
        .replace("[RECURRING]", "\033[93m[RECURRING]\033[0m")
        .replace("[MINOR]", "\033[96m[MINOR]\033[0m")
        .replace("[OPEN]", "\033[91m[OPEN]\033[0m")
        .replace("[VERIFIED RESOLVED]", "\033[92m[VERIFIED RESOLVED]\033[0m")
    )
    print("\n" + "=" * 65)
    print("🔍 Villa del Sol — Guest Reviews Operational Audit")
    print("=" * 65 + "\n")
    print(content_colored)


def print_system_status():
    from datetime import date
    from pathlib import Path
    from src.reservation_store import ReservationStore
    from src.competitor_sales_tracker import CompetitorSalesTracker

    today = date.today().isoformat()
    lines = []
    lines.append(f"🏰 Villa del Sol — STR Advisor Status [{today}]")
    lines.append("=" * 55)

    # 1. Comps Registry
    try:
        import json
        reg_data = json.loads(Path("config/comps_registry.json").read_text())
        tier_a = len(reg_data.get("tier_a", {}))
        tier_b = len(reg_data.get("tier_b", {}))
        disq = len(reg_data.get("disqualified", {}))
        excl = len(reg_data.get("excluded_comps", {}))
        active_total = tier_a + tier_b
        lines.append(f"📊 Comps Registry: {active_total} active comps (Tier A: {tier_a}, Tier B: {tier_b} | Disqualified: {disq}, Excluded: {excl})")
    except Exception as e:
        lines.append(f"📊 Comps Registry: Error loading ({e})")

    # 2. Competitor Sales
    try:
        tracker = CompetitorSalesTracker()
        recent = tracker.get_recent_sales(lookback_days=30)
        grid = tracker.compute_strategy_grid()
        total_sales = grid.get("total_sales", 0)
        p50 = grid.get("overall_median_percentile")
        p50_str = f"{p50:.1f}%" if p50 is not None else "N/A"
        lines.append(f"🏷️  Competitor Sales: {total_sales} confirmed sales in DB (Median Pct: {p50_str})")
        lines.append(f"   Recent (30d): {len(recent)} sales detected")
    except Exception as e:
        lines.append(f"🏷️  Competitor Sales: Error loading ({e})")

    # 3. Reservations
    try:
        store = ReservationStore()
        res_list = store.get_all_reservations()
        upcoming = [r for r in res_list if r.get("end_date", "") >= today]
        upcoming_sorted = sorted(upcoming, key=lambda r: r.get("start_date", ""))
        lines.append(f"📅 Reservations: {len(upcoming)} upcoming booked intervals in DB")
        if upcoming_sorted:
            next_b = upcoming_sorted[0]
            s_date = next_b.get("start_date")
            e_date = next_b.get("end_date")
            nights = int(next_b.get("days_number") or 0)
            gross = float(next_b.get("gross_rent") or 0.0)
            lines.append(f"   Next Booking: {s_date} -> {e_date} ({nights}n, ${gross:,.2f})")
    except Exception as e:
        lines.append(f"📅 Reservations: Error loading ({e})")

    # 4. Snapshots
    data_dir = Path("data")
    snapshots = sorted(data_dir.glob("pricing_data_*.json"))
    if snapshots:
        lines.append(f"📸 Latest Snapshot: {snapshots[-1].name}")
    else:
        lines.append("📸 Snapshots: None found in data/")

    lines.append("=" * 55)
    output = "\n".join(lines)
    print(output)
    return output


if __name__ == "__main__":
    if sys.prefix == sys.base_prefix:
        repo_root = Path(__file__).resolve().parent.parent
        venv_python = repo_root / ".venv" / "bin" / "python"
        if venv_python.exists():
            os.environ["VIRTUAL_ENV"] = str(repo_root / ".venv")
            os.environ["PATH"] = f"{venv_python.parent}:{os.environ.get('PATH', '')}"
            os.environ["PYTHONPATH"] = str(repo_root) + (f":{os.environ['PYTHONPATH']}" if "PYTHONPATH" in os.environ else "")
            os.execv(str(venv_python), [str(venv_python), "-m", "src.cli"] + sys.argv[1:])
    main()
