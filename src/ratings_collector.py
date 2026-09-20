"""
Ratings and Reviews Ingestion Engine for Villa del Sol.
Scrapes and synchronizes ratings, category sub-scores, and guest reviews
across Airbnb, VRBO, and Booking.com using stealth NordVPN
proxy routing, hybrid network/DOM extraction, and incremental deduplication.
"""

import asyncio
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import ssl
import time
from typing import Any, Dict, List, Optional, Tuple
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger("ratings_collector")


def compute_review_id(platform: str, author: str, date_str: str, body: str) -> str:
    """Deterministic unique MD5 identifier for a review."""
    p = str(platform or "").lower().strip()
    a = str(author or "").lower().strip()
    d = str(date_str or "").strip()
    b = str(body or "").strip()[:60]
    raw = f"{p}_{a}_{d}_{b}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


class CrossBorderRedirectError(RuntimeError):
    """Raised when an OTA navigation redirects to a foreign country-code top-level domain (ccTLD)."""
    pass


def check_cross_border_redirect(url: str, expected_domain: str = ".com") -> None:
    """
    Guard against OTA cross-border redirects caused by foreign IP misattribution.
    Detects redirects to foreign ccTLDs (.co.za, .ru, .cn, etc.) or non-matching domains.
    Raises CrossBorderRedirectError to abort scrape and protect proxy corridor.
    """
    if not url or not isinstance(url, str):
        return
    parsed_url = url if ("://" in url or url.startswith("//")) else f"https://{url}"
    from urllib.parse import urlparse
    netloc = urlparse(parsed_url).netloc.lower().split(":")[0]
    if not netloc:
        return
    foreign_tlds = (
        ".co.za", ".za", ".ru", ".cn", ".co.uk", ".com.br", ".com.mx",
        ".de", ".fr", ".es", ".it", ".nl", ".pl", ".jp", ".in", ".com.au",
        ".ca", ".co.nz", ".ch", ".at", ".se", ".no", ".dk", ".fi",
    )
    for tld in foreign_tlds:
        if netloc.endswith(tld):
            raise CrossBorderRedirectError(
                f"Cross-border redirect detected: navigation redirected to foreign ccTLD '{netloc}' "
                f"(expected domain ending with '{expected_domain}'). Aborting to protect proxy corridor."
            )
    if expected_domain and not netloc.endswith(expected_domain):
        raise CrossBorderRedirectError(
            f"Cross-border redirect detected: navigation redirected to unexpected domain '{netloc}' "
            f"(expected domain ending with '{expected_domain}'). Aborting to protect proxy corridor."
        )


def _parse_date_str(val: str) -> str:
    """Helper to convert human-readable or partial dates to YYYY-MM-DD."""
    if not val:
        return ""
    val = str(val).strip()
    m = re.search(r"(\d{4}-\d{2}-\d{2})", val)
    if m:
        return m.group(1)
    cleaned = re.sub(r"^(Reviewed:\s*|Stayed in\s*|Date of review:\s*)", "", val, flags=re.IGNORECASE).strip()
    for fmt in (
        "%B %d, %Y",
        "%b %d, %Y",
        "%d %B %Y",
        "%d %b %Y",
        "%Y/%m/%d",
        "%m/%d/%Y",
    ):
        try:
            return datetime.strptime(cleaned, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    for fmt in ("%B %Y", "%b %Y"):
        try:
            return datetime.strptime(cleaned, fmt).strftime("%Y-%m-01")
        except ValueError:
            pass
    if cleaned.lower() == "today":
        return date.today().isoformat()
    if cleaned.lower() == "yesterday":
        return (date.today() - timedelta(days=1)).isoformat()
    m_rel = re.search(r"(\d+)\s+(day|week|month|year)s?\s+ago", val, re.IGNORECASE)
    if m_rel:
        num = int(m_rel.group(1))
        unit = m_rel.group(2).lower()
        days = num if unit == "day" else (num * 7 if unit == "week" else (num * 30 if unit == "month" else num * 365))
        return (date.today() - timedelta(days=days)).isoformat()
    if len(val) >= 10 and re.match(r"^\d{4}", val):
        return val[:10]
    return val


class RatingsCollector:
    """
    Multi-channel ratings and reviews collector for Villa del Sol.
    """

    CHANNELS: Dict[str, Dict[str, Any]] = {
        "airbnb": {
            "platform_id": "airbnb",
            "display_name": "Airbnb",
            "url": "https://www.airbnb.com/rooms/573857947793833342",
            "proxy_feeder": "feeder-sf",
            "scale": "5.0",
            "rating_max": 5.0,
        },
        "vrbo": {
            "platform_id": "vrbo",
            "display_name": "VRBO",
            "url": "https://www.vrbo.com/2685684",
            "proxy_feeder": "feeder-dal",
            "scale": "10.0",
            "rating_max": 10.0,
            "badge": {
                "name": "Loved by Guests",
                "subtitle": "Top 10% of guest reviews in this area",
                "badge_type": "top_percentile",
            },
        },
        "booking": {
            "platform_id": "booking",
            "display_name": "Booking.com",
            "url": "https://www.booking.com/hotel/us/villa-del-sol-amazing-house-by-kivoya.html",
            "proxy_feeder": "feeder-chi",
            "scale": "10.0",
            "rating_max": 10.0,
        },
    }

    def __init__(
        self,
        db_path: Path = Path("data/ratings_reviews.json"),
        proxy_mgr: Optional[Any] = None,
        headless: bool = True,
        recent_window_days: int = 30,
        stealth_delay: float = 0.0,
    ):
        self.db_path = Path(db_path)
        self.proxy_mgr = proxy_mgr
        self.headless = headless
        self.recent_window_days = recent_window_days
        self.stealth_delay = stealth_delay
        self.data: Dict[str, Any] = self.load_database()

    def load_database(self) -> Dict[str, Any]:
        """Load database from disk or return empty canonical structure."""
        if self.db_path.exists():
            try:
                content = self.db_path.read_text(encoding="utf-8")
                loaded = json.loads(content)
                if isinstance(loaded, dict) and "platforms" in loaded and "reviews" in loaded:
                    return loaded
            except Exception as e:
                logger.warning(f"Could not load existing ratings database from {self.db_path}: {e}")

        # Baseline empty canonical structure
        now_iso = datetime.now(timezone.utc).astimezone().isoformat()
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "last_updated": now_iso,
            "recent_window_days": self.recent_window_days,
            "recent_reviews_count": 0,
            "platforms": {
                p_id: {
                    "platform_id": p_id,
                    "display_name": info["display_name"],
                    "url": info["url"],
                    "scale": info["scale"],
                    "rating_max": info.get("rating_max", 5.0),
                    "rating": None,
                    "review_count": 0,
                    "sub_scores": {},
                    "badge": dict(info["badge"]) if "badge" in info else None,
                    "last_scraped": None,
                    "status": "pending",
                }
                for p_id, info in self.CHANNELS.items()
            },
            "reviews": [],
        }

    def save_database(self) -> None:
        """Atomically persist database to disk."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        now_iso = datetime.now(timezone.utc).astimezone().isoformat()
        self.data["last_updated"] = now_iso
        self.data["recent_window_days"] = self.recent_window_days
        self.recalculate_recency()
        tmp_file = self.db_path.with_suffix(f".tmp.{os.getpid()}.{time.time_ns()}")
        tmp_file.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp_file.replace(self.db_path)

    save = save_database

    def recalculate_recency(self, reference_date: Optional[date] = None) -> int:
        """Update is_recent on all reviews and return total recent count."""
        ref = reference_date or date.today()
        cutoff = ref - timedelta(days=self.recent_window_days)
        recent_count = 0

        for r in self.data.get("reviews", []):
            d_str = r.get("date")
            is_rec = False
            if d_str:
                try:
                    r_date = date.fromisoformat(d_str[:10])
                    if r_date >= cutoff:
                        is_rec = True
                        recent_count += 1
                except Exception:
                    pass
            r["is_recent"] = is_rec

        self.data["recent_reviews_count"] = recent_count
        return recent_count

    # -------------------------------------------------------------------------
    # Parsing Methods (Hybrid GraphQL / DOM / JSON-LD)
    # -------------------------------------------------------------------------

    def parse_airbnb_data(
        self,
        payload: Optional[Dict[str, Any]] = None,
        html_content: Optional[str] = None,
    ) -> Tuple[Optional[float], int, Dict[str, float], List[Dict[str, Any]]]:
        """
        Parse Airbnb rating, total review count, sub_scores, and reviews list.
        """
        rating: Optional[float] = None
        review_count = 0
        sub_scores: Dict[str, float] = {}
        reviews: List[Dict[str, Any]] = []

        if payload and isinstance(payload, dict):
            # 1. Inspect StayProductDetailPage or PdpReviews GraphQL structure
            data_sec = payload.get("data") if isinstance(payload.get("data"), dict) else payload
            presentation = data_sec.get("presentation", {}) if isinstance(data_sec, dict) else {}
            pdp = presentation.get("stayProductDetailPage") or data_sec.get("stayProductDetailPage") or {}

            raw_sections = pdp.get("sections") if isinstance(pdp, dict) else None
            metadata = {}
            review_component = {}

            if isinstance(raw_sections, dict):
                metadata = raw_sections.get("metadata") or {}
                review_component = raw_sections.get("reviewComponent") or {}
            elif isinstance(raw_sections, list):
                for sec in raw_sections:
                    if isinstance(sec, dict):
                        sec_type = (sec.get("sectionComponentType") or sec.get("type") or "").lower()
                        sec_inner = sec.get("section") if isinstance(sec.get("section"), dict) else sec
                        if "review" in sec_type or "reviewcomponent" in str(sec_inner).lower():
                            review_component = sec_inner.get("reviewComponent") or sec_inner
                        if "metadata" in sec_inner and isinstance(sec_inner["metadata"], dict):
                            metadata = sec_inner["metadata"]

            if not metadata:
                metadata = (
                    (pdp.get("metadata") if isinstance(pdp, dict) else None)
                    or (data_sec.get("metadata") if isinstance(data_sec, dict) else None)
                    or (payload.get("metadata") if isinstance(payload, dict) else None)
                    or {}
                )

            # Extract overall rating & review count from node.listingRatingStats (StaysPdpSections)
            node = data_sec.get("node") if isinstance(data_sec, dict) else {}
            if isinstance(node, dict):
                node_stats = node.get("listingRatingStats") or {}
                if isinstance(node_stats, dict):
                    overall_stats = node_stats.get("overallRatingStats") or {}
                    if "ratingAverage" in overall_stats and overall_stats["ratingAverage"] is not None:
                        try:
                            rating = float(overall_stats["ratingAverage"])
                        except Exception:
                            pass
                    if "ratingCount" in overall_stats and overall_stats["ratingCount"] is not None:
                        try:
                            review_count = int(overall_stats["ratingCount"])
                        except Exception:
                            pass
                    for cat in node_stats.get("categoryRatingStats", []):
                        if isinstance(cat, dict):
                            c_name = (cat.get("categoryTypeA") or cat.get("name") or "").lower().replace(" ", "_").replace("-", "_")
                            if c_name in ("checkin", "check_in"):
                                c_name = "check_in"
                            val_dict = cat.get("value") or {}
                            c_val = val_dict.get("ratingAverage") if isinstance(val_dict, dict) else val_dict
                            if c_name and c_val is not None:
                                try:
                                    sub_scores[c_name] = round(float(c_val), 2)
                                except Exception:
                                    pass

            # Extract overall rating
            if rating is None:
                overall_rating = metadata.get("overallRating") or metadata.get("rating") or pdp.get("overallRating")
                if overall_rating is not None:
                    try:
                        rating = float(overall_rating)
                    except Exception:
                        pass

            # Extract review count
            pdp_revs = pdp.get("reviews") if isinstance(pdp, dict) else None
            if review_count == 0 and isinstance(pdp_revs, dict):
                rev_meta = pdp_revs.get("metadata") or {}
                cnt = rev_meta.get("reviewsCount") or rev_meta.get("reviewCount")
                if cnt is not None:
                    try:
                        review_count = int(cnt)
                    except Exception:
                        pass

            if review_count == 0:
                tot_count = metadata.get("reviewCount") or metadata.get("reviewsCount") or pdp.get("reviewCount")
                if tot_count is not None:
                    try:
                        review_count = int(tot_count)
                    except Exception:
                        pass

            # Extract sub-scores
            raw_subscores = []
            if isinstance(review_component, dict) and review_component.get("reviewCategorySubscores"):
                raw_subscores = review_component.get("reviewCategorySubscores")
            elif isinstance(metadata, dict) and metadata.get("reviewCategorySubscores"):
                raw_subscores = metadata.get("reviewCategorySubscores")
            elif isinstance(data_sec, dict) and isinstance(data_sec.get("pdpReviews"), dict) and data_sec["pdpReviews"].get("reviewCategorySubscores"):
                raw_subscores = data_sec["pdpReviews"]["reviewCategorySubscores"]

            for sub in raw_subscores:
                cat = (sub.get("categoryType") or sub.get("title") or "").lower().replace(" ", "_").replace("-", "_")
                if cat in ("checkin", "check_in"):
                    cat = "check_in"
                val = sub.get("rating")
                if cat and val is not None:
                    try:
                        sub_scores[cat] = round(float(val), 2)
                    except Exception:
                        pass

            # Extract reviews list
            raw_reviews = []
            if isinstance(review_component, dict) and review_component.get("reviews"):
                raw_reviews = review_component.get("reviews", [])
            elif isinstance(pdp, dict) and isinstance(pdp.get("reviews"), dict) and isinstance(pdp["reviews"].get("reviews"), list):
                raw_reviews = pdp["reviews"]["reviews"]
            elif isinstance(data_sec, dict) and isinstance(data_sec.get("pdpReviews"), dict) and data_sec["pdpReviews"].get("reviews"):
                raw_reviews = data_sec["pdpReviews"]["reviews"]
            elif isinstance(pdp, dict) and isinstance(pdp.get("reviews"), list):
                raw_reviews = pdp["reviews"]
            elif isinstance(data_sec, dict) and isinstance(data_sec.get("reviews"), list):
                raw_reviews = data_sec["reviews"]
            elif isinstance(payload.get("reviews"), list):
                raw_reviews = payload["reviews"]

            for item in raw_reviews:
                author_info = item.get("author") or item.get("reviewer") or {}
                if isinstance(author_info, dict):
                    author_name = author_info.get("firstName") or author_info.get("name") or item.get("authorName") or "Guest"
                    reviewer_loc = item.get("localizedReviewerLocation") or author_info.get("location")
                else:
                    author_name = str(author_info) if author_info else "Guest"
                    reviewer_loc = item.get("localizedReviewerLocation")

                raw_date = item.get("createdAt") or item.get("date") or item.get("localizedDate") or ""
                r_date = _parse_date_str(raw_date)
                body_text = item.get("comments") or item.get("body") or ""
                r_rating = 5.0
                if "rating" in item and item["rating"] is not None:
                    try:
                        r_rating = float(item["rating"])
                    except Exception:
                        pass

                host_resp = None
                resp_info = item.get("response") or item.get("hostResponse")
                if isinstance(resp_info, str) and resp_info.strip():
                    raw_resp_date = item.get("localizedRespondedDate") or item.get("createdAt") or ""
                    resp_date = _parse_date_str(raw_resp_date)
                    host_resp = {
                        "responder_name": "Villa del Sol Host",
                        "date": resp_date,
                        "body": resp_info.strip(),
                    }
                elif resp_info and isinstance(resp_info, dict) and (resp_info.get("comments") or resp_info.get("body")):
                    raw_resp_date = resp_info.get("createdAt") or resp_info.get("date") or ""
                    resp_date = _parse_date_str(raw_resp_date)
                    host_resp = {
                        "responder_name": resp_info.get("authorName") or "Villa del Sol Host",
                        "date": resp_date,
                        "body": (resp_info.get("comments") or resp_info.get("body") or "").strip(),
                    }

                rev_id = compute_review_id("airbnb", author_name, r_date, body_text)
                reviews.append({
                    "id": rev_id,
                    "platform": "airbnb",
                    "reviewer_name": author_name,
                    "reviewer_location": reviewer_loc,
                    "title": None,
                    "date": r_date,
                    "rating": r_rating,
                    "rating_max": 5.0,
                    "body": body_text.strip(),
                    "host_response": host_resp,
                    "is_recent": False,
                })

        # 2. Fallback to HTML content (JSON-LD, deferred-state scripts, or regex)
        if html_content:
            # Check for embedded state scripts (e.g. data-deferred-state-0 or data-state)
            state_matches = re.findall(r'<script[^>]*id=["\'](?:data-deferred-state|data-state)[^"\']*["\'][^>]*>(.*?)</script>', html_content, re.DOTALL)
            for s_match in state_matches:
                try:
                    state_data = json.loads(s_match)
                    niobe = state_data.get("niobeMinimalClientData") or []
                    for entry in niobe:
                        if isinstance(entry, list) and len(entry) >= 2 and isinstance(entry[1], dict):
                            sr, sc, ssubs, srevs = self.parse_airbnb_data(payload=entry[1])
                            if sr is not None and rating is None:
                                rating = sr
                            if sc > review_count:
                                review_count = sc
                            if ssubs and not sub_scores:
                                sub_scores.update(ssubs)
                            if srevs and not reviews:
                                reviews.extend(srevs)
                except Exception:
                    pass

            if rating is None or review_count == 0:
                # Look for JSON-LD schema
                json_ld_matches = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html_content, re.DOTALL)
                for block in json_ld_matches:
                    try:
                        ld = json.loads(block)
                        agg = ld.get("aggregateRating") or {}
                        if agg:
                            if rating is None and "ratingValue" in agg:
                                rating = float(agg["ratingValue"])
                            if review_count == 0 and "reviewCount" in agg:
                                review_count = int(agg["reviewCount"])
                    except Exception:
                        pass

            if rating is None:
                # e.g. "4.96 · 76 reviews" or "5 · 10 reviews"
                m_score = re.search(r"(\d+(?:\.\d+)?)\s*(?:★|stars?|\(stars?\))?\s*(?:·|\-|\|)?\s*(\d+)\s*reviews?", html_content, re.IGNORECASE)
                if m_score:
                    rating = float(m_score.group(1))
                    review_count = int(m_score.group(2))

        return rating, review_count, sub_scores, reviews

    def parse_vrbo_data(
        self,
        payload: Optional[Dict[str, Any]] = None,
        html_content: Optional[str] = None,
    ) -> Tuple[Optional[float], int, Dict[str, float], List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """
        Parse VRBO native 10.0 scale rating, review count, sub_scores, reviews list, and badge.
        """
        rating: Optional[float] = None
        review_count = 0
        sub_scores: Dict[str, float] = {}
        reviews: List[Dict[str, Any]] = []
        badge: Optional[Dict[str, Any]] = None

        if payload and isinstance(payload, dict):
            # Check Apollo GraphQL or Expedia Reviews API (handle nested data envelope)
            src = payload.get("data") if isinstance(payload.get("data"), dict) else payload
            summary = (
                src.get("propertyReviewSummary")
                or src.get("reviewSummary")
                or payload.get("propertyReviewSummary")
                or payload.get("reviewSummary")
                or {}
            )
            if "rating" in summary and summary["rating"] is not None:
                try:
                    rating = float(summary["rating"])
                except Exception:
                    pass
            if "totalCount" in summary and summary["totalCount"] is not None:
                try:
                    review_count = int(summary["totalCount"])
                except Exception:
                    pass

            # Badge / Awards in GraphQL
            badge_name = (
                summary.get("badgeName")
                or summary.get("badge")
                or src.get("badge")
                or payload.get("badge")
            )
            badge_sub = summary.get("badgeSubtitle") or summary.get("highlight") or src.get("badgeSubtitle")
            if badge_name:
                badge = {
                    "name": str(badge_name),
                    "subtitle": str(badge_sub) if badge_sub else "",
                    "badge_type": "award",
                }

            # Category scores
            for cat_item in summary.get("categoryRatings", []):
                name = (cat_item.get("name") or "").lower().replace(" ", "_")
                val = cat_item.get("rating")
                if name and val is not None:
                    try:
                        sub_scores[name] = round(float(val), 2)
                    except Exception:
                        pass

            rev_list = (
                src.get("reviews")
                or src.get("reviewList")
                or payload.get("reviews")
                or payload.get("reviewList")
                or []
            )
            for item in rev_list:
                author_info = item.get("reviewer") or {}
                author_name = author_info.get("name") or "Guest"
                raw_sub_date = item.get("submissionDate") or item.get("date") or ""
                sub_date = _parse_date_str(raw_sub_date)
                body_text = item.get("text") or item.get("body") or ""
                title_text = item.get("title") or item.get("headline")
                r_rating = 10.0
                if "rating" in item and item["rating"] is not None:
                    try:
                        r_rating = float(item["rating"])
                    except Exception:
                        pass

                host_resp = None
                resp_info = item.get("managementResponse") or item.get("response") or item.get("hostResponse")
                if resp_info and isinstance(resp_info, dict) and (resp_info.get("text") or resp_info.get("body")):
                    host_resp = {
                        "responder_name": "Villa del Sol Host",
                        "date": _parse_date_str(resp_info.get("date") or ""),
                        "body": (resp_info.get("text") or resp_info.get("body") or "").strip(),
                    }

                rev_id = compute_review_id("vrbo", author_name, sub_date, body_text)
                reviews.append({
                    "id": rev_id,
                    "platform": "vrbo",
                    "reviewer_name": author_name,
                    "reviewer_location": author_info.get("location"),
                    "title": title_text,
                    "date": sub_date,
                    "rating": r_rating,
                    "rating_max": 10.0,
                    "body": body_text.strip(),
                    "host_response": host_resp,
                    "is_recent": False,
                })

        if html_content:
            # Check for Loved by Guests badge in HTML
            if "loved by guests" in html_content.lower():
                sub_m = re.search(r"Top\s+(\d+%)\s+of\s+guest\s+reviews(?:\s+in\s+this\s+area)?", html_content, re.IGNORECASE)
                subtitle = f"Top {sub_m.group(1)} of guest reviews in this area" if sub_m else "Top guest reviews in this area"
                badge = {
                    "name": "Loved by Guests",
                    "subtitle": subtitle,
                    "badge_type": "top_percentile",
                }

            if rating is None or review_count == 0:
                # Regex patterns for VRBO summary on native 10.0 scale
                m10 = re.search(r"(\d+(?:\.\d+)?)\s*(?:/\s*10(?:\.0)?(?:\s+(?:Loved by Guests|Exceptional|Wonderful|Excellent))?|\s+Loved by Guests|\s+Exceptional|\s+Wonderful)\s*\(([\d,]+)\s*reviews?\)", html_content, re.IGNORECASE)
                if m10:
                    if rating is None:
                        rating = float(m10.group(1))
                    if review_count == 0:
                        review_count = int(m10.group(2).replace(",", ""))
                else:
                    # Fallback for 5.0 scale if legacy page
                    m5 = re.search(r"(\d+(?:\.\d+)?)\s*/\s*5(?:\.0)?\s*(?:Wonderful|Exceptional|Excellent)?\s*\(([\d,]+)\s*reviews?\)", html_content, re.IGNORECASE)
                    if m5:
                        if rating is None:
                            rating = float(m5.group(1))
                        if review_count == 0:
                            review_count = int(m5.group(2).replace(",", ""))

                # Fallback pattern for modern VRBO: "9.6 out of 10, Loved by Guests" and "See all 24 verified reviews"
                if rating is None:
                    m_rate = re.search(r"(\d+(?:\.\d+)?)\s*(?:/\s*10|out of 10)", html_content, re.IGNORECASE)
                    if m_rate:
                        rating = float(m_rate.group(1))
                if review_count == 0:
                    m_cnt = re.search(r"(?:See all\s+)?([\d,]+)\s*(?:verified reviews|reviews)", html_content, re.IGNORECASE)
                    if m_cnt:
                        review_count = int(m_cnt.group(1).replace(",", ""))

            # Extract review cards from HTML if GraphQL payload returned 0 reviews
            if not reviews and html_content:
                try:
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(html_content, "html.parser")
                    # Strictly target review card nodes; exclude generic layout primitives (.uitk-card, article)
                    review_nodes = soup.select(
                        '[itemprop="review"], [data-testid*="review-card"], [data-testid*="review-item"], '
                        '[data-stid*="review-card"], [data-stid*="review-item"], .review-card'
                    )
                    seen_bodies = set()
                    for rnode in review_nodes:
                        txt = rnode.get_text(separator=" ", strip=True)
                        if not txt or len(txt) < 15:
                            continue

                        # A valid review card must possess an explicit review body element
                        body_el = rnode.select_one('[itemprop="reviewBody"], .review-text, [data-testid*="body"], [data-stid*="review-body"]')
                        if not body_el:
                            continue
                        body_text = body_el.get_text(strip=True)
                        if len(body_text) < 10 or body_text in seen_bodies:
                            continue

                        card_rating = rating or 10.0
                        m_card_rate = re.search(r'(\d+(?:\.\d+)?)\s*/\s*10', txt)
                        if m_card_rate:
                            try:
                                card_rating = float(m_card_rate.group(1))
                            except ValueError:
                                pass

                        author_name = "Guest"
                        author_el = rnode.select_one('[itemprop="author"], .reviewer-name, [data-testid*="author"], [data-stid*="author"]')
                        if author_el:
                            author_name = author_el.get_text(strip=True) or "Guest"

                        sub_date = ""
                        date_el = rnode.select_one('[itemprop="datePublished"], .review-date, [data-testid*="date"], [data-stid*="date"], time')
                        if date_el:
                            sub_date = _parse_date_str(date_el.get_text(strip=True))
                        else:
                            m_date = re.search(r'(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},?\s+\d{4}', txt)
                            if m_date:
                                sub_date = _parse_date_str(m_date.group(0))

                        # Require either a parsed date or an explicit card rating to qualify as a verified review
                        if not sub_date and not m_card_rate:
                            continue

                        seen_bodies.add(body_text)
                        rev_id = compute_review_id("vrbo", author_name, sub_date, body_text)
                        reviews.append({
                            "id": rev_id,
                            "platform": "vrbo",
                            "reviewer_name": author_name,
                            "reviewer_location": None,
                            "title": None,
                            "date": sub_date,
                            "rating": card_rating,
                            "rating_max": 10.0,
                            "body": body_text,
                            "host_response": None,
                            "is_recent": False,
                        })
                except Exception as ex:
                    logger.warning(f"Error parsing VRBO HTML reviews: {ex}")

        return rating, review_count, sub_scores, reviews, badge

    def parse_booking_data(
        self,
        payload: Optional[Dict[str, Any]] = None,
        html_content: Optional[str] = None,
    ) -> Tuple[Optional[float], int, Dict[str, float], List[Dict[str, Any]]]:
        """
        Parse Booking.com native 10.0 scale rating, sub_scores, and reviews list.
        """
        rating: Optional[float] = None
        review_count = 0
        sub_scores: Dict[str, float] = {}
        reviews: List[Dict[str, Any]] = []

        if payload and isinstance(payload, dict):
            if "score" in payload and payload["score"] is not None:
                try:
                    rating = float(payload["score"])
                except Exception:
                    pass
            elif "rating" in payload and payload["rating"] is not None:
                try:
                    rating = float(payload["rating"])
                except Exception:
                    pass

            if "count" in payload and payload["count"] is not None:
                try:
                    review_count = int(payload["count"])
                except Exception:
                    pass

            # Sub scores
            sub_dict = payload.get("sub_scores") or payload.get("subscores") or {}
            for k, v in sub_dict.items():
                try:
                    sub_scores[k.lower().replace(" ", "_")] = round(float(v), 2)
                except Exception:
                    pass

            for item in payload.get("reviews", []):
                author_name = item.get("author_name") or item.get("guest_name") or "Guest"
                r_date = item.get("date") or ""
                if len(r_date) >= 10 and re.match(r"^\d{4}-\d{2}-\d{2}", r_date):
                    r_date = r_date[:10]

                # Combine positive and negative text
                body_parts = []
                pos = item.get("positive_text") or item.get("pros")
                neg = item.get("negative_text") or item.get("cons")
                if pos:
                    body_parts.append(pos.strip())
                if neg:
                    body_parts.append(neg.strip())
                if not body_parts and item.get("body"):
                    body_parts.append(item["body"].strip())
                body_text = "\n\n".join(body_parts)

                r_rating = 10.0
                if "rating" in item and item["rating"] is not None:
                    try:
                        r_rating = float(item["rating"])
                    except Exception:
                        pass

                host_resp = None
                resp = item.get("host_response") or item.get("owner_response")
                if resp and isinstance(resp, dict) and resp.get("body"):
                    host_resp = {
                        "responder_name": resp.get("responder_name") or "Villa del Sol Host",
                        "date": resp.get("date") or "",
                        "body": resp.get("body", "").strip(),
                    }

                rev_id = compute_review_id("booking", author_name, r_date, body_text)
                reviews.append({
                    "id": rev_id,
                    "platform": "booking",
                    "reviewer_name": author_name,
                    "reviewer_location": item.get("country"),
                    "title": item.get("title"),
                    "date": r_date,
                    "rating": r_rating,
                    "rating_max": 10.0,
                    "body": body_text,
                    "host_response": host_resp,
                    "is_recent": False,
                })

        if html_content:
            try:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(html_content, "html.parser")
                if rating is None:
                    badge_el = soup.select_one(".bui-review-score__badge, .review-score-badge, [data-testid='review-score-right-component'], [data-testid='review-score-component']")
                    if badge_el:
                        m_score = re.search(r"(\d+(?:[\.,]\d+)?)", badge_el.get_text())
                        if m_score:
                            try:
                                rating = float(m_score.group(1).replace(",", "."))
                            except ValueError:
                                pass
                if review_count == 0:
                    cnt_el = soup.select_one(".bui-review-score__text, .reviews-count, [data-testid='review-score-right-component'], [data-testid='review-score-component'], #reviews-tab-trigger")
                    if cnt_el:
                        txt_cnt = cnt_el.get_text()
                        m_count = re.search(r"(\d+)\s*(?:verified\s+)?reviews?", txt_cnt, re.IGNORECASE)
                        if not m_count:
                            m_count = re.search(r"reviews?\s*\(([\d,]+)\)", txt_cnt, re.IGNORECASE)
                        if m_count:
                            review_count = int(m_count.group(1).replace(",", ""))

                # Parse review elements (supports legacy .c-review-block, modern review cards, and lightbox items)
                blocks = soup.select(".c-review-block, .review_item, li.review_item, [data-testid*='review-card'], [data-testid*='review-item'], .review-card")
                for block in blocks:
                    author_el = block.select_one(".bui-avatar-block__title, .reviewer_name")
                    author_name = author_el.get_text(strip=True) if author_el else "Guest"

                    country_el = block.select_one(".bui-avatar-block__subtitle, .reviewer_country")
                    country = country_el.get_text(strip=True) if country_el else None

                    date_el = block.select_one(".c-review-block__date, .review_item_date")
                    raw_date = date_el.get_text(strip=True) if date_el else ""
                    r_date = _parse_date_str(raw_date)

                    score_el = block.select_one(".bui-review-score__badge, .review_item_review_score")
                    r_rating = 10.0
                    if score_el:
                        try:
                            r_rating = float(score_el.get_text(strip=True).replace(",", "."))
                        except ValueError:
                            pass

                    title_el = block.select_one(".c-review-block__title, .review_item_header_content")
                    title = title_el.get_text(strip=True) if title_el else None

                    # Pros / Cons pairing
                    body_parts = []
                    pos_el = block.select_one(".c-review__row--positive, .review_pos")
                    if pos_el:
                        pos_copy = BeautifulSoup(str(pos_el), "html.parser")
                        for prefix in pos_copy.select(".c-review__prefix"):
                            prefix.decompose()
                        pos_txt = pos_copy.get_text(separator=" ", strip=True)
                        if pos_txt:
                            body_parts.append(pos_txt)

                    neg_el = block.select_one(".c-review__row--negative, .review_neg")
                    if neg_el:
                        neg_copy = BeautifulSoup(str(neg_el), "html.parser")
                        for prefix in neg_copy.select(".c-review__prefix"):
                            prefix.decompose()
                        neg_txt = neg_copy.get_text(separator=" ", strip=True)
                        if neg_txt:
                            body_parts.append(neg_txt)

                    if not body_parts:
                        body_el = block.select_one(".c-review-block__body, .review_item_review_content")
                        if body_el:
                            body_parts.append(body_el.get_text(separator=" ", strip=True))

                    body_text = "\n\n".join(body_parts)

                    # Host response
                    host_resp = None
                    resp_el = block.select_one(".c-review-block__response, .review_response")
                    if resp_el:
                        resp_body_el = resp_el.select_one(".c-review-block__response__body") or resp_el
                        resp_body = resp_body_el.get_text(strip=True)
                        if resp_body:
                            host_resp = {
                                "responder_name": "Villa del Sol Host",
                                "date": r_date,
                                "body": resp_body,
                            }

                    rev_id = compute_review_id("booking", author_name, r_date, body_text)
                    reviews.append({
                        "id": rev_id,
                        "platform": "booking",
                        "reviewer_name": author_name,
                        "reviewer_location": country,
                        "title": title,
                        "date": r_date,
                        "rating": r_rating,
                        "rating_max": 10.0,
                        "body": body_text,
                        "host_response": host_resp,
                        "is_recent": False,
                    })

                # Modern Booking layout subscores parsing
                if not sub_scores:
                    sub_els = soup.select("[data-testid='review-subscore']")
                    for sel in sub_els:
                        stxt = sel.get_text(separator="|", strip=True)
                        parts = [p.strip() for p in stxt.split("|") if p.strip()]
                        if len(parts) >= 2:
                            k = parts[0].lower().replace(" ", "_")
                            try:
                                v = float(parts[1].replace(",", "."))
                                sub_scores[k] = round(v, 2)
                            except ValueError:
                                pass

                # Modern Booking layout featured reviews parsing if blocks yielded 0 reviews
                if not reviews:
                    raw_feat_cards = soup.select("[data-testid*='featuredreviewcard'], [data-testid='featuredreview']")
                    feat_cards = [
                        el for el in raw_feat_cards
                        if not any(sub in (el.get("data-testid") or "") for sub in ["avatar", "text", "author", "score", "date"])
                    ]
                    seen_feat_text = set()
                    for fcard in feat_cards:
                        txt_el = fcard.select_one("[data-testid*='text']")
                        if not txt_el:
                            continue
                        b_text = txt_el.get_text(strip=True).strip('“"”')
                        if not b_text or len(b_text) < 10 or b_text in seen_feat_text:
                            continue
                        seen_feat_text.add(b_text)
                        av_el = fcard.select_one("[data-testid*='avatar']")
                        author_name = "Guest"
                        loc = None
                        if av_el:
                            av_txt = av_el.get_text(strip=True)
                            if av_txt and len(av_txt) >= 2 and av_txt[0].isupper() and av_txt[1].isupper():
                                av_txt = av_txt[1:]  # Strip avatar letter icon prefix (e.g. "DDedi" -> "Dedi")
                            m_av = re.match(r"^([A-Z][a-z]+(?:\s+[A-Z]\.?)?)(.+)$", av_txt)
                            if m_av:
                                author_name = m_av.group(1)
                                loc = m_av.group(2)
                            else:
                                author_name = av_txt
                        rev_id = compute_review_id("booking", author_name, "", b_text)
                        reviews.append({
                            "id": rev_id,
                            "platform": "booking",
                            "reviewer_name": author_name,
                            "reviewer_location": loc,
                            "title": None,
                            "date": "",
                            "rating": rating or 10.0,
                            "rating_max": 10.0,
                            "body": b_text,
                            "host_response": None,
                            "is_recent": False,
                        })
            except Exception as ex:
                logger.warning(f"Error parsing Booking.com HTML with bs4: {ex}")
                # Fallback to regex if bs4 fails
                if rating is None:
                    m_score = re.search(r'class="[^"]*bui-review-score__badge[^"]*"[^>]*>([\d\.,]+)<', html_content)
                    if m_score:
                        try:
                            rating = float(m_score.group(1).replace(",", "."))
                        except Exception:
                            pass
                if review_count == 0:
                    m_count = re.search(r'(\d+)\s*(?:verified\s+)?reviews?', html_content, re.IGNORECASE)
                    if m_count:
                        review_count = int(m_count.group(1))

        return rating, review_count, sub_scores, reviews

    # -------------------------------------------------------------------------
    # Merge & Deduplication Logic
    # -------------------------------------------------------------------------

    def merge_channel_data(
        self,
        platform_id: str,
        rating: Optional[float],
        review_count: int,
        sub_scores: Dict[str, float],
        new_reviews: List[Dict[str, Any]],
        status: str = "ok",
        badge: Optional[Dict[str, Any]] = None,
    ) -> int:
        """
        Merge scraped platform metrics and new reviews into self.data.
        Existing reviews with identical ReviewID are updated in-place (e.g. host replies).
        New reviews are appended/merged.
        Returns the number of genuinely new reviews added.
        """
        now_iso = datetime.now(timezone.utc).astimezone().isoformat()
        plat = self.data["platforms"].setdefault(platform_id, {
            "platform_id": platform_id,
            "display_name": self.CHANNELS.get(platform_id, {}).get("display_name", platform_id),
            "url": self.CHANNELS.get(platform_id, {}).get("url", ""),
            "scale": self.CHANNELS.get(platform_id, {}).get("scale", "5.0"),
            "rating": None,
            "review_count": 0,
            "sub_scores": {},
            "last_scraped": None,
            "status": "pending",
        })

        if rating is not None:
            plat["rating"] = rating
        if review_count > 0:
            plat["review_count"] = review_count
        elif plat.get("review_count") is None:
            plat["review_count"] = 0
        if sub_scores:
            plat_subs = plat.setdefault("sub_scores", {})
            plat_subs.update(sub_scores)
            if "check_in" in plat_subs and "checkin" in plat_subs:
                del plat_subs["checkin"]
        if badge:
            plat["badge"] = badge
        plat["last_scraped"] = now_iso
        plat["status"] = status

        # Map existing reviews by id
        existing_revs = self.data.setdefault("reviews", [])
        rev_index_map = {r["id"]: idx for idx, r in enumerate(existing_revs)}
        added_count = 0

        for r in new_reviews:
            r_id = r["id"]
            if r_id in rev_index_map:
                # Update in-place to preserve/update host responses or fields
                existing_item = existing_revs[rev_index_map[r_id]]
                if r.get("host_response"):
                    existing_item["host_response"] = r["host_response"]
                if r.get("title") and not existing_item.get("title"):
                    existing_item["title"] = r["title"]
                if r.get("reviewer_location") and not existing_item.get("reviewer_location"):
                    existing_item["reviewer_location"] = r["reviewer_location"]
            else:
                existing_revs.append(r)
                rev_index_map[r_id] = len(existing_revs) - 1
                added_count += 1

        # Re-sort reviews chronologically (newest date first)
        existing_revs.sort(key=lambda x: str(x.get("date") or ""), reverse=True)
        self.recalculate_recency()

        # Compute Harvest Completeness Ratio & Assign Granular Status
        stored_platform_reviews = [r for r in existing_revs if r.get("platform") == platform_id]
        stored_count = len(stored_platform_reviews)
        announced_count = plat.get("review_count", 0)
        if announced_count > 0:
            harvest_ratio = stored_count / announced_count
        else:
            harvest_ratio = 1.0

        plat["harvested_count"] = stored_count
        plat["harvest_ratio"] = round(harvest_ratio, 4)

        if status == "stale_error":
            plat["status"] = "stale_error"
        elif announced_count > 0 and stored_count == 0:
            plat["status"] = "metadata_only"
        elif harvest_ratio >= 0.90:
            plat["status"] = "ok"
        else:
            plat["status"] = "partial_harvest"

        return added_count
    # -------------------------------------------------------------------------
    # Scraping Orchestration & Live Crawlers
    # -------------------------------------------------------------------------

    def _get_corridor_proxy(self, platform_id: str) -> Optional[Dict[str, str]]:
        """
        Resolves the designated out-of-state corridor proxy for a given platform.
        Corridor mapping:
          - airbnb  -> feeder-sf (San Francisco)
          - vrbo    -> feeder-dal (Dallas)
          - booking -> feeder-chi (Chicago)
        If the preferred regional node is not active or unavailable, transparently
        fails over to any healthy out-of-state endpoint from proxy_mgr (FR-PRX-04).
        Returns Playwright proxy dict e.g. {"server": "http://127.0.0.1:56301"} or None.
        """
        chan_info = self.CHANNELS.get(platform_id, {})
        target_feeder = chan_info.get("proxy_feeder")
        if not target_feeder or not self.proxy_mgr:
            return None

        # 1. Check endpoints attribute
        endpoints = getattr(self.proxy_mgr, "endpoints", [])
        if endpoints:
            for ep in endpoints:
                name = getattr(ep, "name", "").lower()
                url = getattr(ep, "url", "")
                if target_feeder.lower() in name and url:
                    return {"server": url}
            # Transparent Failover
            for ep in endpoints:
                url = getattr(ep, "url", "")
                if url:
                    ep_name = getattr(ep, "name", "standby")
                    logger.warning(
                        f"Proxy corridor '{target_feeder}' unavailable for {platform_id}; "
                        f"transparently failing over to {ep_name} ({url})."
                    )
                    return {"server": url}

        # 2. Check get_proxy_configs
        if hasattr(self.proxy_mgr, "get_proxy_configs"):
            try:
                configs = self.proxy_mgr.get_proxy_configs()
                for cfg in configs:
                    name = cfg.get("name", "").lower()
                    if target_feeder.lower() in name and cfg.get("server"):
                        return {"server": cfg["server"]}
                for cfg in configs:
                    if cfg.get("server"):
                        logger.warning(
                            f"Proxy corridor '{target_feeder}' unavailable for {platform_id}; "
                            f"transparently failing over to {cfg.get('name')} ({cfg.get('server')})."
                        )
                        return {"server": cfg["server"]}
            except Exception:
                pass

        return None

    async def _scrape_airbnb_live(
        self,
        context: Any,
        backfill: bool = False,
    ) -> Tuple[Optional[float], int, Dict[str, float], List[Dict[str, Any]]]:
        """
        Live Playwright scraper for Airbnb with GraphQL interception and modal scrolling.
        """
        page = await context.new_page()
        captured_payloads: List[Dict[str, Any]] = []

        async def _on_response(response):
            url = response.url
            if "/api/v3/" in url or "/graphql" in url or "StaysPdpSections" in url or "PdpReviews" in url:
                try:
                    ct = response.headers.get("content-type", "")
                    if "json" in ct or "javascript" in ct:
                        try:
                            data = await response.json()
                        except Exception:
                            body = await response.text()
                            data = json.loads(body)
                        if isinstance(data, dict):
                            captured_payloads.append(data)
                except Exception:
                    pass

        page.on("response", _on_response)

        try:
            try:
                await page.goto(self.CHANNELS["airbnb"]["url"], wait_until="domcontentloaded", timeout=30000)
                try:
                    await page.wait_for_load_state("load", timeout=5000)
                except Exception:
                    pass
            except Exception as nav_err:
                logger.warning(f"Airbnb initial navigation notice: {nav_err}")

            check_cross_border_redirect(page.url)

            if self.stealth_delay > 0:
                await asyncio.sleep(self.stealth_delay)

            try:
                await page.evaluate("() => window.scrollTo(0, 2500)")
            except Exception:
                try:
                    await page.mouse.wheel(0, 2500)
                except Exception:
                    pass

            delay_ms = int(self.stealth_delay * 1000)
            if delay_ms > 0:
                await page.wait_for_timeout(delay_ms)

            rating: Optional[float] = None
            review_count = 0
            sub_scores: Dict[str, float] = {}
            reviews_list: List[Dict[str, Any]] = []

            for p in captured_payloads:
                r, c, subs, revs = self.parse_airbnb_data(payload=p)
                if r is not None and rating is None:
                    rating = r
                if c > review_count:
                    review_count = c
                if subs:
                    sub_scores.update(subs)
                reviews_list.extend(revs)

            if rating is None or review_count == 0:
                html = ""
                for _ in range(4):
                    try:
                        html = await page.content()
                        if html:
                            break
                    except Exception:
                        try:
                            await page.wait_for_load_state("domcontentloaded", timeout=3000)
                        except Exception:
                            pass
                        if self.stealth_delay > 0:
                            await asyncio.sleep(self.stealth_delay)
                        else:
                            await asyncio.sleep(0)

                if html:
                    hr, hc, hsubs, hrevs = self.parse_airbnb_data(html_content=html)
                    if rating is None:
                        rating = hr
                    if review_count == 0:
                        review_count = hc
                    if not sub_scores and hsubs:
                        sub_scores.update(hsubs)
                    if not reviews_list and hrevs:
                        reviews_list.extend(hrevs)

            reviews_by_id: Dict[str, Dict[str, Any]] = {r["id"]: r for r in reviews_list}
            existing_ids = {r["id"] for r in self.data.get("reviews", []) if r.get("platform") == "airbnb"}

            should_paginate = backfill or (review_count > len(reviews_by_id))
            if should_paginate:
                modal_btn = await page.query_selector('button[data-testid="pdp-show-all-reviews-button"]')
                if not modal_btn:
                    modal_btn = await page.query_selector(
                        'button[data-testid*="show-all-reviews"], button:has-text("Show all 7"), button:has-text("Show all 8"), button:has-text("reviews")'
                    )
                if not modal_btn:
                    btns = await page.query_selector_all("button")
                    for b in btns:
                        b_txt = (await b.inner_text()).lower()
                        if "show all" in b_txt and "review" in b_txt:
                            modal_btn = b
                            break

                if modal_btn:
                    try:
                        await page.evaluate("el => el.click()", modal_btn)
                        await page.wait_for_selector(
                            'div[role="dialog"]',
                            timeout=8000,
                        )
                        # Attempt to select "Most recent" sorting if present (FR-DAT-03)
                        try:
                            sort_btn = await page.query_selector('button:has-text("Most recent"), [data-testid*="sort-recent"]')
                            if sort_btn:
                                await page.evaluate("el => el.click()", sort_btn)
                                if self.stealth_delay > 0:
                                    await asyncio.sleep(self.stealth_delay)
                        except Exception:
                            pass

                        modal_panel = await page.query_selector('div[data-testid="pdp-reviews-modal-scrollable-panel"]')
                        consecutive_stalls = 0
                        consecutive_known = 0
                        scroll_cycles = 0
                        max_scroll_cycles = 30 if backfill else 2

                        last_cnt = len(reviews_by_id)
                        parsed_payload_idx = 0
                        while consecutive_stalls < 4 and scroll_cycles < max_scroll_cycles:
                            scroll_cycles += 1
                            if modal_panel:
                                await modal_panel.evaluate("el => el.scrollTop = el.scrollHeight")
                            else:
                                await page.evaluate('''() => {
                                    const dialogs = document.querySelectorAll('div[role="dialog"]');
                                    const target = dialogs[dialogs.length - 1];
                                    if (target) {
                                        target.querySelectorAll('*').forEach(el => {
                                            const style = window.getComputedStyle(el);
                                            if (style.overflowY === 'auto' || style.overflowY === 'scroll') {
                                                el.scrollTop = el.scrollHeight;
                                            }
                                        });
                                    }
                                }''')

                            delay_ms = int(self.stealth_delay * 1000)
                            if delay_ms > 0:
                                await page.wait_for_timeout(max(delay_ms, 1200))

                            new_payloads = captured_payloads[parsed_payload_idx:]
                            parsed_payload_idx = len(captured_payloads)
                            for p in new_payloads:
                                _, _, _, batch_revs = self.parse_airbnb_data(payload=p)
                                for br in batch_revs:
                                    bid = br["id"]
                                    if bid not in reviews_by_id:
                                        reviews_by_id[bid] = br
                                        if bid in existing_ids:
                                            consecutive_known += 1
                                        else:
                                            consecutive_known = 0

                            cur_cnt = len(reviews_by_id)
                            # Condition 1: Reached announced review count
                            if review_count > 0 and cur_cnt >= review_count:
                                logger.info(f"Captured all {cur_cnt}/{review_count} Airbnb reviews; terminating modal pagination.")
                                break
                            # Condition 2: Incremental early stopping
                            if not backfill and consecutive_known >= 3:
                                logger.info("Encountered 3 consecutive known Airbnb reviews in incremental mode; terminating.")
                                break
                            # Condition 3: Check stalls
                            if cur_cnt > last_cnt:
                                last_cnt = cur_cnt
                                consecutive_stalls = 0
                            else:
                                consecutive_stalls += 1
                    except Exception as ex:
                        logger.warning(f"Error during Airbnb modal pagination: {ex}")

            return rating, review_count, sub_scores, list(reviews_by_id.values())
        finally:
            await page.close()

    async def _scrape_vrbo_live(
        self,
        context: Any,
        backfill: bool = False,
    ) -> Tuple[Optional[float], int, Dict[str, float], List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """
        Live Playwright scraper for VRBO with Apollo GraphQL and native 10.0 scale extraction.
        """
        page = await context.new_page()
        captured_payloads: List[Dict[str, Any]] = []

        async def _on_response(response):
            url = response.url
            if "graphql" in url or "review" in url.lower():
                try:
                    ct = response.headers.get("content-type", "")
                    if "application/json" in ct:
                        data = await response.json()
                        captured_payloads.append(data)
                except Exception:
                    pass

        page.on("response", _on_response)

        try:
            await page.goto(self.CHANNELS["vrbo"]["url"], wait_until="domcontentloaded", timeout=30000)
            check_cross_border_redirect(page.url)
            if self.stealth_delay > 0:
                await asyncio.sleep(self.stealth_delay)

            try:
                await page.evaluate("""() => {
                    document.querySelectorAll('#onetrust-banner-sdk, #onetrust-consent-sdk, .onetrust-pc-dark-filter').forEach(el => el.remove());
                }""")
            except Exception:
                pass

            try:
                await page.mouse.wheel(0, 300)
            except Exception:
                pass

            rating: Optional[float] = None
            review_count = 0
            sub_scores: Dict[str, float] = {}
            reviews_list: List[Dict[str, Any]] = []
            badge: Optional[Dict[str, Any]] = None

            for p in captured_payloads:
                vr_res = self.parse_vrbo_data(payload=p)
                if len(vr_res) == 5:
                    r, c, subs, rlist, b = vr_res
                    if b and badge is None:
                        badge = b
                else:
                    r, c, subs, rlist = vr_res
                if r is not None and rating is None:
                    rating = r
                if c > review_count:
                    review_count = c
                if subs:
                    sub_scores.update(subs)
                reviews_list.extend(rlist)

            if rating is None or review_count == 0 or badge is None:
                html = ""
                for _ in range(4):
                    try:
                        html = await page.content()
                        if html:
                            break
                    except Exception:
                        try:
                            await page.wait_for_load_state("domcontentloaded", timeout=3000)
                        except Exception:
                            pass
                        if self.stealth_delay > 0:
                            await asyncio.sleep(self.stealth_delay)
                        else:
                            await asyncio.sleep(0)

                if html:
                    hr, hc, hsubs, _, hbadge = self.parse_vrbo_data(html_content=html)
                    if rating is None:
                        rating = hr
                    if review_count == 0:
                        review_count = hc
                    if not sub_scores and hsubs:
                        sub_scores.update(hsubs)
                    if badge is None and hbadge:
                        badge = hbadge

            # If captured GraphQL yielded no reviews, check window.__APOLLO_STATE__
            if not reviews_list:
                try:
                    apollo_state = await page.evaluate("() => window.__APOLLO_STATE__ || null")
                    if apollo_state and isinstance(apollo_state, dict):
                        for k, v in apollo_state.items():
                            if isinstance(v, dict) and any(rk in k.lower() for rk in ["propertyreview:", "review:", "reviewitem"]):
                                vr_res = self.parse_vrbo_data(payload=v)
                                if len(vr_res) >= 4 and vr_res[3]:
                                    reviews_list.extend(vr_res[3])
                except Exception:
                    pass

            reviews_by_id: Dict[str, Dict[str, Any]] = {r["id"]: r for r in reviews_list}
            existing_ids = {r["id"] for r in self.data.get("reviews", []) if r.get("platform") == "vrbo"}

            should_paginate = backfill or (review_count > len(reviews_by_id))
            if should_paginate:
                btn = await page.query_selector(
                    'button[aria-label*="verified reviews"], button[aria-label*="See all"], [data-stid*="reviews"], '
                    'button:has-text("verified reviews"), button:has-text("See all"), button:has-text("reviews"), '
                    'button:has-text("Guest reviews"), [data-testid*="reviews"]'
                )
                if btn:
                    try:
                        await page.evaluate("el => el.click()", btn)
                        await page.wait_for_selector(
                            'div[role="dialog"], .uitk-sheet, .reviews-container, div[data-testid*="reviews"]',
                            timeout=8000,
                        )
                        sheet = await page.query_selector('.uitk-sheet, div[role="dialog"]')
                        consecutive_stalls = 0
                        consecutive_known = 0
                        scroll_cycles = 0
                        max_scroll_cycles = 30 if backfill else 2

                        last_cnt = len(reviews_by_id)
                        parsed_payload_idx = 0
                        while consecutive_stalls < 3 and scroll_cycles < max_scroll_cycles:
                            scroll_cycles += 1
                            if sheet:
                                await sheet.evaluate("el => { el.scrollTop = el.scrollHeight; }")
                            else:
                                await page.mouse.wheel(0, 1500)

                            delay_ms = int(self.stealth_delay * 1000)
                            if delay_ms > 0:
                                await page.wait_for_timeout(max(delay_ms, 1200))
                            else:
                                await page.wait_for_timeout(500)

                            new_payloads = captured_payloads[parsed_payload_idx:]
                            parsed_payload_idx = len(captured_payloads)
                            for p in new_payloads:
                                vr_res = self.parse_vrbo_data(payload=p)
                                rlist = vr_res[3] if len(vr_res) >= 4 else []
                                for br in rlist:
                                    bid = br["id"]
                                    if bid not in reviews_by_id:
                                        reviews_by_id[bid] = br
                                        if bid in existing_ids:
                                            consecutive_known += 1
                                        else:
                                            consecutive_known = 0

                            # Fallback: Extract rendered review cards from sheet DOM if GraphQL throttled (429)
                            if sheet and len(reviews_by_id) < review_count:
                                try:
                                    sheet_html = await sheet.inner_html()
                                    _, _, _, dom_revs, _ = self.parse_vrbo_data(html_content=sheet_html)
                                    for dr in dom_revs:
                                        did = dr["id"]
                                        if did not in reviews_by_id:
                                            reviews_by_id[did] = dr
                                except Exception:
                                    pass

                            cur_cnt = len(reviews_by_id)
                            if review_count > 0 and cur_cnt >= review_count:
                                break
                            if not backfill and consecutive_known >= 3:
                                break
                            if cur_cnt > last_cnt:
                                last_cnt = cur_cnt
                                consecutive_stalls = 0
                            else:
                                consecutive_stalls += 1
                    except Exception as ex:
                        logger.warning(f"Error during VRBO reviews pagination: {ex}")

            return rating, review_count, sub_scores, list(reviews_by_id.values()), badge
        finally:
            await page.close()

    async def _scrape_booking_live(
        self,
        context: Any,
        backfill: bool = False,
    ) -> Tuple[Optional[float], int, Dict[str, float], List[Dict[str, Any]]]:
        """
        Live Playwright scraper for Booking.com with native 10.0 scale and pros/cons pairing.
        """
        page = await context.new_page()
        try:
            await page.goto(self.CHANNELS["booking"]["url"], wait_until="domcontentloaded", timeout=30000)
            check_cross_border_redirect(page.url)
            if "chal_t=" in page.url:
                if self.stealth_delay > 0:
                    await page.wait_for_timeout(int(self.stealth_delay * 1000 * 3))
            check_cross_border_redirect(page.url)
            if self.stealth_delay > 0:
                await asyncio.sleep(self.stealth_delay)

            try:
                cookie_btn = await page.query_selector(
                    'button#onetrust-accept-btn-handler, button:has-text("Accept"), button:has-text("Agree")'
                )
                if cookie_btn:
                    await cookie_btn.click()
            except Exception:
                pass

            try:
                expand_btn = await page.query_selector(
                    'button[data-testid="fr-read-all-reviews"], button[data-testid*="read-all-actionable"], button:has-text("Read all reviews"), :has-text("Expand ratings and reviews section"), button:has-text("Guest reviews"), #reviews-tab-trigger'
                )
                if expand_btn:
                    await expand_btn.click()
                    delay_ms = int(self.stealth_delay * 1000)
                    if delay_ms > 0:
                        await page.wait_for_timeout(max(delay_ms, 1200))
                    else:
                        await page.wait_for_timeout(500)
            except Exception:
                pass

            try:
                rev_tab = await page.query_selector(
                    '#reviews-tab, a[href*="reviews"], [data-tab="reviews"], button:has-text("Guest reviews"), [data-testid="Property-Header-Nav-Tab-Trigger-reviews"]'
                )
                if rev_tab:
                    await rev_tab.click()
                    delay_ms = int(self.stealth_delay * 1000)
                    if delay_ms > 0:
                        await page.wait_for_timeout(delay_ms)
            except Exception:
                pass

            try:
                await page.wait_for_selector('.hp_rt_lightbox_wrapper, [data-testid*="featuredreview"], #reviews-tab, [data-testid="review-score-component"]', timeout=4000)
            except Exception:
                pass

            html = ""
            for _ in range(4):
                try:
                    html = await page.content()
                    if html:
                        break
                except Exception:
                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=3000)
                    except Exception:
                        pass
                    if self.stealth_delay > 0:
                        await asyncio.sleep(self.stealth_delay)
                    else:
                        await asyncio.sleep(0)

            if html:
                rating, review_count, sub_scores, reviews = self.parse_booking_data(html_content=html)
            else:
                rating, review_count, sub_scores, reviews = None, 0, {}, []
            reviews_by_id = {r["id"]: r for r in reviews}
            existing_ids = {r["id"] for r in self.data.get("reviews", []) if r.get("platform") == "booking"}

            if backfill:
                page_num = 1
                max_pages = 10
                consecutive_known = 0
                while page_num < max_pages:
                    next_btn = await page.query_selector(
                        'a.bui-pagination__link--next, button[aria-label*="Next page"], a[aria-label="Next page"]'
                    )
                    if not next_btn:
                        break
                    try:
                        await next_btn.click()
                        delay_ms = int(self.stealth_delay * 1000)
                        if delay_ms > 0:
                            await page.wait_for_timeout(delay_ms)
                        page_html = await page.content()
                        _, _, _, batch_revs = self.parse_booking_data(html_content=page_html)
                        new_in_page = 0
                        for br in batch_revs:
                            bid = br["id"]
                            if bid not in reviews_by_id:
                                reviews_by_id[bid] = br
                                new_in_page += 1
                                if bid in existing_ids:
                                    consecutive_known += 1
                                else:
                                    consecutive_known = 0
                        if new_in_page == 0:
                            break
                        if consecutive_known >= 3:
                            break
                        page_num += 1
                    except Exception as ex:
                        logger.warning(f"Error clicking Booking pagination: {ex}")
                        break

            return rating, review_count, sub_scores, list(reviews_by_id.values())
        finally:
            await page.close()

    async def sync_channel(
        self,
        platform_id: str,
        force: bool = False,
        backfill: bool = False,
        dry_run: bool = False,
        context: Optional[Any] = None,
        mock_payload: Optional[Dict[str, Any]] = None,
        mock_html: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Synchronize an individual platform channel with error isolation.
        If scraping fails, previous data is preserved and status is marked 'stale_error'.
        """
        if platform_id not in self.CHANNELS:
            raise ValueError(f"Unknown platform ID: {platform_id}")

        chan_info = self.CHANNELS[platform_id]
        logger.info(f"Syncing ratings for {chan_info['display_name']} ({platform_id})...")

        try:
            # Check if mock payload/html was injected (for testing or offline ingestion)
            if mock_payload is not None or mock_html is not None:
                badge = None
                if platform_id == "airbnb":
                    r, c, subs, revs = self.parse_airbnb_data(payload=mock_payload, html_content=mock_html)
                elif platform_id == "vrbo":
                    vrbo_res = self.parse_vrbo_data(payload=mock_payload, html_content=mock_html)
                    if len(vrbo_res) == 5:
                        r, c, subs, revs, badge = vrbo_res
                    else:
                        r, c, subs, revs = vrbo_res
                elif platform_id == "booking":
                    r, c, subs, revs = self.parse_booking_data(payload=mock_payload, html_content=mock_html)
                else:
                    r, c, subs, revs = None, 0, {}, []

                added = self.merge_channel_data(platform_id, r, c, subs, revs, badge=badge)
                if not dry_run:
                    self.save_database()
                plat_data = self.data.get("platforms", {}).get(platform_id, {})
                return {
                    "platform": platform_id,
                    "status": plat_data.get("status", "ok"),
                    "rating": plat_data.get("rating"),
                    "review_count": plat_data.get("review_count", 0),
                    "harvested_count": plat_data.get("harvested_count", 0),
                    "harvest_ratio": plat_data.get("harvest_ratio", 0.0),
                    "new_reviews_added": added,
                }

            # 1. Check proxy requirement for external platforms (Zero Phoenix Policy)
            if chan_info.get("proxy_feeder"):
                if self.proxy_mgr is None:
                    logger.warning(
                        f"No proxy manager configured for {platform_id}; live Playwright network ingestion skipped in offline mode, "
                        "preserving existing database state."
                    )
                    plat_data = self.data.get("platforms", {}).get(platform_id, {})
                    return {
                        "platform": platform_id,
                        "status": plat_data.get("status", "ok"),
                        "rating": plat_data.get("rating"),
                        "review_count": plat_data.get("review_count", 0),
                        "harvested_count": plat_data.get("harvested_count", 0),
                        "harvest_ratio": plat_data.get("harvest_ratio", 0.0),
                        "new_reviews_added": 0,
                    }
                if context is None:
                    proxy_cfg = self._get_corridor_proxy(platform_id)
                    if proxy_cfg is None:
                        raise RuntimeError(
                            f"No healthy proxy corridor available for {platform_id}. "
                            "Direct connection aborted to enforce Zero Phoenix Policy."
                        )

            # 3. Live Playwright scraping execution
            badge = None
            if context is not None:
                if platform_id == "airbnb":
                    r, c, subs, revs = await self._scrape_airbnb_live(context, backfill=backfill)
                elif platform_id == "vrbo":
                    vr_res = await self._scrape_vrbo_live(context, backfill=backfill)
                    r, c, subs, revs, badge = vr_res
                elif platform_id == "booking":
                    r, c, subs, revs = await self._scrape_booking_live(context, backfill=backfill)
                else:
                    r, c, subs, revs = None, 0, {}, []
            else:
                # Standalone channel run: launch browser and dedicated context
                from playwright.async_api import async_playwright
                proxy_cfg = self._get_corridor_proxy(platform_id)
                if chan_info.get("proxy_feeder") and proxy_cfg is None:
                    raise RuntimeError(
                        f"No healthy proxy corridor available for {platform_id}. "
                        "Direct connection aborted to enforce Zero Phoenix Policy."
                    )
                async with async_playwright() as p:
                    browser = await p.chromium.launch(
                        headless=self.headless,
                        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
                    )
                    try:
                        ctx = await browser.new_context(
                            viewport={"width": 1366, "height": 850},
                            user_agent=(
                                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
                            ),
                            proxy=proxy_cfg,
                        )
                        try:
                            if platform_id == "airbnb":
                                r, c, subs, revs = await self._scrape_airbnb_live(ctx, backfill=backfill)
                            elif platform_id == "vrbo":
                                vr_res = await self._scrape_vrbo_live(ctx, backfill=backfill)
                                r, c, subs, revs, badge = vr_res
                            elif platform_id == "booking":
                                r, c, subs, revs = await self._scrape_booking_live(ctx, backfill=backfill)
                            else:
                                r, c, subs, revs = None, 0, {}, []
                        finally:
                            await ctx.close()
                    finally:
                        await browser.close()

            added = self.merge_channel_data(platform_id, r, c, subs, revs, badge=badge)
            if not dry_run:
                self.save_database()

            plat_data = self.data.get("platforms", {}).get(platform_id, {})
            return {
                "platform": platform_id,
                "status": plat_data.get("status", "ok"),
                "rating": plat_data.get("rating"),
                "review_count": plat_data.get("review_count", 0),
                "harvested_count": plat_data.get("harvested_count", 0),
                "harvest_ratio": plat_data.get("harvest_ratio", 0.0),
                "new_reviews_added": added,
            }

        except Exception as e:
            logger.warning(f"Error scraping {platform_id}: {e}. Preserving previous data.")
            if platform_id in self.data.get("platforms", {}):
                self.data["platforms"][platform_id]["status"] = "stale_error"
                try:
                    if not dry_run:
                        self.save_database()
                except Exception:
                    pass
            plat_data = self.data.get("platforms", {}).get(platform_id, {})
            return {
                "platform": platform_id,
                "status": "stale_error",
                "error": str(e),
                "rating": plat_data.get("rating"),
                "review_count": plat_data.get("review_count", 0),
                "harvested_count": plat_data.get("harvested_count", 0),
                "harvest_ratio": plat_data.get("harvest_ratio", 0.0),
                "new_reviews_added": 0,
            }

    async def sync_all(
        self,
        platforms: Optional[List[str]] = None,
        force: bool = False,
        backfill: bool = False,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Synchronize multiple platforms concurrently with error isolation.
        Launches a single Playwright Chromium instance with isolated corridor contexts (FR-ORCH-01).
        """
        target_platforms = platforms or list(self.CHANNELS.keys())
        platforms_needing_browser = list(target_platforms)

        if self.proxy_mgr is None or not platforms_needing_browser:
            # Offline or direct-only run
            tasks = [
                self.sync_channel(p, force=force, backfill=backfill, dry_run=dry_run)
                for p in target_platforms
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
        else:
            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=self.headless,
                    args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
                )
                contexts: Dict[str, Any] = {}
                try:
                    for p_id in platforms_needing_browser:
                        proxy_cfg = self._get_corridor_proxy(p_id)
                        if self.CHANNELS[p_id].get("proxy_feeder") and proxy_cfg is None:
                            logger.error(
                                f"No healthy proxy corridor available for {p_id}. "
                                "Direct connection aborted to enforce Zero Phoenix Policy."
                            )
                            continue
                        try:
                            ctx = await browser.new_context(
                                viewport={"width": 1366, "height": 850},
                                user_agent=(
                                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
                                ),
                                proxy=proxy_cfg,
                            )
                            contexts[p_id] = ctx
                        except Exception as ctx_err:
                            logger.error(f"Failed to create browser context for {p_id}: {ctx_err}")

                    tasks = [
                        self.sync_channel(
                            p,
                            force=force,
                            backfill=backfill,
                            dry_run=dry_run,
                            context=contexts.get(p),
                        )
                        for p in target_platforms
                    ]
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                finally:
                    for ctx in contexts.values():
                        try:
                            await ctx.close()
                        except Exception:
                            pass
                    await browser.close()

        summary = {"channels": {}, "total_new_reviews": 0, "successful": 0, "partial": 0, "errors": 0}
        for idx, res in enumerate(results):
            p_id = target_platforms[idx]
            if isinstance(res, Exception):
                logger.error(f"Platform sync failed for {p_id}: {res}")
                summary["channels"][p_id] = {"status": "error", "error": str(res)}
                summary["errors"] += 1
            else:
                summary["channels"][p_id] = res
                summary["total_new_reviews"] += res.get("new_reviews_added", 0)
                if res.get("status") == "ok":
                    summary["successful"] += 1
                elif res.get("status") in ("partial_harvest", "metadata_only"):
                    summary["partial"] = summary.get("partial", 0) + 1
                else:
                    summary["errors"] += 1

        if not dry_run:
            self.save_database()
        return summary

