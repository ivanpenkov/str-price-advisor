"""
Property Valuation & Sizing Engine.
Extracts living area (sq ft), lot/yard size (acres), and STR license numbers from listing text,
and applies the Corridor Hedonic Pricing Model to estimate property market asset value.
"""

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("property_valuation")


class PropertyValuator:
    """Extracts house/yard sizes and estimates property asset values."""

    # Baseline anchor: Villa del Sol (920 E Carver Rd, Tempe, AZ)
    OUR_BASELINE = {
        "sqft": 5400,
        "lot_acres": 0.75,
        "property_value": 2000000.0,  # $2.0 Million
        "corridor": "South Tempe",
    }

    # Corridor median price-per-square-foot for luxury estates
    CORRIDOR_PRICE_PER_SQFT = {
        "paradise valley": 800.0,
        "pv": 800.0,
        "scottsdale": 550.0,
        "old town": 550.0,
        "north scottsdale": 575.0,
        "south scottsdale": 525.0,
        "arcadia": 475.0,
        "tempe": 400.0,
        "south tempe": 400.0,
        "chandler": 380.0,
        "ahwatukee": 380.0,
        "gilbert": 350.0,
        "mesa": 340.0,
        "phoenix": 375.0,
        "default": 380.0,
    }

    @classmethod
    def extract_license_number(cls, text: str) -> Optional[str]:
        """Extract STR permit / license number if present in listing text."""
        if not text:
            return None

        patterns = [
            r"(?:license|permit|registration|tpt|str)[#:\s]+([A-Z0-9\-_]{4,25})",
            r"(?:city\s+of\s+scottsdale|tempe|mesa|phoenix)\s+(?:license|permit)[#:\s]+([A-Z0-9\-_]{4,25})",
            r"\b(STR-\d{4,10})\b",
            r"\b(20\d{2}-\d{4,8})\b",
        ]
        for p in patterns:
            m = re.search(p, text, re.IGNORECASE)
            if m:
                val = m.group(1).strip().strip(".-#")
                val_lower = val.lower()
                if (
                    len(val) >= 4
                    and not val_lower.startswith("http")
                    and any(c.isdigit() for c in val)
                    and val_lower not in ("license", "permit", "registration", "number", "required", "pending")
                ):
                    return val
        return None

    @classmethod
    def extract_house_sqft(cls, text: str) -> Tuple[Optional[int], str]:
        """
        Extract interior living square footage from listing text.
        Returns: (sqft, source_str)
        """
        if not text:
            return None, "Corridor Hedonic Est."

        # Targeted patterns prioritizing explicit home/estate size
        primary_patterns = [
            r"(?:home|house|estate|villa|living\s*space|retreat|mansion)\s*(?:is|features|measuring|offers|spans)?\s*(?:approximately|approx|over|about|\+)?\s*(\d[\d,]*)\s*(?:sq\s*ft|sqft|square\s*feet|sf)",
            r"(\d[\d,]*)\s*(?:sq\s*ft|sqft|square\s*feet|sf)\s*(?:home|house|estate|villa|retreat|mansion|living\s*space|single\s*level|two\s*story)",
            r"(\d[\d,]*)\s*(?:sq\s*ft|sqft|square\s*feet|sf)\s*of\s*(?:luxury|living|interior|air\s*conditioned|climate\s*controlled)",
        ]
        for p in primary_patterns:
            for m in re.finditer(p, text, re.IGNORECASE):
                try:
                    val = int(m.group(1).replace(",", ""))
                    # Reasonable range for 5-8BR luxury estates: 2,000 to 20,000 sq ft
                    if 2000 <= val <= 20000:
                        return val, "Listing Disclosed"
                except Exception:
                    pass

        # General sqft match (skipping patio/lot mentions)
        gen_pattern = r"(\d[\d,]*)\s*(?:sq\s*ft|sqft|square\s*feet|sq\s*feet)"
        for m in re.finditer(gen_pattern, text, re.IGNORECASE):
            # Check context to avoid "1,500 sq ft covered patio" or "lot size"
            start = max(0, m.start() - 30)
            end = min(len(text), m.end() + 30)
            ctx = text[start:end].lower()
            if any(w in ctx for w in ["patio", "deck", "covered", "lot", "yard", "pool", "garage", "turf", "court"]):
                continue
            try:
                val = int(m.group(1).replace(",", ""))
                if 2000 <= val <= 20000:
                    return val, "Listing Disclosed"
            except Exception:
                pass

        return None, "Corridor Hedonic Est."

    @classmethod
    def extract_lot_acres(cls, text: str) -> Tuple[Optional[float], str]:
        """
        Extract yard/lot size in acres from listing text.
        Returns: (lot_acres, source_str)
        """
        if not text:
            return None, "Corridor Hedonic Est."

        text_lower = text.lower()

        # Fraction/word mapping
        word_map = {
            "quarter": 0.25,
            "1/4": 0.25,
            "one-quarter": 0.25,
            "one quarter": 0.25,
            "third": 0.33,
            "1/3": 0.33,
            "one-third": 0.33,
            "one third": 0.33,
            "half": 0.50,
            "1/2": 0.50,
            "½": 0.50,
            "one-half": 0.50,
            "one half": 0.50,
            "three-quarter": 0.75,
            "three-quarters": 0.75,
            "three quarter": 0.75,
            "three quarters": 0.75,
            "3/4": 0.75,
            "¾": 0.75,
            "one": 1.0,
            "1": 1.0,
            "two": 2.0,
            "2": 2.0,
            "three": 3.0,
            "3": 3.0,
            "four": 4.0,
            "4": 4.0,
            "five": 5.0,
            "5": 5.0,
        }

        acre_pattern = (
            r"(?:over|about|nearly|almost|\+)?\s*"
            r"(half|quarter|one-quarter|one quarter|third|one-third|one third|"
            r"1/4|1/3|1/2|½|3/4|¾|three-quarter|three-quarters|three quarter|three quarters|"
            r"one|two|three|four|five|\d+(?:\.\d+)?)\s*[- ]*acres?"
        )
        for m in re.finditer(acre_pattern, text_lower):
            raw = m.group(1).strip()
            if raw in word_map:
                return word_map[raw], "Listing Disclosed"
            try:
                val = float(raw)
                if 0.15 <= val <= 20.0:
                    return val, "Listing Disclosed"
            except ValueError:
                pass

        # Lot square feet: e.g. "35,000 sq ft lot"
        m_lot_sq = re.search(r"(\d[\d,]*)\s*(?:sq\s*ft|sqft|square\s*feet)\s*(?:lot|yard|parcel|grounds)", text_lower)
        if m_lot_sq:
            try:
                sq = float(m_lot_sq.group(1).replace(",", ""))
                if sq >= 5000:
                    return round(sq / 43560.0, 2), "Listing Disclosed"
            except Exception:
                pass

        return None, "Corridor Hedonic Est."

    @classmethod
    def impute_specs(
        cls,
        br: int,
        ba: float,
        guests: int,
        location: str,
        all_text: str,
    ) -> Tuple[int, float, float]:
        """
        Corridor Hedonic Pricing Model imputation when dimensions are not disclosed in text:
        Returns: (imputed_sqft, imputed_lot_acres, est_property_value)
        """
        text_lower = all_text.lower() if all_text else ""

        # 1. Living Sq Ft Imputation based on physical capacity
        if br >= 8:
            base_sqft = 6800
        elif br == 7:
            base_sqft = 6000
        elif br == 6:
            base_sqft = 5000 if ba >= 5.0 else 4400
        elif br == 5:
            base_sqft = 4000 if ba >= 4.0 else 3400
        else:
            base_sqft = 3000

        # Guest count density adjustment
        if guests >= 18:
            base_sqft += 400
        elif guests >= 16:
            base_sqft += 200

        # Architectural feature additions
        has_casita = any(w in text_lower for w in ["casita", "guest house", "guesthouse", "guest suite"])
        if has_casita:
            base_sqft += 600
        has_theater = any(w in text_lower for w in ["theatre", "theater", "cinema", "movie room"])
        if has_theater:
            base_sqft += 350
        has_game_room = any(w in text_lower for w in ["game room", "arcade", "billiards", "pool table"])
        if has_game_room:
            base_sqft += 300

        # 2. Lot Acreage Imputation based on outdoor footprint & court infrastructure
        has_tennis = any(w in text_lower for w in ["tennis court", "full tennis"])
        has_multi = any(w in text_lower for w in ["multi-sport", "pickleball and basketball", "sports complex"])
        has_court = any(w in text_lower for w in ["pickleball", "basketball court", "bball court", "sport court"])
        has_large_pool = any(w in text_lower for w in ["resort-style pool", "waterfall", "grotto", "massive pool"])

        if has_tennis or has_multi:
            est_lot = 0.85  # Tennis court footprint requires substantial acreage
        elif (has_court and has_large_pool) or br >= 7:
            est_lot = 0.50  # Half acre compound
        elif has_court or has_large_pool or br >= 6:
            est_lot = 0.40  # Extended luxury estate lot
        elif br >= 5:
            est_lot = 0.30
        else:
            est_lot = 0.25  # Standard single-family residential lot

        # 3. Property Asset Value Calculation via Corridor $/sq ft
        corridor_key = "default"
        loc_lower = (location or "").lower()
        for k in cls.CORRIDOR_PRICE_PER_SQFT:
            if k in loc_lower or k in text_lower[:250]:
                corridor_key = k
                break

        price_per_sqft = cls.CORRIDOR_PRICE_PER_SQFT.get(corridor_key, cls.CORRIDOR_PRICE_PER_SQFT["default"])
        
        # Acreage multiplier: premium lots (>0.5 acre) command higher land equity
        lot_mult = 1.0
        if est_lot >= 1.0:
            lot_mult = 1.25
        elif est_lot >= 0.65:
            lot_mult = 1.15
        elif est_lot >= 0.45:
            lot_mult = 1.08
        elif est_lot < 0.20:
            lot_mult = 0.90

        est_val = round((base_sqft * price_per_sqft * lot_mult) / 10000.0) * 10000.0

        return base_sqft, est_lot, est_val

    @classmethod
    def evaluate_property_specs(
        cls,
        listing_id: str,
        title: str = "",
        description: str = "",
        location: str = "",
        br: int = 6,
        ba: float = 6.0,
        guests: int = 16,
        known_address: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Complete sizing and property valuation pipeline.
        Extracts verified/disclosed specs, applies hedonic model where needed, and returns:
            {
                "sqft": int,
                "sqft_source": str,
                "lot_acres": float,
                "lot_source": str,
                "est_property_value": float,
                "property_value_source": str,
                "str_license": Optional[str],
            }
        """
        # Ground truth for Villa del Sol
        if str(listing_id) == "573857947793833342":
            return {
                "sqft": cls.OUR_BASELINE["sqft"],
                "sqft_source": "Assessor Verified",
                "lot_acres": cls.OUR_BASELINE["lot_acres"],
                "lot_source": "Assessor Verified",
                "est_property_value": cls.OUR_BASELINE["property_value"],
                "property_value_source": "Assessor Verified",
                "str_license": "STR-000055",
                "address": "920 E Carver Rd, Tempe, AZ 85284",
            }

        all_text = f"{title} {description}".strip()
        str_license = cls.extract_license_number(all_text)

        sqft, sqft_source = cls.extract_house_sqft(all_text)
        lot_acres, lot_source = cls.extract_lot_acres(all_text)

        imputed_sqft, imputed_lot, hedonic_val = cls.impute_specs(br, ba, guests, location, all_text)

        final_sqft = sqft if sqft else imputed_sqft
        final_lot = lot_acres if lot_acres else imputed_lot

        # Value calculation using final sqft and lot
        corridor_key = "default"
        loc_lower = (location or "").lower()
        for k in cls.CORRIDOR_PRICE_PER_SQFT:
            if k in loc_lower or k in all_text[:250].lower():
                corridor_key = k
                break

        price_per_sqft = cls.CORRIDOR_PRICE_PER_SQFT.get(corridor_key, cls.CORRIDOR_PRICE_PER_SQFT["default"])
        lot_mult = 1.0
        if final_lot >= 1.0:
            lot_mult = 1.25
        elif final_lot >= 0.65:
            lot_mult = 1.15
        elif final_lot >= 0.45:
            lot_mult = 1.08
        elif final_lot < 0.20:
            lot_mult = 0.90

        est_property_value = round((final_sqft * price_per_sqft * lot_mult) / 10000.0) * 10000.0

        return {
            "sqft": final_sqft,
            "sqft_source": sqft_source,
            "lot_acres": final_lot,
            "lot_source": lot_source,
            "est_property_value": est_property_value,
            "property_value_source": "Corridor Hedonic Est.",
            "str_license": str_license,
        }
