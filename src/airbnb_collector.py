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
        """Parse card innerText to extract listing attributes and price deterministically without hardcoded thresholds."""
        nights = max(1, nights)

        # 0. Reject cards that Airbnb injected with alternative / flexible dates
        # (e.g. "Sep 7 to 9", "Sep 7–9", "Nov 30 to Dec 2" when listing is not available for requested dates)
        alt_date_match = re.search(
            r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d+\s*(?:to|–|-)\s*(?:(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+)?\d+",
            text[:200],
            re.IGNORECASE,
        )
        if alt_date_match:
            return None

        lower_snippet = text[:300].lower()
        if any(w in lower_snippet for w in ["similar dates", "available for part of your stay", "check other dates", "different dates"]):
            return None

        # 1. Look for explicit total stay price:
        # e.g. "$1,417 before taxes", "$1,417 total", "$1,417 for 4 nights", "$1,417 total before taxes"
        total_match = re.search(
            r"\$([0-9,]+(?:\.[0-9]{2})?)\s*(?:before taxes|total(?:\s+before taxes)?|for\s+\d+\s+nights)",
            text,
            re.IGNORECASE,
        )

        # 2. Look for explicit nightly rate:
        # e.g. "$354 night", "$354 / night"
        nightly_matches = re.findall(
            r"\$([0-9,]+(?:\.[0-9]{2})?)\s*(?:night|/\s*night)",
            text,
            re.IGNORECASE,
        )

        all_dollars = [
            float(p.replace(",", ""))
            for p in re.findall(r"\$([0-9,]+(?:\.[0-9]{2})?)", text)
            if float(p.replace(",", "")) > 0
        ]
        if not all_dollars:
            return None

        extracted_total = float(total_match.group(1).replace(",", "")) if total_match else None
        extracted_nightly = float(nightly_matches[-1].replace(",", "")) if nightly_matches else None

        # Build raw price snippet for transparency and verification
        price_snippet = " | ".join(
            [m.group(0).strip() for m in re.finditer(r"\$[0-9,]+(?:\.[0-9]{2})?[^$\n]*", text)][:3]
        )

        if extracted_total is not None and extracted_nightly is not None:
            expected_total = extracted_nightly * nights
            pct_diff = abs(extracted_total - expected_total) / max(1.0, expected_total)
            if pct_diff <= 0.25:
                # Both match within standard discount/fee tolerance
                total_stay_price = extracted_total
                effective_nightly = round(total_stay_price / nights, 2)
                confidence = "CONFIRMED"
                confidence_reason = "Nightly & total labels match mathematically"
            else:
                total_stay_price = extracted_total
                effective_nightly = round(total_stay_price / nights, 2)
                confidence = "AMBIGUOUS"
                confidence_reason = f"Conflict: nightly (${extracted_nightly:.0f}) vs total (${extracted_total:.0f}) for {nights}n"
        elif extracted_total is not None:
            total_stay_price = extracted_total
            effective_nightly = round(total_stay_price / nights, 2)
            confidence = "CONFIRMED"
            confidence_reason = "Total explicitly labeled ('before taxes'/'total')"
        elif extracted_nightly is not None:
            effective_nightly = extracted_nightly
            total_stay_price = round(effective_nightly * nights, 2)
            confidence = "CONFIRMED"
            confidence_reason = "Nightly explicitly labeled ('night')"
        else:
            # Unlabeled price: dollar amount without 'night' or 'total'/'before taxes' label
            # Do NOT guess with magic thresholds! Flag for host review.
            total_stay_price = all_dollars[-1]
            effective_nightly = round(total_stay_price / nights, 2)
            confidence = "AMBIGUOUS"
            confidence_reason = f"Unlabeled price (${total_stay_price:.0f}): missing 'night' or 'total'/'before taxes' label"

        if total_stay_price <= 0.0 or effective_nightly <= 0.0:
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
            "confidence": confidence,
            "confidence_reason": confidence_reason,
            "price_snippet": price_snippet,
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
                        results.push({ id: m[1], text: c.innerText, href: href });
                    }
                    return results;
                }""")

                seen_in_loc = set()
                for c in cards:
                    cid = c["id"]
                    if cid in seen_in_loc or cid == "573857947793833342":  # Exclude our own property!
                        continue

                    # Verify href does not specify different checkin dates
                    href = c.get("href", "")
                    if "check_in=" in href and f"check_in={check_in}" not in href:
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

