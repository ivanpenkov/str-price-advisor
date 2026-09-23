"""
UI Integration & JavaScript Syntax Validation Test Suite for STR Price Advisor Dashboard.

Prevents regressions where broken JavaScript syntax, unclosed braces/quotes,
or undefined event handlers (e.g. switchTab, filterComps) break the user interface.
"""

import asyncio
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path
from playwright.async_api import async_playwright

from src.html_generator import HTMLDashboardGenerator


class TestHTMLDashboardUI(unittest.TestCase):
    """Verifies that generated HTML dashboards contain syntactically valid JS and functional UI tabs."""

    @classmethod
    def setUpClass(cls):
        cls.html_path = Path("docs/index.html")
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

                # Ensure tabs navigation is present (exactly 10 tabs)
                tab_btns = await page.query_selector_all(".tab-btn")
                self.assertEqual(len(tab_btns), 10, f"Expected exactly 10 navigation tabs in dashboard, found {len(tab_btns)}")

                # Click every tab and verify that switchTab properly activates the corresponding container
                tab_ids = [
                    "pricing",
                    "reviews",
                    "streamline",
                    "comps",
                    "market-sales",
                    "calendar",
                    "reservations",
                    "revenue",
                    "comparison",
                    "system",
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

                # Test Proposed Prices toggle buttons on pricing tab
                btn_pricing = await page.query_selector("button[onclick*=\"'pricing'\"]")
                if btn_pricing:
                    await btn_pricing.click()
                    await page.wait_for_timeout(30)
                    btn_med = await page.query_selector("#btnSuggestMed")
                    if btn_med:
                        await btn_med.click()
                        await page.wait_for_timeout(30)
                    btn_avg = await page.query_selector("#btnSuggestAvg")
                    if btn_avg:
                        await btn_avg.click()
                        await page.wait_for_timeout(30)

                    # Verify filterProposedOpenCalendar is checked on load and hides closed rows
                    is_proposed_checked = await page.is_checked("#filterProposedOpenCalendar")
                    self.assertTrue(is_proposed_checked, "#filterProposedOpenCalendar should be checked by default on page load")

                    total_closed = await page.evaluate("() => document.querySelectorAll('.proposed-price-row[data-calendar-open=\"false\"]').length")
                    if total_closed == 0:
                        # Kivoya booking calendar is open across the entire 12-month horizon.
                        # Inject a test closed row to exercise the toggle filter logic.
                        await page.evaluate("""() => {
                            const sampleRow = document.querySelector('.proposed-price-row');
                            if (sampleRow && sampleRow.parentElement) {
                                const tr = document.createElement('tr');
                                tr.className = 'proposed-price-row';
                                tr.setAttribute('data-calendar-open', 'false');
                                tr.style.display = 'none';
                                sampleRow.parentElement.appendChild(tr);
                            }
                        }""")
                        total_closed = await page.evaluate("() => document.querySelectorAll('.proposed-price-row[data-calendar-open=\"false\"]').length")
                    self.assertGreater(total_closed, 0, "Expected closed calendar rows to exist in proposed prices table")

                    visible_closed_init = await page.evaluate("() => Array.from(document.querySelectorAll('.proposed-price-row[data-calendar-open=\"false\"]')).filter(r => r.style.display !== 'none').length")
                    self.assertEqual(visible_closed_init, 0, "Closed calendar rows should be hidden on load when filterProposedOpenCalendar is checked")

                    # Uncheck filterProposedOpenCalendar -> closed rows should become visible
                    await page.click("#filterProposedOpenCalendar")
                    await page.wait_for_timeout(30)
                    visible_closed_uncheck = await page.evaluate("() => Array.from(document.querySelectorAll('.proposed-price-row[data-calendar-open=\"false\"]')).filter(r => r.style.display !== 'none').length")
                    self.assertEqual(visible_closed_uncheck, total_closed, "Closed calendar rows should become visible when filterProposedOpenCalendar is unchecked")

                    # Check filterProposedOpenCalendar -> closed rows should become hidden again
                    await page.click("#filterProposedOpenCalendar")
                    await page.wait_for_timeout(30)
                    visible_closed_recheck = await page.evaluate("() => Array.from(document.querySelectorAll('.proposed-price-row[data-calendar-open=\"false\"]')).filter(r => r.style.display !== 'none').length")
                    self.assertEqual(visible_closed_recheck, 0, "Closed calendar rows should be hidden when filterProposedOpenCalendar is re-checked")

                    # Toggle lower table's filterOpenCalendar -> must NOT affect proposed prices table
                    await page.click("#filterOpenCalendar")
                    await page.wait_for_timeout(30)
                    visible_closed_isolated = await page.evaluate("() => Array.from(document.querySelectorAll('.proposed-price-row[data-calendar-open=\"false\"]')).filter(r => r.style.display !== 'none').length")
                    self.assertEqual(visible_closed_isolated, 0, "Toggling lower table's filterOpenCalendar must not alter proposed price rows")

                await browser.close()
                return captured_errors

        errors = asyncio.run(run_browser_check())
        self.assertEqual(
            len(errors),
            0,
            f"JavaScript/Browser errors detected during UI dashboard interaction:\n" + "\n".join(errors),
        )

    def test_reservation_intelligence_ui_elements(self):
        """Verify that newly integrated reservation intelligence elements render properly in HTML."""
        # 1. Main table historical average header
        self.assertIn("<th>Historical AVG</th>", self.html_content)
        self.assertNotIn("<th>Historical Track Record</th>", self.html_content)

        # 2. Reservations tab: Annual weekend vs midweek shift card and table
        self.assertIn("Annual Weekend vs. Midweek Performance &amp; Strategy Shift", self.html_content.replace("&", "&amp;").replace("&amp;amp;", "&amp;"))
        self.assertIn("res-shift-card", self.html_content)
        self.assertIn("res-shift-table", self.html_content)
        self.assertIn("<th><span style=\"color:#818cf8; font-size:0.9rem;\">●</span> Weekend ADR</th>", self.html_content)
        self.assertIn("<th><span style=\"color:#fb923c; font-size:0.9rem;\">●</span> Midweek ADR</th>", self.html_content)
        self.assertIn("<th>Weekend Premium (%)</th>", self.html_content)
        self.assertNotIn("Realized Weekend ADR", self.html_content)
        self.assertNotIn("Realized Midweek ADR", self.html_content)
        self.assertNotIn("<th>ADR Premium (%)</th>", self.html_content)

        # 2b. Calendar 12-month navigation buttons
        self.assertIn("onclick=\"calNavigate(-12)\"", self.html_content)
        self.assertIn("onclick=\"calNavigate(12)\"", self.html_content)
        self.assertIn("◀ Prev 12 Months", self.html_content)
        self.assertIn("Next 12 Months ▶", self.html_content)
        self.assertNotIn("Prev 6 Months", self.html_content)
        self.assertNotIn("Next 6 Months", self.html_content)

        # 3. Reservations tab & Market demand tab: Monthly Advance Booking Horizons (Villa del Sol & Comps)
        self.assertIn("Monthly Advance Booking Horizons (Villa del Sol Empirical Pace)", self.html_content)
        self.assertIn("Monthly Advance Booking Horizons (Comps)", self.html_content)

        # 4. Comp row subtable booking pace & track record banner
        self.assertIn("subtable-intel-banner", self.html_content)
        self.assertIn("Booking Window &amp; Pace", self.html_content.replace("&", "&amp;").replace("&amp;amp;", "&amp;"))

        # 5. Parent row data-hist-med and copy schedule script logic
        self.assertIn("data-hist-med=", self.html_content)
        self.assertIn(". Based on history: ", self.html_content)

        # 6. Proposed Prices table, mode buttons, checked open calendar filter, and copy button
        self.assertIn('id="proposed-prices-table"', self.html_content)
        self.assertIn('id="filterProposedOpenCalendar"', self.html_content)
        self.assertIn('id="filterProposedOpenCalendar" checked', self.html_content)
        self.assertIn('id="btnSuggestAvg"', self.html_content)
        self.assertIn('id="btnSuggestMed"', self.html_content)
        self.assertIn('id="btnCopyProposed"', self.html_content)
        self.assertIn("copyProposedPrices", self.html_content)

    def test_render_table_rows_empty_and_populated_historical_benchmarks(self):
        """Verify that _render_table_rows handles segments with empty/None historical benchmarks without error and scopes data-hist-med correctly."""
        gen = HTMLDashboardGenerator()
        sample_segments = [
            {
                "check_in": "2026-10-01",
                "check_out": "2026-10-04",
                "segment_type": "weekend",
                "nights": 3,
                "price_diff_percent": 0.0,
                "recommended_base_nightly": 500.0,
                "comp_p50_eff": 500.0,
                "comp_target_eff": 500.0,
                "our_base_nightly": 500.0,
                "historical_benchmark": None,  # Row 0 with None
            },
            {
                "check_in": "2026-10-04",
                "check_out": "2026-10-08",
                "segment_type": "midweek",
                "nights": 4,
                "price_diff_percent": 0.0,
                "recommended_base_nightly": 400.0,
                "comp_p50_eff": 400.0,
                "comp_target_eff": 400.0,
                "our_base_nightly": 400.0,
                "historical_benchmark": {
                    "sample_count": 3,
                    "min_rate": 350.0,
                    "max_rate": 450.0,
                    "median_rate": 400.0,
                    "avg_rate": 395.0,
                    "flag_label": "Aligned",
                },  # Row 1 with data
            },
            {
                "check_in": "2026-10-08",
                "check_out": "2026-10-11",
                "segment_type": "weekend",
                "nights": 3,
                "price_diff_percent": 0.0,
                "recommended_base_nightly": 550.0,
                "comp_p50_eff": 550.0,
                "comp_target_eff": 550.0,
                "our_base_nightly": 550.0,
                "historical_benchmark": {},  # Row 2 with empty dict (must not leak row 1's median 400)
            },
        ]
        html_rows = gen._render_table_rows(sample_segments, prefix="test-row")
        # Row 0: empty
        self.assertIn('id="parent-test-row-0"', html_rows)
        self.assertIn('data-hist-count="0"', html_rows)
        self.assertIn('data-hist-med="0"', html_rows)
        # Row 1: populated
        self.assertIn('id="parent-test-row-1"', html_rows)
        self.assertIn('data-hist-count="3"', html_rows)
        self.assertIn('data-hist-med="400"', html_rows)
        self.assertIn('title="3 confirmed prior bookings', html_rows)
        self.assertIn('>$395</strong>', html_rows)
        # Row 2: empty (verifies no cross-row leakage from row 1)
        self.assertIn('id="parent-test-row-2"', html_rows)
        self.assertIn('data-hist-count="0"', html_rows)
        self.assertIn('data-hist-med="0"', html_rows)

    def test_historical_avg_color_thresholds(self):
        """
        Verify that the value in the "Historical AVG" column is colored:
        - Green (#34d399) if >= 5% higher than Kivoya base rate
        - Red (#f87171) if <= 5% lower than Kivoya base rate
        - White (#f8fafc) if within +/- 5%
        And that "Effective Total" remains clean white (#f1f5f9).
        """
        gen = HTMLDashboardGenerator()
        sample_segments = [
            {
                "check_in": "2029-10-01",
                "check_out": "2029-10-04",
                "segment_type": "weekend",
                "nights": 3,
                "price_diff_percent": 0.0,
                "recommended_base_nightly": 500.0,
                "comp_p50_eff": 500.0,
                "comp_target_eff": 500.0,
                "our_base_nightly": 500.0,
                "our_effective_nightly": 600.0,
                "historical_benchmark": {
                    "sample_count": 3,
                    "avg_rate": 600.0,  # +20% -> Green #34d399
                    "median_rate": 600.0,
                    "min_rate": 550.0,
                    "max_rate": 650.0,
                },
            },
            {
                "check_in": "2029-10-04",
                "check_out": "2029-10-08",
                "segment_type": "midweek",
                "nights": 4,
                "price_diff_percent": 0.0,
                "recommended_base_nightly": 500.0,
                "comp_p50_eff": 500.0,
                "comp_target_eff": 500.0,
                "our_base_nightly": 500.0,
                "our_effective_nightly": 450.0,
                "historical_benchmark": {
                    "sample_count": 4,
                    "avg_rate": 450.0,  # -10% -> Red #f87171
                    "median_rate": 450.0,
                    "min_rate": 400.0,
                    "max_rate": 500.0,
                },
            },
            {
                "check_in": "2029-10-08",
                "check_out": "2029-10-11",
                "segment_type": "weekend",
                "nights": 3,
                "price_diff_percent": 0.0,
                "recommended_base_nightly": 500.0,
                "comp_p50_eff": 500.0,
                "comp_target_eff": 500.0,
                "our_base_nightly": 500.0,
                "our_effective_nightly": 510.0,
                "historical_benchmark": {
                    "sample_count": 2,
                    "avg_rate": 510.0,  # +2% -> White #f8fafc
                    "median_rate": 510.0,
                    "min_rate": 500.0,
                    "max_rate": 520.0,
                },
            },
            {
                "check_in": "2029-10-11",
                "check_out": "2029-10-15",
                "segment_type": "midweek",
                "nights": 4,
                "price_diff_percent": 0.0,
                "recommended_base_nightly": 500.0,
                "comp_p50_eff": 500.0,
                "comp_target_eff": 500.0,
                "our_base_nightly": 500.0,
                "our_effective_nightly": 525.0,
                "historical_benchmark": {
                    "sample_count": 5,
                    "avg_rate": 525.0,  # Exactly +5% -> Green #34d399
                    "median_rate": 525.0,
                    "min_rate": 500.0,
                    "max_rate": 550.0,
                },
            },
            {
                "check_in": "2029-10-15",
                "check_out": "2029-10-18",
                "segment_type": "weekend",
                "nights": 3,
                "price_diff_percent": 0.0,
                "recommended_base_nightly": 500.0,
                "comp_p50_eff": 500.0,
                "comp_target_eff": 500.0,
                "our_base_nightly": 500.0,
                "our_effective_nightly": 475.0,
                "historical_benchmark": {
                    "sample_count": 3,
                    "avg_rate": 475.0,  # Exactly -5% -> Red #f87171
                    "median_rate": 475.0,
                    "min_rate": 450.0,
                    "max_rate": 500.0,
                },
            },
        ]
        html_rows = gen._render_table_rows(sample_segments, prefix="color-row")
        
        # Row 0: +20% -> Green #34d399 in Historical AVG
        self.assertIn('id="track-color-row-0"', html_rows)
        self.assertIn('<strong style="color:#34d399;" title="3 confirmed prior bookings within ±15 days in 2022–2026 (Aligned, range: $550–$650, med: $600)">$600</strong>', html_rows)
        
        # Row 1: -10% -> Red #f87171 in Historical AVG
        self.assertIn('id="track-color-row-1"', html_rows)
        self.assertIn('<strong style="color:#f87171;" title="4 confirmed prior bookings within ±15 days in 2022–2026 (Aligned, range: $400–$500, med: $450)">$450</strong>', html_rows)
        
        # Row 2: +2% -> White #f8fafc in Historical AVG
        self.assertIn('id="track-color-row-2"', html_rows)
        self.assertIn('<strong style="color:#f8fafc;" title="2 confirmed prior bookings within ±15 days in 2022–2026 (Aligned, range: $500–$520, med: $510)">$510</strong>', html_rows)
        
        # Row 3: Exactly +5% -> Green #34d399 in Historical AVG
        self.assertIn('id="track-color-row-3"', html_rows)
        self.assertIn('<strong style="color:#34d399;" title="5 confirmed prior bookings within ±15 days in 2022–2026 (Aligned, range: $500–$550, med: $525)">$525</strong>', html_rows)
        
        # Row 4: Exactly -5% -> Red #f87171 in Historical AVG
        self.assertIn('id="track-color-row-4"', html_rows)
        self.assertIn('<strong style="color:#f87171;" title="3 confirmed prior bookings within ±15 days in 2022–2026 (Aligned, range: $450–$500, med: $475)">$475</strong>', html_rows)

        # Effective Total values must be white #f1f5f9
        self.assertIn('id="eff-color-row-0"', html_rows)
        self.assertIn('<strong style="color:#f1f5f9;">$600</strong>', html_rows)

    def test_competitor_policy_benchmarks_ui(self):
        """Verify that Competitor Policy & House Rules Benchmarks render properly in Comps tab."""
        # 1. Policy container and heading
        self.assertIn("comps-policy-stats-container", self.html_content)
        self.assertIn("Competitor Policy &amp; House Rules Benchmarks", self.html_content.replace("&", "&amp;").replace("&amp;amp;", "&amp;"))
        self.assertIn('id="policyCohortCount"', self.html_content)

        # 2. All 8 policy tables exist
        for dim in ("check_in", "check_out", "deposit", "noise", "pool_heating", "min_age", "pets", "events"):
            self.assertIn(f'id="policy-table-{dim}"', self.html_content)

        # 3. Client data payload and update function
        self.assertIn("const COMP_POLICIES_DATA =", self.html_content)
        self.assertIn("function updateCompsPolicyStats()", self.html_content)

        # 4. Sample listing links with tooltip and target="_blank"
        self.assertIn("policy-sample-link", self.html_content)
        self.assertIn('target="_blank"', self.html_content)

        # 5. Comp-card data-listing-id attribute
        self.assertIn('data-listing-id=', self.html_content)

    def test_streamline_pms_tab_ui(self):
        """Verify that dedicated Streamline PMS tab and components render correctly in HTML."""
        # 1. Navigation button and tab content container
        self.assertIn("switchTab('streamline')", self.html_content)
        self.assertIn('id="tab-streamline"', self.html_content)

        # 2. Property Configuration Card
        self.assertIn("Property Configuration &amp; Terms (Kivoya / Streamline VRS)", self.html_content.replace("&", "&amp;").replace("&amp;amp;", "&amp;"))
        self.assertIn("108169", self.html_content)
        self.assertIn("6 BR / 5 BA", self.html_content)
        self.assertIn("$550.00", self.html_content)
        self.assertIn("12.52%", self.html_content)
        self.assertIn("82.0%", self.html_content)

        # 3. Rate Schedule table, API update timestamps, and historical snapshot scrubber
        self.assertIn('id="streamline-pricing-table"', self.html_content)
        self.assertIn('id="streamline-pricing-tbody"', self.html_content)
        self.assertIn('id="streamlineSnapshotSlider"', self.html_content)
        self.assertIn('id="streamlineSnapshotBadge"', self.html_content)
        self.assertIn('id="streamlineLastSyncBadge"', self.html_content)
        self.assertIn("Last API Update:", self.html_content)
        self.assertIn('id="streamlineSnapshotTimestamp"', self.html_content)
        self.assertIn('id="streamlineTableSnapshotLabel"', self.html_content)
        self.assertIn('id="streamlineTableLastUpdate"', self.html_content)
        self.assertIn("Last Streamline Sync", self.html_content)
        self.assertIn('id="btnCopyStreamline"', self.html_content)
        self.assertIn("copyStreamlinePrices", self.html_content)
        self.assertIn("const STREAMLINE_SNAPSHOTS =", self.html_content)
        self.assertIn("updated_at_str", self.html_content)

        # 4. Streamline Calendar Blackouts & Bookings
        self.assertIn("Streamline PMS Calendar Blackouts &amp; Confirmed Bookings", self.html_content.replace("&", "&amp;").replace("&amp;amp;", "&amp;"))
        self.assertIn("Confirmed Stay", self.html_content)
        self.assertIn("Reservation #", self.html_content)

    def test_removed_methodology_and_debug_tabs(self):
        """Verify that methodology and debug tabs and buttons are completely removed from HTML."""
        self.assertNotIn("switchTab('methodology')", self.html_content)
        self.assertNotIn("switchTab('debug')", self.html_content)
        self.assertNotIn('id="tab-methodology"', self.html_content)
        self.assertNotIn('id="tab-debug"', self.html_content)
        self.assertNotIn("methodology-grid", self.html_content)
        self.assertNotIn(".method-card", self.html_content)
        self.assertNotIn(".formula-box", self.html_content)
        self.assertNotIn("'methodology'", self.html_content)
        self.assertNotIn("'debug'", self.html_content)

    def test_mobile_responsive_viewport_playwright(self):
        """Verify that dashboard renders properly on mobile viewport (390x844) without horizontal overflow."""
        async def run_mobile_check():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                # Emulate iPhone 14/15 screen
                page = await browser.new_page(viewport={"width": 390, "height": 844})

                captured_errors = []
                page.on("pageerror", lambda err: captured_errors.append(f"PAGE_ERROR: {err}"))
                page.on("console", lambda msg: captured_errors.append(f"CONSOLE_ERROR: {msg.text}") if msg.type == "error" else None)

                file_uri = f"file://{self.html_path.resolve()}"
                await page.goto(file_uri)
                await page.wait_for_load_state("domcontentloaded")

                self.assertEqual(len(captured_errors), 0, f"Errors on mobile load: {captured_errors}")

                # 1. Verify no horizontal overflow on mobile
                scroll_width = await page.evaluate("document.documentElement.scrollWidth")
                client_width = await page.evaluate("document.documentElement.clientWidth")
                self.assertLessEqual(scroll_width, client_width + 2, f"Mobile horizontal overflow detected: scrollWidth {scroll_width} > clientWidth {client_width}")

                # 2. Verify sticky navigation tabs on mobile
                nav_pos = await page.evaluate("getComputedStyle(document.querySelector('.tabs-nav')).position")
                self.assertEqual(nav_pos, "sticky", f"Expected .tabs-nav to be sticky on mobile, got {nav_pos}")

                # 3. Verify 2-column KPI grid on mobile
                kpi_cols = await page.evaluate("getComputedStyle(document.querySelector('.kpi-grid')).gridTemplateColumns.split(' ').length")
                self.assertEqual(kpi_cols, 2, f"Expected 2 columns for .kpi-grid on mobile, got {kpi_cols}")

                # 4. Test clicking reservation row to open mobile bottom sheet modal
                res_tab_btn = await page.query_selector("button.tab-btn[onclick*=\"switchTab('reservations')\"]")
                if res_tab_btn:
                    await res_tab_btn.click()
                    await page.wait_for_timeout(200)

                    first_res = await page.query_selector(".res-row")
                    if first_res:
                        await first_res.click()
                        await page.wait_for_timeout(200)
                        is_active = await page.evaluate("document.getElementById('resModalOverlay').classList.contains('active')")
                        self.assertTrue(is_active, "Reservation modal did not open on mobile")

                        # Verify bottom sheet alignment on mobile
                        overlay_align = await page.evaluate("getComputedStyle(document.getElementById('resModalOverlay')).alignItems")
                        self.assertEqual(overlay_align, "flex-end", f"Expected bottom sheet align-items: flex-end, got {overlay_align}")

                        # Close modal
                        await page.keyboard.press("Escape")
                        await page.wait_for_timeout(200)

                await browser.close()

        asyncio.run(run_mobile_check())

    def test_desktop_viewport_fidelity_playwright(self):
        """Verify that dashboard preserves multi-column tables and non-sticky desktop navigation on 1440x900."""
        async def run_desktop_check():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page(viewport={"width": 1440, "height": 900})

                file_uri = f"file://{self.html_path.resolve()}"
                await page.goto(file_uri)
                await page.wait_for_load_state("domcontentloaded")

                # 1. Verify desktop navigation is not sticky
                nav_pos = await page.evaluate("getComputedStyle(document.querySelector('.tabs-nav')).position")
                self.assertNotEqual(nav_pos, "sticky", f"Expected .tabs-nav NOT to be sticky on desktop, got {nav_pos}")

                # 2. Verify pricing table thead is visible on desktop
                thead_disp = await page.evaluate("getComputedStyle(document.querySelector('.pricing-table thead')).display")
                self.assertEqual(thead_disp, "table-header-group", f"Expected desktop thead display: table-header-group, got {thead_disp}")

                # 3. Verify multi-column KPI grid on desktop (> 2 columns)
                kpi_cols = await page.evaluate("getComputedStyle(document.querySelector('.kpi-grid')).gridTemplateColumns.split(' ').length")
                self.assertGreater(kpi_cols, 2, f"Expected >2 columns for .kpi-grid on desktop, got {kpi_cols}")

                await browser.close()

        asyncio.run(run_desktop_check())

    def test_mobile_ui_followup_enhancements(self):
        """Verify all 7 follow-up mobile optimizations and views enhancements."""
        # 1. Proposed prices table header and short dates
        self.assertIn('<th style="min-width:140px;">Dates</th>', self.html_content)
        self.assertIn("class=\"proposed-price-row\"", self.html_content)

        # 2. Channels tab AIRBNB (EST) header and short date pills
        self.assertIn("AIRBNB (EST)", self.html_content)
        self.assertIn("(Benchmark)", self.html_content)

        # 3. Streamline pricing table new column layout
        self.assertIn('<th style="min-width: 140px;">Dates</th>', self.html_content)
        self.assertIn('<th style="text-align:right;">Special</th>', self.html_content)
        self.assertIn('<th>Period Name</th>', self.html_content)

        # 4. Calendar defaults in HTML markup
        self.assertIn('id="calColorChannelBtn" class="cal-toggle-btn active"', self.html_content)
        self.assertIn('id="calRatesOffBtn" class="cal-toggle-btn active"', self.html_content)
        self.assertIn('id="calMonthsGrid" class="cal-months-grid cal-hide-rates"', self.html_content)

        # 5. YTD revenue same-day comparison logic
        from src.reservation_store import ReservationStore
        store = ReservationStore()
        rev_calc = store.calculate_cumulative_annual_revenue()
        kpis = rev_calc.get("kpis", {})
        self.assertGreater(kpis.get("ytd_revenue", 0), 0)
        self.assertGreater(kpis.get("prior_ytd_revenue", 0), 0)
        # Prior YTD revenue through today's day-of-year must be strictly less than full prior year total revenue
        py_total = kpis["by_year"][kpis["current_year"] - 1]["total_revenue"]
        self.assertLess(kpis["prior_ytd_revenue"], py_total, "Prior YTD revenue must be less than full prior year revenue")

        # 6. Playwright mobile viewport assertions for header, padding, and tab layouts
        async def run_mobile_followup():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page(viewport={"width": 390, "height": 844})
                file_uri = f"file://{self.html_path.resolve()}"
                await page.goto(file_uri)
                await page.wait_for_load_state("domcontentloaded")

                # Mobile Header: Title & primary badge hidden, Updated timestamp visible
                title_disp = await page.evaluate("getComputedStyle(document.querySelector('.property-title')).display")
                self.assertEqual(title_disp, "none", "Property title must be hidden on mobile")

                badge_primary_disp = await page.evaluate("getComputedStyle(document.querySelector('header .badge-primary')).display")
                self.assertEqual(badge_primary_disp, "none", "Header model badge must be hidden on mobile")

                # Full-width edge-to-edge
                body_padding_left = await page.evaluate("getComputedStyle(document.body).paddingLeft")
                self.assertEqual(body_padding_left, "0px", "Body padding-left must be 0px on mobile")

                # Revenue tab: stacked 1-column KPI cards
                rev_tab_btn = await page.query_selector("button.tab-btn[onclick*=\"switchTab('revenue')\"]")
                if rev_tab_btn:
                    await rev_tab_btn.click()
                    await page.wait_for_timeout(100)
                    rev_cols = await page.evaluate("getComputedStyle(document.querySelector('.rev-kpi-grid')).gridTemplateColumns.split(' ').length")
                    self.assertEqual(rev_cols, 1, f"Expected 1-column stacked KPI cards on mobile, got {rev_cols}")

                await browser.close()

        asyncio.run(run_mobile_followup())

    def test_platform_comparison_timestamps(self):
        """Verify _format_timestamp formatting and presence of sweep timestamps in generated HTML."""
        from src.html_generator import HTMLDashboardGenerator
        # 1. Format timestamp unit tests
        self.assertEqual(HTMLDashboardGenerator._format_timestamp(None), "Projected")
        self.assertEqual(HTMLDashboardGenerator._format_timestamp(""), "Projected")
        self.assertEqual(HTMLDashboardGenerator._format_timestamp("2026-09-06T15:35:16.335945"), "Sep 06, 2026 at 3:35 PM")
        self.assertEqual(HTMLDashboardGenerator._format_timestamp("2026-09-06T15:35:16.335945", short=True), "Sep 06, 15:35")

        # 2. In generated HTML
        html_content = self.html_path.read_text(encoding="utf-8")
        self.assertIn("Last Multi-Platform Sweep:", html_content)
        self.assertIn("Last Verified:", html_content)
        self.assertIn("data-updated-at=", html_content)
        self.assertIn("Platform rates last scraped:", html_content)

    def test_kivoya_dynamic_rate_synchronization(self):
        """Verify that _load_platform_comparisons dynamically recalculates Kivoya quote if catalog rate changed."""
        import json
        from unittest.mock import patch, MagicMock
        from src.html_generator import HTMLDashboardGenerator

        gen = HTMLDashboardGenerator(output_path=str(self.html_path))
        segment = {
            "check_in": "2026-09-13",
            "check_out": "2026-09-17",
            "nights": 4,
            "segment_type": "midweek",
            "is_calendar_open": True,
            "our_base_nightly": 399.0,  # New catalog rate
        }

        # Cached file has old rate 436.5
        mock_cached_comparison = {
            "check_in": "2026-09-13",
            "check_out": "2026-09-17",
            "nights": 4,
            "updated_at": "2026-09-06T15:35:16.335945",
            "airbnb": {"total_price": 3052.67, "available": True, "notes": "12.52%"},
            "vrbo": {"total_price": 3335.92, "available": True, "notes": "14.07%"},
            "booking": {"total_price": 3176.06, "available": True},
            "kivoya": {
                "nightly_rate": 436.5,  # Stale rate
                "base_subtotal": 1746.0,
                "cleaning_fee": 550.0,
                "service_fee": 176.78,
                "taxes": 337.78,
                "total_price": 2810.56,
                "notes": "Direct booking: 14.07% STR taxes",
            },
        }

        with patch("pathlib.Path.glob") as mock_glob:
            mock_file = MagicMock()
            mock_file.read_text.return_value = json.dumps(mock_cached_comparison)
            mock_glob.return_value = [mock_file]

            comparisons = gen._load_platform_comparisons([segment])
            self.assertEqual(len(comparisons), 1)
            comp = comparisons[0]

            # Kivoya Direct must be dynamically recalculated to $399 base ($1,596 subtotal, $2,624.43 total)
            self.assertEqual(comp["kivoya"]["nightly_rate"], 399.0)
            self.assertEqual(comp["kivoya"]["base_subtotal"], 1596.0)
            self.assertEqual(comp["kivoya"]["total_price"], 2624.43)
            self.assertEqual(comp["kivoya"]["effective_nightly"], 656.11)

    def test_max_divergence_column_removed(self):
        """Verify that 'Max Divergence' column is removed from the platform comparison table and export."""
        # 1. Ensure table header does not contain Max Divergence
        self.assertNotIn("<th>Max Divergence</th>", self.html_content)

        # 2. Check headers of the comparison table
        match = re.search(r'Total price comparison between platforms.*?<table>.*?<thead>.*?<tr>(.*?)</tr>.*?</thead>', self.html_content, re.DOTALL)
        self.assertIsNotNone(match, "Could not find comparison table header row")
        th_tags = re.findall(r'<th[^>]*>(.*?)</th>', match.group(1), re.DOTALL)
        self.assertEqual(len(th_tags), 8, f"Expected 8 table headers, got {len(th_tags)}: {th_tags}")
        for th in th_tags:
            self.assertNotIn("Max Divergence", th)

        # 3. Ensure details row uses colspan="8"
        self.assertIn('<td colspan="8">', self.html_content)
        self.assertNotIn('<td colspan="9">', self.html_content)

        # 4. Ensure TSV clipboard copy function does not export Max Divergence
        copy_fn_match = re.search(r'function copyPlatformComparison\(\)\s*\{(.*?)\}', self.html_content, re.DOTALL)
        self.assertIsNotNone(copy_fn_match, "Could not find copyPlatformComparison function")
        copy_fn_body = copy_fn_match.group(1)
        self.assertNotIn("Max Divergence", copy_fn_body)
        self.assertNotIn("dataset.maxDiv", copy_fn_body)

    def test_validity_audit_modal_and_filters(self):
        """Verify that Competitor Validity Audit modal and filters render and function correctly."""
        # 1. Check modal container and script functions in HTML content
        self.assertIn('id="validityModalOverlay"', self.html_content)
        self.assertIn('id="validityModalTitle"', self.html_content)
        self.assertIn('function openValidityModal(', self.html_content)
        self.assertIn('function closeValidityModal(', self.html_content)
        self.assertIn('function filterValidity(', self.html_content)

        # 2. Check filter pill buttons for validity
        self.assertIn("filterValidity('valid', this)", self.html_content)
        self.assertIn("filterValidity('disqualified', this)", self.html_content)
        self.assertIn("Valid Comps Only", self.html_content)
        self.assertIn("Disqualified Comps", self.html_content)

        # 3. Check that comp cards include btn-validity-audit and data-valid attribute
        self.assertIn("btn-validity-audit", self.html_content)
        self.assertIn('data-valid="true"', self.html_content)
        self.assertIn('data-valid="false"', self.html_content)

        # 4. Interactive test via Playwright
        async def run_modal_and_filter_test():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page(viewport={"width": 1280, "height": 800})
                file_uri = f"file://{self.html_path.resolve()}"
                await page.goto(file_uri)
                await page.wait_for_load_state("domcontentloaded")

                # Switch to comps tab
                await page.click("button[onclick*=\"'comps'\"]")
                await page.wait_for_timeout(50)

                # Verify disqualified cards are initially hidden
                disq_cards_init = await page.evaluate(
                    "() => Array.from(document.querySelectorAll('.comp-card[data-valid=\"false\"]')).filter(c => c.style.display !== 'none').length"
                )
                self.assertEqual(disq_cards_init, 0, "Disqualified comp cards should be hidden by default")

                # Click 'Disqualified Comps' filter pill
                await page.click("button[onclick*=\"filterValidity('disqualified'\"]")
                await page.wait_for_timeout(50)

                # Now disqualified cards should be visible
                disq_cards_visible = await page.evaluate(
                    "() => Array.from(document.querySelectorAll('.comp-card[data-valid=\"false\"]')).filter(c => c.style.display !== 'none').length"
                )
                self.assertGreater(disq_cards_visible, 0, "Disqualified comp cards should be visible after clicking filter")

                # And valid cards should be hidden
                valid_cards_hidden = await page.evaluate(
                    "() => Array.from(document.querySelectorAll('.comp-card[data-valid=\"true\"]')).filter(c => c.style.display !== 'none').length"
                )
                self.assertEqual(valid_cards_hidden, 0, "Valid comp cards should be hidden when filtered for disqualified")

                # Switch back to 'Valid Comps Only'
                await page.click("button[onclick*=\"filterValidity('valid'\"]")
                await page.wait_for_timeout(50)

                # Click the first validity audit button
                first_audit_btn = await page.query_selector(".btn-validity-audit")
                self.assertIsNotNone(first_audit_btn, "Expected at least one .btn-validity-audit button")
                await first_audit_btn.click()
                await page.wait_for_timeout(50)

                # Verify modal overlay display is 'flex'
                modal_display = await page.evaluate("() => document.getElementById('validityModalOverlay').style.display")
                self.assertEqual(modal_display, "flex", "Validity modal should display 'flex' when opened")

                # Verify modal title is populated
                modal_title = await page.evaluate("() => document.getElementById('validityModalTitle').textContent")
                self.assertTrue(len(modal_title) > 0 and modal_title != "Competitor Property", "Modal title should be populated with property name")

                # Close modal via Escape key
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(50)
                modal_display_closed = await page.evaluate("() => document.getElementById('validityModalOverlay').style.display")
                self.assertEqual(modal_display_closed, "none", "Validity modal should display 'none' after pressing Escape")

                await browser.close()

        asyncio.run(run_modal_and_filter_test())

    def test_revenue_tab_tot_booked_yoy(self):
        """Verify Revenue tab 2026 Total On-the-Books KPI card displays YoY percentage vs 2025 Total."""
        self.assertIn("Total On-the-Books", self.html_content)
        # Check for YoY percentage pattern e.g. "-12.7% YoY</span> vs 2025 Total"
        m = re.search(r"[+-]?\d+\.\d+%\s*YoY(?:</span>)?\s*vs\s*\d{4}\s*Total", self.html_content)
        self.assertIsNotNone(m, "Expected YoY comparison vs prior year total in Revenue tab KPI card")

    def test_reservations_tab_no_nested_scrollbar(self):
        """Verify .res-table-wrapper has no max-height constraint, preventing nested vertical scrollbars."""
        # Find the primary .res-table-wrapper definition
        m = re.search(r"\.res-table-wrapper\s*\{([^}]+)\}", self.html_content)
        self.assertIsNotNone(m, "Could not locate .res-table-wrapper CSS rule")
        css_body = m.group(1)
        self.assertNotIn("max-height", css_body, ".res-table-wrapper must not have max-height to avoid nested scrollbar")

    def test_reservations_tab_booked_date_sorting(self):
        """Verify Booked Date column header, data-booked attributes, and descending date sorting on click."""
        self.assertIn('id="resTh1">Booked Date', self.html_content)
        self.assertIn('data-booked="', self.html_content)

        async def run_sort_test():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page(viewport={"width": 1280, "height": 800})
                file_uri = f"file://{self.html_path.resolve()}"
                await page.goto(file_uri)
                await page.wait_for_load_state("domcontentloaded")

                # Switch to reservations tab
                await page.click("button[onclick*=\"'reservations'\"]")
                await page.wait_for_timeout(50)

                # Click Booked Date header to sort descending
                await page.click("#resTh1")
                await page.wait_for_timeout(50)

                # Get first 3 data-booked values
                booked_dates = await page.evaluate("""() => {
                    return Array.from(document.querySelectorAll('#resTableBody .res-row'))
                        .map(r => r.dataset.booked || '')
                        .filter(d => d.length > 0)
                        .slice(0, 3);
                }""")
                self.assertGreater(len(booked_dates), 0, "Expected booked reservations")
                # Verify first date is in 2026 (latest bookings e.g. 2026-09-10 or 2026-09-03)
                self.assertTrue(booked_dates[0].startswith("2026-09"), f"Expected latest booking at top, got {booked_dates[0]}")
                # Verify descending order: booked_dates[0] >= booked_dates[1]
                if len(booked_dates) >= 2:
                    self.assertGreaterEqual(booked_dates[0], booked_dates[1])

                await browser.close()

        asyncio.run(run_sort_test())

    def test_reviews_tab_rendering(self):
        """Verifies that the ⭐ Reviews tab, pulsing bell badge, and platform scorecards are rendered."""
        self.assertIn('id="tab-btn-reviews"', self.html_content)
        self.assertIn('review-bell-badge', self.html_content)
        self.assertIn('id="tab-reviews"', self.html_content)
        self.assertIn('Airbnb', self.html_content)
        self.assertIn('VRBO', self.html_content)
        self.assertIn('Booking.com', self.html_content)
        self.assertIn('Kivoya Direct', self.html_content)
        self.assertIn('Native 10.0 Scale', self.html_content)
        self.assertIn('Native 5.0 Scale', self.html_content)
        self.assertIn('filterReviews', self.html_content)

    def test_reviews_tab_omits_bell_badge_when_zero_recent(self):
        """When recent_reviews_count is 0, the bell badge is omitted from the tab button."""
        with tempfile.TemporaryDirectory() as tmpdir:
            out_p = Path(tmpdir) / "index.html"
            gen = HTMLDashboardGenerator(
                output_path=str(out_p),
                ratings_data={"recent_reviews_count": 0, "platforms": {}},
            )
            html_out = gen.generate(evaluated_segments=[])
            self.assertEqual(gen.recent_rev_count, 0)
            self.assertNotIn('review-bell-badge', html_out)

    def test_channels_tab_positioned_after_revenue(self):
        """Verify that the Channels tab is positioned immediately after Revenue in both navigation and DOM."""
        nav_pos_revenue = self.html_content.find("switchTab('revenue')")
        nav_pos_channels = self.html_content.find("switchTab('comparison')")
        self.assertNotEqual(nav_pos_revenue, -1)
        self.assertNotEqual(nav_pos_channels, -1)
        self.assertLess(nav_pos_revenue, nav_pos_channels, "Channels tab button must appear after Revenue button in navigation")

        dom_pos_revenue = self.html_content.find('id="tab-revenue"')
        dom_pos_channels = self.html_content.find('id="tab-comparison"')
        self.assertNotEqual(dom_pos_revenue, -1)
        self.assertNotEqual(dom_pos_channels, -1)
        self.assertLess(dom_pos_revenue, dom_pos_channels, "Channels tab-content section must appear after Revenue tab-content")

    def test_reviews_date_filters_and_defaults(self):
        """Verify presence of multi-interval date filter pills with Last 30d active by default."""
        self.assertIn('id="rev-date-all"', self.html_content)
        self.assertIn('id="rev-date-30"', self.html_content)
        self.assertIn('id="rev-date-90"', self.html_content)
        self.assertIn('id="rev-date-180"', self.html_content)
        self.assertIn('id="rev-date-365"', self.html_content)
        self.assertIn('onclick="setReviewDateFilter(\'30\', this)">Last 30d</button>', self.html_content)
        self.assertIn('id="rev-date-30" aria-pressed="true"', self.html_content)
        self.assertIn('id="reviewDateFilterVal" value="30"', self.html_content)
        self.assertIn('data-date="', self.html_content)

    def test_reviews_side_by_side_grid_and_empty_state(self):
        """Verify 2-column side-by-side grid, guest/team column headers, empty state placeholders, and mobile styles."""
        self.assertIn('.review-grid-2col', self.html_content)
        self.assertIn('class="review-grid-2col"', self.html_content)
        self.assertIn('class="review-guest-col"', self.html_content)
        self.assertIn('class="review-team-col"', self.html_content)
        self.assertIn('👤 Guest Review', self.html_content)
        self.assertIn('💬 Team Response', self.html_content)
        self.assertIn('review-reply-empty', self.html_content)
        self.assertIn('No Reply Posted', self.html_content)
        self.assertIn('No public response recorded for this guest review.', self.html_content)
        # Mobile stacking CSS
        self.assertIn('@media (max-width: 768px)', self.html_content)
        self.assertIn('grid-template-columns: 1fr;', self.html_content)

    def test_reviews_tab_handles_null_date_safely(self):
        """Verify that HTMLDashboardGenerator handles reviews with null or missing dates without crashing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            out_p = Path(tmpdir) / "index.html"
            sample_ratings = {
                "platforms": {"airbnb": {"display_name": "Airbnb", "rating": 5.0, "scale": "5.0"}},
                "reviews": [
                    {
                        "id": "rev_null_date",
                        "platform": "airbnb",
                        "reviewer_name": "Test Guest",
                        "date": None,
                        "rating": 5.0,
                        "body": "Null date review text.",
                        "host_response": None,
                    }
                ],
                "recent_window_days": 30,
            }
            gen = HTMLDashboardGenerator(
                output_path=str(out_p),
                ratings_data=sample_ratings,
            )
            gen.generate(evaluated_segments=[])
            html_content = out_p.read_text(encoding="utf-8")
            self.assertIn("Null date review text.", html_content)
            self.assertIn('data-date=""', html_content)
            self.assertIn("Date Not Available", html_content)

    def test_reviews_filter_interactions_playwright(self):
        """Verify interactive date filtering and mobile layout stacking using Playwright (time-invariant)."""
        today = date.today()
        ratings_json = json.loads(Path("data/ratings_reviews.json").read_text(encoding="utf-8"))
        reviews = ratings_json.get("reviews", [])
        expected_30 = sum(
            1 for r in reviews
            if r.get("date") and 0 <= (today - date.fromisoformat(str(r["date"])[:10])).days <= 30
        )
        expected_90 = sum(
            1 for r in reviews
            if r.get("date") and 0 <= (today - date.fromisoformat(str(r["date"])[:10])).days <= 90
        )
        total_reviews = len(reviews)

        async def run_reviews_test():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                # Desktop viewport
                page = await browser.new_page(viewport={"width": 1280, "height": 900})
                await page.goto(f"file://{self.html_path.resolve()}")

                # 1. Switch to Reviews tab
                await page.click('#tab-btn-reviews')
                await page.wait_for_timeout(100)

                # 2. Verify initial state: Last 30d active (dynamically calculated)
                visible_cards = await page.locator('.review-card:visible').count()
                self.assertEqual(visible_cards, expected_30, f"Expected {expected_30} visible cards for Last 30d default, found {visible_cards}")
                badge_text = await page.locator('#reviewCountBadge').text_content()
                self.assertIn(f"Showing {expected_30} of {total_reviews} reviews", badge_text)
                self.assertEqual(await page.locator('#rev-date-30').get_attribute('aria-pressed'), 'true')

                # 3. Click "All" pill -> all reviews visible
                await page.click('#rev-date-all')
                await page.wait_for_timeout(100)
                visible_cards_all = await page.locator('.review-card:visible').count()
                self.assertEqual(visible_cards_all, total_reviews, f"Expected {total_reviews} visible cards for All filter, found {visible_cards_all}")
                badge_text_all = await page.locator('#reviewCountBadge').text_content()
                self.assertIn(f"Showing {total_reviews} of {total_reviews} reviews", badge_text_all)
                self.assertEqual(await page.locator('#rev-date-all').get_attribute('aria-pressed'), 'true')
                self.assertEqual(await page.locator('#rev-date-30').get_attribute('aria-pressed'), 'false')

                # 4. Click "90d" pill -> expected_90 reviews visible
                await page.click('#rev-date-90')
                await page.wait_for_timeout(100)
                visible_cards_90 = await page.locator('.review-card:visible').count()
                self.assertEqual(visible_cards_90, expected_90, f"Expected {expected_90} visible cards for 90d filter, found {visible_cards_90}")
                self.assertEqual(await page.locator('#rev-date-90').get_attribute('aria-pressed'), 'true')

                # 5. Programmatic call without btn argument (tests ID fallback)
                await page.evaluate("setReviewDateFilter('180')")
                await page.wait_for_timeout(100)
                self.assertEqual(await page.locator('#rev-date-180').get_attribute('aria-pressed'), 'true')

                # 6. Mobile viewport test: verify vertical stacking of columns
                mobile_page = await browser.new_page(viewport={"width": 375, "height": 812})
                await mobile_page.goto(f"file://{self.html_path.resolve()}")
                await mobile_page.click('#tab-btn-reviews')
                await mobile_page.wait_for_timeout(100)
                # Verify grid-template-columns evaluates to a single column on mobile
                first_grid = mobile_page.locator('.review-grid-2col:visible').first
                grid_cols = await first_grid.evaluate("el => window.getComputedStyle(el).gridTemplateColumns")
                # On mobile (width 375), single column will be one pixel value e.g. "301px", not two columns "150px 150px"
                self.assertEqual(len(grid_cols.split()), 1, f"Expected 1 column on mobile viewport, got {grid_cols}")

                await browser.close()

        asyncio.run(run_reviews_test())

    def test_tab_persistence_and_scroll_restoration_on_reload(self):
        """
        Verify that reloading the dashboard preserves the currently active tab
        and restores the user's approximate vertical scroll position.
        """
        async def run_persistence_test():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page(viewport={"width": 1280, "height": 800})
                file_uri = f"file://{self.html_path.resolve()}"
                await page.goto(file_uri)
                await page.wait_for_load_state("domcontentloaded")

                # Initially, pricing tab is active
                active_tab = await page.evaluate("() => document.querySelector('.tab-content.active')?.id")
                self.assertEqual(active_tab, "tab-pricing")

                # Switch to comps tab
                comps_btn = await page.query_selector("button.tab-btn[onclick*=\"'comps'\"]")
                self.assertIsNotNone(comps_btn)
                await comps_btn.click()
                await page.wait_for_timeout(100)

                # Verify tab and hash
                active_tab = await page.evaluate("() => document.querySelector('.tab-content.active')?.id")
                self.assertEqual(active_tab, "tab-comps")
                curr_hash = await page.evaluate("() => window.location.hash")
                self.assertEqual(curr_hash, "#comps")

                # Scroll down 800px on comps tab
                await page.evaluate("() => window.scrollTo(0, 800)")
                await page.wait_for_timeout(150)
                await page.evaluate("() => saveCurrentTabScroll()")

                # Verify sessionStorage has scroll value
                saved_scroll = await page.evaluate("() => sessionStorage.getItem('str_advisor_scroll_comps')")
                self.assertIsNotNone(saved_scroll)
                self.assertGreater(int(saved_scroll), 600)

                # Reload page
                await page.reload()
                await page.wait_for_load_state("domcontentloaded")
                await page.wait_for_timeout(200)

                # Verify active tab is still tab-comps
                active_after_reload = await page.evaluate("() => document.querySelector('.tab-content.active')?.id")
                self.assertEqual(active_after_reload, "tab-comps")
                active_btn = await page.evaluate("() => document.querySelector('.tab-btn.active')?.textContent")
                self.assertIn("Comps", active_btn)

                # Verify scroll position is approximately restored (~800px)
                scroll_y = await page.evaluate("() => window.scrollY")
                self.assertGreater(scroll_y, 600, f"Expected scroll restoration near 800px, got {scroll_y}")

                await browser.close()

        asyncio.run(run_persistence_test())

    def test_browser_back_forward_navigation(self):
        """
        Verify that browser Back and Forward history buttons step through
        previously viewed tabs and update active tab state seamlessly.
        """
        async def run_history_test():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page(viewport={"width": 1280, "height": 800})
                file_uri = f"file://{self.html_path.resolve()}"
                await page.goto(file_uri)
                await page.wait_for_load_state("domcontentloaded")

                # 1. Click reviews tab
                rev_btn = await page.query_selector("button.tab-btn[onclick*=\"'reviews'\"]")
                await rev_btn.click()
                await page.wait_for_timeout(80)
                self.assertEqual(await page.evaluate("() => window.location.hash"), "#reviews")
                self.assertEqual(await page.evaluate("() => document.querySelector('.tab-content.active')?.id"), "tab-reviews")

                # 2. Click calendar tab
                cal_btn = await page.query_selector("button.tab-btn[onclick*=\"'calendar'\"]")
                await cal_btn.click()
                await page.wait_for_timeout(80)
                self.assertEqual(await page.evaluate("() => window.location.hash"), "#calendar")
                self.assertEqual(await page.evaluate("() => document.querySelector('.tab-content.active')?.id"), "tab-calendar")

                # 3. Press browser back -> returns to reviews
                await page.go_back()
                await page.wait_for_timeout(80)
                self.assertEqual(await page.evaluate("() => window.location.hash"), "#reviews")
                self.assertEqual(await page.evaluate("() => document.querySelector('.tab-content.active')?.id"), "tab-reviews")

                # 4. Press browser forward -> returns to calendar
                await page.go_forward()
                await page.wait_for_timeout(80)
                self.assertEqual(await page.evaluate("() => window.location.hash"), "#calendar")
                self.assertEqual(await page.evaluate("() => document.querySelector('.tab-content.active')?.id"), "tab-calendar")

                await browser.close()

        asyncio.run(run_history_test())

    def test_direct_hash_link_and_fallback(self):
        """
        Verify that loading URL with direct hash opens specified tab,
        invalid hashes fall back to pricing, and localStorage restores
        the last active tab when URL has no hash.
        """
        async def run_hash_test():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()

                # Direct hash link #revenue
                await page.goto(f"file://{self.html_path.resolve()}#revenue")
                await page.wait_for_load_state("domcontentloaded")
                await page.wait_for_timeout(100)
                active_tab = await page.evaluate("() => document.querySelector('.tab-content.active')?.id")
                self.assertEqual(active_tab, "tab-revenue")

                # Invalid hash link #invalid-tab with pre-populated localStorage -> strictly defaults to pricing, NOT calendar
                page_inv = await browser.new_page()
                await page_inv.add_init_script("localStorage.setItem('str_advisor_active_tab', 'calendar');")
                await page_inv.goto(f"file://{self.html_path.resolve()}#invalid-tab")
                await page_inv.wait_for_load_state("domcontentloaded")
                await page_inv.wait_for_timeout(100)
                active_tab_invalid = await page_inv.evaluate("() => document.querySelector('.tab-content.active')?.id")
                self.assertEqual(active_tab_invalid, "tab-pricing")
                await page_inv.close()

                # LocalStorage fallback when URL has no hash
                page_ls = await browser.new_page()
                await page_ls.add_init_script("localStorage.setItem('str_advisor_active_tab', 'calendar');")
                await page_ls.goto(f"file://{self.html_path.resolve()}")
                await page_ls.wait_for_load_state("domcontentloaded")
                await page_ls.wait_for_timeout(100)
                active_tab_ls = await page_ls.evaluate("() => document.querySelector('.tab-content.active')?.id")
                self.assertEqual(active_tab_ls, "tab-calendar")
                await page_ls.close()

                await browser.close()

        asyncio.run(run_hash_test())

    def test_clicking_active_tab_scrolls_to_top(self):
        """
        Verify that clicking the currently active tab smoothly scrolls to top.
        """
        async def run_scroll_top_test():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page(viewport={"width": 1280, "height": 800})
                await page.goto(f"file://{self.html_path.resolve()}")
                await page.wait_for_load_state("domcontentloaded")

                # Scroll down 600px on pricing
                await page.evaluate("() => window.scrollTo(0, 600)")
                await page.wait_for_timeout(100)
                self.assertGreater(await page.evaluate("() => window.scrollY"), 400)

                # Click pricing tab button
                pricing_btn = await page.query_selector("button.tab-btn[onclick*=\"'pricing'\"]")
                await pricing_btn.click()
                await page.wait_for_timeout(150)

                # Should be scrolled back to top
                scroll_after = await page.evaluate("() => window.scrollY")
                self.assertEqual(scroll_after, 0)

                await browser.close()

        asyncio.run(run_scroll_top_test())

    def test_calendar_12_months_and_navigation(self):
        """Verify that the calendar tab renders 12 consecutive months and navigates by 12 months."""
        async def run_calendar_test():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page(viewport={"width": 1280, "height": 800})
                file_uri = f"file://{self.html_path.resolve()}"
                await page.goto(file_uri)
                await page.wait_for_load_state("domcontentloaded")

                # Switch to Calendar tab
                cal_btn = await page.query_selector("button.tab-btn[onclick*=\"'calendar'\"]")
                self.assertIsNotNone(cal_btn)
                await cal_btn.click()
                await page.wait_for_timeout(100)

                # Verify 12 month cards are rendered
                cards = await page.query_selector_all("#calMonthsGrid .cal-month-card")
                self.assertEqual(len(cards), 12, f"Expected exactly 12 month cards in calendar grid, got {len(cards)}")

                # Get initial first month header
                first_header = await page.evaluate("() => document.querySelector('#calMonthsGrid .cal-month-header')?.textContent")

                # Click "Next 12 Months ▶"
                next_btn = await page.query_selector("button.cal-btn[onclick*='calNavigate(12)']")
                self.assertIsNotNone(next_btn)
                await next_btn.click()
                await page.wait_for_timeout(100)

                # Verify first month header advanced by 1 year (12 months)
                new_first_header = await page.evaluate("() => document.querySelector('#calMonthsGrid .cal-month-header')?.textContent")
                self.assertNotEqual(first_header, new_first_header)

                # Still exactly 12 month cards
                cards_after = await page.query_selector_all("#calMonthsGrid .cal-month-card")
                self.assertEqual(len(cards_after), 12)

    def test_historical_avg_color_in_browser(self):
        """Verify that Historical AVG values are rendered with conditional colors in the browser DOM, and Effective Total is uncolored."""
        async def run_color_browser_test():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page(viewport={"width": 1280, "height": 800})
                file_uri = f"file://{self.html_path.resolve()}"
                await page.goto(file_uri)
                await page.wait_for_load_state("domcontentloaded")
                await page.wait_for_timeout(100)

                # Locate all Historical AVG strong elements in the main pricing table
                hist_colors = await page.evaluate("""() => {
                    const cells = document.querySelectorAll('td[data-label="Historical AVG"] strong');
                    return Array.from(cells).map(el => window.getComputedStyle(el).color);
                }""")
                self.assertGreater(len(hist_colors), 0, "Expected at least one Historical AVG cell in pricing table")

                # Verify presence of green (rgb(52, 211, 153)) and red (rgb(248, 113, 113))
                green_found = any("52, 211, 153" in c for c in hist_colors)
                red_found = any("248, 113, 113" in c for c in hist_colors)
                self.assertTrue(green_found, f"Expected at least one green historical AVG cell, found: {set(hist_colors)}")
                self.assertTrue(red_found, f"Expected at least one red historical AVG cell, found: {set(hist_colors)}")

                # Verify Effective Total cells are white (rgb(241, 245, 249)), not dynamically colored
                eff_colors = await page.evaluate("""() => {
                    const cells = document.querySelectorAll('td[data-label="Effective Total"] strong');
                    return Array.from(cells).map(el => window.getComputedStyle(el).color);
                }""")
                self.assertGreater(len(eff_colors), 0, "Expected at least one Effective Total cell in pricing table")
                for c in eff_colors:
                    self.assertIn("241, 245, 249", c)

                await browser.close()

        asyncio.run(run_color_browser_test())

    def test_system_monitoring_tab_elements(self):
        """Verify presence of System tab navigation, header pill, status cards, ledger table, and log modal."""
        # Header status pill
        self.assertIn('id="header-status-pill"', self.html_content)
        self.assertIn('id="header-status-text"', self.html_content)
        self.assertIn("switchTab('system')", self.html_content)

        # Tab button
        self.assertIn('id="tab-btn-system"', self.html_content)
        self.assertIn('id="system-tab-badge"', self.html_content)

        # Tab content container
        self.assertIn('id="tab-system"', self.html_content)
        self.assertIn('class="system-cards-grid"', self.html_content)

        # Health cards
        self.assertIn('id="card-pms-sync"', self.html_content)
        self.assertIn('id="card-daily-quickscan"', self.html_content)
        self.assertIn('id="card-weekly-fullscan"', self.html_content)

        # Execution ledger table and wrapper (filters/search removed for minimalism)
        self.assertIn('class="system-ledger-table-wrap"', self.html_content)
        self.assertIn('class="system-scroll-hint"', self.html_content)
        self.assertIn('id="system-ledger-tbody"', self.html_content)
        self.assertNotIn('id="system-ledger-search"', self.html_content)
        self.assertNotIn('id="btn-trig-all"', self.html_content)
        self.assertNotIn('id="btn-status-all"', self.html_content)

        # Modal
        self.assertIn('id="system-log-modal"', self.html_content)
        self.assertIn('id="system-log-content"', self.html_content)
        self.assertIn('id="system-log-search"', self.html_content)
        self.assertIn('id="btn-copy-log"', self.html_content)
        self.assertIn('id="btn-download-log"', self.html_content)

    def test_system_monitoring_interactions_playwright(self):
        """Verify interacting with header status pill switches to system tab, and log modal opens and closes with Escape."""
        async def run_system_interaction_test():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page(viewport={"width": 1280, "height": 800})
                file_uri = f"file://{self.html_path.resolve()}"
                await page.goto(file_uri)
                await page.wait_for_load_state("domcontentloaded")
                await page.wait_for_timeout(50)

                # 1. Click header status pill and verify it activates #tab-system
                pill = await page.query_selector("#header-status-pill")
                self.assertIsNotNone(pill, "#header-status-pill button must exist")
                await pill.click()
                await page.wait_for_timeout(50)

                system_tab = await page.query_selector("#tab-system")
                is_active = await system_tab.evaluate("el => el.classList.contains('active')")
                self.assertTrue(is_active, "Clicking header status pill must switch to and activate #tab-system")

                # 2. Click a view log button in one of the cards
                log_btn = await page.query_selector(".system-btn-log")
                if log_btn and not await log_btn.is_disabled():
                    await log_btn.click()
                    await page.wait_for_timeout(50)

                    modal = await page.query_selector("#system-log-modal")
                    display_val = await modal.evaluate("el => window.getComputedStyle(el).display")
                    self.assertIn(display_val, ["flex", "block"], "Log modal should be visible after clicking log button")

                    # 3. Press Escape to close the modal
                    await page.keyboard.press("Escape")
                    await page.wait_for_timeout(50)
                    display_after = await modal.evaluate("el => window.getComputedStyle(el).display")
                    self.assertEqual(display_after, "none", "Pressing Escape must close the system log modal")

                await browser.close()

        asyncio.run(run_system_interaction_test())

    def test_system_monitoring_mobile_responsiveness_playwright(self):
        """Verify System tab layout on mobile screen (390x844), checking table scrollability, stacking cards, and drawer width."""
        async def run_mobile_test():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page(viewport={"width": 390, "height": 844})
                file_uri = f"file://{self.html_path.resolve()}"
                await page.goto(file_uri)
                await page.wait_for_load_state("domcontentloaded")
                await page.wait_for_timeout(50)

                # Switch to system tab
                await page.click("#tab-btn-system")
                await page.wait_for_timeout(50)

                # 1. Verify ledger table wrap fits inside mobile viewport and is horizontally scrollable
                table_wrap = await page.query_selector(".system-ledger-table-wrap")
                self.assertIsNotNone(table_wrap, ".system-ledger-table-wrap element must exist")
                client_width = await table_wrap.evaluate("el => el.clientWidth")
                scroll_width = await table_wrap.evaluate("el => el.scrollWidth")

                self.assertLessEqual(client_width, 390, "Table container clientWidth must fit inside mobile viewport")
                self.assertGreater(scroll_width, client_width, "Table container scrollWidth must exceed clientWidth for horizontal touch scrolling")

                # 2. Verify service cards stack within mobile viewport
                cards = await page.query_selector_all(".system-card")
                self.assertGreaterEqual(len(cards), 3, "Expected at least 3 system health cards")
                for card in cards:
                    card_w = await card.evaluate("el => el.clientWidth")
                    self.assertLessEqual(card_w, 390, f"System card clientWidth {card_w} must fit within mobile viewport")

                # 3. Verify scroll hint is visible on mobile
                scroll_hint = await page.query_selector(".system-scroll-hint")
                self.assertIsNotNone(scroll_hint)
                hint_display = await scroll_hint.evaluate("el => window.getComputedStyle(el).display")
                self.assertIn(hint_display, ["flex", "inline-flex"], "Scroll hint must be displayed on mobile")

                # 4. Open log modal on mobile and verify full width (100vw = 390px)
                log_btn = await page.query_selector(".system-btn-log:not([disabled])")
                self.assertIsNotNone(log_btn, "Expected at least one enabled log button in system tab")
                await log_btn.click()
                await page.wait_for_timeout(50)
                drawer = await page.query_selector(".system-log-drawer")
                drawer_w = await drawer.evaluate("el => el.clientWidth")
                self.assertEqual(drawer_w, 390, "System log drawer must expand to 100vw (390px) on mobile")

                # Close modal via mobile close button
                close_btn = await page.query_selector(".system-btn-close-mobile")
                await close_btn.click()
                await page.wait_for_timeout(50)
                modal = await page.query_selector("#system-log-modal")
                display_after = await modal.evaluate("el => window.getComputedStyle(el).display")
                self.assertEqual(display_after, "none", "Clicking mobile close button must close the system log modal")

                await browser.close()

        asyncio.run(run_mobile_test())


if __name__ == "__main__":
    unittest.main()

