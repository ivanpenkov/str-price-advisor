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
        data["metadata"]["total_count"] = len(data["tier_a"]) + len(data["tier_b"])
        self.registry_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

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
                        cid = c["listing_id"]
                        if cid == "573857947793833342":
                            continue

                        if cid not in tier_dict:
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

