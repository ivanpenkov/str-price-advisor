"""
Unit tests for StealthConnectionManager:
Parallel VPN pooling, RFC 1928/1929 authentication, Google pre-flight connectivity probing,
dynamic candidate hot-swapping, Phoenix server exclusion, and worker queue leasing.
"""

import asyncio
import io
import json
import os
import signal
import unittest
import urllib.error
from unittest.mock import patch, MagicMock, AsyncMock

from src.stealth_connection import (
    StealthConnectionManager,
    StealthEndpoint,
    load_env_variables,
    get_free_port,
    DEFAULT_STEALTH_HUBS,
    STATIC_CANDIDATE_STEALTH_SERVERS,
    CANDIDATE_STEALTH_SERVERS,
    FALLBACK_STEALTH_SERVER,
    fetch_nordvpn_candidate_servers,
)


class TestStealthConnection(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        os.environ["STEALTH_STARTUP_DELAY"] = "0.0"
        os.environ["STEALTH_WAIT_INTERVAL"] = "0.001"
        os.environ["STEALTH_MAX_WAIT_SECONDS"] = "0.05"
        self.mgr = StealthConnectionManager(required=True, max_workers=4)
        self.mgr.startup_delay = 0.0
        self.patcher_cands = patch(
            "src.stealth_connection.fetch_nordvpn_candidate_servers",
            return_value=list(STATIC_CANDIDATE_STEALTH_SERVERS),
        )
        self.patcher_cands.start()

    async def asyncTearDown(self):
        self.patcher_cands.stop()
        os.environ.pop("STEALTH_STARTUP_DELAY", None)
        os.environ.pop("STEALTH_WAIT_INTERVAL", None)
        os.environ.pop("STEALTH_MAX_WAIT_SECONDS", None)
        await self.mgr.stop_pool()

    def test_load_env_variables(self):
        """Verify load_env_variables loads variables without clobbering existing env."""
        with patch.dict(os.environ, {"EXISTING_KEY": "original"}, clear=True):
            with patch("pathlib.Path.exists", return_value=True), \
                 patch("pathlib.Path.read_text", return_value="EXISTING_KEY=new_val\nNEW_KEY=hello_world\n# Comment\nINVALID_LINE"):
                load_env_variables()
                self.assertEqual(os.environ.get("EXISTING_KEY"), "original")
                self.assertEqual(os.environ.get("NEW_KEY"), "hello_world")

    def test_get_free_port_avoids_excluded(self):
        """Verify get_free_port returns valid ports and avoids excluded ports."""
        port1 = get_free_port()
        self.assertIsInstance(port1, int)
        self.assertGreater(port1, 1024)

        port2 = get_free_port(exclude={port1})
        self.assertNotEqual(port1, port2)

    def test_verify_socks5_success(self):
        """Verify RFC 1928/1929 SOCKS5 authentication handshake succeeds on valid responses."""
        mock_socket = MagicMock()
        mock_socket.__enter__.return_value = mock_socket
        # Greeting response: \x05\x02 (SOCKS5, Username/Password auth)
        # Auth response: \x01\x00 (Version 1, Success)
        mock_socket.recv.side_effect = [b"\x05\x02", b"\x01\x00"]

        with patch("socket.create_connection", return_value=mock_socket):
            ok = StealthConnectionManager.verify_socks5(
                "los-angeles.us.socks.nordhold.net:1080",
                username="test_user",
                password="test_pass",
                timeout=1.0,
            )
            self.assertTrue(ok)
            # Verify greeting packet sent (\x05\x01\x02)
            self.assertEqual(mock_socket.sendall.call_args_list[0][0][0], b"\x05\x01\x02")
            # Verify auth packet starts with \x01 (auth sub-negotiation version)
            auth_packet = mock_socket.sendall.call_args_list[1][0][0]
            self.assertEqual(auth_packet[0], 0x01)
            self.assertEqual(auth_packet[1], len("test_user"))

    def test_verify_socks5_rejection(self):
        """Verify verify_socks5 returns False when auth or greeting fails."""
        mock_socket = MagicMock()
        mock_socket.__enter__.return_value = mock_socket
        # Rejection auth response: \x01\x01 (Version 1, Auth Failed)
        mock_socket.recv.side_effect = [b"\x05\x02", b"\x01\x01"]

        with patch("socket.create_connection", return_value=mock_socket):
            ok = StealthConnectionManager.verify_socks5(
                "los-angeles.us.socks.nordhold.net:1080",
                username="test_user",
                password="wrong_password",
            )
            self.assertFalse(ok)

    def test_verify_socks5_host_sanitization(self):
        """Verify URL schemes, credentials, ports, and fragments are cleanly stripped from host strings."""
        mock_socket = MagicMock()
        mock_socket.__enter__.return_value = mock_socket
        mock_socket.recv.side_effect = [b"\x05\x02", b"\x01\x00"]

        with patch("socket.create_connection", return_value=mock_socket) as mock_conn:
            ok = StealthConnectionManager.verify_socks5(
                "socks5://user:pass@chicago.us.socks.nordhold.net:1080#extra_tag",
                username="test_user",
                password="test_password",
            )
            self.assertTrue(ok)
            mock_conn.assert_called_once_with(("chicago.us.socks.nordhold.net", 1080), timeout=1.5)

    async def test_test_endpoint_connectivity_success(self):
        """Verify test_endpoint_connectivity returns True and HTTP 200 on healthy probe."""
        mock_resp = MagicMock()
        mock_resp.getcode.return_value = 200
        mock_opener = MagicMock()
        mock_opener.open.return_value.__enter__.return_value = mock_resp

        with patch("urllib.request.build_opener", return_value=mock_opener):
            ok, lat, status = await StealthConnectionManager.test_endpoint_connectivity(
                port=56001, target_url="https://www.google.com"
            )
            self.assertTrue(ok)
            self.assertGreater(lat, 0.0)
            self.assertEqual(status, "HTTP 200")

    async def test_test_endpoint_connectivity_timeout(self):
        """Verify test_endpoint_connectivity catches timeout and returns False."""
        mock_opener = MagicMock()
        mock_opener.open.side_effect = TimeoutError("The read operation timed out")

        with patch("urllib.request.build_opener", return_value=mock_opener):
            ok, lat, status = await StealthConnectionManager.test_endpoint_connectivity(
                port=56001, target_url="https://www.google.com"
            )
            self.assertFalse(ok)
            self.assertEqual(status, "Timeout")

    async def test_phoenix_exclusion_in_pool(self):
        """Verify Phoenix servers are strictly excluded and remapped to out-of-state feeder hubs."""
        with patch.dict(os.environ, {"NORDVPN_USER": "u", "NORDVPN_PASS": "p"}):
            with patch.object(self.mgr, "verify_socks5", return_value=True):
                mock_proc = MagicMock()
                mock_proc.returncode = None
                with patch.object(self.mgr, "_launch_forwarder", AsyncMock(side_effect=lambda name, city, remote_host, user, pwd, ports: StealthEndpoint(
                    name=name, remote_host=remote_host, local_port=56001, proc=mock_proc, status="ONLINE"
                ))):
                    endpoints = await self.mgr.start_pool(
                        num_workers=2,
                        servers=[
                            ("feeder-phx", "Phoenix, AZ", "phoenix.us.socks.nordhold.net:1080"),
                            ("feeder-sf", "San Francisco, CA", "san-francisco.us.socks.nordhold.net:1080"),
                        ],
                        test_target=None,
                    )
                    self.assertEqual(len(endpoints), 2)
                    for ep in self.mgr.endpoints:
                        self.assertNotIn("phoenix", ep.remote_host.lower(), f"Phoenix server allowed in pool: {ep.remote_host}")

    async def test_start_pool_hot_swap_failing_endpoint(self):
        """Verify dynamic hot-swap swaps a failing endpoint with a healthy candidate from the fleet."""
        with patch.dict(os.environ, {"NORDVPN_USER": "u", "NORDVPN_PASS": "p"}):
            with patch.object(self.mgr, "verify_socks5", return_value=True):
                mock_proc_bad = MagicMock()
                mock_proc_bad.returncode = None
                mock_proc_good = MagicMock()
                mock_proc_good.returncode = None

                bad_ep = StealthEndpoint(
                    name="feeder-la", remote_host="los-angeles.us.socks.nordhold.net:1080", local_port=56001, proc=mock_proc_bad
                )
                swap_ep = StealthEndpoint(
                    name="feeder-la", remote_host="socks-us60.nordvpn.com:1080", local_port=56002, proc=mock_proc_good
                )

                call_count = 0
                async def mock_launch(name, city, remote_host, user, pwd, ports):
                    nonlocal call_count
                    call_count += 1
                    if call_count == 1:
                        return bad_ep
                    return swap_ep

                with patch.object(self.mgr, "_launch_forwarder", side_effect=mock_launch):
                    # First probe fails (HTTP 503), hot-swapped candidate succeeds (HTTP 200)
                    with patch.object(
                        self.mgr,
                        "test_endpoint_connectivity",
                        AsyncMock(side_effect=[(False, 150.0, "HTTP 503"), (True, 65.0, "HTTP 200")]),
                    ):
                        configs = await self.mgr.start_pool(
                            num_workers=1,
                            servers=[("feeder-la", "Los Angeles, CA", "los-angeles.us.socks.nordhold.net:1080")],
                            test_target="https://www.google.com",
                        )
                        self.assertEqual(len(configs), 1)
                        self.assertEqual(self.mgr.endpoints[0].status, "ONLINE")
                        self.assertEqual(self.mgr.endpoints[0].remote_host, "socks-us60.nordvpn.com:1080")
                        mock_proc_bad.terminate.assert_called()

    async def test_worker_queue_acquire_and_lease(self):
        """Verify acquire_worker, release_worker, and lease_worker context manager cycle properly."""
        ep1 = StealthEndpoint(name="ep1", remote_host="host1", local_port=56001, status="ONLINE")
        ep2 = StealthEndpoint(name="ep2", remote_host="host2", local_port=56002, status="ONLINE")

        self.mgr.endpoints = [ep1, ep2]
        self.mgr._worker_queue = asyncio.Queue()
        self.mgr._worker_queue.put_nowait(ep1)
        self.mgr._worker_queue.put_nowait(ep2)

        # Lease worker 1
        async with self.mgr.lease_worker() as w1:
            self.assertEqual(w1.name, "ep1")
            self.assertEqual(self.mgr._worker_queue.qsize(), 1)
            # Lease worker 2 concurrently
            async with self.mgr.lease_worker() as w2:
                self.assertEqual(w2.name, "ep2")
                self.assertEqual(self.mgr._worker_queue.qsize(), 0)

        # After both exit context manager, both should be back in queue
        self.assertEqual(self.mgr._worker_queue.qsize(), 2)

    async def test_acquire_worker_revives_dead_subprocess(self):
        """Verify acquire_worker detects exited subprocess and hot-revives it before returning."""
        dead_proc = MagicMock()
        dead_proc.returncode = 1
        ep_dead = StealthEndpoint(
            name="ep-dead", city="Dallas", remote_host="host1", local_port=56001, proc=dead_proc, status="ONLINE"
        )

        live_proc = MagicMock()
        live_proc.returncode = None
        ep_revived = StealthEndpoint(
            name="ep-dead", city="Dallas", remote_host="host1", local_port=56001, proc=live_proc, status="ONLINE"
        )

        self.mgr.endpoints = [ep_dead]
        self.mgr.startup_delay = 0.0
        self.mgr._worker_queue = asyncio.Queue()
        self.mgr._worker_queue.put_nowait(ep_dead)

        with patch.object(self.mgr, "_launch_forwarder", AsyncMock(return_value=ep_revived)) as mock_launch:
            worker = await self.mgr.acquire_worker()
            mock_launch.assert_awaited_once()
            self.assertIsNone(worker.proc.returncode)
            self.assertEqual(self.mgr.endpoints[0], ep_revived)

    async def test_min_healthy_threshold_enforcement(self):
        """Verify RuntimeError is raised when active forwarders fail to satisfy min_healthy."""
        with patch.dict(os.environ, {"NORDVPN_USER": "u", "NORDVPN_PASS": "p"}):
            with patch.object(self.mgr, "verify_socks5", return_value=True):
                dead_proc = MagicMock()
                dead_proc.returncode = 1  # exited prematurely

                with patch.object(self.mgr, "_launch_forwarder", AsyncMock(return_value=StealthEndpoint(
                    name="feeder-dead", remote_host="host:1080", local_port=56001, proc=dead_proc
                ))):
                    with self.assertRaises(RuntimeError) as ctx:
                        await self.mgr.start_pool(
                            num_workers=1,
                            min_healthy=1,
                            test_target=None,
                            max_wait_seconds=0.01,
                            wait_interval_seconds=0.0,
                        )
                    self.assertIn("Mandatory NordVPN feeder pool failed to start", str(ctx.exception))

    async def test_min_healthy_rejects_duplicate_hosts(self):
        """Verify RuntimeError is raised when active endpoints share duplicate remote hosts below min_healthy."""
        with patch.dict(os.environ, {"NORDVPN_USER": "u", "NORDVPN_PASS": "p"}):
            with patch.object(self.mgr, "verify_socks5", return_value=True):
                mock_proc1 = MagicMock(returncode=None)
                mock_proc2 = MagicMock(returncode=None)
                # Return 2 endpoints that share the EXACT same remote_host
                ep1 = StealthEndpoint(name="ep1", remote_host="duplicate-host:1080", local_port=56001, proc=mock_proc1)
                ep2 = StealthEndpoint(name="ep2", remote_host="duplicate-host:1080", local_port=56002, proc=mock_proc2)
                launched = [ep1, ep2]
                with patch.object(self.mgr, "_launch_forwarder", AsyncMock(side_effect=lambda *args, **kwargs: launched.pop(0))):
                    with self.assertRaises(RuntimeError) as ctx:
                        # 2 workers requested, min_healthy=2, but only 1 unique host
                        await self.mgr.start_pool(
                            num_workers=2,
                            min_healthy=2,
                            servers=[("ep1", "duplicate-host:1080"), ("ep2", "duplicate-host:1080")],
                            wait_for_full_pool=False,
                            test_target=None,
                        )
                    self.assertIn("distinct healthy remote servers verified", str(ctx.exception))

    async def test_target_hubs_expansion_selects_unassigned_candidates(self):
        """Verify target_hubs expansion pulls unassigned candidates rather than repeating existing hosts."""
        with patch.dict(os.environ, {"NORDVPN_USER": "u", "NORDVPN_PASS": "p"}):
            with patch.object(self.mgr, "verify_socks5", return_value=True):
                mock_proc = MagicMock(returncode=None)
                assigned_hosts = []

                async def mock_launch(name, city, remote_host, *args, **kwargs):
                    assigned_hosts.append(remote_host)
                    return StealthEndpoint(name=name, remote_host=remote_host, local_port=56000 + len(assigned_hosts), proc=mock_proc)

                with patch.object(self.mgr, "_launch_forwarder", AsyncMock(side_effect=mock_launch)):
                    # Request 12 workers (more than DEFAULT_STEALTH_HUBS which has 10)
                    configs = await self.mgr.start_pool(num_workers=12, min_healthy=12, test_target=None)
                    self.assertEqual(len(configs), 12)
                    self.assertEqual(len(set(assigned_hosts)), 12)

    async def test_stop_pool_terminates_all_processes(self):
        """Verify stop_pool terminates all active endpoints and empties the pool."""
        mock_p1 = MagicMock()
        mock_p1.wait = AsyncMock()
        mock_p2 = MagicMock()
        mock_p2.wait = AsyncMock()

        self.mgr.endpoints = [
            StealthEndpoint(name="ep1", remote_host="h1", local_port=56001, proc=mock_p1),
            StealthEndpoint(name="ep2", remote_host="h2", local_port=56002, proc=mock_p2),
        ]
        self.mgr._worker_queue = asyncio.Queue()

        await self.mgr.stop_pool()
        mock_p1.terminate.assert_called_once()
        mock_p2.terminate.assert_called_once()
        self.assertEqual(len(self.mgr.endpoints), 0)
        self.assertIsNone(self.mgr._worker_queue)

    async def test_stop_pool_terminates_single_worker_proc(self):
        """Verify stop_pool terminates and reaps self.proc if set."""
        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.wait = AsyncMock()
        self.mgr.proc = mock_proc
        await self.mgr.stop_pool()
        mock_proc.terminate.assert_called_once()
        self.assertIsNone(self.mgr.proc)

    async def test_failed_endpoints_pruned_from_pool_and_queue(self):
        """Verify endpoints failing connectivity check and hot-swap are pruned from endpoints and queue."""
        with patch.dict(os.environ, {"NORDVPN_USER": "u", "NORDVPN_PASS": "p"}):
            with patch.object(self.mgr, "verify_socks5", return_value=True):
                mock_proc1 = MagicMock()
                mock_proc1.returncode = None
                mock_proc1.wait = AsyncMock()

                mock_proc2 = MagicMock()
                mock_proc2.returncode = None
                mock_proc2.wait = AsyncMock()

                ep1 = StealthEndpoint(name="feeder-good", remote_host="host1:1080", local_port=56001, proc=mock_proc1)
                ep2 = StealthEndpoint(name="feeder-bad", remote_host="host2:1080", local_port=56002, proc=mock_proc2)

                launched = [ep1, ep2]
                with patch.object(self.mgr, "_launch_forwarder", AsyncMock(side_effect=lambda *args, **kwargs: launched.pop(0))):
                    async def fake_probe(port, **kwargs):
                        if port == 56001:
                            return True, 50.0, "HTTP 200"
                        return False, 1500.0, "Timeout"

                    with patch.object(self.mgr, "test_endpoint_connectivity", side_effect=fake_probe):
                        configs = await self.mgr.start_pool(
                            num_workers=2, min_healthy=1, test_target="https://www.google.com", candidate_servers=[]
                        )
                        self.assertEqual(len(configs), 1)
                        self.assertEqual(len(self.mgr.endpoints), 1)
                        self.assertEqual(self.mgr.endpoints[0].name, "feeder-good")
                        self.assertEqual(self.mgr._worker_queue.qsize(), 1)
                        mock_proc2.terminate.assert_called()

    def test_reap_orphaned_forwarders(self):
        """Verify reap_orphaned_forwarders identifies PPID==1 forwarders and sends SIGKILL."""
        fake_ps_output = (
            "  PID  PPID COMMAND\n"
            "  101     1 /usr/bin/python3 -m pproxy -l http://127.0.0.1:56001 -r socks5://los-angeles.us.socks.nordhold.net:1080#u:p\n"
            "  102    55 /usr/bin/python3 -m pproxy -l http://127.0.0.1:56002 -r socks5://san-francisco.us.socks.nordhold.net:1080#u:p\n"
            "  103     1 /usr/local/bin/node server.js\n"
        )
        with patch("subprocess.check_output", return_value=fake_ps_output), \
             patch("os.kill") as mock_kill:
            reaped = StealthConnectionManager.reap_orphaned_forwarders()
            self.assertEqual(reaped, 1)
            mock_kill.assert_called_once_with(101, signal.SIGKILL)

    def test_reap_orphaned_forwarders_pid1_guard(self):
        """Verify reap_orphaned_forwarders returns 0 without killing when running as container PID 1."""
        with patch("os.getpid", return_value=1), \
             patch("subprocess.check_output") as mock_ps:
            reaped = StealthConnectionManager.reap_orphaned_forwarders()
            self.assertEqual(reaped, 0)
            mock_ps.assert_not_called()


class TestCandidateServerDiscovery(unittest.TestCase):
    """Unit tests for dynamic NordVPN candidate proxy server discovery, Phoenix exclusion, and disjointness."""

    def test_static_candidates_strictly_disjoint_from_default_hubs(self):
        """Assert static candidates have zero overlap with DEFAULT_STEALTH_HUBS and contain no Phoenix nodes."""
        default_hosts = {h[2].strip().lower() for h in DEFAULT_STEALTH_HUBS}
        candidate_hosts = {s.strip().lower() for s in STATIC_CANDIDATE_STEALTH_SERVERS}

        # Zero set intersection
        self.assertTrue(
            default_hosts.isdisjoint(candidate_hosts),
            f"Static candidates overlap with default hubs: {default_hosts.intersection(candidate_hosts)}",
        )

        # No Phoenix host in static candidates
        for c in STATIC_CANDIDATE_STEALTH_SERVERS:
            self.assertNotIn("phoenix", c.lower(), f"Phoenix server found in STATIC_CANDIDATE_STEALTH_SERVERS: {c}")

        # No Phoenix host in default hubs
        for h in DEFAULT_STEALTH_HUBS:
            self.assertNotIn("phoenix", h[2].lower(), f"Phoenix server found in DEFAULT_STEALTH_HUBS: {h[2]}")

    def test_fetch_candidate_servers_filters_phoenix_and_default_hubs(self):
        """Verify API discovery strictly strips Phoenix locations and default hub duplicates."""
        mock_api_payload = [
            {
                "hostname": "socks-us80.nordvpn.com",
                "load": 25,
                "status": "online",
                "technologies": [{"id": 7, "name": "Socks 5", "pivot": {"status": "online"}}],
                "locations": [{"country": {"city": {"name": "Denver"}}}],
            },
            {
                "hostname": "socks-us55.nordvpn.com",
                "load": 10,
                "status": "online",
                "technologies": [{"id": 7, "name": "Socks 5", "pivot": {"status": "online"}}],
                "locations": [{"country": {"city": {"name": "Phoenix"}}}],
            },
            {
                "hostname": "phoenix-proxy.nordvpn.com",
                "load": 15,
                "status": "online",
                "technologies": [{"id": 7, "name": "Socks 5", "pivot": {"status": "online"}}],
                "locations": [{"country": {"city": {"name": "Salt Lake City"}}}],
            },
            {
                "hostname": "los-angeles.us.socks.nordhold.net",
                "load": 5,
                "status": "online",
                "technologies": [{"id": 7, "name": "Socks 5", "pivot": {"status": "online"}}],
                "locations": [{"country": {"city": {"name": "Los Angeles"}}}],
            },
        ]

        class MockResp:
            status = 200
            def read(self):
                return json.dumps(mock_api_payload).encode("utf-8")
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass

        with patch("urllib.request.urlopen", return_value=MockResp()):
            candidates = fetch_nordvpn_candidate_servers()

            # Valid candidate is included
            self.assertIn("socks-us80.nordvpn.com:1080", candidates)

            # Phoenix location node is excluded
            self.assertNotIn("socks-us55.nordvpn.com:1080", candidates)

            # Phoenix hostname node is excluded
            self.assertNotIn("phoenix-proxy.nordvpn.com:1080", candidates)

            # Default hub duplicate is excluded
            self.assertNotIn("los-angeles.us.socks.nordhold.net:1080", candidates)

    def test_fetch_candidate_servers_filters_maintenance_and_overloaded(self):
        """Verify API discovery rejects maintenance, offline SOCKS5 technology, and load > 70%, and sorts by load."""
        mock_api_payload = [
            {
                "hostname": "socks-us81.nordvpn.com",
                "load": 10,
                "status": "maintenance",
                "technologies": [{"id": 7, "name": "Socks 5", "pivot": {"status": "online"}}],
                "locations": [{"country": {"city": {"name": "Atlanta"}}}],
            },
            {
                "hostname": "socks-us82.nordvpn.com",
                "load": 12,
                "status": "online",
                "technologies": [{"id": 7, "name": "Socks 5", "pivot": {"status": "offline"}}],
                "locations": [{"country": {"city": {"name": "Dallas"}}}],
            },
            {
                "hostname": "socks-us83.nordvpn.com",
                "load": 85,  # Overloaded (> 70)
                "status": "online",
                "technologies": [{"id": 7, "name": "Socks 5", "pivot": {"status": "online"}}],
                "locations": [{"country": {"city": {"name": "Chicago"}}}],
            },
            {
                "hostname": "socks-us84.nordvpn.com",
                "load": 45,
                "status": "online",
                "technologies": [{"id": 7, "name": "Socks 5", "pivot": {"status": "online"}}],
                "locations": [{"country": {"city": {"name": "New York"}}}],
            },
            {
                "hostname": "socks-us85.nordvpn.com",
                "load": 15,
                "status": "online",
                "technologies": [{"id": 7, "name": "Socks 5", "pivot": {"status": "online"}}],
                "locations": [{"country": {"city": {"name": "Seattle"}}}],
            },
        ]

        class MockResp:
            status = 200
            def read(self):
                return json.dumps(mock_api_payload).encode("utf-8")
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass

        with patch("urllib.request.urlopen", return_value=MockResp()):
            candidates = fetch_nordvpn_candidate_servers()

            # Maintenance server excluded
            self.assertNotIn("socks-us81.nordvpn.com:1080", candidates)

            # Offline technology server excluded
            self.assertNotIn("socks-us82.nordvpn.com:1080", candidates)

            # Overloaded server excluded
            self.assertNotIn("socks-us83.nordvpn.com:1080", candidates)

            # Valid servers included
            self.assertIn("socks-us85.nordvpn.com:1080", candidates)
            self.assertIn("socks-us84.nordvpn.com:1080", candidates)

            # Sorted by load ascending: load 15 before load 45
            idx_85 = candidates.index("socks-us85.nordvpn.com:1080")
            idx_84 = candidates.index("socks-us84.nordvpn.com:1080")
            self.assertLess(idx_85, idx_84)

    def test_fetch_candidate_servers_api_failure_falls_back_to_static(self):
        """Verify API discovery falls back cleanly to STATIC_CANDIDATE_STEALTH_SERVERS on network failure."""
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Connection refused")):
            candidates = fetch_nordvpn_candidate_servers()
            self.assertEqual(len(candidates), len(STATIC_CANDIDATE_STEALTH_SERVERS))
            for s in STATIC_CANDIDATE_STEALTH_SERVERS:
                self.assertIn(s, candidates)

    def test_start_pool_populates_candidate_servers(self):
        """Verify start_pool queries and populates self.candidate_servers with disjoint nodes and prints banners."""
        mgr = StealthConnectionManager(required=True, max_workers=2)
        mgr.startup_delay = 0.0

        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.wait = AsyncMock()
        mock_ep = StealthEndpoint(name="f1", remote_host="host1:1080", local_port=56001, proc=mock_proc)

        with patch.object(mgr, "verify_socks5", return_value=True), \
             patch.object(mgr, "_launch_forwarder", AsyncMock(return_value=mock_ep)), \
             patch.object(mgr, "test_endpoint_connectivity", AsyncMock(return_value=(True, 45.0, "HTTP 200"))), \
             patch("src.stealth_connection.fetch_nordvpn_candidate_servers", return_value=["socks-us99.nordvpn.com:1080"]), \
             patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            configs = asyncio.run(mgr.start_pool(num_workers=1, test_target=None, verbose=True))
            self.assertIsNotNone(mgr.candidate_servers)
            self.assertIn("socks-us99.nordvpn.com:1080", mgr.candidate_servers)
            output = mock_stdout.getvalue()
            self.assertIn("PRIMARY FEEDER HUBS", output)
            self.assertIn("Populated 1 candidate backup servers", output)

    def test_fetch_candidate_servers_strictly_excludes_all_default_hubs_even_when_sliced(self):
        """Verify candidate discovery excludes all DEFAULT_STEALTH_HUBS even if default_hubs is sliced to fewer workers."""
        # Sliced to 2 workers (e.g. LA and SF)
        sliced_hubs = [DEFAULT_STEALTH_HUBS[0], DEFAULT_STEALTH_HUBS[1]]

        # API returns Chicago (DEFAULT_STEALTH_HUBS[3]) and a valid candidate
        mock_api_payload = [
            {
                "hostname": "chicago.us.socks.nordhold.net",
                "load": 15,
                "status": "online",
                "technologies": [{"id": 7, "name": "Socks 5", "pivot": {"status": "online"}}],
                "locations": [{"country": {"city": {"name": "Chicago"}}}],
            },
            {
                "hostname": "socks-us88.nordvpn.com",
                "load": 20,
                "status": "online",
                "technologies": [{"id": 7, "name": "Socks 5", "pivot": {"status": "online"}}],
                "locations": [{"country": {"city": {"name": "Miami"}}}],
            },
        ]

        class MockResp:
            status = 200
            def read(self):
                return json.dumps(mock_api_payload).encode("utf-8")
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass

        with patch("urllib.request.urlopen", return_value=MockResp()):
            candidates = fetch_nordvpn_candidate_servers(default_hubs=sliced_hubs)
            # Chicago must be excluded even though only LA and SF were passed in sliced_hubs
            self.assertNotIn("chicago.us.socks.nordhold.net:1080", candidates)
            self.assertIn("socks-us88.nordvpn.com:1080", candidates)

    def test_stealth_connection_start_pool_accepts_candidate_servers(self):
        """Verify StealthConnectionManager.start_pool accepts custom candidate_servers."""
        mgr = StealthConnectionManager(required=False)
        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.wait = AsyncMock()
        mock_ep = StealthEndpoint(name="f1", remote_host="host1:1080", local_port=56001, proc=mock_proc)

        with patch.object(mgr, "verify_socks5", return_value=True), \
             patch.object(mgr, "_launch_forwarder", AsyncMock(return_value=mock_ep)), \
             patch.object(mgr, "test_endpoint_connectivity", AsyncMock(return_value=(True, 45.0, "HTTP 200"))):
            configs = asyncio.run(mgr.start_pool(
                num_workers=1, test_target=None, candidate_servers=["custom-cand:1080"]
            ))
            self.assertEqual(mgr.candidate_servers, ["custom-cand:1080"])

    def test_start_pool_diagnostic_report_printed_on_wait(self):
        """Verify start_pool prints the investigation report when waiting for healthy servers."""
        mgr = StealthConnectionManager(required=False)
        mock_proc = MagicMock(returncode=None)
        mock_proc.wait = AsyncMock()

        def mock_launch(name, city, remote_host, *args, **kwargs):
            return StealthEndpoint(name=name, city=city, remote_host=remote_host, local_port=get_free_port(), proc=mock_proc)

        call_count = 0
        def fake_verify(host, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            if "bad" in host:
                return call_count > 2
            return True

        with patch.object(mgr, "verify_socks5", side_effect=fake_verify), \
             patch.object(mgr, "_launch_forwarder", side_effect=mock_launch), \
             patch.object(mgr, "test_endpoint_connectivity", AsyncMock(return_value=(True, 50.0, "HTTP 200"))), \
             patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            configs = asyncio.run(mgr.start_pool(
                num_workers=2,
                servers=[("good", "good:1080"), ("bad", "bad:1080")],
                min_healthy=2,
                wait_for_full_pool=True,
                wait_interval_seconds=0.0,
                test_target=None,
                candidate_servers=[],
            ))
            output = mock_stdout.getvalue()
            self.assertIn("WAITING FOR HEALTHY STEALTH SERVERS", output)
            self.assertIn("Reached 2/2 healthy servers! Resuming operation...", output)

    def test_start_pool_hot_swap_replenishes_candidates(self):
        """Verify dynamic hot-swap replenishes candidate fleet from NordVPN API if candidate queue runs out."""
        mgr = StealthConnectionManager(required=True)
        mgr.startup_delay = 0.0

        mock_proc1 = MagicMock(returncode=None)
        mock_proc1.wait = AsyncMock()
        bad_ep = StealthEndpoint(name="feeder-1", remote_host="host1:1080", local_port=56001, proc=mock_proc1)

        mock_proc2 = MagicMock(returncode=None)
        mock_proc2.wait = AsyncMock()
        good_ep = StealthEndpoint(name="feeder-1", remote_host="fresh-api-candidate:1080", local_port=56002, proc=mock_proc2)

        launch_calls = 0
        async def mock_launch(name, city, remote_host, *args, **kwargs):
            nonlocal launch_calls
            launch_calls += 1
            if launch_calls == 1:
                return bad_ep
            return good_ep

        probe_calls = 0
        async def mock_probe(port, **kwargs):
            nonlocal probe_calls
            probe_calls += 1
            if probe_calls == 1:
                return False, 500.0, "HTTP 503"
            return True, 45.0, "HTTP 200"

        with patch.object(mgr, "verify_socks5", return_value=True), \
             patch.object(mgr, "_launch_forwarder", side_effect=mock_launch), \
             patch.object(mgr, "test_endpoint_connectivity", side_effect=mock_probe), \
             patch("src.stealth_connection.fetch_nordvpn_candidate_servers", return_value=["fresh-api-candidate:1080"]):
            configs = asyncio.run(mgr.start_pool(
                num_workers=1,
                servers=[("feeder-1", "host1:1080")],
                min_healthy=1,
                test_target="https://www.google.com",
            ))
            self.assertEqual(len(configs), 1)
            self.assertEqual(mgr.endpoints[0].remote_host, "fresh-api-candidate:1080")
            self.assertEqual(mgr.endpoints[0].status, "ONLINE")

    def test_dynamic_quorum_defaults(self):
        """Verify dynamic quorum min_healthy calculation: max(3, num_workers - 2) for >=3, or num_workers for <3."""
        self.assertEqual(StealthConnectionManager.calculate_min_healthy(1), 1)
        self.assertEqual(StealthConnectionManager.calculate_min_healthy(2), 2)
        self.assertEqual(StealthConnectionManager.calculate_min_healthy(3), 3)
        self.assertEqual(StealthConnectionManager.calculate_min_healthy(4), 3)
        self.assertEqual(StealthConnectionManager.calculate_min_healthy(5), 3)
        self.assertEqual(StealthConnectionManager.calculate_min_healthy(8), 6)
        self.assertEqual(StealthConnectionManager.calculate_min_healthy(10), 8)

    def test_start_pool_quorum_success_returns_unique_endpoints_without_padding(self):
        """Verify start_pool returns only unique verified endpoints without duplicate round-robin padding."""
        mgr = StealthConnectionManager(required=True)
        mgr.startup_delay = 0.0

        mock_proc1 = MagicMock(returncode=None)
        mock_proc2 = MagicMock(returncode=None)
        mock_proc3 = MagicMock(returncode=None)

        ep1 = StealthEndpoint(name="f1", remote_host="h1:1080", local_port=56001, url="http://127.0.0.1:56001", proc=mock_proc1, status="ONLINE")
        ep2 = StealthEndpoint(name="f2", remote_host="h2:1080", local_port=56002, url="http://127.0.0.1:56002", proc=mock_proc2, status="ONLINE")
        ep3 = StealthEndpoint(name="f3", remote_host="h3:1080", local_port=56003, url="http://127.0.0.1:56003", proc=mock_proc3, status="ONLINE")

        launched = [ep1, ep2, ep3]

        async def run():
            with patch.dict(os.environ, {"NORDVPN_USER": "u", "NORDVPN_PASS": "p"}), \
                 patch.object(mgr, "verify_socks5", return_value=True), \
                 patch.object(mgr, "_launch_forwarder", AsyncMock(side_effect=lambda *args, **kwargs: launched.pop(0) if launched else (_ for _ in ()).throw(Exception("No more servers")))), \
                 patch("src.stealth_connection.fetch_nordvpn_candidate_servers", return_value=[]):
                # Request 4 workers, min_healthy defaults to 3 (quorum met with 3/4)
                configs = await mgr.start_pool(
                    num_workers=4,
                    servers=[("f1", "h1:1080"), ("f2", "h2:1080"), ("f3", "h3:1080"), ("f4", "h4:1080")],
                    candidate_servers=[],
                    test_target=None,
                )
                # Must return EXACTLY 3 unique endpoints, NO duplicate padding
                self.assertEqual(len(configs), 3)
                self.assertEqual(len(mgr.endpoints), 3)
                ports = [c["port"] for c in configs]
                self.assertEqual(len(ports), len(set(ports)), "Duplicate ports found in quorum return")
                hosts = [c["remote"] for c in configs]
                self.assertEqual(len(hosts), len(set(hosts)), "Duplicate hosts found in quorum return")
                await mgr.stop_pool()

        asyncio.run(run())

    def test_get_proxy_configs_returns_current_endpoints(self):
        """Verify get_proxy_configs returns formatted dicts of current active endpoints."""
        mgr = StealthConnectionManager(required=False)
        ep1 = StealthEndpoint(name="feeder-1", remote_host="h1:1080", local_port=56001, url="http://127.0.0.1:56001", status="ONLINE")
        ep1.index = 1
        ep1.latency_ms = 42.0
        mgr.endpoints = [ep1]

        configs = mgr.get_proxy_configs()
        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0]["name"], "feeder-1")
        self.assertEqual(configs[0]["server"], "http://127.0.0.1:56001")
        self.assertEqual(configs[0]["remote"], "h1:1080")
        self.assertEqual(configs[0]["port"], 56001)
        self.assertEqual(configs[0]["latency_ms"], 42.0)
        self.assertEqual(configs[0]["status"], "ONLINE")

    def test_start_pool_timeout_at_quorum_proceeds_with_warning(self):
        """Verify start_pool proceeds with warning when max_wait_seconds expires but quorum is satisfied."""
        mgr = StealthConnectionManager(required=True)
        mgr.startup_delay = 0.0

        mock_proc = MagicMock(returncode=None)
        good_ep = StealthEndpoint(name="f1", remote_host="h1:1080", local_port=56001, url="http://127.0.0.1:56001", proc=mock_proc, status="ONLINE")

        call_idx = 0
        async def mock_launch(*args, **kwargs):
            nonlocal call_idx
            call_idx += 1
            if call_idx == 1:
                return good_ep
            raise Exception("Secondary node failed")

        async def run():
            with patch.dict(os.environ, {"NORDVPN_USER": "u", "NORDVPN_PASS": "p"}), \
                 patch.object(mgr, "verify_socks5", return_value=True), \
                 patch.object(mgr, "_launch_forwarder", side_effect=mock_launch), \
                 patch.object(mgr, "test_endpoint_connectivity", AsyncMock(return_value=(True, 50.0, "HTTP 200"))), \
                 patch("src.stealth_connection.fetch_nordvpn_candidate_servers", return_value=[]):
                # Request 2 workers, min_healthy=1, wait_for_full_pool=True, timeout=0.01s
                configs = await mgr.start_pool(
                    num_workers=2,
                    servers=[("f1", "h1:1080"), ("f2", "h2:1080")],
                    min_healthy=1,
                    wait_for_full_pool=True,
                    max_wait_seconds=0.01,
                    wait_interval_seconds=0.0,
                    test_target="https://www.google.com",
                    candidate_servers=[],
                )
                # Quorum is 1, so 1 healthy server satisfies quorum on timeout
                self.assertEqual(len(configs), 1)
                self.assertEqual(configs[0]["remote"], "h1:1080")
                await mgr.stop_pool()

        asyncio.run(run())

    def test_start_pool_wait_for_full_pool_false_retries_until_quorum_met(self):
        """Verify start_pool with wait_for_full_pool=False retries when below quorum and exits as soon as quorum is reached."""
        mgr = StealthConnectionManager(required=True)
        mgr.startup_delay = 0.0

        mock_proc1 = MagicMock(returncode=None)
        mock_proc2 = MagicMock(returncode=None)
        ep1 = StealthEndpoint(name="f1", remote_host="h1:1080", local_port=56001, url="http://127.0.0.1:56001", proc=mock_proc1, status="ONLINE")
        ep2 = StealthEndpoint(name="f2", remote_host="h2:1080", local_port=56002, url="http://127.0.0.1:56002", proc=mock_proc2, status="ONLINE")

        call_idx = 0
        async def mock_launch(*args, **kwargs):
            nonlocal call_idx
            call_idx += 1
            if call_idx == 1:
                return ep1
            elif call_idx == 2:
                # First attempt for f2 fails
                raise Exception("Transient auth reject")
            elif call_idx >= 3:
                # Retry attempt for f2 succeeds
                return ep2

        async def run():
            with patch.dict(os.environ, {"NORDVPN_USER": "u", "NORDVPN_PASS": "p"}), \
                 patch.object(mgr, "verify_socks5", return_value=True), \
                 patch.object(mgr, "_launch_forwarder", side_effect=mock_launch), \
                 patch.object(mgr, "test_endpoint_connectivity", AsyncMock(return_value=(True, 50.0, "HTTP 200"))), \
                 patch("src.stealth_connection.fetch_nordvpn_candidate_servers", return_value=[]):
                # 3 workers requested, min_healthy=2, wait_for_full_pool=False
                configs = await mgr.start_pool(
                    num_workers=3,
                    servers=[("f1", "h1:1080"), ("f2", "h2:1080"), ("f3", "h3:1080")],
                    min_healthy=2,
                    wait_for_full_pool=False,
                    max_wait_seconds=5.0,
                    wait_interval_seconds=0.001,
                    test_target="https://www.google.com",
                    candidate_servers=[],
                )
                # Quorum was 2. Attempt 1 got 1 healthy server (did not break early).
                # Attempt 2 got 2nd healthy server, reaching quorum (2/3) and immediately returning.
                self.assertEqual(len(configs), 2)
                self.assertEqual(len(mgr.endpoints), 2)
                self.assertSetEqual({c["remote"] for c in configs}, {"h1:1080", "h2:1080"})
                await mgr.stop()

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
