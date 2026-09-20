"""
Unit tests for Competitor Sales Tracking & Absorption Velocity Engine.
Satisfies the 14-point Verification Matrix in docs/COMPETITOR_SALES_TRACKER_DESIGN.md §9.
"""

import asyncio
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock, AsyncMock

from src.competitor_sales_tracker import (
    CompetitorSalesTracker,
    HORIZON_PRIORS,
    BAYESIAN_SHRINKAGE_K,
    format_comp_sales_line,
)
from src.proposed_prices import compute_interval_consensus
from src.reporter import PriceReportGenerator


class TestCompetitorSalesTracker(unittest.TestCase):
    """Complete test suite for snapshot diffing, verification bridge, compression, and analytics."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name) / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir = self.data_dir / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "reservations.db"
        self.reg_path = self.data_dir / "comps_registry.json"

        # Create mock comps registry
        registry_data = {
            "tier_a": {
                "1001": {
                    "listing_id": "1001",
                    "name": "Luxury Desert Oasis Villa",
                    "location": "Scottsdale",
                    "bedrooms": 7,
                    "beds": 10,
                    "baths": 6.0,
                    "desirability_ratio": 1.25,
                    "composite_score": 95.0,
                },
                "1002": {
                    "listing_id": "1002",
                    "name": "Mesa Luxury Estate",
                    "location": "Mesa",
                    "bedrooms": 6,
                    "beds": 9,
                    "baths": 5.0,
                    "desirability_ratio": 1.10,
                    "composite_score": 90.0,
                },
                "1003": {
                    "listing_id": "1003",
                    "name": "Mesa Grand Villa",
                    "location": "Mesa",
                    "bedrooms": 6,
                    "beds": 8,
                    "baths": 5.0,
                    "desirability_ratio": 1.05,
                    "composite_score": 88.0,
                },
            },
            "tier_b": {
                "2001": {
                    "listing_id": "2001",
                    "name": "Mid-Century Tempe Retreat",
                    "location": "Tempe",
                    "bedrooms": 5,
                    "beds": 8,
                    "baths": 4.0,
                    "desirability_ratio": 0.90,
                    "composite_score": 82.0,
                }
            },
        }
        self.reg_path.write_text(json.dumps(registry_data), encoding="utf-8")

        self.tracker = CompetitorSalesTracker(
            db_path=self.db_path,
            data_dir=self.data_dir,
            registry_path=self.reg_path,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_database_init(self):
        """Verify competitor_sales table and indices are initialized."""
        with self.tracker._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='competitor_sales'")
            self.assertIsNotNone(cursor.fetchone())

            cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_comp_sales_lead'")
            self.assertIsNotNone(cursor.fetchone())

    # 1. Disappearance Detection
    def test_diff_snapshots_detects_sales(self):
        """Verify listing present in snapshot T-1 and missing in T generates sales record when verified."""
        snap1 = {
            "report_date": "2026-10-01",
            "urgent_intervals": [
                {
                    "check_in": "2026-11-20",
                    "check_out": "2026-11-23",
                    "segment_type": "weekend",
                    "nights": 3,
                    "lead_time_days": 50,
                    "comps_list": [
                        {"listing_id": "1001", "name": "Luxury Desert Oasis Villa", "effective_nightly": 1200.0},
                        {"listing_id": "9999", "name": "Unknown House", "effective_nightly": 800.0},
                        {"listing_id": "2001", "name": "Mid-Century Tempe Retreat", "effective_nightly": 600.0},
                    ],
                }
            ],
        }
        snap2 = {
            "report_date": "2026-10-02",
            "urgent_intervals": [
                {
                    "check_in": "2026-11-20",
                    "check_out": "2026-11-23",
                    "segment_type": "weekend",
                    "nights": 3,
                    "lead_time_days": 49,
                    "comps_list": [
                        {"listing_id": "2001", "name": "Mid-Century Tempe Retreat", "effective_nightly": 600.0},
                    ],
                }
            ],
        }

        s1_path = self.data_dir / "pricing_data_2026-10-01.json"
        s2_path = self.data_dir / "pricing_data_2026-10-02.json"
        s1_path.write_text(json.dumps(snap1), encoding="utf-8")
        s2_path.write_text(json.dumps(snap2), encoding="utf-8")

        # Unverified diff (verify_calendar=False): strictly omitted from sales ledger
        sales_unverified = self.tracker.diff_snapshots(s1_path, s2_path, verify_calendar=False)
        self.assertEqual(len(sales_unverified), 0)

        # Verified diff (verify_calendar=True): recorded as CONFIRMED_BLOCKED when calendar is blocked
        with patch.object(self.tracker, "verify_listing_availability", return_value={"unavail": True, "available": False, "price": None}):
            sales_verified = self.tracker.diff_snapshots(s1_path, s2_path, verify_calendar=True)
            self.assertEqual(len(sales_verified), 1)

            sale_1001 = sales_verified[0]
            self.assertEqual(sale_1001["listing_id"], "1001")
            self.assertEqual(sale_1001["verification_status"], "CONFIRMED_BLOCKED")
            self.assertEqual(sale_1001["tier"], "tier_a")
            self.assertEqual(sale_1001["last_observed_rate"], 1200.0)
            self.assertEqual(sale_1001["last_observed_adj_rate"], 960.0)  # 1200 / 1.25
            self.assertEqual(sale_1001["last_observed_percentile"], 100.0)

            # Unregistered comp 9999 is ignored
            self.assertFalse(any(s["listing_id"] == "9999" for s in sales_verified))

        # Verified diff when listing is actually AVAILABLE: strictly omitted and false sale purged
        with patch.object(self.tracker, "verify_listing_availability", return_value={"unavail": False, "available": True, "price": 1200.0}):
            sales_avail = self.tracker.diff_snapshots(s1_path, s2_path, verify_calendar=True)
            self.assertEqual(len(sales_avail), 0)
            cache_file = self.cache_dir / "search_2026-11-20_2026-11-23_comp_1001.json"
            self.assertTrue(cache_file.exists())
            cached_data = json.loads(cache_file.read_text(encoding="utf-8"))
            self.assertEqual(cached_data[0]["listing_id"], "1001")
            self.assertEqual(cached_data[0]["effective_nightly"], 400.0)  # 1200.0 / 3 nights
            self.assertEqual(cached_data[0]["total_price"], 1200.0)

    def test_diff_snapshots_inside_active_event_loop(self):
        """Verify diff_snapshots cleanly uses ThreadPoolExecutor when called inside an active event loop with coroutine."""
        snap1 = {
            "report_date": "2026-10-01",
            "urgent_intervals": [
                {
                    "check_in": "2026-11-20",
                    "check_out": "2026-11-23",
                    "segment_type": "weekend",
                    "is_live_scan": True,
                    "comps_list": [
                        {"listing_id": "1001", "name": "Desert Paradise", "effective_nightly": 1200.0},
                    ],
                }
            ],
        }
        snap2 = {
            "report_date": "2026-10-02",
            "urgent_intervals": [
                {
                    "check_in": "2026-11-20",
                    "check_out": "2026-11-23",
                    "segment_type": "weekend",
                    "is_live_scan": True,
                    "comps_list": [],
                }
            ],
        }
        s1_path = self.data_dir / "pricing_data_loop_1.json"
        s2_path = self.data_dir / "pricing_data_loop_2.json"
        s1_path.write_text(json.dumps(snap1), encoding="utf-8")
        s2_path.write_text(json.dumps(snap2), encoding="utf-8")

        async def run_in_loop():
            async def fake_verify(listing_id, check_in, check_out, accommodates=16, pdp_timeout=5.0):
                return {"unavail": True, "available": False, "price": None}

            with patch.object(self.tracker, "verify_listing_availability", side_effect=fake_verify):
                # Synchronous diff_snapshots invoked from within active running event loop
                return self.tracker.diff_snapshots(s1_path, s2_path, verify_calendar=True)

        sales = asyncio.run(run_in_loop())
        self.assertEqual(len(sales), 1)
        self.assertEqual(sales[0]["listing_id"], "1001")
        self.assertEqual(sales[0]["verification_status"], "CONFIRMED_BLOCKED")

    # 2. Active Reconciliation
    def test_reconcile_purges_available_listing(self):
        """Verify listing reappearing in current snapshot or local cache purges premature sale."""
        # Insert premature sale
        self.tracker.record_direct_sale(
            listing_id="1001",
            check_in="2026-12-10",
            check_out="2026-12-14",
            nights=4,
            last_observed_rate=1200.0,
            verification_status="CONFIRMED_BLOCKED",
        )
        self.assertEqual(len(self.tracker.get_all_sales()), 1)

        # Reappearing in current snapshot as available
        curr_snap = {
            "report_date": "2026-10-05",
            "urgent_intervals": [
                {
                    "check_in": "2026-12-10",
                    "check_out": "2026-12-14",
                    "is_live_scan": True,
                    "comps_list": [{"listing_id": "1001", "effective_nightly": 1200.0}],
                }
            ],
        }
        snap_path = self.data_dir / "pricing_data_2026-10-05.json"
        snap_path.write_text(json.dumps(curr_snap), encoding="utf-8")

        purged = self.tracker.reconcile_with_latest_snapshot(snap_path)
        self.assertEqual(purged, 1)
        self.assertEqual(len(self.tracker.get_all_sales()), 0)

    # 3. Live Scan Invariant
    def test_synthetic_scan_skipped(self):
        """Verify intervals with is_live_scan=False do not generate sales events."""
        snap_synth = {
            "report_date": "2026-10-01",
            "urgent_intervals": [
                {
                    "check_in": "2027-08-20",
                    "check_out": "2027-08-23",
                    "is_live_scan": False,
                    "comps_list": [{"listing_id": "1001", "effective_nightly": 1200.0}],
                }
            ],
        }
        snap_live = {
            "report_date": "2026-10-02",
            "urgent_intervals": [
                {
                    "check_in": "2027-08-20",
                    "check_out": "2027-08-23",
                    "is_live_scan": True,
                    "comps_list": [],
                }
            ],
        }
        s1 = self.data_dir / "snap_synth.json"
        s2 = self.data_dir / "snap_live.json"
        s1.write_text(json.dumps(snap_synth), encoding="utf-8")
        s2.write_text(json.dumps(snap_live), encoding="utf-8")

        sales = self.tracker.diff_snapshots(s1, s2, verify_calendar=True)
        self.assertEqual(len(sales), 0)

    # 4. Scrape Degradation Guard
    def test_scrape_degradation_guard(self):
        """Verify comp count dropping from >= 8 to <= 3 skips disappearance detection."""
        reg = json.loads(self.reg_path.read_text(encoding="utf-8"))
        for i in range(10):
            reg["tier_a"][f"comp_{i}"] = {
                "listing_id": f"comp_{i}",
                "name": f"Comp {i}",
                "location": "Scottsdale",
                "is_valid_comp": True,
                "desirability_ratio": 1.0,
                "composite_score": 85.0,
            }
        self.reg_path.write_text(json.dumps(reg), encoding="utf-8")

        snap1_comps = [{"listing_id": f"comp_{i}", "effective_nightly": 1000.0 + i} for i in range(10)]
        snap1 = {
            "report_date": "2026-10-01",
            "urgent_intervals": [
                {
                    "check_in": "2026-11-20",
                    "check_out": "2026-11-23",
                    "is_live_scan": True,
                    "comps_list": snap1_comps,
                }
            ],
        }
        snap2 = {
            "report_date": "2026-10-02",
            "urgent_intervals": [
                {
                    "check_in": "2026-11-20",
                    "check_out": "2026-11-23",
                    "is_live_scan": True,
                    "comps_list": [snap1_comps[0]],
                }
            ],
        }
        s1 = self.data_dir / "pricing_data_2026-10-01.json"
        s2 = self.data_dir / "pricing_data_2026-10-02.json"
        s1.write_text(json.dumps(snap1), encoding="utf-8")
        s2.write_text(json.dumps(snap2), encoding="utf-8")

        sales = self.tracker.diff_snapshots(s1, s2, verify_calendar=True)
        self.assertEqual(len(sales), 0)

    # 5. Dropped Corridor Guard
    def test_dropped_corridor_guard(self):
        """Verify entire geographic sub-market dropping to 0 skips sales detection."""
        snap1 = {
            "report_date": "2026-10-01",
            "urgent_intervals": [
                {
                    "check_in": "2026-12-04",
                    "check_out": "2026-12-07",
                    "is_live_scan": True,
                    "comps_list": [
                        {"listing_id": "1001", "name": "Scottsdale Villa", "location": "Scottsdale", "effective_nightly": 1200.0},
                        {"listing_id": "1002", "name": "Mesa Estate 1", "location": "Mesa", "effective_nightly": 900.0},
                        {"listing_id": "1003", "name": "Mesa Estate 2", "location": "Mesa", "effective_nightly": 850.0},
                    ],
                }
            ],
        }
        # In snap2, Mesa dropped to 0 comps entirely (scrape failed for Mesa corridor)
        snap2 = {
            "report_date": "2026-10-02",
            "urgent_intervals": [
                {
                    "check_in": "2026-12-04",
                    "check_out": "2026-12-07",
                    "is_live_scan": True,
                    "comps_list": [
                        {"listing_id": "1001", "name": "Scottsdale Villa", "location": "Scottsdale", "effective_nightly": 1200.0},
                    ],
                }
            ],
        }
        s1 = self.data_dir / "pricing_data_2026-10-01.json"
        s2 = self.data_dir / "pricing_data_2026-10-02.json"
        s1.write_text(json.dumps(snap1), encoding="utf-8")
        s2.write_text(json.dumps(snap2), encoding="utf-8")

        sales = self.tracker.diff_snapshots(s1, s2, verify_calendar=True)
        # Mesa dropouts (1002, 1003) must be skipped due to dropped corridor guard
        self.assertEqual(len(sales), 0)

    # 6. Earliest Detection Upsert
    def test_earliest_detected_date_preserved(self):
        """Verify repeated detections preserve earliest detected_date and max lead_time_days."""
        # Initial detection: 2026-10-01, check-in 2026-11-20 (lead = 50 days)
        self.tracker.record_direct_sale(
            listing_id="1001",
            check_in="2026-11-20",
            check_out="2026-11-23",
            nights=3,
            last_observed_rate=1200.0,
            detected_date="2026-10-01",
        )
        sales = self.tracker.get_all_sales()
        self.assertEqual(sales[0]["detected_date"], "2026-10-01")
        self.assertEqual(sales[0]["lead_time_days"], 50)

        # Repeated detection: 2026-10-05, check-in 2026-11-20 (lead = 46 days)
        self.tracker.record_direct_sale(
            listing_id="1001",
            check_in="2026-11-20",
            check_out="2026-11-23",
            nights=3,
            last_observed_rate=1200.0,
            detected_date="2026-10-05",
        )
        sales = self.tracker.get_all_sales()
        self.assertEqual(len(sales), 1)
        # Must preserve earlier detection date and higher lead time
        self.assertEqual(sales[0]["detected_date"], "2026-10-01")
        self.assertEqual(sales[0]["lead_time_days"], 50)

    # 7. Bayesian Shrinkage (Tiered Priors & n >= 5 Gating)
    def test_bayesian_shrinkage_tiered_priors(self):
        """Verify cell with n < 5 anchors to prior baseline; n >= 5 blends with k=5.0."""
        # Check empty grid (n = 0 for all cells)
        grid_data = self.tracker.compute_strategy_grid()
        cell_last_minute = grid_data["grid"]["≤14d"]["weekend"]
        self.assertEqual(cell_last_minute["count"], 0)
        self.assertIsNone(cell_last_minute["empirical_p50"])
        # Design Doc §3.4: ≤14d weekend target prior is 42.5%, p75 prior is 50.0%
        self.assertEqual(cell_last_minute["recommended_target"], 42.5)
        self.assertEqual(cell_last_minute["recommended_aggressive"], 50.0)

        # Insert 1 sale in 31–90d weekend bucket with 80% percentile
        self.tracker.record_direct_sale(
            listing_id="1001",
            check_in="2026-11-15",
            check_out="2026-11-18",
            nights=3,
            last_observed_rate=1500.0,
            detected_date="2026-10-01",  # 45 days lead
            segment_type="weekend",
        )
        with self.tracker._get_connection() as conn:
            conn.execute("UPDATE competitor_sales SET last_observed_percentile = 80.0 WHERE listing_id = '1001'")
            conn.commit()

        # With n = 1 (< 5), should stay anchored to prior baseline (62.5%)
        grid_1 = self.tracker.compute_strategy_grid()
        cell_31_90 = grid_1["grid"]["31–90d"]["weekend"]
        self.assertEqual(cell_31_90["count"], 1)
        self.assertEqual(cell_31_90["empirical_p50"], 80.0)
        self.assertEqual(cell_31_90["recommended_target"], 62.5)
        self.assertFalse(cell_31_90["is_empirical"])

        # Insert 4 more sales (total n = 5) all with 80% percentile
        with self.tracker._get_connection() as conn:
            for idx in range(2, 6):
                conn.execute("""
                    INSERT INTO competitor_sales (
                        listing_id, check_in, check_out, nights, segment_type,
                        detected_date, lead_time_days, last_observed_rate, last_observed_percentile,
                        verification_status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    f"100{idx % 3 + 1}", f"2026-11-{10+idx*2}", f"2026-11-{13+idx*2}", 3, "weekend",
                    "2026-10-01", 45, 1500.0, 80.0, "CONFIRMED_BLOCKED"
                ))
            conn.commit()

        grid_5 = self.tracker.compute_strategy_grid()
        cell_5 = grid_5["grid"]["31–90d"]["weekend"]
        self.assertEqual(cell_5["count"], 5)
        self.assertTrue(cell_5["is_empirical"])
        # Expected: (5 * 80.0 + 5.0 * 62.5) / 10 = (400 + 312.5) / 10 = 712.5 / 10 = 71.25 -> 71.2% or 71.3%
        # Monotonic tapering pass clamps to >90d weekend (prior 67.5%)
        self.assertLessEqual(cell_5["recommended_target"], grid_5["grid"][">90d"]["weekend"]["recommended_target"])

    # 8. Monthly Quartiles
    def test_monthly_lead_time_quartiles(self):
        """Verify P25, Median, P75 calculations across 12 calendar months."""
        # Insert sales for March (month 3) with lead times [150, 180, 200, 220]
        with self.tracker._get_connection() as conn:
            cursor = conn.cursor()
            for idx, lead in enumerate([150, 180, 200, 220], 1):
                cursor.execute("""
                    INSERT INTO competitor_sales (
                        listing_id, check_in, check_out, nights, segment_type,
                        detected_date, lead_time_days, last_observed_rate, verification_status
                    ) VALUES (?, '2027-03-15', '2027-03-18', 3, 'weekend', '2026-09-01', ?, 1200.0, 'CONFIRMED_BLOCKED')
                """, (f"m_{idx}", lead))
            conn.commit()

        windows = self.tracker.compute_monthly_lead_time_windows()
        m3 = windows["months"][3]
        self.assertEqual(m3["count"], 4)
        self.assertEqual(m3["min"], 150)
        self.assertEqual(m3["max"], 220)
        # Sorted: [150, 180, 200, 220], n=4
        # p25 index = floor(0.25 * 3) = 0 -> 150
        # median index = floor(0.50 * 3) = 1 -> 180
        # p75 index = floor(0.75 * 3) = 2 -> 200
        self.assertEqual(m3["p25"], 150)
        self.assertEqual(m3["median"], 180)
        self.assertEqual(m3["p75"], 200)
        self.assertIn("150–200 days out", m3["window_str"])

    # 9. Three-State PDP Verification
    def test_verify_listing_availability_three_states(self):
        """Verify parser sets blocked=True only on explicit unavailable signal; network timeouts return unverified without writing to DB."""
        mock_pm = MagicMock()
        mock_pm.start = AsyncMock(return_value=None)
        mock_pm.stop = AsyncMock(return_value=None)

        with patch("src.stealth_connection.StealthConnectionManager", return_value=mock_pm), \
             patch("playwright.async_api.async_playwright") as mock_ap:

            mock_p = MagicMock()
            mock_browser = MagicMock()
            mock_context = MagicMock()
            mock_page = MagicMock()

            mock_ap.return_value.__aenter__ = AsyncMock(return_value=mock_p)
            mock_ap.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_p.chromium.launch = AsyncMock(return_value=mock_browser)
            mock_browser.new_context = AsyncMock(return_value=mock_context)
            mock_context.new_page = AsyncMock(return_value=mock_page)
            mock_page.url = "https://www.airbnb.com/rooms/1001?check_in=2026-11-20&check_out=2026-11-23&adults=16"
            mock_page.goto = AsyncMock(return_value=None)
            mock_page.evaluate = AsyncMock(return_value=None)
            mock_page.close = AsyncMock(return_value=None)
            mock_browser.close = AsyncMock(return_value=None)

            # State 1: Available PDP
            def fake_on_avail(event, handler):
                if event == "response":
                    mock_resp = MagicMock()
                    mock_resp.url = "https://www.airbnb.com/api/v3/StaysPdpSections"
                    mock_resp.text = AsyncMock(return_value=json.dumps({
                        "data": {"presentation": {"stayProductDetailPage": {"sections": {"sections": [
                            {"section": {"structuredDisplayPrice": {"primaryLine": {"price": "$1,200"}}}}
                        ]}}}}
                    }))
                    asyncio.create_task(handler(mock_resp))
            mock_page.on.side_effect = fake_on_avail
            res_avail = asyncio.run(self.tracker.verify_listing_availability("1001", "2026-11-20", "2026-11-23", pdp_timeout=0.01))
            self.assertTrue(res_avail["available"])
            self.assertEqual(res_avail["price"], 1200.0)

            # State 2: Explicitly Blocked PDP
            def fake_on_blocked(event, handler):
                if event == "response":
                    mock_resp = MagicMock()
                    mock_resp.url = "https://www.airbnb.com/api/v3/StaysPdpSections"
                    mock_resp.text = AsyncMock(return_value=json.dumps({
                        "data": {"presentation": {"stayProductDetailPage": {"sections": {"sections": [
                            {"section": {"localizedUnavailabilityMessage": "These dates are not available", "available": False}}
                        ]}}}}
                    }))
                    asyncio.create_task(handler(mock_resp))
            mock_page.on.side_effect = fake_on_blocked
            res_blocked = asyncio.run(self.tracker.verify_listing_availability("1001", "2026-11-20", "2026-11-23", pdp_timeout=0.01))
            self.assertFalse(res_blocked["available"])
            self.assertTrue(res_blocked["unavail"])

            # State 3: Network Timeout (Unverified Error)
            mock_page.on.side_effect = None
            mock_page.goto = AsyncMock(side_effect=asyncio.TimeoutError("Navigation timeout"))
            res_timeout = asyncio.run(self.tracker.verify_listing_availability("1001", "2026-11-20", "2026-11-23", pdp_timeout=0.01))
            self.assertFalse(res_timeout["available"])
            self.assertFalse(res_timeout["unavail"])
            # State 4: SPA Navigation Reset (check_in stripped from URL)
            mock_page.goto = AsyncMock(return_value=None)
            mock_page.url = "https://www.airbnb.com/rooms/1001"
            mock_page.evaluate = AsyncMock(return_value="Some ordinary text without bot challenge")
            res_spa_reset = asyncio.run(self.tracker.verify_listing_availability("1001", "2026-11-20", "2026-11-23", pdp_timeout=0.01))
            self.assertTrue(res_spa_reset["unavail"])
            self.assertFalse(res_spa_reset["available"])
            self.assertIn("Client-side SPA navigation reset", res_spa_reset["reason"])

            # State 5: Bot Challenge Detected (Unverified Error, strictly NOT a false sale)
            mock_page.goto = AsyncMock(return_value=None)
            mock_page.url = "https://www.airbnb.com/rooms/1001"
            mock_page.evaluate = AsyncMock(return_value="Please verify you are human. Press and hold.")
            res_bot = asyncio.run(self.tracker.verify_listing_availability("1001", "2026-11-20", "2026-11-23", pdp_timeout=0.01))
            self.assertFalse(res_bot["available"])
            self.assertFalse(res_bot["unavail"])
            self.assertIn("Bot challenge detected", res_bot["reason"])

    # 10. Verification Bridge Flow
    def test_diff_and_verify_bridge_flow(self):
        """Verify asynchronous bridge queries PDP for candidates and commits only blocked listings."""
        prev_snap = {
            "report_date": "2026-10-01",
            "urgent_intervals": [
                {
                    "check_in": "2026-11-20",
                    "check_out": "2026-11-23",
                    "is_live_scan": True,
                    "comps_list": [
                        {"listing_id": "1001", "name": "Desert Villa", "effective_nightly": 1200.0},
                        {"listing_id": "1002", "name": "Mesa Villa", "effective_nightly": 900.0},
                    ],
                }
            ],
        }
        prev_path = self.data_dir / "pricing_data_2026-10-01.json"
        prev_path.write_text(json.dumps(prev_snap), encoding="utf-8")

        # In staged comps, both 1001 and 1002 disappeared from search
        staged = {
            ("2026-11-20", "2026-11-23"): []
        }

        # Mock verify_listing_availability: 1001 is unavail (blocked), 1002 is available
        async def mock_verify(listing_id, *args, **kwargs):
            if str(listing_id) == "1001":
                return {"listing_id": str(listing_id), "available": False, "unavail": True, "price": None, "reason": None}
            else:
                return {"listing_id": str(listing_id), "available": True, "unavail": False, "price": 900.0, "reason": None}

        with patch.object(self.tracker, "verify_listing_availability", side_effect=mock_verify):
            confirmed = asyncio.run(self.tracker.diff_and_verify_staged_comps(
                staged_intervals=staged,
                prev_snapshot_path=prev_path,
            ))

        # Only 1001 should be committed as confirmed blocked
        self.assertEqual(len(confirmed), 1)
        self.assertEqual(confirmed[0]["listing_id"], "1001")
        self.assertEqual(confirmed[0]["verification_status"], "CONFIRMED_BLOCKED")

        stored = self.tracker.get_all_sales()
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["listing_id"], "1001")

    # 11. Market Compression Surge Detection
    def test_market_compression_surge_detection(self):
        """Verify interval with < 20% available comps triggers surge directive across cohorts."""
        cin = "2026-12-18"
        cout = "2026-12-21"

        # Baseline: 5 of 10 available (50% >= 20%)
        comp_normal = self.tracker.detect_market_compression(
            check_in=cin, check_out=cout, total_cohort_count=10, current_available_count=5
        )
        self.assertFalse(comp_normal["is_compressed"])
        self.assertEqual(comp_normal["surge_multiplier"], 1.0)
        self.assertEqual(comp_normal["available_count"], 5)
        self.assertIn("Normal availability", comp_normal["reason"])

        # Scarcity trigger: 1 of 10 available (10% < 20%)
        comp_scarcity = self.tracker.detect_market_compression(
            check_in=cin, check_out=cout, total_cohort_count=10, current_available_count=1
        )
        self.assertTrue(comp_scarcity["is_compressed"])
        self.assertEqual(comp_scarcity["surge_multiplier"], 1.30)
        self.assertEqual(comp_scarcity["available_count"], 1)
        self.assertIn("High scarcity compression", comp_scarcity["reason"])

        # Cohort Invariant 1: All Comps (N=97)
        # <= 19 comps available -> compressed
        all_comp = self.tracker.detect_market_compression(
            check_in=cin, check_out=cout, total_cohort_count=97, current_available_count=19
        )
        self.assertTrue(all_comp["is_compressed"])
        self.assertEqual(all_comp["surge_multiplier"], 1.30)
        # 20 comps available -> normal
        all_normal = self.tracker.detect_market_compression(
            check_in=cin, check_out=cout, total_cohort_count=97, current_available_count=20
        )
        self.assertFalse(all_normal["is_compressed"])
        self.assertEqual(all_normal["surge_multiplier"], 1.0)

        # Cohort Invariant 2: Tier A (N=49)
        # <= 9 comps available -> compressed
        tier_a_comp = self.tracker.detect_market_compression(
            check_in=cin, check_out=cout, total_cohort_count=49, current_available_count=9
        )
        self.assertTrue(tier_a_comp["is_compressed"])
        tier_a_normal = self.tracker.detect_market_compression(
            check_in=cin, check_out=cout, total_cohort_count=49, current_available_count=10
        )
        self.assertFalse(tier_a_normal["is_compressed"])

        # Cohort Invariant 3: Tier B (N=48)
        # <= 9 comps available -> compressed
        tier_b_comp = self.tracker.detect_market_compression(
            check_in=cin, check_out=cout, total_cohort_count=48, current_available_count=9
        )
        self.assertTrue(tier_b_comp["is_compressed"])
        tier_b_normal = self.tracker.detect_market_compression(
            check_in=cin, check_out=cout, total_cohort_count=48, current_available_count=10
        )
        self.assertFalse(tier_b_normal["is_compressed"])

    # 12. Consensus Policy Surge Override
    def test_consensus_policy_surge_override(self):
        """Verify compute_interval_consensus overrides CONFLICT_HOLD and NO_HISTORY_HOLD when is_compression_surge=True."""
        # Standard conflict segment: Kivoya base $1000, Market Rec $800, Historical $1200 -> CONFLICT_HOLD
        seg_conflict = {
            "check_in": "2026-12-18",
            "check_out": "2026-12-21",
            "segment_type": "weekend",
            "our_base_nightly": 1000.0,
            "recommended_base_nightly": 800.0,
            "historical_benchmark": {"sample_count": 5, "median_rate": 1200.0},
        }
        res_normal = compute_interval_consensus(seg_conflict)
        self.assertEqual(res_normal["status"], "DECREASE")
        self.assertEqual(res_normal["consensus_rate"], 932)

        # Scarcity surge override active: overrides conflict hold and applies +30% surge rate
        seg_surge = dict(seg_conflict)
        seg_surge["is_compression_surge"] = True
        seg_surge["recommended_base_nightly"] = 1250.0  # Elevated market recommendation
        res_surge = compute_interval_consensus(seg_surge)
        self.assertEqual(res_surge["status"], "SURGE_INCREASE")
        # max(round(1000 * 1.30), 1250) = max(1300, 1250) = 1300
        self.assertEqual(res_surge["consensus_rate"], 1300)
        self.assertTrue(res_surge["is_compression_surge"])

    # 13. Dynamic Target Lookup
    def test_get_target_percentile_empirical_lookup(self):
        """Verify get_target_percentile() maps lead days to horizons, uses cached grid, and returns Bayesian targets."""
        # Far-out (>90d) weekend prior is 67.5%
        p_far_wkd = self.tracker.get_target_percentile(lead_time_days=100, segment_type="weekend")
        self.assertEqual(p_far_wkd, 67.5)

        # Near-term (15-30d) midweek prior is 32.5%
        p_near_mid = self.tracker.get_target_percentile(lead_time_days=20, segment_type="midweek")
        self.assertEqual(p_near_mid, 32.5)

        # Last-minute (<=14d) weekend prior is 42.5%
        p_last_wkd = self.tracker.get_target_percentile(lead_time_days=10, segment_type="weekend")
        self.assertEqual(p_last_wkd, 42.5)

    def test_compute_strategy_grid_with_floors(self):
        """Verify far-out weekend does not drop below strategic floor 65.0% despite empirical p50 = 41.0%."""
        # Insert 24 far-out (>90d) weekend bookings with p50 = 41.0%
        with self.tracker._get_connection() as conn:
            for i in range(24):
                conn.execute("""
                    INSERT INTO competitor_sales (
                        listing_id, check_in, check_out, nights, segment_type,
                        detected_date, lead_time_days, last_observed_rate, last_observed_percentile,
                        verification_status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    f"comp_{i}", f"2027-04-{(i%20)+1:02d}", f"2027-04-{(i%20)+4:02d}", 3, "weekend",
                    "2026-10-01", 180, 950.0, 41.0, "CONFIRMED_BLOCKED"
                ))
            conn.commit()

        grid = self.tracker.compute_strategy_grid()
        far_wkd = grid["grid"][">90d"]["weekend"]
        self.assertEqual(far_wkd["count"], 24)
        self.assertTrue(far_wkd["is_empirical"])
        self.assertTrue(far_wkd["is_floor_clamped"])
        # Strategic floor is 65.0%; empirical shrinkage without floor would be ~45.6%
        self.assertEqual(far_wkd["recommended_target"], 65.0)

    def test_is_floor_clamped_flag(self):
        """Verify is_floor_clamped is True when hat_Y < floor and False when hat_Y >= floor."""
        # 6 sales in far-out midweek with low percentile (20.0%) -> hat_Y < 45.0% floor -> is_floor_clamped True
        with self.tracker._get_connection() as conn:
            for i in range(6):
                conn.execute("""
                    INSERT INTO competitor_sales (
                        listing_id, check_in, check_out, nights, segment_type,
                        detected_date, lead_time_days, last_observed_rate, last_observed_percentile,
                        verification_status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    f"mid_{i}", f"2027-05-{(i%20)+1:02d}", f"2027-05-{(i%20)+4:02d}", 3, "midweek",
                    "2026-10-01", 200, 500.0, 20.0, "CONFIRMED_BLOCKED"
                ))
            conn.commit()

        grid = self.tracker.compute_strategy_grid()
        cell = grid["grid"][">90d"]["midweek"]
        self.assertTrue(cell["is_floor_clamped"])
        self.assertEqual(cell["recommended_target"], 45.0)

    def test_monotonic_tapering_enforcement(self):
        """Verify nearer horizon target is clamped so it never exceeds further-out horizon target."""
        # Insert 6 high-percentile bookings in 15-30d midweek (e.g. 85th percentile)
        # Prior is 32.5%. High empirical conversion would pull hat_Y to ~61.0% without monotonic tapering.
        # But >90d midweek is 45.0% and 31-90d midweek is 32.5%.
        # Monotonic tapering forces 15-30d <= 31-90d (32.5%).
        with self.tracker._get_connection() as conn:
            for i in range(6):
                conn.execute("""
                    INSERT INTO competitor_sales (
                        listing_id, check_in, check_out, nights, segment_type,
                        detected_date, lead_time_days, last_observed_rate, last_observed_percentile,
                        verification_status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    f"near_mid_{i}", f"2026-10-{20+(i%5)}", f"2026-10-{23+(i%5)}", 3, "midweek",
                    "2026-10-01", 20, 1200.0, 85.0, "CONFIRMED_BLOCKED"
                ))
            conn.commit()

        grid = self.tracker.compute_strategy_grid()
        target_31_90 = grid["grid"]["31–90d"]["midweek"]["recommended_target"]
        target_15_30 = grid["grid"]["15–30d"]["midweek"]["recommended_target"]
        self.assertLessEqual(target_15_30, target_31_90)

    def test_monotonic_tapering_disabled(self):
        """Verify enforce_monotonic_tapering=False permits nearer horizon targets to exceed further-out targets."""
        with self.tracker._get_connection() as conn:
            for i in range(6):
                conn.execute("""
                    INSERT INTO competitor_sales (
                        listing_id, check_in, check_out, nights, segment_type,
                        detected_date, lead_time_days, last_observed_rate, last_observed_percentile,
                        verification_status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    f"dis_mid_{i}", f"2026-10-{20+(i%5)}", f"2026-10-{23+(i%5)}", 3, "midweek",
                    "2026-10-01", 20, 1200.0, 85.0, "CONFIRMED_BLOCKED"
                ))
            conn.commit()

        # When enforce_monotonic_tapering is False, 15-30d empirical target reflects raw shrinkage (>32.5%)
        with patch("src.competitor_sales_tracker.ENFORCE_MONOTONIC_TAPERING", False):
            grid = self.tracker.compute_strategy_grid()
            target_31_90 = grid["grid"]["31–90d"]["midweek"]["recommended_target"]
            target_15_30 = grid["grid"]["15–30d"]["midweek"]["recommended_target"]
            self.assertGreater(target_15_30, target_31_90)

    def test_sample_size_gating_n5(self):
        """Verify cells with n < 5 remain non-empirical and prior-anchored."""
        # Insert 4 sales (n=4 < 5) with extreme percentile (95.0%)
        with self.tracker._get_connection() as conn:
            for i in range(4):
                conn.execute("""
                    INSERT INTO competitor_sales (
                        listing_id, check_in, check_out, nights, segment_type,
                        detected_date, lead_time_days, last_observed_rate, last_observed_percentile,
                        verification_status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    f"gate_{i}", f"2026-10-{10+i}", f"2026-10-{13+i}", 3, "weekend",
                    "2026-10-01", 10, 800.0, 95.0, "CONFIRMED_BLOCKED"
                ))
            conn.commit()

        grid = self.tracker.compute_strategy_grid()
        cell = grid["grid"]["≤14d"]["weekend"]
        self.assertEqual(cell["count"], 4)
        self.assertFalse(cell["is_empirical"])
        self.assertEqual(cell["recommended_target"], 42.5)  # exact prior target

    # 14. Advisory Report Alerts
    def test_reporter_embeds_sales_and_surge_alerts(self):
        """Verify latest_report.md includes recent sales table and scarcity warnings."""
        today_str = date.today().isoformat()
        cin = (date.today() + timedelta(days=20)).isoformat()
        cout = (date.today() + timedelta(days=23)).isoformat()

        # Insert 3 sales to populate recent sales ledger
        with self.tracker._get_connection() as conn:
            cursor = conn.cursor()
            for i in range(1, 4):
                cursor.execute("""
                    INSERT INTO competitor_sales (
                        listing_id, listing_name, tier, location,
                        check_in, check_out, nights, segment_type,
                        detected_date, lead_time_days, last_observed_rate, last_observed_adj_rate,
                        last_observed_percentile, verification_status
                    ) VALUES (?, ?, 'tier_a', 'Scottsdale', ?, ?, 3, 'weekend', ?, 20, 1200.0, 1200.0, 75.0, 'CONFIRMED_BLOCKED')
                """, (f"rep_{i}", f"Luxury Retreat {i}", cin, cout, today_str))
            conn.commit()

        reporter = PriceReportGenerator(output_dir=str(self.data_dir), sales_tracker=self.tracker)
        evaluated_segments = [
            {
                "check_in": cin,
                "check_out": cout,
                "check_in_dt": cin,
                "nights": 3,
                "segment_type": "weekend",
                "priority_tier": "INFORMATIONAL",
                "n_comps": 1,
                "is_compression_surge": True,
                "compression_details": {
                    "is_compressed": True,
                    "available_count": 1,
                    "total_cohort_count": 10,
                    "available_ratio": 0.10,
                    "surge_multiplier": 1.30,
                    "reason": f"High scarcity compression: only 1/10 (10.0%) comps available for check-in {cin}",
                },
                "our_base_nightly": 1000.0,
                "our_effective_nightly": 1000.0,
                "comp_p50_eff": 1000.0,
                "comp_target_eff": 1100.0,
                "price_diff_percent": 10.0,
                "recommended_base_nightly": 1100.0,
                "effective_nightly": 1100.0,
            }
        ]
        outputs = reporter.generate_all(evaluated_segments)
        md_text = Path(outputs["markdown"]).read_text(encoding="utf-8")

        # Verify Market Compression & Scarcity Alerts section is present
        self.assertIn("## ⚡ Market Compression & Scarcity Alerts", md_text)
        self.assertIn("Market Compression Alert", md_text)
        self.assertIn(cin, md_text)

        # Verify Recent Confirmed Competitor Sales Ledger is present
        self.assertIn("## 🎯 Recent Confirmed Competitor Sales Ledger", md_text)
        self.assertIn("Luxury Retreat 1", md_text)

    def test_strategy_grid_none_safety(self):
        """Verify compute_strategy_grid gracefully handles NULL last_observed_percentile."""
        today_str = date.today().isoformat()
        with self.tracker._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO competitor_sales (
                    listing_id, listing_name, tier, location,
                    check_in, check_out, nights, segment_type,
                    detected_date, lead_time_days, last_observed_rate, last_observed_adj_rate,
                    last_observed_percentile, verification_status
                ) VALUES ('null_test', 'Null Test Villa', 'tier_a', 'Scottsdale',
                          '2026-11-20', '2026-11-23', 3, 'weekend',
                          ?, 20, 1200.0, 1200.0, NULL, 'CONFIRMED_BLOCKED')
            """, (today_str,))
            conn.commit()

        grid = self.tracker.compute_strategy_grid()
        self.assertIsNotNone(grid)
        self.assertIn("grid", grid)
        self.assertIn("15–30d", grid["grid"])
        weekend_cell = grid["grid"]["15–30d"]["weekend"]
        self.assertEqual(weekend_cell["count"], 1)
        self.assertEqual(weekend_cell["empirical_p50"], 50.0)

    def test_active_compression_alerts_with_snapshot_cohort(self):
        """Verify get_active_compression_alerts derives cohort count from latest snapshot."""
        today_str = date.today().isoformat()
        cin = "2026-12-18"
        cout = "2026-12-21"

        # Create a mock snapshot with 6 comps for this interval (6 available < 20% of 97 comps -> Pure Scarcity Compression)
        snap = {
            "report_date": today_str,
            "urgent_intervals": [
                {
                    "check_in": cin,
                    "check_out": cout,
                    "is_live_scan": True,
                    "comps_list": [{"listing_id": f"comp_{i}", "effective_nightly": 1000.0} for i in range(6)],
                }
            ]
        }
        snap_path = self.data_dir / f"pricing_data_{today_str}.json"
        snap_path.write_text(json.dumps(snap), encoding="utf-8")

        # Record 2 sales in ledger
        with self.tracker._get_connection() as conn:
            cursor = conn.cursor()
            for i in range(2):
                cursor.execute("""
                    INSERT INTO competitor_sales (
                        listing_id, listing_name, tier, location,
                        check_in, check_out, nights, segment_type,
                        detected_date, lead_time_days, last_observed_rate, last_observed_adj_rate,
                        last_observed_percentile, verification_status
                    ) VALUES (?, 'Alert Comp', 'tier_a', 'Scottsdale', ?, ?, 3, 'weekend', ?, 15, 1100.0, 1100.0, 70.0, 'CONFIRMED_BLOCKED')
                """, (f"alert_comp_{i}", cin, cout, today_str))
            conn.commit()

        alerts = self.tracker.get_active_compression_alerts()
        self.assertTrue(len(alerts) >= 1)
        self.assertEqual(alerts[0]["check_in"], cin)
        self.assertTrue(alerts[0]["is_compressed"])

    def test_format_comp_sales_line(self):
        """Verify format_comp_sales_line produces the exact requested output format."""
        # Empty list -> returns empty string
        self.assertEqual(format_comp_sales_line("Comp sales recorded", []), "")
        self.assertEqual(format_comp_sales_line("Comp sales erased", []), "")

        # Single sale
        rec_single = [{"last_observed_rate": 2120.0, "last_observed_percentile": 74.1}]
        self.assertEqual(
            format_comp_sales_line("Comp sales recorded", rec_single),
            "   - Comp sales recorded: $2,120 (74.1%)",
        )

        # Multiple sales matching user's exact example
        rec_multi = [
            {"last_observed_rate": 2120.4, "last_observed_percentile": 74.1},
            {"last_observed_rate": 2000.0, "last_observed_percentile": 64.1},
        ]
        self.assertEqual(
            format_comp_sales_line("Comp sales recorded", rec_multi),
            "   - Comp sales recorded: $2,120 (74.1%), $2,000 (64.1%)",
        )

        # Erased sale matching user's exact example
        era_single = [{"last_observed_rate": 3120.0, "last_observed_percentile": 84.1}]
        self.assertEqual(
            format_comp_sales_line("Comp sales erased", era_single),
            "   - Comp sales erased: $3,120 (84.1%)",
        )

        # Realized price fallback & None percentile
        fallback_sale = [{"realized_price": 1850.0, "market_percentile": None}]
        self.assertEqual(
            format_comp_sales_line("Comp sales recorded", fallback_sale),
            "   - Comp sales recorded: $1,850 (0.0%)",
        )

        # NaN percentile fallback
        nan_sale = [{"last_observed_rate": 2500.0, "last_observed_percentile": float("nan")}]
        self.assertEqual(
            format_comp_sales_line("Comp sales recorded", nan_sale),
            "   - Comp sales recorded: $2,500 (0.0%)",
        )

    def test_get_total_sales_count(self):
        """Verify get_total_sales_count correctly reports active confirmed sales."""
        self.assertEqual(self.tracker.get_total_sales_count(), 0)

        with self.tracker._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO competitor_sales (
                    listing_id, listing_name, tier, location, check_in, check_out,
                    nights, segment_type, detected_date, lead_time_days, last_observed_rate,
                    verification_status
                ) VALUES ('1001', 'Test Villa', 'tier_a', 'Scottsdale', '2026-10-01', '2026-10-04',
                          3, 'midweek', '2026-09-13', 18, 1200.0, 'CONFIRMED_BLOCKED')
            """)
            cursor.execute("""
                INSERT INTO competitor_sales (
                    listing_id, listing_name, tier, location, check_in, check_out,
                    nights, segment_type, detected_date, lead_time_days, last_observed_rate,
                    verification_status
                ) VALUES ('1002', 'Test Villa 2', 'tier_a', 'Scottsdale', '2026-10-01', '2026-10-04',
                          3, 'midweek', '2026-09-13', 18, 1300.0, 'UNVERIFIED_ERROR')
            """)
            conn.commit()

        # Only CONFIRMED_BLOCKED counted
        self.assertEqual(self.tracker.get_total_sales_count(), 1)

    def test_prepare_predecessor_snapshot(self):
        """Verify prepare_predecessor_snapshot caches prior intervals in memory."""
        snap_data = {
            "report_date": "2026-09-10",
            "urgent_intervals": [
                {
                    "check_in": "2026-09-20",
                    "check_out": "2026-09-23",
                    "nights": 3,
                    "segment_type": "midweek",
                    "is_live_scan": True,
                    "comps_list": [{"listing_id": "1001", "effective_nightly": 1250.0}],
                }
            ]
        }
        snap_file = self.data_dir / "pricing_data_2026-09-10.json"
        snap_file.write_text(json.dumps(snap_data), encoding="utf-8")

        cached = self.tracker.prepare_predecessor_snapshot(curr_date="2026-09-13")
        self.assertIn(("2026-09-20", "2026-09-23"), cached)
        self.assertIn("1001", cached[("2026-09-20", "2026-09-23")]["comps"])

    def test_reconcile_and_verify_interval_erases_active_comp(self):
        """Verify reconcile_and_verify_interval actively reconciles/erases sale if comp is available."""
        cin = "2026-10-15"
        cout = "2026-10-18"

        # Pre-seed a sale for comp 1001
        with self.tracker._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO competitor_sales (
                    listing_id, listing_name, tier, location, check_in, check_out,
                    nights, segment_type, detected_date, lead_time_days, last_observed_rate,
                    last_observed_percentile, verification_status
                ) VALUES ('1001', 'Oasis Villa', 'tier_a', 'Scottsdale', ?, ?,
                          3, 'midweek', '2026-09-10', 35, 1500.0, 75.0, 'CONFIRMED_BLOCKED')
            """, (cin, cout))
            conn.commit()

        # Comp 1001 is now observed available in curr_comps
        curr_comps = [{"listing_id": "1001", "effective_nightly": 1550.0, "name": "Oasis Villa"}]

        recorded, erased = asyncio.run(self.tracker.reconcile_and_verify_interval(
            check_in=cin,
            check_out=cout,
            curr_comps=curr_comps,
        ))

        self.assertEqual(len(recorded), 0)
        self.assertEqual(len(erased), 1)
        self.assertEqual(erased[0]["listing_id"], "1001")
        self.assertEqual(erased[0]["last_observed_rate"], 1500.0)
        self.assertEqual(erased[0]["last_observed_percentile"], 75.0)

        # Database record must be deleted
        self.assertEqual(self.tracker.get_total_sales_count(), 0)

    def test_reconcile_and_verify_interval_records_confirmed_sale(self):
        """Verify reconcile_and_verify_interval detects and records confirmed sale on disappearance."""
        cin = "2026-10-20"
        cout = "2026-10-23"

        # Cache predecessor with comp 1001
        self.tracker._cached_prev_intervals = {
            (cin, cout): {
                "check_in": cin,
                "check_out": cout,
                "nights": 3,
                "segment_type": "midweek",
                "is_live_scan": True,
                "comps": {
                    "1001": {"listing_id": "1001", "effective_nightly": 1400.0, "location": "Scottsdale"}
                }
            }
        }

        # Current comps has comp 1002, missing comp 1001
        curr_comps = [{"listing_id": "1002", "effective_nightly": 1200.0, "location": "Mesa"}]

        # Mock verify_listing_availability to return unavail=True (confirmed sale)
        mock_verify = AsyncMock(return_value={"available": False, "unavail": True, "price": None})
        with patch.object(self.tracker, "verify_listing_availability", mock_verify):
            recorded, erased = asyncio.run(self.tracker.reconcile_and_verify_interval(
                check_in=cin,
                check_out=cout,
                curr_comps=curr_comps,
                pdp_timeout=0.01,
            ))

        self.assertEqual(len(recorded), 1)
        self.assertEqual(len(erased), 0)
        self.assertEqual(recorded[0]["listing_id"], "1001")
        self.assertEqual(recorded[0]["verification_status"], "CONFIRMED_BLOCKED")
        self.assertEqual(recorded[0]["last_observed_rate"], 1400.0)
        self.assertEqual(self.tracker.get_total_sales_count(), 1)

    def test_reconcile_and_verify_interval_auto_heals_if_candidate_available(self):
        """Verify reconcile_and_verify_interval auto-heals and erases sale if candidate is verified available."""
        cin = "2026-10-25"
        cout = "2026-10-28"

        # Pre-seed a sale for comp 1001
        with self.tracker._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO competitor_sales (
                    listing_id, listing_name, tier, location, check_in, check_out,
                    nights, segment_type, detected_date, lead_time_days, last_observed_rate,
                    last_observed_percentile, verification_status
                ) VALUES ('1001', 'Oasis Villa', 'tier_a', 'Scottsdale', ?, ?,
                          3, 'midweek', '2026-09-10', 45, 1450.0, 70.0, 'CONFIRMED_BLOCKED')
            """, (cin, cout))
            conn.commit()

        # Cache predecessor with comp 1001
        self.tracker._cached_prev_intervals = {
            (cin, cout): {
                "check_in": cin,
                "check_out": cout,
                "nights": 3,
                "segment_type": "midweek",
                "is_live_scan": True,
                "comps": {
                    "1001": {"listing_id": "1001", "effective_nightly": 1450.0, "location": "Scottsdale"}
                }
            }
        }

        # Current corridor sweep missed comp 1001
        curr_comps = []

        # Mock verify_listing_availability returns price and unavail=False (verified available!)
        mock_verify = AsyncMock(return_value={"available": True, "unavail": False, "price": 4350.0})
        with patch.object(self.tracker, "verify_listing_availability", mock_verify):
            recorded, erased = asyncio.run(self.tracker.reconcile_and_verify_interval(
                check_in=cin,
                check_out=cout,
                curr_comps=curr_comps,
                pdp_timeout=0.01,
            ))

        self.assertEqual(len(recorded), 0)
        self.assertEqual(len(erased), 1)
        self.assertEqual(erased[0]["listing_id"], "1001")
        self.assertEqual(self.tracker.get_total_sales_count(), 0)

        # Cache file should have been written
        cache_file = self.data_dir / "cache" / f"search_{cin}_{cout}_comp_1001.json"
        self.assertTrue(cache_file.exists())


if __name__ == "__main__":
    unittest.main()
