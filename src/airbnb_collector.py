"""
Airbnb Competitive Data Collector.
Uses Playwright with stealth settings to extract competitive market data
across Tempe, Scottsdale, Chandler, Gilbert, and Phoenix corridor.
Includes:
- Local JSON caching to prevent redundant requests
- Humanized random delays (3-7s) between requests to protect IP reputation
- Robust extraction of effective price, total cost, bedrooms, amenities, and ratings
- Support for both Tier A (16+ guests, 6+ BR) and Tier B (12-15 guests, 5+ BR)
"""

import asyncio
import hashlib
import json
import random
import re
from datetime import date
from pathlib import Path
from typing import List, Dict, Any, Optional
from playwright.async_api import async_playwright, Browser, BrowserContext, Page


class AirbnbCollector:
    """Collects comp listing data and pricing from Airbnb."""

    LOCATIONS = [
        "Tempe--AZ",
        "Scottsdale--AZ",
        "Chandler--AZ",
        "Mesa--AZ",
    ]

    def __init__(
        self,
        cache_dir: str = "data/cache",
        headless: bool = True,
        min_delay: float = 3.5,
        max_delay: float = 6.5,
    ):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.headless = headless
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None

    def _get_cache_key(self, check_in: str, check_out: str, location: str, tier: str) -> Path:
        raw = f"{check_in}_{check_out}_{location}_{tier}"
        h = hashlib.md5(raw.encode()).hexdigest()[:10]
        return self.cache_dir / f"search_{check_in}_{check_out}_{location}_{tier}_{h}.json"

    async def init_browser(self, p):
        """Launch browser with anti-detection flags."""
        self.browser = await p.chromium.launch(
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                "--no-sandbox",
            ],
        )
        self.context = await self.browser.new_context(
            viewport={"width": 1366, "height": 850},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        )

    async def close_browser(self):
        """Close browser resources."""
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()

    def _parse_card_text(self, card_id: str, text: str, nights: int) -> Optional[Dict[str, Any]]:
        """Parse card innerText to extract listing attributes and price."""
        # Find all dollar amounts
        price_matches = re.findall(r"\$([0-9,]+)", text)
        if not price_matches:
            return None

        # Clean numbers
        prices = [int(p.replace(",", "")) for p in price_matches if int(p.replace(",", "")) > 0]
        if not prices:
            return None

        # If strikethrough (e.g. $4,354 $2,998), the second is active price.
        # Otherwise the last valid price before 'Show price breakdown' is the total price.
        # Airbnb usually shows either:
        # - '$850 / night, $2,550 total'
        # - '$4,354 $2,998 for 3 nights'
        # Total price for multi-night stays is generally >= nights * 200.
        total_price = prices[-1]
        if len(prices) >= 2 and prices[-1] < 100:
            total_price = prices[-2]

        # Check if Airbnb gave per-night rate vs total price
        if "night" in text.lower() and "total" not in text.lower() and total_price < 1500:
            # Stated per night
            effective_nightly = float(total_price)
            total_stay_price = effective_nightly * nights
        else:
            # Stated total
            total_stay_price = float(total_price)
            effective_nightly = round(total_stay_price / nights, 2) if nights > 0 else total_stay_price

        # Sanity check for large luxury estate (should be >= $200/night total)
        if effective_nightly < 150.0 or effective_nightly > 10000.0:
            return None

        # Extract bedrooms
        br_match = re.search(r"(\d+)\s*bedrooms?", text, re.IGNORECASE)
        bedrooms = int(br_match.group(1)) if br_match else 6

        # Extract beds
        bed_match = re.search(r"(\d+)\s*beds?", text, re.IGNORECASE)
        beds = int(bed_match.group(1)) if bed_match else bedrooms

        # Extract baths
        ba_match = re.search(r"(\d+(?:\.\d+)?)\s*baths?", text, re.IGNORECASE)
        baths = float(ba_match.group(1)) if ba_match else 3.0

        # Extract title / location
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        title = lines[2] if len(lines) > 2 else lines[0]
        location = "Phoenix Valley"
        for line in lines:
            if "in " in line.lower():
                location = line.replace("Home in", "").replace("Entire home in", "").strip()
                break

        # Rating & reviews
        rating = 4.9
        rating_match = re.search(r"(\d\.\d+)\s*\(([\d,]+)\)", text)
        if rating_match:
            rating = float(rating_match.group(1))
            reviews = int(rating_match.group(2).replace(",", ""))
        else:
            reviews = 10

        return {
            "listing_id": card_id,
            "title": title[:60],
            "location": location,
            "bedrooms": bedrooms,
            "beds": beds,
            "baths": baths,
            "nights": nights,
            "total_price": total_stay_price,
            "effective_nightly": effective_nightly,
            "rating": rating,
            "reviews": reviews,
            "raw_snippet": " | ".join(lines[:4]),
        }

    async def fetch_comps_for_dates(
        self,
        check_in: str,
        check_out: str,
        nights: int,
        tier: str = "tier_a",
        locations: Optional[List[str]] = None,
        use_cache: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Fetch available competitive listings for given check_in and check_out.
        Tier A: 16 adults, 6 min bedrooms
        Tier B: 12 adults, 5 min bedrooms
        """
        locations = locations or self.LOCATIONS
        adults = 16 if tier == "tier_a" else 12
        min_bedrooms = 6 if tier == "tier_a" else 5

        all_listings: Dict[str, Dict[str, Any]] = {}

        for loc in locations:
            cache_file = self._get_cache_key(check_in, check_out, loc, tier)
            if use_cache and cache_file.exists():
                try:
                    cached_data = json.loads(cache_file.read_text(encoding="utf-8"))
                    for item in cached_data:
                        all_listings[item["listing_id"]] = item
                    continue
                except Exception:
                    pass

            # Make request via Playwright
            search_url = (
                f"https://www.airbnb.com/s/{loc}/homes?"
                f"adults={adults}&min_bedrooms={min_bedrooms}&checkin={check_in}&checkout={check_out}"
            )

            page = await self.context.new_page()
            loc_results: List[Dict[str, Any]] = []
            try:
                await page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
                await page.wait_for_timeout(4000)

                # Extract all card containers
                cards = await page.evaluate("""() => {
                    const results = [];
                    const containers = document.querySelectorAll('[data-testid="card-container"]');
                    for (const c of containers) {
                        const link = c.querySelector('a[href*="/rooms/"]');
                        if (!link) continue;
                        const href = link.getAttribute('href') || '';
                        const m = href.match(/rooms\\/([0-9]+)/);
                        if (!m) continue;
                        results.push({ id: m[1], text: c.innerText });
                    }
                    return results;
                }""")

                seen_in_loc = set()
                for c in cards:
                    cid = c["id"]
                    if cid in seen_in_loc or cid == "573857947793833342":  # Exclude our own property!
                        continue
                    seen_in_loc.add(cid)

                    parsed = self._parse_card_text(cid, c["text"], nights)
                    if parsed:
                        loc_results.append(parsed)
                        all_listings[cid] = parsed

                # Cache this location's results
                cache_file.write_text(json.dumps(loc_results, indent=2), encoding="utf-8")

            except Exception as e:
                print(f"Warning: could not fetch {loc} for {check_in}: {e}")
            finally:
                await page.close()

            # Random polite sleep between locations
            await asyncio.sleep(random.uniform(self.min_delay, self.max_delay))

        return list(all_listings.values())

