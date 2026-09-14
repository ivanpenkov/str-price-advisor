"""
Comp Cohort Curator & Bootstrapper.
Discovers, validates, and maintains the curated registry of 50-100 luxury comps
in the Tempe / Scottsdale / East Valley corridor.
Classifies them into:
- Tier A: Direct comps (16+ guests, 6+ BR, pool, luxury resort yard)
- Tier B: Secondary comps (12-15 guests, 5+ BR, pool, luxury)
"""

import asyncio
import json
from pathlib import Path
from typing import List, Dict, Any, Optional
from playwright.async_api import async_playwright
from src.airbnb_collector import AirbnbCollector


class CompCurator:
    """Bootstraps and updates the verified comp registry."""

    REGISTRY_PATH = Path("config/comps_registry.json")

    SAMPLE_DATES = [
        ("2026-10-15", "2026-10-18"),  # Peak Fall weekend
        ("2026-11-12", "2026-11-15"),  # November weekend
        ("2027-02-18", "2027-02-21"),  # Winter / Spring Training peak
        ("2027-04-15", "2027-04-18"),  # Spring peak
    ]

    LOCATIONS = [
        "Tempe--AZ",
        "Scottsdale--AZ",
        "Chandler--AZ",
        "Gilbert--AZ",
        "Mesa--AZ",
    ]

    def __init__(self, registry_path: Optional[str] = None):
        self.registry_path = Path(registry_path) if registry_path else self.REGISTRY_PATH
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)

    def load_registry(self) -> Dict[str, Any]:
        """Load existing comps from registry if available."""
        if self.registry_path.exists():
            try:
                return json.loads(self.registry_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"tier_a": {}, "tier_b": {}, "metadata": {"last_updated": None, "total_count": 0}}

    def save_registry(self, data: Dict[str, Any]):
        """Write registry to JSON."""
        data.setdefault("metadata", {})
        unique_active = len(set(data.get("tier_a", {}).keys()) | set(data.get("tier_b", {}).keys()))
        data["metadata"]["total_count"] = unique_active
        data["metadata"]["total_comps"] = unique_active
        self.registry_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    async def bootstrap_market(self, limit_per_tier: int = 40) -> Dict[str, Any]:
        """Discover and compile luxury comps across target corridors."""
        registry = self.load_registry()
        collector = AirbnbCollector()

        print("🔍 Bootstrapping Comp Registry across Tempe, Scottsdale, Chandler, Mesa, Gilbert...")
        async with async_playwright() as p:
            await collector.init_browser(p)

            for tier_name, tier_code, min_br in [
                ("Tier A (16+ guests, 6+ BR)", "tier_a", 6),
                ("Tier B (12-15 guests, 5+ BR)", "tier_b", 5),
            ]:
                print(f"\nScanning for {tier_name} across corridors...")
                tier_dict = registry[tier_code]

                for s_in, s_out in self.SAMPLE_DATES:
                    if len(tier_dict) >= limit_per_tier:
                        break

                    print(f"  Sampling market dates: {s_in} -> {s_out}...")
                    comps = await collector.fetch_comps_for_dates(
                        check_in=s_in,
                        check_out=s_out,
                        nights=3,
                        tier=tier_code,
                        locations=self.LOCATIONS,
                        use_cache=True,
                    )

                    for c in comps:
                        cid = str(c["listing_id"])
                        if cid == "573857947793833342":
                            continue

                        # Ensure comp does not exist in any tier or excluded list
                        if (
                            cid in registry.get("tier_a", {})
                            or cid in registry.get("tier_b", {})
                            or cid in registry.get("disqualified", {})
                            or cid in registry.get("excluded_comps", {})
                        ):
                            continue

                        tier_dict[cid] = {
                            "listing_id": cid,
                            "name": c["title"],
                            "location": c["location"],
                            "bedrooms": c["bedrooms"],
                            "beds": c["beds"],
                            "baths": c["baths"],
                            "rating": c["rating"],
                            "reviews": c["reviews"],
                            "url": f"https://www.airbnb.com/rooms/{cid}",
                            "discovered_at_sample": f"{s_in} to {s_out}",
                            "photo_url": c.get("photo_url"),
                        }

                    print(f"    Total {tier_code} unique comps registered so far: {len(tier_dict)}")

            await collector.close_browser()

        from datetime import datetime
        registry["metadata"]["last_updated"] = datetime.now().isoformat()
        self.save_registry(registry)

        print("\n" + "=" * 60)
        print(f"✅ Comp Registry Bootstrap Complete!")
        print(f"  - Tier A Comps (Direct 16+ guests): {len(registry['tier_a'])}")
        print(f"  - Tier B Comps (Secondary 12-15 guests): {len(registry['tier_b'])}")
        print(f"  - Total Curated Listings: {registry['metadata']['total_count']}")
        print(f"  - Saved to: {self.registry_path}")
        print("=" * 60)
        return registry

    async def discover_comps(
        self,
        corridors: Optional[List[str]] = None,
        limit: int = 20,
        tier: str = "both",
        min_rating: float = 4.85,
        output_json: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Scan nearby market corridors (Tempe, Chandler, Ahwatukee, Scottsdale) for candidate luxury properties.
        Filters out properties already in the registry or excluded, evaluates similarity to Villa del Sol,
        and ranks top candidates.
        """
        registry = self.load_registry()
        existing_ids = (
            set(registry.get("tier_a", {}).keys())
            | set(registry.get("tier_b", {}).keys())
            | set(registry.get("disqualified", {}).keys())
            | set(registry.get("excluded_comps", {}).keys())
            | {"573857947793833342"}
        )

        corridor_map = {
            "tempe": "Tempe--AZ",
            "scottsdale": "Scottsdale--AZ",
            "chandler": "Chandler--AZ",
            "ahwatukee": "Phoenix--AZ",
            "gilbert": "Gilbert--AZ",
            "mesa": "Mesa--AZ",
        }
        target_locations = []
        if corridors:
            for c in corridors:
                c_clean = c.strip().lower()
                if c_clean in corridor_map:
                    target_locations.append(corridor_map[c_clean])
                else:
                    target_locations.append(f"{c_clean.title()}--AZ")
        if not target_locations:
            target_locations = ["Tempe--AZ", "Chandler--AZ", "Scottsdale--AZ", "Phoenix--AZ"]

        tier_list = ["tier_a", "tier_b"] if tier == "both" else [tier]

        print(f"\n🔍 [CompCurator] Initiating nearby luxury comp discovery...")
        print(f"  Target corridors: {target_locations}")
        print(f"  Tiers: {tier_list} | Min rating: {min_rating}★ | Limit: {limit}")

        collector = AirbnbCollector()
        discovered: Dict[str, Dict[str, Any]] = {}

        async with async_playwright() as p:
            await collector.init_browser(p)

            for t_code in tier_list:
                for s_in, s_out in self.SAMPLE_DATES:
                    if len(discovered) >= limit * 2:
                        break
                    print(f"  Scanning {t_code} for {s_in} -> {s_out}...")
                    comps = await collector.fetch_comps_for_dates(
                        check_in=s_in,
                        check_out=s_out,
                        nights=3,
                        tier=t_code,
                        locations=target_locations,
                        use_cache=True,
                    )
                    for c in comps:
                        cid = str(c.get("listing_id") or "")
                        if not cid or cid in existing_ids or cid in discovered:
                            continue

                        rating = float(c.get("rating") or 0.0)
                        reviews = int(c.get("reviews") or 0)
                        br = int(c.get("bedrooms") or 0)
                        # Filter criteria: min rating (or new), min 5 BR
                        if rating > 0 and rating < min_rating and reviews >= 5:
                            continue
                        if br < 5:
                            continue

                        # Compute preliminary similarity score against Villa del Sol baseline
                        sim_score = 70.0
                        if br >= 6: sim_score += 10.0
                        if float(c.get("baths") or 0.0) >= 5.0: sim_score += 5.0
                        if rating >= 4.90: sim_score += 10.0
                        loc = (c.get("location") or "").lower()
                        if "tempe" in loc or "ahwatukee" in loc: sim_score += 5.0

                        candidate = {
                            "listing_id": cid,
                            "name": c.get("title") or c.get("name"),
                            "location": c.get("location"),
                            "bedrooms": br,
                            "beds": c.get("beds"),
                            "baths": c.get("baths"),
                            "rating": rating,
                            "reviews": reviews,
                            "url": f"https://www.airbnb.com/rooms/{cid}",
                            "photo_url": c.get("photo_url"),
                            "similarity_score": min(100.0, sim_score),
                            "discovered_tier": t_code,
                        }
                        discovered[cid] = candidate

            await collector.close_browser()

        candidates = sorted(discovered.values(), key=lambda x: (x["similarity_score"], x["rating"]), reverse=True)[:limit]

        if output_json:
            print(json.dumps(candidates, indent=2))
            return candidates

        print("\n" + "=" * 70)
        print(f"🏆 DISCOVERED {len(candidates)} NEARBY LUXURY CANDIDATES")
        print("=" * 70)
        for idx, cand in enumerate(candidates, 1):
            r_str = f"⭐ {cand['rating']:.2f} ({cand['reviews']})" if cand['rating'] > 0 else "⭐ New"
            print(f" {idx:2d}. [{cand['discovered_tier'].upper()}] {cand['name']}")
            print(f"     ID: {cand['listing_id']} | {cand['location']} | {cand['bedrooms']} BR | {cand['baths']} BA | {r_str} | Similarity: {cand['similarity_score']:.0f}%")
            print(f"     URL: {cand['url']}")
        print("=" * 70)
        print("💡 To add any candidate to the registry, run:")
        print("   .venv/bin/python -m src.cli add-comp <listing_id> --scrape-prices\n")

        return candidates

