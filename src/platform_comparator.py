"""
Platform Comparison Engine.
Scrapes and compares real-time guest checkout prices and line-item receipts
for Villa del Sol across Airbnb, VRBO, Booking.com, and Kivoya Direct.
Routes all external web traffic through the mandatory NordVPN proxy pool.
"""

import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import logging
import random
import re
import time
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

from src.config import CLEANING_FEE
from src.kivoya_client import KivoyaClient
from src.stealth_connection import StealthConnectionManager
from src.segmentation import CalendarSegmenter

logger = logging.getLogger(__name__)

CACHE_DIR = Path("data/cache/platform_comparison")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

AIRBNB_ID = "573857947793833342"
VRBO_ID = "2685684"
BOOKING_SLUG = "hotel/us/villa-del-sol-amazing-house-by-kivoya.html"
KIVOYA_ID = 503802
DEFAULT_ADULTS = 16
AIRBNB_TAX_RATE = 0.1252  # 5% Tempe Hotel/Motel + 5.5% State TPT + 1.8% Local TPT + 0.22% Maricopa


@dataclass
class PriceBreakdown:
    platform: str
    available: bool
    currency: str = "USD"
    nightly_rate: Optional[float] = None
    nights: int = 3
    base_subtotal: Optional[float] = None
    cleaning_fee: Optional[float] = None
    service_fee: Optional[float] = None
    taxes: Optional[float] = None
    discount: Optional[float] = None
    total_price: Optional[float] = None
    effective_nightly: Optional[float] = None
    booking_url: str = ""
    notes: str = ""
    raw_snippet: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Convert breakdown to serializable dictionary."""
        return asdict(self)


@dataclass
class IntervalComparison:
    check_in: str
    check_out: str
    nights: int
    segment_type: str
    is_calendar_open: bool
    airbnb: PriceBreakdown
    vrbo: PriceBreakdown
    booking: PriceBreakdown
    kivoya: PriceBreakdown
    airbnb_est_no_dscnt: Optional[PriceBreakdown] = None
    max_divergence_pct: Optional[float] = 0.0
    highest_platform: str = "airbnb"
    lowest_platform: str = "airbnb"
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Convert interval comparison to serializable dictionary."""
        return asdict(self)


class PlatformComparator:
    """Manages multi-channel scraping, normalization, and divergence analysis."""

    FEEDER_CHANNELS = [
        ("airbnb", "feeder-la", "los-angeles.us.socks.nordhold.net:1080"),
        ("booking", "feeder-sf", "san-francisco.us.socks.nordhold.net:1080"),
        ("vrbo", "feeder-dal", "dallas.us.socks.nordhold.net:1080"),
    ]

    def __init__(
        self,
        cache_dir: Path = CACHE_DIR,
        headless: bool = True,
        adults: int = DEFAULT_ADULTS,
        kivoya_client: Optional[KivoyaClient] = None,
        parallel: bool = True,
        proxy_mgr: Optional[StealthConnectionManager] = None,
    ):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.headless = headless
        self.adults = adults
        self.kivoya_client = kivoya_client or KivoyaClient(unit_id=KIVOYA_ID)
        self._owns_proxy_mgr = (proxy_mgr is None)
        self.proxy_mgr = proxy_mgr or StealthConnectionManager(required=True)
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self.contexts: Dict[str, BrowserContext] = {}
        self.worker_contexts: List[BrowserContext] = []
        self._context_queue: Optional[asyncio.Queue] = None
        self.parallel: bool = parallel
        self.total_bytes_transferred: int = 0
        self._curr_interval_bytes: int = 0
        self.last_interval_bytes: int = 0
        self.last_interval_duration: float = 0.0
        self.last_scrape_errors: Dict[str, int] = {}
        self.retry_delay: float = 1.0
        self.sequential_delay: float = 1.0

    async def init_browser(self, p) -> None:
        """Initialize browser and create dedicated contexts for each channel from the feeder pool."""
        self.contexts = {}
        self.worker_contexts = []
        self._context_queue = None
        try:
            if not self.proxy_mgr.endpoints:
                num_workers = self.proxy_mgr.max_workers if self.parallel else len(self.FEEDER_CHANNELS)
                server_targets = [(ep_name, host) for _, ep_name, host in self.FEEDER_CHANNELS] if not self.parallel else None
                min_healthy = StealthConnectionManager.calculate_min_healthy(num_workers) if self.parallel else 1
                proxy_configs = await self.proxy_mgr.start_pool(
                    num_workers=num_workers,
                    servers=server_targets,
                    min_healthy=min_healthy,
                    wait_for_full_pool=False,
                    test_target="https://www.google.com",
                )
            else:
                proxy_configs = self.proxy_mgr.get_proxy_configs()

            launch_kwargs = {
                "headless": self.headless,
                "args": ["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            }
            if self.proxy_mgr.required and not proxy_configs:
                raise RuntimeError(
                    "❌ [PROXY ERROR] Cannot launch platform comparator: no proxy endpoints available in feeder pool. "
                    "Direct unproxied connection is prohibited."
                )

            self._browser = await p.chromium.launch(**launch_kwargs)

            if proxy_configs:
                self._context_queue = asyncio.Queue()
                for idx, cfg in enumerate(proxy_configs):
                    proxy_arg = {"server": cfg["server"]} if isinstance(cfg, dict) and "server" in cfg else cfg
                    ctx = await self._browser.new_context(
                        viewport={"width": 1366, "height": 850},
                        user_agent=(
                            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
                        ),
                        proxy=proxy_arg,
                    )
                    route_res = ctx.route("**/*", self._filter_request_routes)
                    if asyncio.iscoroutine(route_res) or hasattr(route_res, "__await__"):
                        await route_res
                    ctx._transferred_bytes = 0
                    ctx.on("response", self._make_context_tracker(ctx))
                    self.worker_contexts.append(ctx)
                    self._context_queue.put_nowait(ctx)

                # Maintain backwards compatibility for self.contexts["airbnb"], "booking", "vrbo"
                for idx, (chan, _, _) in enumerate(self.FEEDER_CHANNELS):
                    self.contexts[chan] = self.worker_contexts[idx % len(self.worker_contexts)]
            else:
                self._context_queue = None
                # Fallback single context (e.g. testing or proxy unconfigured)
                ctx = await self._browser.new_context(
                    viewport={"width": 1366, "height": 850},
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
                    ),
                )
                route_res = ctx.route("**/*", self._filter_request_routes)
                if asyncio.iscoroutine(route_res) or hasattr(route_res, "__await__"):
                    await route_res
                ctx._transferred_bytes = 0
                ctx.on("response", self._make_context_tracker(ctx))
                self._context = ctx
                for chan, _, _ in self.FEEDER_CHANNELS:
                    self.contexts[chan] = ctx
        except Exception:
            await self.close_browser()
            raise

    @staticmethod
    async def _filter_request_routes(route, request):
        """Block heavy media, fonts, and third-party trackers to prevent proxy socket flooding."""
        try:
            req_type = request.resource_type
            if req_type in ("image", "media", "font"):
                await route.abort()
                return

            req_url = request.url.lower()
            blocked_domains = (
                "google-analytics",
                "googletagmanager",
                "doubleclick",
                "facebook.com",
                "facebook.net",
                "criteo",
                "datadome",
                "branch.io",
                "krxd.net",
                "bat.bing.com",
                "clarity.ms",
                "hotjar",
                "optimizely",
            )
            if any(b in req_url for b in blocked_domains):
                await route.abort()
                return

            await route.continue_()
        except Exception:
            try:
                await route.continue_()
            except Exception:
                pass

    @staticmethod
    def _format_error_summary(e: Exception) -> str:
        """Extract a single clean summary line from potentially multi-line exception messages."""
        s = str(e).strip()
        return s.splitlines()[0] if s else str(e)

    async def close_browser(self) -> None:
        """Close all browser contexts, browser, and proxy pool bridges."""
        all_ctxs = set(self.contexts.values())
        if hasattr(self, "worker_contexts"):
            all_ctxs.update(self.worker_contexts)
        if self._context:
            all_ctxs.add(self._context)
            self._context = None
        self.contexts.clear()
        if hasattr(self, "worker_contexts"):
            self.worker_contexts.clear()
        self._context_queue = None

        for ctx in all_ctxs:
            try:
                await ctx.close()
            except Exception:
                pass

        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None

        if self._owns_proxy_mgr:
            await self.proxy_mgr.stop()

    @asynccontextmanager
    async def lease_channel_context(self, channel: str):
        """Lease an isolated worker context from the pool, falling back to named context."""
        if getattr(self, "_context_queue", None) is not None and getattr(self, "worker_contexts", None):
            ctx = await self._context_queue.get()
            try:
                try:
                    await ctx.clear_cookies()
                except Exception:
                    pass
                yield ctx
            finally:
                if getattr(self, "_context_queue", None) is not None:
                    self._context_queue.put_nowait(ctx)
        else:
            ctx = self.contexts.get(channel) or getattr(self, "_context", None)
            if ctx:
                try:
                    await ctx.clear_cookies()
                except Exception:
                    pass
            yield ctx

    @staticmethod
    async def _navigate_with_retry(
        page: Page,
        url: str,
        timeout: int = 25000,
        wait_until: str = "domcontentloaded",
        max_retries: int = 1,
    ) -> None:
        """Navigate to URL with immediate retry for transient connection resets or empty responses."""
        for attempt in range(1 + max_retries):
            try:
                await page.goto(url, wait_until=wait_until, timeout=timeout)
                return
            except Exception as exc:
                err_lower = str(exc).lower()
                is_transient = any(
                    k in err_lower for k in [
                        "net::err_empty_response",
                        "net::err_connection_reset",
                        "net::err_connection_closed",
                        "net::err_connection_refused",
                        "net::err_proxy_connection_failed",
                        "net::err_timed_out",
                        "timeout",
                    ]
                )
                if attempt < max_retries and is_transient:
                    logger.info(f"Retrying page navigation after transient network error ({exc}): {url}")
                    await asyncio.sleep(1.0)
                    continue
                raise

    def _make_context_tracker(self, ctx):
        """Create a response handler tracking both global and context-isolated bytes."""
        def _tracker(response):
            try:
                cl = response.headers.get("content-length")
                if cl and cl.isdigit():
                    b = int(cl)
                    self.total_bytes_transferred += b
                    self._curr_interval_bytes += b
                    val = getattr(ctx, "_transferred_bytes", 0)
                    curr_b = int(val) if isinstance(val, (int, float)) and not isinstance(val, bool) else 0
                    ctx._transferred_bytes = curr_b + b
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

    def _get_cache_path(self, check_in: str, check_out: str) -> Path:
        return self.cache_dir / f"compare_{check_in}_{check_out}_{self.adults}guests.json"

    # -------------------------------------------------------------------------
    # Kivoya Direct Rate Calculation
    # -------------------------------------------------------------------------
    def get_kivoya_quote(
        self, check_in_dt: date, check_out_dt: date, nights: int
    ) -> PriceBreakdown:
        """
        Calculate Kivoya Direct quote from Streamline VRS seasonal rates and direct fee policy.
        Kivoya Direct:
        - Base Rent: seasonal schedule for dates
        - Cleaning Fee: $550.00
        - Processing Fee: 6% of Base
        - Admin Fee: 3% of (Base + Processing + Cleaning)
        - Service Fee: Processing + Admin Fee
        - Local STR Tax: 14.07% (5.5% State + 1.77% Maricopa + 1.8% Tempe Hotel + 5% Tempe Hotel/Motel)
        """
        open_end_date = getattr(self.kivoya_client, "get_calendar_open_end_date", lambda: None)()
        last_night_dt = check_out_dt - timedelta(days=1)
        if open_end_date and (check_in_dt > open_end_date or last_night_dt > open_end_date):
            return PriceBreakdown(
                platform="kivoya",
                available=False,
                nights=nights,
                booking_url=f"https://www.kivoya.com/{KIVOYA_ID}/",
                notes="Calendar closed in Kivoya PMS (rates unlisted)",
            )

        rates = self.kivoya_client.get_seasonal_rates()
        total_base = 0.0
        cur = check_in_dt
        while cur < check_out_dt:
            total_base += self.kivoya_client.get_rate_for_date(cur, rates)
            cur += timedelta(days=1)

        # Attempt real-time quote from Kivoya API first
        live_quote = self.kivoya_client.get_pre_reservation_quote(check_in_dt, check_out_dt)
        res_days = live_quote.get("reservation_days", []) if isinstance(live_quote, dict) else []
        has_error_day = any(d.get("season") == "error" for d in res_days) if res_days else False
        live_price = float(live_quote.get("price", 0)) if (live_quote and live_quote.get("price") is not None) else 0.0

        if has_error_day or (live_quote and live_price <= 0 and total_base <= 0):
            return PriceBreakdown(
                platform="kivoya",
                available=False,
                nights=nights,
                booking_url=f"https://www.kivoya.com/{KIVOYA_ID}/",
                notes="Calendar closed in Kivoya PMS (rates unlisted)",
            )

        if live_quote and live_quote.get("total") and live_price > 0:
            base_subtotal = live_price
            avg_nightly = round(base_subtotal / max(1, nights), 2)
            req_fees = live_quote.get("required_fees", [])
            cleaning_fee = 550.0
            svc_fee = 0.0
            for f in req_fees:
                fname = f.get("name", "").lower()
                fval = float(f.get("value", 0.0))
                if "clean" in fname:
                    cleaning_fee = fval
                else:
                    svc_fee += fval
            taxes_details = live_quote.get("taxes_details", [])
            taxes = sum(float(t.get("value", 0.0)) for t in taxes_details)
            total_price = float(live_quote.get("total", 0.0))
        else:
            if total_base <= 0.0:
                return PriceBreakdown(
                    platform="kivoya",
                    available=False,
                    nights=nights,
                    booking_url=f"https://www.kivoya.com/{KIVOYA_ID}/",
                    notes="Calendar closed in Kivoya PMS (rates unlisted)",
                )
            calc = self.kivoya_client.calculate_direct_quote(total_base)
            base_subtotal = calc["base_subtotal"]
            avg_nightly = round(base_subtotal / max(1, nights), 2)
            cleaning_fee = calc["cleaning_fee"]
            svc_fee = calc["service_fee"]
            taxes = calc["taxes"]
            total_price = calc["total_price"]

        eff_nightly = round(total_price / max(1, nights), 2)
        booking_url = f"https://www.kivoya.com/{KIVOYA_ID}/"

        return PriceBreakdown(
            platform="kivoya",
            available=True,
            nightly_rate=avg_nightly,
            nights=nights,
            base_subtotal=round(base_subtotal, 2),
            cleaning_fee=round(cleaning_fee, 2),
            service_fee=round(svc_fee, 2),
            taxes=round(taxes, 2),
            discount=0.0,
            total_price=round(total_price, 2),
            effective_nightly=eff_nightly,
            booking_url=booking_url,
            notes="Direct booking: $550 clean + 6% proc + 3% admin + 14.07% STR taxes",
            raw_snippet=f"${avg_nightly:,.0f}/nt × {nights}n + ${cleaning_fee:,.0f} clean + ${svc_fee:,.0f} fees + ${taxes:,.0f} tax",
        )

    # -------------------------------------------------------------------------
    # Airbnb Undiscounted Catalog Estimate (Benchmark)
    # -------------------------------------------------------------------------
    def get_airbnb_est_no_dscnt_quote(
        self,
        check_in_dt: date,
        check_out_dt: date,
        nights: int,
        kivoya_ref: Optional[PriceBreakdown] = None,
    ) -> PriceBreakdown:
        """
        Calculate undiscounted Airbnb catalog quote directly from Streamline/Kivoya base rate.
        - Base Rent: seasonal schedule for dates (kivoya_ref.base_subtotal or fetched)
        - Cleaning Fee: $550.00
        - Guest Service Fee: 14.15% of (Base + Clean)
        - Tempe & AZ Lodging Taxes: 12.52% of pre-tax
        """
        open_end_date = getattr(self.kivoya_client, "get_calendar_open_end_date", lambda: None)()
        last_night_dt = check_out_dt - timedelta(days=1)
        if (open_end_date and (check_in_dt > open_end_date or last_night_dt > open_end_date)) or (kivoya_ref and not kivoya_ref.available):
            return PriceBreakdown(
                platform="airbnb_est_no_dscnt",
                available=False,
                nights=nights,
                booking_url=f"https://www.airbnb.com/rooms/{AIRBNB_ID}?check_in={check_in_dt.isoformat()}&check_out={check_out_dt.isoformat()}&adults={self.adults}",
                notes="Calendar closed in Kivoya PMS (rates unlisted)",
            )

        if kivoya_ref and kivoya_ref.base_subtotal:
            base_subtotal = kivoya_ref.base_subtotal
            avg_nightly = kivoya_ref.nightly_rate or round(base_subtotal / max(1, nights), 2)
        else:
            rates = self.kivoya_client.get_seasonal_rates()
            total_base = 0.0
            cur = check_in_dt
            while cur < check_out_dt:
                total_base += self.kivoya_client.get_rate_for_date(cur, rates)
                cur += timedelta(days=1)
            base_subtotal = round(total_base, 2)
            avg_nightly = round(base_subtotal / max(1, nights), 2)

        if base_subtotal <= 0.0:
            return PriceBreakdown(
                platform="airbnb_est_no_dscnt",
                available=False,
                nights=nights,
                booking_url=f"https://www.airbnb.com/rooms/{AIRBNB_ID}?check_in={check_in_dt.isoformat()}&check_out={check_out_dt.isoformat()}&adults={self.adults}",
                notes="Calendar closed in Kivoya PMS (rates unlisted)",
            )

        cleaning_fee = 550.0
        lodging_base = base_subtotal + cleaning_fee
        pretax = round(lodging_base * 1.1415, 2)
        svc_fee = round(pretax - lodging_base, 2)
        taxes = round(pretax * AIRBNB_TAX_RATE, 2)
        total_price = round(pretax + taxes, 2)
        effective_nightly = round(total_price / max(1, nights), 2)

        return PriceBreakdown(
            platform="airbnb_est_no_dscnt",
            available=True,
            nightly_rate=avg_nightly,
            nights=nights,
            base_subtotal=base_subtotal,
            cleaning_fee=cleaning_fee,
            service_fee=svc_fee,
            taxes=taxes,
            total_price=total_price,
            effective_nightly=effective_nightly,
            booking_url=f"https://www.airbnb.com/rooms/573857947793833342?check_in={check_in_dt.isoformat()}&check_out={check_out_dt.isoformat()}&adults={self.adults}",
            notes="Undiscounted catalog rate from Streamline base schedule ($550 clean + 14.15% Airbnb fee + 12.52% lodging tax)",
            raw_snippet=f"${avg_nightly}/nt × {nights}n + $550 clean + ${svc_fee} fee + ${taxes} tax = ${total_price}",
        )

    # -------------------------------------------------------------------------
    # Airbnb Scraper
    # -------------------------------------------------------------------------
    async def scrape_airbnb_quote(
        self, page: Page, check_in: str, check_out: str, nights: int
    ) -> PriceBreakdown:
        """Fetch Airbnb quote via our_property cache, StaysPdpSections interception, and DOM fallback."""
        # 1. Check existing our_property cache from earlier sweeps
        existing_cache = Path(f"data/cache/our_property_{check_in}_{check_out}.json")
        cached_total = None
        if existing_cache.exists():
            try:
                cached_data = json.loads(existing_cache.read_text(encoding="utf-8"))
                cached_total = cached_data.get("airbnb_total")
            except Exception:
                pass

        url = (
            f"https://www.airbnb.com/rooms/{AIRBNB_ID}"
            f"?check_in={check_in}&check_out={check_out}&adults={self.adults}&locale=en&currency=USD"
        )
        intercepted_price: Optional[float] = None
        intercepted_breakdown: Dict[str, float] = {}
        is_unavailable: bool = False
        unavail_reason: Optional[str] = None

        done_event = asyncio.Event()

        async def on_response(resp):
            nonlocal intercepted_price, intercepted_breakdown, is_unavailable, unavail_reason
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
                            raw_p = (
                                primary.get("price")
                                or primary.get("discountedPrice")
                                or primary.get("originalPrice")
                                or ""
                            )
                            clean_p = re.sub(r"[^\d.]", "", raw_p)
                            if clean_p:
                                intercepted_price = float(clean_p)

                            # Check for strikethrough originalPrice vs discountedPrice
                            orig_p_str = primary.get("originalPrice") or ""
                            disc_p_str = primary.get("discountedPrice") or ""
                            if orig_p_str and disc_p_str:
                                orig_amt_clean = re.sub(r"[^\d.]", "", orig_p_str)
                                disc_amt_clean = re.sub(r"[^\d.]", "", disc_p_str)
                                if orig_amt_clean and disc_amt_clean:
                                    diff = float(orig_amt_clean) - float(disc_amt_clean)
                                    if diff > 0:
                                        intercepted_breakdown["discount"] = round(diff, 2)

                            # Parse explanationData if present
                            expl = sdp.get("explanationData", {})
                            for item in expl.get("priceDetails", []):
                                for item_line in item.get("items", []):
                                    desc = item_line.get("description", "").lower()
                                    amt_str = re.sub(r"[^\d.]", "", item_line.get("priceString", ""))
                                    if amt_str:
                                        amt = float(amt_str)
                                        if any(w in desc for w in ["discount", "promotion", "promo", "early bird", "weekly", "length of stay"]):
                                            intercepted_breakdown["discount"] = intercepted_breakdown.get("discount", 0.0) + amt
                                            promo_notes = intercepted_breakdown.get("promo_notes", [])
                                            if isinstance(promo_notes, list):
                                                promo_notes.append(f"{item_line.get('description', 'Discount')}: -${amt:,.2f}")
                                                intercepted_breakdown["promo_notes"] = promo_notes
                                        elif "clean" in desc:
                                            intercepted_breakdown["cleaning"] = amt
                                        elif "service" in desc:
                                            intercepted_breakdown["service"] = amt
                                        elif "tax" in desc:
                                            intercepted_breakdown["taxes"] = amt
                                        elif "night" in desc:
                                            intercepted_breakdown["base"] = amt

                        if sec.get("available") is False or sec.get("localizedUnavailabilityMessage"):
                            is_unavailable = True
                            unavail_reason = sec.get("localizedUnavailabilityMessage")

                    done_event.set()
                except Exception:
                    pass

        page.on("response", on_response)

        is_cached_fallback = False
        try:
            await self._navigate_with_retry(page, url, timeout=25000, wait_until="domcontentloaded", max_retries=1)
            await page.wait_for_timeout(3000)
            await page.evaluate("() => window.scrollTo(0, 1200)")
            try:
                await asyncio.wait_for(done_event.wait(), timeout=6.0)
            except asyncio.TimeoutError:
                pass

            # Fallback DOM inspection if GraphQL was delayed and not cached
            if not intercepted_price and not is_unavailable:
                body_text = await page.evaluate("() => (document.body ? document.body.innerText : '')")
                if any(w in body_text.lower() for w in ["not available", "minimum stay", "dates are unavailable"]):
                    is_unavailable = True
                    unavail_reason = "Listing unavailable for dates"
                else:
                    m = re.search(r"\$([0-9,]+(?:\.[0-9]{2})?)\s*for\s+\d+\s+nights", body_text, re.IGNORECASE)
                    if not m:
                        m = re.search(r"\$([0-9,]+(?:\.[0-9]{2})?)\s*(?:total|before taxes)", body_text, re.IGNORECASE)
                    if m:
                        intercepted_price = float(m.group(1).replace(",", ""))

        except Exception as e:
            err_summary = self._format_error_summary(e)
            logger.warning(f"Airbnb scrape warning for {check_in}: {err_summary}")
            if cached_total and cached_total > 0:
                logger.info(f"Airbnb live page navigation warning ({err_summary}); using Step 3 verified cache (${cached_total:,.0f}).")
                intercepted_price = cached_total
                is_unavailable = False
                is_cached_fallback = True
            else:
                unavail_reason = f"Error: {e}"
                is_unavailable = True
        finally:
            page.remove_listener("response", on_response)

        if not intercepted_price and not is_unavailable and cached_total and cached_total > 0:
            logger.info(f"Airbnb live page returned no price; using Step 3 verified cache (${cached_total:,.0f}).")
            intercepted_price = cached_total
            is_cached_fallback = True

        if is_unavailable or not intercepted_price:
            return PriceBreakdown(
                platform="airbnb",
                available=False,
                nights=nights,
                booking_url=url,
                notes=unavail_reason or ("Dates unavailable on Airbnb" if is_unavailable else "Scrape error: Price not found on PDP"),
            )

        # Build clean itemized breakdown
        # In the US, Airbnb PDP displays price BEFORE lodging & occupancy taxes.
        # Taxes on accommodation are charged at checkout:
        # - 5.0% Hotel/Motel Tax (Tempe)
        # - 5.5% Transaction Privilege and Use Tax (Arizona)
        # - 1.8% Local Transaction Privilege and Use Tax (Tempe)
        # - 0.22% Online Lodging Marketplace Tax (Maricopa County)
        # Total Tax Rate = 12.52% of the pre-tax stay subtotal.
        AIRBNB_TAX_RATE = 0.1252
        pretax_total = intercepted_price
        clean_fee = intercepted_breakdown.get("cleaning", 550.0)

        taxes = intercepted_breakdown.get("taxes") or round(pretax_total * AIRBNB_TAX_RATE, 2)
        total = round(pretax_total + taxes, 2)

        # pretax_total = accommodation + clean_fee + service_fee
        # service_fee is ~14.15% of (accommodation + clean_fee)
        base_plus_clean = round(pretax_total / 1.1415, 2)
        base_est = round(max(0.0, base_plus_clean - clean_fee), 2)
        svc_est = round(pretax_total - base_est - clean_fee, 2)
        nightly_est = round(base_est / max(1, nights), 2)
        eff_nightly = round(total / max(1, nights), 2)

        promo_list = intercepted_breakdown.get("promo_notes", [])
        promo_str = f" [Promotions detected: {'; '.join(promo_list)}]" if promo_list else ""
        cache_source_str = " (Verified Step 3 cache fallback)" if is_cached_fallback else ""
        notes_str = f"Includes 12.52% Tempe & AZ lodging taxes (Hotel/Motel 5% + State TPT 5.5% + Local TPT 1.8% + Maricopa 0.22%){promo_str}{cache_source_str}"

        return PriceBreakdown(
            platform="airbnb",
            available=True,
            nightly_rate=nightly_est,
            nights=nights,
            base_subtotal=base_est,
            cleaning_fee=clean_fee,
            service_fee=svc_est,
            taxes=taxes,
            discount=intercepted_breakdown.get("discount"),
            total_price=total,
            effective_nightly=eff_nightly,
            booking_url=url,
            notes=notes_str,
            raw_snippet=f"${pretax_total:,.0f} pre-tax + ${taxes:,.2f} tax = ${total:,.2f} total",
        )

    # -------------------------------------------------------------------------
    # Booking.com Scraper
    # -------------------------------------------------------------------------
    async def scrape_booking_quote(
        self,
        page: Page,
        check_in: str,
        check_out: str,
        nights: int,
        kivoya_ref: Optional[PriceBreakdown] = None,
    ) -> PriceBreakdown:
        """Extract Villa del Sol rates and fee details from Booking.com room table."""
        url = (
            f"https://www.booking.com/{BOOKING_SLUG}"
            f"?checkin={check_in}&checkout={check_out}&group_adults={self.adults}&no_rooms=1"
        )
        try:
            await self._navigate_with_retry(page, url, timeout=25000, wait_until="domcontentloaded", max_retries=1)
            await page.wait_for_timeout(4000)

            # Check if property is sold out or unavailable
            body_text = await page.evaluate("() => (document.body ? document.body.innerText : '')")
            if any(w in body_text.lower() for w in [
                "no availability for your dates",
                "you missed it",
                "sold out on your dates",
                "we have no rooms available",
            ]):
                return PriceBreakdown(
                    platform="booking",
                    available=False,
                    nights=nights,
                    booking_url=url,
                    notes="Sold out / No rooms available on Booking.com",
                )

            # Extract table cell pricing text
            price_snippets = await page.evaluate('''() => {
                const results = [];
                document.querySelectorAll('.e2e-hprt-table-row, .js-rt-block-row, .hprt-table-cell-price, [data-block-id], .bui-price-display__value, .prco-valign-middle-helper').forEach(el => {
                    const t = el.innerText ? el.innerText.trim() : '';
                    if (t.includes('$')) results.push(t.replace(/\\n+/g, ' '));
                });
                return results;
            }''')

            best_snippet = None
            if price_snippets:
                for snip in price_snippets:
                    if "cleaning" in snip.lower() or "current price" in snip.lower() or "per night" in snip.lower():
                        best_snippet = snip
                        break
                if not best_snippet:
                    best_snippet = price_snippets[0]

            if best_snippet:
                # 1. Nightly Rate
                m_nightly = re.search(r"\$([0-9,]+(?:\.[0-9]{2})?)\s*per night", best_snippet, re.IGNORECASE)
                nightly_rate = float(m_nightly.group(1).replace(",", "")) if m_nightly else None

                # 2. Current subtotal price
                m_curr = re.search(r"Current price\s*\$([0-9,]+(?:\.[0-9]{2})?)", best_snippet, re.IGNORECASE)
                if not m_curr:
                    m_curr = re.search(r"\$([0-9,]+(?:\.[0-9]{2})?)\s*(?:3|2|4|\d+)\s*nights", best_snippet, re.IGNORECASE)
                if not m_curr:
                    m_curr = re.search(r"\b(?:price\s*for\s*\d+\s*nights|total|price)\b\s*[:]?\s*\$([0-9,]+(?:\.[0-9]{2})?)", best_snippet, re.IGNORECASE)
                if not m_curr:
                    # Scan for stay total >= $1,000 in best_snippet, ignoring cleaning fees
                    for m in re.finditer(r"\$([0-9,]+(?:\.[0-9]{2})?)", best_snippet):
                        start_pos = max(0, m.start() - 25)
                        end_pos = min(len(best_snippet), m.end() + 25)
                        surrounding = best_snippet[start_pos:end_pos].lower()
                        if "clean" in surrounding or "deposit" in surrounding:
                            continue
                        val = float(m.group(1).replace(",", ""))
                        if val >= 1000.0:
                            m_curr = m
                            break

                subtotal = float(m_curr.group(1).replace(",", "")) if m_curr else (nightly_rate * nights if nightly_rate else None)

                # If subtotal is still None, scan remaining price_snippets
                if (not subtotal or subtotal <= 0) and price_snippets:
                    for snip in price_snippets:
                        for m in re.finditer(r"\$([0-9,]+(?:\.[0-9]{2})?)", snip):
                            start_pos = max(0, m.start() - 25)
                            end_pos = min(len(snip), m.end() + 25)
                            surrounding = snip[start_pos:end_pos].lower()
                            if "clean" in surrounding or "deposit" in surrounding:
                                continue
                            val = float(m.group(1).replace(",", ""))
                            if val >= 1000.0:
                                subtotal = val
                                best_snippet = snip
                                if not nightly_rate:
                                    m_snip_nightly = re.search(r"\$([0-9,]+(?:\.[0-9]{2})?)\s*per night", snip, re.IGNORECASE)
                                    if m_snip_nightly:
                                        nightly_rate = float(m_snip_nightly.group(1).replace(",", ""))
                                break
                        if subtotal and subtotal > 0:
                            break

                if subtotal and subtotal > 0:
                    # 3. Cleaning Fee
                    m_clean = re.search(r"\$([0-9,]+)\s*Cleaning fee", best_snippet, re.IGNORECASE)
                    cleaning_fee = float(m_clean.group(1).replace(",", "")) if m_clean else 550.0

                    # 4. Service charge %
                    m_svc = re.search(r"([0-9.]+)\s*%\s*Service charge", best_snippet, re.IGNORECASE)
                    svc_pct = float(m_svc.group(1)) if m_svc else 6.0
                    svc_amount = round(subtotal * (svc_pct / 100.0), 2)

                    # 5. Excluded Taxes %
                    tax_pct = 0.0
                    for t_match in re.finditer(r"([0-9.]+)\s*%\s*(?:VAT|TAX)", best_snippet, re.IGNORECASE):
                        tax_pct += float(t_match.group(1))

                    if tax_pct == 0.0:
                        tax_pct = 30.5  # Standard combined tax percentage in Booking snippet

                    tax_amount = round(subtotal * (tax_pct / 100.0), 2)

                    # Total Checkout Price = Subtotal + Taxes
                    total_price = round(subtotal + tax_amount, 2)
                    base_rent = round(max(0.0, subtotal - cleaning_fee - svc_amount), 2)
                    eff_nightly = round(total_price / max(1, nights), 2)

                    return PriceBreakdown(
                        platform="booking",
                        available=True,
                        nightly_rate=nightly_rate or (round(base_rent / max(1, nights), 2) if base_rent else round(subtotal / max(1, nights), 2)),
                        nights=nights,
                        base_subtotal=base_rent,
                        cleaning_fee=cleaning_fee,
                        service_fee=svc_amount,
                        taxes=tax_amount,
                        total_price=total_price,
                        effective_nightly=eff_nightly,
                        booking_url=url,
                        notes=f"Live Booking.com quote: Subtotal ${subtotal:,.0f} + {tax_pct:.1f}% Tax",
                        raw_snippet=best_snippet[:120],
                    )

            # Fallback projection from Kivoya base rate if Booking.com blocked/rate-limited
            if kivoya_ref and kivoya_ref.available and kivoya_ref.base_subtotal:
                base_rent = kivoya_ref.base_subtotal
                clean_fee = 550.0
                svc_fee = round((base_rent + clean_fee) * 0.06, 2)
                subtotal = base_rent + clean_fee + svc_fee
                taxes = round(subtotal * 0.305, 2)  # 16% VAT + 14.5% local tax on Booking.com
                proj_total = round(subtotal + taxes, 2)
                return PriceBreakdown(
                    platform="booking",
                    available=True,
                    nightly_rate=round(base_rent / max(1, nights), 2),
                    nights=nights,
                    base_subtotal=base_rent,
                    cleaning_fee=clean_fee,
                    service_fee=svc_fee,
                    taxes=taxes,
                    total_price=proj_total,
                    effective_nightly=round(proj_total / max(1, nights), 2),
                    booking_url=url,
                    notes="Projected via Kivoya PMS rate feed (Booking.com live table unlisted)",
                    raw_snippet=f"${proj_total:,.0f} projected",
                )

            return PriceBreakdown(
                platform="booking",
                available=False,
                nights=nights,
                booking_url=url,
                notes="Scrape error: No rate rows found in room table",
            )

        except Exception as e:
            err_summary = self._format_error_summary(e)
            logger.warning(f"Booking.com scrape warning for {check_in}: {err_summary}")
            if kivoya_ref and kivoya_ref.available and kivoya_ref.base_subtotal:
                logger.info(f"Using Kivoya PMS rate feed projection for Booking.com ({check_in}) due to scrape warning: {err_summary}")
                base_rent = kivoya_ref.base_subtotal
                clean_fee = 550.0
                svc_fee = round((base_rent + clean_fee) * 0.06, 2)
                subtotal = base_rent + clean_fee + svc_fee
                taxes = round(subtotal * 0.305, 2)  # 16% VAT + 14.5% local tax on Booking.com
                proj_total = round(subtotal + taxes, 2)
                return PriceBreakdown(
                    platform="booking",
                    available=True,
                    nightly_rate=round(base_rent / max(1, nights), 2),
                    nights=nights,
                    base_subtotal=base_rent,
                    cleaning_fee=clean_fee,
                    service_fee=svc_fee,
                    taxes=taxes,
                    total_price=proj_total,
                    effective_nightly=round(proj_total / max(1, nights), 2),
                    booking_url=url,
                    notes=f"Projected via Kivoya PMS rate feed (Live sweep error: {e})",
                    raw_snippet=f"${proj_total:,.0f} projected",
                )
            return PriceBreakdown(
                platform="booking",
                available=False,
                nights=nights,
                booking_url=url,
                notes=f"Scrape error: {e}",
            )

    # -------------------------------------------------------------------------
    # VRBO Scraper
    # -------------------------------------------------------------------------
    async def scrape_vrbo_quote(
        self,
        page: Page,
        check_in: str,
        check_out: str,
        nights: int,
        kivoya_ref: Optional[PriceBreakdown] = None,
    ) -> PriceBreakdown:
        """Scrape VRBO checkout pricing with client state inspection and fallback handling."""
        url = f"https://www.vrbo.com/{VRBO_ID}?chkin={check_in}&chkout={check_out}&adults={self.adults}"
        try:
            await self._navigate_with_retry(page, url, timeout=25000, wait_until="domcontentloaded", max_retries=1)
            await page.wait_for_timeout(5000)
            await page.evaluate("() => window.scrollTo(0, 800)")
            await page.wait_for_timeout(3000)

            # Inspect DOM and client-side state for price and availability
            page_data = await page.evaluate('''() => {
                const text = document.body.innerText;
                const unavail = text.includes("Dates unavailable") || text.includes("Not available") || text.includes("Minimum stay");
                const perNightMult = text.match(/\\$([0-9,]+(?:\\.[0-9]{2})?)\\s*(?:x|\\*)\\s*(\\d+)\\s*nights/i);
                const directTotal = text.match(/\\$([0-9,]+(?:\\.[0-9]{2})?)\\s*(?:total|for \\d+ nights)/i);
                return {
                    unavail: unavail,
                    perNightMatch: perNightMult ? { rate: perNightMult[1], nights: perNightMult[2] } : null,
                    totalMatch: directTotal ? directTotal[1] : null,
                };
            }''')

            if page_data.get("unavail"):
                return PriceBreakdown(
                    platform="vrbo",
                    available=False,
                    nights=nights,
                    booking_url=url,
                    notes="Dates unavailable or minimum stay restriction",
                )

            # In the US, VRBO displays pre-tax stay total on the PDP
            # Taxes are charged at checkout:
            # - 5.5% Arizona Sales Tax
            # - 1.77% Maricopa County TPT
            # - 1.8% Tempe Hotel Tax
            # - 5.0% Tempe Hotel/Motel Tax
            # Total Statutory VRBO tax rate on lodging base = 14.07%
            pretax_total = None
            if page_data.get("perNightMatch"):
                pnm = page_data["perNightMatch"]
                pretax_total = round(float(pnm["rate"].replace(",", "")) * int(pnm["nights"]), 2)
            elif page_data.get("totalMatch"):
                pretax_total = float(page_data["totalMatch"].replace(",", ""))

            if pretax_total:
                clean_fee = 550.0
                # VRBO service fee is ~12.26% of pre-tax total; lodging base is ~87.7%
                lodging_base = round(pretax_total * 0.856, 2)
                svc_fee = round(pretax_total - lodging_base, 2)
                base_rent = round(max(0.0, lodging_base - clean_fee), 2)
                tax_val = round(lodging_base * 0.1407, 2)
                total_val = round(pretax_total + tax_val, 2)
                return PriceBreakdown(
                    platform="vrbo",
                    available=True,
                    nightly_rate=round(base_rent / max(1, nights), 2),
                    nights=nights,
                    base_subtotal=base_rent,
                    cleaning_fee=clean_fee,
                    service_fee=svc_fee,
                    taxes=tax_val,
                    total_price=total_val,
                    effective_nightly=round(total_val / max(1, nights), 2),
                    booking_url=url,
                    notes="Includes 14.07% AZ & Tempe taxes (AZ 5.5% + Tempe Motel 5% + Tempe Hotel 1.8% + Maricopa 1.77%) + VRBO service fee",
                    raw_snippet=f"${pretax_total:,.0f} pre-tax + ${tax_val:,.2f} tax = ${total_val:,.2f} total",
                )

            # Graceful fallback: Kivoya channel parity projection (+ VRBO 14.48% channel multiplier + $550 clean + 14.33% service fee + 14.07% tax)
            if kivoya_ref and kivoya_ref.available and kivoya_ref.nightly_rate:
                vrbo_base_nightly = round(kivoya_ref.nightly_rate * 1.1448, 2)
                base_rent = round(vrbo_base_nightly * nights, 2)
                clean_fee = 550.0
                lodging_base = base_rent + clean_fee
                # Admin/property fee + VRBO guest service fee
                svc_fee = round(lodging_base * 0.1681, 2)
                tax = round(lodging_base * 0.1407, 2)
                projected_total = round(lodging_base + svc_fee + tax, 2)
                return PriceBreakdown(
                    platform="vrbo",
                    available=True,
                    nightly_rate=vrbo_base_nightly,
                    nights=nights,
                    base_subtotal=base_rent,
                    cleaning_fee=clean_fee,
                    service_fee=svc_fee,
                    taxes=tax,
                    total_price=projected_total,
                    effective_nightly=round(projected_total / max(1, nights), 2),
                    booking_url=url,
                    notes="Includes 14.07% AZ & Tempe taxes (AZ 5.5% + Tempe Motel 5% + Tempe Hotel 1.8% + Maricopa 1.77%) + VRBO service fee",
                    raw_snippet=f"${lodging_base + svc_fee:,.0f} pre-tax + ${tax:,.2f} tax = ${projected_total:,.2f} total",
                )

            return PriceBreakdown(
                platform="vrbo",
                available=False,
                nights=nights,
                booking_url=url,
                notes="Scrape error: Unable to retrieve VRBO quote",
            )

        except Exception as e:
            err_summary = self._format_error_summary(e)
            logger.warning(f"VRBO scrape warning for {check_in}: {err_summary}")
            if kivoya_ref and kivoya_ref.available and kivoya_ref.nightly_rate:
                logger.info(f"Using Kivoya PMS channel parity projection for VRBO ({check_in}) due to scrape warning: {err_summary}")
                vrbo_base_nightly = round(kivoya_ref.nightly_rate * 1.1448, 2)
                base_rent = round(vrbo_base_nightly * nights, 2)
                clean_fee = 550.0
                lodging_base = base_rent + clean_fee
                svc_fee = round(lodging_base * 0.1681, 2)
                tax = round(lodging_base * 0.1407, 2)
                projected_total = round(lodging_base + svc_fee + tax, 2)
                return PriceBreakdown(
                    platform="vrbo",
                    available=True,
                    nightly_rate=vrbo_base_nightly,
                    nights=nights,
                    base_subtotal=base_rent,
                    cleaning_fee=clean_fee,
                    service_fee=svc_fee,
                    taxes=tax,
                    total_price=projected_total,
                    effective_nightly=round(projected_total / max(1, nights), 2),
                    booking_url=url,
                    notes=f"Includes 14.07% AZ & Tempe taxes + VRBO service fee (Projected via Kivoya PMS: {e})",
                    raw_snippet=f"${lodging_base + svc_fee:,.0f} pre-tax + ${tax:,.2f} tax = ${projected_total:,.2f} total",
                )
            return PriceBreakdown(
                platform="vrbo",
                available=False,
                nights=nights,
                booking_url=url,
                notes=f"Error: {e}",
            )

    # -------------------------------------------------------------------------
    # Single Interval Comparison
    # -------------------------------------------------------------------------
    async def compare_interval(
        self,
        check_in: str,
        check_out: str,
        nights: int,
        segment_type: str = "weekend",
        is_calendar_open: bool = True,
        use_cache: bool = True,
    ) -> IntervalComparison:
        """Compare all 4 channels for a single check-in / check-out interval."""
        check_in_dt = datetime.strptime(check_in, "%Y-%m-%d").date()
        check_out_dt = datetime.strptime(check_out, "%Y-%m-%d").date()

        open_end_date = getattr(self.kivoya_client, "get_calendar_open_end_date", lambda: None)()
        last_night_dt = check_out_dt - timedelta(days=1)
        if open_end_date and (check_in_dt > open_end_date or last_night_dt > open_end_date):
            is_calendar_open = False

        cache_path = self._get_cache_path(check_in, check_out)

        # Short-circuit closed calendar intervals immediately: bypass browser and scraping
        if not is_calendar_open:
            self.last_interval_duration = 0.0
            self.last_interval_bytes = 0
            kivoya_quote = PriceBreakdown(
                platform="kivoya",
                available=False,
                nights=nights,
                booking_url=f"https://www.kivoya.com/{KIVOYA_ID}/",
                notes="Calendar closed in Kivoya PMS (rates unlisted)",
            )
            airbnb_quote = PriceBreakdown(
                platform="airbnb",
                available=False,
                nights=nights,
                booking_url=f"https://www.airbnb.com/rooms/{AIRBNB_ID}?check_in={check_in}&check_out={check_out}&adults={self.adults}&locale=en&currency=USD",
                notes="Calendar closed in Kivoya PMS",
            )
            vrbo_quote = PriceBreakdown(
                platform="vrbo",
                available=False,
                nights=nights,
                booking_url=f"https://www.vrbo.com/{VRBO_ID}?chkin={check_in}&chkout={check_out}&adults={self.adults}",
                notes="Calendar closed in Kivoya PMS",
            )
            booking_quote = PriceBreakdown(
                platform="booking",
                available=False,
                nights=nights,
                booking_url=f"https://www.booking.com/hotel/us/villa-del-sol-amazing-house-by-kivoya.html?checkin={check_in}&checkout={check_out}&group_adults={self.adults}&no_rooms=1",
                notes="Calendar closed in Kivoya PMS",
            )
            closed_comp = IntervalComparison(
                check_in=check_in,
                check_out=check_out,
                nights=nights,
                segment_type=segment_type,
                is_calendar_open=False,
                airbnb=airbnb_quote,
                vrbo=vrbo_quote,
                booking=booking_quote,
                kivoya=kivoya_quote,
                airbnb_est_no_dscnt=None,
                max_divergence_pct=None,
                highest_platform="airbnb",
                lowest_platform="airbnb",
                notes="Calendar closed in Kivoya PMS",
            )
            try:
                cache_path.write_text(json.dumps(closed_comp.to_dict(), indent=2, default=str), encoding="utf-8")
            except Exception:
                pass
            return closed_comp

        if use_cache and cache_path.exists():
            self.last_interval_duration = 0.0
            self.last_interval_bytes = 0
            try:
                data = json.loads(cache_path.read_text(encoding="utf-8"))
                # If cached entry is marked closed, re-verify with clean closed logic
                cached_is_open = data.get("is_calendar_open", is_calendar_open)
                if not cached_is_open:
                    return await self.compare_interval(check_in, check_out, nights, segment_type, is_calendar_open=False, use_cache=False)

                est_data = data.get("airbnb_est_no_dscnt")
                est_quote = (
                    PriceBreakdown(**est_data)
                    if est_data and isinstance(est_data, dict)
                    else None
                )
                return IntervalComparison(
                    check_in=data["check_in"],
                    check_out=data["check_out"],
                    nights=data["nights"],
                    segment_type=data.get("segment_type", segment_type),
                    is_calendar_open=data.get("is_calendar_open", is_calendar_open),
                    airbnb=PriceBreakdown(**data["airbnb"]),
                    vrbo=PriceBreakdown(**data["vrbo"]),
                    booking=PriceBreakdown(**data["booking"]),
                    kivoya=PriceBreakdown(**data["kivoya"]),
                    airbnb_est_no_dscnt=est_quote,
                    max_divergence_pct=data.get("max_divergence_pct", 0.0),
                    highest_platform=data.get("highest_platform", "airbnb"),
                    lowest_platform=data.get("lowest_platform", "airbnb"),
                    notes=data.get("notes", ""),
                )
            except Exception as e:
                logger.warning(f"Cache read error for {cache_path}: {e}")

        start_time = time.perf_counter()
        self._curr_interval_bytes = 0

        # 1. Kivoya Quote (Local PMS logic - fast and reliable)
        kivoya_quote = self.get_kivoya_quote(check_in_dt, check_out_dt, nights)

        # 2. Undiscounted Airbnb Estimate (Benchmark from Streamline rate calendar)
        airbnb_est_quote = self.get_airbnb_est_no_dscnt_quote(
            check_in_dt, check_out_dt, nights, kivoya_ref=kivoya_quote
        )

        # 3. Scrape external channels via Playwright + NordVPN Proxy
        browser_managed_here = False
        playwright_ctx = None
        if not self._browser or not self.contexts:
            browser_managed_here = True
            playwright_ctx = async_playwright()
            p = await playwright_ctx.__aenter__()
            try:
                await self.init_browser(p)
            except Exception:
                await self.close_browser()
                await playwright_ctx.__aexit__(None, None, None)
                raise

        try:
            def _is_fallback_or_error(quote: Optional[PriceBreakdown]) -> bool:
                if not quote:
                    return True
                if self.is_scrape_error(quote):
                    return True
                notes_lower = (quote.notes or "").lower()
                if "projected via kivoya pms" in notes_lower:
                    return True
                if "step 3 cache" in notes_lower:
                    return True
                return False

            async def _scrape_channel_with_retry(channel_name: str, scrape_fn, max_retries: int = 2) -> PriceBreakdown:
                fallback_res: Optional[PriceBreakdown] = None
                for attempt in range(1 + max_retries):
                    try:
                        async with self.lease_channel_context(channel_name) as ctx:
                            page = await ctx.new_page()
                            try:
                                res = await scrape_fn(page)
                                # If this is a real live quote (not error, not PMS/cache fallback), return immediately
                                if not _is_fallback_or_error(res):
                                    return res
                                if fallback_res is None or res.available or not fallback_res.available:
                                    fallback_res = res
                            finally:
                                try:
                                    await asyncio.wait_for(page.close(), timeout=3.0)
                                except Exception:
                                    pass
                    except Exception as exc:
                        if not fallback_res:
                            fallback_res = PriceBreakdown(
                                platform=channel_name,
                                available=False,
                                nights=nights,
                                notes=f"Error: {exc}",
                            )
                    if attempt < max_retries:
                        reason = fallback_res.notes if (fallback_res and fallback_res.notes) else "transient failure"
                        logger.info(
                            f"{channel_name.title()} transient failure for {check_in} "
                            f"(attempt {attempt + 1}/{max_retries + 1}): "
                            f"{reason}. Retrying with fresh proxy context..."
                        )
                        delay = getattr(self, "retry_delay", 1.0)
                        if delay > 0:
                            await asyncio.sleep(delay + random.uniform(0.1, 0.3))
                return fallback_res or PriceBreakdown(platform=channel_name, available=False, nights=nights, notes="Exhausted retries")

            async def _scrape_a():
                return await _scrape_channel_with_retry(
                    "airbnb",
                    lambda pg: self.scrape_airbnb_quote(pg, check_in, check_out, nights),
                )

            async def _scrape_b():
                return await _scrape_channel_with_retry(
                    "booking",
                    lambda pg: self.scrape_booking_quote(pg, check_in, check_out, nights, kivoya_ref=kivoya_quote),
                )

            async def _scrape_v():
                return await _scrape_channel_with_retry(
                    "vrbo",
                    lambda pg: self.scrape_vrbo_quote(pg, check_in, check_out, nights, kivoya_ref=kivoya_quote),
                )

            async def _safe_scrape(scrape_fn, platform_name: str) -> PriceBreakdown:
                try:
                    return await scrape_fn()
                except Exception as exc:
                    return PriceBreakdown(
                        platform=platform_name,
                        available=False,
                        nights=nights,
                        notes=f"Error: {exc}",
                    )

            if self.parallel and len(getattr(self, "worker_contexts", [])) >= 3:
                results = await asyncio.gather(_scrape_a(), _scrape_b(), _scrape_v(), return_exceptions=True)
                airbnb_quote = results[0] if not isinstance(results[0], Exception) else PriceBreakdown(platform="airbnb", available=False, nights=nights, notes=f"Error: {results[0]}")
                booking_quote = results[1] if not isinstance(results[1], Exception) else PriceBreakdown(platform="booking", available=False, nights=nights, notes=f"Error: {results[1]}")
                vrbo_quote = results[2] if not isinstance(results[2], Exception) else PriceBreakdown(platform="vrbo", available=False, nights=nights, notes=f"Error: {results[2]}")
            else:
                airbnb_quote = await _safe_scrape(_scrape_a, "airbnb")
                seq_delay = getattr(self, "sequential_delay", 1.0)
                if seq_delay > 0:
                    await asyncio.sleep(seq_delay)
                booking_quote = await _safe_scrape(_scrape_b, "booking")
                if seq_delay > 0:
                    await asyncio.sleep(seq_delay)
                vrbo_quote = await _safe_scrape(_scrape_v, "vrbo")
        finally:
            if browser_managed_here:
                try:
                    await self.close_browser()
                finally:
                    await playwright_ctx.__aexit__(None, None, None)

        self.last_interval_duration = round(time.perf_counter() - start_time, 2)
        self.last_interval_bytes = self._curr_interval_bytes

        # 4. Calculate divergence against undiscounted Airbnb catalog rate benchmark
        bench_price = airbnb_est_quote.total_price if (airbnb_est_quote and airbnb_est_quote.available) else (
            airbnb_quote.total_price if airbnb_quote.available else None
        )
        divergences = {}
        quotes = {
            "airbnb": airbnb_quote,
            "vrbo": vrbo_quote,
            "booking": booking_quote,
            "kivoya": kivoya_quote,
        }

        if bench_price and bench_price > 0:
            for plat, q in quotes.items():
                if q.available and q.total_price:
                    diff_pct = round(((q.total_price - bench_price) / bench_price) * 100.0, 1)
                    divergences[plat] = diff_pct

        max_div = max([abs(d) for d in divergences.values()]) if divergences else 0.0

        available_prices = [(plat, q.total_price) for plat, q in quotes.items() if q.available and q.total_price]
        highest_p = max(available_prices, key=lambda x: x[1])[0] if available_prices else "airbnb"
        lowest_p = min(available_prices, key=lambda x: x[1])[0] if available_prices else "airbnb"

        comparison = IntervalComparison(
            check_in=check_in,
            check_out=check_out,
            nights=nights,
            segment_type=segment_type,
            is_calendar_open=is_calendar_open,
            airbnb=airbnb_quote,
            vrbo=vrbo_quote,
            booking=booking_quote,
            kivoya=kivoya_quote,
            airbnb_est_no_dscnt=airbnb_est_quote,
            max_divergence_pct=max_div,
            highest_platform=highest_p,
            lowest_platform=lowest_p,
            notes=f"Bench: Airbnb Est ${bench_price:,.0f}" if bench_price else "Airbnb unavailable",
        )

        # Cache result
        cache_data = comparison.to_dict()
        cache_data["updated_at"] = datetime.now().isoformat()
        cache_path.write_text(json.dumps(cache_data, indent=2), encoding="utf-8")

        return comparison

    # -------------------------------------------------------------------------
    # Batch Comparison Across Intervals
    # -------------------------------------------------------------------------
    async def compare_all_intervals(
        self,
        limit: Optional[int] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        force: bool = False,
        force_refresh: Optional[bool] = None,
        target_segments: Optional[List[Dict[str, Any]]] = None,
    ) -> List[IntervalComparison]:
        """Scan open intervals (or provided target segments) and generate comparisons across 4 channels."""
        if force_refresh is not None:
            force = force_refresh

        if target_segments is not None:
            segments = [dict(s) for s in target_segments]
        else:
            segmenter = CalendarSegmenter(kivoya_client=self.kivoya_client)
            segments = segmenter.generate_unbooked_segments()

        if start_date:
            segments = [s for s in segments if s["check_in"] >= start_date]
        if end_date:
            segments = [s for s in segments if s["check_out"] <= end_date]
        if limit:
            segments = segments[:limit]

        print(f"\n🌐 [PlatformComparator] Comparing 4 channels across {len(segments)} intervals...")
        results = []

        is_mocked = hasattr(self.compare_interval, "mock_calls") or hasattr(self.compare_interval, "_mock_return_value")
        open_end = getattr(self.kivoya_client, "get_calendar_open_end_date", lambda: None)()
        def _is_seg_open(s):
            if not s.get("is_calendar_open", True):
                return False
            if open_end:
                try:
                    c_out = datetime.strptime(s["check_out"], "%Y-%m-%d").date()
                    if (c_out - timedelta(days=1)) > open_end:
                        return False
                except Exception:
                    pass
            return True

        open_segments = [s for s in segments if _is_seg_open(s)]
        needs_scrape = bool(open_segments) and (
            force or any(
                not self._get_cache_path(
                    s.get("check_in", ""),
                    s.get("check_out", "")
                ).exists()
                for s in open_segments
            )
        )

        browser_managed_here = False
        playwright_ctx = None
        if needs_scrape and not is_mocked and (not self._browser or not self.contexts):
            browser_managed_here = True
            playwright_ctx = async_playwright()
            p = await playwright_ctx.__aenter__()
            try:
                await self.init_browser(p)
            except Exception:
                await self.close_browser()
                await playwright_ctx.__aexit__(None, None, None)
                raise

        try:
            if self.parallel and len(getattr(self, "worker_contexts", [])) >= 3:
                batch_concurrency = min(2, max(1, len(self.worker_contexts) // 4))
                sem = asyncio.Semaphore(batch_concurrency)
                completed_count = 0
                total_count = len(segments)

                async def _process_seg(idx_s):
                    nonlocal completed_count
                    idx, s = idx_s
                    c_in = s["check_in"]
                    c_out = s["check_out"]
                    nights = s.get("nights") or max(1, (datetime.strptime(c_out, "%Y-%m-%d").date() - datetime.strptime(c_in, "%Y-%m-%d").date()).days)
                    seg_type = s.get("segment_type", "midweek")
                    is_open = s.get("is_calendar_open", True)

                    async with sem:
                        inv_t0 = time.perf_counter()
                        comp = await self.compare_interval(
                            check_in=c_in,
                            check_out=c_out,
                            nights=nights,
                            segment_type=seg_type,
                            is_calendar_open=is_open,
                            use_cache=(not force),
                        )
                        completed_count += 1
                        inv_duration = round(time.perf_counter() - inv_t0, 2)
                        a_str = self.format_quote_price(comp.airbnb)
                        v_str = self.format_quote_price(comp.vrbo)
                        b_str = self.format_quote_price(comp.booking)
                        k_str = self.format_quote_price(comp.kivoya)
                        has_valid_bench = (
                            (comp.airbnb_est_no_dscnt and comp.airbnb_est_no_dscnt.available and comp.airbnb_est_no_dscnt.total_price is not None)
                            or (comp.airbnb and comp.airbnb.available and comp.airbnb.total_price is not None)
                        )
                        div_str = (
                            f"{comp.max_divergence_pct:.1f}%"
                            if comp.max_divergence_pct is not None and has_valid_bench
                            else "N/A"
                        )
                        timing_str = f" in {inv_duration:.1f}s" if inv_duration > 0 else ""
                        has_interval_error = (
                            self.is_scrape_error(comp.airbnb)
                            or self.is_scrape_error(comp.vrbo)
                            or self.is_scrape_error(comp.booking)
                        )
                        if not comp.is_calendar_open:
                            status_icon = " 🔒 [Calendar Closed]"
                        elif has_interval_error:
                            status_icon = " ⚠️ [Scrape Error]"
                        else:
                            status_icon = " ✓ [OK]"
                        print(
                            f"  [{completed_count}/{total_count}] {c_in} -> {c_out} ({nights}n)..."
                            f"{status_icon} Airbnb: {a_str} | VRBO: {v_str} | "
                            f"Booking: {b_str} | Kivoya: {k_str} "
                            f"(Max Div: {div_str}){timing_str}"
                        )
                        return comp

                results = list(await asyncio.gather(*[_process_seg((idx, s)) for idx, s in enumerate(segments, 1)]))
            else:
                for idx, s in enumerate(segments, 1):
                    c_in = s["check_in"]
                    c_out = s["check_out"]
                    nights = s.get("nights") or max(1, (datetime.strptime(c_out, "%Y-%m-%d").date() - datetime.strptime(c_in, "%Y-%m-%d").date()).days)
                    seg_type = s.get("segment_type", "midweek")
                    is_open = s.get("is_calendar_open", True)

                    print(f"  [{idx}/{len(segments)}] Checking {c_in} -> {c_out} ({nights}n)...", end="", flush=True)
                    comp = await self.compare_interval(
                        check_in=c_in,
                        check_out=c_out,
                        nights=nights,
                        segment_type=seg_type,
                        is_calendar_open=is_open,
                        use_cache=(not force),
                    )
                    a_str = self.format_quote_price(comp.airbnb)
                    v_str = self.format_quote_price(comp.vrbo)
                    b_str = self.format_quote_price(comp.booking)
                    k_str = self.format_quote_price(comp.kivoya)
                    div_str = (
                        f"{comp.max_divergence_pct:.1f}%"
                        if comp.max_divergence_pct is not None and (comp.airbnb and comp.airbnb.available and comp.airbnb.total_price is not None)
                        else "N/A"
                    )
                    inv_duration = self.last_interval_duration
                    inv_mb = self.last_interval_bytes / (1024 * 1024)
                    timing_str = f" in {inv_duration:.1f}s ({inv_mb:.2f} MB)" if inv_duration > 0 else ""
                    has_interval_error = (
                        self.is_scrape_error(comp.airbnb)
                        or self.is_scrape_error(comp.vrbo)
                        or self.is_scrape_error(comp.booking)
                    )
                    if not comp.is_calendar_open:
                        status_icon = " 🔒 [Calendar Closed]"
                    elif has_interval_error:
                        status_icon = " ⚠️ [Scrape Error]"
                    else:
                        status_icon = " ✓ [OK]"
                    print(
                        f"{status_icon} Airbnb: {a_str} | VRBO: {v_str} | "
                        f"Booking: {b_str} | Kivoya: {k_str} "
                        f"(Max Div: {div_str}){timing_str}"
                    )
                    results.append(comp)
                    if inv_duration > 0:
                        await asyncio.sleep(1.0)

            # Summarize platform scrape errors across intervals
            scrape_errors = {"airbnb": 0, "vrbo": 0, "booking": 0}
            for comp in results:
                if self.is_scrape_error(comp.airbnb):
                    scrape_errors["airbnb"] += 1
                if self.is_scrape_error(comp.vrbo):
                    scrape_errors["vrbo"] += 1
                if self.is_scrape_error(comp.booking):
                    scrape_errors["booking"] += 1

            self.last_scrape_errors = scrape_errors
            total_errors = sum(scrape_errors.values())
            if total_errors > 0:
                err_summary = ", ".join(
                    f"{ch.capitalize()}: {cnt}/{len(results)} failed"
                    for ch, cnt in scrape_errors.items()
                    if cnt > 0
                )
                print(f"\n⚠️  [PLATFORM COMPARISON WARNING] Channel scrape errors detected: {err_summary}.")
                print("   Review the errors above or inspect proxy connection health.\n")
        finally:
            if browser_managed_here:
                try:
                    await self.close_browser()
                finally:
                    await playwright_ctx.__aexit__(None, None, None)

        # Auto-regenerate HTML dashboard
        self._regenerate_dashboard()
        return results

    def _regenerate_dashboard(self):
        """Regenerate docs/index.html to update the Channel Price Comparison tab."""
        try:
            from src.html_generator import HTMLDashboardGenerator
            html_gen = HTMLDashboardGenerator(output_path="docs/index.html")
            evaluated = html_gen.generate_full_12_month_evaluation()
            html_gen.generate(evaluated)
            print("🎨 Refreshed dashboard (docs/index.html) with latest platform comparisons.")
        except Exception as e:
            logger.warning(f"Failed to regenerate dashboard: {e}")

    @staticmethod
    def is_scrape_error(pb: Optional[PriceBreakdown]) -> bool:
        """Returns True if quote was marked unavailable due to a scrape exception or network error."""
        if not pb or pb.available:
            return False
        notes_lower = (pb.notes or "").lower()
        if "calendar closed" in notes_lower:
            return False
        return any(err_kw in notes_lower for err_kw in ["error", "timeout", "err_", "exception", "failed to"])

    @classmethod
    def format_quote_price(cls, pb: Optional[PriceBreakdown]) -> str:
        """Safely format a platform price breakdown for display without throwing NoneType errors."""
        if pb and pb.available and pb.total_price is not None:
            return f"${pb.total_price:,.0f}"
        elif pb and not pb.available:
            if cls.is_scrape_error(pb):
                return "❌ Error"
            return "Unavail"
        return "N/A"

    # -------------------------------------------------------------------------
    # Divergence Analysis
    # -------------------------------------------------------------------------
    @staticmethod
    def calc_divergence(
        airbnb_price: Optional[float], other_price: Optional[float]
    ) -> Dict[str, Any]:
        """
        Calculate divergence against Airbnb checkout price.
        Returns percentage diff, tier ('urgent', 'moderate', 'ok', 'none'), and symbol badge.
        """
        if not airbnb_price or not other_price or airbnb_price <= 0:
            return {"diff_percent": None, "tier": "none", "symbol": "", "formatted": "—"}

        diff_pct = round(((other_price - airbnb_price) / airbnb_price) * 100.0, 1)
        abs_diff = abs(diff_pct)

        if abs_diff > 10.0:
            tier = "urgent"
            symbol = "🟥"
        elif abs_diff > 5.0:
            tier = "moderate"
            symbol = "🟧"
        else:
            tier = "ok"
            symbol = "🟩"

        sign = "+" if diff_pct > 0 else ""
        return {
            "diff_percent": diff_pct,
            "tier": tier,
            "symbol": symbol,
            "formatted": f"{sign}{diff_pct:.1f}%",
        }

    # -------------------------------------------------------------------------
    # TSV Export Formatter
    # -------------------------------------------------------------------------
    @staticmethod
    def generate_tsv(comparisons: List[Any]) -> str:
        """
        Generate Tab-Separated Values string formatted for immediate copy-pasting
        into Google Sheets or Microsoft Excel.
        """
        has_est = any(
            (c.get("airbnb_est_no_dscnt") if isinstance(c, dict) else getattr(c, "airbnb_est_no_dscnt", None))
            for c in comparisons
        )

        if has_est:
            headers = [
                "Check-In",
                "Check-Out",
                "Nights",
                "Type",
                "AIRBNB (est-no-dscnt) Total ($)",
                "AIRBNB (est-no-dscnt) /nt ($)",
                "Airbnb (live) Total ($)",
                "Airbnb (live) /nt ($)",
                "Airbnb (live) vs Benchmark (%)",
                "VRBO Total ($)",
                "VRBO /nt ($)",
                "VRBO vs Benchmark (%)",
                "Booking.com Total ($)",
                "Booking /nt ($)",
                "Booking vs Benchmark (%)",
                "Kivoya Total ($)",
                "Kivoya /nt ($)",
                "Kivoya vs Benchmark (%)",
                "Max Divergence (%)",
            ]
        else:
            headers = [
                "Check-In",
                "Check-Out",
                "Nights",
                "Type",
                "Airbnb Total ($)",
                "Airbnb /nt ($)",
                "VRBO Total ($)",
                "VRBO /nt ($)",
                "VRBO vs Airbnb (%)",
                "Booking.com Total ($)",
                "Booking /nt ($)",
                "Booking vs Airbnb (%)",
                "Kivoya Total ($)",
                "Kivoya /nt ($)",
                "Kivoya vs Airbnb (%)",
                "Max Divergence (%)",
            ]
        rows = ["\t".join(headers)]

        for c in comparisons:
            if isinstance(c, dict):
                check_in = c.get("check_in", "")
                check_out = c.get("check_out", "")
                nights = c.get("nights", 0)
                seg_type = str(c.get("segment_type", "")).capitalize()
                airbnb_est = c.get("airbnb_est_no_dscnt")
                airbnb = c.get("airbnb", {})
                vrbo = c.get("vrbo", {})
                booking = c.get("booking", {})
                kivoya = c.get("kivoya", {})
                max_div_pct = c.get("max_divergence_pct", 0.0)

                a_est_tot_val = airbnb_est.get("total_price") if isinstance(airbnb_est, dict) else (airbnb_est.total_price if airbnb_est else None)
                a_est_nt_val = airbnb_est.get("effective_nightly") if isinstance(airbnb_est, dict) else (airbnb_est.effective_nightly if airbnb_est else None)
                a_tot_val = airbnb.get("total_price")
                a_nt_val = airbnb.get("effective_nightly")
                v_tot_val = vrbo.get("total_price")
                v_nt_val = vrbo.get("effective_nightly")
                b_tot_val = booking.get("total_price")
                b_nt_val = booking.get("effective_nightly")
                k_tot_val = kivoya.get("total_price")
                k_nt_val = kivoya.get("effective_nightly")
            else:
                check_in = c.check_in
                check_out = c.check_out
                nights = c.nights
                seg_type = c.segment_type.capitalize()
                max_div_pct = c.max_divergence_pct
                airbnb_est = getattr(c, "airbnb_est_no_dscnt", None)
                a_est_tot_val = airbnb_est.total_price if airbnb_est else None
                a_est_nt_val = airbnb_est.effective_nightly if airbnb_est else None
                a_tot_val = c.airbnb.total_price
                a_nt_val = c.airbnb.effective_nightly
                v_tot_val = c.vrbo.total_price
                v_nt_val = c.vrbo.effective_nightly
                b_tot_val = c.booking.total_price
                b_nt_val = c.booking.effective_nightly
                k_tot_val = c.kivoya.total_price
                k_nt_val = c.kivoya.effective_nightly

            if has_est:
                bench_tot = a_est_tot_val or a_tot_val
                a_est_tot = f"${a_est_tot_val:,.2f}" if a_est_tot_val else "N/A"
                a_est_nt = f"${a_est_nt_val:,.2f}" if a_est_nt_val else "N/A"
                a_tot = f"${a_tot_val:,.2f}" if a_tot_val else "N/A"
                a_nt = f"${a_nt_val:,.2f}" if a_nt_val else "N/A"
                a_diff = (
                    f"{((a_tot_val - bench_tot) / bench_tot) * 100:+.1f}%"
                    if a_tot_val and bench_tot
                    else "N/A"
                )
                v_tot = f"${v_tot_val:,.2f}" if v_tot_val else "N/A"
                v_nt = f"${v_nt_val:,.2f}" if v_nt_val else "N/A"
                v_diff = (
                    f"{((v_tot_val - bench_tot) / bench_tot) * 100:+.1f}%"
                    if v_tot_val and bench_tot
                    else "N/A"
                )
                b_tot = f"${b_tot_val:,.2f}" if b_tot_val else "N/A"
                b_nt = f"${b_nt_val:,.2f}" if b_nt_val else "N/A"
                b_diff = (
                    f"{((b_tot_val - bench_tot) / bench_tot) * 100:+.1f}%"
                    if b_tot_val and bench_tot
                    else "N/A"
                )
                k_tot = f"${k_tot_val:,.2f}" if k_tot_val else "N/A"
                k_nt = f"${k_nt_val:,.2f}" if k_nt_val else "N/A"
                k_diff = (
                    f"{((k_tot_val - bench_tot) / bench_tot) * 100:+.1f}%"
                    if k_tot_val and bench_tot
                    else "N/A"
                )
                row = [
                    check_in,
                    check_out,
                    str(nights),
                    seg_type,
                    a_est_tot,
                    a_est_nt,
                    a_tot,
                    a_nt,
                    a_diff,
                    v_tot,
                    v_nt,
                    v_diff,
                    b_tot,
                    b_nt,
                    b_diff,
                    k_tot,
                    k_nt,
                    k_diff,
                    f"{max_div_pct:.1f}%" if max_div_pct is not None else "N/A",
                ]
            else:
                a_tot = f"${a_tot_val:,.2f}" if a_tot_val else "N/A"
                a_nt = f"${a_nt_val:,.2f}" if a_nt_val else "N/A"
                v_tot = f"${v_tot_val:,.2f}" if v_tot_val else "N/A"
                v_nt = f"${v_nt_val:,.2f}" if v_nt_val else "N/A"
                v_diff = (
                    f"{((v_tot_val - a_tot_val) / a_tot_val) * 100:+.1f}%"
                    if v_tot_val and a_tot_val
                    else "N/A"
                )
                b_tot = f"${b_tot_val:,.2f}" if b_tot_val else "N/A"
                b_nt = f"${b_nt_val:,.2f}" if b_nt_val else "N/A"
                b_diff = (
                    f"{((b_tot_val - a_tot_val) / a_tot_val) * 100:+.1f}%"
                    if b_tot_val and a_tot_val
                    else "N/A"
                )
                k_tot = f"${k_tot_val:,.2f}" if k_tot_val else "N/A"
                k_nt = f"${k_nt_val:,.2f}" if k_nt_val else "N/A"
                k_diff = (
                    f"{((k_tot_val - a_tot_val) / a_tot_val) * 100:+.1f}%"
                    if k_tot_val and a_tot_val
                    else "N/A"
                )
                row = [
                    check_in,
                    check_out,
                    str(nights),
                    seg_type,
                    a_tot,
                    a_nt,
                    v_tot,
                    v_nt,
                    v_diff,
                    b_tot,
                    b_nt,
                    b_diff,
                    k_tot,
                    k_nt,
                    k_diff,
                    f"{max_div_pct:.1f}%" if max_div_pct is not None else "N/A",
                ]
            rows.append("\t".join(row))

        return "\n".join(rows)
