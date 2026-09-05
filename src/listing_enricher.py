"""
Deep Listing Enricher.
Extracts comprehensive listing metadata, descriptions, room details, and full amenity
lists for our property and all competitor properties in the registry.
Caches enriched listing profiles under data/enriched_comps/.
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from playwright.async_api import Browser, BrowserContext, Page, async_playwright

logger = logging.getLogger("listing_enricher")


class ListingEnricher:
    """Extracts and caches detailed listing profiles from Airbnb."""

    OUR_AIRBNB_ID = "573857947793833342"
    ENRICHED_DIR = Path("data/enriched_comps")
    OUR_PROFILE_PATH = Path("data/our_property_profile.json")
    REGISTRY_PATH = Path("config/comps_registry.json")
    SPECS_PATH = Path("config/listing_specs.json")

    def __init__(self, headless: bool = True):
        self.headless = headless
        self.ENRICHED_DIR.mkdir(parents=True, exist_ok=True)
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        from src.proxy_manager import ProxyManager
        self.proxy_mgr = ProxyManager(required=True)

    async def init_browser(self, p):
        """Launch headless browser with anti-detection args and proxy support."""
        proxy_cfg = await self.proxy_mgr.start()
        launch_kwargs = {
            "headless": self.headless,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        }
        if proxy_cfg:
            launch_kwargs["proxy"] = proxy_cfg

        self.browser = await p.chromium.launch(**launch_kwargs)
        self.context = await self.browser.new_context(
            viewport={"width": 1366, "height": 850},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            ),
        )

    async def close_browser(self):
        """Close browser resources and terminate proxy bridge."""
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        await self.proxy_mgr.stop()

    def get_cached_profile(self, listing_id: str) -> Optional[Dict[str, Any]]:
        """Return cached enriched listing if present."""
        path = self.ENRICHED_DIR / f"{listing_id}.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return None

    def save_cached_profile(self, listing_id: str, data: Dict[str, Any]):
        """Save enriched listing to disk."""
        path = self.ENRICHED_DIR / f"{listing_id}.json"
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def parse_deferred_state(deferred_text: str) -> Dict[str, Any]:
        """
        Parse Airbnb Apollo / Niobe deferred client state string.
        Extracts bedrooms, beds, baths, guest capacity, and all available amenities.
        """
        amenity_titles: List[str] = []
        if deferred_text:
            try:
                data = json.loads(deferred_text)

                def search_amenities(obj):
                    if isinstance(obj, dict):
                        if obj.get("__typename") == "AmenityItem" and obj.get("available") is True:
                            t = obj.get("title")
                            if t and t not in amenity_titles:
                                amenity_titles.append(t)
                        for v in obj.values():
                            search_amenities(v)
                    elif isinstance(obj, list):
                        for item in obj:
                            search_amenities(item)

                search_amenities(data)
            except Exception as e:
                logger.warning(f"Error parsing deferred state JSON: {e}")

        extracted_bedrooms = None
        extracted_beds = None
        extracted_baths = None
        extracted_guests = None

        if deferred_text:
            # 1. Composite subtitle banner: "6 bedrooms · 14 beds · 4 baths" (handles literal middle dots, unicode \u00b7, bullets, pipes)
            sep = r"(?:\s*·\s*|\\u00b7|\s*•\s*|\\u2022|\s*\|\s*|,|\s+)"
            pattern = rf"(\d+)\s*bedrooms?{sep}+(\d+)\s*beds?\b(?!room){sep}+(\d+(?:\.\d+)?)\s*baths?"
            m_summary = re.search(pattern, deferred_text, re.IGNORECASE)
            if m_summary:
                extracted_bedrooms = int(m_summary.group(1))
                extracted_beds = int(m_summary.group(2))
                extracted_baths = float(m_summary.group(3))

            # 2. Individual fallback tokens with negative lookahead
            if not extracted_bedrooms:
                m_br = re.search(r"(\d+)\s*bedrooms?\b", deferred_text, re.IGNORECASE)
                if m_br:
                    extracted_bedrooms = int(m_br.group(1))
            if not extracted_beds:
                m_bed = re.search(r"(\d+)\s*beds?\b(?!room)", deferred_text, re.IGNORECASE)
                if m_bed:
                    extracted_beds = int(m_bed.group(1))
            if not extracted_baths:
                m_ba = re.search(r"(\d+(?:\.\d+)?)\s*baths?\b", deferred_text, re.IGNORECASE)
                if m_ba:
                    extracted_baths = float(m_ba.group(1))

            m_guests = re.search(r"(\d+\+?)\s*guests?\b", deferred_text, re.IGNORECASE)
            if m_guests:
                extracted_guests = m_guests.group(1)

        return {
            "bedrooms": extracted_bedrooms,
            "beds": extracted_beds,
            "baths": extracted_baths,
            "guests": extracted_guests,
            "amenities": sorted(list(set(amenity_titles))),
            "amenities_count": len(amenity_titles),
        }

    @staticmethod
    def parse_json_ld(ld_data: Dict[str, Any]) -> Dict[str, Any]:
        """Extract name, description, rating, reviews, image, and address from schema.org JSON-LD."""
        title = ld_data.get("name")
        if title and title.startswith("503 Service"):
            title = None

        description = ld_data.get("description", "")
        rating_obj = ld_data.get("aggregateRating", {})
        rating = None
        reviews = None
        if rating_obj:
            try:
                r_val = rating_obj.get("ratingValue")
                if r_val is not None:
                    rating = float(r_val)
                rev_val = rating_obj.get("ratingCount")
                if rev_val is not None:
                    reviews = int(rev_val)
            except Exception:
                pass

        photo_url = ld_data.get("image")
        if isinstance(photo_url, list) and photo_url:
            photo_url = photo_url[0]

        address = ld_data.get("address")
        return {
            "title": title,
            "description": description,
            "rating": rating,
            "reviews": reviews,
            "photo_url": photo_url,
            "address": address,
        }

    @classmethod
    def parse_page_content(
        cls,
        deferred_text: str = "",
        ld_data: Optional[Dict[str, Any]] = None,
        page_title: Optional[str] = None,
        dom_overview: Optional[List[str]] = None,
        dom_description: Optional[str] = None,
        dom_photo: Optional[str] = None,
        listing_id: str = "",
    ) -> Dict[str, Any]:
        """Combine all page sources into a normalized listing profile dictionary."""
        deferred_parsed = cls.parse_deferred_state(deferred_text)
        ld_parsed = cls.parse_json_ld(ld_data or {})

        # Title resolution
        title = ld_parsed.get("title")
        if not title and page_title:
            t = page_title
            if " - Airbnb" in t:
                t = t.split(" - Airbnb")[0].strip()
            if not t.startswith("503 Service"):
                title = t

        description = ld_parsed.get("description") or dom_description or ""
        photo_url = ld_parsed.get("photo_url") or dom_photo

        bedrooms = deferred_parsed.get("bedrooms")
        beds = deferred_parsed.get("beds")
        baths = deferred_parsed.get("baths")
        guests = deferred_parsed.get("guests")

        overview = dom_overview or []
        if not overview:
            overview = []
            if guests:
                overview.append(f"{guests} guests")
            if bedrooms:
                overview.append(f"{bedrooms} bedrooms")
            if beds:
                overview.append(f"{beds} beds")
            if baths:
                overview.append(f"{baths:.1f} baths" if baths % 1 != 0 else f"{int(baths)} baths")

        url = f"https://www.airbnb.com/rooms/{listing_id}" if listing_id else ""

        return {
            "listing_id": listing_id,
            "title": title,
            "description": description,
            "bedrooms": bedrooms,
            "beds": beds,
            "baths": baths,
            "guests": guests,
            "amenities": deferred_parsed.get("amenities", []),
            "amenities_count": deferred_parsed.get("amenities_count", 0),
            "overview": overview[:8],
            "rating": ld_parsed.get("rating"),
            "reviews": ld_parsed.get("reviews"),
            "address": ld_parsed.get("address"),
            "photo_url": photo_url,
            "url": url,
            "enriched_at": datetime.now().isoformat(),
        }

    async def extract_listing_data(self, page: Page, listing_id: str) -> Dict[str, Any]:
        """Navigate to listing page and extract all available metadata, description, and amenities."""
        url = f"https://www.airbnb.com/rooms/{listing_id}"
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)

        # 1. Extract JSON-LD scripts
        scripts = await page.query_selector_all("script[type='application/ld+json']")
        ld_data = {}
        for s in scripts:
            try:
                parsed = json.loads(await s.inner_text())
                if isinstance(parsed, dict) and parsed.get("description"):
                    ld_data = parsed
                    break
            except Exception:
                continue

        # 2. Extract Apollo / Niobe deferred state
        deferred_text = await page.evaluate(
            "() => { const el = document.getElementById('data-deferred-state-0'); return el ? el.innerText : ''; }"
        )

        # 3. DOM Overviews, Title, Description, Meta photo
        dom_overview = await page.evaluate("""() => {
            const items = Array.from(document.querySelectorAll("ol li, div[data-section-id='OVERVIEW_DEFAULT'] li, [data-testid='overview'] li"));
            return items.map(e => e.innerText.trim()).filter(t => t.length > 0 && t.length < 50 && !t.includes('\\n'));
        }""")
        page_title = await page.title()
        dom_desc = await page.evaluate("""() => {
            const el = document.querySelector("[data-section-id='DESCRIPTION_DEFAULT']");
            return el ? el.innerText.trim() : '';
        }""")
        dom_photo = await page.evaluate("""() => {
            const img = document.querySelector("meta[property='og:image']");
            return img ? img.getAttribute("content") : null;
        }""")

        return self.parse_page_content(
            deferred_text=deferred_text or "",
            ld_data=ld_data,
            page_title=page_title,
            dom_overview=dom_overview,
            dom_description=dom_desc,
            dom_photo=dom_photo,
            listing_id=listing_id,
        )

    async def enrich_listing(self, page: Page, listing_id: str, force_refresh: bool = False) -> Dict[str, Any]:
        """Fetch and cache listing profile."""
        if not force_refresh:
            cached = self.get_cached_profile(listing_id)
            if cached and cached.get("amenities_count", 0) > 0:
                return cached

        logger.info(f"Enriching listing {listing_id}...")
        data = await self.extract_listing_data(page, listing_id)
        self.save_cached_profile(listing_id, data)
        return data

    async def enrich_our_property(self, force_refresh: bool = False) -> Dict[str, Any]:
        """Deep scrape and build our home profile for Villa del Sol."""
        async with async_playwright() as p:
            await self.init_browser(p)
            page = await self.context.new_page()
            data = await self.enrich_listing(page, self.OUR_AIRBNB_ID, force_refresh=force_refresh)
            await self.close_browser()

        # Augment with verified ground truth details from settings.yaml
        ground_truth = {
            "property_name": "Villa del Sol",
            "full_address": "920 E Carver Rd, Tempe, AZ 85284",
            "kivoya_unit_id": 503802,
            "airbnb_room_id": self.OUR_AIRBNB_ID,
            "bedrooms": 6,
            "bathrooms": 6.0,
            "max_guests": 16,
            "lot_size": "0.75-acre private gated compound",
            "detached_guest_house": True,
            "headline_features": [
                "Gated 3/4-acre private compound in quiet South Tempe enclave",
                "Massive heated 30,000-gallon saltwater resort pool with rock waterfall grotto",
                "Private heated in-ground spa",
                "Full private basketball half-court and putting green",
                "Detached 1BR/1BA luxury guest house (casita)",
                "Billiards room with championship pool table",
                "Covered outdoor chef kitchen and BBQ pavilion with fire pit",
                "Chef's kitchen with GE stainless steel appliances and 16-person dining",
                "Ultra-fast 433+ Mbps gigabit WiFi and 4K smart TVs throughout",
            ],
            "key_specs": {
                "bedrooms": 6,
                "bathrooms": 6,
                "beds": 11,
                "guests": 16,
                "pool": "Private heated saltwater with grotto",
                "sports": ["Basketball half-court", "Putting green", "Billiards"],
            },
        }
        profile = {**data, **ground_truth}
        self.OUR_PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.OUR_PROFILE_PATH.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info(f"Saved our property profile to {self.OUR_PROFILE_PATH}")
        return profile

    async def enrich_all_comps(self, limit: Optional[int] = None, force_refresh: bool = False, concurrency: int = 4) -> Dict[str, Any]:
        """Iterate through comp registry and enrich all listings concurrently with full descriptions and amenities."""
        if not self.REGISTRY_PATH.exists():
            raise FileNotFoundError(f"Registry not found at {self.REGISTRY_PATH}")

        registry = json.loads(self.REGISTRY_PATH.read_text(encoding="utf-8"))
        all_comps: List[Dict[str, Any]] = []
        for tier_key in ("tier_a", "tier_b"):
            for cid, comp in registry.get(tier_key, {}).items():
                all_comps.append(comp)

        if limit:
            all_comps = all_comps[:limit]

        logger.info(f"Enriching {len(all_comps)} comps (concurrency={concurrency}, force_refresh={force_refresh})...")
        print(f"🚀 Starting parallel enrichment for {len(all_comps)} comps (workers={concurrency})...")

        async with async_playwright() as p:
            await self.init_browser(p)
            sem = asyncio.Semaphore(concurrency)
            progress = {"completed": 0, "total": len(all_comps)}

            async def process_comp(comp: Dict[str, Any]):
                cid = str(comp["listing_id"])
                async with sem:
                    # Check cache first before opening a page
                    if not force_refresh:
                        cached = self.get_cached_profile(cid)
                        if cached and cached.get("amenities_count", 0) > 0:
                            if cached.get("title") and (comp.get("name") in ("Home in Scottsdale", "Home in Tempe", "Home in Mesa", "Home in Chandler") or comp.get("name", "").startswith("503 Service")):
                                comp["name"] = cached["title"]
                            if cached.get("photo_url") and not comp.get("photo_url"):
                                comp["photo_url"] = cached["photo_url"]
                            if cached.get("bedrooms"):
                                comp["bedrooms"] = cached["bedrooms"]
                            if cached.get("beds"):
                                comp["beds"] = cached["beds"]
                            if cached.get("baths"):
                                comp["baths"] = cached["baths"]
                            comp["amenities_count"] = cached.get("amenities_count", 0)
                            progress["completed"] += 1
                            print(f"[{progress['completed']}/{progress['total']}] ⚡ [Cached] {cid}: {comp.get('name')[:35]} ({comp.get('beds')} beds, {comp.get('amenities_count')} amenities)")
                            return

                    page = await self.context.new_page()
                    try:
                        enriched = await self.enrich_listing(page, cid, force_refresh=force_refresh)
                        if enriched.get("title") and (comp.get("name") in ("Home in Scottsdale", "Home in Tempe", "Home in Mesa", "Home in Chandler") or comp.get("name", "").startswith("503 Service")):
                            comp["name"] = enriched["title"]
                        if enriched.get("photo_url") and not comp.get("photo_url"):
                            comp["photo_url"] = enriched["photo_url"]
                        if enriched.get("bedrooms"):
                            comp["bedrooms"] = enriched["bedrooms"]
                        if enriched.get("beds"):
                            comp["beds"] = enriched["beds"]
                        if enriched.get("baths"):
                            comp["baths"] = enriched["baths"]
                        comp["amenities_count"] = enriched.get("amenities_count", 0)
                        progress["completed"] += 1
                        print(f"[{progress['completed']}/{progress['total']}] ✅ {cid}: {enriched.get('title', comp.get('name'))[:35]} ({comp.get('beds')} beds, {enriched.get('amenities_count')} amenities)")
                    except Exception as e:
                        progress["completed"] += 1
                        print(f"[{progress['completed']}/{progress['total']}] ⚠️ Error {cid}: {e}")
                    finally:
                        await page.close()
                    await asyncio.sleep(1.0)

            await asyncio.gather(*(process_comp(c) for c in all_comps))
            await self.close_browser()

        # Save registry updates
        self.REGISTRY_PATH.write_text(json.dumps(registry, indent=2, ensure_ascii=False), encoding="utf-8")

        # Also synchronize config/listing_specs.json
        specs = {}
        if self.SPECS_PATH.exists():
            try:
                specs = json.loads(self.SPECS_PATH.read_text(encoding="utf-8"))
            except Exception:
                specs = {}
        for comp in all_comps:
            cid_str = str(comp.get("listing_id"))
            if not cid_str:
                continue
            if cid_str not in specs:
                specs[cid_str] = {"listing_id": cid_str}
            if comp.get("name"):
                specs[cid_str]["title"] = comp["name"]
            if comp.get("location"):
                specs[cid_str]["location"] = comp["location"]
            if comp.get("bedrooms") is not None:
                specs[cid_str]["bedrooms"] = comp["bedrooms"]
            if comp.get("beds") is not None:
                specs[cid_str]["beds"] = comp["beds"]
            if comp.get("baths") is not None:
                specs[cid_str]["baths"] = comp["baths"]
            if comp.get("rating") is not None:
                specs[cid_str]["rating"] = comp["rating"]
            if comp.get("reviews") is not None:
                specs[cid_str]["reviews"] = comp["reviews"]
            if comp.get("photo_url"):
                specs[cid_str]["photo_url"] = comp["photo_url"]
        self.SPECS_PATH.write_text(json.dumps(specs, indent=2, ensure_ascii=False), encoding="utf-8")
        print("✨ Comp registry and listing specs updated with enriched metadata!")
        return registry

    def sync_cached_to_registry(self) -> Dict[str, Any]:
        """
        Synchronize all cached listing profiles in data/enriched_comps/
        to config/comps_registry.json and config/listing_specs.json without scraping.
        """
        if not self.REGISTRY_PATH.exists():
            raise FileNotFoundError(f"Registry not found at {self.REGISTRY_PATH}")

        registry = json.loads(self.REGISTRY_PATH.read_text(encoding="utf-8"))
        specs = {}
        if self.SPECS_PATH.exists():
            try:
                specs = json.loads(self.SPECS_PATH.read_text(encoding="utf-8"))
            except Exception:
                specs = {}

        updated_count = 0
        for tier_key in ("tier_a", "tier_b"):
            for cid, comp in registry.get(tier_key, {}).items():
                cached = self.get_cached_profile(str(cid))
                if not cached:
                    continue

                if cached.get("title") and not cached.get("title", "").startswith("503 Service"):
                    comp["name"] = cached["title"]
                if cached.get("photo_url"):
                    comp["photo_url"] = cached["photo_url"]
                if cached.get("bedrooms") is not None:
                    comp["bedrooms"] = cached["bedrooms"]
                if cached.get("beds") is not None:
                    comp["beds"] = cached["beds"]
                if cached.get("baths") is not None:
                    comp["baths"] = cached["baths"]
                if cached.get("guests") is not None:
                    try:
                        comp["accommodates"] = int(str(cached["guests"]).replace("+", ""))
                    except Exception:
                        pass
                if cached.get("amenities_count") is not None:
                    comp["amenities_count"] = cached["amenities_count"]

                # Sync to specs
                cid_str = str(cid)
                if cid_str not in specs:
                    specs[cid_str] = {"listing_id": cid_str}
                specs[cid_str]["title"] = comp.get("name")
                specs[cid_str]["location"] = comp.get("location")
                specs[cid_str]["bedrooms"] = comp.get("bedrooms")
                specs[cid_str]["beds"] = comp.get("beds")
                specs[cid_str]["baths"] = comp.get("baths")
                specs[cid_str]["rating"] = comp.get("rating")
                specs[cid_str]["reviews"] = comp.get("reviews")
                if comp.get("photo_url"):
                    specs[cid_str]["photo_url"] = comp.get("photo_url")
                updated_count += 1

        self.REGISTRY_PATH.write_text(json.dumps(registry, indent=2, ensure_ascii=False), encoding="utf-8")
        self.SPECS_PATH.write_text(json.dumps(specs, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"✨ Synchronized {updated_count} comps from cached profiles to registry and listing_specs.json!")
        return registry


if __name__ == "__main__":
    enricher = ListingEnricher(headless=True)
    print("Enriching our property profile...")
    asyncio.run(enricher.enrich_our_property(force_refresh=True))
