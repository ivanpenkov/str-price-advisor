"""
Unit tests for CLI proxy orchestration and test-stealth diagnostic command:
Verifies shared proxy manager persistence across Step 3 and Step 4b,
dynamic quorum evaluation, and degraded capacity reporting.
"""

import asyncio
import io
import os
import sys
import unittest
from unittest.mock import patch, MagicMock, AsyncMock

from src.cli import main, run_weekly_advisory
from src.stealth_connection import StealthConnectionManager, StealthEndpoint


class TestCliProxyOrchestration(unittest.TestCase):

    def setUp(self):
        self.patcher_env = patch.dict("os.environ", {
            "NORDVPN_USER": "test_user",
            "NORDVPN_PASS": "test_pass",
            "STEALTH_STARTUP_DELAY": "0.0",
        })
        self.patcher_env.start()

    def tearDown(self):
        self.patcher_env.stop()

    def _patch_stealth_mgr(self, mock_mgr):
        p = patch("src.stealth_connection.StealthConnectionManager", return_value=mock_mgr)
        mock_cls = p.start()
        mock_cls.calculate_min_healthy = StealthConnectionManager.calculate_min_healthy
        self.addCleanup(p.stop)
        return mock_cls

    def test_stealth_command_quorum_met_exits_zero_with_warning(self):
        """Verify cli.py test-stealth exits with 0 and prints degraded warning when quorum is met but < count."""
        mock_mgr = MagicMock()
        mock_mgr.max_workers = 8
        # 6 healthy endpoints (quorum met for 8 workers: min_healthy = max(3, 8-2) = 6)
        eps = [
            StealthEndpoint(name=f"feeder-{i}", remote_host=f"host{i}:1080", local_port=56000 + i, status="ONLINE", latency_ms=45.0)
            for i in range(1, 7)
        ]
        mock_mgr.endpoints = eps
        mock_mgr.start_pool = AsyncMock()
        mock_mgr.stop = AsyncMock()

        self._patch_stealth_mgr(mock_mgr)
        test_args = ["src.cli", "test-stealth", "--count", "8"]
        with patch.object(sys, "argv", test_args), \
             patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            with self.assertRaises(SystemExit) as cm:
                main()
            self.assertEqual(cm.exception.code, 0)
            stdout_val = mock_stdout.getvalue()
            self.assertIn("QUORUM MET", stdout_val)
            self.assertIn("6/8 healthy nodes verified", stdout_val)

    def test_stealth_command_below_quorum_exits_one(self):
        """Verify cli.py test-stealth exits with 1 when healthy nodes fall below quorum."""
        mock_mgr = MagicMock()
        mock_mgr.max_workers = 8
        # Only 2 healthy endpoints (< min_healthy 6)
        eps = [
            StealthEndpoint(name=f"feeder-{i}", remote_host=f"host{i}:1080", local_port=56000 + i, status="ONLINE", latency_ms=45.0)
            for i in range(1, 3)
        ]
        mock_mgr.endpoints = eps
        mock_mgr.start_pool = AsyncMock()
        mock_mgr.stop = AsyncMock()

        self._patch_stealth_mgr(mock_mgr)
        test_args = ["src.cli", "test-stealth", "--count", "8"]
        with patch.object(sys, "argv", test_args), \
             patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            with self.assertRaises(SystemExit) as cm:
                main()
            self.assertEqual(cm.exception.code, 1)
            stdout_val = mock_stdout.getvalue()
            self.assertIn("Pre-flight audit failed", stdout_val)
            self.assertIn("minimum quorum: 6 required", stdout_val)

    def test_stealth_command_full_health_exits_zero(self):
        """Verify cli.py test-stealth exits with 0 and prints SUCCESS when all requested nodes are online."""
        mock_mgr = MagicMock()
        mock_mgr.max_workers = 8
        # All 8 healthy endpoints
        eps = [
            StealthEndpoint(name=f"feeder-{i}", remote_host=f"host{i}:1080", local_port=56000 + i, status="ONLINE", latency_ms=45.0)
            for i in range(1, 9)
        ]
        mock_mgr.endpoints = eps
        mock_mgr.start_pool = AsyncMock()
        mock_mgr.stop = AsyncMock()

        self._patch_stealth_mgr(mock_mgr)
        test_args = ["src.cli", "test-stealth", "--count", "8"]
        with patch.object(sys, "argv", test_args), \
             patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            with self.assertRaises(SystemExit) as cm:
                main()
            self.assertEqual(cm.exception.code, 0)
            stdout_val = mock_stdout.getvalue()
            self.assertIn("[SUCCESS] All 8/8 stealth endpoints are healthy", stdout_val)

    def test_run_weekly_advisory_shares_proxy_mgr_and_stops_in_finally(self):
        """Verify run_weekly_advisory instantiates shared StealthConnectionManager and stops pool in finally."""
        mock_proxy_mgr = MagicMock()
        mock_proxy_mgr.max_workers = 8
        mock_proxy_mgr.start_pool = AsyncMock()
        mock_proxy_mgr.stop = AsyncMock()
        mock_proxy_mgr.endpoints = [MagicMock()]

        collector_instances = []
        def fake_collector(*args, **kwargs):
            inst = MagicMock()
            inst.init_browser = AsyncMock()
            inst.close_browser = AsyncMock()
            inst.total_bytes_transferred = 1000
            inst.proxy_mgr = kwargs.get("proxy_mgr")
            collector_instances.append(inst)
            return inst

        comparator_instances = []
        def fake_comparator(*args, **kwargs):
            inst = MagicMock()
            inst.compare_all_intervals = AsyncMock()
            inst.total_bytes_transferred = 500
            inst.last_scrape_errors = {}
            inst.proxy_mgr = kwargs.get("proxy_mgr")
            comparator_instances.append(inst)
            return inst

        mock_config = {
            "property": {"name": "Villa del Sol", "address": "Tempe, AZ", "kivoya_unit_id": 123, "cleaning_fee": 300},
            "strategy": {"base_percentile": 50, "anomaly_thresholds": {"urgent_percent_diff": 10, "urgent_lead_days": 14, "moderate_percent_diff": 5}},
        }

        self._patch_stealth_mgr(mock_proxy_mgr)

        with patch("src.cli.load_config", return_value=mock_config), \
             patch("src.cli.KivoyaClient"), \
             patch("src.cli.CalendarSegmenter") as mock_seg, \
             patch("src.reservation_store.ReservationStore"), \
             patch("src.cli.snapshot_kivoya_rates", return_value=0), \
             patch("src.cli.PricingAnalyticsEngine"), \
             patch("src.cli.PriceReportGenerator") as mock_rep, \
             patch("src.cli.run_interval_evaluations", AsyncMock(return_value=([], []))), \
             patch("src.html_generator.HTMLDashboardGenerator") as mock_html, \
             patch("shutil.copy"), \
             patch("playwright.async_api.async_playwright") as mock_pw, \
             patch("src.cli.AirbnbCollector", side_effect=fake_collector), \
             patch("src.platform_comparator.PlatformComparator", side_effect=fake_comparator):

            mock_seg_inst = MagicMock()
            mock_seg_inst.generate_unbooked_segments.return_value = []
            mock_seg.return_value = mock_seg_inst

            mock_rep_inst = MagicMock()
            mock_rep_inst.generate_all.return_value = {"markdown": "m.md", "csv": "s.csv", "json": "j.json"}
            mock_rep.return_value = mock_rep_inst

            mock_pw_ctx = AsyncMock()
            mock_pw.return_value.__aenter__.return_value = mock_pw_ctx

            asyncio.run(run_weekly_advisory(quick=True, compare_platforms=True, parallel=True))

            # Assert shared proxy manager started before Step 3
            mock_proxy_mgr.start_pool.assert_awaited_once_with(
                num_workers=8,
                min_healthy=6,
                wait_for_full_pool=False,
            )

            # Assert shared manager passed to collector
            self.assertEqual(len(collector_instances), 1)
            self.assertIs(collector_instances[0].proxy_mgr, mock_proxy_mgr)

            # Assert shared manager passed to comparator
            self.assertEqual(len(comparator_instances), 1)
            self.assertIs(comparator_instances[0].proxy_mgr, mock_proxy_mgr)

            # Assert stop called in finally
            mock_proxy_mgr.stop.assert_awaited_once()

    def test_run_weekly_advisory_sequential_mode_starts_single_worker_pool(self):
        """Verify run_weekly_advisory in sequential mode (parallel=False) starts 1-worker pool with min_healthy=1."""
        mock_proxy_mgr = MagicMock()
        mock_proxy_mgr.max_workers = 8
        mock_proxy_mgr.start_pool = AsyncMock()
        mock_proxy_mgr.stop = AsyncMock()
        mock_proxy_mgr.endpoints = [MagicMock()]

        mock_config = {
            "property": {"name": "Villa del Sol", "address": "Tempe, AZ", "kivoya_unit_id": 123, "cleaning_fee": 300},
            "strategy": {"base_percentile": 50, "anomaly_thresholds": {"urgent_percent_diff": 10, "urgent_lead_days": 14, "moderate_percent_diff": 5}},
        }

        self._patch_stealth_mgr(mock_proxy_mgr)

        with patch("src.cli.load_config", return_value=mock_config), \
             patch("src.cli.KivoyaClient"), \
             patch("src.cli.CalendarSegmenter") as mock_seg, \
             patch("src.reservation_store.ReservationStore"), \
             patch("src.cli.snapshot_kivoya_rates", return_value=0), \
             patch("src.cli.PricingAnalyticsEngine"), \
             patch("src.cli.PriceReportGenerator") as mock_rep, \
             patch("src.cli.run_interval_evaluations", AsyncMock(return_value=([], []))), \
             patch("src.html_generator.HTMLDashboardGenerator"), \
             patch("shutil.copy"), \
             patch("playwright.async_api.async_playwright"), \
             patch("src.cli.AirbnbCollector") as mock_coll, \
             patch("src.platform_comparator.PlatformComparator") as mock_comp:

            mock_seg_inst = MagicMock()
            mock_seg_inst.generate_unbooked_segments.return_value = []
            mock_seg.return_value = mock_seg_inst

            mock_coll_inst = MagicMock()
            mock_coll_inst.init_browser = AsyncMock()
            mock_coll_inst.close_browser = AsyncMock()
            mock_coll_inst.total_bytes_transferred = 1000
            mock_coll.return_value = mock_coll_inst

            mock_comp_inst = MagicMock()
            mock_comp_inst.compare_all_intervals = AsyncMock()
            mock_comp_inst.total_bytes_transferred = 500
            mock_comp_inst.last_scrape_errors = {}
            mock_comp.return_value = mock_comp_inst

            asyncio.run(run_weekly_advisory(quick=True, compare_platforms=True, parallel=False))

            mock_proxy_mgr.start_pool.assert_awaited_once_with(
                num_workers=1,
                min_healthy=1,
                wait_for_full_pool=False,
            )
            mock_proxy_mgr.stop.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
