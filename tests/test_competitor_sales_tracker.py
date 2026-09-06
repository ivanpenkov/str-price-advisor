"""
Unit tests for Competitor Sales Tracking & Absorption Velocity Engine.
"""

from datetime import date
import json
from pathlib import Path
import tempfile
import unittest

from src.competitor_sales_tracker import CompetitorSalesTracker, BAYESIAN_SHRINKAGE_K


class TestCompetitorSalesTracker(unittest.TestCase):
    """Test suite for snapshot diffing, sales detection, and strategy grid analytics."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name) / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "reservations.db"
        self.reg_path = self.data_dir / "comps_registry.json"

        # Create mock comps registry with 1 registered comp
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
                }
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
            }
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

    def test_diff_snapshots_detects_confirmed_and_absent_sales(self):
        """Verify snapshot diffing correctly detects disappearance and classifies registered comps."""
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
                        {
                            "listing_id": "1001",
                            "name": "Luxury Desert Oasis Villa",
                            "effective_nightly": 1200.0,
                        },
                        {
                            "listing_id": "9999",  # Unregistered comp
                            "name": "Unknown House",
                            "effective_nightly": 800.0,
                        },
                        {
                            "listing_id": "2001",  # Still available in snap2
                            "name": "Mid-Century Tempe Retreat",
                            "effective_nightly": 600.0,
                        },
                    ]
                }
            ],
            "moderate_intervals": [],
            "informational_intervals": [],
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
                        {
                            "listing_id": "2001",  # Comp 2001 still available
                            "name": "Mid-Century Tempe Retreat",
                            "effective_nightly": 600.0,
                        },
                    ]
                }
            ],
            "moderate_intervals": [],
            "informational_intervals": [],
        }

        s1_path = self.data_dir / "pricing_data_2026-10-01.json"
        s2_path = self.data_dir / "pricing_data_2026-10-02.json"
        s1_path.write_text(json.dumps(snap1), encoding="utf-8")
        s2_path.write_text(json.dumps(snap2), encoding="utf-8")

        sales = self.tracker.diff_snapshots(s1_path, s2_path)
        self.assertEqual(len(sales), 2)

        # Comp 1001: registered -> CONFIRMED_BLOCKED
        sale_1001 = next(s for s in sales if s["listing_id"] == "1001")
        self.assertEqual(sale_1001["verification_status"], "CONFIRMED_BLOCKED")
        self.assertEqual(sale_1001["tier"], "tier_a")
        self.assertEqual(sale_1001["location"], "Scottsdale")
        self.assertEqual(sale_1001["last_observed_rate"], 1200.0)
        # Quality-adjusted: 1200 / 1.25 = 960.0
        self.assertEqual(sale_1001["last_observed_adj_rate"], 960.0)
        self.assertEqual(sale_1001["lead_time_days"], 49)  # 2026-11-20 - 2026-10-02 = 49 days
        self.assertEqual(sale_1001["segment_type"], "weekend")
        # 1200 is highest among [600, 800, 1200], so 3/3 = 100.0%
        self.assertEqual(sale_1001["last_observed_percentile"], 100.0)

        # Comp 9999: unregistered -> SEARCH_ABSENT
        sale_9999 = next(s for s in sales if s["listing_id"] == "9999")
        self.assertEqual(sale_9999["verification_status"], "SEARCH_ABSENT")
        self.assertEqual(sale_9999["last_observed_rate"], 800.0)
        # 800 is 2nd among [600, 800, 1200], so 2/3 = 66.7%
        self.assertEqual(sale_9999["last_observed_percentile"], 66.7)

    def test_idempotency_and_backfill(self):
        """Verify backfilling multiple times does not create duplicate entries."""
        snap1 = {
            "report_date": "2026-10-01",
            "urgent_intervals": [
                {
                    "check_in": "2026-12-10",
                    "check_out": "2026-12-14",
                    "segment_type": "weekend",
                    "nights": 4,
                    "comps_list": [{"listing_id": "1001", "effective_nightly": 1000.0}],
                }
            ]
        }
        snap2 = {
            "report_date": "2026-10-02",
            "urgent_intervals": [
                {
                    "check_in": "2026-12-10",
                    "check_out": "2026-12-14",
                    "segment_type": "weekend",
                    "nights": 4,
                    "comps_list": [],  # Disappeared!
                }
            ]
        }
        (self.data_dir / "pricing_data_2026-10-01.json").write_text(json.dumps(snap1))
        (self.data_dir / "pricing_data_2026-10-02.json").write_text(json.dumps(snap2))

        # First run
        sales1 = self.tracker.backfill_all_snapshots()
        self.assertEqual(len(sales1), 1)

        # Second run should not duplicate
        sales2 = self.tracker.backfill_all_snapshots()
        all_stored = self.tracker.get_all_sales()
        self.assertEqual(len(all_stored), 1)

    def test_strategy_grid_bayesian_shrinkage(self):
        """Verify 2D Strategy Matrix aggregates lead horizons and applies Bayesian shrinkage."""
        with self.tracker._get_connection() as conn:
            cursor = conn.cursor()
            # Insert 1 sale in 31-90d weekend bucket with 80% percentile
            cursor.execute("""
                INSERT INTO competitor_sales (
                    listing_id, listing_name, tier, location,
                    check_in, check_out, nights, segment_type,
                    detected_date, lead_time_days,
                    last_observed_rate, last_observed_adj_rate, last_observed_percentile,
                    composite_score, desirability_ratio, verification_status
                ) VALUES (
                    '1001', 'Luxury Villa', 'tier_a', 'Scottsdale',
                    '2026-11-15', '2026-11-18', 3, 'weekend',
                    '2026-10-01', 45,
                    1500.0, 1200.0, 80.0,
                    95.0, 1.25, 'CONFIRMED_BLOCKED'
                )
            """)
            conn.commit()

        grid_data = self.tracker.compute_strategy_grid()
        self.assertEqual(grid_data["total_sales"], 1)
        self.assertEqual(grid_data["avg_lead_time_days"], 45.0)
        self.assertEqual(grid_data["avg_rate"], 1500.0)

        # Check 31-90d weekend cell
        cell = grid_data["grid"]["31–90d"]["weekend"]
        self.assertEqual(cell["count"], 1)
        self.assertEqual(cell["empirical_p50"], 80.0)
        # Prior is 65.0, k = 3.0
        # Expected: (1 * 80.0 + 3 * 65.0) / (1 + 3) = (80 + 195) / 4 = 275 / 4 = 68.8%
        self.assertEqual(cell["recommended_target"], 68.8)
        self.assertFalse(cell["is_empirical"])  # n < 3

        # Insert 2 more sales to make n >= 3
        with self.tracker._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO competitor_sales (
                    listing_id, listing_name, tier, location,
                    check_in, check_out, nights, segment_type,
                    detected_date, lead_time_days,
                    last_observed_rate, last_observed_adj_rate, last_observed_percentile,
                    composite_score, desirability_ratio, verification_status
                ) VALUES 
                ('1002', 'Villa 2', 'tier_a', 'Scottsdale', '2026-11-20', '2026-11-23', 3, 'weekend', '2026-10-01', 50, 1400.0, 1100.0, 70.0, 95.0, 1.25, 'CONFIRMED_BLOCKED'),
                ('1003', 'Villa 3', 'tier_a', 'Scottsdale', '2026-11-27', '2026-11-30', 3, 'weekend', '2026-10-01', 57, 1600.0, 1300.0, 90.0, 95.0, 1.25, 'CONFIRMED_BLOCKED')
            """)
            conn.commit()

        grid_data_3 = self.tracker.compute_strategy_grid()
        cell_3 = grid_data_3["grid"]["31–90d"]["weekend"]
        self.assertEqual(cell_3["count"], 3)
        self.assertTrue(cell_3["is_empirical"])
        self.assertEqual(cell_3["empirical_p50"], 80.0)
        # Empirical median of [70.0, 80.0, 90.0] is 80.0
        # (3 * 80.0 + 3 * 65.0) / 6 = (240 + 195) / 6 = 435 / 6 = 72.5
        self.assertEqual(cell_3["recommended_target"], 72.5)


if __name__ == "__main__":
    unittest.main()
