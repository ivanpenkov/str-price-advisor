"""
Comp Manager Module for STR Price Advisor.

Provides complete lifecycle management for competitor properties:
1. add_comp: Scrapes listing details via NordVPN proxy, scores quality with CompEvaluator,
   registers in config/comps_registry.json & config/listing_specs.json, and regenerates dashboard.
2. remove_comp: Unregisters listing from registries, purges single-comp cache, and regenerates dashboard.
3. scrape_comp_interval_prices: Scrapes live checkout prices across all unbooked calendar intervals
   using mandatory NordVPN proxy, caching results in data/cache/ for immediate dashboard display.
"""

import asyncio
import json
import logging
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from playwright.async_api import async_playwright

from src.proxy_manager import ProxyManager
from src.listing_enricher import ListingEnricher
from src.comp_evaluator import CompEvaluator
from src.segmentation import CalendarSegmenter

logger = logging.getLogger("comp_manager")


def extract_listing_id(identifier: str) -> str:
    """Extract numeric Airbnb listing ID from URL or raw string."""
    if not identifier:
        raise ValueError("Listing identifier cannot be empty")
    
    clean_id = str(identifier).strip()
    match = re.search(r"rooms(?:/|\=|\?id=)(\d+)", clean_id)
    if match:
        return match.group(1)
    
    digits = re.search(r"^\d+$", clean_id)
    if digits:
        return digits.group(0)
        
    num_match = re.search(r"(\d{6,})", clean_id)
    if num_match:
        return num_match.group(1)

    raise ValueError(f"Could not extract a valid Airbnb listing ID from: {identifier}")


class CompManager:
    """Manages adding, removing, and interval pricing extraction for competitor listings."""

    REGISTRY_PATH = Path("config/comps_registry.json")
    SPECS_PATH = Path("config/listing_specs.json")
    CACHE_DIR = Path("data/cache")
    ENRICHED_DIR = Path("data/enriched_comps")

    def __init__(self):
        self.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self.ENRICHED_DIR.mkdir(parents=True, exist_ok=True)
        self.evaluator = CompEvaluator()

    def _load_registry(self) -> Dict[str, Any]:
        if not self.REGISTRY_PATH.exists():
            return {"metadata": {}, "tier_a": {}, "tier_b": {}, "disqualified": {}}
        try:
            return json.loads(self.REGISTRY_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"Error reading {self.REGISTRY_PATH}: {e}")
            return {"metadata": {}, "tier_a": {}, "tier_b": {}, "disqualified": {}}

    def _save_registry(self, data: Dict[str, Any]):
        self.REGISTRY_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def _load_specs(self) -> Dict[str, Dict[str, Any]]:
        if not self.SPECS_PATH.exists():
            return {}
        try:
            return json.loads(self.SPECS_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_specs(self, data: Dict[str, Dict[str, Any]]):
        self.SPECS_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def find_comp(self, listing_id: str) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        """Find comp in registry, returning (tier, comp_dict) or (None, None)."""
        registry = self._load_registry()
        for tier in ("tier_a", "tier_b", "disqualified"):
            if listing_id in registry.get(tier, {}):
                return tier, registry[tier][listing_id]
        return None, None

    async def add_comp(
        self,
        identifier: str,
        tier: Optional[str] = None,
        scrape_prices: bool = False,
        limit_intervals: Optional[int] = None,
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        """
        Deep-scrape and evaluate a new competitor listing, registering it into comps catalog.
        """
        listing_id = extract_listing_id(identifier)
        print(f"\n🚀 [CompManager] Initiating addition of comp ID: {listing_id}")

        existing_tier, existing_comp = self.find_comp(listing_id)
        if existing_tier and not force_refresh:
            print(f"ℹ️ Comp {listing_id} is already in {existing_tier}: \"{existing_comp.get('name')}\"")
            if scrape_prices:
                print(f"🔄 Proceeding with price scraping for existing comp {listing_id}...")
                await self.scrape_comp_interval_prices(listing_id, limit=limit_intervals)
            return existing_comp

        # 1. Deep scrape listing profile using NordVPN proxy
        print(f"📡 Deep-scraping listing profile for {listing_id} via NordVPN proxy...")
        enricher = ListingEnricher(headless=True)
        async with async_playwright() as p:
            await enricher.init_browser(p)
            page = await enricher.context.new_page()
            try:
                enriched = await enricher.enrich_listing(page, listing_id, force_refresh=force_refresh)
            finally:
                await page.close()
                await enricher.close_browser()

        # 2. Evaluate comp against Villa del Sol
        print("🎯 Evaluating comp with 5-factor luxury rubric against Villa del Sol...")
        raw_meta = {
            "listing_id": listing_id,
            "name": enriched.get("title", f"Luxury Estate {listing_id}"),
            "location": enriched.get("address", {}).get("addressLocality", "Scottsdale"),
            "bedrooms": enriched.get("bedrooms", 6),
            "beds": enriched.get("beds", 10),
            "baths": enriched.get("baths", 4.0),
            "rating": enriched.get("rating", 4.9),
            "reviews": enriched.get("reviews", 10),
            "url": f"https://www.airbnb.com/rooms/{listing_id}",
            "photo_url": enriched.get("photo_url"),
            "amenities_count": enriched.get("amenities_count", 0),
        }

        evaluation = self.evaluator.evaluate_comp(raw_meta, enriched_data=enriched)
        comp_record = {**raw_meta, **evaluation}
        comp_record["discovered_at_sample"] = f"{date.today().isoformat()} manual addition"

        # Determine target tier
        br = comp_record.get("bedrooms") or 5
        guests = 14
        if enriched.get("guests"):
            try:
                guests = int(str(enriched["guests"]).replace("+", ""))
            except Exception:
                guests = 14

        comp_record["accommodates"] = guests

        if not comp_record.get("is_valid_comp"):
            target_tier = "disqualified"
        elif tier:
            target_tier = tier.lower()
        else:
            if br >= 6 and guests >= 14:
                target_tier = "tier_a"
            else:
                target_tier = "tier_b"

        # 3. Register in config/comps_registry.json
        registry = self._load_registry()
        # Remove from any old tier
        for t in ("tier_a", "tier_b", "disqualified"):
            registry.get(t, {}).pop(listing_id, None)

        if target_tier not in registry:
            registry[target_tier] = {}
        registry[target_tier][listing_id] = comp_record

        # Update metadata counts
        registry.setdefault("metadata", {})
        registry["metadata"]["last_updated"] = datetime.now().isoformat()
        registry["metadata"]["total_comps"] = len(registry.get("tier_a", {})) + len(registry.get("tier_b", {}))
        self._save_registry(registry)
        print(f"✅ Added {listing_id} to {target_tier} in {self.REGISTRY_PATH} (Desirability: {comp_record.get('desirability_ratio')}x)")

        # 4. Synchronize config/listing_specs.json
        specs = self._load_specs()
        specs[listing_id] = {
            "listing_id": listing_id,
            "title": comp_record.get("name"),
            "location": comp_record.get("location"),
            "bedrooms": comp_record.get("bedrooms"),
            "beds": comp_record.get("beds"),
            "baths": comp_record.get("baths"),
            "rating": comp_record.get("rating"),
            "reviews": comp_record.get("reviews"),
            "photo_url": comp_record.get("photo_url"),
        }
        self._save_specs(specs)
        print(f"✅ Updated listing specifications in {self.SPECS_PATH}")

        # 5. Optionally scrape interval prices
        if scrape_prices:
            print(f"\n📊 Scraping interval checkout pricing for new comp {listing_id}...")
            await self.scrape_comp_interval_prices(listing_id, limit=limit_intervals)

        # 6. Regenerate HTML dashboard
        self._regenerate_dashboard()

        return comp_record

    def remove_comp(self, identifier: str) -> bool:
        """
        Unregister a competitor listing, purge single-comp cache, and update dashboard.
        """
        listing_id = extract_listing_id(identifier)
        print(f"\n🗑️ [CompManager] Initiating removal of comp ID: {listing_id}")

        registry = self._load_registry()
        found_tier = None
        for t in ("tier_a", "tier_b", "disqualified"):
            if listing_id in registry.get(t, {}):
                found_tier = t
                del registry[t][listing_id]
                break

        if not found_tier:
            print(f"⚠️ Listing ID {listing_id} was not found in comps registry.")
            return False

        # Save registry
        registry.setdefault("metadata", {})
        registry["metadata"]["last_updated"] = datetime.now().isoformat()
        registry["metadata"]["total_comps"] = len(registry.get("tier_a", {})) + len(registry.get("tier_b", {}))
        self._save_registry(registry)
        print(f"  ✓ Removed {listing_id} from {found_tier} in {self.REGISTRY_PATH}")

        # Remove from listing_specs.json
        specs = self._load_specs()
        if listing_id in specs:
            del specs[listing_id]
            self._save_specs(specs)
            print(f"  ✓ Removed {listing_id} from {self.SPECS_PATH}")

        # Purge single-comp cache files
        purged = 0
        for cache_file in self.CACHE_DIR.glob(f"search_*_comp_{listing_id}.json"):
            try:
                cache_file.unlink()
                purged += 1
            except Exception:
                pass
        if purged > 0:
            print(f"  ✓ Purged {purged} single-comp cached pricing files in {self.CACHE_DIR}")

        # Purge enriched profile
        enriched_file = self.ENRICHED_DIR / f"{listing_id}.json"
        if enriched_file.exists():
            try:
                enriched_file.unlink()
                print(f"  ✓ Removed profile cache {enriched_file}")
            except Exception:
                pass

        # Regenerate dashboard
        self._regenerate_dashboard()
        print(f"✨ Comp {listing_id} completely removed and dashboard refreshed.")
        return True

    async def scrape_comp_interval_prices(
        self,
        identifier: str,
        limit: Optional[int] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Scrape checkout pricing for this specific comp across all unbooked intervals
        using the mandatory NordVPN proxy.
        """
        listing_id = extract_listing_id(identifier)
        tier, comp_meta = self.find_comp(listing_id)
        if not comp_meta:
            specs = self._load_specs()
            if listing_id in specs:
                comp_meta = specs[listing_id]
            else:
                raise ValueError(f"Comp {listing_id} must be added to registry before price scraping.")

        print(f"\n🔍 [CompManager] Scraping interval rates for Comp {listing_id}: \"{comp_meta.get('name') or comp_meta.get('title')}\"")

        # 1. Fetch unbooked stay intervals from Kivoya
        segmenter = CalendarSegmenter()
        segments = segmenter.generate_unbooked_segments()

        if start_date:
            segments = [s for s in segments if s["check_in"] >= start_date]
        if end_date:
            segments = [s for s in segments if s["check_out"] <= end_date]
        if limit:
            segments = segments[:limit]

        print(f"  📅 Target intervals to scan: {len(segments)}")

        results: List[Dict[str, Any]] = []
        proxy_mgr = ProxyManager(required=True)
        proxy_cfg = await proxy_mgr.start()

        launch_kwargs = {
            "headless": True,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        }
        if proxy_cfg:
            launch_kwargs["proxy"] = proxy_cfg

        accommodates = comp_meta.get("accommodates") or comp_meta.get("beds") or 14

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

            total_segs = len(segments)
            for idx, seg in enumerate(segments, 1):
                c_in = seg["check_in"]
                c_out = seg["check_out"]
                nights = seg["nights"]

                print(f"  [{idx}/{total_segs}] Checking {c_in} -> {c_out} ({nights}n)...", end="", flush=True)

                intercepted_price: Optional[float] = None
                intercepted_label: Optional[str] = None
                is_unavailable: bool = False

                done_event = asyncio.Event()

                async def on_response(resp):
                    nonlocal intercepted_price, intercepted_label, is_unavailable
                    if "StaysPdpSections" in resp.url:
                        try:
                            body = await resp.text()
                            data = json.loads(body)
                            sections = (
                                data.get("data", {})
                                .get("presentation", {})
                                .get("stayProductDetailPage", {})
                                .get("sections", {})
                                .get("sections", [])
                            )
                            for s in sections:
                                sec = s.get("section", {})
                                sdp = sec.get("structuredDisplayPrice")
                                if sdp and not intercepted_price:
                                    primary = sdp.get("primaryLine", {})
                                    raw_p = primary.get("price", "")
                                    clean_p = re.sub(r"[^\d.]", "", raw_p)
                                    if clean_p:
                                        try:
                                            intercepted_price = float(clean_p)
                                            intercepted_label = primary.get("accessibilityLabel") or raw_p
                                        except Exception:
                                            pass
                                
                                # Check unavailability flag
                                log_event = sec.get("tripDetailsLoggingEventData", {})
                                if log_event and "selectUnavailable" in str(log_event):
                                    is_unavailable = True
                            done_event.set()
                        except Exception:
                            pass

                page.on("response", on_response)

                url = f"https://www.airbnb.com/rooms/{listing_id}?check_in={c_in}&check_out={c_out}&adults={accommodates}"
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=25000)
                    await page.evaluate("() => window.scrollTo(0, 1500)")
                    try:
                        await asyncio.wait_for(done_event.wait(), timeout=6.0)
                    except asyncio.TimeoutError:
                        pass
                except Exception as e:
                    logger.warning(f"Timeout/error loading {url}: {e}")
                finally:
                    page.remove_listener("response", on_response)

                cache_file = self.CACHE_DIR / f"search_{c_in}_{c_out}_comp_{listing_id}.json"

                if intercepted_price and intercepted_price > 0.0:
                    eff_nightly = round(intercepted_price / max(1, nights), 2)
                    status = "AVAILABLE"
                    item = {
                        "listing_id": str(listing_id),
                        "title": comp_meta.get("name") or comp_meta.get("title", f"Comp {listing_id}"),
                        "location": comp_meta.get("location", "Scottsdale"),
                        "bedrooms": comp_meta.get("bedrooms", 6),
                        "beds": comp_meta.get("beds", 10),
                        "baths": comp_meta.get("baths", 4.0),
                        "nights": nights,
                        "total_price": intercepted_price,
                        "effective_nightly": eff_nightly,
                        "rating": comp_meta.get("rating"),
                        "reviews": comp_meta.get("reviews"),
                        "confidence": "CONFIRMED",
                        "confidence_reason": "Direct single-comp checkout pricing via Airbnb API",
                        "price_snippet": f"${intercepted_price:,.0f} for {nights} nights | ${eff_nightly:,.0f}/night",
                        "raw_snippet": f"Single Comp Sweep | {comp_meta.get('name')} | {intercepted_label or f'${intercepted_price}'}",
                        "photo_url": comp_meta.get("photo_url"),
                    }
                    cache_file.write_text(json.dumps([item], indent=2, ensure_ascii=False), encoding="utf-8")
                    print(f" ✅ ${eff_nightly:,.0f}/night (Total ${intercepted_price:,.0f})")
                    results.append({"interval": f"{c_in}_{c_out}", "status": status, "rate": eff_nightly, "total": intercepted_price})
                else:
                    status = "BOOKED / BLOCKED"
                    print(" ⛔ BOOKED / UNAVAILABLE")
                    # If previously had cached available rate for this date, keep it or remove it?
                    # Removing stale available rate if it is now confirmed booked
                    if cache_file.exists():
                        try:
                            cache_file.unlink()
                        except Exception:
                            pass
                    results.append({"interval": f"{c_in}_{c_out}", "status": status, "rate": None, "total": None})

                # Polite delay between intervals
                await asyncio.sleep(1.5)

            await browser.close()

        await proxy_mgr.stop()

        # Regenerate HTML dashboard so new prices appear immediately
        self._regenerate_dashboard()

        return results

    def _regenerate_dashboard(self):
        """Regenerate docs/index.html with updated comp and pricing data, updating all reports."""
        try:
            import shutil
            from src.html_generator import HTMLDashboardGenerator
            from src.reporter import PriceReportGenerator

            html_gen = HTMLDashboardGenerator(output_path="docs/index.html")
            evaluated_segments = html_gen.generate_full_12_month_evaluation()
            html_gen.generate(evaluated_segments)

            reporter = PriceReportGenerator(output_dir="data")
            reporter.generate_all(evaluated_segments=evaluated_segments, property_name="Villa del Sol")

            if Path("data/latest_sheet.csv").exists():
                shutil.copy("data/latest_sheet.csv", "docs/latest_sheet.csv")
            if Path("data/latest_report.md").exists():
                shutil.copy("data/latest_report.md", "docs/latest_report.md")

            print("🎨 Regenerated interactive dashboard (docs/index.html) and advisory reports (data/ & docs/)")
        except Exception as e:
            logger.warning(f"Could not regenerate HTML dashboard: {e}")

