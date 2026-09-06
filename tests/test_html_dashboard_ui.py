"""
UI Integration & JavaScript Syntax Validation Test Suite for STR Price Advisor Dashboard.

Prevents regressions where broken JavaScript syntax, unclosed braces/quotes,
or undefined event handlers (e.g. switchTab, filterComps) break the user interface.
"""

import asyncio
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from playwright.async_api import async_playwright

from src.html_generator import HTMLDashboardGenerator


class TestHTMLDashboardUI(unittest.TestCase):
    """Verifies that generated HTML dashboards contain syntactically valid JS and functional UI tabs."""

    @classmethod
    def setUpClass(cls):
        cls.html_path = Path("docs/index.html")
        if not cls.html_path.exists():
            generator = HTMLDashboardGenerator()
            generator.generate()

        cls.html_content = cls.html_path.read_text(encoding="utf-8")

    def test_embedded_javascript_syntax_via_node(self):
        """
        Extract all embedded <script> tags and validate syntax via node --check.
        Guarantees that syntax errors like 'Unexpected end of input' or unclosed braces fail the build.
        """
        node_bin = shutil.which("node")
        scripts = re.findall(r"<script(?:\s+[^>]*)?>(.*?)</script>", self.html_content, re.DOTALL)
        self.assertGreater(len(scripts), 0, "No <script> blocks found in dashboard HTML")

        # Fallback balanced brace validation
        for idx, script_text in enumerate(scripts):
            trimmed = script_text.strip()
            if not trimmed:
                continue

            open_braces = trimmed.count("{")
            close_braces = trimmed.count("}")
            self.assertEqual(
                open_braces,
                close_braces,
                f"Mismatched curly braces in script #{idx}: {open_braces} open vs {close_braces} close",
            )

        if not node_bin:
            self.skipTest("Node.js not installed in PATH; skipped node --check execution.")

        with tempfile.TemporaryDirectory() as tmpdir:
            for idx, script_text in enumerate(scripts):
                trimmed = script_text.strip()
                if not trimmed:
                    continue

                script_file = Path(tmpdir) / f"script_{idx}.js"
                script_file.write_text(trimmed, encoding="utf-8")

                res = subprocess.run(
                    [node_bin, "--check", str(script_file)],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(
                    res.returncode,
                    0,
                    f"JavaScript syntax error detected in script block #{idx}:\n{res.stderr}",
                )

    def test_headless_browser_tabs_and_event_handlers(self):
        """
        Launch headless Chromium via Playwright, load docs/index.html, and click every tab button.
        Monitors pageerror and console.error events to ensure switchTab and all handlers work.
        """
        async def run_browser_check():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()

                captured_errors = []
                page.on("pageerror", lambda err: captured_errors.append(f"PAGE_ERROR: {err}"))
                page.on("console", lambda msg: captured_errors.append(f"CONSOLE_ERROR: {msg.text}") if msg.type == "error" else None)

                file_uri = f"file://{self.html_path.resolve()}"
                await page.goto(file_uri)
                await page.wait_for_load_state("domcontentloaded")

                # Verify page title
                title = await page.title()
                self.assertIn("Villa del Sol", title)

                # Ensure tabs navigation is present
                tab_btns = await page.query_selector_all(".tab-btn")
                self.assertGreaterEqual(len(tab_btns), 8, "Expected at least 8 navigation tabs in dashboard")

                # Click every tab and verify that switchTab properly activates the corresponding container
                tab_ids = [
                    "pricing",
                    "comparison",
                    "comps",
                    "market-sales",
                    "calendar",
                    "reservations",
                    "revenue",
                    "methodology",
                    "debug",
                ]

                for tid in tab_ids:
                    btn = await page.query_selector(f"button[onclick*=\"'{tid}'\"]")
                    self.assertIsNotNone(btn, f"Tab button for '{tid}' not found in DOM")
                    await btn.click()
                    await page.wait_for_timeout(40)

                    target_tab = await page.query_selector(f"#tab-{tid}")
                    self.assertIsNotNone(target_tab, f"Tab content container '#tab-{tid}' not found")
                    is_active = await target_tab.evaluate("el => el.classList.contains('active')")
                    self.assertTrue(is_active, f"Tab '#tab-{tid}' failed to activate on click")

                # Test search filters to ensure client-side filter scripts don't throw runtime exceptions
                btn_comps = await page.query_selector("button[onclick*=\"'comps'\"]")
                if btn_comps:
                    await btn_comps.click()
                    await page.wait_for_timeout(50)
                    comp_search = await page.query_selector("#compSearch")
                    if comp_search:
                        await comp_search.fill("Scottsdale")
                        await page.wait_for_timeout(30)

                btn_sales = await page.query_selector("button[onclick*=\"'market-sales'\"]")
                if btn_sales:
                    await btn_sales.click()
                    await page.wait_for_timeout(50)
                    sales_search = await page.query_selector("#salesSearch")
                    if sales_search:
                        await sales_search.fill("7BR")
                        await page.wait_for_timeout(30)

                await browser.close()
                return captured_errors

        errors = asyncio.run(run_browser_check())
        self.assertEqual(
            len(errors),
            0,
            f"JavaScript/Browser errors detected during UI dashboard interaction:\n" + "\n".join(errors),
        )


if __name__ == "__main__":
    unittest.main()

