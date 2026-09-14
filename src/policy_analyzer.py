"""
Policy Analyzer for Competitor Listings.
Extracts, normalizes, and aggregates STR policies and house rules across 8 dimensions:
1. Check-In Window
2. Check-Out Deadline
3. Security / Damage Deposit
4. Noise & Quiet Hours Enforcement
5. Pool Heating Fee Policy
6. Minimum Guest Age
7. Pet Policy
8. Events & Parties Policy

Supports both structured house rules (scraped from Airbnb) and text-extracted rules
(from cached descriptions, titles, and amenities), retaining exact evidence snippets
for debugging and tooltips.
"""

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("policy_analyzer")


class PolicyAnalyzer:
    """Extracts, categorizes, and aggregates competitor policies and house rules."""

    REGISTRY_PATH = Path("config/comps_registry.json")
    ENRICHED_DIR = Path("data/enriched_comps")

    DIMENSIONS = [
        {
            "id": "check_in",
            "title": "Check-In Window",
            "icon": "🕒",
            "desc": "Standard guest arrival time window",
            "default_order": ["3:00 PM", "4:00 PM", "5:00 PM", "Flexible / Other", "Undisclosed"],
        },
        {
            "id": "check_out",
            "title": "Check-Out Deadline",
            "icon": "🕙",
            "desc": "Required departure time on checkout day",
            "default_order": ["10:00 AM", "11:00 AM", "12:00 PM", "Flexible / Other", "Undisclosed"],
        },
        {
            "id": "deposit",
            "title": "Security / Damage Deposit",
            "icon": "🛡️",
            "desc": "Separate security, damage, or incidental hold required",
            "default_order": [
                "$1,000+ Deposit Required",
                "$500–$999 Deposit Required",
                "Deposit Required (Unspecified)",
                "None Mentioned / Platform Only",
            ],
        },
        {
            "id": "noise",
            "title": "Noise & Quiet Hours Enforcement",
            "icon": "🔊",
            "desc": "Decibel sensor devices, quiet hours, or local ordinance enforcement",
            "default_order": [
                "Active Decibel Sensor (Minut / NoiseAware)",
                "Strict Quiet Hours Declared",
                "City Noise Ordinance Warning",
                "Undisclosed / Standard",
            ],
        },
        {
            "id": "pool_heating",
            "title": "Pool Heating Fee Policy",
            "icon": "🏊‍♂️",
            "desc": "Whether pool heating is complimentary, paid extra daily, or unheated",
            "default_order": [
                "Free / Included in Rate",
                "Paid Extra Daily Fee",
                "Unheated Pool",
                "No Pool",
            ],
        },
        {
            "id": "min_age",
            "title": "Minimum Guest Age",
            "icon": "🪪",
            "desc": "Minimum age requirement for primary booking renter",
            "default_order": ["25+ Years", "21+ Years", "Other Age", "Undisclosed"],
        },
        {
            "id": "pets",
            "title": "Pet Policy",
            "icon": "🐾",
            "desc": "Whether pets/dogs are permitted with fee or strictly prohibited",
            "default_order": ["Pets Allowed (w/ Fee)", "Strict No Pets", "Undisclosed"],
        },
        {
            "id": "events",
            "title": "Events & Parties Policy",
            "icon": "🎉",
            "desc": "House rules regarding parties, events, and large gatherings",
            "default_order": [
                "Strictly Prohibited",
                "Permitted w/ Approval or Fee",
                "Undisclosed",
            ],
        },
    ]

    @classmethod
    def _extract_snippet(cls, text: str, start_idx: int, end_idx: int, padding: int = 40) -> str:
        """Extract a clean, readable context snippet around a regex match."""
        s = max(0, start_idx - padding)
        e = min(len(text), end_idx + padding)
        snippet = text[s:e].replace("\n", " ").strip()
        snippet = re.sub(r"\s+", " ", snippet)
        return snippet

    @classmethod
    def extract_check_in(cls, text: str, structured_rules: Optional[Dict[str, Any]] = None) -> Tuple[str, str]:
        """Extract check-in policy bucket and evidence snippet."""
        if structured_rules and structured_rules.get("check_in_time"):
            val = str(structured_rules["check_in_time"]).strip()
            raw_upper = val.upper()
            if "3" in raw_upper and ("PM" in raw_upper or ":00" in raw_upper or raw_upper.startswith("3")):
                return "3:00 PM", f"House Rules: Check-in {val}"
            if "4" in raw_upper and ("PM" in raw_upper or ":00" in raw_upper or raw_upper.startswith("4")):
                return "4:00 PM", f"House Rules: Check-in {val}"
            if "5" in raw_upper and ("PM" in raw_upper or ":00" in raw_upper or raw_upper.startswith("5")):
                return "5:00 PM", f"House Rules: Check-in {val}"
            return "Flexible / Other", f"House Rules: Check-in {val}"

        if not text:
            return "Undisclosed", "Not specified in listing description"

        m = re.search(
            r"(?:check[\s-]*in(?:(?:\s*time)?(?:\s*(?:is|after|from|at|:)){1,2})?\s*([0-9]{1,2}(?::[0-9]{2})?\s*(?:am|pm)?))",
            text,
            re.IGNORECASE,
        )
        if m:
            raw = m.group(1).upper().strip()
            snippet = cls._extract_snippet(text, m.start(), m.end())
            if "3" in raw and ("PM" in raw or ":00" in raw or raw == "3"):
                return "3:00 PM", snippet
            if "4" in raw and ("PM" in raw or ":00" in raw or raw == "4"):
                return "4:00 PM", snippet
            if "5" in raw and ("PM" in raw or ":00" in raw or raw == "5"):
                return "5:00 PM", snippet
            return "Flexible / Other", snippet

        return "Undisclosed", "Not specified in listing description"

    @classmethod
    def extract_check_out(cls, text: str, structured_rules: Optional[Dict[str, Any]] = None) -> Tuple[str, str]:
        """Extract check-out policy bucket and evidence snippet."""
        if structured_rules and structured_rules.get("check_out_time"):
            val = str(structured_rules["check_out_time"]).strip()
            raw_upper = val.upper()
            if "10" in raw_upper:
                return "10:00 AM", f"House Rules: Check-out {val}"
            if "11" in raw_upper:
                return "11:00 AM", f"House Rules: Check-out {val}"
            if "12" in raw_upper:
                return "12:00 PM", f"House Rules: Check-out {val}"
            return "Flexible / Other", f"House Rules: Check-out {val}"

        if not text:
            return "Undisclosed", "Not specified in listing description"

        m = re.search(
            r"(?:check[\s-]*out(?:(?:\s*time)?(?:\s*(?:is|before|by|until|at|:)){1,2})?\s*([0-9]{1,2}(?::[0-9]{2})?\s*(?:am|pm)?))",
            text,
            re.IGNORECASE,
        )
        if m:
            raw = m.group(1).upper().strip()
            snippet = cls._extract_snippet(text, m.start(), m.end())
            if "10" in raw:
                return "10:00 AM", snippet
            if "11" in raw:
                return "11:00 AM", snippet
            if "12" in raw:
                return "12:00 PM", snippet
            return "Flexible / Other", snippet

        return "Undisclosed", "Not specified in listing description"

    @classmethod
    def extract_deposit(cls, text: str, structured_rules: Optional[Dict[str, Any]] = None) -> Tuple[str, str]:
        """Extract security / damage deposit bucket and evidence snippet."""
        if structured_rules and structured_rules.get("security_deposit"):
            raw_dep = str(structured_rules["security_deposit"])
            m_amt = re.search(r"(\d[\d,]*)", raw_dep)
            if m_amt:
                amt = int(m_amt.group(1).replace(",", ""))
                if amt >= 1000:
                    return "$1,000+ Deposit Required", f"House Rules Deposit: ${amt:,}"
                if amt >= 500:
                    return "$500–$999 Deposit Required", f"House Rules Deposit: ${amt:,}"
                return "Deposit Required (Unspecified)", f"House Rules Deposit: ${amt:,}"
            return "Deposit Required (Unspecified)", f"House Rules: {raw_dep}"

        if not text:
            return "None Mentioned / Platform Only", "No separate deposit mentioned (covered by platform / AirCover)"

        m = re.search(
            r"(\$\s*(\d[\d,]*)\s*(?:security|damage|incidental|hold|refundable)?\s*deposit|(?:security|damage|incidental|hold|refundable)?\s*deposit\s*(?:of\s*)?\$\s*(\d[\d,]*)|(?:hold\s*(?:a\s*)?deposit|deposit\s*required|refundable\s*(?:security\s*)?deposit))",
            text,
            re.IGNORECASE,
        )
        if m:
            amt_str = m.group(2) or m.group(3)
            snippet = cls._extract_snippet(text, m.start(), m.end())
            if amt_str:
                amt = int(amt_str.replace(",", ""))
                if amt >= 1000:
                    return "$1,000+ Deposit Required", snippet
                if amt >= 500:
                    return "$500–$999 Deposit Required", snippet
                return "Deposit Required (Unspecified)", snippet
            return "Deposit Required (Unspecified)", snippet

        return "None Mentioned / Platform Only", "No separate deposit mentioned (covered by platform / AirCover)"

    @classmethod
    def extract_noise(cls, text: str, structured_rules: Optional[Dict[str, Any]] = None) -> Tuple[str, str]:
        """Extract noise monitoring & quiet hours enforcement bucket and snippet."""
        m_dev = re.search(
            r"\b(minut|noiseaware|decibel\s*sensor|decibel\s*monitor|sound\s*meter|noise\s*monitor(?:ing)?(?:\s*device)?)\b",
            text or "",
            re.IGNORECASE,
        )
        if m_dev:
            snippet = cls._extract_snippet(text, m_dev.start(), m_dev.end())
            return "Active Decibel Sensor (Minut / NoiseAware)", snippet

        if structured_rules:
            if structured_rules.get("noise_monitoring"):
                return "Active Decibel Sensor (Minut / NoiseAware)", "House Rules: Noise monitoring active"
            if structured_rules.get("quiet_hours"):
                return "Strict Quiet Hours Declared", f"House Rules: Quiet hours {structured_rules['quiet_hours']}"

        m_quiet = re.search(
            r"(quiet\s*hours?\s*(?:are|from|between|:)?\s*([0-9]{1,2}(?::[0-9]{2})?\s*(?:am|pm)?\s*(?:to|-)\s*[0-9]{1,2}(?::[0-9]{2})?\s*(?:am|pm)?)|quiet\s*hours?\b)",
            text or "",
            re.IGNORECASE,
        )
        if m_quiet:
            snippet = cls._extract_snippet(text, m_quiet.start(), m_quiet.end())
            return "Strict Quiet Hours Declared", snippet

        m_ord = re.search(
            r"(city\s*of\s*(?:scottsdale|tempe|phoenix|mesa|paradise\s*valley|chandler|gilbert)\s*(?:noise|ordinance)|noise\s*ordinance|strict\s*noise\s*policy)",
            text or "",
            re.IGNORECASE,
        )
        if m_ord:
            snippet = cls._extract_snippet(text, m_ord.start(), m_ord.end())
            return "City Noise Ordinance Warning", snippet

        return "Undisclosed / Standard", "No special decibel sensors or strict quiet hours declared"

    @classmethod
    def extract_pool_heating(
        cls,
        text: str,
        pool_specs: Optional[Dict[str, Any]] = None,
        structured_rules: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, str]:
        """Extract pool heating fee policy bucket and evidence snippet."""
        specs = pool_specs or {}
        ht = specs.get("heating", "")
        has_pool = specs.get("has_pool", True)

        if not has_pool or ht in ("no_pool", "none"):
            return "No Pool", "Listing does not feature a private swimming pool"

        if ht in ("free", "free_heated"):
            return "Free / Included in Rate", specs.get("heating_source", "Free heated pool included")

        m_free = re.search(
            r"\b("
            r"(?:free|complimentary)[\s-]+pool[\s-]+heat(?:ing)?\b"
            r"|(?:free|complimentary)[\s-]+heated[\s-]+pool\b"
            r"|free-heated\s+pool\b"
            r"|pool\s*heat(?:ing)?\s*(?:is\s*)?(?:always\s*)?included\b"
            r"|heated(?:\s*,\s*sparkling)?\s*pool\s*(?:\(included\)|\s*included)\b"
            r"|no\s*(?:extra\s*|additional\s*)?(?:fee|charge|cost)\s*(?:for|to\s+heat)\s*(?:the\s+)?pool\b"
            r"|no\s*pool\s*heat(?:ing)?\s*(?:fee|charge|cost)\b"
            r"|pool\s*(?:is\s*)?heated\s*at\s*no\s*(?:extra|additional)\s*(?:charge|fee|cost)\b"
            r"|heated\s*pool\s*,\s*no\s*fees?\b"
            r"|pool\s*heat\s*is\s*free\b"
            r")",
            text or "",
            re.IGNORECASE,
        )
        if m_free:
            snippet = cls._extract_snippet(text, m_free.start(), m_free.end())
            return "Free / Included in Rate", snippet

        if ht == "unheated":
            return "Unheated Pool", "Pool is unheated"

        m_paid = re.search(
            r"(pool\s*(?:can\s*be)?\s*heated\s*for\s*(?:an?\s*)?(?:additional|extra)?\s*fee|pool\s*heat\s*(?:fee|\$\d+)|optional\s*pool\s*heat|heat\s*the\s*pool\s*(?:is|for)?\s*\$\d+)",
            text or "",
            re.IGNORECASE,
        )
        if m_paid:
            snippet = cls._extract_snippet(text, m_paid.start(), m_paid.end())
            return "Paid Extra Daily Fee", snippet

        if ht in ["fee", "fee_heated", "standard_heated"]:
            return "Paid Extra Daily Fee", specs.get("heating_source", "Pool heating available for an additional fee")

        return "Paid Extra Daily Fee", "Pool heating available as optional add-on fee"

    @classmethod
    def extract_min_age(cls, text: str, structured_rules: Optional[Dict[str, Any]] = None) -> Tuple[str, str]:
        """Extract minimum renter age bucket and evidence snippet."""
        if structured_rules and structured_rules.get("min_age"):
            val = str(structured_rules["min_age"])
            if val == "25":
                return "25+ Years", f"House Rules: Minimum age {val}"
            if val == "21":
                return "21+ Years", f"House Rules: Minimum age {val}"
            return f"{val}+ Years", f"House Rules: Minimum age {val}"

        if not text:
            return "Undisclosed", "Not specified in listing description"

        m = re.search(
            r"(?:age|renter|guests?|primary\s*renter)\s*(?:minimum|requirement|must\s*be)?\s*(?:of|at\s*least)?\s*([23][0-9])\b|(?:minimum\s*age\s*(?:of|is|:)?\s*([23][0-9]))",
            text,
            re.IGNORECASE,
        )
        if m:
            val = m.group(1) or m.group(2)
            snippet = cls._extract_snippet(text, m.start(), m.end())
            if val == "25":
                return "25+ Years", snippet
            if val == "21":
                return "21+ Years", snippet
            return f"{val}+ Years", snippet

        return "Undisclosed", "Not specified in listing description"

    @classmethod
    def extract_pets(cls, text: str, structured_rules: Optional[Dict[str, Any]] = None) -> Tuple[str, str]:
        """Extract pet policy bucket and evidence snippet."""
        if structured_rules and "pets_allowed" in structured_rules:
            if structured_rules["pets_allowed"] is False:
                return "Strict No Pets", "House Rules: No pets allowed"
            if structured_rules["pets_allowed"] is True:
                return "Pets Allowed (w/ Fee)", "House Rules: Pets allowed"

        if not text:
            return "Undisclosed", "Not specified in listing description"

        m_no = re.search(
            r"\b(no\s*pets(?:\s*allowed)?|pets?\s*(?:are\s*)?not\s*allowed|strict\s*no\s*pets?|no\s*animals)\b",
            text,
            re.IGNORECASE,
        )
        m_yes = re.search(
            r"(?<!no\s)(?<!not\s)\b(pets?\s*(?:are\s*)?allowed|pet\s*friendly|pets?\s*welcome|pet\s*fee|dogs?\s*(?:are\s*)?welcome)\b",
            text,
            re.IGNORECASE,
        )

        if m_no and not m_yes:
            snippet = cls._extract_snippet(text, m_no.start(), m_no.end())
            return "Strict No Pets", snippet
        if m_yes:
            snippet = cls._extract_snippet(text, m_yes.start(), m_yes.end())
            return "Pets Allowed (w/ Fee)", snippet
        if m_no:
            snippet = cls._extract_snippet(text, m_no.start(), m_no.end())
            return "Strict No Pets", snippet

        return "Undisclosed", "Not specified in listing description"

    @classmethod
    def extract_events(cls, text: str, structured_rules: Optional[Dict[str, Any]] = None) -> Tuple[str, str]:
        """Extract events and parties policy bucket and evidence snippet."""
        if structured_rules and "events_allowed" in structured_rules:
            if structured_rules["events_allowed"] is False:
                return "Strictly Prohibited", "House Rules: No parties or events allowed"
            if structured_rules["events_allowed"] is True:
                return "Permitted w/ Approval or Fee", "House Rules: Events allowed with host approval"

        if not text:
            return "Undisclosed", "Not specified in listing description"

        m_no = re.search(
            r"\b(no\s*(?:parties|events|gatherings|bachelor|bachelorette)|parties\s*(?:are\s*)?not\s*allowed|strictly\s*no\s*(?:parties|events)|zero\s*tolerance\s*for\s*parties)\b",
            text,
            re.IGNORECASE,
        )
        m_yes = re.search(
            r"(?<!no\s)(?<!not\s)\b(events?\s*(?:are\s*)?allowed|gatherings?\s*permitted|event\s*fee|small\s*events?\s*permitted)\b",
            text,
            re.IGNORECASE,
        )

        if m_no:
            snippet = cls._extract_snippet(text, m_no.start(), m_no.end())
            return "Strictly Prohibited", snippet
        if m_yes:
            snippet = cls._extract_snippet(text, m_yes.start(), m_yes.end())
            return "Permitted w/ Approval or Fee", snippet

        return "Undisclosed", "Not specified in listing description"

    @classmethod
    def extract_all_for_listing(
        cls,
        listing_id: str,
        comp_registry_entry: Optional[Dict[str, Any]] = None,
        profile_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Extract all 8 policy dimensions for a single listing."""
        comp_entry = comp_registry_entry or {}
        prof = profile_data or {}
        desc = prof.get("description", "")
        title = (prof.get("title") or comp_entry.get("name") or "").strip()
        url = prof.get("url") or comp_entry.get("url") or f"https://www.airbnb.com/rooms/{listing_id}"
        pool_specs = comp_entry.get("pool_specs", {})
        structured_rules = prof.get("house_rules", {})

        ci_val, ci_snip = cls.extract_check_in(desc, structured_rules)
        co_val, co_snip = cls.extract_check_out(desc, structured_rules)
        dep_val, dep_snip = cls.extract_deposit(desc, structured_rules)
        noise_val, noise_snip = cls.extract_noise(desc, structured_rules)
        pool_text = f"{title or ''} {desc or ''}".strip()
        pool_val, pool_snip = cls.extract_pool_heating(pool_text, pool_specs, structured_rules)
        age_val, age_snip = cls.extract_min_age(desc, structured_rules)
        pet_val, pet_snip = cls.extract_pets(desc, structured_rules)
        ev_val, ev_snip = cls.extract_events(desc, structured_rules)

        # Tier calculation
        tier = comp_entry.get("tier", "tier_a" if "tier_a" in str(comp_entry.get("category", "")) else "tier_b")
        loc = comp_entry.get("location") or (prof.get("address") or {}).get("addressLocality", "")

        return {
            "listing_id": str(listing_id),
            "title": title,
            "url": url,
            "tier": tier,
            "location": loc,
            "is_valid": bool(comp_entry.get("is_valid_comp", True)),
            "policies": {
                "check_in": {"bucket": ci_val, "snippet": ci_snip},
                "check_out": {"bucket": co_val, "snippet": co_snip},
                "deposit": {"bucket": dep_val, "snippet": dep_snip},
                "noise": {"bucket": noise_val, "snippet": noise_snip},
                "pool_heating": {"bucket": pool_val, "snippet": pool_snip},
                "min_age": {"bucket": age_val, "snippet": age_snip},
                "pets": {"bucket": pet_val, "snippet": pet_snip},
                "events": {"bucket": ev_val, "snippet": ev_snip},
            },
        }

    @classmethod
    def load_all_registry_comp_policies(
        cls,
        registry_path: Optional[Path] = None,
        enriched_dir: Optional[Path] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """Load and extract policies for all comps registered in config/comps_registry.json."""
        reg_file = registry_path or cls.REGISTRY_PATH
        cache_dir = enriched_dir or cls.ENRICHED_DIR

        if not reg_file.exists():
            logger.warning(f"Registry file not found at {reg_file}")
            return {}

        with open(reg_file, "r", encoding="utf-8") as f:
            registry = json.load(f)

        comps_meta = {}
        for tier_key in ("tier_a", "tier_b"):
            for lid, comp in registry.get(tier_key, {}).items():
                c = dict(comp)
                c["tier"] = tier_key
                comps_meta[str(lid)] = c

        results = {}
        for lid, comp in comps_meta.items():
            profile_path = cache_dir / f"{lid}.json"
            prof = {}
            if profile_path.exists():
                try:
                    with open(profile_path, "r", encoding="utf-8") as pf:
                        prof = json.load(pf)
                except Exception as e:
                    logger.warning(f"Failed to read profile for {lid}: {e}")

            results[lid] = cls.extract_all_for_listing(lid, comp, prof)

        return results

    @classmethod
    def compute_distributions(
        cls,
        comp_policies: Dict[str, Dict[str, Any]],
        filter_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Compute percentage distributions and select debug sample listings for each dimension.
        If filter_ids is provided, only aggregates across the specified listings.
        """
        if filter_ids is not None:
            active_listings = [c for lid, c in comp_policies.items() if lid in filter_ids]
        else:
            active_listings = list(comp_policies.values())

        total = len(active_listings)
        distributions = {}

        for dim in cls.DIMENSIONS:
            dim_id = dim["id"]
            default_order = dim.get("default_order", [])

            bucket_counts: Dict[str, int] = {}
            bucket_samples: Dict[str, Dict[str, Any]] = {}

            for listing in active_listings:
                policy = listing["policies"].get(dim_id, {})
                bucket = policy.get("bucket", "Undisclosed")
                bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1

                # Keep first listing encountered as sample with its snippet
                if bucket not in bucket_samples:
                    bucket_samples[bucket] = {
                        "listing_id": listing["listing_id"],
                        "title": listing["title"],
                        "url": listing["url"],
                        "snippet": policy.get("snippet", ""),
                    }

            # Build ordered list of rows
            rows = []
            seen_buckets = set()

            # First add buckets defined in default order
            for b in default_order:
                if b in bucket_counts:
                    cnt = bucket_counts[b]
                    pct = round((cnt / total) * 100, 1) if total > 0 else 0.0
                    rows.append({
                        "bucket": b,
                        "count": cnt,
                        "percent": pct,
                        "sample": bucket_samples.get(b, {}),
                    })
                    seen_buckets.add(b)

            # Add any other buckets discovered
            for b, cnt in sorted(bucket_counts.items(), key=lambda x: x[1], reverse=True):
                if b not in seen_buckets:
                    pct = round((cnt / total) * 100, 1) if total > 0 else 0.0
                    rows.append({
                        "bucket": b,
                        "count": cnt,
                        "percent": pct,
                        "sample": bucket_samples.get(b, {}),
                    })

            distributions[dim_id] = {
                "id": dim_id,
                "title": dim["title"],
                "icon": dim["icon"],
                "desc": dim["desc"],
                "total_cohort": total,
                "rows": rows,
            }

        return distributions
