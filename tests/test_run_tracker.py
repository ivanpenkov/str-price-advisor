"""
Unit Tests for RunTracker Lifecycle Manager and SecretScrubber.
"""

from datetime import datetime, timezone, timedelta
import io
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from src.run_tracker import (
    RunTracker,
    SecretScrubber,
    TeeStream,
    format_duration,
    get_active_host,
)


class TestSecretScrubber(unittest.TestCase):
    """Test credential discovery and sanitization rules."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.env_file = Path(self.tmpdir.name) / ".env"
        self.env_file.write_text(
            "STREAMLINE_API_KEY=secret_streamline_key_12345\n"
            "STREAMLINE_SECRET=super_secret_token_98765\n"
            "NORDVPN_PASSWORD=nord_ultra_pass_999\n"
            "CITY=Seattle\n"
            "DEBUG=True\n"
            "STATE=Arizona\n"
            "PORT=8000\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_scrubs_discovered_secrets(self):
        scrubber = SecretScrubber(env_path=self.env_file)
        raw_text = (
            "Connecting to Streamline API with secret_streamline_key_12345 and "
            "token super_secret_token_98765. Proxy auth nord_ultra_pass_999."
        )
        scrubbed = scrubber.scrub(raw_text)
        self.assertNotIn("secret_streamline_key_12345", scrubbed)
        self.assertNotIn("super_secret_token_98765", scrubbed)
        self.assertNotIn("nord_ultra_pass_999", scrubbed)
        self.assertIn("[REDACTED_SECRET]", scrubbed)

    def test_does_not_scrub_whitelisted_words(self):
        scrubber = SecretScrubber(env_path=self.env_file)
        raw_text = "Properties in Seattle, Arizona. DEBUG mode True on port 8000."
        scrubbed = scrubber.scrub(raw_text)
        self.assertIn("Seattle", scrubbed)
        self.assertIn("Arizona", scrubbed)
        self.assertIn("True", scrubbed)

    def test_scrubs_structural_patterns(self):
        scrubber = SecretScrubber(env_path=self.env_file)
        sample_jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozGzP_dummy_jwt_signature_long_enough_here"
        sample_sk = "sk_live_1234567890abcdef123456"
        sample_url = "https://user_bob:secret_pwd_999@api.example.com/v1/sync"
        sample_socks = "socks5://proxy_user:proxy_secret_pwd@feeder.nordvpn.com:1080"

        text = f"JWT: {sample_jwt}\nKey: {sample_sk}\nURL: {sample_url}\nProxy: {sample_socks}"
        scrubbed = scrubber.scrub(text)

        self.assertNotIn(sample_jwt, scrubbed)
        self.assertNotIn(sample_sk, scrubbed)
        self.assertNotIn("secret_pwd_999", scrubbed)
        self.assertNotIn("proxy_secret_pwd", scrubbed)
        self.assertIn("[REDACTED_JWT]", scrubbed)
        self.assertIn("[REDACTED_API_KEY]", scrubbed)
        self.assertIn("[REDACTED_USER]:[REDACTED_PASS]", scrubbed)


class TestTeeStream(unittest.TestCase):
    """Test console and buffer multiplexing with byte cap."""

    def test_multiplexing(self):
        orig = io.StringIO()
        buf = io.StringIO()
        tee = TeeStream(orig, buf)
        tee.write("Hello world!\n")
        self.assertEqual(orig.getvalue(), "Hello world!\n")
        self.assertEqual(buf.getvalue(), "Hello world!\n")

    def test_buffer_capping(self):
        orig = io.StringIO()
        buf = io.StringIO()
        tee = TeeStream(orig, buf)
        tee.MAX_BUFFER_BYTES = 50  # Low cap for test

        large_str = "A" * 100
        tee.write(large_str)
        self.assertIn("[OUTPUT BUFFER CAPPED AT 10 MB - TRUNCATED]", buf.getvalue())
        # Original stream received full content
        self.assertEqual(orig.getvalue(), large_str)


class TestRunTrackerLifecycle(unittest.TestCase):
    """Test RunTracker execution lifecycle, history tracking, and retention."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.base = Path(self.tmpdir.name)
        self.log_dir = self.base / "docs" / "logs"
        self.hist_path = self.base / "data" / "run_history.json"
        self.docs_hist_path = self.base / "docs" / "data" / "run_history.json"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_duration_formatting(self):
        self.assertEqual(format_duration(44.1), "44s")
        self.assertEqual(format_duration(397.4), "6m 37s")
        self.assertEqual(format_duration(3720.0), "1h 2m 0s")
        self.assertIsNone(format_duration(None))

    def test_successful_run(self):
        with RunTracker(
            job_type="daily-quickscan",
            trigger="launchd",
            log_dir=self.log_dir,
            history_path=self.hist_path,
            docs_history_path=self.docs_hist_path,
        ) as tracker:
            print("Running quickscan...")
            tracker.set_summary(intervals_evaluated=12, comps_scraped=97)
            tracker.set_message("Quickscan finished cleanly.")

        # Assert log file created
        log_files = list(self.log_dir.glob("*.txt"))
        self.assertEqual(len(log_files), 1)
        log_text = log_files[0].read_text(encoding="utf-8")
        self.assertIn("Running quickscan...", log_text)

        # Assert history files written
        self.assertTrue(self.hist_path.exists())
        self.assertTrue(self.docs_hist_path.exists())

        hist_data = json.loads(self.hist_path.read_text(encoding="utf-8"))
        self.assertEqual(len(hist_data["runs"]), 1)
        run = hist_data["runs"][0]
        self.assertEqual(run["job_type"], "daily-quickscan")
        self.assertEqual(run["status"], "SUCCESS")
        self.assertEqual(run["exit_code"], 0)
        self.assertEqual(run["summary"]["intervals_evaluated"], 12)
        self.assertEqual(run["summary"]["message"], "Quickscan finished cleanly.")
        self.assertIsNone(run["error_excerpt"])

    def test_failed_run_records_excerpt_and_propagates_exception(self):
        with self.assertRaises(ValueError):
            with RunTracker(
                job_type="pms-sync",
                trigger="launchd",
                log_dir=self.log_dir,
                history_path=self.hist_path,
                docs_history_path=self.docs_hist_path,
            ) as tracker:
                print("Starting PMS sync...")
                raise ValueError("Streamline connection timeout after 30s")

        hist_data = json.loads(self.hist_path.read_text(encoding="utf-8"))
        run = hist_data["runs"][0]
        self.assertEqual(run["status"], "FAILED")
        self.assertEqual(run["exit_code"], 1)
        self.assertIn("Streamline connection timeout", run["error_excerpt"])

    def test_untracked_manual_run_bypasses_persistence(self):
        with RunTracker(
            job_type="manual-cli",
            trigger="manual",
            enabled=False,
            log_dir=self.log_dir,
            history_path=self.hist_path,
            docs_history_path=self.docs_hist_path,
        ) as tracker:
            print("Local debug command")

        self.assertFalse(self.hist_path.exists())
        self.assertFalse(self.log_dir.exists())

    def test_retention_pruning(self):
        self.log_dir.mkdir(parents=True, exist_ok=True)
        # Create an old log file (20 days ago) and a recent log file (2 days ago)
        old_date = (datetime.now(timezone.utc) - timedelta(days=20)).strftime("%Y-%m-%d")
        recent_date = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d")

        old_log = self.log_dir / f"{old_date}_060000_pms-sync.txt"
        old_log.write_text("Old log content", encoding="utf-8")
        recent_log = self.log_dir / f"{recent_date}_060000_pms-sync.txt"
        recent_log.write_text("Recent log content", encoding="utf-8")

        # Create history file with 40-day old run and 5-day old run
        old_run_date = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat().replace("+00:00", "Z")
        recent_run_date = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat().replace("+00:00", "Z")

        initial_store = {
            "version": "1.0",
            "last_updated": recent_run_date,
            "active_host": "mac-mini",
            "runs": [
                {
                    "run_id": f"old_run_{old_date}",
                    "job_type": "pms-sync",
                    "job_title": "Daily Streamline PMS Sync",
                    "trigger": "launchd",
                    "status": "SUCCESS",
                    "start_time": old_run_date,
                    "end_time": old_run_date,
                    "duration_sec": 40.0,
                    "duration_formatted": "40s",
                    "exit_code": 0,
                    "git_commit": "abc1234",
                    "log_file": None,
                    "summary": {},
                    "error_excerpt": None,
                },
                {
                    "run_id": f"recent_run_{recent_date}",
                    "job_type": "pms-sync",
                    "job_title": "Daily Streamline PMS Sync",
                    "trigger": "launchd",
                    "status": "SUCCESS",
                    "start_time": recent_run_date,
                    "end_time": recent_run_date,
                    "duration_sec": 40.0,
                    "duration_formatted": "40s",
                    "exit_code": 0,
                    "git_commit": "abc1234",
                    "log_file": None,
                    "summary": {},
                    "error_excerpt": None,
                },
            ],
        }
        self.hist_path.parent.mkdir(parents=True, exist_ok=True)
        self.hist_path.write_text(json.dumps(initial_store), encoding="utf-8")

        # Execute a new run to trigger pruning
        with RunTracker(
            job_type="daily-quickscan",
            trigger="launchd",
            log_dir=self.log_dir,
            history_path=self.hist_path,
            docs_history_path=self.docs_hist_path,
        ) as tracker:
            tracker.set_message("New run")

        # Assert old log was pruned (> 14d) and recent was kept
        self.assertFalse(old_log.exists())
        self.assertTrue(recent_log.exists())

        # Assert old history run was purged (> 30d) and recent was kept
        updated_data = json.loads(self.hist_path.read_text(encoding="utf-8"))
        run_ids = [r["run_id"] for r in updated_data["runs"]]
        self.assertNotIn(f"old_run_{old_date}", run_ids)
        self.assertIn(f"recent_run_{recent_date}", run_ids)

    def test_load_store_fallback_to_docs_history(self):
        """When history_path is absent, _load_store falls back to docs_history_path."""
        self.docs_hist_path.parent.mkdir(parents=True, exist_ok=True)
        fallback_data = {
            "version": "1.0",
            "last_updated": "2026-09-22T00:00:00Z",
            "active_host": "mac-mini",
            "runs": [{"run_id": "fallback_run_1", "job_type": "pms-sync"}],
        }
        self.docs_hist_path.write_text(json.dumps(fallback_data), encoding="utf-8")
        if self.hist_path.exists():
            self.hist_path.unlink()

        tracker = RunTracker(
            job_type="pms-sync",
            history_path=self.hist_path,
            docs_history_path=self.docs_hist_path,
            enabled=False,
        )
        loaded = tracker._load_store(self.hist_path)
        self.assertEqual(len(loaded["runs"]), 1)
        self.assertEqual(loaded["runs"][0]["run_id"], "fallback_run_1")

    @patch("subprocess.run")
    def test_emergency_git_push_success_flow(self, mock_subproc):
        """Verify _emergency_git_push stages targets, commits alert, rebases, and pushes."""
        from src.run_tracker import REPO_ROOT

        # Return mock results for subprocess calls:
        # 1. git rev-parse --abbrev-ref HEAD -> main
        # 2. git add ...
        # 3. git diff --staged --quiet -> returncode 1 (staged changes exist)
        # 4. date -> 2026-09-22 23:00:00 PT
        # 5. git commit -m ...
        # 6. git pull --rebase ...
        # 7. git push ...
        def side_effect(cmd, **kwargs):
            m = MagicMock()
            m.returncode = 0
            if "rev-parse" in cmd:
                m.stdout = "main\n"
            elif "diff" in cmd and "--staged" in cmd:
                m.returncode = 1
            elif "date" in cmd[0]:
                m.stdout = "2026-09-22 23:00:00 PT\n"
            return m

        mock_subproc.side_effect = side_effect

        tracker = RunTracker(
            job_type="daily-quickscan",
            trigger="launchd",
            log_dir=self.log_dir,
            history_path=self.hist_path,
            docs_history_path=REPO_ROOT / "docs" / "data" / "run_history.json",
        )
        tracker.exit_code = 1
        tracker.status = "FAILED"
        tracker._emergency_git_push()

        # Check that commit and push commands were called
        called_cmds = [call.args[0] for call in mock_subproc.call_args_list if call.args]
        has_commit = any(isinstance(c, list) and c[0] == "git" and c[1] == "commit" for c in called_cmds)
        has_rebase = any(isinstance(c, list) and "--rebase" in c for c in called_cmds)
        has_push = any(isinstance(c, list) and c[0] == "git" and c[1] == "push" for c in called_cmds)

        self.assertTrue(has_commit, "Emergency push must execute git commit")
        self.assertTrue(has_rebase, "Emergency push must execute git pull --rebase")
        self.assertTrue(has_push, "Emergency push must execute git push")

    @patch("subprocess.run")
    def test_emergency_git_push_rebase_conflict_abort(self, mock_subproc):
        """Verify _emergency_git_push aborts rebase and skips push if rebase fails."""
        import subprocess
        from src.run_tracker import REPO_ROOT

        def side_effect(cmd, **kwargs):
            m = MagicMock()
            m.returncode = 0
            if "rev-parse" in cmd:
                m.stdout = "main\n"
            elif "diff" in cmd and "--staged" in cmd:
                m.returncode = 1
            elif "date" in cmd[0]:
                m.stdout = "2026-09-22 23:00:00 PT\n"
            elif "pull" in cmd and "--rebase" in cmd:
                raise subprocess.CalledProcessError(1, cmd)
            return m

        mock_subproc.side_effect = side_effect

        tracker = RunTracker(
            job_type="pms-sync",
            trigger="launchd",
            log_dir=self.log_dir,
            history_path=self.hist_path,
            docs_history_path=REPO_ROOT / "docs" / "data" / "run_history.json",
        )
        tracker.exit_code = 1
        tracker.status = "FAILED"
        tracker._emergency_git_push()

        called_cmds = [call.args[0] for call in mock_subproc.call_args_list if call.args]
        has_abort = any(isinstance(c, list) and c[0] == "git" and c[1] == "rebase" and c[2] == "--abort" for c in called_cmds)
        has_push = any(isinstance(c, list) and c[0] == "git" and c[1] == "push" for c in called_cmds)

        self.assertTrue(has_abort, "Must abort rebase on merge conflict")
        self.assertFalse(has_push, "Must NOT push when rebase failed")

    def test_secret_scrubber_nordvpn_pass_and_pproxy_format(self):
        """Verify SecretScrubber picks up NORDVPN_PASS and redacts pproxy #user:pass schemes."""
        with tempfile.NamedTemporaryFile("w+", delete=False) as f:
            f.write("NORDVPN_PASS=super_secret_vpn_pwd_1234\n")
            f.flush()
            env_file = Path(f.name)

        try:
            scrubber = SecretScrubber(env_path=env_file)
            self.assertIn("super_secret_vpn_pwd_1234", scrubber.secrets)

            # Test pproxy #user:pass scrubbing
            raw_log = "Spawning forwarder: pproxy -l http://127.0.0.1:56001 -r socks5://san-francisco.nordhold.net:1080#nord_user_99:nord_pass_secret"
            scrubbed = scrubber.scrub(raw_log)
            self.assertNotIn("nord_user_99", scrubbed)
            self.assertNotIn("nord_pass_secret", scrubbed)
            self.assertIn("[REDACTED_USER]:[REDACTED_PASS]", scrubbed)
        finally:
            env_file.unlink(missing_ok=True)

    def test_record_error_without_raising_preserves_failed_status(self):
        """Verify record_error without raising preserves FAILED status in __exit__."""
        tracker = RunTracker(
            job_type="pms-sync",
            trigger="launchd",
            log_dir=self.log_dir,
            history_path=self.hist_path,
            docs_history_path=self.docs_hist_path,
        )

        with tracker:
            print("Processing reservations...")
            tracker.record_error(RuntimeError("API quota exhausted"), excerpt="API quota exhausted (HTTP 429)")

        self.assertEqual(tracker.status, "FAILED")
        self.assertEqual(tracker.exit_code, 1)
        self.assertEqual(tracker.error_excerpt, "API quota exhausted (HTTP 429)")

        # Verify persisted store recorded FAILED
        store = tracker._load_store(self.hist_path)
        self.assertEqual(len(store["runs"]), 1)
        self.assertEqual(store["runs"][0]["status"], "FAILED")
        self.assertEqual(store["runs"][0]["exit_code"], 1)

    def test_html_patch_system_tab(self):
        """Verify HTMLDashboardGenerator.patch_system_tab updates the system tab in index.html in-place."""
        from src.html_generator import HTMLDashboardGenerator

        dummy_html = """<!DOCTYPE html>
<html>
<head><title>Test</title></head>
<body>
  <div id="tab-system" class="tab-content">
    <!-- BEGIN_SYSTEM_TAB -->
    <div>Old System Content</div>
    <!-- END_SYSTEM_TAB -->
  </div>
  <script>
    // BEGIN_SYSTEM_STORE_JSON
    let currentSystemHistory = {"runs": []};
    // END_SYSTEM_STORE_JSON
  </script>
</body>
</html>"""
        with tempfile.NamedTemporaryFile("w+", suffix=".html", delete=False) as f:
            f.write(dummy_html)
            f.flush()
            temp_path = Path(f.name)

        try:
            res = HTMLDashboardGenerator.patch_system_tab(temp_path)
            self.assertTrue(res, "patch_system_tab should return True on success")
            patched_content = temp_path.read_text(encoding="utf-8")
            self.assertNotIn("<div>Old System Content</div>", patched_content)
            self.assertIn("card-pms-sync", patched_content)
            self.assertIn("system-ledger-tbody", patched_content)
            self.assertIn("BEGIN_SYSTEM_STORE_JSON", patched_content)
        finally:
            temp_path.unlink(missing_ok=True)

    def test_html_patch_system_tab_fallback_regex(self):
        """Verify HTMLDashboardGenerator.patch_system_tab works with fallback regex when markers are absent."""
        from src.html_generator import HTMLDashboardGenerator

        dummy_html = """<!DOCTYPE html>
<html>
<head><title>Test Fallback</title></head>
<body>
  <div id="tab-system" class="tab-content">
    <div>Unmarked System Content</div>
  </div>
  <!-- Footer -->
  <script>
    let currentSystemHistory = {"runs": []};
  </script>
</body>
</html>"""
        with tempfile.NamedTemporaryFile("w+", suffix=".html", delete=False) as f:
            f.write(dummy_html)
            f.flush()
            temp_path = Path(f.name)

        try:
            res = HTMLDashboardGenerator.patch_system_tab(temp_path)
            self.assertTrue(res, "patch_system_tab should succeed via fallback regex")
            patched_content = temp_path.read_text(encoding="utf-8")
            self.assertNotIn("Unmarked System Content", patched_content)
            self.assertIn("BEGIN_SYSTEM_TAB", patched_content)
            self.assertIn("card-pms-sync", patched_content)
            self.assertIn("BEGIN_SYSTEM_STORE_JSON", patched_content)
        finally:
            temp_path.unlink(missing_ok=True)

    def test_html_patch_system_tab_with_traceback_backslashes_and_newlines(self):
        """Verify patch_system_tab safely handles error tracebacks, backslashes, and newlines without JS syntax errors."""
        from src.html_generator import HTMLDashboardGenerator

        # Create temporary directory structure: <temp_dir>/docs/index.html and <temp_dir>/docs/data/run_history.json
        with tempfile.TemporaryDirectory() as temp_dir:
            docs_dir = Path(temp_dir) / "docs"
            data_dir = docs_dir / "data"
            data_dir.mkdir(parents=True)
            html_file = docs_dir / "index.html"

            # Create history with multiline tracebacks, backslashes (\1, \g<1>), and special chars
            tricky_history = {
                "version": "1.0",
                "last_updated": "2026-09-23T07:00:00Z",
                "active_host": "mac-mini",
                "runs": [
                    {
                        "run_id": "test_err_run",
                        "job_type": "pms-sync",
                        "status": "FAILED",
                        "exit_code": 1,
                        "error_excerpt": "Traceback (most recent call last):\n  File \"re_test.py\", line 12\nre.error: invalid group reference \\1 \\g<1>",
                        "message": "Encountered C:\\path\\to\\file with \\n and \\t escape chars",
                    }
                ],
            }
            (data_dir / "run_history.json").write_text(json.dumps(tricky_history), encoding="utf-8")

            dummy_html = """<!DOCTYPE html>
<html>
<head><title>Test Escapes</title></head>
<body>
  <div id="tab-system" class="tab-content">
    <!-- BEGIN_SYSTEM_TAB -->
    <div>Old</div>
    <!-- END_SYSTEM_TAB -->
  </div>
  <script>
    // BEGIN_SYSTEM_STORE_JSON
    let currentSystemHistory = {};
    // END_SYSTEM_STORE_JSON
  </script>
</body>
</html>"""
            html_file.write_text(dummy_html, encoding="utf-8")

            res = HTMLDashboardGenerator.patch_system_tab(html_file)
            self.assertTrue(res, "patch_system_tab should succeed without re.error")

            content = html_file.read_text(encoding="utf-8")
            # Extract the embedded JSON string
            m = re.search(r"let currentSystemHistory = (\{.*?\});", content, re.DOTALL)
            self.assertIsNotNone(m, "currentSystemHistory must be present")
            embedded_json_str = m.group(1)

            # Ensure the embedded string is valid JSON and didn't have \n expanded to raw unescaped newlines
            parsed = json.loads(embedded_json_str)
            self.assertEqual(len(parsed["runs"]), 1)
            self.assertIn("invalid group reference \\1", parsed["runs"][0]["error_excerpt"])

    def test_run_tracker_exit_isolation_guard(self):
        """Verify RunTracker.__exit__ does not patch production docs/index.html when running with custom docs_history_path."""
        from src.run_tracker import REPO_ROOT

        with patch("src.html_generator.HTMLDashboardGenerator.patch_system_tab") as mock_patch, \
             patch.object(RunTracker, "_persist_history"), \
             patch.object(RunTracker, "_prune_retention"):
            # Custom path (should be skipped due to non-production docs_history_path)
            tracker = RunTracker(
                job_type="pms-sync",
                enabled=True,
                log_dir=self.log_dir,
                history_path=self.hist_path,
                docs_history_path=self.docs_hist_path,
            )
            with tracker:
                pass

            mock_patch.assert_not_called()

            # Production path (should trigger patch_system_tab)
            tracker_prod = RunTracker(
                job_type="pms-sync",
                enabled=True,
                log_dir=self.log_dir,
                history_path=self.hist_path,
                docs_history_path=REPO_ROOT / "docs" / "data" / "run_history.json",
            )
            with tracker_prod:
                pass

            mock_patch.assert_called_once_with(REPO_ROOT / "docs" / "index.html")


if __name__ == "__main__":
    unittest.main()

