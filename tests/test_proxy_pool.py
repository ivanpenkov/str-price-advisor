"""
Unit tests for StealthConnectionManager stealth proxy pool, port allocation,
Phoenix IP exclusion, fallback behavior, and process lifecycle management.
"""

import asyncio
import itertools
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from src.stealth_connection import (
    StealthConnectionManager,
    StealthEndpoint,
    ServerHealthRecord,
    format_unhealthy_investigation_report,
    DEFAULT_STEALTH_HUBS,
    DEFAULT_MAX_STEALTH_CONNECTIONS,
    FALLBACK_STEALTH_SERVER,
    STATIC_CANDIDATE_STEALTH_SERVERS,
    get_free_port,
)


class TestStealthConnectionPool(unittest.TestCase):

    def setUp(self):
        self.patcher_env = patch.dict("os.environ", {
            "NORDVPN_USER": "test_user",
            "NORDVPN_PASS": "test_pass",
            "NORDVPN_SERVER": "los-angeles.us.socks.nordhold.net:1080",
            "STEALTH_STARTUP_DELAY": "0.0",
        })
        self.patcher_env.start()
        self.patcher_verify = patch.object(StealthConnectionManager, "verify_socks5", return_value=True)
        self.patcher_verify.start()
        self.patcher_conn = patch.object(StealthConnectionManager, "test_endpoint_connectivity", AsyncMock(return_value=(True, 50.0, "HTTP 200")))
        self.patcher_conn.start()
        self.patcher_cands = patch(
            "src.stealth_connection.fetch_nordvpn_candidate_servers",
            return_value=list(STATIC_CANDIDATE_STEALTH_SERVERS),
        )
        self.patcher_cands.start()

    def tearDown(self):
        self.patcher_cands.stop()
        self.patcher_conn.stop()
        self.patcher_verify.stop()
        self.patcher_env.stop()

    def test_default_max_stealth_connections_is_eight(self):
        """Verify DEFAULT_MAX_STEALTH_CONNECTIONS is 8 (leaving 2 connections for personal devices)."""
        self.assertEqual(DEFAULT_MAX_STEALTH_CONNECTIONS, 8)
        mgr = StealthConnectionManager(required=False)
        self.assertEqual(mgr.max_workers, 8)

    def test_feeder_servers_exclude_phoenix(self):
        """Verify that DEFAULT_STEALTH_HUBS strictly excludes Phoenix to avoid local host surveillance."""
        for item in DEFAULT_STEALTH_HUBS:
            name, host = item[0], item[2]
            self.assertNotIn("phoenix", host.lower(), f"Phoenix server found in stealth pool: {name} -> {host}")
        self.assertNotIn("phoenix", FALLBACK_STEALTH_SERVER.lower())

    def test_feeder_servers_major_travel_markets(self):
        """Verify that default stealth hubs correspond to major out-of-state travel hubs."""
        expected_hubs = ["los-angeles", "san-francisco", "dallas", "chicago"]
        for hub in expected_hubs:
            self.assertTrue(
                any(hub in item[2] for item in DEFAULT_STEALTH_HUBS),
                f"Expected travel hub '{hub}' not found in DEFAULT_STEALTH_HUBS",
            )

    @patch("src.stealth_connection.asyncio.create_subprocess_exec")
    @patch("src.stealth_connection.get_free_port")
    def test_start_pool_allocates_distinct_ports(self, mock_port, mock_subproc):
        """Verify start_pool assigns unique local ports to each stealth forwarder."""
        port_gen = itertools.count(56000)
        mock_port.side_effect = lambda *args, **kwargs: next(port_gen)

        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.wait = AsyncMock()
        mock_subproc.return_value = mock_proc

        mgr = StealthConnectionManager(required=True)

        async def run():
            configs = await mgr.start_pool(num_workers=4)
            return configs

        configs = asyncio.run(run())

        self.assertEqual(len(configs), 4)
        ports = [c["port"] for c in configs]
        self.assertEqual(len(ports), 4)
        self.assertEqual(len(set(ports)), 4)
        self.assertEqual(len(mgr.endpoints), 4)

        # Cleanup
        asyncio.run(mgr.stop_pool())
        self.assertEqual(len(mgr.endpoints), 0)

    @patch("src.stealth_connection.asyncio.create_subprocess_exec")
    @patch("src.stealth_connection.get_free_port")
    def test_start_pool_redirects_phoenix_server(self, mock_port, mock_subproc):
        """Verify that if a Phoenix server is explicitly passed to start_pool, it is redirected to Los Angeles."""
        port_gen = itertools.count(56100)
        mock_port.side_effect = lambda *args, **kwargs: next(port_gen)

        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.wait = AsyncMock()
        mock_subproc.return_value = mock_proc

        mgr = StealthConnectionManager(required=True)

        custom_servers = [
            ("feeder-phx", "phoenix.us.socks.nordhold.net:1080"),
            ("feeder-dal", "dallas.us.socks.nordhold.net:1080"),
        ]

        async def run():
            await mgr.start_pool(num_workers=2, servers=custom_servers)

        asyncio.run(run())

        # Check endpoints
        self.assertEqual(len(mgr.endpoints), 2)
        endpoint_hosts = [ep.remote_host for ep in mgr.endpoints]
        self.assertNotIn("phoenix.us.socks.nordhold.net:1080", endpoint_hosts)
        self.assertIn("los-angeles.us.socks.nordhold.net:1080", endpoint_hosts)
        self.assertIn("dallas.us.socks.nordhold.net:1080", endpoint_hosts)

        asyncio.run(mgr.stop_pool())

    @patch("src.stealth_connection.asyncio.create_subprocess_exec")
    @patch("src.stealth_connection.get_free_port")
    def test_start_pool_falls_back_on_subprocess_failure(self, mock_port, mock_subproc):
        """Verify that if a primary stealth server forwarder fails to launch, it falls back to FALLBACK_STEALTH_SERVER."""
        port_gen = itertools.count(56200)
        mock_port.side_effect = lambda *args, **kwargs: next(port_gen)

        good_proc = MagicMock()
        good_proc.returncode = None
        good_proc.wait = AsyncMock()
        # First call fails, subsequent calls succeed (including fallback)
        mock_subproc.side_effect = [Exception("Binding failed"), good_proc, good_proc, good_proc, good_proc]

        mgr = StealthConnectionManager(required=True)

        async def run():
            configs = await mgr.start_pool(num_workers=4)
            return configs

        configs = asyncio.run(run())
        self.assertEqual(len(configs), 4)

        # Check that one endpoint used a backup candidate or fallback server
        candidate_or_fallback_used = any(
            ep.remote_host in STATIC_CANDIDATE_STEALTH_SERVERS or ep.remote_host == FALLBACK_STEALTH_SERVER
            for ep in mgr.endpoints
        )
        self.assertTrue(candidate_or_fallback_used, "Expected backup candidate or fallback server to be utilized upon primary forwarder failure")

        asyncio.run(mgr.stop_pool())

    def test_missing_credentials_handling(self):
        """Verify StealthConnectionManager raises RuntimeError if required and credentials missing, or returns empty list if optional."""
        with patch.object(StealthConnectionManager, "is_configured", False):
            with self.assertRaises(RuntimeError):
                mgr_req = StealthConnectionManager(required=True)
                asyncio.run(mgr_req.start_pool(num_workers=2))

            mgr_opt = StealthConnectionManager(required=False)
            res = asyncio.run(mgr_opt.start_pool(num_workers=2))
            self.assertEqual(res, [])

    @patch("src.stealth_connection.asyncio.create_subprocess_exec")
    @patch("src.stealth_connection.get_free_port")
    def test_stop_pool_terminates_all_worker_processes(self, mock_port, mock_subproc):
        """Verify stop_pool calls terminate() and wait() on all active worker processes."""
        port_gen = itertools.count(56300)
        mock_port.side_effect = lambda *args, **kwargs: next(port_gen)

        proc1 = MagicMock()
        proc1.returncode = None
        proc1.wait = AsyncMock()
        proc2 = MagicMock()
        proc2.returncode = None
        proc2.wait = AsyncMock()
        mock_subproc.side_effect = [proc1, proc2]

        mgr = StealthConnectionManager(required=True)

        async def run():
            await mgr.start_pool(num_workers=2)
            await mgr.stop_pool()

        asyncio.run(run())

        proc1.terminate.assert_called_once()
        proc1.wait.assert_awaited_once()
        proc2.terminate.assert_called_once()
        proc2.wait.assert_awaited_once()
        self.assertEqual(len(mgr.endpoints), 0)

    @patch("src.stealth_connection.asyncio.create_subprocess_exec")
    @patch("src.stealth_connection.get_free_port")
    def test_start_pool_raises_when_all_forwarders_fail(self, mock_port, mock_subproc):
        """Verify start_pool raises RuntimeError if required=True and all forwarder launches fail."""
        mock_port.side_effect = itertools.count(56400)
        mock_subproc.side_effect = Exception("System resource exhaustion")

        mgr = StealthConnectionManager(required=True)

        async def run():
            await mgr.start_pool(num_workers=2, max_wait_seconds=0.01, wait_interval_seconds=0.0)

        with self.assertRaises(RuntimeError) as ctx:
            asyncio.run(run())
        self.assertIn("Mandatory NordVPN feeder pool failed to start", str(ctx.exception))

    @patch("src.stealth_connection.asyncio.create_subprocess_exec")
    @patch("src.stealth_connection.get_free_port")
    def test_start_pool_filters_prematurely_terminated_processes(self, mock_port, mock_subproc):
        """Verify start_pool filters out processes that die immediately upon launch."""
        mock_port.side_effect = itertools.count(56500)
        dead_proc = MagicMock()
        dead_proc.returncode = 1  # exited immediately
        dead_proc.wait = AsyncMock()

        live_proc = MagicMock()
        live_proc.returncode = None  # running
        live_proc.wait = AsyncMock()

        mock_subproc.side_effect = [dead_proc, live_proc]

        mgr = StealthConnectionManager(required=True)

        async def run():
            configs = await mgr.start_pool(num_workers=2, min_healthy=1, wait_for_full_pool=False, candidate_servers=[])
            return configs

        configs = asyncio.run(run())
        # Only 1 endpoint should remain
        self.assertEqual(len(configs), 1)
        self.assertEqual(len(mgr.endpoints), 1)
        self.assertEqual(mgr.endpoints[0].name, "feeder-sf-1")

        asyncio.run(mgr.stop_pool())

    @patch("src.stealth_connection.asyncio.create_subprocess_exec")
    @patch("src.stealth_connection.get_free_port")
    def test_stop_pool_escalates_to_kill_on_timeout(self, mock_port, mock_subproc):
        """Verify stop_pool escalates to proc.kill() if proc.wait() times out."""
        mock_port.side_effect = itertools.count(56600)
        proc = MagicMock()
        proc.returncode = None
        # First wait times out, second wait (after kill) succeeds
        proc.wait = AsyncMock(side_effect=[asyncio.TimeoutError, None])
        mock_subproc.return_value = proc

        mgr = StealthConnectionManager(required=True)

        async def run():
            await mgr.start_pool(num_workers=1)
            await mgr.stop_pool()

        asyncio.run(run())

        proc.terminate.assert_called_once()
        proc.kill.assert_called_once()
        self.assertEqual(len(mgr.endpoints), 0)

    @patch("src.stealth_connection.asyncio.create_subprocess_exec")
    @patch("src.stealth_connection.get_free_port")
    def test_start_single_proxy_redirects_phoenix(self, mock_port, mock_subproc):
        """Verify single-bridge start() redirects Phoenix to Los Angeles and sets proxy config."""
        mock_port.return_value = 56700
        proc = MagicMock()
        proc.returncode = None
        proc.wait = AsyncMock()
        mock_subproc.return_value = proc

        mgr = StealthConnectionManager(required=True)

        async def run():
            cfg = await mgr.start(remote_host="phoenix.us.socks.nordhold.net:1080")
            return cfg

        cfg = asyncio.run(run())
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg["server"], "http://127.0.0.1:56700")

        # Verify command line redirected phoenix to los-angeles
        args, kwargs = mock_subproc.call_args
        cmd_str = " ".join(args)
        self.assertNotIn("phoenix.us.socks.nordhold.net:1080", cmd_str)
        self.assertIn("los-angeles.us.socks.nordhold.net:1080", cmd_str)

        # Cleanup
        asyncio.run(mgr.stop())

    @patch("src.stealth_connection.asyncio.create_subprocess_exec")
    @patch("src.stealth_connection.get_free_port")
    def test_start_pool_cleans_up_existing_endpoints_on_reinit(self, mock_port, mock_subproc):
        """Verify calling start_pool() twice cleans up previous worker processes."""
        mock_port.side_effect = itertools.count(56800)
        proc1 = MagicMock()
        proc1.returncode = None
        proc1.wait = AsyncMock()
        proc2 = MagicMock()
        proc2.returncode = None
        proc2.wait = AsyncMock()
        mock_subproc.side_effect = [proc1, proc2]

        mgr = StealthConnectionManager(required=True)

        async def run():
            await mgr.start_pool(num_workers=1)
            await mgr.start_pool(num_workers=1)
            await mgr.stop_pool()

        asyncio.run(run())
        proc1.terminate.assert_called()

    @patch("src.stealth_connection.asyncio.create_subprocess_exec")
    @patch("src.stealth_connection.get_free_port")
    def test_start_single_cleans_up_existing_proc_on_reinit(self, mock_port, mock_subproc):
        """Verify calling start() twice cleans up previous forwarder process."""
        mock_port.side_effect = itertools.count(56900)
        proc1 = MagicMock()
        proc1.returncode = None
        proc1.wait = AsyncMock()
        proc2 = MagicMock()
        proc2.returncode = None
        proc2.wait = AsyncMock()
        mock_subproc.side_effect = [proc1, proc2]

        mgr = StealthConnectionManager(required=True)

        async def run():
            await mgr.start()
            await mgr.start()
            await mgr.stop()

        asyncio.run(run())
        proc1.terminate.assert_called()

    def test_get_free_port_avoids_excluded(self):
        """Verify get_free_port respects excluded port set."""
        from src.stealth_connection import get_free_port
        p1 = get_free_port()
        p2 = get_free_port(exclude={p1})
        self.assertNotEqual(p1, p2)

    @patch("src.stealth_connection.asyncio.create_subprocess_exec")
    @patch("src.stealth_connection.get_free_port")
    @patch.object(StealthConnectionManager, "verify_socks5")
    def test_start_pool_dynamic_health_routing(self, mock_verify, mock_port, mock_subproc):
        """Verify start_pool replaces dead endpoints with verified healthy candidates."""
        port_gen = itertools.count(57000)
        mock_port.side_effect = lambda *args, **kwargs: next(port_gen)

        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.wait = AsyncMock()
        mock_subproc.return_value = mock_proc

        # Suppose los-angeles works, but san-francisco and dallas fail auth.
        # Candidate socks-us51 (SF) and socks-us74 (Dallas) work.
        def mock_verify_impl(host, *args, **kwargs):
            if "los-angeles.us.socks.nordhold.net" in host:
                return True
            elif "socks-us51.nordvpn.com" in host or "socks-us74.nordvpn.com" in host:
                return True
            return False

        mock_verify.side_effect = mock_verify_impl

        mgr = StealthConnectionManager(required=True)
        custom_targets = [
            ("feeder-la", "los-angeles.us.socks.nordhold.net:1080"),
            ("feeder-sf", "san-francisco.us.socks.nordhold.net:1080"),
            ("feeder-dal", "dallas.us.socks.nordhold.net:1080"),
        ]

        async def run():
            return await mgr.start_pool(num_workers=3, servers=custom_targets)

        configs = asyncio.run(run())
        self.assertEqual(len(configs), 3)

        remote_hosts = [ep.remote_host for ep in mgr.endpoints]
        # feeder-la kept healthy server
        self.assertEqual(remote_hosts[0], "los-angeles.us.socks.nordhold.net:1080")
        # feeder-sf routed to candidate
        self.assertIn("socks-us51.nordvpn.com:1080", remote_hosts)
        # feeder-dal routed to candidate
        self.assertIn("socks-us74.nordvpn.com:1080", remote_hosts)

        asyncio.run(mgr.stop_pool())

    @patch("src.stealth_connection.asyncio.create_subprocess_exec")
    @patch("src.stealth_connection.get_free_port")
    def test_start_pool_waits_and_resumes_when_servers_become_healthy(self, mock_port, mock_subproc):
        """Verify start_pool waits when servers are unhealthy and resumes immediately once min_healthy is reached."""
        mock_port.side_effect = itertools.count(57500)
        mock_proc = MagicMock(returncode=None)
        mock_proc.wait = AsyncMock()
        mock_subproc.return_value = mock_proc

        mgr = StealthConnectionManager(required=True)
        call_counts: Dict[str, int] = {}

        def mock_verify_transient(host, *args, **kwargs):
            call_counts[host] = call_counts.get(host, 0) + 1
            if "dallas" in host:
                # First attempt fails, second attempt succeeds
                return call_counts[host] > 1
            return True

        with patch.object(mgr, "verify_socks5", side_effect=mock_verify_transient):
            custom_targets = [
                ("feeder-la", "los-angeles.us.socks.nordhold.net:1080"),
                ("feeder-dal", "dallas.us.socks.nordhold.net:1080"),
            ]
            async def run():
                return await mgr.start_pool(
                    num_workers=2,
                    servers=custom_targets,
                    min_healthy=2,
                    wait_for_full_pool=True,
                    wait_interval_seconds=0.0,
                    test_target=None,
                    candidate_servers=[],
                )

            configs = asyncio.run(run())
            self.assertEqual(len(configs), 2)
            self.assertEqual(len(mgr.endpoints), 2)
            self.assertGreaterEqual(call_counts.get("dallas.us.socks.nordhold.net:1080", 0), 2)

            asyncio.run(mgr.stop_pool())

    @patch("src.stealth_connection.asyncio.create_subprocess_exec")
    @patch("src.stealth_connection.get_free_port")
    def test_start_pool_retains_healthy_candidates_across_wait_iterations(self, mock_port, mock_subproc):
        """Verify candidate servers validated in iteration 1 are retained in iteration 2 when waiting for more."""
        mock_port.side_effect = itertools.count(57600)
        mock_proc = MagicMock(returncode=None)
        mock_proc.wait = AsyncMock()
        mock_subproc.return_value = mock_proc

        mgr = StealthConnectionManager(required=True)
        call_counts: Dict[str, int] = {}

        def mock_verify(host, *args, **kwargs):
            call_counts[host] = call_counts.get(host, 0) + 1
            if "cand2" in host:
                return call_counts[host] > 1
            return True

        with patch.object(mgr, "verify_socks5", side_effect=mock_verify):
            custom_targets = [
                ("feeder-hub1", "hub1.nordhold.net:1080"),
            ]
            candidates = ["cand1.nordhold.net:1080", "cand2.nordhold.net:1080"]
            async def run():
                return await mgr.start_pool(
                    num_workers=3,
                    servers=custom_targets,
                    min_healthy=3,
                    wait_for_full_pool=True,
                    wait_interval_seconds=0.0,
                    test_target=None,
                    candidate_servers=candidates,
                )

            configs = asyncio.run(run())
            self.assertEqual(len(configs), 3)
            self.assertEqual(len(mgr.endpoints), 3)
            self.assertGreaterEqual(call_counts.get("cand2.nordhold.net:1080", 0), 2)
            remote_hosts = {ep.remote_host for ep in mgr.endpoints}
            self.assertEqual(remote_hosts, {"hub1.nordhold.net:1080", "cand1.nordhold.net:1080", "cand2.nordhold.net:1080"})

            asyncio.run(mgr.stop_pool())

    @patch("src.stealth_connection.asyncio.create_subprocess_exec")
    @patch("src.stealth_connection.get_free_port")
    def test_start_pool_max_wait_seconds_timeout(self, mock_port, mock_subproc):
        """Verify start_pool respects max_wait_seconds and times out when servers remain unhealthy."""
        mock_port.side_effect = itertools.count(57700)
        mock_proc = MagicMock(returncode=None)
        mock_proc.wait = AsyncMock()
        mock_subproc.return_value = mock_proc

        mgr = StealthConnectionManager(required=False)

        with patch.object(mgr, "verify_socks5", return_value=False):
            custom_targets = [
                ("feeder-1", "dead1.nordhold.net:1080"),
                ("feeder-2", "dead2.nordhold.net:1080"),
            ]
            async def run():
                return await mgr.start_pool(
                    num_workers=2,
                    servers=custom_targets,
                    min_healthy=2,
                    wait_for_full_pool=True,
                    wait_interval_seconds=0.0,
                    max_wait_seconds=0.01,
                    test_target=None,
                    candidate_servers=[],
                )

            configs = asyncio.run(run())
            self.assertEqual(len(configs), 0)
            asyncio.run(mgr.stop_pool())

    @patch("src.stealth_connection.asyncio.create_subprocess_exec")
    @patch("src.stealth_connection.get_free_port")
    def test_start_pool_env_vars_max_wait_and_interval(self, mock_port, mock_subproc):
        """Verify STEALTH_MAX_WAIT_SECONDS and STEALTH_WAIT_INTERVAL are read from environment."""
        mock_port.side_effect = itertools.count(57800)
        mock_proc = MagicMock(returncode=None)
        mock_proc.wait = AsyncMock()
        mock_subproc.return_value = mock_proc

        mgr = StealthConnectionManager(required=False)

        with patch.dict("os.environ", {
            "STEALTH_MAX_WAIT_SECONDS": "0.01",
            "STEALTH_WAIT_INTERVAL": "0.0",
        }), patch.object(mgr, "verify_socks5", return_value=False):
            async def run():
                return await mgr.start_pool(
                    num_workers=2,
                    servers=[("f1", "dead1:1080"), ("f2", "dead2:1080")],
                    min_healthy=2,
                    candidate_servers=[],
                    test_target=None,
                )
            configs = asyncio.run(run())
            self.assertEqual(len(configs), 0)
            asyncio.run(mgr.stop_pool())


class TestStealthConnectionVerifySocks5(unittest.TestCase):
    """Unit tests for verify_socks5 RFC 1928/1929 socket handshake and authentication."""

    def setUp(self):
        self.patcher_env = patch.dict("os.environ", {
            "NORDVPN_USER": "test_user",
            "NORDVPN_PASS": "test_pass",
        })
        self.patcher_env.start()

    def tearDown(self):
        self.patcher_env.stop()

    @patch("socket.create_connection")
    def test_verify_socks5_success(self, mock_create_conn):
        """Verify verify_socks5 returns True when server accepts greeting and RFC 1929 auth."""
        mock_sock = MagicMock()
        mock_sock.recv.side_effect = [b"\x05\x02", b"\x01\x00"]
        mock_create_conn.return_value.__enter__.return_value = mock_sock

        ok = StealthConnectionManager.verify_socks5("socks-us61.nordvpn.com:1080", username="user", password="pwd")
        self.assertTrue(ok)
        mock_create_conn.assert_called_once_with(("socks-us61.nordvpn.com", 1080), timeout=1.5)
        mock_sock.sendall.assert_any_call(b"\x05\x01\x02")

    @patch("socket.create_connection")
    def test_verify_socks5_detailed_success(self, mock_create_conn):
        """Verify verify_socks5_detailed returns category 'OK' on success."""
        mock_sock = MagicMock()
        mock_sock.recv.side_effect = [b"\x05\x02", b"\x01\x00"]
        mock_create_conn.return_value.__enter__.return_value = mock_sock

        ok, msg, cat = StealthConnectionManager.verify_socks5_detailed("socks-us61.nordvpn.com:1080", username="u", password="p")
        self.assertTrue(ok)
        self.assertEqual(cat, "OK")
        self.assertIn("RFC 1929", msg)

    @patch("socket.create_connection")
    def test_verify_socks5_detailed_dns_fail(self, mock_create_conn):
        """Verify verify_socks5_detailed returns category 'DNS_FAIL' on socket.gaierror."""
        import socket as sock_mod
        mock_create_conn.side_effect = sock_mod.gaierror(8, "nodename nor servname provided, or not known")

        ok, msg, cat = StealthConnectionManager.verify_socks5_detailed("dead-host.nordvpn.com:1080", username="u", password="p")
        self.assertFalse(ok)
        self.assertEqual(cat, "DNS_FAIL")
        self.assertIn("DNS resolution failed", msg)

    @patch("socket.create_connection")
    def test_verify_socks5_detailed_timeout(self, mock_create_conn):
        """Verify verify_socks5_detailed returns category 'TIMEOUT' on socket timeout."""
        mock_create_conn.side_effect = TimeoutError("Connection timed out")

        ok, msg, cat = StealthConnectionManager.verify_socks5_detailed("slow-host.nordvpn.com:1080", username="u", password="p")
        self.assertFalse(ok)
        self.assertEqual(cat, "TIMEOUT")
        self.assertIn("timed out", msg.lower())

    @patch("socket.create_connection")
    def test_verify_socks5_detailed_conn_refused(self, mock_create_conn):
        """Verify verify_socks5_detailed returns category 'CONN_REFUSED' on ConnectionRefusedError."""
        mock_create_conn.side_effect = ConnectionRefusedError("Connection refused")

        ok, msg, cat = StealthConnectionManager.verify_socks5_detailed("refused-host.nordvpn.com:1080", username="u", password="p")
        self.assertFalse(ok)
        self.assertEqual(cat, "CONN_REFUSED")
        self.assertIn("Connection refused", msg)

    @patch("socket.create_connection")
    def test_verify_socks5_detailed_auth_fail(self, mock_create_conn):
        """Verify verify_socks5_detailed returns category 'AUTH_FAIL' when auth packet rejected."""
        mock_sock = MagicMock()
        mock_sock.recv.side_effect = [b"\x05\x02", b"\x01\x01"]
        mock_create_conn.return_value.__enter__.return_value = mock_sock

        ok, msg, cat = StealthConnectionManager.verify_socks5_detailed("auth-fail.nordvpn.com:1080", username="u", password="p")
        self.assertFalse(ok)
        self.assertEqual(cat, "AUTH_FAIL")
        self.assertIn("auth rejected", msg.lower())

    @patch("socket.create_connection")
    def test_verify_socks5_detailed_auth_rejected(self, mock_create_conn):
        """Verify verify_socks5_detailed returns category 'AUTH_REJECTED' when greeting rejected."""
        mock_sock = MagicMock()
        mock_sock.recv.side_effect = [b"\x05\xff"]
        mock_create_conn.return_value.__enter__.return_value = mock_sock

        ok, msg, cat = StealthConnectionManager.verify_socks5_detailed("auth-rej.nordvpn.com:1080", username="u", password="p")
        self.assertFalse(ok)
        self.assertEqual(cat, "AUTH_REJECTED")

    @patch("socket.create_connection")
    def test_verify_socks5_detailed_auth_eof(self, mock_create_conn):
        """Verify verify_socks5_detailed returns category 'AUTH_EOF' on premature socket close during auth."""
        mock_sock = MagicMock()
        mock_sock.recv.side_effect = [b"\x05\x02", b""]  # greeting ok, auth packet closes immediately
        mock_create_conn.return_value.__enter__.return_value = mock_sock

        ok, msg, cat = StealthConnectionManager.verify_socks5_detailed("auth-eof.nordvpn.com:1080", username="u", password="p")
        self.assertFalse(ok)
        self.assertEqual(cat, "AUTH_EOF")
        self.assertIn("Premature EOF", msg)

    @patch("socket.create_connection")
    def test_verify_socks5_detailed_protocol_err(self, mock_create_conn):
        """Verify verify_socks5_detailed returns category 'PROTOCOL_ERR' on invalid protocol or versions."""
        mock_sock = MagicMock()
        mock_sock.recv.side_effect = [b"\x04\x00"]  # SOCKS4 response instead of SOCKS5
        mock_create_conn.return_value.__enter__.return_value = mock_sock

        ok, msg, cat = StealthConnectionManager.verify_socks5_detailed("proto-err.nordvpn.com:1080", username="u", password="p")
        self.assertFalse(ok)
        self.assertEqual(cat, "PROTOCOL_ERR")
        self.assertIn("Not a SOCKS5 server", msg)

    @patch("socket.create_connection")
    def test_verify_socks5_auth_failed(self, mock_create_conn):
        """Verify verify_socks5 returns False when server returns 0x01 0x01 (Auth Failed)."""
        mock_sock = MagicMock()
        mock_sock.recv.side_effect = [b"\x05\x02", b"\x01\x01"]
        mock_create_conn.return_value.__enter__.return_value = mock_sock

        ok = StealthConnectionManager.verify_socks5("socks-us71.nordvpn.com:1080", username="user", password="pwd")
        self.assertFalse(ok)

    @patch("socket.create_connection")
    def test_verify_socks5_greeting_failed(self, mock_create_conn):
        """Verify verify_socks5 returns False when greeting fails (no acceptable methods)."""
        mock_sock = MagicMock()
        mock_sock.recv.side_effect = [b"\x05\xff"]
        mock_create_conn.return_value.__enter__.return_value = mock_sock

        ok = StealthConnectionManager.verify_socks5("unresponsive.server:1080", username="user", password="pwd")
        self.assertFalse(ok)

    @patch("socket.create_connection")
    def test_verify_socks5_timeout_or_error(self, mock_create_conn):
        """Verify verify_socks5 handles socket timeout or network errors gracefully."""
        mock_create_conn.side_effect = TimeoutError("Connection timed out")

        ok = StealthConnectionManager.verify_socks5("dead.server:1080", username="user", password="pwd")
        self.assertFalse(ok)

    def test_verify_socks5_missing_credentials(self):
        """Verify verify_socks5 returns False immediately when username or password missing."""
        with patch.dict("os.environ", {"NORDVPN_USER": "", "NORDVPN_PASS": ""}):
            ok = StealthConnectionManager.verify_socks5("socks-us61.nordvpn.com:1080", username="", password="")
            self.assertFalse(ok)

    @patch("socket.create_connection")
    def test_verify_socks5_uri_scheme_userinfo_and_hash_stripping(self, mock_create_conn):
        """Verify verify_socks5 cleanly parses URI schemes, userinfo, ports, and hash fragments."""
        mock_sock = MagicMock()
        mock_sock.recv.side_effect = [b"\x05\x02", b"\x01\x00"]
        mock_create_conn.return_value.__enter__.return_value = mock_sock

        complex_uri = "socks5://custom_user:custom_pass@socks-us61.nordvpn.com:1080#endpoint_tag"
        ok = StealthConnectionManager.verify_socks5(complex_uri, username="test_user", password="test_pass")
        self.assertTrue(ok)
        mock_create_conn.assert_called_once_with(("socks-us61.nordvpn.com", 1080), timeout=1.5)


class TestUnhealthyInvestigationReport(unittest.TestCase):
    """Unit tests for format_unhealthy_investigation_report diagnostic formatting."""

    def test_format_report_displays_healthy_and_unhealthy_breakdown(self):
        records = {
            "good-host:1080": ServerHealthRecord(
                host="good-host:1080",
                name="feeder-la-1",
                city="Los Angeles, CA",
                is_healthy=True,
                stage="SOCKS5_AUTH",
                category="OK",
                error_detail="SOCKS5 verified",
            ),
            "bad-host:1080": ServerHealthRecord(
                host="bad-host:1080",
                name="feeder-dal-1",
                city="Dallas, TX",
                is_healthy=False,
                stage="SOCKS5_AUTH",
                category="TIMEOUT",
                error_detail="TCP connection timed out after 1.5s",
                consecutive_failures=2,
            ),
            "auth-host:1080": ServerHealthRecord(
                host="auth-host:1080",
                name="feeder-sf-2",
                city="San Francisco, CA",
                is_healthy=False,
                stage="SOCKS5_AUTH",
                category="AUTH_FAIL",
                error_detail="SOCKS5 auth rejected (status 0x01)",
                consecutive_failures=1,
            ),
        }

        report = format_unhealthy_investigation_report(
            records=records,
            target_count=3,
            elapsed_seconds=65.0,
            next_check_seconds=60.0,
        )

        self.assertIn("WAITING FOR HEALTHY STEALTH SERVERS (1/3 Healthy)", report)
        self.assertIn("feeder-la-1", report)
        self.assertIn("feeder-dal-1", report)
        self.assertIn("[TIMEOUT]", report)
        self.assertIn("[AUTH_FAIL]", report)
        self.assertIn("NordVPN limits accounts to 10 active concurrent", report)
        self.assertIn("Elapsed: 1m 05s", report)


if __name__ == "__main__":
    unittest.main()
