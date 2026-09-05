"""
Comp Evaluator & Desirability Adjustment Engine.
Implements the 5-factor weighted rubric aligned during user interview:
1. Outdoor Resort Yard (30%)
2. Bedrooms, Bathrooms & Capacity (25%)
3. Interior Luxury & Finishes (20%)
4. Location & Corridor (15%)
5. Reputation & Review Quality (10%)

Computes:
- is_valid_comp (bool)
- validity_reason (str)
- category_scores (dict)
- composite_score (float)
- desirability_ratio (float, comp_score / our_score)
- rationale (str)
"""

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("comp_evaluator")


class CompEvaluator:
    """Evaluates competitor listings against Villa del Sol baseline."""

    REGISTRY_PATH = Path("config/comps_registry.json")
    OUR_PROFILE_PATH = Path("data/our_property_profile.json")
    ENRICHED_DIR = Path("data/enriched_comps")

    # Villa del Sol benchmark composite score
    OUR_BASELINE_SCORE = 88.0

    CATEGORY_WEIGHTS = {
        "outdoor": 0.30,
        "capacity": 0.25,
        "interior": 0.20,
        "location": 0.15,
        "reputation": 0.10,
    }

    def __init__(self):
        self.our_profile = self._load_our_profile()

    def _load_our_profile(self) -> Dict[str, Any]:
        """Load Villa del Sol profile."""
        if self.OUR_PROFILE_PATH.exists():
            try:
                return json.loads(self.OUR_PROFILE_PATH.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {
            "property_name": "Villa del Sol",
            "bedrooms": 6,
            "bathrooms": 6.0,
            "max_guests": 16,
            "lot_size": "0.75 acre",
            "address": {"addressLocality": "Tempe"},
            "rating": 4.83,
            "reviews": 76,
        }

    @classmethod
    def extract_pool_specs(
        cls,
        all_text: str,
        amenities: List[str],
        listing_id: str = "",
        reviews: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Extract pool heating status and pool size from amenities, textual highlights, and guest reviews.
        Returns:
            {
                "has_pool": bool,
                "heating": "free" | "standard_heated" | "fee" | "unheated",
                "heating_source": str,
                "pool_size": "large" | "standard" | "plunge",
                "size_source": str,
                "gallons": Optional[int],
            }
        """
        if str(listing_id) == "573857947793833342":
            return {
                "has_pool": True,
                "heating": "free",
                "heating_source": "Villa del Sol verified specs: year-round free heated saltwater pool",
                "pool_size": "large",
                "size_source": "30,000-gallon saltwater pool with rock waterfall grotto",
                "gallons": 30000,
            }

        text_lower = all_text.lower()
        amenities_lower = [a.lower() for a in amenities]

        # 1. Has Pool Check
        has_pool = any("pool" in a and "table" not in a for a in amenities_lower) or any(
            w in text_lower for w in ["swimming pool", "private pool", "heated pool", "resort pool", "lap pool", "plunge pool"]
        )
        if not has_pool and "pool" in text_lower:
            if not any(neg in text_lower for neg in ["no pool", "without a pool", "does not have a pool", "no swimming pool"]):
                has_pool = True

        if not has_pool:
            return {
                "has_pool": False,
                "heating": "none",
                "heating_source": "No swimming pool found",
                "pool_size": "none",
                "size_source": "No swimming pool found",
                "gallons": None,
            }

        # 2. Pool Heating Status
        # Determine if we have comprehensive listing details (verified amenities list or detailed description)
        has_full_details = len(amenities) >= 10 or len(text_lower) > 250

        free_patterns = [
            "free pool heat", "free heated pool", "free pool heating", "complimentary pool heat",
            "complimentary heated pool", "pool heat included", "pool heat is included",
            "pool heat is always included", "no pool heat fee", "|free pool heat|",
            "free heated", "heated pool included", "heated pool is free"
        ]
        fee_patterns = [
            "pool heat fee", "pool heating fee", "fee to heat", "heat is available for $",
            "pool heat is $", "pool heat is available upon request", "additional fee for pool heat",
            "pool heating upon request", "pool heating available upon request", "optional pool heat",
            "pool heat avail", "pool heating available for a fee", "fee for pool heating"
        ]

        # Default: if full amenities/description not yet scraped, assume standard heated for luxury cohort.
        # Only declare as confirmed unheated when full profile details have been verified and lack heating.
        heating = "standard_heated" if not has_full_details else "unheated"
        heating_source = (
            "Heated pool assumed (standard luxury tier default - full amenities not yet scraped)"
            if not has_full_details
            else "Unheated (no pool heating mentioned in verified amenities/description)"
        )

        if any(p in text_lower for p in free_patterns):
            heating = "free"
            heating_source = "Explicit free / complimentary pool heat mentioned in listing text"
        elif any(p in text_lower for p in fee_patterns) or re.search(r"pool heat[^\.\n\$\d]*\$\d+", text_lower):
            heating = "fee"
            heating_source = "Explicit pool heating fee disclosed in listing text"
        elif any("heated" in a and "pool" in a for a in amenities_lower) or any(
            w in text_lower for w in ["htd pool", "heated pool", "heated private pool", "pool is heated", "pool heating"]
        ):
            heating = "standard_heated"
            heating_source = "Heated pool declared in amenities/title (standard/unspecified fee)"

        # 2b. Guest Review Signals
        # Guest reviews provide strong empirical ground-truth on pool heating and fees
        reviews_combined = " ".join(reviews or []).lower()
        if reviews_combined:
            review_free_patterns = [
                "free heated pool", "free pool heat", "pool heat was included",
                "pool heat included", "complimentary pool heat", "complimentary heated pool",
            ]
            review_fee_patterns = [
                "paid for pool heat", "paid the pool heat", "pool heat fee",
                "fee to heat the pool", "paid to heat the pool", "extra for pool heat",
                "charged for pool heat", "pool heating fee",
            ]
            review_heated_patterns = [
                "pool was heated", "heated pool was", "heated the pool", "pool was warm",
                "warm pool", "loved the heated pool", "enjoyed the heated pool",
                "pool temp was", "pool temperature was", "heated pool is great",
                "swam in the heated pool", "kids loved the heated pool",
            ]
            review_unheated_patterns = [
                "pool was unheated", "unheated pool", "pool was freezing",
                "pool was too cold to swim", "no pool heater", "pool has no heat",
                "could not use the pool", "couldn't use the pool because it was cold",
            ]

            if any(p in reviews_combined for p in review_free_patterns):
                heating = "free"
                heating_source = "Free pool heat confirmed by guest reviews"
            elif any(p in reviews_combined for p in review_fee_patterns) and heating != "free":
                heating = "fee"
                heating_source = "Pool heating fee confirmed by guest reviews"
            elif any(p in reviews_combined for p in review_heated_patterns) and heating in ("unheated", "standard_heated"):
                heating = "standard_heated"
                heating_source = "Heated pool confirmed by guest reviews"
            elif any(p in reviews_combined for p in review_unheated_patterns) and heating not in ("free", "standard_heated", "fee"):
                heating = "unheated"
                heating_source = "Unheated / cold pool reported by guest reviews"

        # 3. Pool Size & Volume
        gallons = None
        m_gal = re.search(r"(\d+[\d,]*)\s*(?:gallon|gal)\s*(?:pool)?", text_lower)
        if m_gal:
            try:
                gallons = int(m_gal.group(1).replace(",", ""))
            except Exception:
                gallons = None

        m_dim = re.search(r"(\d+)\s*(?:ft|')?\s*[xX]\s*(\d+)\s*(?:ft|')?\s*(?:pool)?", text_lower)

        plunge_patterns = ["plunge pool", "cocktail pool", "spool", "small pool", "dip pool"]
        large_patterns = [
            "resort-style pool", "resort style pool", "resort pool", "oversized pool",
            "massive pool", "huge pool", "olympic pool", "lap pool", "waterfall grotto",
            "water slide", "lazy river", "grotto"
        ]

        if any(p in text_lower for p in plunge_patterns) or any("plunge pool" in a for a in amenities_lower) or (gallons and gallons < 10000):
            pool_size = "plunge"
            size_source = "Small / cocktail / plunge pool"
        elif (gallons and gallons >= 25000) or any(p in text_lower for p in large_patterns) or (m_dim and (int(m_dim.group(1)) >= 35 or int(m_dim.group(2)) >= 35)):
            pool_size = "large"
            size_source = f"Large / resort-scale pool ({gallons:,} gal)" if gallons else "Large / resort-scale pool with water features"
        else:
            pool_size = "standard"
            size_source = "Standard residential backyard pool"

        return {
            "has_pool": True,
            "heating": heating,
            "heating_source": heating_source,
            "pool_size": pool_size,
            "size_source": size_source,
            "gallons": gallons,
        }

    def _evaluate_single_season(
        self,
        comp_meta: Dict[str, Any],
        enriched: Dict[str, Any],
        all_text: str,
        sqft: Optional[int],
        br: int,
        ba: float,
        beds: int,
        guests: int,
        location: str,
        rating: float,
        reviews: int,
        pool_specs: Dict[str, Any],
        is_winter: bool,
    ) -> Dict[str, Any]:
        """Evaluate a comp for a specific season (Winter Oct-Apr vs Summer May-Sep)."""
        # A. Outdoor Yard (30%)
        has_spa = any(w in all_text for w in ["spa", "hot tub", "jacuzzi", "whirlpool"])
        has_tennis = any(w in all_text for w in ["tennis court", "tennis"])
        has_court = any(w in all_text for w in ["basketball", "pickleball", "sports court", "sport court", "half court"]) or has_tennis
        has_multiple_courts = (has_tennis and any(w in all_text for w in ["pickleball", "basketball"])) or (
            "pickleball" in all_text and "basketball" in all_text
        )
        has_sauna = any(w in all_text for w in ["sauna", "steam room", "cold plunge", "ice bath"])
        has_green = any(w in all_text for w in ["putting green", "mini golf", "turf"])
        has_bbq_fire = any(w in all_text for w in ["bbq", "grill", "fire pit", "outdoor kitchen", "cabana", "gazebo"])
        has_grotto = any(w in all_text for w in ["grotto", "waterfall", "slide", "lazy river"])

        heating = pool_specs.get("heating", "unheated")
        pool_size = pool_specs.get("pool_size", "standard")

        if is_winter:
            # Winter (Oct 1 - Apr 30): Pool heating is paramount
            outdoor_score = 55  # Base for having a pool in winter
            if heating == "free":
                outdoor_score += 18  # 2x hot tub boost
            elif heating == "standard_heated":
                outdoor_score += 12
            elif heating == "fee":
                outdoor_score += 8
            else:  # unheated
                outdoor_score -= 10  # Winter unheated penalty

            if has_spa:
                outdoor_score += 9  # Hot tub (half of free pool heat)
        else:
            # Summer (May 1 - Sep 30): Water is naturally 85-92F
            outdoor_score = 65  # High summer base
            if heating == "free":
                outdoor_score += 6
            elif heating in ("standard_heated", "fee"):
                outdoor_score += 4
            # Unheated has no penalty in summer

            if has_spa:
                outdoor_score += 5

        # Pool size adjustment
        if pool_size == "large":
            outdoor_score += 6
        elif pool_size == "plunge":
            outdoor_score -= 8

        # Sports courts
        if has_multiple_courts:
            outdoor_score += 16
        elif has_tennis:
            outdoor_score += 14
        elif has_court:
            outdoor_score += 10

        # Other outdoor features
        if has_sauna:
            outdoor_score += 6
        if has_green:
            outdoor_score += 5
        if has_bbq_fire:
            outdoor_score += 5
        if has_grotto:
            outdoor_score += 5

        outdoor_score = min(100, max(25, outdoor_score))

        # B. Bedrooms & Bathrooms (25%)
        capacity_score = 60
        if br >= 7:
            capacity_score += 20
        elif br == 6:
            capacity_score += 15
        elif br == 5:
            capacity_score += 8

        if ba >= 5.5:
            capacity_score += 15
        elif ba >= 4.5:
            capacity_score += 10
        elif ba >= 3.5:
            capacity_score += 5

        if guests >= 16:
            capacity_score += 10
        elif guests >= 14:
            capacity_score += 6

        has_casita = any(w in all_text for w in ["casita", "guest house", "guesthouse", "guest suite", "detached"])
        if has_casita:
            capacity_score += 5

        if sqft:
            if sqft >= 6500:
                capacity_score += 8
            elif sqft >= 5000:
                capacity_score += 4
            elif sqft < 4000:
                capacity_score -= 5

        capacity_score = min(100, max(40, capacity_score))

        # C. Interior Luxury & Entertainment (20%)
        has_billiards = any(w in all_text for w in ["pool table", "billiards", "billiard"])
        has_theater = any(w in all_text for w in ["theatre", "theater", "cinema", "movie room"])
        has_game_room = any(w in all_text for w in ["game room", "arcade", "ping pong", "foosball", "shuffleboard"])
        has_chef_kitchen = any(w in all_text for w in ["chef", "stainless", "sub-zero", "subzero", "viking", "miele", "wolf", "wine cooler", "granite", "quartz"])
        has_luxury_vibe = any(w in all_text for w in ["luxury", "estate", "remodeled", "designer", "mansion", "resort"])

        interior_score = 70
        if has_billiards:
            interior_score += 8
        if has_theater:
            interior_score += 8
        if has_game_room:
            interior_score += 6
        if has_chef_kitchen:
            interior_score += 7
        if has_luxury_vibe:
            interior_score += 5
        interior_score = min(100, interior_score)

        # D. Location Corridor (15%)
        if any(w in location or w in all_text[:200] for w in ["paradise valley", "pv", "old town", "scottsdale"]):
            location_score = 95
        elif any(w in location or w in all_text[:200] for w in ["tempe", "south tempe", "arcadia"]):
            location_score = 88
        elif any(w in location or w in all_text[:200] for w in ["chandler", "gilbert", "ahwatukee"]):
            location_score = 82
        elif "mesa" in location or "mesa" in all_text[:200]:
            location_score = 78
        else:
            location_score = 75

        # E. Reputation & Reviews (10%)
        reputation_score = 75
        if rating >= 4.95 and reviews >= 20:
            reputation_score = 98
        elif rating >= 4.90 and reviews >= 15:
            reputation_score = 94
        elif rating >= 4.80:
            reputation_score = 88
        elif rating >= 4.70:
            reputation_score = 78
        elif rating > 0:
            reputation_score = 70
        else:
            reputation_score = 75

        # Composite & Ratio
        composite = (
            self.CATEGORY_WEIGHTS["outdoor"] * outdoor_score
            + self.CATEGORY_WEIGHTS["capacity"] * capacity_score
            + self.CATEGORY_WEIGHTS["interior"] * interior_score
            + self.CATEGORY_WEIGHTS["location"] * location_score
            + self.CATEGORY_WEIGHTS["reputation"] * reputation_score
        )
        composite = round(composite, 1)

        SENSITIVITY = 2.0
        delta = (composite - self.OUR_BASELINE_SCORE) / self.OUR_BASELINE_SCORE
        raw_ratio = 1.0 + (SENSITIVITY * delta)
        ratio = round(max(0.65, min(1.35, raw_ratio)), 2)

        # Rationale
        highlights = []
        shortcomings = []

        if heating == "free":
            highlights.append("free heated pool")
        elif heating == "standard_heated":
            highlights.append("heated pool")
        elif heating == "fee":
            shortcomings.append("pool heat fee required")
        elif is_winter:
            shortcomings.append("unheated pool in winter")

        if pool_size == "large":
            highlights.append("resort-scale pool")
        elif pool_size == "plunge":
            shortcomings.append("small plunge pool")

        if has_tennis:
            highlights.append("private tennis court")
        elif has_court:
            highlights.append("dedicated sports court")

        if has_sauna:
            highlights.append("private sauna")
        if has_theater:
            highlights.append("movie theater")
        if has_spa:
            highlights.append("heated spa")
        if has_billiards:
            highlights.append("pool table")

        if sqft and sqft >= 6500:
            highlights.append(f"{sqft:,} sq ft estate")
        elif br >= 7:
            highlights.append(f"{br} bedrooms")
        elif br < 6:
            shortcomings.append(f"{br} BR vs our 6 BR")

        if ba < 4.5:
            shortcomings.append(f"{ba} baths")

        if location_score > 90:
            highlights.append("Scottsdale location premium")
        elif location_score < 80:
            shortcomings.append(f"{location.title()} location discount")

        season_label = "Winter" if is_winter else "Summer"
        if ratio >= 1.05:
            pct = round((ratio - 1.0) * 100)
            summary = f"Premium comp ({pct}% superior desirability, {season_label}). Features " + ", ".join(highlights[:3]) + "."
        elif ratio <= 0.95:
            pct = round((1.0 - ratio) * 100)
            summary = f"Moderate comp ({pct}% lower desirability, {season_label}). "
            if highlights and shortcomings:
                summary += f"Features {highlights[0]}, but noted gaps: " + ", ".join(shortcomings[:2]) + "."
            elif highlights:
                summary += "Features " + ", ".join(highlights[:2]) + "."
            elif shortcomings:
                summary += "Noted gaps: " + ", ".join(shortcomings[:3]) + "."
            else:
                summary += f"{br}BR estate in {location.title()} with standard luxury amenities."
        else:
            summary = f"Direct peer comp (near-equal quality, {season_label}). {br}BR / {ba}BA estate matching Villa del Sol's capacity and luxury tier."

        return {
            "desirability_ratio": ratio,
            "composite_score": composite,
            "category_scores": {
                "outdoor": outdoor_score,
                "capacity": capacity_score,
                "interior": interior_score,
                "location": location_score,
                "reputation": reputation_score,
            },
            "rationale": summary,
        }

    def evaluate_comp(
        self,
        comp_meta: Dict[str, Any],
        enriched_data: Optional[Dict[str, Any]] = None,
        season: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Evaluate a single competitor listing against Villa del Sol.
        If season is 'winter' or 'summer', returns that season's evaluation.
        If season is None, returns a merged dict with both winter_ratio and summer_ratio.
        """
        cid = str(comp_meta.get("listing_id", ""))
        enriched = enriched_data or {}
        if not enriched and cid:
            cached_path = self.ENRICHED_DIR / f"{cid}.json"
            if cached_path.exists():
                try:
                    enriched = json.loads(cached_path.read_text(encoding="utf-8"))
                except Exception:
                    pass

        # Combine metadata and enriched details
        title = enriched.get("title") or comp_meta.get("name") or comp_meta.get("title", "Luxury Estate")
        desc = (enriched.get("description") or comp_meta.get("description") or "").lower()
        amenities = [a.lower() for a in (enriched.get("amenities") or comp_meta.get("amenities") or [])]
        raw_snippet = (comp_meta.get("raw_snippet") or enriched.get("raw_snippet") or "").lower()
        all_text = (title + " " + desc + " " + raw_snippet + " " + " ".join(amenities)).lower()

        # Extract pool specs (including guest review signals)
        reviews = (
            enriched.get("review_snippets")
            or enriched.get("reviews_samples")
            or comp_meta.get("review_snippets")
            or []
        )
        pool_specs = self.extract_pool_specs(all_text, amenities, listing_id=cid, reviews=reviews)

        # Extract square footage if mentioned in all_text
        sqft = None
        m_sqft = re.search(r"(\d[\d,]*)\s*(?:sq\s*ft|sqft|square\s*feet|sq\s*feet)", all_text)
        if m_sqft:
            try:
                sqft = int(m_sqft.group(1).replace(",", ""))
            except Exception:
                sqft = None

        br = comp_meta.get("bedrooms") or enriched.get("bedrooms")
        if not br:
            m_br = re.search(r"(\d+)\s*(?:br|bd|bedrooms?)\b", all_text, re.IGNORECASE)
            br = int(m_br.group(1)) if m_br else 5
        else:
            try:
                br = int(br)
            except Exception:
                br = 5

        ba = comp_meta.get("baths") or enriched.get("baths")
        if not ba:
            m_ba = re.search(r"(\d+(?:\.\d+)?)\s*(?:ba|baths?|bathrooms?)\b", all_text, re.IGNORECASE)
            ba = float(m_ba.group(1)) if m_ba else 4.0
        else:
            try:
                ba = float(ba)
            except Exception:
                ba = 4.0

        beds = comp_meta.get("beds") or enriched.get("beds")
        if not beds:
            m_beds = re.search(r"(\d+)\s*beds?\b(?!room)", all_text, re.IGNORECASE)
            beds = int(m_beds.group(1)) if m_beds else br
        else:
            try:
                beds = int(beds)
            except Exception:
                beds = br

        # Determine guest capacity
        guests = comp_meta.get("guests") or comp_meta.get("max_guests") or comp_meta.get("accommodates") or enriched.get("guests")
        if not guests:
            for item in enriched.get("overview", []):
                if "guest" in item.lower():
                    digits = "".join(c for c in item if c.isdigit())
                    if digits:
                        guests = int(digits)
                        break
        if not guests:
            m_g = re.search(r"(?:up to|sleeps|accommodates)\s*(\d+)", all_text, re.IGNORECASE)
            if m_g:
                guests = int(m_g.group(1))
        if not guests:
            guests = max(12, beds * 2) if br >= 5 else (beds * 2)
        else:
            try:
                guests = int(str(guests).replace("+", ""))
            except Exception:
                guests = 14

        location = (comp_meta.get("location") or "").lower()
        if not location and enriched.get("address"):
            loc_val = enriched["address"]
            location = loc_val.get("addressLocality", "") if isinstance(loc_val, dict) else str(loc_val)
            location = location.lower()

        rating = enriched.get("rating") or comp_meta.get("rating")
        reviews = enriched.get("reviews") or comp_meta.get("reviews") or 0
        try:
            rating = float(rating) if rating is not None else 4.80
        except Exception:
            rating = 4.80
        try:
            reviews = int(reviews)
        except Exception:
            reviews = 0

        # -------------------------------------------------------------
        # 1. VALIDITY CHECK
        # -------------------------------------------------------------
        has_pool = pool_specs["has_pool"]
        if not has_pool:
            return {
                "is_valid_comp": False,
                "validity_reason": "Disqualified: Property lacks a private swimming pool.",
                "desirability_ratio": 0.50,
                "winter_ratio": 0.50,
                "summer_ratio": 0.50,
                "pool_specs": pool_specs,
                "category_scores": {"outdoor": 30, "capacity": 60, "interior": 60, "location": 70, "reputation": 70},
                "composite_score": 52.0,
                "rationale": "Disqualified from luxury comp cohort because verified listing details show no private swimming pool.",
            }

        if br < 4 or (br < 5 and guests < 12):
            return {
                "is_valid_comp": False,
                "validity_reason": f"Disqualified: Only {br} bedrooms (capacity {guests} guests), below the 5 BR / 12 guest threshold.",
                "desirability_ratio": 0.55,
                "winter_ratio": 0.55,
                "summer_ratio": 0.55,
                "pool_specs": pool_specs,
                "category_scores": {"outdoor": 65, "capacity": 40, "interior": 65, "location": 75, "reputation": 75},
                "composite_score": 60.0,
                "rationale": f"Disqualified: Under-sized listing ({br} BR / {guests} guests) cannot benchmark 16-guest luxury estates.",
            }

        outlier_cities = ["surprise", "buckeye", "casa grande", "maricopa", "goodyear", "sun city"]
        if any(city in location for city in outlier_cities) or any(city in all_text[:200] for city in outlier_cities):
            return {
                "is_valid_comp": False,
                "validity_reason": f"Disqualified: Located in peripheral suburb ({location.title()}), outside competitive drive corridor.",
                "desirability_ratio": 0.60,
                "winter_ratio": 0.60,
                "summer_ratio": 0.60,
                "pool_specs": pool_specs,
                "category_scores": {"outdoor": 70, "capacity": 75, "interior": 70, "location": 40, "reputation": 75},
                "composite_score": 66.0,
                "rationale": f"Disqualified: Located too far from the Scottsdale/Tempe corridor in {location.title()}.",
            }

        # -------------------------------------------------------------
        # 2. EVALUATE SEASONS
        # -------------------------------------------------------------
        if season == "summer":
            res = self._evaluate_single_season(
                comp_meta, enriched, all_text, sqft, br, ba, beds, guests, location, rating, reviews, pool_specs, is_winter=False
            )
            res.update({
                "is_valid_comp": True,
                "validity_reason": f"Valid {br}BR luxury estate comp in {location.title() or 'Phoenix corridor'}.",
                "pool_specs": pool_specs,
            })
            return res
        elif season == "winter":
            res = self._evaluate_single_season(
                comp_meta, enriched, all_text, sqft, br, ba, beds, guests, location, rating, reviews, pool_specs, is_winter=True
            )
            res.update({
                "is_valid_comp": True,
                "validity_reason": f"Valid {br}BR luxury estate comp in {location.title() or 'Phoenix corridor'}.",
                "pool_specs": pool_specs,
            })
            return res

        # Default: evaluate both Winter and Summer
        winter_res = self._evaluate_single_season(
            comp_meta, enriched, all_text, sqft, br, ba, beds, guests, location, rating, reviews, pool_specs, is_winter=True
        )
        summer_res = self._evaluate_single_season(
            comp_meta, enriched, all_text, sqft, br, ba, beds, guests, location, rating, reviews, pool_specs, is_winter=False
        )

        return {
            "is_valid_comp": True,
            "validity_reason": f"Valid {br}BR luxury estate comp in {location.title() or 'Phoenix corridor'}.",
            "desirability_ratio": winter_res["desirability_ratio"],  # Peak Winter baseline
            "winter_ratio": winter_res["desirability_ratio"],
            "summer_ratio": summer_res["desirability_ratio"],
            "pool_specs": pool_specs,
            "composite_score": winter_res["composite_score"],
            "winter_composite_score": winter_res["composite_score"],
            "summer_composite_score": summer_res["composite_score"],
            "category_scores": winter_res["category_scores"],
            "winter_category_scores": winter_res["category_scores"],
            "summer_category_scores": summer_res["category_scores"],
            "rationale": winter_res["rationale"],
            "winter_rationale": winter_res["rationale"],
            "summer_rationale": summer_res["rationale"],
        }

    def evaluate_all_in_registry(self, save: bool = True) -> Dict[str, Any]:
        """Iterate through all comps in config/comps_registry.json and evaluate each."""
        if not self.REGISTRY_PATH.exists():
            raise FileNotFoundError(f"Registry not found: {self.REGISTRY_PATH}")

        registry = json.loads(self.REGISTRY_PATH.read_text(encoding="utf-8"))
        evaluated_count = 0
        valid_count = 0
        disqualified_count = 0

        for tier_key in ("tier_a", "tier_b"):
            for cid, comp in registry.get(tier_key, {}).items():
                ev = self.evaluate_comp(comp)
                comp.update(ev)
                evaluated_count += 1
                if ev["is_valid_comp"]:
                    valid_count += 1
                else:
                    disqualified_count += 1

        if save:
            registry["metadata"]["evaluated_at"] = Path(__file__).name
            registry["metadata"]["valid_comps_count"] = valid_count
            registry["metadata"]["disqualified_comps_count"] = disqualified_count
            self.REGISTRY_PATH.write_text(json.dumps(registry, indent=2, ensure_ascii=False), encoding="utf-8")
            logger.info(f"Evaluated and saved {evaluated_count} comps to {self.REGISTRY_PATH}")

        print("\n" + "=" * 60)
        print("🎯 COMP EVALUATION COMPLETE")
        print("=" * 60)
        print(f"  Total Comps Evaluated:     {evaluated_count}")
        print(f"  ✅ Valid Luxury Comps:     {valid_count}")
        print(f"  ⛔ Disqualified Comps:     {disqualified_count}")
        print("=" * 60)
        return registry


if __name__ == "__main__":
    evaluator = CompEvaluator()
    evaluator.evaluate_all_in_registry(save=True)
