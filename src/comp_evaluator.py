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

    def evaluate_comp(self, comp_meta: Dict[str, Any], enriched_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Evaluate a single competitor listing against Villa del Sol.
        Returns a dict containing is_valid_comp, desirability_ratio, scores, and rationale.
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

        # Extract square footage if mentioned in all_text
        sqft = None
        m_sqft = re.search(r"(\d[\d,]*)\s*(?:sq\s*ft|sqft|square\s*feet|sq\s*feet)", all_text)
        if m_sqft:
            try:
                sqft = int(m_sqft.group(1).replace(",", ""))
            except Exception:
                sqft = None

        br = comp_meta.get("bedrooms") or enriched.get("bedrooms", 5)
        try:
            br = int(br)
        except Exception:
            br = 5

        ba = comp_meta.get("baths") or enriched.get("baths", 4.0)
        try:
            ba = float(ba)
        except Exception:
            ba = 4.0

        beds = comp_meta.get("beds") or enriched.get("beds", br)
        try:
            beds = int(beds)
        except Exception:
            beds = br

        # Determine guest capacity
        guests = comp_meta.get("guests") or comp_meta.get("max_guests") or comp_meta.get("accommodates")
        if not guests:
            for item in enriched.get("overview", []):
                if "guest" in item.lower():
                    digits = "".join(c for c in item if c.isdigit())
                    if digits:
                        guests = int(digits)
                        break
        if not guests:
            guests = max(12, beds * 2) if br >= 5 else (beds * 2)
        else:
            try:
                guests = int(guests)
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
        has_deep_data = (
            enriched.get("amenities_count", 0) > 0
            or comp_meta.get("amenities_count", 0) > 0
            or len(amenities) > 0
            or len(desc) > 100
        )
        if len(amenities) > 0:
            has_pool = any("pool" in a and "table" not in a for a in amenities)
        elif has_deep_data:
            has_negative_pool = any(neg in all_text for neg in ["no pool", "without a pool", "does not have a pool", "doesn't have a pool", "no swimming pool"])
            has_positive_pool = any(w in all_text for w in ["swimming pool", "private outdoor pool", "heated pool", "saltwater pool", "resort pool", "private pool", "lap pool"]) or ("pool" in title.lower() and not has_negative_pool)
            has_pool = has_positive_pool and not has_negative_pool
        else:
            # Fallback for listings awaiting deep amenities enrichment
            has_pool = True

        if not has_pool:
            return {
                "is_valid_comp": False,
                "validity_reason": "Disqualified: Property lacks a private swimming pool.",
                "desirability_ratio": 0.50,
                "category_scores": {"outdoor": 30, "capacity": 60, "interior": 60, "location": 70, "reputation": 70},
                "composite_score": 52.0,
                "rationale": "Disqualified from luxury comp cohort because verified listing details show no private swimming pool.",
            }

        if br < 4 or (br < 5 and guests < 12):
            return {
                "is_valid_comp": False,
                "validity_reason": f"Disqualified: Only {br} bedrooms (capacity {guests} guests), below the 5 BR / 12 guest threshold.",
                "desirability_ratio": 0.55,
                "category_scores": {"outdoor": 65, "capacity": 40, "interior": 65, "location": 75, "reputation": 75},
                "composite_score": 60.0,
                "rationale": f"Disqualified: Under-sized listing ({br} BR / {guests} guests) cannot benchmark 16-guest luxury estates.",
            }

        # Check for far outliers (>30 miles)
        outlier_cities = ["surprise", "buckeye", "casa grande", "maricopa", "goodyear", "sun city"]
        if any(city in location for city in outlier_cities) or any(city in all_text[:200] for city in outlier_cities):
            return {
                "is_valid_comp": False,
                "validity_reason": f"Disqualified: Located in peripheral suburb ({location.title()}), outside competitive drive corridor.",
                "desirability_ratio": 0.60,
                "category_scores": {"outdoor": 70, "capacity": 75, "interior": 70, "location": 40, "reputation": 75},
                "composite_score": 66.0,
                "rationale": f"Disqualified: Located too far from the Scottsdale/Tempe corridor in {location.title()}.",
            }

        # -------------------------------------------------------------
        # 2. CATEGORY SCORING (0 to 100)
        # -------------------------------------------------------------
        # A. Outdoor Yard (30%)
        # Features: pool, spa/hot tub, sports courts (tennis, pickleball, basketball), sauna/wellness, putting green, BBQ, acreage
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

        outdoor_score = 65  # Base for having a pool
        if has_spa:
            outdoor_score += 10
        if has_multiple_courts:
            outdoor_score += 16
        elif has_tennis:
            outdoor_score += 14
        elif has_court:
            outdoor_score += 10
        if has_sauna:
            outdoor_score += 6
        if has_green:
            outdoor_score += 5
        if has_bbq_fire:
            outdoor_score += 5
        if has_grotto:
            outdoor_score += 5
        outdoor_score = min(100, outdoor_score)

        # B. Bedrooms & Bathrooms (25%)
        # Villa del Sol: 6 BR, 6 BA, 16 guests, detached casita, 5,400 sq ft
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
        # Features: pool table / billiards, theater, arcade / game room, chef kitchen, luxury remodel
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
        # Scottsdale / PV (+5-15% peak demand): 95
        # South Tempe (Villa del Sol): 88
        # Chandler / Gilbert: 82
        # Mesa: 78
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
            reputation_score = 75  # New listing

        # -------------------------------------------------------------
        # 3. COMPOSITE SCORE & DESIRABILITY RATIO
        # -------------------------------------------------------------
        composite = (
            self.CATEGORY_WEIGHTS["outdoor"] * outdoor_score
            + self.CATEGORY_WEIGHTS["capacity"] * capacity_score
            + self.CATEGORY_WEIGHTS["interior"] * interior_score
            + self.CATEGORY_WEIGHTS["location"] * location_score
            + self.CATEGORY_WEIGHTS["reputation"] * reputation_score
        )
        composite = round(composite, 1)

        # Sensitivity-scaled expansion (sensitivity = 2.0 centered at 88.0)
        # Unlocks the full 0.65x - 1.35x realistic luxury range, preventing linear compression.
        SENSITIVITY = 2.0
        delta = (composite - self.OUR_BASELINE_SCORE) / self.OUR_BASELINE_SCORE
        raw_ratio = 1.0 + (SENSITIVITY * delta)
        ratio = round(max(0.65, min(1.35, raw_ratio)), 2)

        # -------------------------------------------------------------
        # 4. RATIONALE GENERATION
        # -------------------------------------------------------------
        highlights = []
        shortcomings = []

        if has_tennis:
            highlights.append("private tennis court")
        elif has_court:
            highlights.append("dedicated sports court")
        elif "basketball" in self.our_profile.get("description", "").lower():
            shortcomings.append("lacks basketball court")

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

        if ratio >= 1.05:
            delta = round((ratio - 1.0) * 100)
            summary = f"Premium comp ({delta}% superior desirability). Features " + ", ".join(highlights[:3]) + "."
        elif ratio <= 0.95:
            delta = round((1.0 - ratio) * 100)
            summary = f"Moderate comp ({delta}% lower desirability). "
            if highlights and shortcomings:
                summary += f"Features {highlights[0]}, but noted gaps: " + ", ".join(shortcomings[:2]) + "."
            elif highlights:
                summary += "Features " + ", ".join(highlights[:2]) + "."
            elif shortcomings:
                summary += "Noted gaps: " + ", ".join(shortcomings[:3]) + "."
            else:
                summary += f"{br}BR estate in {location.title()} with standard luxury amenities."
        else:
            summary = f"Direct peer comp (near-equal quality). {br}BR / {ba}BA estate matching Villa del Sol's capacity and luxury tier."

        return {
            "is_valid_comp": True,
            "validity_reason": f"Valid {br}BR luxury estate comp in {location.title() or 'Phoenix corridor'}.",
            "desirability_ratio": ratio,
            "category_scores": {
                "outdoor": outdoor_score,
                "capacity": capacity_score,
                "interior": interior_score,
                "location": location_score,
                "reputation": reputation_score,
            },
            "composite_score": composite,
            "rationale": summary,
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
