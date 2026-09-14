"""
Unit Tests for Mobile ntfy Bridge Daemon & Hybrid Smart Router
==============================================================
Tests configuration loading, shortcut dispatching, natural language agent routing,
immediate ACK notification formatting, response truncation, deduplication, and
Zero-Sleep invariant compliance.
"""

import json
import subprocess
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.mobile_ntfy_bridge import (
    CommandResult,
    CommandRouter,
    MobileBridgeConfig,
    NotificationDispatcher,
    NtfyBridgeDaemon,
)


class TestMobileBridgeConfig(unittest.TestCase):
    """Test configuration loading, environment overrides, and path resolution."""

    def test_default_config_loading(self):
        config = MobileBridgeConfig()
        self.assertTrue(config.inbound_topic.startswith("ivan-str-cmd-"))
        self.assertEqual(config.outbound_topic, "ivan-str-advisor-xyz")
        self.assertIn("status", config.shortcuts)
        self.assertIn("audit", config.shortcuts)
        self.assertIn("sales", config.shortcuts)
        self.assertIn("rating", config.shortcuts)
        self.assertIn("ratings", config.shortcuts)
        self.assertGreater(config.max_output_length, 1000)

    @patch.dict("os.environ", {
        "NTFY_INBOUND_TOPIC": "test-custom-inbound",
        "NTFY_OUTBOUND_TOPIC": "test-custom-outbound",
    })
    def test_environment_variable_overrides(self):
        config = MobileBridgeConfig()
        self.assertEqual(config.inbound_topic, "test-custom-inbound")
        self.assertEqual(config.outbound_topic, "test-custom-outbound")

    def test_missing_config_file_uses_defaults(self):
        config = MobileBridgeConfig(config_path=Path("/tmp/nonexistent_bridge_config.json"))
        self.assertTrue(len(config.inbound_topic) > 0)
        self.assertTrue(len(config.outbound_topic) > 0)

    def test_env_file_loading(self):
        import tempfile
        import os
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            env_file = tmp_root / ".env"
            env_file.write_text("NTFY_INBOUND_TOPIC=test-from-env-file\n")
            with patch.dict(os.environ, {}, clear=False):
                if "NTFY_INBOUND_TOPIC" in os.environ:
                    del os.environ["NTFY_INBOUND_TOPIC"]
                config = MobileBridgeConfig(config_path=tmp_root / "dummy.json", workspace_dir=tmp_root)
                self.assertEqual(config.inbound_topic, "test-from-env-file")


class TestCommandRouter(unittest.TestCase):
    """Test shortcut dispatching, argument forwarding, and agent invocation."""

    def setUp(self):
        self.config = MobileBridgeConfig()
        self.config.reconnect_delay = 0.0
        self.router = CommandRouter(self.config)

    def test_empty_message_returns_error(self):
        res = self.router.route_command("   ")
        self.assertFalse(res.success)
        self.assertEqual(res.returncode, 1)
        self.assertIn("No command", res.output)

    def test_help_menu_returns_registered_shortcuts(self):
        res = self.router.route_command("help")
        self.assertTrue(res.success)
        self.assertEqual(res.title, "Command Menu")
        self.assertIn("status", res.output)
        self.assertIn("audit", res.output)
        self.assertIn("Natural Language Agent Tasks", res.output)

    @patch("subprocess.run")
    def test_shortcut_execution_success(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["python", "-m", "src.cli", "status"],
            returncode=0,
            stdout="Status OK: 97 comps, 19 sales",
            stderr="",
        )

        res = self.router.route_command("status")
        self.assertTrue(res.success)
        self.assertEqual(res.title, "CLI: status")
        self.assertIn("97 comps", res.output)
        self.assertFalse(res.is_agent)
        mock_run.assert_called_once()

    @patch("subprocess.run")
    def test_slash_prefixed_shortcut_execution(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["python", "-m", "src.cli", "status"],
            returncode=0,
            stdout="Status OK: 97 comps",
            stderr="",
        )

        res = self.router.route_command("/status")
        self.assertTrue(res.success)
        self.assertEqual(res.title, "CLI: status")
        self.assertFalse(res.is_agent)
        mock_run.assert_called_once()

    @patch("subprocess.run")
    def test_rating_and_ratings_shortcuts_route_execution(self, mock_run):
        """Verify rating and ratings shortcuts dispatch show-ratings --mobile."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=["python", "-m", "src.cli", "show-ratings", "--mobile"],
            returncode=0,
            stdout="⭐ Villa del Sol — Ratings & Recent Reviews",
            stderr="",
        )

        res1 = self.router.route_command("rating")
        self.assertTrue(res1.success)
        self.assertEqual(res1.title, "CLI: rating")
        call_args1 = mock_run.call_args_list[0][0][0]
        self.assertIn("show-ratings", call_args1)
        self.assertIn("--mobile", call_args1)

        res2 = self.router.route_command("ratings")
        self.assertTrue(res2.success)
        self.assertEqual(res2.title, "CLI: ratings")
        call_args2 = mock_run.call_args_list[1][0][0]
        self.assertIn("show-ratings", call_args2)
        self.assertIn("--mobile", call_args2)

    @patch("subprocess.run")
    def test_shortcut_with_extra_arguments(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="Audited successfully",
            stderr="",
        )

        res = self.router.route_command("audit --no-save")
        self.assertTrue(res.success)
        called_cmd = mock_run.call_args[0][0]
        self.assertIn("--no-save", called_cmd)

    @patch("subprocess.run")
    def test_shortcut_timeout_handling(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["python"], timeout=300)
        res = self.router.route_command("status")
        self.assertFalse(res.success)
        self.assertIn("timed out", res.output)

    @patch("pathlib.Path.exists", return_value=True)
    @patch("subprocess.run")
    def test_natural_language_dispatches_to_agy(self, mock_run, mock_exists):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["agy"],
            returncode=0,
            stdout="I have analyzed the competitor sales.",
            stderr="",
        )

        prompt = "Why did comp 12345 get disqualified?"
        res = self.router.route_command(prompt)
        self.assertTrue(res.success)
        self.assertTrue(res.is_agent)
        self.assertEqual(res.title, "Agent Response")
        self.assertIn("competitor sales", res.output)

        called_cmd = mock_run.call_args[0][0]
        self.assertIn("-c", called_cmd)
        self.assertIn("-p", called_cmd)
        self.assertIn(prompt, called_cmd)
        self.assertIn("--dangerously-skip-permissions", called_cmd)

    @patch("pathlib.Path.exists", return_value=True)
    @patch("subprocess.run")
    def test_agent_includes_stderr_on_error(self, mock_run, mock_exists):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["agy"],
            returncode=1,
            stdout="Partial output before crash",
            stderr="Fatal agent execution error",
        )

        res = self.router.route_command("Failing request")
        self.assertFalse(res.success)
        self.assertIn("Partial output", res.output)
        self.assertIn("Errors/Warnings:", res.output)
        self.assertIn("Fatal agent execution error", res.output)

    @patch.object(Path, "exists", return_value=False)
    def test_agent_dispatch_when_agy_binary_missing(self, mock_exists):
        # Point to a guaranteed nonexistent path
        self.config.agy_path = Path("/nonexistent/bin/agy")
        res = self.router.route_command("Hello agent")
        self.assertFalse(res.success)
        self.assertIn("Could not locate 'agy' binary", res.output)


class TestNotificationDispatcher(unittest.TestCase):
    """Test immediate ACK generation and response payload formatting."""

    def setUp(self):
        self.config = MobileBridgeConfig()
        self.dispatcher = NotificationDispatcher(self.config)

    @patch("subprocess.run")
    def test_send_ack_formats_preview(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        long_prompt = "A" * 120
        self.dispatcher.send_ack(long_prompt)

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        self.assertIn("Antigravity: Command Received", cmd)
        msg_idx = cmd.index("--message") + 1
        self.assertTrue(cmd[msg_idx].endswith('..."'))
        self.assertLessEqual(len(cmd[msg_idx]), 95)

    @patch("subprocess.run")
    def test_send_response_truncates_long_output(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        huge_output = "X" * 5000
        result = CommandResult(
            title="Long Output",
            output=huge_output,
            success=True,
            duration_sec=1.5,
            is_agent=False,
        )

        self.dispatcher.send_response(result)
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        msg_idx = cmd.index("--message") + 1
        delivered_msg = cmd[msg_idx]
        self.assertLessEqual(len(delivered_msg), self.config.max_output_length)
        self.assertIn("Output truncated", delivered_msg)

    @patch("subprocess.run")
    def test_send_response_failure_sets_warning_tags(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        result = CommandResult(
            title="Failed Command",
            output="Fatal error occurred",
            success=False,
            duration_sec=0.2,
            is_agent=False,
            returncode=1,
        )

        self.dispatcher.send_response(result)
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        tags_idx = cmd.index("--tags") + 1
        self.assertEqual(cmd[tags_idx], "warning,x")
        priority_idx = cmd.index("--priority") + 1
        self.assertEqual(cmd[priority_idx], "high")


class TestNtfyBridgeDaemon(unittest.TestCase):
    """Test daemon lifecycle, event deduplication, and historical message filtering."""

    def setUp(self):
        self.config = MobileBridgeConfig()
        self.config.reconnect_delay = 0.0
        self.daemon = NtfyBridgeDaemon(self.config)

    def test_ignores_non_message_events(self):
        payload = {"event": "open", "topic": "test"}
        res = self.daemon.handle_message_payload(payload)
        self.assertIsNone(res)

        payload_keepalive = {"event": "keepalive", "topic": "test"}
        res = self.daemon.handle_message_payload(payload_keepalive)
        self.assertIsNone(res)

    def test_ignores_historical_messages(self):
        self.daemon.startup_time = 1000.0
        old_payload = {
            "id": "msg-old-1",
            "event": "message",
            "time": 800.0,
            "message": "status",
        }
        res = self.daemon.handle_message_payload(old_payload)
        self.assertIsNone(res)

    def test_deduplicates_duplicate_message_ids(self):
        self.daemon.startup_time = 1000.0
        payload = {
            "id": "msg-uniq-1",
            "event": "message",
            "time": 1050.0,
            "message": "help",
        }

        with patch.object(self.daemon.dispatcher, "send_ack"), \
             patch.object(self.daemon.dispatcher, "send_response"):
            first_res = self.daemon.handle_message_payload(payload)
            self.assertIsNotNone(first_res)

            # Second identical message must be dropped
            second_res = self.daemon.handle_message_payload(payload)
            self.assertIsNone(second_res)

    @patch("requests.get")
    def test_run_stream_stops_after_max_messages(self, mock_get):
        # Mock streaming response with NDJSON lines
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.iter_lines.return_value = [
            json.dumps({"event": "open"}).encode("utf-8"),
            json.dumps({
                "id": "msg-stream-1",
                "event": "message",
                "time": time.time() + 10,
                "message": "help",
            }).encode("utf-8"),
        ]
        mock_response.__enter__.return_value = mock_response
        mock_get.return_value = mock_response

        with patch.object(self.daemon.dispatcher, "send_ack"), \
             patch.object(self.daemon.dispatcher, "send_response"):
            self.daemon.run_stream(max_messages=1)

        self.assertFalse(self.daemon.running)
        self.assertIn("msg-stream-1", self.daemon.seen_ids)


class TestCliStatus(unittest.TestCase):
    """Test print_system_status in src/cli.py."""

    def test_print_system_status_structure(self):
        from src.cli import print_system_status
        status_str = print_system_status()
        self.assertIn("Villa del Sol — STR Advisor Status", status_str)
        self.assertIn("Comps Registry:", status_str)
        self.assertIn("Competitor Sales:", status_str)
        self.assertIn("Reservations:", status_str)
        self.assertIn("Latest Snapshot:", status_str)


if __name__ == "__main__":
    unittest.main()
