"""
Unit tests for real-time per-interval competitor sales logging in CLI.
Verifies inline active reconciliation, dropout verification, formatted terminal logging,
and Zero-Sleep test environment invariant compliance.
"""

import asyncio
from datetime import date
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock, AsyncMock

from src.cli import run_interval_evaluations
from src.competitor_sales_tracker import CompetitorSalesTracker


class TestCliSalesLogging(unittest.TestCase):
    """Test suite for inline per-interval competitor sales logging in CLI."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name) / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "reservations.db"
        self.reg_path = self.data_dir / "comps_registry.json"

        registry_data = {
            "tier_a": {
                "1001": {
                    "listing_id": "1001",
                    "name": "Luxury Desert Oasis Villa",
                    "location": "Scottsdale",
                    "bedrooms": 7,
                    "desirability_ratio": 1.25,
                    "composite_score": 95.0,
                },
            },
            "tier_b": {},
        }
        self.reg_path.write_text(json.dumps(registry_data), encoding="utf-8")

        self.tracker = CompetitorSalesTracker(
            db_path=self.db_path,
            data_dir=self.data_dir,
            registry_path=self.reg_path,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_run_interval_evaluations_logs_recorded_and_erased_sales(self):
        """Verify run_interval_evaluations prints recorded and erased lines directly under scan row."""
        active_segments = [
            {
                "check_in": "2026-09-21",
                "check_out": "2026-09-24",
                "nights": 3,
                "lead_time_days": 8,
                "segment_type": "midweek",
            },
            {
                "check_in": "2026-09-24",
                "check_out": "2026-09-27",
                "nights": 3,
                "lead_time_days": 11,
                "segment_type": "weekend",
            },
        ]

        # Mock collector
        mock_collector = MagicMock()
        del mock_collector.track_interval_bytes
        mock_collector.worker_contexts = []
        mock_collector.total_bytes_transferred = 500000
        mock_collector.fetch_comps_for_dates = AsyncMock(return_value=[
            {"listing_id": "1001", "effective_nightly": 1200.0}
        ])
        mock_collector.fetch_our_listing_price = AsyncMock(return_value=None)

        # Mock analytics engine
        mock_analytics = MagicMock()
        mock_analytics.sales_tracker = self.tracker
        mock_analytics.evaluate_segment = MagicMock(side_effect=lambda seg, rates, comp_metadata=None: {
            **seg,
            "comps_count": len(rates),
            "comps_list": comp_metadata or [],
            "p50_comp_rate": 1200.0,
        })

        # Mock reconcile_and_verify_interval to return recorded and erased sales for interval 1, empty for interval 2
        async def fake_reconcile(check_in, check_out, curr_comps, lead_time_days=None, segment_type=None, max_concurrent_checks=5, pdp_timeout=5.0):
            if check_in == "2026-09-21":
                return (
                    [
                        {"last_observed_rate": 2120.0, "last_observed_percentile": 74.1},
                        {"last_observed_rate": 2000.0, "last_observed_percentile": 64.1},
                    ],
                    [
                        {"last_observed_rate": 3120.0, "last_observed_percentile": 84.1},
                    ],
                )
            return ([], [])

        with patch.object(self.tracker, "reconcile_and_verify_interval", side_effect=fake_reconcile), \
             patch.object(self.tracker, "get_total_sales_count", return_value=19), \
             patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:

            evaluated, metrics = asyncio.run(run_interval_evaluations(
                collector=mock_collector,
                active_segments=active_segments,
                analytics=mock_analytics,
                parallel=False,
            ))

            output = mock_stdout.getvalue()

            # Verify interval 1 scan line and inline sales lines
            self.assertIn("Scanning 2026-09-21 -> 2026-09-24", output)
            self.assertIn("   - Comp sales recorded: $2,120 (74.1%), $2,000 (64.1%)", output)
            self.assertIn("   - Comp sales erased: $3,120 (84.1%)", output)

            # Verify interval 2 scan line
            self.assertIn("Scanning 2026-09-24 -> 2026-09-27", output)

            # Verify Phase 3b summary
            self.assertIn("✓ Competitor Sales Engine: Recorded 2 new sales, erased 1 sales (Total active sales in ledger: 19).", output)

    def test_run_interval_evaluations_suppresses_zero_sales(self):
        """Verify no extra sales lines are printed when an interval has zero sales."""
        active_segments = [
            {
                "check_in": "2026-10-01",
                "check_out": "2026-10-04",
                "nights": 3,
                "lead_time_days": 18,
                "segment_type": "midweek",
            },
        ]

        mock_collector = MagicMock()
        del mock_collector.track_interval_bytes
        mock_collector.worker_contexts = []
        mock_collector.total_bytes_transferred = 100000
        mock_collector.fetch_comps_for_dates = AsyncMock(return_value=[])
        mock_collector.fetch_our_listing_price = AsyncMock(return_value=None)

        mock_analytics = MagicMock()
        mock_analytics.sales_tracker = self.tracker
        mock_analytics.evaluate_segment = MagicMock(side_effect=lambda seg, rates, comp_metadata=None: {
            **seg,
            "comps_count": 0,
            "comps_list": [],
        })

        async def fake_zero_reconcile(*args, **kwargs):
            return ([], [])

        with patch.object(self.tracker, "reconcile_and_verify_interval", side_effect=fake_zero_reconcile), \
             patch.object(self.tracker, "get_total_sales_count", return_value=0), \
             patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:

            asyncio.run(run_interval_evaluations(
                collector=mock_collector,
                active_segments=active_segments,
                analytics=mock_analytics,
                parallel=False,
            ))

            output = mock_stdout.getvalue()
            self.assertIn("Scanning 2026-10-01 -> 2026-10-04", output)
            self.assertNotIn("Comp sales recorded", output)
            self.assertNotIn("Comp sales erased", output)
            self.assertIn("✓ Competitor Sales Engine: Recorded 0 new sales, erased 0 sales (Total active sales in ledger: 0).", output)


if __name__ == "__main__":
    unittest.main()
