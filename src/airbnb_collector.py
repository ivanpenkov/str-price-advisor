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
import base64
import hashlib
import json
import os
import random
import re
import time
import urllib.parse
import contextvars
from contextlib import asynccontextmanager
from datetime import date
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from playwright.async_api import async_playwright, Browser, BrowserContext, Page


logger = logging.getLogger(__name__)

from src.stealth_connection import StealthConnectionManager
from src.html_generator import extract_clean_listing_title


class IntervalByteTracker:
    """Tracks isolated network payload bytes for a specific interval task."""

    def __init__(self):
        self.bytes: int = 0

    def add_bytes(self, byte_count: Any) -> None:
        if isinstance(byte_count, (int, float)) and not isinstance(byte_count, bool) and byte_count > 0:
            self.bytes += int(byte_count)


_current_interval_tracker: contextvars.ContextVar[Optional[IntervalByteTracker]] = contextvars.ContextVar(
    "_current_interval_tracker", default=None
)


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
        parallel: bool = True,
        max_attempts: int = 3,
        retry_delay: Optional[float] = None,
        proxy_mgr: Optional[StealthConnectionManager] = None,
    ):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.headless = headless
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.parallel = parallel
        self.max_attempts = max_attempts
        self.retry_delay = (
            float(os.getenv("AIRBNB_RETRY_DELAY", "1.0"))
            if retry_delay is None
            else retry_delay
        )
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.contexts: Dict[str, BrowserContext] = {}
        self.worker_contexts: List[BrowserContext] = []
        self._worker_queue: Optional[asyncio.Queue] = None
        self.total_bytes_transferred: int = 0
        self._curr_interval_bytes: int = 0
        self.last_interval_bytes: int = 0
        self.last_interval_duration: float = 0.0
        self._owns_proxy_mgr = (proxy_mgr is None)
        self.proxy_mgr = proxy_mgr or StealthConnectionManager(required=True)
        self.pdp_timeout: float = 5.0
        self.specs_path = Path("config/listing_specs.json")
        self.listing_specs: Dict[str, Dict[str, Any]] = {}
        if self.specs_path.exists():
            try:
                self.listing_specs = json.loads(self.specs_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        self.excluded_comps: set = set()
        comps_path = Path("config/comps_registry.json")
        if comps_path.exists():
            try:
                reg = json.loads(comps_path.read_text(encoding="utf-8"))
                self.excluded_comps = {str(k) for k in reg.get("excluded_comps", {}).keys()}
                self.excluded_comps.update({str(k) for k in reg.get("disqualified", {}).keys()})
            except Exception:
                pass

    @asynccontextmanager
    async def track_interval_bytes(self):
        """Task-scoped async context manager capturing isolated payload bytes for an interval."""
        tracker = IntervalByteTracker()
        token = _current_interval_tracker.set(tracker)
        try:
            yield tracker
        finally:
            _current_interval_tracker.reset(token)

    def _make_context_tracker(self, ctx: BrowserContext):
        """Create a response handler tracking both global and context-isolated bytes."""
        def _tracker(response):
            try:
                cl = response.headers.get("content-length")
                if cl and cl.isdigit():
                    b = int(cl)
                    self.total_bytes_transferred += b
                    self._curr_interval_bytes += b
                    ctx._transferred_bytes = self._safe_get_context_bytes(ctx) + b
            except Exception:
                pass
        return _tracker

    def _track_response_bytes(self, response):
        """Track payload size in bytes from HTTP response headers."""
        try:
            cl = response.headers.get("content-length")
            if cl and cl.isdigit():
                b = int(cl)
                self.total_bytes_transferred += b
                self._curr_interval_bytes += b
        except Exception:
            pass

    def _get_cache_key(self, check_in: str, check_out: str, location: str, tier: str) -> Path:
        raw = f"{check_in}_{check_out}_{location}_{tier}"
        h = hashlib.md5(raw.encode()).hexdigest()[:10]
        return self.cache_dir / f"search_{check_in}_{check_out}_{location}_{tier}_{h}.json"

    async def init_browser(self, p):
        """Launch browser with anti-detection flags and proxy support (multi-pool or single)."""
        launch_kwargs = {
            "headless": self.headless,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                "--no-sandbox",
            ],
        }

        try:
            if self.parallel:
                if not self.proxy_mgr.endpoints:
                    num_workers = self.proxy_mgr.max_workers
                    min_healthy = StealthConnectionManager.calculate_min_healthy(num_workers)
                    proxy_configs = await self.proxy_mgr.start_pool(
                        num_workers=num_workers,
                        min_healthy=min_healthy,
                        wait_for_full_pool=False,
                    )
                else:
                    proxy_configs = self.proxy_mgr.get_proxy_configs()
                if self.proxy_mgr.required and not proxy_configs:
                    raise RuntimeError("❌ [PROXY ERROR] Cannot launch parallel browser contexts: no proxy endpoints available in feeder pool.")
                self.browser = await p.chromium.launch(**launch_kwargs)
                self.worker_contexts = []
                self._worker_queue = asyncio.Queue()
                self.contexts = {}
                if proxy_configs:
                    for idx, p_cfg in enumerate(proxy_configs):
                        if self.proxy_mgr.required and (not p_cfg or not p_cfg.get("server")):
                            raise RuntimeError(f"❌ [PROXY ERROR] No proxy endpoint available for worker {idx}. Direct unproxied connection is prohibited.")
                        ctx_kwargs = {
                            "viewport": {"width": 1366, "height": 850},
                            "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                        }
                        if p_cfg and p_cfg.get("server"):
                            ctx_kwargs["proxy"] = {"server": p_cfg["server"]}
                        ctx = await self.browser.new_context(**ctx_kwargs)
                        ctx._transferred_bytes = 0
                        ctx.on("response", self._make_context_tracker(ctx))
                        self.worker_contexts.append(ctx)
                        await self._worker_queue.put(ctx)

                    for loc_idx, loc in enumerate(self.LOCATIONS):
                        if self.worker_contexts:
                            self.contexts[loc] = self.worker_contexts[loc_idx % len(self.worker_contexts)]
                else:
                    # Fallback single unproxied context for test/offline environments when required=False
                    ctx = await self.browser.new_context(
                        viewport={"width": 1366, "height": 850},
                        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                    )
                    ctx._transferred_bytes = 0
                    ctx.on("response", self._make_context_tracker(ctx))
                    self.worker_contexts.append(ctx)
                    await self._worker_queue.put(ctx)
                    self.contexts = {loc: ctx for loc in self.LOCATIONS}

                # Primary context for single queries / backwards compatibility
                self.context = self.worker_contexts[0] if self.worker_contexts else None
            else:
                if self.proxy_mgr.endpoints:
                    proxy_cfg = {"server": self.proxy_mgr.endpoints[0].url}
                else:
                    proxy_cfg = await self.proxy_mgr.start()
                if self.proxy_mgr.required and not proxy_cfg:
                    raise RuntimeError(
                        "❌ [PROXY ERROR] Cannot launch Airbnb collector: no proxy endpoints available in feeder pool. "
                        "Direct unproxied connection is prohibited."
                    )
                if proxy_cfg:
                    launch_kwargs["proxy"] = proxy_cfg
                self.browser = await p.chromium.launch(**launch_kwargs)
                self.context = await self.browser.new_context(
                    viewport={"width": 1366, "height": 850},
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                )
                self.context._transferred_bytes = 0
                self.context.on("response", self._make_context_tracker(self.context))
                self.worker_contexts = [self.context]
                self._worker_queue = asyncio.Queue()
                self._worker_queue.put_nowait(self.context)
                self.contexts = {loc: self.context for loc in self.LOCATIONS}
        except Exception:
            await self.close_browser()
            raise

    @staticmethod
    def _safe_get_context_bytes(ctx: Any) -> int:
        """Safely extract integer byte count, guarding against MagicMock, bool, or None."""
        val = getattr(ctx, "_transferred_bytes", 0)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return int(val)
        return 0

    @asynccontextmanager
    async def lease_context(self):
        """Lease an isolated browser context from the worker pool with FIFO queue waiting."""
        queue = getattr(self, "_worker_queue", None)
        should_requeue = False
        if queue is not None:
            ctx = await queue.get()
            should_requeue = True
        elif getattr(self, "worker_contexts", None):
            ctx = self.worker_contexts[0]
        elif getattr(self, "context", None):
            ctx = self.context
        elif getattr(self, "contexts", None):
            ctx = list(self.contexts.values())[0]
        else:
            raise RuntimeError("No browser context available for leasing in AirbnbCollector.")

        start_b = self._safe_get_context_bytes(ctx)
        try:
            yield ctx
        finally:
            delta_b = max(0, self._safe_get_context_bytes(ctx) - start_b)
            tracker = _current_interval_tracker.get()
            if tracker is not None:
                tracker.add_bytes(delta_b)
            if should_requeue and getattr(self, "_worker_queue", None) is not None:
                await queue.put(ctx)

    async def close_browser(self):
        """Close browser resources and terminate proxy bridges."""
        all_ctxs = set(getattr(self, "worker_contexts", [])) | set(self.contexts.values())
        if self.context:
            all_ctxs.add(self.context)
        for ctx in all_ctxs:
            try:
                await ctx.close()
            except Exception:
                pass
        self.worker_contexts.clear()
        self._worker_queue = None
        self.contexts.clear()
        self.context = None
        if self.browser:
            try:
                await self.browser.close()
            except Exception:
                pass
            self.browser = None
        if self._owns_proxy_mgr:
            await self.proxy_mgr.stop()

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
        elif len(all_dollars) >= 2 and all_dollars[1] < all_dollars[0] and (0.40 <= all_dollars[1] / all_dollars[0] < 1.0):
            # Promotional strikethrough total: e.g. "$4,884 $4,445" (Early bird / weekly discount)
            total_stay_price = all_dollars[1]
            effective_nightly = round(total_stay_price / nights, 2)
            discount_pct = round((1.0 - (all_dollars[1] / all_dollars[0])) * 100)
            confidence = "CONFIRMED"
            confidence_reason = f"Promotional stay total (${all_dollars[1]:.0f} discounted from ${all_dollars[0]:.0f}, -{discount_pct}%)"
        else:
            # Unlabeled single price: dollar amount without 'night' or 'total'/'before taxes' label
            # Do NOT guess with magic thresholds! Flag for host review.
            total_stay_price = all_dollars[-1]
            effective_nightly = round(total_stay_price / nights, 2)
            confidence = "AMBIGUOUS"
            confidence_reason = f"Unlabeled price (${total_stay_price:.0f}): missing 'night' or 'total'/'before taxes' label"

        if total_stay_price <= 0.0 or effective_nightly <= 0.0:
            return None

        if card_id and str(card_id) in self.excluded_comps:
            return None

        spec = self.listing_specs.get(str(card_id), {})

        # Extract bedrooms
        br_match = re.search(r"(\d+)\s*bedrooms?", text, re.IGNORECASE)
        bedrooms = int(br_match.group(1)) if br_match else spec.get("bedrooms", 6)

        # Extract beds
        bed_match = re.search(
            r"\b(\d+)\s*(?:[-–]|(?:(?:king|queen|double|single|bunk|twin|sofa|day|murphy)\s*)*)?beds?\b(?!rooms?)",
            text,
            re.IGNORECASE,
        )
        if bed_match:
            beds = int(bed_match.group(1))
        elif spec.get("beds"):
            beds = int(spec["beds"])
        else:
            beds = bedrooms

        # Extract baths
        ba_match = re.search(r"(\d+(?:\.\d+)?)\s*baths?", text, re.IGNORECASE)
        baths = float(ba_match.group(1)) if ba_match else spec.get("baths", 3.0)

        # Extract title / location
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        reg_title = spec.get("title") or spec.get("name") or ""
        fallback_title = lines[2] if len(lines) > 2 else (lines[0] if lines else "")
        title = extract_clean_listing_title(
            raw_snippet=text,
            default_title=fallback_title,
            registered_name=reg_title,
        )
        location = "Phoenix Valley"
        for line in lines:
            if "in " in line.lower():
                location = line.replace("Home in", "").replace("Entire home in", "").strip()
                break

        # Rating & reviews
        rating = None
        reviews = 0
        rating_match = re.search(r"\b(\d(?:\.\d+)?)\s*\(([\d,]+)\)", text)
        if not rating_match:
            rating_match = re.search(r"Rating\s+(\d(?:\.\d+)?)\s+out\s+of\s+5;?\s*([\d,]+)?\s*reviews?", text, re.IGNORECASE)
        if rating_match:
            try:
                rating = float(rating_match.group(1))
                reviews = int(rating_match.group(2).replace(",", "")) if rating_match.group(2) else 0
            except Exception:
                pass

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

    @staticmethod
    def get_search_cursor(offset: int) -> str:
        """Generate base64 cursor token for Airbnb search pagination."""
        payload = json.dumps({"section_offset": 0, "items_offset": offset, "version": 1}, separators=(',', ':'))
        return base64.b64encode(payload.encode("utf-8")).decode("utf-8")

    async def _scrape_corridor(
        self,
        loc: str,
        check_in: str,
        check_out: str,
        nights: int,
        tier: str = "tier_a",
        max_pages: Optional[int] = None,
        use_cache: bool = True,
        max_cache_age_hours: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """Scrape or load cache for a single location corridor."""
        adults = 16 if tier == "tier_a" else 12
        min_bedrooms = 6 if tier == "tier_a" else 5
        pages_to_fetch = max_pages if (max_pages is not None and max_pages > 0) else random.choice([2, 3])

        cache_file = self._get_cache_key(check_in, check_out, loc, tier)
        is_cache_valid = False
        if use_cache and cache_file.exists():
            if max_cache_age_hours is not None:
                try:
                    file_age_hours = (time.time() - cache_file.stat().st_mtime) / 3600.0
                    is_cache_valid = (file_age_hours <= max_cache_age_hours)
                except Exception:
                    is_cache_valid = True
            else:
                is_cache_valid = True

        if is_cache_valid:
            try:
                cached_data = json.loads(cache_file.read_text(encoding="utf-8"))
                valid_items = []
                for item in cached_data:
                    lid = str(item.get("listing_id") or "")
                    if lid and lid not in self.excluded_comps:
                        valid_items.append(item)
                return valid_items
            except Exception:
                pass

        base_search_url = (
            f"https://www.airbnb.com/s/{loc}/homes?"
            f"adults={adults}&min_bedrooms={min_bedrooms}&checkin={check_in}&checkout={check_out}&locale=en&currency=USD"
        )

        seen_in_loc = set()
        loc_results: List[Dict[str, Any]] = []

        any_fetch_success = False
        async with self.lease_context() as context:
            for page_num in range(1, pages_to_fetch + 1):
                offset = (page_num - 1) * 18
                if page_num == 1:
                    target_url = base_search_url
                else:
                    cursor_token = self.get_search_cursor(offset)
                    target_url = f"{base_search_url}&cursor={urllib.parse.quote(cursor_token)}"

                fetch_success = False
                cards_count = 0
                max_attempts = getattr(self, "max_attempts", 3)
                last_exc = None

                for attempt in range(1, max_attempts + 1):
                    page = None
                    try:
                        page = await context.new_page()
                        await page.goto(target_url, wait_until="domcontentloaded", timeout=20000)
                        try:
                            await page.wait_for_selector('[data-testid="card-container"]', timeout=4000)
                        except Exception:
                            pass
                        await page.wait_for_timeout(1000)

                        # Extract all card containers on current page
                        cards = await page.evaluate("""() => {
                            const results = [];
                            const containers = document.querySelectorAll('[data-testid="card-container"]');
                            for (const c of containers) {
                                const link = c.querySelector('a[href*="/rooms/"]');
                                if (!link) continue;
                                const href = link.getAttribute('href') || '';
                                const m = href.match(/rooms\\/([0-9]+)/);
                                if (!m) continue;
                                let photoUrl = null;
                                const img = c.querySelector('img[src*="/Hosting-"], img[src*="/pictures/miso/"], img[src*="/pictures/prohost-api/"], img[src*="/pictures/"]');
                                if (img) {
                                    photoUrl = img.getAttribute('src') || img.src || null;
                                }
                                results.push({ id: m[1], text: c.innerText, href: href, photo_url: photoUrl });
                            }
                            return results;
                        }""")

                        page_seen = set()
                        page_parsed = []
                        for c in cards:
                            cid = c["id"]
                            if cid in seen_in_loc or cid in page_seen:
                                continue

                            # If this is our own property, capture its live Airbnb price for apples-to-apples comparison!
                            if cid == "573857947793833342":
                                parsed_our = self._parse_card_text(cid, c["text"], nights)
                                if parsed_our:
                                    our_cache_file = Path(f"data/cache/our_property_{check_in}_{check_out}.json")
                                    our_cache_file.write_text(json.dumps({
                                        "listing_id": cid,
                                        "check_in": check_in,
                                        "check_out": check_out,
                                        "nights": nights,
                                        "airbnb_total": parsed_our["total_price"],
                                        "airbnb_effective_nightly": parsed_our["effective_nightly"],
                                    }, indent=2), encoding="utf-8")
                                continue

                            # Verify href does not specify different checkin dates
                            href = c.get("href", "")
                            if "check_in=" in href and f"check_in={check_in}" not in href:
                                continue

                            page_seen.add(cid)

                            parsed = self._parse_card_text(cid, c["text"], nights)
                            if parsed:
                                if c.get("photo_url"):
                                    parsed["photo_url"] = c["photo_url"]
                                page_parsed.append(parsed)

                        seen_in_loc.update(page_seen)
                        loc_results.extend(page_parsed)
                        cards_count = len(cards)
                        fetch_success = True
                        any_fetch_success = True
                        break

                    except Exception as e:
                        last_exc = e
                        if attempt < max_attempts:
                            retry_delay = getattr(self, "retry_delay", 1.0)
                            if retry_delay > 0:
                                await asyncio.sleep(retry_delay)
                        else:
                            print(f"Warning: could not fetch {loc} page {page_num} for {check_in}: {last_exc}")
                    finally:
                        if page is not None:
                            try:
                                await asyncio.wait_for(page.close(), timeout=3.0)
                            except Exception:
                                pass

                if not fetch_success:
                    break

                # If page returned fewer than 18 cards, no further pages exist for this location
                if cards_count < 18:
                    break

                # Polite delay between page requests within a location
                if page_num < pages_to_fetch:
                    if getattr(self, "min_delay", 1.0) > 0:
                        await asyncio.sleep(random.uniform(1.0, 2.5))

        # Cache this location's aggregated results across all scraped pages
        if loc_results:
            cache_file.write_text(json.dumps(loc_results, indent=2), encoding="utf-8")
        elif any_fetch_success and not cache_file.exists():
            cache_file.write_text(json.dumps(loc_results, indent=2), encoding="utf-8")

        return loc_results

    async def fetch_comps_for_dates(
        self,
        check_in: str,
        check_out: str,
        nights: int,
        tier: str = "tier_a",
        locations: Optional[List[str]] = None,
        use_cache: bool = True,
        max_cache_age_hours: Optional[float] = None,
        max_pages: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Fetch available competitive listings for given check_in and check_out.
        Runs across 4 corridors concurrently via multi-IP proxy pool if self.parallel is True.
        Tracks execution duration and payload bytes transferred.
        """
        start_time = time.perf_counter()
        self._curr_interval_bytes = 0
        locations = locations or self.LOCATIONS

        all_listings: Dict[str, Dict[str, Any]] = {}

        if self.parallel and len(locations) > 1 and (self.worker_contexts or self.contexts):
            corridor_tasks = [
                self._scrape_corridor(
                    loc=loc,
                    check_in=check_in,
                    check_out=check_out,
                    nights=nights,
                    tier=tier,
                    max_pages=max_pages,
                    use_cache=use_cache,
                    max_cache_age_hours=max_cache_age_hours,
                )
                for loc in locations
            ]
            results_by_loc = await asyncio.gather(*corridor_tasks, return_exceptions=True)
            for res_list in results_by_loc:
                if isinstance(res_list, Exception):
                    print(f"Warning: corridor scraping task failed: {res_list}")
                    continue
                for item in res_list:
                    lid = str(item.get("listing_id") or "")
                    if lid and lid not in self.excluded_comps:
                        all_listings[lid] = item
        else:
            for loc in locations:
                res_list = await self._scrape_corridor(
                    loc=loc,
                    check_in=check_in,
                    check_out=check_out,
                    nights=nights,
                    tier=tier,
                    max_pages=max_pages,
                    use_cache=use_cache,
                    max_cache_age_hours=max_cache_age_hours,
                )
                for item in res_list:
                    lid = str(item.get("listing_id") or "")
                    if lid and lid not in self.excluded_comps:
                        all_listings[lid] = item
                await asyncio.sleep(random.uniform(self.min_delay, self.max_delay))

        self.last_interval_duration = round(time.perf_counter() - start_time, 2)
        self.last_interval_bytes = self._curr_interval_bytes
        return list(all_listings.values())

    async def fetch_our_listing_price(
        self, check_in: str, check_out: str, nights: int, use_cache: bool = True
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch Villa del Sol's live guest-facing price directly from its Airbnb listing page.
        Returns dict with: airbnb_total, airbnb_effective_nightly.
        """
        cache_file = Path(f"data/cache/our_property_{check_in}_{check_out}.json")
        if use_cache and cache_file.exists():
            try:
                return json.loads(cache_file.read_text(encoding="utf-8"))
            except Exception:
                pass

        room_id = "573857947793833342"
        url = f"https://www.airbnb.com/rooms/{room_id}?check_in={check_in}&check_out={check_out}&adults=1&locale=en&currency=USD"

        async with self.lease_context() as context:
            max_attempts = getattr(self, "max_attempts", 3)
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                page = None
                try:
                    page = await context.new_page()
                    await page.goto(url, wait_until="domcontentloaded", timeout=25000)
                    await page.wait_for_timeout(4000)

                    body_text = await page.evaluate("() => document.body.innerText")

                    m = re.search(r"\$([0-9,]+(?:\.[0-9]{2})?)\s*for\s+\d+\s+nights", body_text, re.IGNORECASE)
                    if m:
                        total_stay = float(m.group(1).replace(",", ""))
                    else:
                        m2 = re.search(r"\$([0-9,]+(?:\.[0-9]{2})?)\s*(?:before taxes|total)", body_text, re.IGNORECASE)
                        total_stay = float(m2.group(1).replace(",", "")) if m2 else None

                    if total_stay:
                        eff = round(total_stay / max(1, nights), 2)
                        res = {
                            "listing_id": room_id,
                            "check_in": check_in,
                            "check_out": check_out,
                            "nights": nights,
                            "airbnb_total": total_stay,
                            "airbnb_effective_nightly": eff,
                        }
                        cache_file.write_text(json.dumps(res, indent=2), encoding="utf-8")
                        return res
                    # If page loaded without exception but total_stay was not found, exit retry loop
                    break
                except Exception as e:
                    last_exc = e
                    if attempt < max_attempts:
                        retry_delay = getattr(self, "retry_delay", 1.0)
                        if retry_delay > 0:
                            await asyncio.sleep(retry_delay)
                    else:
                        print(f"Warning: could not fetch our listing price for {check_in}: {last_exc}")
                finally:
                    if page is not None:
                        try:
                            await asyncio.wait_for(page.close(), timeout=3.0)
                        except Exception:
                            pass
        return None

    async def fetch_direct_comp_price(
        self,
        listing_id: str,
        check_in: str,
        check_out: str,
        nights: int,
        comp_meta: Optional[Dict[str, Any]] = None,
        use_cache: bool = True,
        max_cache_age_hours: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Directly check room listing page for checkout pricing and availability.
        Uses StaysPdpSections GraphQL interception with fallback to DOM body text.
        Caches available results in data/cache/search_{check_in}_{check_out}_comp_{listing_id}.json
        Caches unavailable results in data/cache/unavailable_{check_in}_{check_out}_comp_{listing_id}.json
        """
        listing_id = str(listing_id)
        if listing_id in self.excluded_comps:
            return None

        avail_cache_file = self.cache_dir / f"search_{check_in}_{check_out}_comp_{listing_id}.json"
        unavail_cache_file = self.cache_dir / f"unavailable_{check_in}_{check_out}_comp_{listing_id}.json"

        if use_cache:
            # Check available cache first
            if avail_cache_file.exists():
                is_valid = True
                if max_cache_age_hours is not None:
                    try:
                        age = (time.time() - avail_cache_file.stat().st_mtime) / 3600.0
                        is_valid = (age <= max_cache_age_hours)
                    except Exception:
                        is_valid = True
                if is_valid:
                    try:
                        cached_items = json.loads(avail_cache_file.read_text(encoding="utf-8"))
                        if cached_items and isinstance(cached_items, list):
                            return cached_items[0]
                        elif isinstance(cached_items, dict):
                            return cached_items
                    except Exception:
                        pass

            # Check unavailable cache
            if unavail_cache_file.exists():
                is_valid = True
                if max_cache_age_hours is not None:
                    try:
                        age = (time.time() - unavail_cache_file.stat().st_mtime) / 3600.0
                        is_valid = (age <= max_cache_age_hours)
                    except Exception:
                        is_valid = True
                if is_valid:
                    return None

        # Resolve metadata from comp_meta or listing_specs
        from src.comp_manager import CompManager
        meta = comp_meta or self.listing_specs.get(listing_id, {})
        title = meta.get("name") or meta.get("title", f"Comp {listing_id}")
        location = meta.get("location", "Phoenix Valley")
        bedrooms = meta.get("bedrooms", 6)
        beds = meta.get("beds", bedrooms)
        baths = meta.get("baths", 3.0)
        rating = meta.get("rating")
        reviews = meta.get("reviews", 0)
        photo_url = meta.get("photo_url")

        def _parse_int(v: Any, default: int = 16) -> int:
            if v is None:
                return default
            try:
                m = re.search(r"\d+", str(v))
                return int(m.group(0)) if m else default
            except Exception:
                return default

        raw_acc = meta.get("accommodates") or meta.get("beds")
        accommodates = min(_parse_int(raw_acc, 16), 16)

        url = f"https://www.airbnb.com/rooms/{listing_id}?check_in={check_in}&check_out={check_out}&adults={accommodates}&locale=en&currency=USD"

        async with self.lease_context() as context:
            page = await context.new_page()
            intercepted_price: Optional[float] = None
            intercepted_label: Optional[str] = None
            is_unavailable: bool = False
            unavail_reason: Optional[str] = None
            done_event = asyncio.Event()

            async def on_response(resp):
                nonlocal intercepted_price, intercepted_label, is_unavailable, unavail_reason
                if "StaysPdpSections" in resp.url:
                    try:
                        body = await resp.text()
                        data = json.loads(body)
                        p, l, unavail, r = CompManager.parse_stays_pdp_sections(data)
                        if p and not intercepted_price:
                            intercepted_price = p
                            intercepted_label = l
                        if unavail:
                            is_unavailable = True
                        if r and not unavail_reason:
                            unavail_reason = r
                        if (p and p > 0) or unavail:
                            done_event.set()
                    except Exception:
                        pass

            page.on("response", on_response)
            try:
                resp_nav = await page.goto(url, wait_until="domcontentloaded", timeout=25000)
                if resp_nav and getattr(resp_nav, "status", 200) >= 400:
                    return None

                await page.evaluate("() => window.scrollTo(0, 1500)")
                try:
                    await asyncio.wait_for(done_event.wait(), timeout=getattr(self, "pdp_timeout", 5.0))
                except asyncio.TimeoutError:
                    pass

                # If no StaysPdpSections response, fallback to DOM inspection
                if not intercepted_price and not is_unavailable:
                    body_text = await page.evaluate("() => document.body.innerText")
                    lower_body = (body_text or "").lower()
                    if any(bot_phrase in lower_body for bot_phrase in [
                        "verify you are human", "press and hold", "access denied",
                        "please verify", "security check", "robot or human",
                    ]):
                        return None

                    if any(phrase in lower_body for phrase in [
                        "dates are not available", "dates aren't available",
                        "selected dates are unavailable", "these dates are unavailable",
                        "dates not available", "unavailable for these dates",
                        "minimum stay", "dates are unavailable",
                    ]):
                        is_unavailable = True
                        unavail_reason = "Dates unavailable on listing page"
                    else:
                        m = re.search(r"\$([0-9,]+(?:\.[0-9]{2})?)\s*for\s+\d+\s+nights", body_text, re.IGNORECASE)
                        if m:
                            intercepted_price = float(m.group(1).replace(",", ""))
                        else:
                            m2 = re.search(r"\$([0-9,]+(?:\.[0-9]{2})?)\s*(?:before taxes|total)", body_text, re.IGNORECASE)
                            if m2:
                                intercepted_price = float(m2.group(1).replace(",", ""))

                if intercepted_price and intercepted_price > 0:
                    eff_nightly = round(intercepted_price / max(1, nights), 2)
                    item = {
                        "listing_id": str(listing_id),
                        "title": title[:60],
                        "location": location,
                        "bedrooms": bedrooms,
                        "beds": beds,
                        "baths": baths,
                        "nights": nights,
                        "total_price": intercepted_price,
                        "effective_nightly": eff_nightly,
                        "rating": rating,
                        "reviews": reviews,
                        "confidence": "CONFIRMED",
                        "confidence_reason": "Direct single-comp checkout pricing via Airbnb API",
                        "price_snippet": f"${intercepted_price:,.0f} for {nights} nights | ${eff_nightly:,.0f}/night",
                        "raw_snippet": f"Direct Room Sweep | {title} | {intercepted_label or f'${intercepted_price}'}",
                        "photo_url": photo_url,
                    }
                    avail_cache_file.write_text(json.dumps([item], indent=2, ensure_ascii=False), encoding="utf-8")
                    if unavail_cache_file.exists():
                        try:
                            unavail_cache_file.unlink()
                        except Exception:
                            pass
                    return item
                elif is_unavailable:
                    # Confirmed booked / unavailable
                    unavail_record = {
                        "listing_id": str(listing_id),
                        "available": False,
                        "status": "BOOKED",
                        "reason": unavail_reason or "Listing unavailable / booked for selected dates",
                        "check_in": check_in,
                        "check_out": check_out,
                        "nights": nights,
                        "timestamp": time.time(),
                    }
                    unavail_cache_file.write_text(json.dumps(unavail_record, indent=2), encoding="utf-8")
                    if avail_cache_file.exists():
                        try:
                            avail_cache_file.unlink()
                        except Exception:
                            pass
                    return None
                else:
                    # Indeterminate state (e.g. transient glitch, layout shift) - do not poison cache
                    return None
            except Exception:
                return None
            finally:
                try:
                    page.remove_listener("response", on_response)
                except Exception:
                    pass
                try:
                    await asyncio.wait_for(page.close(), timeout=3.0)
                except Exception:
                    pass

    async def fetch_missing_comps_fallback(
        self,
        missing_comps: Dict[str, Dict[str, Any]],
        check_in: str,
        check_out: str,
        nights: int,
        use_cache: bool = True,
        max_cache_age_hours: Optional[float] = None,
        max_concurrency: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Concurrently check all missing comps across the multi-IP feeder pool.
        Returns list of available comp items.
        """
        if not missing_comps:
            return []

        num_contexts = len(self.worker_contexts or self.contexts or [])
        concurrency = max_concurrency or (max(1, num_contexts) if num_contexts > 0 else 5)
        sem = asyncio.Semaphore(concurrency)

        async def _check_comp(cid: str, meta: Dict[str, Any]):
            async with sem:
                try:
                    return await asyncio.wait_for(
                        self.fetch_direct_comp_price(
                            listing_id=cid,
                            check_in=check_in,
                            check_out=check_out,
                            nights=nights,
                            comp_meta=meta,
                            use_cache=use_cache,
                            max_cache_age_hours=max_cache_age_hours,
                        ),
                        timeout=35.0,
                    )
                except (asyncio.TimeoutError, Exception) as exc:
                    logger.debug(f"Direct comp fallback timed out or failed for {cid}: {exc}")
                    return None

        tasks = [_check_comp(cid, meta) for cid, meta in missing_comps.items()]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        available_comps = []
        for r in results:
            if r and isinstance(r, dict) and r.get("effective_nightly"):
                available_comps.append(r)
        return available_comps

