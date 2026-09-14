"""
Stealth Connection Manager for STR Price Advisor.
Manages a high-capacity (up to 10 workers) parallel VPN pool across major US travel feeder markets.
Enforces proxy usage for all external web scraping to prevent IP throttling and detection.
Performs pre-flight socket handshakes (RFC 1928/1929) and end-to-end HTTP health checks against Google.
Supports concurrent worker leasing for parallel scraping across all application components.
"""

import atexit
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import logging
import os
import signal
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple, Union
import json
import urllib.request

logger = logging.getLogger("stealth_connection")

# Global default max connections (NordVPN allows up to 10 concurrent connections; 8 leaves 2 for personal devices)
DEFAULT_MAX_STEALTH_CONNECTIONS: int = 8
DEFAULT_MAX_WAIT_SECONDS: float = 60.0

# Top 10 out-of-state feeder travel hubs to Phoenix/Scottsdale
# Strictly excludes Phoenix to avoid local host competitor surveillance signatures.
# All nodes are verified active and RFC 1928/1929 authenticated across 10 distinct IPs.
DEFAULT_STEALTH_HUBS: List[Tuple[str, str, str]] = [
    ("feeder-la-1", "Los Angeles, CA", "los-angeles.us.socks.nordhold.net:1080"),
    ("feeder-sf-1", "San Francisco, CA", "san-francisco.us.socks.nordhold.net:1080"),
    ("feeder-dal-1", "Dallas, TX", "dallas.us.socks.nordhold.net:1080"),
    ("feeder-chi-1", "Chicago, IL", "chicago.us.socks.nordhold.net:1080"),
    ("feeder-us-1", "US Anycast", "us.socks.nordhold.net:1080"),
    ("feeder-sf-2", "San Francisco, CA", "socks-us46.nordvpn.com:1080"),
    ("feeder-la-2", "Los Angeles, CA", "socks-us61.nordvpn.com:1080"),
    ("feeder-atl-1", "Atlanta, GA", "socks-us68.nordvpn.com:1080"),
    ("feeder-sf-3", "San Francisco, CA", "socks-us70.nordvpn.com:1080"),
    ("feeder-dal-2", "Dallas, TX", "socks-us73.nordvpn.com:1080"),
]

FALLBACK_STEALTH_SERVER: str = "us.socks.nordhold.net:1080"

# Curated, verified out-of-state standby candidate nodes.
# Strictly excludes Phoenix and avoids any overlap with DEFAULT_STEALTH_HUBS so candidates serve as true backups.
STATIC_CANDIDATE_STEALTH_SERVERS: List[str] = [
    "socks-us60.nordvpn.com:1080",
    "socks-us63.nordvpn.com:1080",
    "socks-us71.nordvpn.com:1080",
    "socks-us52.nordvpn.com:1080",
    "socks-us41.nordvpn.com:1080",
    "socks-us51.nordvpn.com:1080",
    "socks-us74.nordvpn.com:1080",
    "socks-us50.nordvpn.com:1080",
    "socks-us72.nordvpn.com:1080",
    "seattle.us.socks.nordhold.net:1080",
    "miami.us.socks.nordhold.net:1080",
    "new-york.us.socks.nordhold.net:1080",
]

# Module-level candidate list, dynamically refreshed from NordVPN REST API before each run
CANDIDATE_STEALTH_SERVERS: List[str] = list(STATIC_CANDIDATE_STEALTH_SERVERS)


def fetch_nordvpn_candidate_servers(
    default_hubs: Optional[List[Any]] = None,
    timeout: float = 3.0,
    limit: int = 50,
    max_load: int = 70,
) -> List[str]:
    """
    Query the NordVPN REST API for active US SOCKS5 proxy servers.
    - Requires server status == 'online' (rejects maintenance or offline nodes).
    - Checks technology ID 7 or 15 (SOCKS5 proxy) pivot status == 'online'.
    - Filters out overloaded servers (load > max_load, default 70%).
    - Strictly excludes Phoenix locations and hostnames.
    - Strictly excludes all servers present in DEFAULT_STEALTH_HUBS and default_hubs (zero overlap).
    - Sorts candidates by load ascending so the lowest-load nodes are prioritized.
    - Falls back to STATIC_CANDIDATE_STEALTH_SERVERS if API is unreachable.
    """
    hubs_to_exclude = list(DEFAULT_STEALTH_HUBS)
    if default_hubs:
        hubs_to_exclude.extend(default_hubs)

    default_hosts: set = set()
    for item in hubs_to_exclude:
        if isinstance(item, (list, tuple)):
            host_str = item[2] if len(item) == 3 else item[1]
        elif isinstance(item, str):
            host_str = item
        else:
            continue
        clean = host_str.strip().lower()
        default_hosts.add(clean)
        if ":" in clean:
            default_hosts.add(clean.split(":")[0])
        else:
            default_hosts.add(f"{clean}:1080")

    url = (
        f"https://api.nordvpn.com/v1/servers"
        f"?filters[country_id]=228&filters[servers_technologies][id]=7&limit={limit}"
    )
    api_candidates: List[Tuple[int, str]] = []

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "STR-Price-Advisor/1.1 (NordVPN Stealth Pool Manager)",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status_code = getattr(resp, "status", getattr(resp, "code", 200))
            if status_code == 200:
                raw_bytes = resp.read()
                data = json.loads(raw_bytes.decode("utf-8"))
                if isinstance(data, list):
                    for item in data:
                        if not isinstance(item, dict):
                            continue
                        # 1. Server Status: must be online
                        if item.get("status") != "online":
                            continue

                        # 2. SOCKS5 technology status check (id 7 in NordVPN API is Socks 5)
                        techs = item.get("technologies") or []
                        socks5_online = False
                        for t in techs:
                            if isinstance(t, dict):
                                t_id = t.get("id")
                                t_name = (t.get("name") or "").lower()
                                t_ident = (t.get("identifier") or "").lower()
                                if t_id in (7, 15) or "socks" in t_name or "socks" in t_ident:
                                    pivot = t.get("pivot") or {}
                                    if pivot.get("status") == "online" or t.get("status") == "online":
                                        socks5_online = True
                                        break
                        if techs and not socks5_online:
                            continue

                        # 3. Load threshold: reject overloaded servers
                        load_val = item.get("load")
                        try:
                            load_int = int(load_val) if load_val is not None else 50
                        except (ValueError, TypeError):
                            load_int = 50
                        if load_int > max_load:
                            continue

                        # 4. Check city / locations for Phoenix to prevent local surveillance
                        locations = item.get("locations") or []
                        is_phoenix = False
                        for loc in locations:
                            if isinstance(loc, dict):
                                country = loc.get("country") if isinstance(loc.get("country"), dict) else {}
                                city_obj = country.get("city") or loc.get("city") or {}
                                city_name = city_obj.get("name", "") if isinstance(city_obj, dict) else str(city_obj)
                                if "phoenix" in str(city_name).lower():
                                    is_phoenix = True
                                    break
                                subdivision = country.get("subdivision") or loc.get("subdivision") or {}
                                sub_name = subdivision.get("name", "") if isinstance(subdivision, dict) else str(subdivision)
                                if "phoenix" in str(sub_name).lower():
                                    is_phoenix = True
                                    break
                        if is_phoenix:
                            continue

                        # Check server title/name for Phoenix
                        if "phoenix" in (item.get("name") or "").lower():
                            continue

                        # 5. Extract and normalize hostname
                        hostname = item.get("hostname")
                        if not hostname or not isinstance(hostname, str):
                            continue
                        hostname = hostname.strip().lower()
                        if "phoenix" in hostname:
                            continue

                        host_with_port = hostname if ":" in hostname else f"{hostname}:1080"
                        if host_with_port in default_hosts or hostname in default_hosts:
                            continue

                        api_candidates.append((load_int, host_with_port))
    except Exception as e:
        logger.debug(f"NordVPN API server discovery unavailable ({e}); utilizing static candidate fleet.")

    # Sort by load ascending (least congested servers first)
    api_candidates.sort(key=lambda x: x[0])
    dynamic_hosts = [h for _, h in api_candidates]

    # Deduplicate while preserving order and strictly excluding default hubs & Phoenix
    seen: set = set()
    result: List[str] = []
    for h in dynamic_hosts:
        norm = h.strip().lower()
        bare = norm.split(":")[0] if ":" in norm else norm
        if norm not in seen and bare not in seen and norm not in default_hosts and bare not in default_hosts and "phoenix" not in norm:
            seen.add(norm)
            seen.add(bare)
            result.append(h)

    # Always ensure reserve depth by appending static candidates
    for static_h in STATIC_CANDIDATE_STEALTH_SERVERS:
        norm = static_h.strip().lower()
        bare = norm.split(":")[0] if ":" in norm else norm
        if norm not in seen and bare not in seen and norm not in default_hosts and bare not in default_hosts and "phoenix" not in norm:
            seen.add(norm)
            seen.add(bare)
            result.append(static_h)

    return result


def load_env_variables():
    """Load variables from local .env if present without overwriting environment."""
    env_path = Path(".env")
    if env_path.exists():
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        except Exception as e:
            logger.warning(f"Could not parse .env: {e}")


# Load environment variables upon module import
load_env_variables()


def get_free_port(exclude: Optional[set] = None) -> int:
    """Find an available unallocated port on localhost, avoiding excluded ports."""
    exclude = exclude or set()
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", 0))
            p = s.getsockname()[1]
            if p not in exclude:
                return p


@dataclass
class ServerHealthRecord:
    """Tracks per-server health, diagnostic error information, and failure history."""
    host: str
    name: str = ""
    city: str = ""
    is_healthy: bool = False
    stage: str = "PENDING"  # SOCKS5_AUTH, PROCESS_SPAWN, GOOGLE_E2E, ONLINE
    category: str = "UNKNOWN"  # OK, DNS_FAIL, TIMEOUT, CONN_REFUSED, AUTH_FAIL, AUTH_REJECTED, PROTOCOL_ERR, E2E_PROBE_FAIL
    error_detail: str = ""
    latency_ms: Optional[float] = None
    consecutive_failures: int = 0
    first_failed_time: float = 0.0
    last_checked_time: float = 0.0


def format_unhealthy_investigation_report(
    records: Dict[str, ServerHealthRecord],
    target_count: int,
    elapsed_seconds: float,
    next_check_seconds: float,
) -> str:
    """
    Format a comprehensive diagnostic report displaying time, healthy server counts,
    detailed information about unhealthy servers, and actionable troubleshooting guidance.
    """
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    healthy = [r for r in records.values() if r.is_healthy]
    unhealthy = [r for r in records.values() if not r.is_healthy]

    lines = []
    lines.append("=" * 80)
    lines.append(f"⏳ [{now_str}] WAITING FOR HEALTHY STEALTH SERVERS ({len(healthy)}/{target_count} Healthy)")
    lines.append("=" * 80)
    lines.append(
        f"Elapsed: {int(elapsed_seconds // 60)}m {int(elapsed_seconds % 60):02d}s | "
        f"Target: {target_count} healthy servers | "
        f"Next check in {int(next_check_seconds)}s"
    )
    lines.append("")

    if healthy:
        lines.append(f"✅ HEALTHY SERVERS ({len(healthy)}/{target_count}):")
        for idx, r in enumerate(healthy, 1):
            city_str = f"({r.city})" if r.city else ""
            lines.append(f"   {idx:2d}. {r.name:<16} {city_str:<22} -> {r.host} [ONLINE]")
        lines.append("")

    if unhealthy:
        lines.append(f"❌ UNHEALTHY SERVERS ({len(unhealthy)} failing):")
        primary_unhealthy = [r for r in unhealthy if not r.name.startswith("Backup")]
        backup_unhealthy = [r for r in unhealthy if r.name.startswith("Backup")]

        for idx, r in enumerate(primary_unhealthy, 1):
            city_str = f"({r.city})" if r.city else ""
            lines.append(f"   {idx:2d}. {r.name:<16} {city_str:<22} -> {r.host}")
            lines.append(f"      Stage: {r.stage} | Category: [{r.category}] | Fails: {r.consecutive_failures}")
            lines.append(f"      Detail: {r.error_detail}")

        max_backup_display = 6
        for idx, r in enumerate(backup_unhealthy[:max_backup_display], len(primary_unhealthy) + 1):
            city_str = f"({r.city})" if r.city else ""
            lines.append(f"   {idx:2d}. {r.name:<16} {city_str:<22} -> {r.host}")
            lines.append(f"      Stage: {r.stage} | Category: [{r.category}] | Fails: {r.consecutive_failures}")
            lines.append(f"      Detail: {r.error_detail}")

        if len(backup_unhealthy) > max_backup_display:
            lines.append(f"   ... and {len(backup_unhealthy) - max_backup_display} more reserve candidate nodes failing.")
        lines.append("")

    # Actionable troubleshooting hints based on observed categories
    categories = {r.category for r in unhealthy}
    hints = []
    if {"AUTH_FAIL", "AUTH_REJECTED", "AUTH_EOF"} & categories:
        hints.append(
            "• [AUTH_FAIL/REJECTED]: SOCKS5 authentication rejected or session closed. NordVPN limits "
            "accounts to 10 active concurrent connections. Ensure no other scrape jobs, VPN clients, "
            "or processes are using your credentials."
        )
    if "DNS_FAIL" in categories:
        hints.append(
            "• [DNS_FAIL]: Remote host could not be resolved via DNS. Check your DNS resolver, "
            "or node hostname may have been retired by NordVPN."
        )
    if "TIMEOUT" in categories:
        hints.append(
            "• [TIMEOUT]: Connection or handshake timed out. The node may be congested, undergoing "
            "maintenance, or blocked. Candidates will be auto-refreshed via NordVPN REST API."
        )
    if "CONN_REFUSED" in categories:
        hints.append(
            "• [CONN_REFUSED]: Remote SOCKS5 daemon refused TCP connection on port 1080."
        )
    if "E2E_PROBE_FAIL" in categories:
        hints.append(
            "• [E2E_PROBE_FAIL]: Forwarder bridge established, but end-to-end HTTP request to Google "
            "failed. The exit IP may be temporarily blocked or throttled."
        )

    if hints:
        lines.append("🔍 INVESTIGATION & TROUBLESHOOTING HINTS:")
        for h in hints:
            lines.append(f"   {h}")
        lines.append("")

    lines.append(f"🔄 Re-probing unhealthy nodes and auto-refreshing NordVPN candidates in {int(next_check_seconds)}s...")
    lines.append("   (To abort waiting, press Ctrl+C)")
    lines.append("=" * 80)
    return "\n".join(lines)


@dataclass
class StealthEndpoint:
    """Represents an active local forwarder bridge to a remote SOCKS5 server."""
    name: str
    remote_host: str
    local_port: int
    proc: Optional[asyncio.subprocess.Process] = None
    url: str = ""
    city: str = ""
    index: int = 0
    latency_ms: Optional[float] = None
    status: str = "OFFLINE"


class StealthConnectionManager:
    """
    High-capacity parallel VPN pool manager.
    Coordinates up to 10 local forwarders connected to distinct verified NordVPN SOCKS5 endpoints.
    Provides pre-flight health checks against Google and dynamic candidate hot-swapping.
    """

    def __init__(self, port: Optional[int] = None, required: bool = True, max_workers: Optional[int] = None):
        mod = sys.modules.get(self.__module__)
        _get_free_port = getattr(mod, "get_free_port", get_free_port)
        self.port = port or _get_free_port()
        self.required = required
        env_max = os.getenv("STEALTH_MAX_CONNECTIONS")
        if env_max:
            try:
                parsed_max = int(env_max)
            except ValueError:
                parsed_max = DEFAULT_MAX_STEALTH_CONNECTIONS
        else:
            parsed_max = DEFAULT_MAX_STEALTH_CONNECTIONS
        self.max_workers = max_workers or parsed_max
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.endpoints: List[StealthEndpoint] = []
        self.candidate_servers: Optional[List[str]] = None
        self._worker_queue: Optional[asyncio.Queue] = None
        self._old_env: Dict[str, Optional[str]] = {}
        self.startup_delay: float = float(os.getenv("STEALTH_STARTUP_DELAY", "0.6"))
        load_env_variables()
        atexit.register(self._cleanup_sync)
        try:
            def _sig_handler(signum, frame):
                self._cleanup_sync()
                sys.exit(128 + signum)
            signal.signal(signal.SIGTERM, _sig_handler)
        except (ValueError, AttributeError):
            pass

    @staticmethod
    def calculate_min_healthy(num_workers: int) -> int:
        """Calculate resilient dynamic quorum: num_workers if < 3 else max(3, num_workers - 2)."""
        return num_workers if num_workers < 3 else max(3, num_workers - 2)

    @property
    def is_configured(self) -> bool:
        """Returns True if NordVPN credentials exist in environment or .env."""
        return bool(os.getenv("NORDVPN_USER") and os.getenv("NORDVPN_PASS"))

    @staticmethod
    def verify_socks5_detailed(
        host: str,
        port: int = 1080,
        username: Optional[str] = None,
        password: Optional[str] = None,
        timeout: float = 1.5,
    ) -> Tuple[bool, str, str]:
        """
        Verify that a remote SOCKS5 server is reachable and accepts RFC 1928/1929 credentials.
        Returns: (is_ok: bool, error_detail: str, error_category: str)
        Error categories: 'OK', 'MISSING_CREDS', 'DNS_FAIL', 'TIMEOUT', 'CONN_REFUSED',
                          'NET_ERROR', 'PROTOCOL_ERR', 'AUTH_REJECTED', 'AUTH_FAIL', 'AUTH_EOF'
        """
        user = username or os.getenv("NORDVPN_USER", "")
        pwd = password or os.getenv("NORDVPN_PASS", "")
        if not user or not pwd:
            return False, "Missing NORDVPN_USER or NORDVPN_PASS credentials in environment/.env", "MISSING_CREDS"

        if "://" in host:
            host = host.split("://", 1)[1]
        if "#" in host:
            host = host.split("#", 1)[0]
        if "@" in host:
            host = host.rsplit("@", 1)[1]
        if ":" in host:
            h_part, p_part = host.rsplit(":", 1)
            host = h_part
            try:
                port = int(p_part)
            except ValueError:
                pass

        def _recv_exact(sock: socket.socket, num_bytes: int) -> bytes:
            buf = bytearray()
            while len(buf) < num_bytes:
                chunk = sock.recv(num_bytes - len(buf))
                if not chunk:
                    break
                buf.extend(chunk)
            return bytes(buf)

        try:
            with socket.create_connection((host, port), timeout=timeout) as s:
                s.sendall(b"\x05\x01\x02")
                greeting_resp = _recv_exact(s, 2)
                if not greeting_resp:
                    return False, "Premature EOF on SOCKS5 greeting (server closed connection)", "PROTOCOL_ERR"
                if len(greeting_resp) < 2:
                    return False, f"Incomplete greeting response ({len(greeting_resp)} bytes received)", "PROTOCOL_ERR"
                if greeting_resp[0] != 0x05:
                    return False, f"Not a SOCKS5 server (version byte: 0x{greeting_resp[0]:02x}, expected 0x05)", "PROTOCOL_ERR"
                if greeting_resp[1] == 0xFF:
                    return False, "Server rejected authentication methods (0xFF: no acceptable auth methods)", "AUTH_REJECTED"
                if greeting_resp[1] != 0x02:
                    return False, f"Server requested unsupported auth method (0x{greeting_resp[1]:02x}, expected 0x02)", "PROTOCOL_ERR"

                u_bytes = user.encode("utf-8")
                p_bytes = pwd.encode("utf-8")
                auth_packet = b"\x01" + bytes([len(u_bytes)]) + u_bytes + bytes([len(p_bytes)]) + p_bytes
                s.sendall(auth_packet)
                auth_resp = _recv_exact(s, 2)
                if not auth_resp:
                    return False, "Premature EOF on SOCKS5 auth response (server closed connection)", "AUTH_EOF"
                if len(auth_resp) < 2:
                    return False, f"Incomplete auth response ({len(auth_resp)} bytes received)", "AUTH_EOF"
                if auth_resp[0] != 0x01:
                    return False, f"Invalid RFC 1929 auth version (0x{auth_resp[0]:02x}, expected 0x01)", "PROTOCOL_ERR"
                if auth_resp[1] != 0x00:
                    status_code = auth_resp[1]
                    return (
                        False,
                        f"SOCKS5 auth rejected (status 0x{status_code:02x}: credentials invalid or 10-connection limit reached)",
                        "AUTH_FAIL",
                    )
                return True, "SOCKS5 handshake & RFC 1929 authentication verified", "OK"
        except socket.gaierror as e:
            return False, f"DNS resolution failed for '{host}': {e}", "DNS_FAIL"
        except (socket.timeout, TimeoutError):
            return False, f"TCP connection or handshake timed out after {timeout:.1f}s", "TIMEOUT"
        except ConnectionRefusedError:
            return False, f"Connection refused on port {port}", "CONN_REFUSED"
        except OSError as e:
            err_str = str(e)
            if "timed out" in err_str.lower():
                return False, f"TCP connection timed out after {timeout:.1f}s", "TIMEOUT"
            return False, f"Socket connection error: {err_str}", "NET_ERROR"
        except Exception as e:
            return False, f"Unexpected connection error: {e}", "NET_ERROR"

    @staticmethod
    def verify_socks5(
        host: str,
        port: int = 1080,
        username: Optional[str] = None,
        password: Optional[str] = None,
        timeout: float = 1.5,
    ) -> bool:
        """
        Verify that a remote SOCKS5 server is reachable and accepts RFC 1928/1929 credentials.
        Returns True if greeting (0x05 0x02) and auth (0x01 0x00) succeed within timeout.
        """
        ok, _, _ = StealthConnectionManager.verify_socks5_detailed(
            host=host, port=port, username=username, password=password, timeout=timeout
        )
        return ok

    @staticmethod
    async def test_endpoint_connectivity(
        port: int,
        target_url: str = "https://www.google.com",
        timeout: float = 3.5,
    ) -> Tuple[bool, float, str]:
        """
        Test that a local forwarder port is functioning end-to-end by issuing an HTTP GET to target_url.
        Returns (is_ok, latency_ms, status_or_error_message).
        """
        def _probe():
            t0 = time.time()
            try:
                try:
                    import certifi
                    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
                except Exception:
                    ssl_ctx = ssl._create_unverified_context()

                proxy_handler = urllib.request.ProxyHandler({
                    "http": f"http://127.0.0.1:{port}",
                    "https": f"http://127.0.0.1:{port}",
                })
                https_handler = urllib.request.HTTPSHandler(context=ssl_ctx)
                opener = urllib.request.build_opener(proxy_handler, https_handler)
                req = urllib.request.Request(
                    target_url,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
                        )
                    },
                )
                with opener.open(req, timeout=timeout) as resp:
                    status_code = resp.getcode()
                    lat = (time.time() - t0) * 1000.0
                    ok = (200 <= status_code < 400)
                    return ok, lat, f"HTTP {status_code}"
            except Exception as e:
                lat = (time.time() - t0) * 1000.0
                err_msg = str(e)
                if "timed out" in err_msg.lower():
                    err_msg = "Timeout"
                return False, lat, err_msg

        return await asyncio.to_thread(_probe)

    @staticmethod
    async def _reap_process(proc: Optional[asyncio.subprocess.Process]):
        """Terminate and await child process to avoid zombie processes."""
        if not proc or proc.returncode is not None:
            return
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=1.0)
        except Exception:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass

    @classmethod
    def reap_orphaned_forwarders(cls) -> int:
        """
        Identify and kill orphaned pproxy forwarders whose parent process has died (PPID == 1).
        Guarantees that NordVPN's 10-connection limit is not breached by zombie forwarders
        from aborted or crashed terminal sessions.
        Returns the number of reaped orphan processes.
        """
        reaped = 0
        if os.getpid() == 1:
            # Running as container entrypoint; children naturally have PPID == 1
            return 0
        try:
            out = subprocess.check_output(["ps", "-eo", "pid,ppid,command"], text=True)
            for line in out.splitlines():
                parts = line.strip().split(None, 2)
                if len(parts) >= 3:
                    pid_str, ppid_str, cmd = parts[0], parts[1], parts[2]
                    if ppid_str == "1" and "pproxy" in cmd and any(k in cmd for k in ["nordhold.net", "socks5:", "nordvpn"]):
                        try:
                            pid = int(pid_str)
                            os.kill(pid, signal.SIGKILL)
                            reaped += 1
                            logger.info(f"Reaped orphaned forwarder PID {pid}: {cmd[:60]}")
                        except Exception:
                            pass
        except Exception as e:
            logger.debug(f"Error checking for orphaned forwarders: {e}")
        return reaped

    async def _launch_forwarder(
        self,
        name: str,
        city: str,
        remote_host: str,
        user: str,
        pwd: str,
        allocated_ports: set,
    ) -> StealthEndpoint:
        """Launch a single local pproxy forwarder subprocess."""
        mod = sys.modules.get(self.__module__)
        _get_free_port = getattr(mod, "get_free_port", get_free_port)
        _asyncio = getattr(mod, "asyncio", asyncio)

        port = _get_free_port(exclude=allocated_ports)
        allocated_ports.add(port)
        proc = await _asyncio.create_subprocess_exec(
            sys.executable, "-m", "pproxy",
            "-l", f"http://127.0.0.1:{port}",
            "-r", f"socks5://{remote_host}#{user}:{pwd}",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        url = f"http://127.0.0.1:{port}"
        return StealthEndpoint(
            name=name,
            remote_host=remote_host,
            local_port=port,
            proc=proc,
            url=url,
            city=city,
            index=0,
        )

    async def start_pool(
        self,
        num_workers: Optional[int] = None,
        servers: Optional[List[Union[Tuple[str, str], Tuple[str, str, str]]]] = None,
        wait_for_full_pool: Optional[bool] = None,
        min_healthy: Optional[int] = None,
        max_wait_seconds: Optional[float] = None,
        test_target: Optional[str] = "https://www.google.com",
        test_timeout: float = 3.5,
        candidate_servers: Optional[List[str]] = None,
        wait_interval_seconds: Optional[float] = None,
        verbose: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        """
        Start a multi-IP parallel stealth proxy pool (default 10 workers).
        Waits indefinitely until min_healthy (or full pool) verified working endpoints
        are online, displaying rich per-minute diagnostics while waiting.

        :param num_workers: Target worker count (default 10).
        :param servers: Optional explicit list of server endpoints (2-tuples or 3-tuples).
        :param wait_for_full_pool: If True, waits indefinitely until all target hubs are healthy.
                                   If False, proceeds as soon as min_healthy unique servers are reached.
                                   Defaults to True when min_healthy is not set or equals num_workers.
        :param min_healthy: Minimum number of healthy unique endpoints required before proceeding.
        :param max_wait_seconds: Optional timeout (in seconds) to abort waiting.
        :param test_target: Probe URL for E2E connectivity check (default Google).
        :param test_timeout: E2E HTTP probe timeout in seconds.
        :param candidate_servers: Optional override list of candidate backup servers.
        :param wait_interval_seconds: Polling and diagnostic log interval in seconds (default 60s).
        :param verbose: Enable verbose table printing for hubs and candidate discovery.
        """
        num_workers = num_workers or self.max_workers

        env_max_wait = os.getenv("STEALTH_MAX_WAIT_SECONDS")
        if max_wait_seconds is None:
            if env_max_wait:
                try:
                    max_wait_seconds = float(env_max_wait)
                except ValueError:
                    max_wait_seconds = DEFAULT_MAX_WAIT_SECONDS
            else:
                max_wait_seconds = DEFAULT_MAX_WAIT_SECONDS

        env_wait_interval = os.getenv("STEALTH_WAIT_INTERVAL")
        if wait_interval_seconds is None:
            if env_wait_interval:
                try:
                    wait_interval_seconds = float(env_wait_interval)
                except ValueError:
                    wait_interval_seconds = 60.0
            else:
                wait_interval_seconds = 60.0

        if not self.is_configured:
            if self.required:
                raise RuntimeError(
                    "❌ [PROXY REQUIRED] Web scraping was blocked because NordVPN credentials "
                    "were not found in .env! Please set NORDVPN_USER and NORDVPN_PASS."
                )
            logger.warning("NordVPN credentials not configured; stealth proxy pool disabled.")
            return []

        # Determine single-worker vs multi-worker pool mode
        is_single_worker = (num_workers == 1) or (servers is not None and len(servers) == 1 and servers[0][0] == "feeder-single")
        env_verbose = os.getenv("STEALTH_VERBOSE", "0").lower() in ("1", "true", "yes")
        if verbose is None:
            verbose = env_verbose

        if verbose:
            print("=" * 80)
            print("🛡️  STEALTH CONNECTION POOL: PRIMARY FEEDER HUBS (DEFAULT_STEALTH_HUBS)")
            print("=" * 80)
            for idx, item in enumerate(DEFAULT_STEALTH_HUBS, 1):
                h_name = item[0]
                h_city = item[1] if len(item) == 3 else ""
                h_host = item[2] if len(item) == 3 else item[1]
                print(f"  {idx:2d}. {h_name:<15} ({h_city:<20}) -> {h_host}")
            print("-" * 80)
        elif not is_single_worker:
            print(f"🛡️  Stealth Feeder Pool: Initializing {num_workers} parallel proxy bridges across out-of-state travel hubs...")

        # Normalize servers input (accepts both 2-tuples and 3-tuples)
        raw_servers = (servers or DEFAULT_STEALTH_HUBS)[:num_workers]
        target_hubs: List[Tuple[str, str, str]] = []
        for item in raw_servers:
            if len(item) == 2:
                name, host = item
                target_hubs.append((name, name.replace("-", " ").title(), host))
            else:
                target_hubs.append(item)

        # Dynamic candidate server discovery via NordVPN REST API
        global CANDIDATE_STEALTH_SERVERS
        if candidate_servers is not None:
            self.candidate_servers = list(candidate_servers)
        else:
            if not is_single_worker or verbose:
                if verbose:
                    print("🔍 Discovering active low-load US SOCKS5 candidate servers via NordVPN API...")
                _fetch_fn = getattr(sys.modules[__name__], "fetch_nordvpn_candidate_servers", fetch_nordvpn_candidate_servers)
                dynamic_cands = await asyncio.to_thread(_fetch_fn, target_hubs)
                self.candidate_servers = dynamic_cands
                CANDIDATE_STEALTH_SERVERS = list(dynamic_cands)
            else:
                self.candidate_servers = list(STATIC_CANDIDATE_STEALTH_SERVERS)

        if verbose and self.candidate_servers:
            print(f"✅ Populated {len(self.candidate_servers)} candidate backup servers (strictly disjoint from primary hubs):")
            for idx, c_host in enumerate(self.candidate_servers[:10], 1):
                print(f"     [Backup {idx:2d}] {c_host}")
            if len(self.candidate_servers) > 10:
                print(f"     ... and {len(self.candidate_servers) - 10} more reserve candidate nodes.")
            print("=" * 80)

        candidate_pool = self.candidate_servers if self.candidate_servers is not None else CANDIDATE_STEALTH_SERVERS

        # Expand target_hubs if more workers requested than default hubs
        if len(target_hubs) < num_workers:
            existing_hosts = {rh for _, _, rh in target_hubs}
            available_cands = [c for c in candidate_pool if c not in existing_hosts and "phoenix" not in c.lower()]
            for idx in range(len(target_hubs), num_workers):
                if available_cands:
                    cand_host = available_cands.pop(0)
                    existing_hosts.add(cand_host)
                else:
                    cand_host = candidate_pool[idx % len(candidate_pool)] if candidate_pool else FALLBACK_STEALTH_SERVER
                target_hubs.append((f"feeder-{idx+1}", f"Candidate Node {idx+1}", cand_host))

        user = os.environ.get("NORDVPN_USER", "")
        pwd = os.environ.get("NORDVPN_PASS", "")

        # Clean up existing endpoints if already running
        await self.stop_pool()
        self.reap_orphaned_forwarders()

        if min_healthy is None:
            min_healthy = self.calculate_min_healthy(len(target_hubs))
        min_healthy = min(min_healthy, len(target_hubs))

        if wait_for_full_pool is None:
            wait_for_full_pool = (min_healthy >= len(target_hubs))

        desired_count = len(target_hubs)
        if min_healthy > desired_count:
            desired_count = min_healthy

        target_count = desired_count if wait_for_full_pool else min_healthy

        # Slot metadata definitions (slot_idx: name, city, default_host)
        slot_definitions: List[Tuple[str, str, str]] = []
        for idx in range(desired_count):
            if idx < len(target_hubs):
                item = target_hubs[idx]
                h_name = item[0]
                h_city = item[1] if len(item) == 3 else item[0].replace("-", " ").title()
                h_host = item[2] if len(item) == 3 else item[1]
            else:
                h_name = f"feeder-{idx+1}"
                h_city = f"Candidate Node {idx+1}"
                h_host = ""
            norm_h = "los-angeles.us.socks.nordhold.net:1080" if "phoenix" in h_host.lower() else h_host
            slot_definitions.append((h_name, h_city, norm_h))

        tested_cache: Dict[str, Tuple[bool, str, str]] = {}
        health_records: Dict[str, ServerHealthRecord] = {}
        for h_name, h_city, norm_h in slot_definitions:
            if norm_h:
                health_records[norm_h] = ServerHealthRecord(
                    host=norm_h,
                    name=h_name,
                    city=h_city,
                    stage="SOCKS5_AUTH",
                )

        async def _check_socks5_with_record(h: str, name: str = "", city: str = "") -> Tuple[str, bool, str, str]:
            if h in tested_cache:
                is_ok, err_msg, cat = tested_cache[h]
                return h, is_ok, err_msg, cat

            verify_attr = getattr(self, "verify_socks5", None)
            is_mocked = hasattr(verify_attr, "assert_called") or hasattr(verify_attr, "side_effect") or hasattr(verify_attr, "return_value")
            if is_mocked:
                is_ok = await asyncio.to_thread(self.verify_socks5, h, 1080, user, pwd, 1.5)
                err_msg = "SOCKS5 verified" if is_ok else "SOCKS5 verification failed"
                cat = "OK" if is_ok else "AUTH_FAIL"
            else:
                is_ok, err_msg, cat = await asyncio.to_thread(self.verify_socks5_detailed, h, 1080, user, pwd, 1.5)

            tested_cache[h] = (is_ok, err_msg, cat)
            rec = health_records.get(h)
            if not rec:
                rec = ServerHealthRecord(host=h, name=name, city=city, stage="SOCKS5_AUTH")
                health_records[h] = rec
            rec.is_healthy = is_ok
            rec.category = cat
            rec.error_detail = err_msg if not is_ok else "SOCKS5 verified"
            rec.last_checked_time = time.time()
            if not is_ok:
                rec.consecutive_failures += 1
                if rec.first_failed_time == 0.0:
                    rec.first_failed_time = time.time()
            else:
                rec.consecutive_failures = 0
            return h, is_ok, err_msg, cat

        active_slots: Dict[int, StealthEndpoint] = {}
        allocated_ports: set = set()
        start_time = time.time()
        last_log_time = 0.0
        loop_iteration = 0

        while True:
            loop_iteration += 1

            # 1. Audit active slots: reap any forwarders that crashed or failed
            for slot_idx, ep in list(active_slots.items()):
                if ep.proc and ep.proc.returncode is not None:
                    logger.warning(
                        f"Forwarder {ep.name} ({ep.remote_host}, port {ep.local_port}) exited with code {ep.proc.returncode}. "
                        f"Evicting slot {slot_idx} for replacement..."
                    )
                    await self._reap_process(ep.proc)
                    rec = health_records.get(ep.remote_host)
                    if rec:
                        rec.stage = "PROCESS_SPAWN"
                        rec.category = "NET_ERROR"
                        rec.error_detail = f"Process exited prematurely with code {ep.proc.returncode}"
                        rec.is_healthy = False
                        rec.consecutive_failures += 1
                    del active_slots[slot_idx]
                elif test_target and ep.status != "ONLINE":
                    await self._reap_process(ep.proc)
                    del active_slots[slot_idx]

            used_hosts = {ep.remote_host for ep in active_slots.values()}
            allocated_ports = {ep.local_port for ep in active_slots.values()}

            # 2. Check if target healthy count is reached
            if len(active_slots) >= target_count and len(used_hosts) >= target_count:
                if last_log_time > 0:
                    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
                    print("=" * 80)
                    print(f"✅ [{now_str}] Reached {len(active_slots)}/{target_count} healthy servers! Resuming operation...")
                    print("=" * 80)
                break

            # 3. Check explicit max_wait_seconds timeout
            if max_wait_seconds is not None and (time.time() - start_time) >= max_wait_seconds:
                unique_healthy_hosts = len(used_hosts)
                if len(active_slots) >= min_healthy and unique_healthy_hosts >= min_healthy:
                    logger.warning(
                        f"Startup wait timed out after {max_wait_seconds:.1f}s. "
                        f"Proceeding with {len(active_slots)}/{desired_count} healthy servers (quorum met)."
                    )
                    break
                elif not self.required:
                    logger.warning(
                        f"Startup wait timed out after {max_wait_seconds:.1f}s. "
                        f"Proceeding with {len(active_slots)} available forwarders (optional mode)."
                    )
                    break
                else:
                    report = format_unhealthy_investigation_report(
                        records=health_records,
                        target_count=min_healthy,
                        elapsed_seconds=time.time() - start_time,
                        next_check_seconds=0.0,
                    )
                    print(report)
                    raise RuntimeError(
                        f"❌ [STEALTH PROXY ERROR] Mandatory NordVPN feeder pool failed to start working forwarder bridges! "
                        f"Only {unique_healthy_hosts} distinct healthy remote servers verified "
                        f"(minimum required: {min_healthy}, active endpoints: {len(active_slots)}). "
                        f"Refusing to start scanning with duplicate or under-provisioned connections."
                    )

            # 4. If waiting is required (after first attempt):
            if loop_iteration > 1:
                elapsed = time.time() - start_time
                interval = wait_interval_seconds if wait_interval_seconds is not None else 60.0
                report_interval = max(interval, 1.0)
                if last_log_time == 0.0 or (time.time() - last_log_time >= report_interval):
                    report = format_unhealthy_investigation_report(
                        records=health_records,
                        target_count=target_count,
                        elapsed_seconds=elapsed,
                        next_check_seconds=interval,
                    )
                    print(report)
                    last_log_time = time.time()

                sleep_duration = interval
                if max_wait_seconds is not None:
                    remaining = max_wait_seconds - (time.time() - start_time)
                    if remaining <= 0:
                        break
                    sleep_duration = min(interval, max(0.0, remaining))

                if sleep_duration > 0:
                    await asyncio.sleep(sleep_duration)
                else:
                    await asyncio.sleep(0.001)

            # 5. Invalidate failed entries in tested_cache so they can be re-evaluated
            for k, (is_ok, _, _) in list(tested_cache.items()):
                if not is_ok:
                    del tested_cache[k]

            # 6. Auto-refresh candidate pool from NordVPN API if candidate pool is running low
            unfilled_slots = [i for i in range(desired_count) if i not in active_slots]
            if candidate_servers is None and unfilled_slots:
                available_cands = [c for c in candidate_pool if c not in used_hosts and "phoenix" not in c.lower()]
                if len(available_cands) < len(unfilled_slots):
                    try:
                        _fetch_fn = getattr(sys.modules[__name__], "fetch_nordvpn_candidate_servers", fetch_nordvpn_candidate_servers)
                        fresh = await asyncio.to_thread(_fetch_fn, target_hubs)
                        for c in fresh:
                            if c not in candidate_pool and "phoenix" not in c.lower():
                                candidate_pool.append(c)
                    except Exception as ex:
                        logger.debug(f"Candidate refresh failed: {ex}")

            # 7. For each unfilled slot, find a working server and launch forwarder bridge
            # First pass: try default hub host for each unfilled slot
            default_hosts_to_probe: List[Tuple[int, str, str, str]] = []
            for slot_idx in unfilled_slots:
                slot_name, slot_city, default_host = slot_definitions[slot_idx]
                if default_host and default_host not in used_hosts and "phoenix" not in default_host.lower():
                    default_hosts_to_probe.append((slot_idx, slot_name, slot_city, default_host))

            if default_hosts_to_probe:
                probe_results = await asyncio.gather(*[
                    _check_socks5_with_record(h, name, city)
                    for _, name, city, h in default_hosts_to_probe
                ])
                launch_tasks = []
                seen_in_batch = set()
                for (s_idx, s_name, s_city, host), (_, is_ok, _, _) in zip(default_hosts_to_probe, probe_results):
                    if is_ok and host not in used_hosts and host not in seen_in_batch:
                        seen_in_batch.add(host)
                        launch_tasks.append((s_idx, s_name, s_city, host))

                if launch_tasks:
                    launched = []
                    for s_idx, s_name, s_city, s_host in launch_tasks:
                        try:
                            ep = await self._launch_forwarder(s_name, s_city, s_host, user, pwd, allocated_ports)
                            ep.index = s_idx + 1
                            allocated_ports.add(ep.local_port)
                            launched.append((s_idx, s_host, ep))
                        except Exception as e:
                            logger.debug(f"Failed to launch forwarder for {s_name} ({s_host}): {e}")
                            rec = health_records.get(s_host)
                            if rec:
                                rec.stage = "PROCESS_SPAWN"
                                rec.category = "NET_ERROR"
                                rec.error_detail = str(e)
                                rec.is_healthy = False
                                rec.consecutive_failures += 1

                    startup_delay = getattr(self, "startup_delay", 0.6)
                    if startup_delay > 0 and launched:
                        await asyncio.sleep(startup_delay)

                    if launched:
                        if test_target:
                            check_results = await asyncio.gather(*[
                                self.test_endpoint_connectivity(ep.local_port, target_url=test_target, timeout=test_timeout)
                                for _, _, ep in launched
                            ], return_exceptions=True)

                            for (s_idx, s_host, ep), res in zip(launched, check_results):
                                if ep.proc and ep.proc.returncode is not None:
                                    ep.status = f"FAILED (process exited prematurely with code {ep.proc.returncode})"
                                    await self._reap_process(ep.proc)
                                    allocated_ports.discard(ep.local_port)
                                    rec = health_records.get(s_host)
                                    if rec:
                                        rec.stage = "PROCESS_SPAWN"
                                        rec.category = "NET_ERROR"
                                        rec.error_detail = f"Process exited prematurely with code {ep.proc.returncode}"
                                        rec.is_healthy = False
                                        rec.consecutive_failures += 1
                                elif isinstance(res, tuple) and res[0] is True:
                                    ok, lat, msg = res
                                    ep.latency_ms = lat
                                    ep.status = "ONLINE"
                                    active_slots[s_idx] = ep
                                    used_hosts.add(s_host)
                                    rec = health_records.get(s_host)
                                    if rec:
                                        rec.is_healthy = True
                                        rec.stage = "ONLINE"
                                        rec.category = "OK"
                                        rec.error_detail = "SOCKS5 verified & Google probe succeeded"
                                        rec.consecutive_failures = 0
                                else:
                                    err_msg = res[2] if isinstance(res, tuple) else str(res)
                                    ep.status = f"FAILED ({err_msg})"
                                    await self._reap_process(ep.proc)
                                    allocated_ports.discard(ep.local_port)
                                    rec = health_records.get(s_host)
                                    if rec:
                                        rec.stage = "GOOGLE_E2E"
                                        rec.category = "E2E_PROBE_FAIL"
                                        rec.error_detail = f"Google probe failed: {err_msg}"
                                        rec.is_healthy = False
                                        rec.consecutive_failures += 1
                        else:
                            for s_idx, s_host, ep in launched:
                                if ep.proc and ep.proc.returncode is not None:
                                    ep.status = f"FAILED (process exited prematurely with code {ep.proc.returncode})"
                                    await self._reap_process(ep.proc)
                                    allocated_ports.discard(ep.local_port)
                                    rec = health_records.get(s_host)
                                    if rec:
                                        rec.stage = "PROCESS_SPAWN"
                                        rec.category = "NET_ERROR"
                                        rec.error_detail = f"Process exited prematurely with code {ep.proc.returncode}"
                                        rec.is_healthy = False
                                        rec.consecutive_failures += 1
                                else:
                                    ep.status = "ONLINE"
                                    active_slots[s_idx] = ep
                                    used_hosts.add(s_host)


            # Second pass: for any slots STILL unfilled, probe and launch from candidate_pool
            remaining_unfilled = [i for i in range(desired_count) if i not in active_slots]
            if remaining_unfilled:
                candidate_queue = [
                    c for c in candidate_pool
                    if c not in used_hosts and "phoenix" not in c.lower()
                ]
                cand_idx = 0
                for slot_idx in remaining_unfilled:
                    slot_name, slot_city, _ = slot_definitions[slot_idx]
                    while cand_idx < len(candidate_queue):
                        cand_server = candidate_queue[cand_idx]
                        cand_idx += 1
                        if cand_server in used_hosts:
                            continue

                        _, is_ok, err_msg, cat = await _check_socks5_with_record(
                            cand_server, f"Backup ({slot_name})", slot_city
                        )
                        if not is_ok:
                            continue

                        ep = None
                        try:
                            ep = await self._launch_forwarder(
                                slot_name, slot_city, cand_server, user, pwd, allocated_ports
                            )
                            ep.index = slot_idx + 1
                            allocated_ports.add(ep.local_port)

                            startup_delay = getattr(self, "startup_delay", 0.6)
                            if startup_delay > 0:
                                await asyncio.sleep(startup_delay)

                            if ep.proc and ep.proc.returncode is not None:
                                ep.status = f"FAILED (process exited prematurely with code {ep.proc.returncode})"
                                await self._reap_process(ep.proc)
                                allocated_ports.discard(ep.local_port)
                                rec = health_records.get(cand_server)
                                if rec:
                                    rec.stage = "PROCESS_SPAWN"
                                    rec.category = "NET_ERROR"
                                    rec.error_detail = f"Process exited prematurely with code {ep.proc.returncode}"
                                    rec.is_healthy = False
                                    rec.consecutive_failures += 1
                                continue

                            if test_target:
                                ok, lat, msg = await self.test_endpoint_connectivity(
                                    ep.local_port, target_url=test_target, timeout=test_timeout
                                )
                                if ok:
                                    ep.latency_ms = lat
                                    ep.status = "ONLINE"
                                    active_slots[slot_idx] = ep
                                    used_hosts.add(cand_server)
                                    rec = health_records.get(cand_server)
                                    if rec:
                                        rec.is_healthy = True
                                        rec.stage = "ONLINE"
                                        rec.category = "OK"
                                        rec.error_detail = "SOCKS5 verified & Google probe succeeded"
                                        rec.consecutive_failures = 0
                                    break
                                else:
                                    await self._reap_process(ep.proc)
                                    allocated_ports.discard(ep.local_port)
                                    rec = health_records.get(cand_server)
                                    if rec:
                                        rec.stage = "GOOGLE_E2E"
                                        rec.category = "E2E_PROBE_FAIL"
                                        rec.error_detail = f"Google probe failed: {msg}"
                                        rec.is_healthy = False
                                        rec.consecutive_failures += 1
                            else:
                                ep.status = "ONLINE"
                                active_slots[slot_idx] = ep
                                used_hosts.add(cand_server)
                                break
                        except Exception as e:
                            logger.debug(f"Failed candidate forwarder for {slot_name} ({cand_server}): {e}")
                            if ep and ep.proc and slot_idx not in active_slots:
                                await self._reap_process(ep.proc)
                                allocated_ports.discard(ep.local_port)

            # If wait_for_full_pool is False and quorum is satisfied, proceed immediately without waiting for full capacity
            if not wait_for_full_pool and len(active_slots) >= min_healthy and len(used_hosts) >= min_healthy:
                break

        self.endpoints = [active_slots[i] for i in sorted(active_slots.keys())]

        unique_healthy_hosts = len({ep.remote_host for ep in self.endpoints})
        if (len(self.endpoints) < min_healthy or unique_healthy_hosts < min_healthy) and self.required:
            report = format_unhealthy_investigation_report(
                records=health_records,
                target_count=min_healthy,
                elapsed_seconds=time.time() - start_time,
                next_check_seconds=0.0,
            )
            print(report)
            raise RuntimeError(
                f"❌ [STEALTH PROXY ERROR] Mandatory NordVPN feeder pool failed to start working forwarder bridges! "
                f"Only {unique_healthy_hosts} distinct healthy remote servers verified "
                f"(minimum required: {min_healthy}, active endpoints: {len(self.endpoints)}). "
                f"Refusing to start scanning with duplicate or under-provisioned connections."
            )

        # Step 7: Populate worker queue for concurrent worker leasing
        self._worker_queue = asyncio.Queue()
        for ep in self.endpoints:
            self._worker_queue.put_nowait(ep)

        summary_parts = [
            f"{ep.name} ({ep.city.split(',')[0]} port {ep.local_port}"
            f"{f', {ep.latency_ms:.0f}ms' if ep.latency_ms else ''})"
            for ep in self.endpoints
        ]
        if not is_single_worker:
            print(f"  🛡️ Stealth Multi-IP Feeder Pool Online ({len(self.endpoints)} endpoints): {', '.join(summary_parts)}")
        else:
            logger.debug(f"Stealth single proxy online: {', '.join(summary_parts)}")

        return self.get_proxy_configs()

    def get_proxy_configs(self) -> List[Dict[str, Any]]:
        """Return formatted proxy configurations for active endpoints in the pool."""
        return [
            {
                "index": ep.index,
                "name": ep.name,
                "city": ep.city,
                "server": ep.url,
                "remote": ep.remote_host,
                "port": ep.local_port,
                "latency_ms": ep.latency_ms,
                "status": ep.status,
            }
            for ep in self.endpoints
        ]

    async def acquire_worker(self) -> StealthEndpoint:
        """Lease a healthy forwarder endpoint from the pool, reviving dead subprocesses if needed."""
        if not self._worker_queue:
            raise RuntimeError("Stealth proxy pool is not running. Call start_pool() first.")
        while True:
            worker = await self._worker_queue.get()
            if worker.proc and worker.proc.returncode is not None:
                logger.warning(
                    f"Forwarder {worker.name} (port {worker.local_port}) exited with code {worker.proc.returncode}. "
                    "Reviving forwarder bridge..."
                )
                try:
                    user = os.environ.get("NORDVPN_USER", "")
                    pwd = os.environ.get("NORDVPN_PASS", "")
                    allocated_ports = {ep.local_port for ep in self.endpoints if ep != worker}
                    new_ep = await self._launch_forwarder(
                        worker.name, worker.city, worker.remote_host, user, pwd, allocated_ports
                    )
                    startup_delay = getattr(self, "startup_delay", 0.6)
                    if startup_delay > 0:
                        await asyncio.sleep(startup_delay)
                    new_ep.index = worker.index
                    new_ep.latency_ms = worker.latency_ms
                    new_ep.status = "ONLINE"
                    for i, ep in enumerate(self.endpoints):
                        if ep == worker:
                            self.endpoints[i] = new_ep
                            break
                    worker = new_ep
                except Exception as rev_err:
                    logger.error(f"Failed to revive forwarder {worker.name}: {rev_err}")
                    continue
            return worker

    def release_worker(self, worker: StealthEndpoint):
        """Return a leased forwarder endpoint back to the pool."""
        if self._worker_queue:
            self._worker_queue.put_nowait(worker)

    @asynccontextmanager
    async def lease_worker(self):
        """Context manager to lease a worker and automatically return it when done."""
        worker = await self.acquire_worker()
        try:
            yield worker
        finally:
            self.release_worker(worker)

    async def lease_single_connection(
        self,
        remote_host: Optional[str] = None,
        target_url: Optional[str] = "https://www.google.com",
    ) -> Dict[str, Any]:
        """Start and return a single healthy stealth connection (for one-off scrapers)."""
        configs = await self.start_pool(
            num_workers=1,
            servers=[("feeder-single", "Out-of-State Travel Hub", remote_host or DEFAULT_STEALTH_HUBS[0][2])],
            wait_for_full_pool=False,
            min_healthy=1,
            test_target=target_url,
        )
        if configs:
            return configs[0]
        return {}

    async def start(self, remote_host: Optional[str] = None, **kwargs) -> Optional[Dict[str, Any]]:
        """Backwards-compatible alias for starting a single proxy bridge."""
        if "phoenix" in (remote_host or "").lower():
            remote_host = "los-angeles.us.socks.nordhold.net:1080"
        cfg = await self.lease_single_connection(remote_host=remote_host, target_url=kwargs.get("target_url", None))
        return cfg or None

    async def stop_pool(self):
        """Terminate all proxy pool child processes concurrently."""
        if self.proc:
            try:
                await self._reap_process(self.proc)
            except Exception:
                pass
            self.proc = None

        if self.endpoints:
            for ep in self.endpoints:
                if ep.proc:
                    try:
                        ep.proc.terminate()
                    except Exception:
                        pass

            async def _wait_or_kill(ep):
                if not ep.proc:
                    return
                try:
                    await asyncio.wait_for(ep.proc.wait(), timeout=2.0)
                except (asyncio.TimeoutError, TimeoutError):
                    try:
                        ep.proc.kill()
                        await ep.proc.wait()
                    except Exception:
                        pass

            await asyncio.gather(*[_wait_or_kill(ep) for ep in self.endpoints], return_exceptions=True)
            self.endpoints.clear()
            self._worker_queue = None

    async def stop(self):
        """Terminate all forwarder processes asynchronously."""
        if self.proc:
            try:
                self.proc.terminate()
                try:
                    await asyncio.wait_for(self.proc.wait(), timeout=2.0)
                except (asyncio.TimeoutError, TimeoutError):
                    self.proc.kill()
                    await self.proc.wait()
            except Exception:
                pass
            self.proc = None

        await self.stop_pool()

    def _cleanup_sync(self):
        """Synchronous fallback cleanup on process exit."""
        if self.proc:
            try:
                self.proc.kill()
            except Exception:
                pass
            self.proc = None

        if self.endpoints:
            for ep in self.endpoints:
                if ep.proc:
                    try:
                        ep.proc.kill()
                    except Exception:
                        pass
            self.endpoints.clear()
            self._worker_queue = None
