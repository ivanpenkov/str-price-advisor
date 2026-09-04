"""
Deep Listing Enricher.
Extracts comprehensive listing metadata, descriptions, room details, and full amenity
lists for our property and all competitor properties in the registry.
Caches enriched listing profiles under data/enriched_comps/.
"""

import asyncio
import json
import logging
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

    def __init__(self, headless: bool = True):
        self.headless = headless
        self.ENRICHED_DIR.mkdir(parents=True, exist_ok=True)
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None

    async def init_browser(self, p):
        """Launch headless browser with anti-detection args."""
        self.browser = await p.chromium.launch(
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        self.context = await self.browser.new_context(
            viewport={"width": 1366, "height": 850},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            ),
        )

    async def close_browser(self):
        """Close browser resources."""
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()

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
        amenity_titles = []
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
                logger.warning(f"Error parsing deferred state for {listing_id}: {e}")

        # 3. Extract overview badges (e.g. 16+ guests, 6 bedrooms, 11 beds, 5.5 baths)
        overview = await page.evaluate("""() => {
            const items = Array.from(document.querySelectorAll("ol li, div[data-section-id='OVERVIEW_DEFAULT'] li, [data-testid='overview'] li"));
            return items.map(e => e.innerText.trim()).filter(t => t.length > 0 && t.length < 50 && !t.includes('\\n'));
        }""")

        # 4. Fallback description & title
        title = ld_data.get("name")
        if not title:
            title = await page.title()

        description = ld_data.get("description", "")
        if not description:
            desc_text = await page.evaluate("""() => {
                const el = document.querySelector("[data-section-id='DESCRIPTION_DEFAULT']");
                return el ? el.innerText.trim() : '';
            }""")
            description = desc_text

        # 5. Rating & Reviews
        rating_obj = ld_data.get("aggregateRating", {})
        rating = None
        reviews = None
        if rating_obj:
            rating = rating_obj.get("ratingValue")
            reviews = rating_obj.get("ratingCount")
            try:
                if rating is not None:
                    rating = float(rating)
                if reviews is not None:
                    reviews = int(reviews)
            except Exception:
                pass

        # 6. Address / Location
        address = ld_data.get("address")

        # 7. Image / Cover photo
        photo_url = ld_data.get("image")
        if isinstance(photo_url, list) and photo_url:
            photo_url = photo_url[0]
        elif not photo_url:
            photo_url = await page.evaluate("""() => {
                const img = document.querySelector("meta[property='og:image']");
                return img ? img.getAttribute("content") : null;
            }""")

        result = {
            "listing_id": listing_id,
            "title": title,
            "description": description,
            "amenities": sorted(list(set(amenity_titles))),
            "amenities_count": len(amenity_titles),
            "overview": overview[:8],
            "rating": rating,
            "reviews": reviews,
            "address": address,
            "photo_url": photo_url,
            "url": url,
            "enriched_at": datetime.now().isoformat(),
        }
        return result

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
                            if cached.get("title") and comp.get("name") in ("Home in Scottsdale", "Home in Tempe", "Home in Mesa", "Home in Chandler"):
                                comp["name"] = cached["title"]
                            if cached.get("photo_url") and not comp.get("photo_url"):
                                comp["photo_url"] = cached["photo_url"]
                            comp["amenities_count"] = cached.get("amenities_count", 0)
                            progress["completed"] += 1
                            print(f"[{progress['completed']}/{progress['total']}] ⚡ [Cached] {cid}: {comp.get('name')[:35]} ({comp.get('amenities_count')} amenities)")
                            return

                    page = await self.context.new_page()
                    try:
                        enriched = await self.enrich_listing(page, cid, force_refresh=force_refresh)
                        if enriched.get("title") and comp.get("name") in ("Home in Scottsdale", "Home in Tempe", "Home in Mesa", "Home in Chandler"):
                            comp["name"] = enriched["title"]
                        if enriched.get("photo_url") and not comp.get("photo_url"):
                            comp["photo_url"] = enriched["photo_url"]
                        comp["amenities_count"] = enriched.get("amenities_count", 0)
                        progress["completed"] += 1
                        print(f"[{progress['completed']}/{progress['total']}] ✅ {cid}: {enriched.get('title', comp.get('name'))[:35]} ({enriched.get('amenities_count')} amenities)")
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
        print("✨ Comp registry updated with enriched metadata!")
        return registry


if __name__ == "__main__":
    enricher = ListingEnricher(headless=True)
    print("Enriching our property profile...")
    asyncio.run(enricher.enrich_our_property(force_refresh=True))
