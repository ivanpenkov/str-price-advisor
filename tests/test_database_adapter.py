"""
Unit tests for Unified Database Adapter (src/database.py)
and Database Administration Tooling (src/db_admin.py).
"""

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.database import (
    LibSQLRow,
    LibSQLCursor,
    TursoRemoteConnection,
    is_cloud_enabled,
    get_db_connection,
    DEFAULT_LOCAL_DB,
    _is_retryable_error,
)
from src.db_admin import (
    TABLE_SCHEMAS,
    provision_schemas,
    check_database_health,
    backup_cloud_database,
)


class TestLibSQLRow(unittest.TestCase):
    def test_row_access_by_index_and_key(self):
        columns = ["id", "CONFIRMATION_ID", "gross_rent"]
        values = [1, 1001, 2500.50]
        row = LibSQLRow(columns, values)

        # Index access
        self.assertEqual(row[0], 1)
        self.assertEqual(row[1], 1001)
        self.assertEqual(row[2], 2500.50)

        # Exact key access
        self.assertEqual(row["id"], 1)
        self.assertEqual(row["CONFIRMATION_ID"], 1001)

        # Case-insensitive access
        self.assertEqual(row["confirmation_id"], 1001)
        self.assertEqual(row.get("GROSS_RENT"), 2500.50)
        self.assertEqual(row.get("non_existent", "default"), "default")

        # Dict helpers
        self.assertEqual(row.keys(), ["id", "CONFIRMATION_ID", "gross_rent"])
        self.assertEqual(row.values(), [1, 1001, 2500.50])
        self.assertEqual(len(row), 3)
        self.assertEqual(list(row), [1, 1001, 2500.50])

    def test_row_slicing(self):
        columns = ["a", "b", "c", "d"]
        values = [10, 20, 30, 40]
        row = LibSQLRow(columns, values)
        self.assertEqual(row[1:3], (20, 30))
        self.assertEqual(row[:2], (10, 20))
        self.assertEqual(row[2:], (30, 40))

    def test_row_invalid_key_type(self):
        row = LibSQLRow(["col"], [123])
        with self.assertRaises(IndexError):
            _ = row[None]
        with self.assertRaises(IndexError):
            _ = row[1.5]


class TestLibSQLCursor(unittest.TestCase):
    def test_cursor_emulation(self):
        mock_conn = MagicMock()
        mock_rs = MagicMock()
        mock_rs.columns = ["id", "status"]
        mock_rs.rows = [[1, "confirmed"], [2, "tentative"]]
        mock_rs.last_insert_rowid = 2
        mock_rs.rows_affected = 2
        mock_conn._execute_with_retry.return_value = mock_rs

        cur = LibSQLCursor(mock_conn)
        cur.execute("SELECT id, status FROM reservations")

        # Description conforms to DB-API 7-tuple
        self.assertIsNotNone(cur.description)
        self.assertEqual(len(cur.description), 2)
        self.assertEqual(cur.description[0][0], "id")
        self.assertEqual(cur.lastrowid, 2)
        self.assertEqual(cur.rowcount, 2)

        # Fetchone
        r1 = cur.fetchone()
        self.assertEqual(r1["id"], 1)
        self.assertEqual(r1["status"], "confirmed")

        # Fetchremaining via fetchall
        rest = cur.fetchall()
        self.assertEqual(len(rest), 1)
        self.assertEqual(rest[0]["id"], 2)
        self.assertIsNone(cur.fetchone())

    def test_cursor_iterator_protocol(self):
        mock_conn = MagicMock()
        mock_rs = MagicMock()
        mock_rs.columns = ["id"]
        mock_rs.rows = [[1], [2], [3]]
        mock_conn._execute_with_retry.return_value = mock_rs

        cur = LibSQLCursor(mock_conn)
        cur.execute("SELECT id FROM tbl")

        # __iter__ returns self
        self.assertIs(iter(cur), cur)

        # Partial iteration via break
        for row in cur:
            self.assertEqual(row[0], 1)
            break

        # Subsequent fetchone resumes from next position
        r2 = cur.fetchone()
        self.assertIsNotNone(r2)
        self.assertEqual(r2[0], 2)

        # Next via iterator protocol
        r3 = next(cur)
        self.assertEqual(r3[0], 3)

        with self.assertRaises(StopIteration):
            next(cur)

    def test_cursor_context_manager(self):
        mock_conn = MagicMock()
        cur = LibSQLCursor(mock_conn)
        with cur as c:
            self.assertIs(c, cur)
        self.assertEqual(len(cur._rows), 0)


class TestDatabaseConnectionRouting(unittest.TestCase):
    def test_explicit_path_routes_to_local_sqlite(self):
        conn = get_db_connection(":memory:")
        self.assertIsInstance(conn, sqlite3.Connection)
        conn.close()

    def test_local_override_env_flag(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as tf:
            with patch.dict(os.environ, {"USE_LOCAL_SQLITE": "1"}):
                conn = get_db_connection(tf.name)
                self.assertIsInstance(conn, sqlite3.Connection)
                conn.close()

    def test_is_cloud_enabled_respects_flags(self):
        with patch.dict(os.environ, {"USE_LOCAL_SQLITE": "1", "TURSO_DATABASE_URL": "libsql://x", "TURSO_AUTH_TOKEN": "y"}):
            self.assertFalse(is_cloud_enabled())

        with patch.dict(os.environ, {"USE_LOCAL_SQLITE": "0", "TURSO_DATABASE_URL": "", "TURSO_AUTH_TOKEN": ""}):
            self.assertFalse(is_cloud_enabled())

    def test_non_retryable_errors(self):
        self.assertFalse(_is_retryable_error(Exception("SQLite error: no such table: foo")))
        self.assertFalse(_is_retryable_error(Exception("UNIQUE constraint failed: res.id")))
        self.assertTrue(_is_retryable_error(Exception("503 Service Unavailable")))


class TestDbAdmin(unittest.TestCase):
    def test_provision_schemas(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as tf:
            conn = sqlite3.connect(tf.name)
            provision_schemas(conn)
            cur = conn.cursor()
            for tbl in TABLE_SCHEMAS.keys():
                cur.execute(f"SELECT COUNT(*) FROM {tbl}")
                self.assertEqual(cur.fetchone()[0], 0)
            conn.close()

    def test_check_database_health(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as tf:
            conn = sqlite3.connect(tf.name)
            provision_schemas(conn)
            conn.close()
            with patch("src.db_admin.database.get_db_connection", side_effect=lambda: sqlite3.connect(tf.name)):
                health = check_database_health()
                self.assertEqual(health["status"], "healthy")
                self.assertEqual(health["backend_type"], "Local SQLite (data/reservations.db)")
                self.assertIn("reservations", health["table_counts"])
                self.assertEqual(health["table_counts"]["reservations"], 0)

    def test_load_streamline_snapshots_integration(self):
        from src.html_generator import HTMLDashboardGenerator
        with tempfile.NamedTemporaryFile(suffix=".db") as tf:
            conn = sqlite3.connect(tf.name)
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE property_rate_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_date TEXT,
                    calendar_date TEXT,
                    nightly_rate REAL,
                    interval_type TEXT,
                    season_name TEXT,
                    period_name TEXT,
                    created_at TEXT
                )
            """)
            cur.execute("""
                INSERT INTO property_rate_snapshots (
                    snapshot_date, calendar_date, nightly_rate, interval_type, season_name, period_name, created_at
                ) VALUES ('2026-02-01', '2026-03-01', 500.0, 'weekend', 'Spring', 'Period 1', '2026-02-01T12:00:00')
            """)
            conn.commit()
            conn.close()

            with patch("src.database.get_db_connection", return_value=sqlite3.connect(tf.name)):
                gen = HTMLDashboardGenerator()
                snaps, dt = gen._load_streamline_snapshots()
                self.assertIn("2026-02-01", snaps)
                self.assertEqual(len(snaps["2026-02-01"]["periods"]), 1)
                self.assertEqual(snaps["2026-02-01"]["periods"][0]["weekend"], 500.0)


class TestLibSQLRowEquality(unittest.TestCase):
    def test_row_equality(self):
        r1 = LibSQLRow(["id", "name"], [1, "test"])
        r2 = LibSQLRow(["id", "name"], [1, "test"])
        r3 = LibSQLRow(["id", "name"], [2, "test"])

        self.assertEqual(r1, r2)
        self.assertNotEqual(r1, r3)
        self.assertEqual(r1, [1, "test"])
        self.assertEqual(r1, (1, "test"))
        self.assertEqual(r1, {"id": 1, "name": "test"})
        self.assertNotEqual(r1, "invalid-type")


class TestCursorBatchAndFetchMany(unittest.TestCase):
    def test_executemany_aggregates_total_affected(self):
        mock_conn = MagicMock()
        mock_rs = MagicMock()
        mock_rs.columns = ["id"]
        mock_rs.rows = []
        mock_conn._executemany_with_retry.return_value = (mock_rs, 10)

        cur = LibSQLCursor(mock_conn)
        cur.executemany("INSERT INTO tbl VALUES (?)", [(1,), (2,)])
        self.assertEqual(cur.rowcount, 10)

    def test_fetchmany(self):
        mock_conn = MagicMock()
        mock_rs = MagicMock()
        mock_rs.columns = ["id"]
        mock_rs.rows = [[1], [2], [3], [4]]
        mock_conn._execute_with_retry.return_value = mock_rs

        cur = LibSQLCursor(mock_conn)
        cur.execute("SELECT id FROM tbl")
        b1 = cur.fetchmany(2)
        self.assertEqual(len(b1), 2)
        self.assertEqual(b1[0][0], 1)
        self.assertEqual(b1[1][0], 2)

        b2 = cur.fetchmany(2)
        self.assertEqual(len(b2), 2)
        self.assertEqual(b2[0][0], 3)
        self.assertEqual(b2[1][0], 4)

        b3 = cur.fetchmany(2)
        self.assertEqual(len(b3), 0)


class TestAuthErrorsNonRetryable(unittest.TestCase):
    def test_auth_errors_are_non_retryable(self):
        self.assertFalse(_is_retryable_error(Exception("401 Unauthorized")))
        self.assertFalse(_is_retryable_error(Exception("403 Forbidden")))
        self.assertFalse(_is_retryable_error(Exception("authentication failed")))
        self.assertFalse(_is_retryable_error(Exception("invalid token")))
        self.assertFalse(_is_retryable_error(Exception("SSLCertVerificationError: certificate verify failed")))
        self.assertFalse(_is_retryable_error(Exception("unable to get local issuer certificate")))


class TestZeroSleepRetry(unittest.TestCase):
    def test_retry_delay_env_zero(self):
        mock_client = MagicMock()
        mock_client.execute.side_effect = [Exception("503 Service Unavailable"), MagicMock(columns=["id"], rows=[[1]])]
        with patch.dict(os.environ, {"TURSO_RETRY_DELAY": "0.0"}):
            with patch("src.database._patch_libsql_http_if_needed"):
                conn = TursoRemoteConnection.__new__(TursoRemoteConnection)
                conn._client = mock_client
                rs = conn._execute_with_retry("SELECT 1", max_retries=2)
                self.assertEqual(rs.rows, [[1]])
                self.assertEqual(mock_client.execute.call_count, 2)


class TestExecutemanyChunking(unittest.TestCase):
    def test_chunking_sub_batches(self):
        mock_client = MagicMock()
        mock_client.batch.side_effect = [
            [MagicMock(rows_affected=500)],
            [MagicMock(rows_affected=500)],
            [MagicMock(rows_affected=200)],
        ]
        with patch("src.database._patch_libsql_http_if_needed"):
            conn = TursoRemoteConnection.__new__(TursoRemoteConnection)
            conn._client = mock_client
            params = [(i,) for i in range(1200)]
            last_rs, total = conn._executemany_with_retry("INSERT INTO tbl VALUES (?)", params, chunk_size=500)
            self.assertEqual(mock_client.batch.call_count, 3)
            self.assertEqual(total, 1200)


class TestStreamingBackup(unittest.TestCase):
    def test_backup_cloud_database_streaming(self):
        import gzip
        with tempfile.TemporaryDirectory() as td:
            with tempfile.NamedTemporaryFile(suffix=".db") as tf:
                db_conn = sqlite3.connect(tf.name)
                db_cur = db_conn.cursor()
                for tbl, schema in TABLE_SCHEMAS.items():
                    db_conn.executescript(schema)
                db_cur.execute("INSERT INTO sync_history (id, synced_at, sync_mode, records_fetched) VALUES (1, '2026-09-20T12:00:00', 'full', 42)")
                db_cur.execute("INSERT INTO reservations (id, days_number, status_name, gross_rent, last_scraped_at) VALUES (?, ?, ?, ?, ?)", (999, 3, 'confirmed', float('nan'), '2026-09-20T12:00:00'))
                db_conn.commit()
                db_conn.close()

                with patch("src.db_admin.database.get_db_connection", side_effect=lambda: sqlite3.connect(tf.name)):
                    backup_file = backup_cloud_database(output_dir=td, retention_days=30)
                    self.assertTrue(backup_file.exists())
                    with gzip.open(backup_file, "rt", encoding="utf-8") as f:
                        dump_text = f.read()
                    self.assertIn("BEGIN TRANSACTION;", dump_text)
                    self.assertIn("COMMIT;", dump_text)
                    self.assertIn("42", dump_text)
                    self.assertIn("NULL", dump_text)
                    # Verify gross_rent with float('nan') was converted to NULL and not unquoted nan
                    self.assertNotIn(" 999, ... nan", dump_text)
                    self.assertNotIn(", nan,", dump_text.lower())
                    self.assertEqual(dump_text.count("INSERT INTO sync_history"), 1)
                    self.assertEqual(dump_text.count("INSERT INTO reservations"), 1)


class TestDaemonThreadAndExitCleanup(unittest.TestCase):
    def test_async_executor_daemon_thread(self):
        from src.database import _patch_libsql_http_if_needed
        import libsql_client.sync
        _patch_libsql_http_if_needed()
        executor = libsql_client.sync._AsyncExecutor()
        try:
            self.assertTrue(executor._thread.daemon)
        finally:
            executor.close()

    def test_cleanup_shared_turso_client(self):
        from src.database import _cleanup_shared_turso_client, _TURSO_CLIENT_LOCK
        import src.database as db_mod
        mock_client = MagicMock()
        with _TURSO_CLIENT_LOCK:
            db_mod._SHARED_TURSO_CLIENT = mock_client
            db_mod._SHARED_TURSO_KEY = ("libsql://test", "test-token")
        _cleanup_shared_turso_client()
        mock_client.close.assert_called_once()
        self.assertIsNone(db_mod._SHARED_TURSO_CLIENT)
        self.assertIsNone(db_mod._SHARED_TURSO_KEY)


class TestOperationalRoleSeparation(unittest.TestCase):
    def test_workstation_mode_guards_scrape(self):
        import asyncio
        from src.cli import run_weekly_advisory
        with patch.dict(os.environ, {"STR_NODE_ROLE": "workstation", "IS_PRIMARY_SCRAPER": "0"}):
            with patch("builtins.print") as mock_print:
                asyncio.run(run_weekly_advisory(force=False))
                printed_text = " ".join(str(call) for call in mock_print.call_args_list)
                self.assertIn("OPERATIONAL ROLE SEPARATION: WORKSTATION MODE ACTIVE", printed_text)

    def test_workstation_mode_force_bypasses_guard(self):
        import asyncio
        from src.cli import run_weekly_advisory

        class HaltGuardBypassed(Exception):
            pass

        with patch.dict(os.environ, {"STR_NODE_ROLE": "workstation", "IS_PRIMARY_SCRAPER": "0"}):
            with patch("builtins.print") as mock_print:
                with patch("src.cli.load_config", return_value={"property": {"name": "Villa del Sol", "address": "Tempe, AZ", "kivoya_unit_id": "123"}}), \
                     patch("src.cli.KivoyaClient", side_effect=HaltGuardBypassed):
                    with self.assertRaises(HaltGuardBypassed):
                        asyncio.run(run_weekly_advisory(force=True))
                    printed_text = " ".join(str(call) for call in mock_print.call_args_list)
                    self.assertNotIn("OPERATIONAL ROLE SEPARATION: WORKSTATION MODE ACTIVE", printed_text)
                    self.assertIn("Villa del Sol", printed_text)



    @patch("src.database._load_env_file")
    def test_is_primary_node_precedence(self, mock_load_env):
        from src.cli import is_primary_node
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(is_primary_node())
        with patch.dict(os.environ, {"STR_NODE_ROLE": "workstation"}, clear=True):
            self.assertFalse(is_primary_node())
        with patch.dict(os.environ, {"STR_NODE_ROLE": "primary", "IS_PRIMARY_SCRAPER": "0"}, clear=True):
            self.assertFalse(is_primary_node())
        with patch.dict(os.environ, {"STR_NODE_ROLE": "workstation", "IS_PRIMARY_SCRAPER": "1"}, clear=True):
            self.assertTrue(is_primary_node())

    def test_push_to_github_skips_when_not_on_main(self):
        from src.cli import push_to_github
        with patch("builtins.print") as mock_print, patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="feature-branch\n")
            result = push_to_github()
            self.assertTrue(result)
            call_cmds = [c[0][0] for c in mock_run.call_args_list]
            self.assertEqual(call_cmds, [["git", "rev-parse", "--abbrev-ref", "HEAD"]])
            printed_text = " ".join(str(call) for call in mock_print.call_args_list)
            self.assertIn("Active branch is 'feature-branch' (not 'main')", printed_text)

    def test_push_to_github_includes_config_and_rebase(self):
        from src.cli import push_to_github
        with patch("builtins.print"), patch("subprocess.run") as mock_run:
            # git diff --staged returns 1 (has changes)
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="main\n"), # rev-parse branch
                MagicMock(returncode=0), # git add
                MagicMock(returncode=1), # git diff --staged
                MagicMock(returncode=0), # git commit
                MagicMock(returncode=0), # git pull --rebase
                MagicMock(returncode=0), # git push
            ]
            result = push_to_github()
            self.assertTrue(result)
            call_cmds = [c[0][0] for c in mock_run.call_args_list]
            self.assertEqual(call_cmds[0], ["git", "rev-parse", "--abbrev-ref", "HEAD"])
            self.assertEqual(call_cmds[1], ["git", "add", "docs/", "data/", "config/"])
            self.assertEqual(call_cmds[2], ["git", "diff", "--staged", "--quiet"])
            self.assertEqual(call_cmds[4], ["git", "pull", "--rebase", "-X", "theirs", "--autostash", "origin", "main"])
            self.assertEqual(call_cmds[5], ["git", "push", "origin", "main"])
            # Verify cwd was specified
            for call in mock_run.call_args_list:
                self.assertIn("cwd", call[1])

    def test_push_to_github_aborts_rebase_on_conflict(self):
        import subprocess
        from src.cli import push_to_github
        with patch("builtins.print"), patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="main\n"), # rev-parse branch
                MagicMock(returncode=0), # git add
                MagicMock(returncode=1), # git diff --staged
                MagicMock(returncode=0), # git commit
                subprocess.CalledProcessError(1, ["git", "pull", "--rebase"]), # rebase conflict
                MagicMock(returncode=0), # git rebase --abort
            ]
            result = push_to_github()
            self.assertFalse(result)
            call_cmds = [c[0][0] for c in mock_run.call_args_list]
            self.assertEqual(call_cmds[4], ["git", "pull", "--rebase", "-X", "theirs", "--autostash", "origin", "main"])
            self.assertEqual(call_cmds[5], ["git", "rebase", "--abort"])

    def test_push_to_github_pushes_ahead_commits_when_clean_stage(self):
        from src.cli import push_to_github
        with patch("builtins.print"), patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="main\n"), # rev-parse branch
                MagicMock(returncode=0), # git add
                MagicMock(returncode=0), # git diff --staged (clean)
                MagicMock(returncode=0, stdout="abc1234\n"), # git rev-list origin/main..HEAD (ahead)
                MagicMock(returncode=0), # git pull --rebase
                MagicMock(returncode=0), # git push
            ]
            result = push_to_github()
            self.assertTrue(result)
            call_cmds = [c[0][0] for c in mock_run.call_args_list]
            self.assertEqual(call_cmds[0], ["git", "rev-parse", "--abbrev-ref", "HEAD"])
            self.assertEqual(call_cmds[1], ["git", "add", "docs/", "data/", "config/"])
            self.assertEqual(call_cmds[2], ["git", "diff", "--staged", "--quiet"])
            self.assertEqual(call_cmds[3], ["git", "rev-list", "origin/main..HEAD"])
            self.assertEqual(call_cmds[4], ["git", "pull", "--rebase", "-X", "theirs", "--autostash", "origin", "main"])
            self.assertEqual(call_cmds[5], ["git", "push", "origin", "main"])

    def test_push_to_github_clean_noop(self):
        from src.cli import push_to_github
        with patch("builtins.print"), patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="main\n"), # rev-parse branch
                MagicMock(returncode=0), # git add
                MagicMock(returncode=0), # git diff --staged (clean)
                MagicMock(returncode=0, stdout="\n"), # git rev-list origin/main..HEAD (empty, not ahead)
            ]
            result = push_to_github()
            self.assertTrue(result)
            call_cmds = [c[0][0] for c in mock_run.call_args_list]
            self.assertEqual(call_cmds[0], ["git", "rev-parse", "--abbrev-ref", "HEAD"])
            self.assertEqual(call_cmds[1], ["git", "add", "docs/", "data/", "config/"])
            self.assertEqual(call_cmds[2], ["git", "diff", "--staged", "--quiet"])
            self.assertEqual(call_cmds[3], ["git", "rev-list", "origin/main..HEAD"])
            # Should not call git pull or git push
            self.assertEqual(len(call_cmds), 4)


if __name__ == "__main__":
    unittest.main()





