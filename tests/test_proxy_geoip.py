"""
Unit tests for Proxy GeoIP and African / AFRINIC Subnet Blacklisting.
Verifies that IP blocks registered to foreign / African subnets (196.0.0.0/8, 102.0.0.0/8, etc.)
are rejected at DNS resolution and pre-flight GeoIP stages.
"""

import asyncio
import os
import unittest
from unittest.mock import patch, MagicMock
import urllib.request
import json
import io

from src.stealth_connection import (
    is_blacklisted_subnet,
    StealthConnectionManager,
    DEFAULT_STEALTH_HUBS,
    STATIC_CANDIDATE_STEALTH_SERVERS,
    fetch_nordvpn_candidate_servers,
)


class TestProxyGeoIPAndBlacklist(unittest.TestCase):

    def test_is_blacklisted_subnet_afrinic_prefixes(self):
        """Verify is_blacklisted_subnet flags African and foreign subnets."""
        # 196.247.4.27 (NordVPN LA node leased from AFRINIC FIBERSA)
        self.assertTrue(is_blacklisted_subnet("196.247.4.27"))
        self.assertTrue(is_blacklisted_subnet("196.196.27.147"))
        self.assertTrue(is_blacklisted_subnet("102.165.32.1"))
        self.assertTrue(is_blacklisted_subnet("105.112.4.5"))
        self.assertTrue(is_blacklisted_subnet("41.203.4.1"))

    def test_is_blacklisted_subnet_allows_legitimate_us_ips(self):
        """Verify is_blacklisted_subnet allows clean US IPs and local loopback."""
        self.assertFalse(is_blacklisted_subnet("127.0.0.1"))
        self.assertFalse(is_blacklisted_subnet("149.248.55.1"))
        self.assertFalse(is_blacklisted_subnet("173.239.192.1"))
        self.assertFalse(is_blacklisted_subnet("23.106.56.2"))
        self.assertFalse(is_blacklisted_subnet(""))
        self.assertFalse(is_blacklisted_subnet(None))

    def test_default_stealth_hubs_contain_no_blacklisted_ips_or_decommissioned_hubs(self):
        """Verify DEFAULT_STEALTH_HUBS contains no decommissioned AFRINIC hubs."""
        for name, city, host in DEFAULT_STEALTH_HUBS:
            self.assertNotIn("los-angeles", host.lower(), f"Decommissioned LA hub found in default hubs: {name} ({host})")
            self.assertNotIn("socks-us61", host.lower(), f"Decommissioned socks-us61 found in default hubs: {name} ({host})")
            self.assertNotIn("socks-us68", host.lower(), f"Decommissioned socks-us68 found in default hubs: {name} ({host})")

    def test_static_candidates_contain_no_decommissioned_hosts(self):
        """Verify STATIC_CANDIDATE_STEALTH_SERVERS contains no decommissioned AFRINIC hosts."""
        for host in STATIC_CANDIDATE_STEALTH_SERVERS:
            self.assertNotIn("socks-us61", host.lower())
            self.assertNotIn("socks-us68", host.lower())
            self.assertNotIn("los-angeles", host.lower())

    @patch.dict("os.environ", {"STEALTH_STARTUP_DELAY": "0.0"})
    def test_verify_endpoint_geoip_zero_delay_bypass(self):
        """Verify verify_endpoint_geoip returns instantaneous mock result in zero-delay test mode."""
        async def run():
            return await StealthConnectionManager.verify_endpoint_geoip(port=56001)

        is_us, country, ip = asyncio.run(run())
        self.assertTrue(is_us)
        self.assertEqual(country, "United States")
        self.assertEqual(ip, "127.0.0.1")

    @patch.dict("os.environ", {"STEALTH_STARTUP_DELAY": "0.6"})
    @patch("urllib.request.build_opener")
    def test_verify_endpoint_geoip_live_probe_us(self, mock_build_opener):
        """Verify verify_endpoint_geoip parses US response correctly."""
        mock_resp = MagicMock()
        mock_resp.getcode.return_value = 200
        mock_resp.read.return_value = json.dumps({
            "country": "United States",
            "countryCode": "US",
            "query": "173.239.192.10",
        }).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        mock_opener = MagicMock()
        mock_opener.open.return_value = mock_resp
        mock_build_opener.return_value = mock_opener

        async def run():
            return await StealthConnectionManager.verify_endpoint_geoip(port=56001)

        is_us, country, ip = asyncio.run(run())
        self.assertTrue(is_us)
        self.assertEqual(country, "United States")
        self.assertEqual(ip, "173.239.192.10")

    @patch.dict("os.environ", {"STEALTH_STARTUP_DELAY": "0.6"})
    @patch("urllib.request.build_opener")
    def test_verify_endpoint_geoip_live_probe_foreign_flagged(self, mock_build_opener):
        """Verify verify_endpoint_geoip detects non-US IP and flags is_us=False."""
        mock_resp = MagicMock()
        mock_resp.getcode.return_value = 200
        mock_resp.read.return_value = json.dumps({
            "country": "South Africa",
            "countryCode": "ZA",
            "query": "196.247.4.27",
        }).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        mock_opener = MagicMock()
        mock_opener.open.return_value = mock_resp
        mock_build_opener.return_value = mock_opener

        async def run():
            return await StealthConnectionManager.verify_endpoint_geoip(port=56001)

        is_us, country, ip = asyncio.run(run())
        self.assertFalse(is_us)
        self.assertEqual(country, "South Africa")
        self.assertEqual(ip, "196.247.4.27")

    @patch("socket.gethostbyname")
    def test_verify_socks5_detailed_blocks_afrinic_ip(self, mock_dns):
        """Verify verify_socks5_detailed detects AFRINIC IP and immediately rejects with AFRINIC_BLOCKED."""
        mock_dns.return_value = "196.247.4.27"
        ok, msg, cat = StealthConnectionManager.verify_socks5_detailed(
            "bad-hub.nordhold.net:1080", username="u", password="p"
        )
        self.assertFalse(ok)
        self.assertEqual(cat, "AFRINIC_BLOCKED")
        self.assertIn("blacklisted foreign/African subnet", msg)


if __name__ == "__main__":
    unittest.main()

