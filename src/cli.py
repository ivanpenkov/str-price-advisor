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
from pathlib import Path
import sys
from typing import List, Dict, Any

from playwright.async_api import async_playwright
import yaml

from src.kivoya_client import KivoyaClient
from src.segmentation import CalendarSegmenter
from src.airbnb_collector import AirbnbCollector
from src.analytics import PricingAnalyticsEngine
from src.reporter import PriceReportGenerator


def load_config(config_path: str = "config/settings.yaml") -> Dict[str, Any]:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


async def run_weekly_advisory(quick: bool = False, max_segments: int = 12):
    """
    Execute full weekly pricing audit:
    1. Fetch Kivoya calendar & base seasonal rates
    2. Segment open dates into weekends and midweeks
    3. Gather comp rates via Airbnb collector
    4. Compute percentiles, fee normalization, and prioritized recommendations
    5. Output Markdown, Google Sheets CSV, and JSON
    """
    config = load_config()
    print("=" * 70)
    print(f"🏠 STR Competitive Price Advisor: {config['property']['name']}")
    print(f"📍 Location: {config['property']['address']}")
    print("=" * 70)

    # 1. Kivoya Ingestion
    print("\n[Step 1/5] Querying Kivoya / Streamline VRS API...")
    kivoya = KivoyaClient(unit_id=config["property"]["kivoya_unit_id"])
    blocked = kivoya.get_blocked_periods()
    rates = kivoya.get_seasonal_rates()
    print(f"  ✓ Found {len(blocked)} active reservations / blocked periods.")
    print(f"  ✓ Found {len(rates)} seasonal rate periods configured in Kivoya.")

    # 2. Date Segmentation
    print("\n[Step 2/5] Segmenting open calendar over next 12 months...")
    segmenter = CalendarSegmenter(
        kivoya_client=kivoya,
        cleaning_fee=config["property"]["cleaning_fee"],
    )
    segments = segmenter.generate_unbooked_segments()
    print(f"  ✓ Generated {len(segments)} unbooked intervals (weekends & midweeks).")

    if quick:
        print(f"  ⚡ Quick mode: Evaluating first {max_segments} upcoming intervals.")
        active_segments = segments[:max_segments]
    else:
        active_segments = segments

    # 3. Comp Data Collection
    print("\n[Step 3/5] Collecting luxury competitive listings from Airbnb...")
    collector = AirbnbCollector()
    evaluated_results: List[Dict[str, Any]] = []
    analytics = PricingAnalyticsEngine(
        base_percentile=config["strategy"]["base_percentile"],
        cleaning_fee=config["property"]["cleaning_fee"],
        urgent_pct_diff=config["strategy"]["anomaly_thresholds"]["urgent_percent_diff"],
        urgent_lead_days=config["strategy"]["anomaly_thresholds"]["urgent_lead_days"],
        moderate_pct_diff=config["strategy"]["anomaly_thresholds"]["moderate_percent_diff"],
    )

    async with async_playwright() as p:
        await collector.init_browser(p)
        total = len(active_segments)

        for idx, seg in enumerate(active_segments, 1):
            c_in = seg["check_in"]
            c_out = seg["check_out"]
            nights = seg["nights"]
            lead = seg["lead_time_days"]
            print(f"  [{idx}/{total}] Scanning {c_in} -> {c_out} ({seg['segment_type']}, {nights}n, lead={lead}d)...", end="", flush=True)

            comps = await collector.fetch_comps_for_dates(
                check_in=c_in,
                check_out=c_out,
                nights=nights,
                tier="tier_a",
            )

            comp_rates = [c["effective_nightly"] for c in comps]

            # If fewer than 4 comps on Tier A for specific midweeks, supplement with Tier B
            if len(comp_rates) < 4:
                comps_b = await collector.fetch_comps_for_dates(
                    check_in=c_in,
                    check_out=c_out,
                    nights=nights,
                    tier="tier_b",
                )
                comps.extend(comps_b)
                comp_rates.extend([c["effective_nightly"] for c in comps_b])

            evaluated = analytics.evaluate_segment(seg, comp_rates, comp_metadata=comps)
            evaluated_results.append(evaluated)

            status_icon = "🚨" if evaluated["priority_tier"] == "URGENT_ACTION" else ("⚠️" if evaluated["priority_tier"] == "MODERATE_ADJUSTMENT" else "✅")
            print(f" {status_icon} Found {evaluated['comps_count']} comps | P50=${evaluated['comp_p50_eff']:.0f} | Target=${evaluated['comp_target_eff']:.0f} | Diff={evaluated['price_diff_percent']:+.1f}% | Rec Base=${evaluated['recommended_base_nightly']:.0f}")

        await collector.close_browser()

    # 4. Reporting
    print("\n[Step 4/5] Generating multi-format advisory reports...")
    reporter = PriceReportGenerator(output_dir="data")
    outputs = reporter.generate_all(
        evaluated_segments=evaluated_results,
        property_name=config["property"]["name"],
    )

    # 5. HTML Dashboard Generation (docs/index.html)
    print("\n[Step 5/5] Generating interactive static HTML dashboard in docs/...")
    from src.html_generator import HTMLDashboardGenerator
    import shutil
    html_gen = HTMLDashboardGenerator(output_path="docs/index.html")
    html_file = html_gen.generate(evaluated_segments=evaluated_results)
    shutil.copy("data/latest_sheet.csv", "docs/latest_sheet.csv")
    shutil.copy("data/latest_report.md", "docs/latest_report.md")

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

    subparsers.add_parser("test-kivoya", help="Verify Kivoya API connectivity and rates")

    subparsers.add_parser("generate-html", help="Re-generate docs/index.html from existing data")

    bootstrap_parser = subparsers.add_parser("bootstrap-comps", help="Bootstrap and curate comp registry")
    bootstrap_parser.add_argument("--limit", type=int, default=40, help="Max listings per tier")

    args = parser.parse_args()

    if args.command == "run":
        if args.quick:
            asyncio.run(run_weekly_advisory(quick=True, max_segments=args.limit))
        else:
            asyncio.run(run_weekly_advisory(quick=False))
    elif args.command == "generate-html":
        from src.html_generator import HTMLDashboardGenerator
        import shutil
        html_gen = HTMLDashboardGenerator(output_path="docs/index.html")
        out = html_gen.generate()
        if Path("data/latest_sheet.csv").exists():
            shutil.copy("data/latest_sheet.csv", "docs/latest_sheet.csv")
        if Path("data/latest_report.md").exists():
            shutil.copy("data/latest_report.md", "docs/latest_report.md")
        print(f"✅ Dashboard generated successfully at: {out}")
    elif args.command == "bootstrap-comps":
        from src.comp_curator import CompCurator
        curator = CompCurator()
        asyncio.run(curator.bootstrap_market(limit_per_tier=args.limit))
    elif args.command == "test-kivoya":
        test_kivoya_only()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
