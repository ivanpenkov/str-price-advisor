"""
Proxy Manager for STR Price Advisor.
Bridges Playwright to NordVPN authenticated SOCKS5 proxy pool.
Enforces proxy usage for all external web scraping to prevent IP throttling.
"""

import atexit
import asyncio
import logging
import os
import socket
import sys
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger("proxy_manager")


def get_free_port() -> int:
    """Find an available unallocated port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


class ProxyManager:
    """Manages local background forwarder for authenticated SOCKS5 proxies (NordVPN)."""

    def __init__(self, port: Optional[int] = None, required: bool = True):
        self.port = port or get_free_port()
        self.required = required
        self.proc: Optional[asyncio.subprocess.Process] = None
        self._load_env()
        atexit.register(self._cleanup_sync)

    def _load_env(self):
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

    @property
    def is_configured(self) -> bool:
        """Returns True if NordVPN credentials exist in environment or .env."""
        return bool(os.getenv("NORDVPN_USER") and os.getenv("NORDVPN_PASS"))

    async def start(self) -> Optional[Dict[str, str]]:
        """
        Start local forwarder bridge.
        Returns Playwright proxy configuration dict.
        Raises RuntimeError if required=True and proxy credentials or forwarder are unavailable.
        """
        if not self.is_configured:
            if self.required:
                msg = (
                    "❌ [PROXY REQUIRED] Web scraping was blocked because NordVPN proxy credentials "
                    "were not found in .env! To protect your residential IP and prevent Airbnb 503 "
                    "throttling, all web scraping must route through NordVPN. "
                    "Please ensure NORDVPN_USER and NORDVPN_PASS are set in .env."
                )
                print(f"\n{msg}\n")
                raise RuntimeError(msg)
            return None

        user = os.getenv("NORDVPN_USER")
        pwd = os.getenv("NORDVPN_PASS")
        remote_host = os.getenv("NORDVPN_SERVER", "phoenix.us.socks.nordhold.net:1080")

        try:
            self.proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "pproxy",
                "-l", f"http://127.0.0.1:{self.port}",
                "-r", f"socks5://{remote_host}#{user}:{pwd}",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.sleep(0.8)
            print(f"  🛡️ NordVPN Proxy Enforced: Routing web traffic through {remote_host} (port {self.port})")

            # Propagate to standard environment variables for requests/urllib/aiohttp
            self._old_env = {
                k: os.environ.get(k)
                for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "NO_PROXY", "no_proxy")
            }
            proxy_url = f"http://127.0.0.1:{self.port}"
            for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
                os.environ[k] = proxy_url
            for k in ("NO_PROXY", "no_proxy"):
                os.environ[k] = "localhost,127.0.0.1"

            return {"server": proxy_url}
        except Exception as e:
            if self.required:
                raise RuntimeError(f"Failed to start mandatory NordVPN proxy bridge: {e}")
            logger.error(f"Failed to start proxy forwarder: {e}")
            return None

    def _restore_env(self):
        """Restore environment variables to pre-proxy state."""
        if hasattr(self, "_old_env") and self._old_env:
            for k, v in self._old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            self._old_env = {}

    async def stop(self):
        """Terminate local forwarder process asynchronously."""
        self._restore_env()
        if self.proc:
            try:
                self.proc.terminate()
                await self.proc.wait()
            except Exception:
                pass
            self.proc = None

    def _cleanup_sync(self):
        """Synchronous fallback cleanup on process exit."""
        self._restore_env()
        if self.proc:
            try:
                self.proc.kill()
            except Exception:
                pass
            self.proc = None
