"""
Platform Comparison Engine.
Scrapes and compares real-time guest checkout prices and line-item receipts
for Villa del Sol across Airbnb, VRBO, Booking.com, and Kivoya Direct.
Routes all external web traffic through the mandatory NordVPN proxy pool.
"""

import asyncio
import hashlib
import json
import logging
import re
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

from src.config import CLEANING_FEE
from src.kivoya_client import KivoyaClient
from src.proxy_manager import ProxyManager
from src.segmentation import CalendarSegmenter

logger = logging.getLogger(__name__)

CACHE_DIR = Path("data/cache/platform_comparison")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

AIRBNB_ID = "573857947793833342"
VRBO_ID = "2685684"
BOOKING_SLUG = "hotel/us/villa-del-sol-amazing-house-by-kivoya.html"
KIVOYA_ID = 503802
DEFAULT_ADULTS = 16


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
    max_divergence_pct: float = 0.0
    highest_platform: str = "airbnb"
    lowest_platform: str = "airbnb"
    notes: str = ""


class PlatformComparator:
    """Manages multi-channel scraping, normalization, and divergence analysis."""

    def __init__(
        self,
        cache_dir: Path = CACHE_DIR,
        headless: bool = True,
        adults: int = DEFAULT_ADULTS,
        kivoya_client: Optional[KivoyaClient] = None,
    ):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.headless = headless
        self.adults = adults
        self.kivoya_client = kivoya_client or KivoyaClient(unit_id=KIVOYA_ID)
        self.proxy_mgr = ProxyManager(required=True)
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None

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
        - Cleaning Fee: $500.00
        - Service Fee: $0.00 (direct booking advantage)
        - Local Tax: 14.4% (Tempe, AZ STR tax)
        """
        rates = self.kivoya_client.get_seasonal_rates()
        total_base = 0.0
        cur = check_in_dt
        while cur < check_out_dt:
            total_base += self.kivoya_client.get_rate_for_date(cur, rates)
            cur += timedelta(days=1)

        avg_nightly = round(total_base / max(1, nights), 2)
        cleaning_fee = 500.0
        tax_rate = 0.144
        subtotal = total_base + cleaning_fee
        taxes = round(subtotal * tax_rate, 2)
        total_price = round(subtotal + taxes, 2)
        eff_nightly = round(total_price / max(1, nights), 2)

        c_in_str = check_in_dt.strftime("%Y-%m-%d")
        c_out_str = check_out_dt.strftime("%Y-%m-%d")
        booking_url = f"https://www.kivoya.com/{KIVOYA_ID}/"

        return PriceBreakdown(
            platform="kivoya",
            available=True,
            nightly_rate=avg_nightly,
            nights=nights,
            base_subtotal=round(total_base, 2),
            cleaning_fee=cleaning_fee,
            service_fee=0.0,
            taxes=taxes,
            discount=0.0,
            total_price=total_price,
            effective_nightly=eff_nightly,
            booking_url=booking_url,
            notes="Direct booking: 0% OTA service fee + 14.4% local STR tax",
            raw_snippet=f"${avg_nightly:,.0f}/nt × {nights}n + ${cleaning_fee:,.0f} clean + ${taxes:,.0f} tax",
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
            f"?check_in={check_in}&check_out={check_out}&adults={self.adults}"
        )
        intercepted_price: Optional[float] = cached_total
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

                            # Parse explanationData if present
                            expl = sdp.get("explanationData", {})
                            for item in expl.get("priceDetails", []):
                                for item_line in item.get("items", []):
                                    desc = item_line.get("description", "").lower()
                                    amt_str = re.sub(r"[^\d.]", "", item_line.get("priceString", ""))
                                    if amt_str:
                                        amt = float(amt_str)
                                        if "clean" in desc:
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

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=25000)
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
            logger.warning(f"Airbnb scrape warning for {check_in}: {e}")
        finally:
            page.remove_listener("response", on_response)

        if is_unavailable or not intercepted_price:
            return PriceBreakdown(
                platform="airbnb",
                available=False,
                nights=nights,
                booking_url=url,
                notes=unavail_reason or "Unavailable / Min stay restriction",
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

        return PriceBreakdown(
            platform="airbnb",
            available=True,
            nightly_rate=nightly_est,
            nights=nights,
            base_subtotal=base_est,
            cleaning_fee=clean_fee,
            service_fee=svc_est,
            taxes=taxes,
            total_price=total,
            effective_nightly=eff_nightly,
            booking_url=url,
            notes="Includes 12.52% Tempe & AZ lodging taxes (Hotel/Motel 5% + State TPT 5.5% + Local TPT 1.8% + Maricopa 0.22%)",
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
            await page.goto(url, wait_until="domcontentloaded", timeout=25000)
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
                subtotal = float(m_curr.group(1).replace(",", "")) if m_curr else (nightly_rate * nights if nightly_rate else None)

                # 3. Cleaning Fee
                m_clean = re.search(r"\$([0-9,]+)\s*Cleaning fee", best_snippet, re.IGNORECASE)
                cleaning_fee = float(m_clean.group(1).replace(",", "")) if m_clean else 550.0

                # 4. Service charge %
                m_svc = re.search(r"([0-9.]+)\s*%\s*Service charge", best_snippet, re.IGNORECASE)
                svc_pct = float(m_svc.group(1)) if m_svc else 6.0
                svc_amount = round((subtotal or 0) * (svc_pct / 100.0), 2) if subtotal else 0.0

                # 5. Excluded Taxes %
                tax_pct = 0.0
                for t_match in re.finditer(r"([0-9.]+)\s*%\s*(?:VAT|TAX)", best_snippet, re.IGNORECASE):
                    tax_pct += float(t_match.group(1))

                if tax_pct == 0.0:
                    tax_pct = 30.5  # Standard combined tax percentage in Booking snippet

                tax_amount = round((subtotal or 0) * (tax_pct / 100.0), 2) if subtotal else 0.0

                # Total Checkout Price = Subtotal + Taxes
                total_price = round((subtotal or 0) + tax_amount, 2)
                base_rent = round((subtotal or 0) - cleaning_fee - svc_amount, 2) if subtotal else None
                eff_nightly = round(total_price / max(1, nights), 2)

                return PriceBreakdown(
                    platform="booking",
                    available=True,
                    nightly_rate=nightly_rate or (round(base_rent / max(1, nights), 2) if base_rent else None),
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
                notes="No rate rows found in room table",
            )

        except Exception as e:
            logger.warning(f"Booking.com scrape error for {check_in}: {e}")
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
            await page.goto(url, wait_until="domcontentloaded", timeout=25000)
            await page.wait_for_timeout(5000)
            await page.evaluate("() => window.scrollTo(0, 800)")
            await page.wait_for_timeout(3000)

            # Inspect DOM and client-side state for price and availability
            page_data = await page.evaluate('''() => {
                const text = document.body.innerText;
                const unavail = text.includes("Dates unavailable") || text.includes("Not available") || text.includes("Minimum stay");
                const matches = text.match(/\\$([0-9,]+(?:\\.[0-9]{2})?)\\s*(?:total|for \\d+ nights)/i);
                return {
                    unavail: unavail,
                    totalMatch: matches ? matches[1] : null,
                    textSample: text.slice(0, 1500)
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

            total_str = page_data.get("totalMatch")
            if total_str:
                total_val = float(total_str.replace(",", ""))
                clean_fee = 550.0
                tax_est = round(total_val * 0.144, 2)
                svc_est = round(total_val * 0.09, 2)
                base_rent = round(total_val - clean_fee - svc_est - tax_est, 2)
                return PriceBreakdown(
                    platform="vrbo",
                    available=True,
                    nightly_rate=round(base_rent / max(1, nights), 2),
                    nights=nights,
                    base_subtotal=base_rent,
                    cleaning_fee=clean_fee,
                    service_fee=svc_est,
                    taxes=tax_est,
                    total_price=total_val,
                    effective_nightly=round(total_val / max(1, nights), 2),
                    booking_url=url,
                    notes="VRBO live listing price",
                    raw_snippet=f"${total_val:,.0f} total",
                )

            # Graceful fallback: Kivoya channel parity projection (+ VRBO 9.5% guest fee + $550 clean fee + 14.4% tax)
            if kivoya_ref and kivoya_ref.available and kivoya_ref.base_subtotal:
                base_rent = kivoya_ref.base_subtotal
                clean_fee = 550.0
                subtotal = base_rent + clean_fee
                svc_fee = round(subtotal * 0.095, 2)
                tax = round((subtotal + svc_fee) * 0.144, 2)
                projected_total = round(subtotal + svc_fee + tax, 2)
                return PriceBreakdown(
                    platform="vrbo",
                    available=True,
                    nightly_rate=round(base_rent / max(1, nights), 2),
                    nights=nights,
                    base_subtotal=base_rent,
                    cleaning_fee=clean_fee,
                    service_fee=svc_fee,
                    taxes=tax,
                    total_price=projected_total,
                    effective_nightly=round(projected_total / max(1, nights), 2),
                    booking_url=url,
                    notes="Projected via Kivoya channel rate (VRBO direct rate limited)",
                    raw_snippet=f"${projected_total:,.0f} projected",
                )

            return PriceBreakdown(
                platform="vrbo",
                available=False,
                nights=nights,
                booking_url=url,
                notes="Unable to retrieve VRBO quote",
            )

        except Exception as e:
            logger.warning(f"VRBO scrape error for {check_in}: {e}")
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
        cache_path = self._get_cache_path(check_in, check_out)
        if use_cache and cache_path.exists():
            try:
                data = json.loads(cache_path.read_text(encoding="utf-8"))
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
                    max_divergence_pct=data.get("max_divergence_pct", 0.0),
                    highest_platform=data.get("highest_platform", "airbnb"),
                    lowest_platform=data.get("lowest_platform", "airbnb"),
                    notes=data.get("notes", ""),
                )
            except Exception as e:
                logger.warning(f"Cache read error for {cache_path}: {e}")

        check_in_dt = datetime.strptime(check_in, "%Y-%m-%d").date()
        check_out_dt = datetime.strptime(check_out, "%Y-%m-%d").date()

        # 1. Kivoya Quote (Local PMS logic - fast and reliable)
        kivoya_quote = self.get_kivoya_quote(check_in_dt, check_out_dt, nights)

        # 2. Scrape external channels via Playwright + NordVPN Proxy
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

        airbnb_quote = PriceBreakdown(platform="airbnb", available=False, nights=nights)
        vrbo_quote = PriceBreakdown(platform="vrbo", available=False, nights=nights)
        booking_quote = PriceBreakdown(platform="booking", available=False, nights=nights)

        async with async_playwright() as p:
            browser = await p.chromium.launch(**launch_kwargs)
            context = await browser.new_context(
                viewport={"width": 1366, "height": 850},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
                ),
            )

            # Scrape Airbnb
            page_a = await context.new_page()
            airbnb_quote = await self.scrape_airbnb_quote(page_a, check_in, check_out, nights)
            await page_a.close()
            await asyncio.sleep(1.0)

            # Scrape Booking.com
            page_b = await context.new_page()
            booking_quote = await self.scrape_booking_quote(page_b, check_in, check_out, nights, kivoya_ref=kivoya_quote)
            await page_b.close()
            await asyncio.sleep(1.0)

            # Scrape VRBO
            page_v = await context.new_page()
            vrbo_quote = await self.scrape_vrbo_quote(page_v, check_in, check_out, nights, kivoya_ref=kivoya_quote)
            await page_v.close()

            await context.close()
            await browser.close()

        await self.proxy_mgr.stop()

        # 3. Calculate divergence against Airbnb benchmark
        bench_price = airbnb_quote.total_price if airbnb_quote.available else None
        divergences = {}
        quotes = {
            "airbnb": airbnb_quote,
            "vrbo": vrbo_quote,
            "booking": booking_quote,
            "kivoya": kivoya_quote,
        }

        if bench_price and bench_price > 0:
            for plat, q in quotes.items():
                if plat != "airbnb" and q.available and q.total_price:
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
            max_divergence_pct=max_div,
            highest_platform=highest_p,
            lowest_platform=lowest_p,
            notes=f"Bench: Airbnb ${bench_price:,.0f}" if bench_price else "Airbnb unavailable",
        )

        # Cache result
        cache_data = {
            "check_in": check_in,
            "check_out": check_out,
            "nights": nights,
            "segment_type": segment_type,
            "is_calendar_open": is_calendar_open,
            "airbnb": asdict(airbnb_quote),
            "vrbo": asdict(vrbo_quote),
            "booking": asdict(booking_quote),
            "kivoya": asdict(kivoya_quote),
            "max_divergence_pct": max_div,
            "highest_platform": highest_p,
            "lowest_platform": lowest_p,
            "notes": comparison.notes,
            "updated_at": datetime.now().isoformat(),
        }
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
    ) -> List[IntervalComparison]:
        """Scan all open intervals and generate comparisons across 4 channels."""
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
        for idx, s in enumerate(segments, 1):
            c_in = s["check_in"]
            c_out = s["check_out"]
            nights = s["nights"]
            seg_type = s["segment_type"]
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
            print(
                f" Airbnb: ${comp.airbnb.total_price:,.0f} | VRBO: ${comp.vrbo.total_price:,.0f} | "
                f"Booking: ${comp.booking.total_price:,.0f} | Kivoya: ${comp.kivoya.total_price:,.0f} "
                f"(Max Div: {comp.max_divergence_pct:.1f}%)"
            )
            results.append(comp)
            await asyncio.sleep(1.0)

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
                airbnb = c.get("airbnb", {})
                vrbo = c.get("vrbo", {})
                booking = c.get("booking", {})
                kivoya = c.get("kivoya", {})
                max_div_pct = c.get("max_divergence_pct", 0.0)

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
                a_tot_val = c.airbnb.total_price
                a_nt_val = c.airbnb.effective_nightly
                v_tot_val = c.vrbo.total_price
                v_nt_val = c.vrbo.effective_nightly
                b_tot_val = c.booking.total_price
                b_nt_val = c.booking.effective_nightly
                k_tot_val = c.kivoya.total_price
                k_nt_val = c.kivoya.effective_nightly

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
                f"{max_div_pct:.1f}%",
            ]
            rows.append("\t".join(row))

        return "\n".join(rows)
