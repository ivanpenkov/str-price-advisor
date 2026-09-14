"""
Unit tests for the 12-Month Competitor Market Availability & Absorption Trajectory Chart.
Verifies timeline generation, mathematical invariants across cohorts, active reconciliation,
HTML embedding, and Chart.js integration.
"""

import json
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from src.competitor_sales_tracker import CompetitorSalesTracker
from src.html_generator import HTMLDashboardGenerator


class TestMarketTrajectoryChart(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name) / "data"
        self.config_dir = Path(self.temp_dir.name) / "config"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.config_dir.mkdir(parents=True, exist_ok=True)

        self.db_path = self.data_dir / "reservations.db"
        self.registry_path = self.config_dir / "comps_registry.json"

        # Create mock curated registry: 3 Tier A comps, 2 Tier B comps = 5 total comps
        self.registry_data = {
            "tier_a": {
                "comp_a1": {"listing_id": "comp_a1", "name": "Estate A1", "is_valid_comp": True},
                "comp_a2": {"listing_id": "comp_a2", "name": "Estate A2", "is_valid_comp": True},
                "comp_a3": {"listing_id": "comp_a3", "name": "Estate A3", "is_valid_comp": True},
            },
            "tier_b": {
                "comp_b1": {"listing_id": "comp_b1", "name": "Villa B1", "is_valid_comp": True},
                "comp_b2": {"listing_id": "comp_b2", "name": "Villa B2", "is_valid_comp": True},
            },
            "excluded_comps": {},
            "disqualified": {},
        }
        self.registry_path.write_text(json.dumps(self.registry_data), encoding="utf-8")

        # Initialize SQLite DB with tables
        conn = sqlite3.connect(str(self.db_path))
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS competitor_sales (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                listing_id TEXT NOT NULL,
                listing_name TEXT,
                tier TEXT,
                location TEXT,
                check_in TEXT NOT NULL,
                check_out TEXT NOT NULL,
                nights INTEGER NOT NULL,
                segment_type TEXT NOT NULL,
                detected_date TEXT NOT NULL,
                lead_time_days INTEGER NOT NULL,
                last_observed_rate REAL NOT NULL,
                last_observed_adj_rate REAL,
                last_observed_percentile REAL,
                composite_score REAL,
                desirability_ratio REAL,
                verification_status TEXT NOT NULL,
                raw_snippet TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(listing_id, check_in, check_out)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS reservations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                confirmation_id TEXT,
                creation_date TEXT,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                days_number INTEGER,
                type_id INTEGER,
                type_name TEXT,
                type_description TEXT,
                status_name TEXT NOT NULL,
                occupants INTEGER,
                occupants_small INTEGER,
                pets INTEGER,
                unit_id INTEGER,
                unit_name TEXT,
                owner_payout REAL,
                management_fee REAL,
                gross_rent REAL,
                is_future INTEGER,
                last_scraped_at TEXT,
                raw_json TEXT
            )
        """)
        # Insert a confirmed sale for comp_a2 on 2026-10-01 to 2026-10-04
        c.execute("""
            INSERT INTO competitor_sales (
                listing_id, listing_name, tier, location, check_in, check_out,
                nights, segment_type, detected_date, lead_time_days, last_observed_rate,
                verification_status
            ) VALUES ('comp_a2', 'Estate A2', 'tier_a', 'Scottsdale', '2026-10-01', '2026-10-04',
                      3, 'midweek', '2026-09-10', 21, 1500.0, 'CONFIRMED_BLOCKED')
        """)
        # Insert a Villa del Sol booking for 2026-10-15 to 2026-10-18
        c.execute("""
            INSERT INTO reservations (start_date, end_date, status_name)
            VALUES ('2026-10-15', '2026-10-18', 'Confirmed')
        """)
        conn.commit()
        conn.close()

        # Create mock pricing snapshot for 2026-09-13
        # Interval 1 (2026-09-20 to 2026-09-24): comp_a1 and comp_b1 available
        # Interval 2 (2026-10-01 to 2026-10-04): comp_a1 and comp_b2 available (comp_a2 has confirmed sale!)
        self.snapshot_path = self.data_dir / "pricing_data_2026-09-13.json"
        snapshot_data = {
            "property": "Villa del Sol",
            "report_date": "2026-09-13",
            "urgent_intervals": [
                {
                    "check_in": "2026-09-20",
                    "check_out": "2026-09-24",
                    "nights": 4,
                    "segment_type": "midweek",
                    "is_live_scan": True,
                    "comps_list": [
                        {"listing_id": "comp_a1", "title": "Estate A1"},
                        {"listing_id": "comp_b1", "title": "Villa B1"},
                    ],
                }
            ],
            "moderate_intervals": [
                {
                    "check_in": "2026-10-01",
                    "check_out": "2026-10-04",
                    "nights": 3,
                    "segment_type": "midweek",
                    "is_live_scan": True,
                    "comps_list": [
                        {"listing_id": "comp_a1", "title": "Estate A1"},
                        {"listing_id": "comp_b2", "title": "Villa B2"},
                    ],
                }
            ],
            "informational_intervals": [],
        }
        self.snapshot_path.write_text(json.dumps(snapshot_data), encoding="utf-8")

        self.tracker = CompetitorSalesTracker(
            db_path=self.db_path,
            data_dir=self.data_dir,
            registry_path=self.registry_path,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_timeline_structure_and_schema(self):
        timeline = self.tracker.compute_daily_market_inventory_timeline(
            snapshot_path=self.snapshot_path, days_ahead=365
        )
        self.assertEqual(timeline["days_count"], 365)
        self.assertEqual(len(timeline["dates"]), 365)
        self.assertEqual(len(timeline["month_labels"]), 365)
        self.assertEqual(len(timeline["days_meta"]), 365)
        self.assertIn("cohorts", timeline)
        self.assertIn("all", timeline["cohorts"])
        self.assertIn("tier_a", timeline["cohorts"])
        self.assertIn("tier_b", timeline["cohorts"])

    def test_mathematical_invariants_across_all_cohorts(self):
        timeline = self.tracker.compute_daily_market_inventory_timeline(
            snapshot_path=self.snapshot_path, days_ahead=365
        )
        all_c = timeline["cohorts"]["all"]
        tier_a_c = timeline["cohorts"]["tier_a"]
        tier_b_c = timeline["cohorts"]["tier_b"]

        self.assertEqual(all_c["total_comps"], 5)
        self.assertEqual(tier_a_c["total_comps"], 3)
        self.assertEqual(tier_b_c["total_comps"], 2)

        for i in range(365):
            # All comps: available + recorded_sales + unknown == 5
            sum_all = all_c["available"][i] + all_c["recorded_sales"][i] + all_c["unknown_unavailable"][i]
            self.assertEqual(sum_all, 5, f"All comps sum mismatch on day {i}")

            # Tier A: sum == 3
            sum_a = tier_a_c["available"][i] + tier_a_c["recorded_sales"][i] + tier_a_c["unknown_unavailable"][i]
            self.assertEqual(sum_a, 3, f"Tier A sum mismatch on day {i}")

            # Tier B: sum == 2
            sum_b = tier_b_c["available"][i] + tier_b_c["recorded_sales"][i] + tier_b_c["unknown_unavailable"][i]
            self.assertEqual(sum_b, 2, f"Tier B sum mismatch on day {i}")

    def test_sale_attribution_and_villa_booked_flag(self):
        timeline = self.tracker.compute_daily_market_inventory_timeline(
            snapshot_path=self.snapshot_path, days_ahead=60
        )
        dates = timeline["dates"]
        days_meta = timeline["days_meta"]
        all_c = timeline["cohorts"]["all"]

        # 2026-10-02 is inside comp_a2 sale interval (2026-10-01 to 2026-10-04)
        idx_sale = dates.index("2026-10-02")
        self.assertGreaterEqual(all_c["recorded_sales"][idx_sale], 1)
        # comp_a1 and comp_b2 available (2), comp_a2 sold (1), comp_a3 & comp_b1 unknown (2)
        self.assertEqual(all_c["available"][idx_sale], 2)
        self.assertEqual(all_c["recorded_sales"][idx_sale], 1)
        self.assertEqual(all_c["unknown_unavailable"][idx_sale], 2)

        # 2026-10-16 is inside Villa del Sol reservation (2026-10-15 to 2026-10-18)
        idx_villa = dates.index("2026-10-16")
        self.assertTrue(days_meta[idx_villa]["is_villa_booked"])
        self.assertFalse(days_meta[idx_villa]["is_villa_available"])
        self.assertEqual(days_meta[idx_villa]["villa_color"], "#475569")
        self.assertEqual(days_meta[idx_villa]["villa_status"], "BOOKED")
        self.assertIn("Guest Stay", days_meta[idx_villa]["villa_status_label"])

        # An unbooked date (e.g. 2026-09-25) should be marked available
        idx_open = dates.index("2026-09-25")
        self.assertTrue(days_meta[idx_open]["is_villa_available"])
        self.assertEqual(days_meta[idx_open]["villa_color"], "#facc15")
        self.assertEqual(days_meta[idx_open]["villa_status"], "AVAILABLE")
        self.assertEqual(days_meta[idx_open]["villa_status_label"], "Available & Open")

    def test_villa_del_sol_summary_metrics(self):
        timeline = self.tracker.compute_daily_market_inventory_timeline(
            snapshot_path=self.snapshot_path, days_ahead=60
        )
        self.assertIn("villa_del_sol_summary", timeline)
        v_summary = timeline["villa_del_sol_summary"]
        self.assertEqual(v_summary["total_days"], 60)
        self.assertEqual(v_summary["unavailable_days"], 3)  # Oct 15, 16, 17 (Oct 18 is checkout)
        self.assertEqual(v_summary["available_days"], 57)
        self.assertEqual(v_summary["availability_pct"], 95.0)
        self.assertEqual(v_summary["unavailability_pct"], 5.0)

    def test_html_rendering_and_chart_js_integration(self):
        generator = HTMLDashboardGenerator(output_path=self.data_dir / "index.html")
        timeline = self.tracker.compute_daily_market_inventory_timeline(
            snapshot_path=self.snapshot_path, days_ahead=365
        )
        sales_data = {"total_sales": 1, "overall_median_percentile": 60.0, "avg_lead_time_days": 21.0, "avg_rate": 1500.0, "weekend_sales_count": 0, "midweek_sales_count": 1}
        html_out = generator._render_market_sales_tab(
            sales_data=sales_data,
            recent_sales=[],
            market_timeline_data=timeline,
        )

        self.assertIn("12-Month Competitor Market Availability &amp; Absorption Trajectory", html_out)
        self.assertIn('id="marketTrajectoryChart"', html_out)
        self.assertIn('id="trajVillaStatusBadge"', html_out)
        self.assertIn('id="trajCompressionDays"', html_out)
        self.assertIn("villaAvailabilityBandPlugin", html_out)
        self.assertIn("marketCompressionPlugin", html_out)
        self.assertIn("plugins: [marketCompressionPlugin, villaAvailabilityBandPlugin]", html_out)
        self.assertIn("20% Availability Threshold", html_out)
        self.assertIn("High Compression Days", html_out)
        self.assertIn("Villa del Sol:", html_out)
        self.assertIn("displayColors: false", html_out)
        self.assertNotIn("Total Tracked Comps:", html_out)
        self.assertNotIn("Market Absorbed/Blocked:", html_out)
        self.assertIn("trajectory-tier-pills", html_out)
        self.assertIn("trajectory-range-pills", html_out)
        self.assertIn("setTrajectoryTier", html_out)
        self.assertIn("setTrajectoryRange", html_out)
        self.assertIn("initMarketTrajectoryChart", html_out)
        self.assertIn("window.trajectoryData =", html_out)
        self.assertIn("stack: 'comps'", html_out)

    def test_scarcity_compression_detection_and_timeline_metrics(self):
        # 1. Direct detect_market_compression scarcity test
        comp_res_true = self.tracker.detect_market_compression(
            check_in="2026-11-01",
            total_cohort_count=100,
            current_available_count=18,
        )
        self.assertTrue(comp_res_true["is_compressed"])
        self.assertEqual(comp_res_true["available_ratio"], 0.18)
        self.assertIn("High scarcity compression", comp_res_true["reason"])

        comp_res_false = self.tracker.detect_market_compression(
            check_in="2026-11-01",
            total_cohort_count=100,
            current_available_count=25,
        )
        self.assertFalse(comp_res_false["is_compressed"])
        self.assertEqual(comp_res_false["available_ratio"], 0.25)

        # 2. Timeline metrics test
        timeline = self.tracker.compute_daily_market_inventory_timeline(
            snapshot_path=self.snapshot_path, days_ahead=60
        )
        all_c = timeline["cohorts"]["all"]
        self.assertIn("compression_threshold", all_c)
        self.assertEqual(all_c["compression_threshold"], 1.0)  # 20% of 5 comps = 1.0
        self.assertIn("compression_days_count", all_c)
        self.assertIn("compression_days_pct", all_c)
        self.assertIn("is_compressed_series", all_c)

        days_meta = timeline["days_meta"]
        self.assertIn("is_compressed", days_meta[0])
        self.assertIn("compression_threshold", days_meta[0])


if __name__ == "__main__":
    unittest.main()
