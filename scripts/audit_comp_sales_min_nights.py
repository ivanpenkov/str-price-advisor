#!/usr/bin/env python3
"""
Audit Competitor Sales via 4-Night Minimum Stay Protocol.

Tests each recorded future competitor sale by extending the stay window:
1. Check 1: 1 day before, 4-night reservation.
2. Check 2: 1 day after, 4-night reservation.

Identifies false sales (properties that are actually available but require
a 4-night minimum) versus real confirmed sales (truly blocked/booked dates).
Uses NordVPN out-of-state proxy pool via StealthConnectionManager.
"""

import argparse
import asyncio
from datetime import date, datetime, timedelta
import json
import logging
import os
from pathlib import Path
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.database import get_db_connection
from src.stealth_connection import StealthConnectionManager
from src.comp_manager import CompManager
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("audit_sales")

RESULTS_FILE = REPO_ROOT / "data" / "comp_sales_verification_results.json"


def compute_test_intervals(check_in_str: str, check_out_str: str) -> Tuple[Tuple[str, str], Tuple[str, str]]:
    """
    Compute Check 1 (extended before) and Check 2 (extended after) test intervals
    for a stay with nights < 4.

    Both test intervals strictly contain all original stay nights:
    delta_days = max(1, 4 - nights)
    Check 1: cin - delta_days to cout (at least 4 nights).
    Check 2: cin to cout + delta_days (at least 4 nights).
    """
    cin = datetime.strptime(check_in_str, "%Y-%m-%d").date()
    cout = datetime.strptime(check_out_str, "%Y-%m-%d").date()
    nights = (cout - cin).days
    delta_days = max(1, 4 - nights)

    # Check 1: delta_days before, ending at cout
    cin_1 = cin - timedelta(days=delta_days)
    cout_1 = cout

    # Check 2: starting at cin, ending at cout + delta_days
    cin_2 = cin
    cout_2 = cout + timedelta(days=delta_days)

    return (cin_1.isoformat(), cout_1.isoformat()), (cin_2.isoformat(), cout_2.isoformat())


async def check_single_interval(
    page: Page,
    listing_id: str,
    check_in: str,
    check_out: str,
    adults: int = 1,
    pdp_timeout: float = 4.0,
) -> Dict[str, Any]:
    """Check a single interval on Airbnb via Playwright."""
    result = {
        "check_in": check_in,
        "check_out": check_out,
        "nights": (datetime.strptime(check_out, "%Y-%m-%d").date() - datetime.strptime(check_in, "%Y-%m-%d").date()).days,
        "available": False,
        "unavail": False,
        "price": None,
        "price_label": None,
        "reason": None,
    }

    pdp_event = asyncio.Event()

    async def on_response(resp):
        if "StaysPdpSections" in resp.url:
            try:
                body = await resp.text()
                data = json.loads(body)
                pr, label, unavail, reason = CompManager.parse_stays_pdp_sections(data)
                if pr and not result["price"]:
                    result["price"] = pr
                    result["price_label"] = label
                    result["available"] = True
                    result["unavail"] = False
                if unavail:
                    result["unavail"] = True
                    result["available"] = False
                    if reason:
                        result["reason"] = reason
                if pr or unavail:
                    pdp_event.set()
            except Exception:
                pass

    page.on("response", on_response)
    url = f"https://www.airbnb.com/rooms/{listing_id}?check_in={check_in}&check_out={check_out}&adults={adults}&locale=en&currency=USD"

    try:
        for attempt in range(2):
            pdp_event.clear()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=25000)
                try:
                    await asyncio.wait_for(pdp_event.wait(), timeout=pdp_timeout)
                except asyncio.TimeoutError:
                    pass

                # Fallback to DOM evaluation if StaysPdpSections did not definitively resolve
                if not result["price"] and not result["unavail"]:
                    try:
                        body_text = await page.evaluate("() => document.body.innerText")
                    except Exception:
                        body_text = ""
                    lower = (body_text or "").lower()

                    if any(bp in lower for bp in ["verify you are human", "press and hold", "access denied", "robot or human"]):
                        result["reason"] = "Bot challenge detected"
                    elif any(up in lower for up in [
                        "those dates are not available", "dates are not available", "dates aren't available",
                        "these dates are unavailable", "unavailable for these dates",
                    ]):
                        result["unavail"] = True
                        result["available"] = False
                        result["reason"] = "Those dates are not available"
                    elif "minimum stay" in lower:
                        # Capture minimum stay notice
                        m_min = re.search(r"(\d+)\s+nights?\s+minimum", lower)
                        min_nights_val = m_min.group(1) if m_min else "stricter"
                        result["unavail"] = True
                        result["available"] = False
                        result["reason"] = f"Requires {min_nights_val}-night minimum stay"
                    else:
                        m_price = re.search(r"\$([0-9,]+(?:\.[0-9]{2})?)\s*(?:total|for \d+ nights|before taxes)", body_text, re.IGNORECASE)
                        if m_price:
                            result["price"] = float(m_price.group(1).replace(",", ""))
                            result["available"] = True
                            result["unavail"] = False
                            result["reason"] = "Available (DOM price)"
                        else:
                            current_url = getattr(page, "url", "")
                            if f"check_in={check_in}" not in current_url:
                                result["unavail"] = True
                                result["available"] = False
                                result["reason"] = "SPA date reset (dates unavailable or min stay violated)"
                            else:
                                result["reason"] = "Inconclusive response"
                break
            except Exception as e:
                if attempt == 0:
                    await asyncio.sleep(1.5)
                    continue
                result["reason"] = f"Error: {e}"
    finally:
        page.remove_listener("response", on_response)

    return result


async def verify_comp_sale(
    worker_mgr: StealthConnectionManager,
    sale: Dict[str, Any],
) -> Dict[str, Any]:
    """Verify a single recorded competitor sale using the 4-night extension protocol."""
    sale_id = sale["id"]
    listing_id = sale["listing_id"]
    listing_name = sale.get("listing_name") or f"Listing #{listing_id}"
    check_in = sale["check_in"]
    check_out = sale["check_out"]
    nights = sale.get("nights") or (datetime.strptime(check_out, "%Y-%m-%d").date() - datetime.strptime(check_in, "%Y-%m-%d").date()).days

    verif_res = {
        "id": sale_id,
        "listing_id": listing_id,
        "listing_name": listing_name,
        "original_check_in": check_in,
        "original_check_out": check_out,
        "original_nights": nights,
        "detected_date": sale.get("detected_date"),
        "check1_interval": "",
        "check1_result": None,
        "check2_interval": "",
        "check2_result": None,
        "verdict": "UNKNOWN",
        "verdict_reason": "",
        "realized_rate": sale.get("last_observed_rate"),
    }

    # Stays with nights >= 4 already meet a 4-night minimum requirement.
    # Unavailability on a 4-night search could not be caused by a 4-night minimum.
    if nights >= 4:
        verif_res["verdict"] = "CONFIRMED_REAL"
        verif_res["verdict_reason"] = (
            f"Original stay ({check_in} -> {check_out}) is {nights} nights, which already satisfies "
            f"a 4-night minimum requirement. Recorded sale is confirmed real."
        )
        return verif_res

    today = date.today()
    (cin_1, cout_1), (cin_2, cout_2) = compute_test_intervals(check_in, check_out)
    cin_1_d = datetime.strptime(cin_1, "%Y-%m-%d").date()
    cout_1_d = datetime.strptime(cout_1, "%Y-%m-%d").date()
    cin_2_d = datetime.strptime(cin_2, "%Y-%m-%d").date()
    cout_2_d = datetime.strptime(cout_2, "%Y-%m-%d").date()

    verif_res["check1_interval"] = f"{cin_1} -> {cout_1}"
    verif_res["check2_interval"] = f"{cin_2} -> {cout_2}"

    async with worker_mgr.lease_worker() as worker:
        proxy_cfg = {"server": worker.url}
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                proxy=proxy_cfg,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
            try:
                context = await browser.new_context(
                    viewport={"width": 1366, "height": 850},
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                )

                # Test Check 1: Extended before (run only if check-in is not in the past)
                if cin_1_d >= today:
                    page1 = await context.new_page()
                    try:
                        res1 = await check_single_interval(page1, listing_id, cin_1, cout_1, adults=1)
                        verif_res["check1_result"] = res1
                    finally:
                        await page1.close()

                    # If Check 1 is available, property is definitely available!
                    if res1.get("available") and res1.get("price"):
                        n1 = (cout_1_d - cin_1_d).days
                        verif_res["verdict"] = "FALSE_SALE"
                        verif_res["verdict_reason"] = (
                            f"Check 1 ({cin_1} -> {cout_1}, {n1}n) is AVAILABLE on Airbnb for ${res1['price']:,.0f} "
                            f"(${res1['price']/n1:,.0f}/nt). Unavailability on {check_in}->{check_out} was due to 4-night minimum."
                        )
                        return verif_res
                else:
                    logger.info(f"Check 1 ({cin_1}) is in the past; skipping Check 1 to avoid false calendar rejection.")
                    verif_res["check1_result"] = {
                        "check_in": cin_1,
                        "check_out": cout_1,
                        "skipped": True,
                        "unavail": False,
                        "available": False,
                        "reason": f"Check-in date ({cin_1}) is in the past",
                    }

                # Test Check 2: Extended after (fresh page instance to eliminate SPA cache contamination)
                page2 = await context.new_page()
                try:
                    res2 = await check_single_interval(page2, listing_id, cin_2, cout_2, adults=1)
                    verif_res["check2_result"] = res2
                finally:
                    await page2.close()
            finally:
                await browser.close()

    # Determine verdict from Check 1 and Check 2
    res1 = verif_res["check1_result"] or {}
    res2 = verif_res["check2_result"] or {}

    if res2.get("available") and res2.get("price"):
        n2 = (cout_2_d - cin_2_d).days
        verif_res["verdict"] = "FALSE_SALE"
        verif_res["verdict_reason"] = (
            f"Check 2 ({cin_2} -> {cout_2}, {n2}n) is AVAILABLE on Airbnb for ${res2['price']:,.0f} "
            f"(${res2['price']/n2:,.0f}/nt). Unavailability on {check_in}->{check_out} was due to 4-night minimum."
        )
    elif "minimum" in (res1.get("reason") or "").lower() or "minimum" in (res2.get("reason") or "").lower():
        reason_str = res1.get("reason") if "minimum" in (res1.get("reason") or "").lower() else res2.get("reason")
        verif_res["verdict"] = "STRICTER_MIN_STAY"
        verif_res["verdict_reason"] = f"Listing enforces stricter minimum stay rule: {reason_str}."
    elif (res1.get("unavail") or res1.get("skipped")) and res2.get("unavail"):
        verif_res["verdict"] = "CONFIRMED_REAL"
        verif_res["verdict_reason"] = (
            f"4-night test interval(s) unavailable on Airbnb "
            f"({res1.get('reason', 'Blocked')} / {res2.get('reason', 'Blocked')}). Calendar dates are genuinely booked."
        )
    else:
        verif_res["verdict"] = "INCONCLUSIVE"
        verif_res["verdict_reason"] = f"Check 1: {res1.get('reason')}; Check 2: {res2.get('reason')}"

    return verif_res


def load_recorded_sales(future_only: bool = True) -> List[Dict[str, Any]]:
    """Load recorded sales from the database."""
    today_iso = date.today().isoformat()
    conn = get_db_connection()
    try:
        c = conn.cursor()
        if future_only:
            c.execute("""
                SELECT id, listing_id, listing_name, check_in, check_out, nights, 
                       detected_date, lead_time_days, last_observed_rate, verification_status
                FROM competitor_sales
                WHERE check_in >= ?
                ORDER BY check_in ASC, listing_id ASC
            """, (today_iso,))
        else:
            c.execute("""
                SELECT id, listing_id, listing_name, check_in, check_out, nights, 
                       detected_date, lead_time_days, last_observed_rate, verification_status
                FROM competitor_sales
                ORDER BY check_in ASC, listing_id ASC
            """)
        return [dict(r) for r in c.fetchall()]
    finally:
        if hasattr(conn, "close"):
            conn.close()


def purge_sales_from_db(sale_ids: List[int]):
    """Purge verified false sales from Turso Cloud and local reservations.db."""
    if not sale_ids:
        return
    placeholders = ",".join("?" for _ in sale_ids)
    conn = get_db_connection()
    try:
        c = conn.cursor()
        c.execute(f"DELETE FROM competitor_sales WHERE id IN ({placeholders})", sale_ids)
        c.execute("SELECT COUNT(*) FROM competitor_sales")
        remaining = c.fetchone()[0]
        conn.commit()
    finally:
        if hasattr(conn, "close"):
            conn.close()

    # Also delete from local SQLite if running with Turso Cloud so local replica stays in sync
    local_db = REPO_ROOT / "data" / "reservations.db"
    if local_db.exists():
        try:
            import sqlite3
            with sqlite3.connect(local_db) as l_conn:
                l_c = l_conn.cursor()
                l_c.execute(f"DELETE FROM competitor_sales WHERE id IN ({placeholders})", sale_ids)
                l_conn.commit()
        except Exception as e:
            logger.warning(f"Could not purge from local SQLite: {e}")

    logger.info(f"✓ Purged {len(sale_ids)} false sales from database. Remaining active sales: {remaining}")


async def run_audit(
    workers: int = 4,
    limit: Optional[int] = None,
    dry_run: bool = False,
    resume: bool = True,
    purge: bool = False,
):
    print("=" * 80)
    print("🔍 COMPETITOR SALES 4-NIGHT MINIMUM AUDIT ENGINE")
    print("=" * 80)

    sales = load_recorded_sales(future_only=True)
    if limit:
        sales = sales[:limit]
    total_sales = len(sales)
    print(f"📊 Loaded {total_sales} future competitor sales from database (check_in >= {date.today().isoformat()}).")

    # Load existing results if resuming
    existing_results: Dict[int, Dict[str, Any]] = {}
    if resume and RESULTS_FILE.exists():
        try:
            data = json.loads(RESULTS_FILE.read_text())
            for item in data:
                existing_results[item["id"]] = item
            print(f"📂 Loaded {len(existing_results)} previously verified results from {RESULTS_FILE.name}.")
        except Exception as e:
            logger.warning(f"Could not parse existing results file: {e}")

    # Determine remaining to check (retry any previously inconclusive items)
    resolved_existing = {k: v for k, v in existing_results.items() if v.get("verdict") != "INCONCLUSIVE"}
    remaining_sales = [s for s in sales if s["id"] not in resolved_existing]
    print(f"🎯 Remaining sales to verify live on Airbnb: {len(remaining_sales)} "
          f"({len(sales) - len(remaining_sales)} already resolved, {len(existing_results) - len(resolved_existing)} inconclusive to retry).")

    results_map: Dict[int, Dict[str, Any]] = dict(resolved_existing)

    if remaining_sales:
        mgr = StealthConnectionManager(required=True, max_workers=workers)
        print(f"\n🛡️ Starting NordVPN Stealth Proxy Pool ({workers} workers across US travel feeder hubs)...")
        pool = await mgr.start_pool(num_workers=workers, min_healthy=min(3, workers), wait_for_full_pool=False, max_wait_seconds=25)
        print(f"  ✓ NordVPN Stealth Pool Online ({len(pool)} verified US proxies ready).")

        semaphore = asyncio.Semaphore(workers)

        async def worker_task(sale_item: Dict[str, Any], idx: int):
            async with semaphore:
                try:
                    print(f"  [{idx+1}/{len(remaining_sales)}] Verifying Comp Sale #{sale_item['id']} "
                          f"({sale_item.get('listing_name', '')[:30]} | {sale_item['check_in']} -> {sale_item['check_out']})...")
                    res = await verify_comp_sale(mgr, sale_item)
                except Exception as e:
                    logger.warning(f"Verification error on sale #{sale_item['id']}: {e}")
                    res = {
                        "id": sale_item["id"],
                        "listing_id": sale_item["listing_id"],
                        "listing_name": sale_item.get("listing_name"),
                        "original_check_in": sale_item["check_in"],
                        "original_check_out": sale_item["check_out"],
                        "original_nights": sale_item.get("nights", 3),
                        "verdict": "INCONCLUSIVE",
                        "verdict_reason": f"Execution error: {e}",
                    }
                verdict_icon = "❌ FALSE SALE" if res["verdict"] == "FALSE_SALE" else ("✅ REAL" if res["verdict"] == "CONFIRMED_REAL" else "⚠️ " + res["verdict"])
                print(f"    ↳ Verdict: {verdict_icon} | {res['verdict_reason'][:75]}...")
                results_map[res["id"]] = res
                # Save checkpoint periodically
                try:
                    RESULTS_FILE.write_text(json.dumps(list(results_map.values()), indent=2))
                except Exception:
                    pass

        try:
            tasks = [worker_task(s, i) for i, s in enumerate(remaining_sales)]
            await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            await mgr.stop_pool()
            print("\n🛡️ NordVPN Stealth Pool stopped cleanly.")

    results_list = list(results_map.values())

    # Save final results
    RESULTS_FILE.write_text(json.dumps(results_list, indent=2))
    print(f"\n💾 Saved all {len(results_list)} verification records to {RESULTS_FILE.relative_to(REPO_ROOT)}.")

    # Summary analysis
    confirmed_real = [r for r in results_list if r["verdict"] == "CONFIRMED_REAL"]
    false_sales = [r for r in results_list if r["verdict"] == "FALSE_SALE"]
    stricter_min = [r for r in results_list if r["verdict"] == "STRICTER_MIN_STAY"]
    inconclusive = [r for r in results_list if r["verdict"] not in ("CONFIRMED_REAL", "FALSE_SALE", "STRICTER_MIN_STAY")]

    print("\n" + "=" * 80)
    print("📈 AUDIT SUMMARY BREAKDOWN")
    print("=" * 80)
    print(f"Total Sales Evaluated:              {len(results_list)}")
    print(f"  ✅ Confirmed Real Sales (Booked):  {len(confirmed_real)} ({len(confirmed_real)/len(results_list)*100:.1f}%)")
    print(f"  ❌ False Sales (Available on 4n): {len(false_sales)} ({len(false_sales)/len(results_list)*100:.1f}%)")
    print(f"  ⚠️ Stricter Min Stay (>4 Nights):  {len(stricter_min)} ({len(stricter_min)/len(results_list)*100:.1f}%)")
    print(f"  ❓ Inconclusive / Retries:        {len(inconclusive)}")

    if false_sales:
        print("\n❌ FALSE SALES IDENTIFIED (Unbooked properties with 4-night minimum):")
        print("-" * 110)
        print(f"{'ID':<6} {'Listing Name':<38} {'Dates':<26} {'N':<3} {'Realized':<10} {'Details'}")
        print("-" * 110)
        for fs in false_sales:
            name = (fs.get("listing_name") or f"Listing {fs['listing_id']}")[:36]
            dates = f"{fs['original_check_in']} -> {fs['original_check_out']}"
            n = fs["original_nights"]
            rate = f"${fs.get('realized_rate', 0):,.0f}" if fs.get('realized_rate') else "N/A"
            reason = fs.get("verdict_reason", "")
            print(f"{fs['id']:<6} {name:<38} {dates:<26} {n:<3} {rate:<10} {reason[:40]}...")

    if purge and (false_sales or stricter_min):
        to_purge_ids = [fs["id"] for fs in false_sales + stricter_min]
        print(f"\n🧹 Purging {len(to_purge_ids)} false/stricter-min sales from database...")
        if not dry_run:
            purge_sales_from_db(to_purge_ids)
        else:
            print("  [DRY-RUN] Purge skipped.")

    return results_list


def main():
    parser = argparse.ArgumentParser(description="Audit competitor sales via 4-night minimum stay protocol.")
    parser.add_argument("--workers", type=int, default=4, help="Number of concurrent proxy workers (default: 4)")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of sales to check (for testing)")
    parser.add_argument("--dry-run", action="store_true", help="Do not mutate the database")
    parser.add_argument("--no-resume", action="store_true", help="Do not resume from existing results file")
    parser.add_argument("--purge", action="store_true", help="Purge confirmed false sales from database")
    args = parser.parse_args()

    asyncio.run(run_audit(
        workers=args.workers,
        limit=args.limit,
        dry_run=args.dry_run,
        resume=not args.no_resume,
        purge=args.purge,
    ))


if __name__ == "__main__":
    main()
