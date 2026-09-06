"""
Unit tests for duplicate competitor listing prevention, catalog integrity, and cross-tier uniqueness.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from src.comp_curator import CompCurator
from src.comp_manager import CompManager
from src.html_generator import HTMLDashboardGenerator


class TestCompDuplicatesAndIntegrity(unittest.TestCase):
    """Test suite ensuring zero duplicate competitor listings across all subsystems."""

    def test_comps_registry_has_no_duplicates(self):
        """Verify that the production comps_registry.json contains 0 overlapping IDs."""
        reg_path = Path("config/comps_registry.json")
        self.assertTrue(reg_path.exists(), "config/comps_registry.json must exist")

        with open(reg_path, "r", encoding="utf-8") as f:
            registry = json.load(f)

        tier_a_ids = set(registry.get("tier_a", {}).keys())
        tier_b_ids = set(registry.get("tier_b", {}).keys())
        disq_ids = set(registry.get("disqualified", {}).keys())
        excl_ids = set(registry.get("excluded_comps", {}).keys())

        # Assert no cross-tier duplicates between active tiers
        overlap_ab = tier_a_ids & tier_b_ids
        self.assertEqual(
            overlap_ab,
            set(),
            f"Found {len(overlap_ab)} duplicate comps in both tier_a and tier_b: {overlap_ab}",
        )

        # Assert no active comp is also in disqualified or excluded
        overlap_disq = (tier_a_ids | tier_b_ids) & disq_ids
        self.assertEqual(
            overlap_disq,
            set(),
            f"Active comps found in disqualified: {overlap_disq}",
        )

        overlap_excl = (tier_a_ids | tier_b_ids) & excl_ids
        self.assertEqual(
            overlap_excl,
            set(),
            f"Active comps found in excluded_comps: {overlap_excl}",
        )

        # Assert metadata count matches active unique listings
        total_unique = len(tier_a_ids | tier_b_ids)
        self.assertEqual(
            registry.get("metadata", {}).get("total_comps"),
            total_unique,
            f"metadata.total_comps ({registry.get('metadata', {}).get('total_comps')}) does not match unique active count ({total_unique})",
        )
        self.assertEqual(total_unique, 103, "Total unique comps should be 103 after cleaning the 13 duplicates")

    def test_pricing_data_intervals_have_no_duplicates(self):
        """Verify that all pricing data files have 0 duplicate listings in their interval comps_list."""
        pricing_files = list(Path("data").glob("pricing_data_*.json"))
        self.assertTrue(len(pricing_files) > 0, "Expected at least one pricing data file in data/")

        for p_file in pricing_files:
            with open(p_file, "r", encoding="utf-8") as f:
                pdata = json.load(f)

            for group in ("urgent_intervals", "moderate_intervals", "informational_intervals"):
                for interval in pdata.get(group, []):
                    dates = interval.get("dates", "Unknown")
                    comps_list = interval.get("comps_list", [])
                    cids = [str(c.get("listing_id")) for c in comps_list if c.get("listing_id")]
                    seen = set()
                    dupes = []
                    for cid in cids:
                        if cid in seen:
                            dupes.append(cid)
                        else:
                            seen.add(cid)

                    self.assertEqual(
                        dupes,
                        [],
                        f"Found duplicate comps in {p_file.name} interval '{dates}': {dupes}",
                    )

    def test_comp_curator_prevents_duplicate_insertion(self):
        """Verify CompCurator.bootstrap_market does not add the same comp to multiple tiers."""
        with tempfile.TemporaryDirectory() as tmpdir:
            reg_path = Path(tmpdir) / "comps_registry.json"
            curator = CompCurator(registry_path=str(reg_path))

            mock_listing = {
                "listing_id": "999888777",
                "title": "Desert Dream Villa",
                "location": "Scottsdale",
                "bedrooms": 6,
                "beds": 8,
                "baths": 5.0,
                "rating": 4.95,
                "reviews": 25,
            }

            async def fake_fetch(*args, **kwargs):
                return [dict(mock_listing)]

            with patch("src.comp_curator.async_playwright") as mock_playwright:
                mock_p = AsyncMock()
                mock_playwright.return_value.__aenter__.return_value = mock_p

                with patch("src.comp_curator.AirbnbCollector") as mock_col_cls:
                    mock_col = AsyncMock()
                    mock_col_cls.return_value = mock_col
                    mock_col.fetch_comps_for_dates.side_effect = fake_fetch

                    import asyncio
                    res = asyncio.run(curator.bootstrap_market(limit_per_tier=5))

                    self.assertIn("999888777", res["tier_a"])
                    self.assertNotIn(
                        "999888777",
                        res["tier_b"],
                        "Listing added to tier_a must NOT be added as duplicate to tier_b",
                    )
                    self.assertEqual(res["metadata"]["total_count"], 1)

    def test_comp_manager_remove_cleanses_all_tiers(self):
        """Verify CompManager.remove_comp removes a listing from ALL tiers even if duplicates exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            reg_path = Path(tmpdir) / "comps_registry.json"
            specs_path = Path(tmpdir) / "listing_specs.json"

            corrupt_reg = {
                "metadata": {"total_comps": 2},
                "tier_a": {
                    "888111": {"listing_id": "888111", "name": "Dupe Comp A", "bedrooms": 6}
                },
                "tier_b": {
                    "888111": {"listing_id": "888111", "name": "Dupe Comp B", "bedrooms": 6}
                },
                "disqualified": {},
                "excluded_comps": {},
            }
            reg_path.write_text(json.dumps(corrupt_reg), encoding="utf-8")
            specs_path.write_text("{}", encoding="utf-8")

            mgr = CompManager()
            mgr.REGISTRY_PATH = reg_path
            mgr.SPECS_PATH = specs_path
            mgr.CACHE_DIR = Path(tmpdir) / "cache"
            mgr.ENRICHED_DIR = Path(tmpdir) / "enriched"
            mgr.CACHE_DIR.mkdir()
            mgr.ENRICHED_DIR.mkdir()

            with patch.object(mgr, "_regenerate_dashboard"):
                removed = mgr.remove_comp("888111", reason="Testing multi-tier purge")

            self.assertTrue(removed)
            updated_reg = json.loads(reg_path.read_text(encoding="utf-8"))
            self.assertNotIn("888111", updated_reg["tier_a"])
            self.assertNotIn("888111", updated_reg["tier_b"])
            self.assertIn("888111", updated_reg["excluded_comps"])
            self.assertEqual(updated_reg["metadata"]["total_comps"], 0)

    def test_comp_manager_integrity_auto_heal(self):
        """Verify enforce_registry_integrity correctly heals corrupted multi-tier overlaps."""
        mgr = CompManager()
        corrupt_reg = {
            "metadata": {"total_comps": 5},
            "tier_a": {
                "6br_comp": {"listing_id": "6br_comp", "bedrooms": 6},
                "5br_comp": {"listing_id": "5br_comp", "bedrooms": 5},
                "excluded_comp": {"listing_id": "excluded_comp", "bedrooms": 6},
            },
            "tier_b": {
                "6br_comp": {"listing_id": "6br_comp", "bedrooms": 6},
                "5br_comp": {"listing_id": "5br_comp", "bedrooms": 5},
            },
            "disqualified": {},
            "excluded_comps": {
                "excluded_comp": {"listing_id": "excluded_comp", "reason": "Lacks pool"}
            },
        }

        healed, modified = mgr.enforce_registry_integrity(corrupt_reg)
        self.assertTrue(modified)

        # 6BR should be kept in tier_a and purged from tier_b
        self.assertIn("6br_comp", healed["tier_a"])
        self.assertNotIn("6br_comp", healed["tier_b"])

        # 5BR should be kept in tier_b and purged from tier_a
        self.assertIn("5br_comp", healed["tier_b"])
        self.assertNotIn("5br_comp", healed["tier_a"])

        # excluded_comp should be purged from tier_a
        self.assertNotIn("excluded_comp", healed["tier_a"])

        # total_comps should be exactly 2
        self.assertEqual(healed["metadata"]["total_comps"], 2)

    def test_html_generator_cohort_deduplication(self):
        """Verify HTMLGenerator._get_cohort_comps_for_segment guarantees strictly unique comps."""
        with tempfile.TemporaryDirectory() as tmpdir:
            reg_path = Path(tmpdir) / "comps_registry.json"
            out_path = Path(tmpdir) / "index.html"

            # Artificially create duplicate across tiers
            test_reg = {
                "metadata": {"total_comps": 2},
                "tier_a": {
                    "dupe_123": {"listing_id": "dupe_123", "name": "Estate", "bedrooms": 6}
                },
                "tier_b": {
                    "dupe_123": {"listing_id": "dupe_123", "name": "Estate", "bedrooms": 6}
                },
                "disqualified": {},
                "excluded_comps": {},
            }
            reg_path.write_text(json.dumps(test_reg), encoding="utf-8")

            gen = HTMLDashboardGenerator(output_path=str(out_path), comps_registry_path=str(reg_path))
            seg = {"check_in": "2026-10-15", "check_out": "2026-10-18", "nights": 3}
            cohort_comps = gen._get_cohort_comps_for_segment(seg, mult=1.0)

            cids = [c["listing_id"] for c in cohort_comps]
            self.assertEqual(len(cids), len(set(cids)), "Cohort comps must contain strictly unique listing IDs")
            self.assertEqual(cids.count("dupe_123"), 1, "Duplicate comp must only appear once in cohort")


if __name__ == "__main__":
    unittest.main()
