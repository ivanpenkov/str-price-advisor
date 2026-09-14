"""Unit tests for AirbnbCollector multi-page cursor pagination and scraping logic."""

import asyncio
import io
import json
import shutil
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from src.airbnb_collector import AirbnbCollector


class TestAirbnbCollectorPagination(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.collector = AirbnbCollector(cache_dir=str(self.tmp_dir / "cache"))
        self.collector.LOCATIONS = ["Scottsdale--AZ"]
        self.collector.min_delay = 0.0
        self.collector.max_delay = 0.0
        self.collector.pdp_timeout = 0.05
        self.collector.retry_delay = 0.0

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_cursor_token_url_quoting(self):
        """Verify get_search_cursor produces tokens with padding that quote cleanly for URLs."""
        cursor_token = self.collector.get_search_cursor(18)
        quoted = urllib.parse.quote(cursor_token)
        self.assertIn("%3D", quoted)
        self.assertEqual(urllib.parse.unquote(quoted), cursor_token)

    def test_multi_page_pagination_calls_and_cursors(self):
        """Verify fetch_comps_for_dates requests Pages 1-3 with proper cursor parameters."""
        mock_context = MagicMock()
        self.collector.context = mock_context

        visited_urls = []

        async def fake_goto(url, *args, **kwargs):
            visited_urls.append(url)

        def make_fake_cards(page_idx):
            # Return 18 distinct cards per page
            return [
                {
                    "id": f"{page_idx}_{i}",
                    "text": f"Home in Scottsdale\nListing {page_idx}_{i}\n5.0 (10)\n6 bedrooms · 6 beds · 4 baths\n$800 night · $2,400 before taxes",
                    "href": f"/rooms/{page_idx}_{i}",
                    "photo_url": None,
                }
                for i in range(18)
            ]

        card_batches = [make_fake_cards(1), make_fake_cards(2), make_fake_cards(3)]

        async def run_test():
            page_counter = [0]

            async def fake_new_page():
                page_mock = AsyncMock()
                current_idx = page_counter[0]
                page_counter[0] += 1

                page_mock.goto.side_effect = fake_goto
                page_mock.wait_for_selector = AsyncMock()
                page_mock.wait_for_timeout = AsyncMock()
                page_mock.close = AsyncMock()

                async def fake_eval(script):
                    batch_idx = min(current_idx, len(card_batches) - 1)
                    return card_batches[batch_idx]

                page_mock.evaluate.side_effect = fake_eval
                return page_mock

            mock_context.new_page.side_effect = fake_new_page

            results = await self.collector.fetch_comps_for_dates(
                check_in="2026-10-15",
                check_out="2026-10-18",
                nights=3,
                tier="tier_a",
                locations=["Scottsdale--AZ"],
                use_cache=False,
                max_pages=3,
            )
            return results

        results = asyncio.run(run_test())

        # Should query exactly 3 pages
        self.assertEqual(len(visited_urls), 3)
        self.assertNotIn("cursor=", visited_urls[0])
        self.assertIn("&cursor=", visited_urls[1])
        self.assertIn("&cursor=", visited_urls[2])

        # Verify cursor for page 2 corresponds to offset 18
        cursor_18 = urllib.parse.quote(self.collector.get_search_cursor(18))
        self.assertIn(f"&cursor={cursor_18}", visited_urls[1])

        # Verify cursor for page 3 corresponds to offset 36
        cursor_36 = urllib.parse.quote(self.collector.get_search_cursor(36))
        self.assertIn(f"&cursor={cursor_36}", visited_urls[2])

        # Total listings extracted: 18 * 3 = 54
        self.assertEqual(len(results), 54)

    def test_early_exit_when_fewer_than_18_cards(self):
        """Verify pagination halts early if a page yields fewer than 18 cards."""
        mock_context = MagicMock()
        self.collector.context = mock_context

        visited_urls = []

        async def fake_goto(url, *args, **kwargs):
            visited_urls.append(url)

        # Page 1 returns 10 cards (< 18)
        page1_cards = [
            {
                "id": f"card_{i}",
                "text": f"Home in Mesa\nListing {i}\n5.0 (10)\n6 bedrooms · 6 beds · 4 baths\n$600 night · $1,800 before taxes",
                "href": f"/rooms/card_{i}",
                "photo_url": None,
            }
            for i in range(10)
        ]

        async def run_test():
            async def fake_new_page():
                page_mock = AsyncMock()
                page_mock.goto.side_effect = fake_goto
                page_mock.wait_for_selector = AsyncMock()
                page_mock.wait_for_timeout = AsyncMock()
                page_mock.close = AsyncMock()
                page_mock.evaluate.return_value = page1_cards
                return page_mock

            mock_context.new_page.side_effect = fake_new_page

            results = await self.collector.fetch_comps_for_dates(
                check_in="2026-10-15",
                check_out="2026-10-18",
                nights=3,
                tier="tier_a",
                locations=["Mesa--AZ"],
                use_cache=False,
                max_pages=3,
            )
            return results

        results = asyncio.run(run_test())

        # Should only have visited Page 1
        self.assertEqual(len(visited_urls), 1)
        self.assertEqual(len(results), 10)

    def test_deduplication_across_pages(self):
        """Verify duplicate listings appearing on multiple pages are deduplicated."""
        mock_context = MagicMock()
        self.collector.context = mock_context

        # Page 1 and Page 2 contain an overlapping listing 'shared_comp'
        page1_cards = [
            {
                "id": "shared_comp",
                "text": "Home in Scottsdale\nShared Comp\n5.0 (10)\n6 bedrooms · 6 beds · 4 baths\n$800 night · $2,400 before taxes",
                "href": "/rooms/shared_comp",
                "photo_url": None,
            }
        ] + [
            {
                "id": f"p1_{i}",
                "text": f"Home in Scottsdale\nListing {i}\n5.0 (10)\n6 bedrooms · 6 beds · 4 baths\n$800 night · $2,400 before taxes",
                "href": f"/rooms/p1_{i}",
                "photo_url": None,
            }
            for i in range(17)
        ]

        page2_cards = [
            {
                "id": "shared_comp",  # Duplicate on page 2
                "text": "Home in Scottsdale\nShared Comp\n5.0 (10)\n6 bedrooms · 6 beds · 4 baths\n$800 night · $2,400 before taxes",
                "href": "/rooms/shared_comp",
                "photo_url": None,
            }
        ] + [
            {
                "id": f"p2_{i}",
                "text": f"Home in Scottsdale\nListing {i}\n5.0 (10)\n6 bedrooms · 6 beds · 4 baths\n$800 night · $2,400 before taxes",
                "href": f"/rooms/p2_{i}",
                "photo_url": None,
            }
            for i in range(5)  # < 18, so stops here
        ]

        async def run_test():
            page_counter = [0]

            async def fake_new_page():
                page_mock = AsyncMock()
                idx = page_counter[0]
                page_counter[0] += 1
                page_mock.goto = AsyncMock()
                page_mock.wait_for_selector = AsyncMock()
                page_mock.wait_for_timeout = AsyncMock()
                page_mock.close = AsyncMock()
                page_mock.evaluate.return_value = page1_cards if idx == 0 else page2_cards
                return page_mock

            mock_context.new_page.side_effect = fake_new_page

            results = await self.collector.fetch_comps_for_dates(
                check_in="2026-10-15",
                check_out="2026-10-18",
                nights=3,
                tier="tier_a",
                locations=["Scottsdale--AZ"],
                use_cache=False,
                max_pages=3,
            )
            return results

        results = asyncio.run(run_test())

        # Total cards = 18 on p1 + 6 on p2 = 24 cards, but 1 was shared -> exactly 23 unique
        listing_ids = [r["listing_id"] for r in results]
        self.assertEqual(len(listing_ids), len(set(listing_ids)))
        self.assertEqual(len(listing_ids), 23)
        self.assertEqual(listing_ids.count("shared_comp"), 1)

    def test_page_failure_preserves_earlier_results(self):
        """Verify error on page 2 terminates pagination gracefully while saving page 1 results."""
        mock_context = MagicMock()
        self.collector.context = mock_context

        page1_cards = [
            {
                "id": f"p1_{i}",
                "text": f"Home in Scottsdale\nListing {i}\n5.0 (10)\n6 bedrooms · 6 beds · 4 baths\n$800 night · $2,400 before taxes",
                "href": f"/rooms/p1_{i}",
                "photo_url": None,
            }
            for i in range(18)
        ]

        async def run_test():
            page_counter = [0]

            async def fake_new_page():
                page_mock = AsyncMock()
                idx = page_counter[0]
                page_counter[0] += 1
                if idx == 0:
                    page_mock.goto = AsyncMock()
                    page_mock.wait_for_selector = AsyncMock()
                    page_mock.wait_for_timeout = AsyncMock()
                    page_mock.close = AsyncMock()
                    page_mock.evaluate.return_value = page1_cards
                else:
                    # Page 2 fails navigation
                    page_mock.goto = AsyncMock(side_effect=Exception("Navigation timeout"))
                    page_mock.close = AsyncMock()
                return page_mock

            mock_context.new_page.side_effect = fake_new_page

            results = await self.collector.fetch_comps_for_dates(
                check_in="2026-10-15",
                check_out="2026-10-18",
                nights=3,
                tier="tier_a",
                locations=["Scottsdale--AZ"],
                use_cache=False,
                max_pages=3,
            )
            return results

        results = asyncio.run(run_test())
        # Results from page 1 must still be returned
        self.assertEqual(len(results), 18)

    def test_track_response_bytes(self):
        """Verify _track_response_bytes parses content-length and updates byte counters."""
        self.assertEqual(self.collector.total_bytes_transferred, 0)
        self.assertEqual(self.collector._curr_interval_bytes, 0)

        # Valid 500 KB response
        resp_valid = MagicMock()
        resp_valid.headers = {"content-length": "512000"}
        self.collector._track_response_bytes(resp_valid)
        self.assertEqual(self.collector.total_bytes_transferred, 512000)
        self.assertEqual(self.collector._curr_interval_bytes, 512000)

        # Additional 250 KB response
        resp_valid_2 = MagicMock()
        resp_valid_2.headers = {"content-length": "256000"}
        self.collector._track_response_bytes(resp_valid_2)
        self.assertEqual(self.collector.total_bytes_transferred, 768000)
        self.assertEqual(self.collector._curr_interval_bytes, 768000)

        # Response without content-length header
        resp_no_cl = MagicMock()
        resp_no_cl.headers = {}
        self.collector._track_response_bytes(resp_no_cl)
        self.assertEqual(self.collector.total_bytes_transferred, 768000)

        # Response with non-digit content-length header
        resp_bad = MagicMock()
        resp_bad.headers = {"content-length": "invalid"}
        self.collector._track_response_bytes(resp_bad)
        self.assertEqual(self.collector.total_bytes_transferred, 768000)

    def test_usd_currency_enforced_in_search_url(self):
        """Verify that search URLs enforce English locale and USD currency."""
        mock_context = MagicMock()
        self.collector.contexts = {"Scottsdale--AZ": mock_context}
        self.collector.context = mock_context

        visited_urls = []

        async def fake_goto(url, *args, **kwargs):
            visited_urls.append(url)

        async def fake_new_page():
            page_mock = AsyncMock()
            page_mock.goto.side_effect = fake_goto
            page_mock.wait_for_selector = AsyncMock()
            page_mock.wait_for_timeout = AsyncMock()
            page_mock.close = AsyncMock()
            page_mock.evaluate.return_value = []
            return page_mock

        mock_context.new_page.side_effect = fake_new_page

        asyncio.run(self.collector._scrape_corridor(
            loc="Scottsdale--AZ",
            check_in="2026-10-15",
            check_out="2026-10-18",
            nights=3,
            use_cache=False,
            max_pages=1,
        ))

        self.assertEqual(len(visited_urls), 1)
        self.assertIn("locale=en", visited_urls[0])
        self.assertIn("currency=USD", visited_urls[0])

    def test_parallel_corridor_concurrency(self):
        """Verify fetch_comps_for_dates aggregates results across multiple corridors in parallel via asyncio.gather."""
        self.collector.parallel = True
        self.collector.LOCATIONS = ["Tempe--AZ", "Scottsdale--AZ"]
        self.collector.contexts = {
            "Tempe--AZ": MagicMock(),
            "Scottsdale--AZ": MagicMock(),
        }

        concurrently_running = 0
        max_concurrent = 0

        async def fake_scrape_corridor(loc, *args, **kwargs):
            nonlocal concurrently_running, max_concurrent
            concurrently_running += 1
            max_concurrent = max(max_concurrent, concurrently_running)
            await asyncio.sleep(0.05)
            concurrently_running -= 1
            if loc == "Tempe--AZ":
                return [{"listing_id": "101", "effective_nightly": 500}]
            else:
                return [{"listing_id": "201", "effective_nightly": 600}]

        with patch.object(self.collector, "_scrape_corridor", side_effect=fake_scrape_corridor):
            results = asyncio.run(self.collector.fetch_comps_for_dates(
                check_in="2026-10-15",
                check_out="2026-10-18",
                nights=3,
                use_cache=False,
            ))

        ids = [r["listing_id"] for r in results]
        self.assertIn("101", ids)
        self.assertIn("201", ids)
        self.assertEqual(max_concurrent, 2, "Expected 2 corridors to execute concurrently via asyncio.gather")
        self.assertGreaterEqual(self.collector.last_interval_duration, 0.0)

    def test_parallel_corridor_error_isolation(self):
        """Verify that a failure in one corridor task does not abort healthy scraped results in other corridors."""
        self.collector.parallel = True
        self.collector.LOCATIONS = ["Tempe--AZ", "Scottsdale--AZ"]
        self.collector.contexts = {
            "Tempe--AZ": MagicMock(),
            "Scottsdale--AZ": MagicMock(),
        }

        async def fake_scrape_corridor(loc, *args, **kwargs):
            if loc == "Tempe--AZ":
                raise RuntimeError("Playwright context crashed")
            return [{"listing_id": "201", "effective_nightly": 600}]

        with patch.object(self.collector, "_scrape_corridor", side_effect=fake_scrape_corridor):
            results = asyncio.run(self.collector.fetch_comps_for_dates(
                check_in="2026-10-15",
                check_out="2026-10-18",
                nights=3,
                use_cache=False,
            ))

        ids = [r["listing_id"] for r in results]
        self.assertEqual(ids, ["201"])

    def test_sequential_corridor_execution(self):
        """Verify fetch_comps_for_dates executes sequentially when parallel is False."""
        self.collector.parallel = False
        self.collector.LOCATIONS = ["Tempe--AZ", "Scottsdale--AZ"]

        async def fake_scrape_corridor(loc, *args, **kwargs):
            return [{"listing_id": f"id_{loc}", "effective_nightly": 550}]

        with patch.object(self.collector, "_scrape_corridor", side_effect=fake_scrape_corridor):
            results = asyncio.run(self.collector.fetch_comps_for_dates(
                check_in="2026-10-15",
                check_out="2026-10-18",
                nights=3,
                use_cache=False,
            ))

        ids = [r["listing_id"] for r in results]
        self.assertEqual(len(ids), 2)
        self.assertIn("id_Tempe--AZ", ids)
        self.assertIn("id_Scottsdale--AZ", ids)

    def test_lease_context_fifo_queue(self):
        """Verify lease_context acquires from _worker_queue and releases back on exit."""
        ctx1 = MagicMock(name="ctx1")
        ctx2 = MagicMock(name="ctx2")
        q = asyncio.Queue()
        q.put_nowait(ctx1)
        q.put_nowait(ctx2)
        self.collector._worker_queue = q
        self.collector.worker_contexts = [ctx1, ctx2]

        async def run_lease():
            leased = []
            async with self.collector.lease_context() as c:
                leased.append(c)
                self.assertEqual(q.qsize(), 1)
            self.assertEqual(q.qsize(), 2)
            async with self.collector.lease_context() as c2:
                leased.append(c2)
            return leased

        res = asyncio.run(run_lease())
        self.assertEqual(res, [ctx1, ctx2])

    def test_multi_worker_init_browser(self):
        """Verify init_browser creates 10 worker contexts when max_workers is 10."""
        self.collector.parallel = True
        self.collector.proxy_mgr.max_workers = 10
        mock_p = MagicMock()
        mock_browser = AsyncMock()
        mock_p.chromium.launch = AsyncMock(return_value=mock_browser)

        def make_mock_ctx(i):
            c = AsyncMock(name=f"ctx_{i}")
            c.on = MagicMock()
            return c

        mock_contexts = [make_mock_ctx(i) for i in range(10)]
        mock_browser.new_context = AsyncMock(side_effect=mock_contexts)

        mock_proxy_configs = [{"server": f"http://127.0.0.1:{60000+i}"} for i in range(10)]

        with patch.object(self.collector.proxy_mgr, "start_pool", AsyncMock(return_value=mock_proxy_configs)):
            asyncio.run(self.collector.init_browser(mock_p))

        self.assertEqual(len(self.collector.worker_contexts), 10)
        self.assertEqual(self.collector._worker_queue.qsize(), 10)
        self.assertEqual(self.collector.browser, mock_browser)
        self.assertIsNotNone(self.collector.context)

        # Verify close_browser cleans up all 10 contexts
        asyncio.run(self.collector.close_browser())
        self.assertEqual(len(self.collector.worker_contexts), 0)
        self.assertIsNone(self.collector._worker_queue)
        self.assertIsNone(self.collector.browser)
        for ctx in mock_contexts:
            ctx.close.assert_awaited()

    def test_multi_interval_concurrent_execution(self):
        """Verify that multiple intervals run concurrently and strictly preserve chronological ordering."""
        segments = [
            {"check_in": "2026-10-01", "check_out": "2026-10-04", "nights": 3, "lead_time_days": 10, "segment_type": "weekend", "our_base_nightly": 500.0, "our_effective_nightly": 500.0},
            {"check_in": "2026-10-04", "check_out": "2026-10-08", "nights": 4, "lead_time_days": 13, "segment_type": "midweek", "our_base_nightly": 400.0, "our_effective_nightly": 400.0},
            {"check_in": "2026-10-08", "check_out": "2026-10-11", "nights": 3, "lead_time_days": 17, "segment_type": "weekend", "our_base_nightly": 550.0, "our_effective_nightly": 550.0},
        ]

        concurrently_active = 0
        max_active = 0

        async def fake_fetch_comps(check_in, *args, **kwargs):
            nonlocal concurrently_active, max_active
            concurrently_active += 1
            max_active = max(max_active, concurrently_active)
            await asyncio.sleep(0.05)
            concurrently_active -= 1
            return [{"listing_id": f"comp_{check_in}", "effective_nightly": 500.0, "is_valid_comp": True}]

        async def fake_fetch_our(*args, **kwargs):
            return None

        # Simulate collector with 10 workers
        self.collector.worker_contexts = [MagicMock() for _ in range(10)]
        self.collector.parallel = True

        from src.analytics import PricingAnalyticsEngine
        from src.cli import run_interval_evaluations
        analytics = PricingAnalyticsEngine()

        with patch.object(self.collector, "fetch_comps_for_dates", side_effect=fake_fetch_comps), \
             patch.object(self.collector, "fetch_our_listing_price", side_effect=fake_fetch_our):
            evaluated_results, interval_metrics = asyncio.run(run_interval_evaluations(
                collector=self.collector,
                active_segments=segments,
                analytics=analytics,
                parallel=True,
                force=True,
            ))

        # Verify ordering is strictly preserved
        self.assertEqual(len(evaluated_results), 3)
        self.assertEqual(evaluated_results[0]["check_in"], "2026-10-01")
        self.assertEqual(evaluated_results[1]["check_in"], "2026-10-04")
        self.assertEqual(evaluated_results[2]["check_in"], "2026-10-08")

        self.assertIn("2026-10-01 -> 2026-10-04", interval_metrics[0]["label"])
        self.assertIn("2026-10-04 -> 2026-10-08", interval_metrics[1]["label"])
        self.assertIn("2026-10-08 -> 2026-10-11", interval_metrics[2]["label"])

        # Verify concurrency occurred
        self.assertGreater(max_active, 1, "Expected multiple intervals to run concurrently")

        # Verify sequential fallback (parallel=False)
        concurrently_active = 0
        max_active_seq = 0

        async def fake_fetch_comps_seq(check_in, *args, **kwargs):
            nonlocal concurrently_active, max_active_seq
            concurrently_active += 1
            max_active_seq = max(max_active_seq, concurrently_active)
            await asyncio.sleep(0.02)
            concurrently_active -= 1
            return [{"listing_id": f"comp_{check_in}", "effective_nightly": 500.0, "is_valid_comp": True}]

        with patch.object(self.collector, "fetch_comps_for_dates", side_effect=fake_fetch_comps_seq), \
             patch.object(self.collector, "fetch_our_listing_price", side_effect=fake_fetch_our):
            seq_results, _ = asyncio.run(run_interval_evaluations(
                collector=self.collector,
                active_segments=segments,
                analytics=analytics,
                parallel=False,
                force=True,
            ))
        self.assertEqual(len(seq_results), 3)
        self.assertEqual(max_active_seq, 1, "Expected strictly sequential execution when parallel=False")

    def test_interval_byte_tracker_and_context_manager(self):
        """Verify IntervalByteTracker accumulates bytes and track_interval_bytes context manager isolates task scope."""
        from src.airbnb_collector import IntervalByteTracker, _current_interval_tracker

        tracker = IntervalByteTracker()
        self.assertEqual(tracker.bytes, 0)
        tracker.add_bytes(1024)
        tracker.add_bytes(2048)
        tracker.add_bytes(-500)  # Should ignore negative bytes
        self.assertEqual(tracker.bytes, 3072)

        async def run_context_test():
            self.assertIsNone(_current_interval_tracker.get())
            async with self.collector.track_interval_bytes() as t:
                self.assertIs(t, _current_interval_tracker.get())
                t.add_bytes(5000)
                self.assertEqual(t.bytes, 5000)
            self.assertIsNone(_current_interval_tracker.get())

        asyncio.run(run_context_test())

    def test_per_worker_context_isolated_byte_tracking(self):
        """Verify individual BrowserContext instances track isolated _transferred_bytes."""
        ctx1 = MagicMock()
        ctx2 = MagicMock()
        ctx1._transferred_bytes = 0
        ctx2._transferred_bytes = 0

        tracker1 = self.collector._make_context_tracker(ctx1)
        tracker2 = self.collector._make_context_tracker(ctx2)

        resp1 = MagicMock()
        resp1.headers.get.return_value = "1000"

        resp2 = MagicMock()
        resp2.headers.get.return_value = "2500"

        tracker1(resp1)
        self.assertEqual(ctx1._transferred_bytes, 1000)
        self.assertEqual(ctx2._transferred_bytes, 0)
        self.assertEqual(self.collector.total_bytes_transferred, 1000)

        tracker2(resp2)
        self.assertEqual(ctx1._transferred_bytes, 1000)
        self.assertEqual(ctx2._transferred_bytes, 2500)
        self.assertEqual(self.collector.total_bytes_transferred, 3500)

    def test_concurrent_intervals_isolated_byte_accounting(self):
        """Verify concurrent interval tasks leasing distinct contexts accrue isolated bytes with zero overlap."""
        ctx1 = MagicMock(name="ctx1")
        ctx2 = MagicMock(name="ctx2")
        ctx1._transferred_bytes = 0
        ctx2._transferred_bytes = 0

        q = asyncio.Queue()
        q.put_nowait(ctx1)
        q.put_nowait(ctx2)
        self.collector._worker_queue = q
        self.collector.worker_contexts = [ctx1, ctx2]

        tracker1 = self.collector._make_context_tracker(ctx1)
        tracker2 = self.collector._make_context_tracker(ctx2)

        async def simulate_interval_1():
            async with self.collector.track_interval_bytes() as t1:
                async with self.collector.lease_context() as c1:
                    await asyncio.sleep(0.01)
                    resp = MagicMock()
                    resp.headers.get.return_value = "1000000"
                    tracker1(resp)
                    await asyncio.sleep(0.01)
                return t1.bytes

        async def simulate_interval_2():
            async with self.collector.track_interval_bytes() as t2:
                async with self.collector.lease_context() as c2:
                    await asyncio.sleep(0.01)
                    resp = MagicMock()
                    resp.headers.get.return_value = "500000"
                    tracker2(resp)
                    await asyncio.sleep(0.01)
                return t2.bytes

        async def run_all():
            return await asyncio.gather(simulate_interval_1(), simulate_interval_2())

        bytes_inv1, bytes_inv2 = asyncio.run(run_all())

        self.assertEqual(bytes_inv1, 1000000, "Interval 1 should record strictly its own bytes")
        self.assertEqual(bytes_inv2, 500000, "Interval 2 should record strictly its own bytes")
        self.assertEqual(self.collector.total_bytes_transferred, 1500000, "Collector total should be exact sum of both")
        self.assertEqual(bytes_inv1 + bytes_inv2, self.collector.total_bytes_transferred, "Sub-intervals must sum exactly to collector total")

    def test_safe_get_context_bytes_type_matrix(self):
        """Verify _safe_get_context_bytes handles diverse types defensively without exceptions."""
        safe_fn = AirbnbCollector._safe_get_context_bytes

        # Plain object / None
        self.assertEqual(safe_fn(None), 0)
        self.assertEqual(safe_fn(object()), 0)

        # MagicMock (auto-generates Mock on attribute access)
        mock_ctx = MagicMock()
        self.assertEqual(safe_fn(mock_ctx), 0)

        # Booleans (subclass of int in Python, should be rejected)
        bool_ctx = MagicMock()
        bool_ctx._transferred_bytes = True
        self.assertEqual(safe_fn(bool_ctx), 0)
        bool_ctx._transferred_bytes = False
        self.assertEqual(safe_fn(bool_ctx), 0)

        # Valid integers and floats
        valid_int_ctx = MagicMock()
        valid_int_ctx._transferred_bytes = 4096
        self.assertEqual(safe_fn(valid_int_ctx), 4096)

        valid_float_ctx = MagicMock()
        valid_float_ctx._transferred_bytes = 8192.5
        self.assertEqual(safe_fn(valid_float_ctx), 8192)

    def test_single_interval_multi_context_leases(self):
        """Verify multiple sequential and concurrent context leases within one interval accumulate into the single tracker."""
        ctx1 = MagicMock(name="ctx1")
        ctx2 = MagicMock(name="ctx2")
        ctx1._transferred_bytes = 0
        ctx2._transferred_bytes = 0

        q = asyncio.Queue()
        q.put_nowait(ctx1)
        q.put_nowait(ctx2)
        self.collector._worker_queue = q
        self.collector.worker_contexts = [ctx1, ctx2]

        tracker1 = self.collector._make_context_tracker(ctx1)
        tracker2 = self.collector._make_context_tracker(ctx2)

        async def run_interval():
            async with self.collector.track_interval_bytes() as t:
                # 1. First lease (e.g. corridor 1)
                async with self.collector.lease_context() as c:
                    resp = MagicMock()
                    resp.headers.get.return_value = "3000"
                    tracker1(resp) if c == ctx1 else tracker2(resp)

                # 2. Second lease (e.g. corridor 2)
                async with self.collector.lease_context() as c:
                    resp = MagicMock()
                    resp.headers.get.return_value = "7000"
                    tracker1(resp) if c == ctx1 else tracker2(resp)

                return t.bytes

        total_inv_bytes = asyncio.run(run_interval())
        self.assertEqual(total_inv_bytes, 10000, "Interval tracker should accumulate across multiple sequential leases")

    def test_randomized_corridor_pages_selection(self):
        """Verify _scrape_corridor picks random 2 or 3 pages when max_pages is None."""
        mock_context = MagicMock()
        self.collector.context = mock_context
        visited_pages = []

        async def fake_goto(url, *args, **kwargs):
            visited_pages.append(url)

        async def fake_new_page():
            p = AsyncMock()
            p.goto.side_effect = fake_goto
            p.evaluate.return_value = [{"id": "1", "text": "Home\n6 bedrooms · $500 night · $1500 before taxes", "href": "/rooms/1"}] * 18
            return p

        mock_context.new_page.side_effect = fake_new_page

        with patch("random.choice", return_value=2) as mock_rc:
            asyncio.run(self.collector._scrape_corridor(
                loc="Scottsdale--AZ",
                check_in="2026-10-15",
                check_out="2026-10-18",
                nights=3,
                max_pages=None,
                use_cache=False,
            ))
            mock_rc.assert_called_with([2, 3])
            self.assertEqual(len(visited_pages), 2)

    def test_fetch_direct_comp_price_available_pdp(self):
        """Verify fetch_direct_comp_price intercepts StaysPdpSections and caches available price."""
        mock_context = MagicMock()
        self.collector.context = mock_context

        mock_payload = {
            "data": {
                "presentation": {
                    "stayProductDetailPage": {
                        "sections": {
                            "sections": [
                                {
                                    "sectionId": "BOOK_IT_SIDEBAR",
                                    "section": {
                                        "available": True,
                                        "structuredDisplayPrice": {
                                            "primaryLine": {
                                                "__typename": "BasicDisplayPriceLine",
                                                "accessibilityLabel": "$2,400 for 3 nights",
                                                "price": "$2,400",
                                                "qualifier": "for 3 nights",
                                            }
                                        },
                                        "localizedUnavailabilityMessage": None,
                                    },
                                }
                            ]
                        }
                    }
                }
            }
        }

        async def fake_new_page():
            p = AsyncMock()
            listeners = {}
            def on_handler(event, cb):
                listeners[event] = cb
            def remove_handler(event, cb):
                listeners.pop(event, None)
            p.on = MagicMock(side_effect=on_handler)
            p.remove_listener = MagicMock(side_effect=remove_handler)

            async def fake_goto(url, *args, **kwargs):
                if "response" in listeners:
                    resp_mock = AsyncMock()
                    resp_mock.url = "https://www.airbnb.com/api/v3/StaysPdpSections"
                    resp_mock.text.return_value = json.dumps(mock_payload)
                    await listeners["response"](resp_mock)

            p.goto.side_effect = fake_goto
            p.evaluate = AsyncMock(return_value="")
            return p

        mock_context.new_page.side_effect = fake_new_page

        comp_meta = {"name": "Desert Oasis", "location": "Scottsdale", "bedrooms": 6, "beds": 8, "baths": 4.0}
        result = asyncio.run(self.collector.fetch_direct_comp_price(
            listing_id="9990001",
            check_in="2026-10-15",
            check_out="2026-10-18",
            nights=3,
            comp_meta=comp_meta,
            use_cache=False,
        ))

        self.assertIsNotNone(result)
        self.assertEqual(result["listing_id"], "9990001")
        self.assertEqual(result["total_price"], 2400.0)
        self.assertEqual(result["effective_nightly"], 800.0)
        self.assertEqual(result["title"], "Desert Oasis")

        cache_file = self.collector.cache_dir / "search_2026-10-15_2026-10-18_comp_9990001.json"
        self.assertTrue(cache_file.exists())
        cached_data = json.loads(cache_file.read_text())
        self.assertEqual(cached_data[0]["effective_nightly"], 800.0)

    def test_fetch_direct_comp_price_unavailable_pdp(self):
        """Verify fetch_direct_comp_price recognizes unavailable stays and caches unavailable status."""
        mock_context = MagicMock()
        self.collector.context = mock_context

        mock_payload = {
            "data": {
                "presentation": {
                    "stayProductDetailPage": {
                        "sections": {
                            "sections": [
                                {
                                    "sectionId": "BOOK_IT_SIDEBAR",
                                    "section": {
                                        "available": False,
                                        "localizedUnavailabilityMessage": "These dates are not available",
                                        "structuredDisplayPrice": None,
                                    },
                                }
                            ]
                        }
                    }
                }
            }
        }

        async def fake_new_page():
            p = AsyncMock()
            listeners = {}
            p.on = MagicMock(side_effect=lambda event, cb: listeners.update({event: cb}))
            p.remove_listener = MagicMock(side_effect=lambda event, cb: listeners.pop(event, None))

            async def fake_goto(url, *args, **kwargs):
                if "response" in listeners:
                    resp_mock = AsyncMock()
                    resp_mock.url = "https://www.airbnb.com/api/v3/StaysPdpSections"
                    resp_mock.text.return_value = json.dumps(mock_payload)
                    await listeners["response"](resp_mock)

            p.goto.side_effect = fake_goto
            p.evaluate = AsyncMock(return_value="")
            return p

        mock_context.new_page.side_effect = fake_new_page

        result = asyncio.run(self.collector.fetch_direct_comp_price(
            listing_id="9990002",
            check_in="2026-10-15",
            check_out="2026-10-18",
            nights=3,
            use_cache=False,
        ))

        self.assertIsNone(result)

        unavail_file = self.collector.cache_dir / "unavailable_2026-10-15_2026-10-18_comp_9990002.json"
        self.assertTrue(unavail_file.exists())
        data = json.loads(unavail_file.read_text())
        self.assertFalse(data["available"])
        self.assertEqual(data["status"], "BOOKED")

    def test_fetch_direct_comp_price_cache_hit(self):
        """Verify cache hits return immediately without opening new Playwright pages."""
        avail_file = self.collector.cache_dir / "search_2026-10-15_2026-10-18_comp_9990003.json"
        avail_file.write_text(json.dumps([{"listing_id": "9990003", "effective_nightly": 950.0}]))

        mock_context = MagicMock()
        self.collector.context = mock_context

        res = asyncio.run(self.collector.fetch_direct_comp_price(
            listing_id="9990003",
            check_in="2026-10-15",
            check_out="2026-10-18",
            nights=3,
            use_cache=True,
        ))
        self.assertEqual(res["effective_nightly"], 950.0)
        mock_context.new_page.assert_not_called()

        unavail_file = self.collector.cache_dir / "unavailable_2026-10-15_2026-10-18_comp_9990004.json"
        unavail_file.write_text(json.dumps({"listing_id": "9990004", "available": False, "status": "BOOKED"}))

        res_unavail = asyncio.run(self.collector.fetch_direct_comp_price(
            listing_id="9990004",
            check_in="2026-10-15",
            check_out="2026-10-18",
            nights=3,
            use_cache=True,
        ))
        self.assertIsNone(res_unavail)
        mock_context.new_page.assert_not_called()

    def test_fetch_missing_comps_fallback_concurrency(self):
        """Verify fetch_missing_comps_fallback executes checks across all missing comps."""
        async def fake_fetch_direct(listing_id, *args, **kwargs):
            if listing_id == "c1":
                return {"listing_id": "c1", "effective_nightly": 600.0}
            return None

        with patch.object(self.collector, "fetch_direct_comp_price", side_effect=fake_fetch_direct):
            missing = {
                "c1": {"name": "Comp 1"},
                "c2": {"name": "Comp 2"},
            }
            results = asyncio.run(self.collector.fetch_missing_comps_fallback(
                missing_comps=missing,
                check_in="2026-10-15",
                check_out="2026-10-18",
                nights=3,
            ))
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["listing_id"], "c1")
            self.assertEqual(results[0]["effective_nightly"], 600.0)

    def test_fetch_missing_comps_fallback_timeout_handling(self):
        """Verify fetch_missing_comps_fallback handles individual timeout or exception gracefully and logs debug info."""
        async def fake_hanging_fetch(listing_id, *args, **kwargs):
            if listing_id == "hang":
                raise asyncio.TimeoutError("Stalled socket connection")
            elif listing_id == "ok":
                return {"listing_id": "ok", "effective_nightly": 750.0}
            return None

        with patch.object(self.collector, "fetch_direct_comp_price", side_effect=fake_hanging_fetch), \
             patch("src.airbnb_collector.logger.debug") as mock_debug:
            missing = {
                "hang": {"name": "Hanging Comp"},
                "ok": {"name": "Healthy Comp"},
            }
            results = asyncio.run(self.collector.fetch_missing_comps_fallback(
                missing_comps=missing,
                check_in="2026-10-15",
                check_out="2026-10-18",
                nights=3,
            ))
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["listing_id"], "ok")
            self.assertEqual(results[0]["effective_nightly"], 750.0)
            mock_debug.assert_called_once()
            self.assertIn("hang", mock_debug.call_args[0][0])

    def test_fetch_direct_comp_price_page_close_timeout_suppression(self):
        """Verify page.close timeout is safely caught and suppressed without raising."""
        mock_context = MagicMock()
        self.collector.context = mock_context

        async def fake_new_page():
            p = AsyncMock()
            p.on = MagicMock()
            p.remove_listener = MagicMock()
            p.goto = AsyncMock(side_effect=Exception("Navigation error"))
            p.close = AsyncMock(side_effect=asyncio.TimeoutError("Page close hung"))
            return p

        mock_context.new_page.side_effect = fake_new_page

        result = asyncio.run(self.collector.fetch_direct_comp_price(
            listing_id="9990008",
            check_in="2026-10-15",
            check_out="2026-10-18",
            nights=3,
            use_cache=False,
        ))
        self.assertIsNone(result)

    def test_fetch_direct_comp_price_dom_fallback(self):
        """Verify DOM text parsing fallback when StaysPdpSections response is absent."""
        mock_context = MagicMock()
        self.collector.context = mock_context

        async def fake_new_page():
            p = AsyncMock()
            listeners = {}
            p.on = MagicMock(side_effect=lambda event, cb: listeners.update({event: cb}))
            p.remove_listener = MagicMock(side_effect=lambda event, cb: listeners.pop(event, None))
            resp_mock = MagicMock()
            resp_mock.status = 200
            p.goto = AsyncMock(return_value=resp_mock)
            # Simulate DOM innerText having "$1,800 for 3 nights"
            p.evaluate = AsyncMock(side_effect=[
                None,  # scrollTo
                "Luxury Villa in Scottsdale\n$1,800 for 3 nights\nFree cancellation",
            ])
            return p

        mock_context.new_page.side_effect = fake_new_page

        result = asyncio.run(self.collector.fetch_direct_comp_price(
            listing_id="9990005",
            check_in="2026-10-15",
            check_out="2026-10-18",
            nights=3,
            use_cache=False,
        ))

        self.assertIsNotNone(result)
        self.assertEqual(result["listing_id"], "9990005")
        self.assertEqual(result["total_price"], 1800.0)
        self.assertEqual(result["effective_nightly"], 600.0)

        avail_file = self.collector.cache_dir / "search_2026-10-15_2026-10-18_comp_9990005.json"
        self.assertTrue(avail_file.exists())

    def test_fetch_direct_comp_price_http_error_status(self):
        """Verify HTTP 403/503 responses abort without poisoning cache."""
        mock_context = MagicMock()
        self.collector.context = mock_context

        async def fake_new_page():
            p = AsyncMock()
            p.on = MagicMock()
            p.remove_listener = MagicMock()
            resp_mock = MagicMock()
            resp_mock.status = 403
            p.goto = AsyncMock(return_value=resp_mock)
            return p

        mock_context.new_page.side_effect = fake_new_page

        result = asyncio.run(self.collector.fetch_direct_comp_price(
            listing_id="9990006",
            check_in="2026-10-15",
            check_out="2026-10-18",
            nights=3,
            use_cache=False,
        ))

        self.assertIsNone(result)
        avail_file = self.collector.cache_dir / "search_2026-10-15_2026-10-18_comp_9990006.json"
        unavail_file = self.collector.cache_dir / "unavailable_2026-10-15_2026-10-18_comp_9990006.json"
        self.assertFalse(avail_file.exists())
        self.assertFalse(unavail_file.exists())

    def test_fetch_direct_comp_price_bot_challenge(self):
        """Verify bot challenges abort cleanly without recording listing as BOOKED."""
        mock_context = MagicMock()
        self.collector.context = mock_context

        async def fake_new_page():
            p = AsyncMock()
            p.on = MagicMock()
            p.remove_listener = MagicMock()
            resp_mock = MagicMock()
            resp_mock.status = 200
            p.goto = AsyncMock(return_value=resp_mock)
            p.evaluate = AsyncMock(side_effect=[
                None,  # scrollTo
                "Please verify you are human before continuing. Press and hold to confirm.",
            ])
            return p

        mock_context.new_page.side_effect = fake_new_page

        result = asyncio.run(self.collector.fetch_direct_comp_price(
            listing_id="9990007",
            check_in="2026-10-15",
            check_out="2026-10-18",
            nights=3,
            use_cache=False,
        ))

        self.assertIsNone(result)
        avail_file = self.collector.cache_dir / "search_2026-10-15_2026-10-18_comp_9990007.json"
        unavail_file = self.collector.cache_dir / "unavailable_2026-10-15_2026-10-18_comp_9990007.json"
        self.assertFalse(avail_file.exists())
        self.assertFalse(unavail_file.exists())

    def test_init_browser_cleans_up_on_failure(self):
        """Verify close_browser is invoked if browser initialization fails."""
        mock_p = MagicMock()
        mock_p.chromium.launch = AsyncMock(side_effect=RuntimeError("Chromium launch failed"))
        with patch.object(self.collector.proxy_mgr, "start_pool", AsyncMock(return_value=[{"server": "http://127.0.0.1:56001"}])):
            with patch.object(self.collector, "close_browser", AsyncMock()) as mock_close:
                with self.assertRaises(RuntimeError):
                    asyncio.run(self.collector.init_browser(mock_p))
                mock_close.assert_called_once()

    def test_corridor_scraping_transient_retry_success(self):
        """Verify _scrape_corridor retries navigation on failure and succeeds without logging warning."""
        mock_context = MagicMock()
        self.collector.context = mock_context

        attempt_counter = [0]
        page_mock = AsyncMock()

        async def fake_goto(url, *args, **kwargs):
            attempt_counter[0] += 1
            if attempt_counter[0] == 1:
                raise Exception("net::ERR_EMPTY_RESPONSE")
            return None

        page_mock.goto = AsyncMock(side_effect=fake_goto)
        page_mock.wait_for_selector = AsyncMock()
        page_mock.wait_for_timeout = AsyncMock()
        page_mock.close = AsyncMock()
        page_mock.evaluate = AsyncMock(return_value=[
            {
                "id": "12345",
                "text": "Home in Scottsdale\nListing 12345\n5.0 (10)\n6 bedrooms · 6 beds · 4 baths\n$800 night · $2,400 before taxes",
                "href": "/rooms/12345",
                "photo_url": None,
            }
        ])

        mock_context.new_page = AsyncMock(return_value=page_mock)

        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            results = asyncio.run(self.collector._scrape_corridor(
                loc="Scottsdale--AZ",
                check_in="2026-10-15",
                check_out="2026-10-18",
                nights=3,
                max_pages=1,
                use_cache=False,
            ))

        self.assertEqual(attempt_counter[0], 2)
        self.assertEqual(page_mock.close.call_count, 2)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["listing_id"], "12345")
        self.assertNotIn("Warning: could not fetch", mock_stdout.getvalue())
        cache_file = self.collector._get_cache_key("2026-10-15", "2026-10-18", "Scottsdale--AZ", "tier_a")
        self.assertTrue(cache_file.exists())

    def test_corridor_scraping_fails_after_three_attempts_and_logs_warning(self):
        """Verify _scrape_corridor attempts 3 times before logging warning, breaking, and not writing empty cache."""
        mock_context = MagicMock()
        self.collector.context = mock_context

        attempt_counter = [0]
        page_mock = AsyncMock()

        async def fake_goto(url, *args, **kwargs):
            attempt_counter[0] += 1
            raise Exception("net::ERR_EMPTY_RESPONSE")

        page_mock.goto = AsyncMock(side_effect=fake_goto)
        page_mock.close = AsyncMock()

        mock_context.new_page = AsyncMock(return_value=page_mock)

        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            results = asyncio.run(self.collector._scrape_corridor(
                loc="Scottsdale--AZ",
                check_in="2026-10-15",
                check_out="2026-10-18",
                nights=3,
                max_pages=1,
                use_cache=False,
            ))

        self.assertEqual(attempt_counter[0], 3)
        self.assertEqual(page_mock.close.call_count, 3)
        self.assertEqual(len(results), 0)
        stdout_val = mock_stdout.getvalue()
        self.assertIn("Warning: could not fetch Scottsdale--AZ page 1 for 2026-10-15: net::ERR_EMPTY_RESPONSE", stdout_val)
        # Verify warning was only printed once, not on every attempt
        self.assertEqual(stdout_val.count("Warning: could not fetch"), 1)
        # Verify cache file was NOT created to prevent cache poisoning on failure
        cache_file = self.collector._get_cache_key("2026-10-15", "2026-10-18", "Scottsdale--AZ", "tier_a")
        self.assertFalse(cache_file.exists())

    def test_fetch_our_listing_price_transient_retry_success(self):
        """Verify fetch_our_listing_price retries on transient error and succeeds without warning."""
        mock_context = MagicMock()
        self.collector.context = mock_context

        attempt_counter = [0]
        page_mock = AsyncMock()

        async def fake_goto(url, *args, **kwargs):
            attempt_counter[0] += 1
            if attempt_counter[0] == 1:
                raise Exception("net::ERR_EMPTY_RESPONSE")
            return None

        page_mock.goto = AsyncMock(side_effect=fake_goto)
        page_mock.wait_for_timeout = AsyncMock()
        page_mock.evaluate = AsyncMock(return_value="$3,000 for 3 nights")
        page_mock.close = AsyncMock()

        mock_context.new_page = AsyncMock(return_value=page_mock)

        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            res = asyncio.run(self.collector.fetch_our_listing_price(
                check_in="2026-10-15",
                check_out="2026-10-18",
                nights=3,
                use_cache=False,
            ))

        self.assertEqual(attempt_counter[0], 2)
        self.assertEqual(page_mock.close.call_count, 2)
        self.assertIsNotNone(res)
        self.assertEqual(res["airbnb_total"], 3000.0)
        self.assertNotIn("Warning:", mock_stdout.getvalue())

    def test_fetch_our_listing_price_fails_after_three_attempts(self):
        """Verify fetch_our_listing_price attempts 3 times before logging warning and returning None."""
        mock_context = MagicMock()
        self.collector.context = mock_context

        attempt_counter = [0]
        page_mock = AsyncMock()

        async def fake_goto(url, *args, **kwargs):
            attempt_counter[0] += 1
            raise Exception("net::ERR_EMPTY_RESPONSE")

        page_mock.goto = AsyncMock(side_effect=fake_goto)
        page_mock.close = AsyncMock()

        mock_context.new_page = AsyncMock(return_value=page_mock)

        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            res = asyncio.run(self.collector.fetch_our_listing_price(
                check_in="2026-10-15",
                check_out="2026-10-18",
                nights=3,
                use_cache=False,
            ))

        self.assertEqual(attempt_counter[0], 3)
        self.assertEqual(page_mock.close.call_count, 3)
        self.assertIsNone(res)
        stdout_val = mock_stdout.getvalue()
        self.assertIn("Warning: could not fetch our listing price for 2026-10-15: net::ERR_EMPTY_RESPONSE", stdout_val)
        self.assertEqual(stdout_val.count("Warning: could not fetch"), 1)

    def test_shared_proxy_mgr_preserves_pool_on_close(self):
        """Verify AirbnbCollector with shared proxy_mgr does not stop proxy pool on close_browser."""
        mock_mgr = MagicMock()
        mock_mgr.stop = AsyncMock()
        mock_mgr.endpoints = [MagicMock()]
        collector = AirbnbCollector(proxy_mgr=mock_mgr)
        self.assertFalse(collector._owns_proxy_mgr)
        self.assertIs(collector.proxy_mgr, mock_mgr)

        asyncio.run(collector.close_browser())
        mock_mgr.stop.assert_not_called()

    def test_init_browser_reuses_running_proxy_endpoints(self):
        """Verify init_browser uses get_proxy_configs() when proxy_mgr already has active endpoints."""
        mock_mgr = MagicMock()
        mock_mgr.endpoints = [MagicMock()]
        mock_mgr.required = False
        mock_mgr.get_proxy_configs.return_value = [{"server": "http://127.0.0.1:56001"}]
        mock_mgr.start_pool = AsyncMock()

        collector = AirbnbCollector(parallel=True, proxy_mgr=mock_mgr)

        mock_playwright = MagicMock()
        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_context.on = MagicMock()
        mock_browser.new_context.return_value = mock_context
        mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)

        asyncio.run(collector.init_browser(mock_playwright))
        mock_mgr.start_pool.assert_not_called()
        mock_mgr.get_proxy_configs.assert_called_once()
        self.assertEqual(len(collector.worker_contexts), 1)
        asyncio.run(collector.close_browser())


if __name__ == "__main__":
    unittest.main()
