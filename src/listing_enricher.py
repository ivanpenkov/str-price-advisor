"""
Deep Listing Enricher.
Extracts comprehensive listing metadata, descriptions, room details, and full amenity
lists for our property and all competitor properties in the registry.
Caches enriched listing profiles under data/enriched_comps/.
"""

import asyncio
from contextlib import asynccontextmanager
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
        self.worker_contexts: List[BrowserContext] = []
        self._context_queue: Optional[asyncio.Queue] = None
        from src.stealth_connection import StealthConnectionManager
        self.proxy_mgr = StealthConnectionManager(required=True)

    async def init_browser(self, p, num_workers: int = 1):
        """Launch headless browser with anti-detection args and stealth proxy pool support."""
        self.worker_contexts = []
        self._context_queue = asyncio.Queue()
        launch_kwargs = {
            "headless": self.headless,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        }

        if num_workers > 1:
            target_workers = min(num_workers, self.proxy_mgr.max_workers)
            proxy_configs = await self.proxy_mgr.start_pool(num_workers=target_workers)
            self.browser = await p.chromium.launch(**launch_kwargs)
            if proxy_configs:
                for cfg in proxy_configs:
                    proxy_arg = {"server": cfg["server"]} if isinstance(cfg, dict) and "server" in cfg else cfg
                    ctx = await self.browser.new_context(
                        viewport={"width": 1366, "height": 850},
                        user_agent=(
                            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
                        ),
                        proxy=proxy_arg,
                    )
                    self.worker_contexts.append(ctx)
                    self._context_queue.put_nowait(ctx)
                self.context = self.worker_contexts[0]
            else:
                self.context = await self.browser.new_context(
                    viewport={"width": 1366, "height": 850},
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
                    ),
                )
                self.worker_contexts.append(self.context)
                self._context_queue.put_nowait(self.context)
        else:
            proxy_cfg = await self.proxy_mgr.start()
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
            self.worker_contexts.append(self.context)
            self._context_queue.put_nowait(self.context)

    @asynccontextmanager
    async def lease_context(self):
        """Lease a worker context from the pool, returning it upon completion."""
        if getattr(self, "_context_queue", None) is not None:
            ctx = await self._context_queue.get()
            try:
                yield ctx
            finally:
                self._context_queue.put_nowait(ctx)
        else:
            yield self.context

    async def close_browser(self):
        """Close browser resources and terminate proxy bridge."""
        if hasattr(self, "worker_contexts"):
            for ctx in self.worker_contexts:
                try:
                    await ctx.close()
                except Exception:
                    pass
            self.worker_contexts.clear()
        elif self.context:
            try:
                await self.context.close()
            except Exception:
                pass
        self.context = None
        self._context_queue = None
        if self.browser:
            try:
                await self.browser.close()
            except Exception:
                pass
            self.browser = None
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
        extracted_description: Optional[str] = None
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

                # Extract complete listing description (including The Space, Guest Access, Other Things To Note)
                desc_candidates: List[str] = []

                def search_description(obj):
                    if isinstance(obj, dict):
                        for key in ("longDescriptionHtml", "description", "details"):
                            if key in obj and isinstance(obj[key], dict):
                                val = obj[key].get("source") or obj[key].get("htmlText")
                                if val and isinstance(val, str) and len(val) > 100:
                                    desc_candidates.append(val)
                        for v in obj.values():
                            search_description(v)
                    elif isinstance(obj, list):
                        for item in obj:
                            search_description(item)

                search_description(data)
                if desc_candidates:
                    desc_candidates.sort(key=lambda x: len(x), reverse=True)
                    clean = re.sub(r"<br\s*/?>", "\n", desc_candidates[0], flags=re.IGNORECASE)
                    clean = re.sub(r"<[^>]+>", " ", clean)
                    clean = re.sub(r"&[a-z]+;", " ", clean)
                    clean = re.sub(r"[ \t]+", " ", clean)
                    clean = re.sub(r"\n\s*\n+", "\n\n", clean).strip()
                    if clean:
                        extracted_description = clean
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
            "description": extracted_description,
        }

    @classmethod
    def clean_profile_title(cls, s: Optional[str]) -> Optional[str]:
        """
        Clean and normalize an Airbnb profile title from DOM <h1>, og:title, page title, or JSON-LD.
        Strips trailing Airbnb platform suffixes like ' - Houses for Rent in ... - Airbnb'
        and rejects generic location prefixes (e.g. 'Home in Tempe').
        """
        if not s or not isinstance(s, str):
            return None
        t = s.strip().strip('"\'')
        if not t:
            return None

        # Strip trailing " - Airbnb"
        if t.endswith(" - Airbnb"):
            t = t[:-9].strip()
        elif " - Airbnb" in t:
            t = t.split(" - Airbnb")[0].strip()

        # Strip trailing " - <Property Type> for Rent in <Location>" or " - Entire home in <Location>"
        t = re.sub(
            r"\s*-\s*(?:Houses|Homes|Entire home|Villas|Places to stay|Rooms|Condos|Apartments|Guest suites|Estates|Chalets)?\s*(?:for Rent in|in)\s+[^-]+$",
            "",
            t,
            flags=re.IGNORECASE,
        ).strip().strip('"\'')

        if not t or t.lower().startswith("503 service"):
            return None

        t_lower = t.lower()
        if t_lower.startswith("airbnb") or t_lower == "airbnb" or "vacation rentals, cabins" in t_lower or "vacation homes & condo rentals" in t_lower:
            return None
        if any(t_lower.startswith(pref) for pref in [
            "home in ", "entire home in ", "villa in ", "room in ",
            "cabin in ", "place to stay in ", "guesthouse in ", "townhouse in "
        ]):
            return None
        if t_lower in ["home", "villa", "entire home", "luxury estate", "house"]:
            return None
        if re.match(r"^\d+\s*bedrooms?$", t_lower) or re.match(r"^\d+\s*beds?$", t_lower):
            return None

        return t

    @classmethod
    def parse_json_ld(cls, ld_data: Dict[str, Any]) -> Dict[str, Any]:
        """Extract name, description, rating, reviews, image, and address from schema.org JSON-LD."""
        title = cls.clean_profile_title(ld_data.get("name"))

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

        reviews_samples = []
        if ld_data and isinstance(ld_data.get("review"), list):
            for r in ld_data["review"]:
                if isinstance(r, dict) and r.get("reviewBody"):
                    reviews_samples.append(r["reviewBody"].strip())

        address = ld_data.get("address")
        return {
            "title": title,
            "description": description,
            "rating": rating,
            "reviews": reviews,
            "photo_url": photo_url,
            "address": address,
            "reviews_samples": reviews_samples,
        }

    @classmethod
    def parse_house_rules(cls, dom_rules: Optional[List[str]] = None, deferred_text: str = "") -> Dict[str, Any]:
        """Parse structured house rules from DOM items and deferred client state."""
        raw_rules: List[str] = list(dom_rules or [])
        ci_time = None
        co_time = None
        deposit = None
        quiet_hours = None
        noise_monitoring = False
        pets_allowed = None
        events_allowed = None
        min_age = None
        additional_rules_text = ""

        def extract_text(val: Any) -> str:
            if not val:
                return ""
            if isinstance(val, str):
                return val.replace("\u202f", " ").strip()
            if isinstance(val, dict):
                if "content" in val and isinstance(val["content"], dict):
                    res = extract_text(val["content"])
                    if res:
                        return res
                for k in ("localizedStringWithTranslationPreference", "localizedString", "text", "html", "source", "title"):
                    if k in val and isinstance(val[k], str):
                        return val[k].replace("\u202f", " ").strip()
                for v in val.values():
                    if isinstance(v, (str, dict)):
                        res = extract_text(v)
                        if res:
                            return res
            return ""

        if deferred_text:
            try:
                data = json.loads(deferred_text)

                rules_obj = None
                pet_policy = None

                def find_objects(obj):
                    nonlocal rules_obj, pet_policy
                    if isinstance(obj, dict):
                        if "pdpPresentation" in obj and isinstance(obj["pdpPresentation"], dict) and "rules" in obj["pdpPresentation"]:
                            rules_obj = obj["pdpPresentation"]["rules"]
                        if "petPolicy" in obj and isinstance(obj["petPolicy"], dict):
                            pet_policy = obj["petPolicy"]
                        for v in obj.values():
                            find_objects(v)
                    elif isinstance(obj, list):
                        for item in obj:
                            find_objects(item)

                find_objects(data)

                if pet_policy and "isAllowed" in pet_policy:
                    pets_allowed = bool(pet_policy["isAllowed"])

                if rules_obj:
                    group_items = rules_obj.get("groupItems") or []
                    for g in group_items:
                        for item in g.get("items") or []:
                            title = extract_text(item.get("title"))
                            desc = extract_text(item.get("description"))
                            title_lower = title.lower()
                            if "additional rules" not in title_lower:
                                rule_line = f"{title}: {desc}".strip(" :") if desc else title
                                if rule_line and rule_line not in raw_rules:
                                    raw_rules.append(rule_line)
                            elif desc:
                                additional_rules_text = (additional_rules_text + "\n" + desc).strip()

                            item_combined = f"{title} {desc}".strip()
                            item_combined_lower = item_combined.lower()
                            if "self check-in" not in item_combined_lower and "additional rules" not in title.lower():
                                if re.search(r"check-?in\s*(?:after|before|between|from|:|\b)", item_combined, re.I):
                                    m_time = re.search(r"(?:check-?in[^\d\n]*)?(\d{1,2}(?::\d{2})?\s*(?:am|pm)(?:\s*(?:-|–|to)\s*\d{1,2}(?::\d{2})?\s*(?:am|pm))?)", item_combined, re.I)
                                    if m_time and not ci_time:
                                        ci_time = title if any(c.isdigit() for c in title) else (f"{title}: {desc}" if desc else m_time.group(0))

                                if re.search(r"check-?out\s*(?:after|before|by|until|between|from|:|\b)", item_combined, re.I):
                                    m_time = re.search(r"(?:check-?out[^\d\n]*)?(\d{1,2}(?::\d{2})?\s*(?:am|pm))", item_combined, re.I)
                                    if m_time and not co_time:
                                        co_time = title if any(c.isdigit() for c in title) else (f"{title}: {desc}" if desc else m_time.group(0))

                            if "quiet hours" in item_combined_lower and not quiet_hours:
                                quiet_hours = f"{title}: {desc}" if desc else title

                    add_rules = rules_obj.get("additionalRules") or {}
                    if add_rules:
                        extra_text = extract_text(add_rules)
                        if extra_text:
                            additional_rules_text = (additional_rules_text + "\n" + extra_text).strip()

                # Fallback: search rules recursively in any other format
                if not ci_time or not co_time:
                    def search_legacy_rules(obj):
                        nonlocal ci_time, co_time, deposit, quiet_hours
                        if isinstance(obj, dict):
                            title = str(obj.get("title") or obj.get("text") or "")
                            sub = str(obj.get("subtitle") or "")
                            combined = f"{title} {sub}".strip().lower()
                            if "self check-in" not in combined and "check-in" in combined and any(c.isdigit() for c in combined):
                                ci_time = ci_time or sub or title
                            elif "checkout" in combined or "check-out" in combined:
                                if any(c.isdigit() for c in combined):
                                    co_time = co_time or sub or title
                            elif "quiet hours" in combined and not quiet_hours:
                                quiet_hours = sub or title
                            for v in obj.values():
                                search_legacy_rules(v)
                        elif isinstance(obj, list):
                            for item in obj:
                                search_legacy_rules(item)

                    search_legacy_rules(data)

            except Exception as e:
                logger.debug(f"Could not parse house rules from deferred state: {e}")

        # Combine raw rules and additional rules
        combined_text = "\n".join(raw_rules)
        if additional_rules_text:
            combined_text += "\n" + additional_rules_text

        # Evaluate rules from raw_rules and dom_rules
        for rule in raw_rules:
            r_lower = rule.lower()
            if not ci_time and "self check-in" not in r_lower and "check-in" in r_lower and any(c.isdigit() for c in rule):
                ci_time = rule
            elif not co_time and ("checkout" in r_lower or "check-out" in r_lower or "check out" in r_lower) and any(c.isdigit() for c in rule):
                co_time = rule
            elif not quiet_hours and "quiet hours" in r_lower:
                quiet_hours = rule
            elif not deposit and ("deposit" in r_lower or "security" in r_lower) and len(rule) < 80:
                deposit = rule
            if any(k in r_lower for k in ("noise monitor", "decibel", "minut", "noiseaware", "sound meter")):
                noise_monitoring = True
            if pets_allowed is None:
                if "no pets" in r_lower or "pets not allowed" in r_lower or "pets are not allowed" in r_lower:
                    pets_allowed = False
                elif "pets allowed" in r_lower or "pet friendly" in r_lower:
                    pets_allowed = True
            if events_allowed is None:
                if "no parties" in r_lower or "no events" in r_lower or "parties or events not allowed" in r_lower:
                    events_allowed = False
                elif "events allowed" in r_lower:
                    events_allowed = True
            m_age = re.search(r"(\d{2})\s*(?:\+|years|or older)", r_lower)
            if m_age and not min_age:
                min_age = int(m_age.group(1))

        # Deep regex scans across combined_text (including full additional rules)
        if not quiet_hours:
            m_q = re.search(
                r"quiet\s*(?:hours|times?)\s*(?:are|from|between|:)?\s*([0-9]{1,2}(?::[0-9]{2})?\s*(?:am|pm)?\s*(?:to|-)\s*[0-9]{1,2}(?::[0-9]{2})?\s*(?:am|pm)?)",
                combined_text,
                re.IGNORECASE,
            )
            if m_q:
                quiet_hours = m_q.group(0).strip()

        if not min_age:
            m_a = re.search(
                r"(?:under|minimum\s*age\s*(?:of|is)?|must\s*be\s*at\s*least|primary\s*renter\s*must\s*be)\s*([23][0-9])",
                combined_text,
                re.IGNORECASE,
            )
            if m_a:
                min_age = int(m_a.group(1))

        if not deposit:
            m_d = re.search(
                r"(\$\s*(\d[\d,]*)\s*(?:security|damage|incidental|hold|refundable)?\s*deposit|(?:security|damage|incidental|hold|refundable)?\s*deposit\s*(?:of\s*)?\$\s*(\d[\d,]*)|(?:hold\s*(?:a\s*)?deposit|deposit\s*required|refundable\s*(?:security\s*)?deposit|forfeiture\s*of\s*security\s*deposit))",
                combined_text,
                re.IGNORECASE,
            )
            if m_d:
                deposit = m_d.group(0).strip()

        if any(k in combined_text.lower() for k in ("noise monitor", "decibel", "minut", "noiseaware", "sound meter")):
            noise_monitoring = True

        if events_allowed is None:
            if re.search(r"no\s*(?:parties|events|gatherings)", combined_text, re.IGNORECASE):
                events_allowed = False
            elif re.search(r"events?\s*(?:are\s*)?allowed", combined_text, re.IGNORECASE):
                events_allowed = True

        if pets_allowed is None:
            if re.search(r"no\s*pets|pets\s*(?:are\s*)?not\s*allowed", combined_text, re.IGNORECASE):
                pets_allowed = False
            elif re.search(r"pets?\s*allowed|pet\s*friendly", combined_text, re.IGNORECASE):
                pets_allowed = True

        return {
            "check_in_time": ci_time,
            "check_out_time": co_time,
            "security_deposit": deposit,
            "quiet_hours": quiet_hours,
            "noise_monitoring": noise_monitoring,
            "pets_allowed": pets_allowed,
            "events_allowed": events_allowed,
            "min_age": min_age,
            "raw_rules": raw_rules[:30],
            "additional_rules": additional_rules_text[:500] if additional_rules_text else None,
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
        dom_h1: Optional[str] = None,
        og_title: Optional[str] = None,
        listing_id: str = "",
        dom_reviews: Optional[List[str]] = None,
        dom_house_rules: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Combine all page sources into a normalized listing profile dictionary."""
        deferred_parsed = cls.parse_deferred_state(deferred_text)
        ld_parsed = cls.parse_json_ld(ld_data or {})

        # Title resolution order:
        # 1. DOM <h1> (exact marketing title rendered on the listing page)
        # 2. og:title meta tag (cleaned)
        # 3. page_title document title (cleaned of Airbnb suffixes)
        # 4. JSON-LD name (if non-generic)
        title = None
        for candidate in (dom_h1, og_title, page_title, ld_parsed.get("title")):
            cleaned = cls.clean_profile_title(candidate)
            if cleaned:
                title = cleaned
                break

        deferred_desc = deferred_parsed.get("description") or ""
        ld_desc = ld_parsed.get("description") or ""
        # Prefer complete description from deferred state (which includes The space and Other things to note) over truncated schema.org summary
        description = deferred_desc if len(deferred_desc) > len(ld_desc) else (ld_desc or dom_description or "")
        photo_url = ld_parsed.get("photo_url") or dom_photo

        # Review snippets for pool heating, noise, and condition ground truth
        all_reviews = list(dict.fromkeys((ld_parsed.get("reviews_samples") or []) + (dom_reviews or [])))

        bedrooms = deferred_parsed.get("bedrooms")
        beds = deferred_parsed.get("beds")
        baths = deferred_parsed.get("baths")
        guests = deferred_parsed.get("guests")

        overview = dom_overview or []
        for item in overview:
            if bedrooms is None:
                m = re.search(r"(\d+)\s*bedrooms?\b", item, re.IGNORECASE)
                if m:
                    bedrooms = int(m.group(1))
            if beds is None:
                m = re.search(r"(\d+)\s*beds?\b(?!room)", item, re.IGNORECASE)
                if m:
                    beds = int(m.group(1))
            if baths is None:
                m = re.search(r"(\d+(?:\.\d+)?)\s*baths?\b", item, re.IGNORECASE)
                if m:
                    baths = float(m.group(1))
            if guests is None:
                m = re.search(r"(\d+\+?)\s*guests?\b", item, re.IGNORECASE)
                if m:
                    guests = m.group(1)

        if description:
            if bedrooms is None:
                m = re.search(r"(\d+)\s*(?:br|bd|bedrooms?)\b", description, re.IGNORECASE)
                if m:
                    bedrooms = int(m.group(1))
            if baths is None:
                m = re.search(r"(\d+(?:\.\d+)?)\s*(?:ba|baths?|bathrooms?)\b", description, re.IGNORECASE)
                if m:
                    baths = float(m.group(1))
            if guests is None:
                m = re.search(r"(?:up to|sleeps|accommodates)\s*(\d+)", description, re.IGNORECASE)
                if m:
                    guests = m.group(1)

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

        from src.property_valuation import PropertyValuator
        loc_str = ""
        if isinstance(ld_parsed.get("address"), dict):
            loc_str = ld_parsed["address"].get("addressLocality") or ""

        guest_int = 16
        if guests:
            m_g = re.search(r"\d+", str(guests))
            if m_g:
                guest_int = int(m_g.group(0))

        prop_specs = PropertyValuator.evaluate_property_specs(
            listing_id=listing_id,
            title=title or "",
            description=description or "",
            location=loc_str,
            br=bedrooms or 6,
            ba=baths or 5.0,
            guests=guest_int,
        )

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
            "review_snippets": all_reviews[:15],
            "property_specs": prop_specs,
            "house_rules": cls.parse_house_rules(dom_rules=dom_house_rules, deferred_text=deferred_text),
            "enriched_at": datetime.now().isoformat(),
        }

    async def extract_listing_data(self, page: Page, listing_id: str) -> Dict[str, Any]:
        """Navigate to listing page and extract all available metadata, description, amenities, and reviews."""
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

        # 3. DOM Overviews, Title, Description, Meta photo, Reviews
        dom_h1 = await page.evaluate("""() => {
            const h1 = document.querySelector("h1, [data-section-id='TITLE_DEFAULT'] h1");
            return h1 ? h1.innerText.trim() : null;
        }""")
        og_title = await page.evaluate("""() => {
            const meta = document.querySelector("meta[property='og:title']");
            return meta ? meta.getAttribute("content") : null;
        }""")
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
        dom_reviews = await page.evaluate("""() => {
            const items = [];
            document.querySelectorAll("[data-review-id] span, [data-section-id='REVIEWS_DEFAULT'] span, div[role='article'] span").forEach(el => {
                const t = el.innerText.trim();
                if (t.length > 25 && t.length < 500 && !t.includes('\\n') && !t.startsWith('★')) {
                    items.push(t);
                }
            });
            return Array.from(new Set(items)).slice(0, 15);
        }""")
        dom_house_rules = await page.evaluate("""() => {
            const rules = [];
            const selectors = [
                "[data-section-id='POLICIES_DEFAULT'] li",
                "[data-section-id='POLICIES_DEFAULT'] div[role='group'] > div",
                "[data-section-id='HOUSE_RULES_DEFAULT'] li",
                "[data-section-id='THINGS_TO_KNOW'] li",
                "div[data-plugin-in-point-id='HOUSE_RULES_DEFAULT'] li",
                "[data-testid='house-rules'] li"
            ];
            selectors.forEach(sel => {
                document.querySelectorAll(sel).forEach(el => {
                    const text = el.innerText.trim();
                    if (text && text.length > 3 && text.length < 250 && !rules.includes(text)) {
                        rules.push(text);
                    }
                });
            });
            return rules;
        }""")

        return self.parse_page_content(
            deferred_text=deferred_text or "",
            ld_data=ld_data,
            page_title=page_title,
            dom_overview=dom_overview,
            dom_description=dom_desc,
            dom_photo=dom_photo,
            dom_h1=dom_h1,
            og_title=og_title,
            listing_id=listing_id,
            dom_reviews=dom_reviews,
            dom_house_rules=dom_house_rules,
        )

    async def enrich_listing(self, page: Page, listing_id: str, force_refresh: bool = False) -> Dict[str, Any]:
        """Fetch and cache listing profile."""
        if not force_refresh:
            cached = self.get_cached_profile(listing_id)
            if cached and cached.get("amenities_count", 0) > 0 and len(cached.get("description", "")) >= 600:
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
            "bathrooms": 5.0,
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
                "bathrooms": 5,
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

    async def enrich_all_comps(
        self,
        limit: Optional[int] = None,
        force_refresh: bool = False,
        concurrency: int = 4,
        unenriched_only: bool = False,
        listing_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Iterate through comp registry or discovered specs and enrich listings concurrently with full descriptions and amenities."""
        registry = {}
        if self.REGISTRY_PATH.exists():
            try:
                registry = json.loads(self.REGISTRY_PATH.read_text(encoding="utf-8"))
            except Exception:
                registry = {}

        specs = {}
        if self.SPECS_PATH.exists():
            try:
                specs = json.loads(self.SPECS_PATH.read_text(encoding="utf-8"))
            except Exception:
                specs = {}

        all_comps: List[Dict[str, Any]] = []

        if listing_ids:
            seen_ids = set()
            for raw_id in listing_ids:
                m = re.search(r"(\d{5,})", str(raw_id))
                cid = m.group(1) if m else str(raw_id).strip()
                if not cid or cid in seen_ids:
                    continue
                seen_ids.add(cid)
                comp_data = None
                for tier in ("tier_a", "tier_b"):
                    if cid in registry.get(tier, {}):
                        comp_data = dict(registry[tier][cid])
                        break
                if not comp_data and cid in specs:
                    comp_data = dict(specs[cid])
                if not comp_data:
                    comp_data = {"listing_id": cid, "name": f"Listing {cid}"}
                all_comps.append(comp_data)
        elif unenriched_only:
            for cid, spec in specs.items():
                cid_str = str(cid)
                cached = self.get_cached_profile(cid_str)
                is_enriched = bool(cached and cached.get("amenities_count", 0) > 0 and cached.get("description"))
                if not is_enriched:
                    item = dict(spec)
                    item["listing_id"] = cid_str
                    if "title" in item and "name" not in item:
                        item["name"] = item["title"]
                    all_comps.append(item)
        else:
            seen_cids = set()
            for tier_key in ("tier_a", "tier_b"):
                for cid, comp in registry.get(tier_key, {}).items():
                    cid_str = str(cid)
                    if cid_str in seen_cids:
                        continue
                    seen_cids.add(cid_str)
                    all_comps.append(comp)

        # Prioritize comps with truncated or missing descriptions (<600 chars)
        if not force_refresh and not listing_ids:
            all_comps.sort(
                key=lambda c: (
                    len((self.get_cached_profile(str(c.get("listing_id"))) or {}).get("description", "")) >= 600,
                    len((self.get_cached_profile(str(c.get("listing_id"))) or {}).get("description", "")),
                )
            )

        if not all_comps:
            if unenriched_only:
                print("🎉 All discovered listings in config/listing_specs.json are already fully enriched!")
            else:
                print("⚠️ No comps found to enrich.")
            return registry

        # If targeting registry by default and not forcing, check if all are already cached
        # If targeting registry by default and not forcing, check if all are already cached with full descriptions
        if not force_refresh and not unenriched_only and not listing_ids:
            cached_count = sum(
                1 for c in all_comps
                if (cached := self.get_cached_profile(str(c.get("listing_id"))))
                and cached.get("amenities_count", 0) > 0
                and len(cached.get("description", "")) >= 600
            )
            if cached_count == len(all_comps):
                print(f"⚡ All {cached_count} active comps in comps_registry.json are already fully enriched with complete descriptions and cached in data/enriched_comps/!")
                print("💡 To enrich other listings or re-scrape:")
                print("  • Enrich unenriched discovered comps:  python -m src.cli enrich-comps --unenriched --concurrency 2")
                print("  • Enrich with limit:                   python -m src.cli enrich-comps --unenriched --limit 10")
                print("  • Enrich specific comp(s):             python -m src.cli enrich-comps <listing_id>")
                print("  • Force re-scrape active comps:        python -m src.cli enrich-comps --force --concurrency 2")
                return registry

        if limit:
            all_comps = all_comps[:limit]

        target_desc = f"{len(all_comps)} unenriched comps" if unenriched_only else (f"{len(all_comps)} specific comps" if listing_ids else f"{len(all_comps)} comps")
        logger.info(f"Enriching {target_desc} (concurrency={concurrency}, force_refresh={force_refresh})...")
        print(f"🚀 Starting parallel enrichment for {target_desc} (workers={concurrency})...")

        async with async_playwright() as p:
            await self.init_browser(p, num_workers=concurrency)
            sem = asyncio.Semaphore(concurrency)
            progress = {"completed": 0, "total": len(all_comps)}

            async def process_comp(comp: Dict[str, Any]):
                cid = str(comp["listing_id"])
                async with sem:
                    # Check cache first before opening a page (must have amenities AND full description >= 600 chars)
                    if not force_refresh:
                        cached = self.get_cached_profile(cid)
                        if cached and cached.get("amenities_count", 0) > 0 and len(cached.get("description", "")) >= 600:
                            cur_name = comp.get("name") or comp.get("title") or ""
                            if cached.get("title") and (cur_name in ("Home in Scottsdale", "Home in Tempe", "Home in Mesa", "Home in Chandler") or cur_name.startswith("503 Service") or not cur_name):
                                comp["name"] = cached["title"]
                                comp["title"] = cached["title"]
                            if cached.get("photo_url") and not comp.get("photo_url"):
                                comp["photo_url"] = cached["photo_url"]
                            if cached.get("bedrooms") is not None:
                                comp["bedrooms"] = cached["bedrooms"]
                            if cached.get("beds") is not None:
                                comp["beds"] = cached["beds"]
                            if cached.get("baths") is not None:
                                comp["baths"] = cached["baths"]
                            if cached.get("rating") is not None:
                                comp["rating"] = cached["rating"]
                            if cached.get("reviews") is not None:
                                comp["reviews"] = cached["reviews"]
                            comp["amenities_count"] = cached.get("amenities_count", 0)
                            progress["completed"] += 1
                            disp_title = (comp.get("title") or comp.get("name") or f"Listing {cid}")[:35]
                            print(f"[{progress['completed']}/{progress['total']}] ⚡ [Cached] {cid}: {disp_title} ({comp.get('beds')} beds, {comp.get('amenities_count')} amenities)")
                            return

                    async with self.lease_context() as ctx:
                        page = await ctx.new_page()
                        try:
                            enriched = await self.enrich_listing(page, cid, force_refresh=force_refresh)
                            if enriched.get("title"):
                                cur_name = comp.get("name") or comp.get("title") or ""
                                if not cur_name or self.clean_profile_title(cur_name) is None or cur_name.startswith("503 Service"):
                                    comp["name"] = enriched["title"]
                                    comp["title"] = enriched["title"]
                            if enriched.get("photo_url"):
                                comp["photo_url"] = enriched["photo_url"]
                            if enriched.get("bedrooms") is not None:
                                comp["bedrooms"] = enriched["bedrooms"]
                            if enriched.get("beds") is not None:
                                comp["beds"] = enriched["beds"]
                            if enriched.get("baths") is not None:
                                comp["baths"] = enriched["baths"]
                            if enriched.get("rating") is not None:
                                comp["rating"] = enriched["rating"]
                            if enriched.get("reviews") is not None:
                                comp["reviews"] = enriched["reviews"]
                            comp["amenities_count"] = enriched.get("amenities_count", 0)
                            progress["completed"] += 1
                            disp_title = (enriched.get("title") or comp.get("title") or comp.get("name") or f"Listing {cid}")[:35]
                            print(f"[{progress['completed']}/{progress['total']}] ✅ {cid}: {disp_title} ({comp.get('beds')} beds, {comp.get('amenities_count')} amenities)")
                        except Exception as e:
                            progress["completed"] += 1
                            print(f"[{progress['completed']}/{progress['total']}] ⚠️ Error {cid}: {e}")
                        finally:
                            await page.close()
                    await asyncio.sleep(1.0)

            try:
                await asyncio.gather(*(process_comp(c) for c in all_comps))
            finally:
                await self.close_browser()

        # Update registry if any updated comps are in it
        registry_modified = False
        for tier_key in ("tier_a", "tier_b"):
            tier_dict = registry.get(tier_key, {})
            for comp in all_comps:
                cid_str = str(comp.get("listing_id"))
                if cid_str in tier_dict:
                    reg_comp = tier_dict[cid_str]
                    if comp.get("name"):
                        reg_comp["name"] = comp["name"]
                    if comp.get("photo_url"):
                        reg_comp["photo_url"] = comp["photo_url"]
                    if comp.get("bedrooms") is not None:
                        reg_comp["bedrooms"] = comp["bedrooms"]
                    if comp.get("beds") is not None:
                        reg_comp["beds"] = comp["beds"]
                    if comp.get("baths") is not None:
                        reg_comp["baths"] = comp["baths"]
                    if comp.get("rating") is not None:
                        reg_comp["rating"] = comp["rating"]
                    if comp.get("reviews") is not None:
                        reg_comp["reviews"] = comp["reviews"]
                    if comp.get("amenities_count") is not None:
                        reg_comp["amenities_count"] = comp["amenities_count"]
                    registry_modified = True

        if registry_modified and self.REGISTRY_PATH.exists():
            self.REGISTRY_PATH.write_text(json.dumps(registry, indent=2, ensure_ascii=False), encoding="utf-8")

        # Also synchronize config/listing_specs.json
        if self.SPECS_PATH.exists():
            for comp in all_comps:
                cid_str = str(comp.get("listing_id"))
                if not cid_str:
                    continue
                if cid_str not in specs:
                    specs[cid_str] = {"listing_id": cid_str}
                title = comp.get("title") or comp.get("name")
                if title:
                    specs[cid_str]["title"] = title
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

        print("✨ Metadata and listing specs updated with enriched data!")
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

                clean_title = self.clean_profile_title(cached.get("title"))
                if clean_title:
                    comp["name"] = clean_title
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
                if comp.get("name"):
                    specs[cid_str]["title"] = comp.get("name")
                if comp.get("location"):
                    specs[cid_str]["location"] = comp.get("location")
                if comp.get("bedrooms") is not None:
                    specs[cid_str]["bedrooms"] = comp.get("bedrooms")
                if comp.get("beds") is not None:
                    specs[cid_str]["beds"] = comp.get("beds")
                if comp.get("baths") is not None:
                    specs[cid_str]["baths"] = comp.get("baths")
                if comp.get("rating") is not None:
                    specs[cid_str]["rating"] = comp.get("rating")
                if comp.get("reviews") is not None:
                    specs[cid_str]["reviews"] = comp.get("reviews")
                if comp.get("photo_url"):
                    specs[cid_str]["photo_url"] = comp.get("photo_url")
                updated_count += 1

        # Also sync any cached profiles that may not be in registry (e.g. cohort comps)
        if self.ENRICHED_DIR.exists():
            for f in self.ENRICHED_DIR.glob("*.json"):
                try:
                    c_data = json.loads(f.read_text(encoding="utf-8"))
                    c_id = str(c_data.get("listing_id") or f.stem)
                    clean_t = self.clean_profile_title(c_data.get("title"))
                    if c_id not in specs:
                        specs[c_id] = {"listing_id": c_id}
                    if clean_t:
                        specs[c_id]["title"] = clean_t
                    if c_data.get("address", {}).get("addressLocality") and not specs[c_id].get("location"):
                        specs[c_id]["location"] = c_data["address"]["addressLocality"]
                    if c_data.get("location") and not specs[c_id].get("location"):
                        specs[c_id]["location"] = c_data["location"]
                    if c_data.get("bedrooms") is not None:
                        specs[c_id]["bedrooms"] = c_data["bedrooms"]
                    if c_data.get("beds") is not None:
                        specs[c_id]["beds"] = c_data["beds"]
                    if c_data.get("baths") is not None:
                        specs[c_id]["baths"] = c_data["baths"]
                    if c_data.get("rating") is not None:
                        specs[c_id]["rating"] = c_data["rating"]
                    if c_data.get("reviews") is not None:
                        specs[c_id]["reviews"] = c_data["reviews"]
                    if c_data.get("photo_url"):
                        specs[c_id]["photo_url"] = c_data["photo_url"]
                except Exception:
                    pass

        self.REGISTRY_PATH.write_text(json.dumps(registry, indent=2, ensure_ascii=False), encoding="utf-8")
        self.SPECS_PATH.write_text(json.dumps(specs, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"✨ Synchronized {updated_count} comps from cached profiles to registry and listing_specs.json!")
        return registry

    async def enrich_house_rules_only(
        self,
        listing_ids: Optional[List[str]] = None,
        concurrency: int = 2,
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Targeted scrape of dedicated House Rules / Policies sections for comps
        to backfill check-in/out times, deposits, and noise policies without re-scraping full media/specs.
        """
        registry = {}
        if self.REGISTRY_PATH.exists():
            try:
                registry = json.loads(self.REGISTRY_PATH.read_text(encoding="utf-8"))
            except Exception:
                registry = {}

        target_ids: List[str] = []
        if listing_ids:
            for raw_id in listing_ids:
                m = re.search(r"(\d{5,})", str(raw_id))
                cid = m.group(1) if m else str(raw_id).strip()
                if cid and cid not in target_ids:
                    target_ids.append(cid)
        else:
            for tier in ("tier_a", "tier_b"):
                for cid in registry.get(tier, {}):
                    if cid not in target_ids:
                        target_ids.append(str(cid))

        if limit:
            target_ids = target_ids[:limit]

        logger.info(f"Targeting {len(target_ids)} comps for house rules enrichment...")
        p = await async_playwright().start()
        try:
            await self.init_browser(p, num_workers=concurrency)
        except Exception:
            await p.stop()
            raise

        sem = asyncio.Semaphore(concurrency)
        progress = {"completed": 0, "total": len(target_ids), "updated": 0}

        async def process_one(cid: str):
            async with sem:
                cached = self.get_cached_profile(cid) or {"listing_id": cid}
                async with self.lease_context() as ctx:
                    page = await ctx.new_page()
                    try:
                        url = f"https://www.airbnb.com/rooms/{cid}"
                        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                        await page.wait_for_timeout(2500)

                        deferred_text = await page.evaluate("""() => {
                            const el = document.getElementById('data-deferred-state-0');
                            if (el && el.innerText && el.innerText.includes('rules')) return el.innerText;
                            const scripts = Array.from(document.querySelectorAll("script[id^='data-deferred-state']"));
                            for (const s of scripts) {
                                if (s.innerText.includes('rules') || s.innerText.includes('pdpPresentation')) return s.innerText;
                            }
                            return el ? el.innerText : '';
                        }""")
                        dom_house_rules = await page.evaluate("""() => {
                            const rules = [];
                            const selectors = [
                                "[data-section-id='POLICIES_DEFAULT'] li",
                                "[data-section-id='POLICIES_DEFAULT'] div[role='group'] > div",
                                "[data-section-id='HOUSE_RULES_DEFAULT'] li",
                                "[data-section-id='THINGS_TO_KNOW'] li",
                                "div[data-plugin-in-point-id='HOUSE_RULES_DEFAULT'] li",
                                "[data-testid='house-rules'] li"
                            ];
                            selectors.forEach(sel => {
                                document.querySelectorAll(sel).forEach(el => {
                                    const text = el.innerText.trim();
                                    if (text && text.length > 3 && text.length < 250 && !rules.includes(text)) {
                                        rules.push(text);
                                    }
                                });
                            });
                            return rules;
                        }""")

                        rules = self.parse_house_rules(dom_rules=dom_house_rules, deferred_text=deferred_text)
                        cached["house_rules"] = rules
                        cached["rules_enriched_at"] = datetime.now().isoformat()
                        self.save_cached_profile(cid, cached)
                        progress["completed"] += 1
                        progress["updated"] += 1
                        print(f"[{progress['completed']}/{progress['total']}] 📜 Rules saved for {cid}: in={rules.get('check_in_time')} out={rules.get('check_out_time')} deposit={rules.get('security_deposit')}")
                    except Exception as e:
                        progress["completed"] += 1
                        print(f"[{progress['completed']}/{progress['total']}] ⚠️ Error rules for {cid}: {e}")
                    finally:
                        await page.close()
                await asyncio.sleep(1.0)

        try:
            await asyncio.gather(*(process_one(cid) for cid in target_ids))
        finally:
            await self.close_browser()
            await p.stop()
        return progress


if __name__ == "__main__":
    enricher = ListingEnricher(headless=True)
    print("Enriching our property profile...")
    asyncio.run(enricher.enrich_our_property(force_refresh=True))
