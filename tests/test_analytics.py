"""Unit tests for PricingAnalyticsEngine."""

import unittest
from src.analytics import PricingAnalyticsEngine


class TestPricingAnalyticsEngine(unittest.TestCase):

    def setUp(self):
        self.engine = PricingAnalyticsEngine(
            base_percentile=65.0,
            cleaning_fee=500.0,
            urgent_pct_diff=25.0,
            urgent_lead_days=60,
            moderate_pct_diff=10.0,
        )

    def test_lead_time_tapering(self):
        """Lead time curves should taper across the 4 horizons for weekends."""
        self.assertEqual(self.engine.get_target_percentile(200), 85.0)  # >180d
        self.assertEqual(self.engine.get_target_percentile(120), 67.5)  # 91–180d
        self.assertEqual(self.engine.get_target_percentile(60), 62.5)   # 31–90d
        self.assertEqual(self.engine.get_target_percentile(20), 42.5)   # ≤30d
        self.assertEqual(self.engine.get_target_percentile(10), 42.5)   # ≤30d
        # Aggressive percentiles
        self.assertEqual(self.engine.get_target_percentile(200, aggressive=True), 90.0)
        self.assertEqual(self.engine.get_target_percentile(120, aggressive=True), 75.0)
        self.assertEqual(self.engine.get_target_percentile(60, aggressive=True), 70.0)
        self.assertEqual(self.engine.get_target_percentile(20, aggressive=True), 50.0)

    def test_midweek_target_percentiles(self):
        """Midweek target percentiles should follow the approved 4-tier matrix curve."""
        self.assertEqual(self.engine.get_target_percentile(200, segment_type="midweek"), 50.0)  # >180d
        self.assertEqual(self.engine.get_target_percentile(120, segment_type="midweek"), 47.5)  # 91–180d
        self.assertEqual(self.engine.get_target_percentile(60, segment_type="midweek"), 32.5)   # 31–90d
        self.assertEqual(self.engine.get_target_percentile(20, segment_type="midweek"), 30.0)   # ≤30d
        self.assertEqual(self.engine.get_target_percentile(10, segment_type="midweek"), 30.0)   # ≤30d
        # Aggressive percentiles
        self.assertEqual(self.engine.get_target_percentile(200, segment_type="midweek", aggressive=True), 60.0)
        self.assertEqual(self.engine.get_target_percentile(120, segment_type="midweek", aggressive=True), 55.0)
        self.assertEqual(self.engine.get_target_percentile(60, segment_type="midweek", aggressive=True), 45.0)
        self.assertEqual(self.engine.get_target_percentile(20, segment_type="midweek", aggressive=True), 38.0)

    def test_operational_floors(self):
        """Recommended base rates must enforce operational floors: >= $300 Midweek, >= $450 Weekend."""
        # Low comp prices that would otherwise produce < $300 or < $450
        comp_rates = [200.0, 250.0, 280.0]
        
        # Midweek segment: recommended base must be clamped at $300
        mid_seg = {
            "check_in": "2026-10-05",
            "check_out": "2026-10-08",
            "nights": 3,
            "lead_time_days": 15,
            "segment_type": "midweek",
            "our_base_nightly": 350.0,
            "our_effective_nightly": 517.0,
        }
        res_mid = self.engine.evaluate_segment(mid_seg, comp_rates)
        self.assertGreaterEqual(res_mid["recommended_base_nightly"], 300.0)

        # Weekend segment: recommended base must be clamped at $450
        wkd_seg = {
            "check_in": "2026-10-09",
            "check_out": "2026-10-12",
            "nights": 3,
            "lead_time_days": 15,
            "segment_type": "weekend",
            "our_base_nightly": 500.0,
            "our_effective_nightly": 667.0,
        }
        res_wkd = self.engine.evaluate_segment(wkd_seg, comp_rates)
        self.assertGreaterEqual(res_wkd["recommended_base_nightly"], 450.0)

    def test_outlier_removal(self):
        """Should filter out extreme prices using IQR."""
        # Standard cluster around $800-$1200 with crazy outliers $50 and $99,999
        prices = [750.0, 800.0, 850.0, 900.0, 950.0, 1000.0, 1050.0, 1100.0, 1200.0, 99999.0]
        cleaned = self.engine.remove_outliers(prices)
        self.assertNotIn(99999.0, cleaned)
        self.assertEqual(len(cleaned), 9)

    def test_translate_to_base_rate(self):
        """Should properly translate effective total rate to base nightly rate with $500 cleaning fee."""
        # 3 nights stay at $1000/night target effective cost ($3000 total)
        # target_base_total = 3000 - 500 = 2500
        # recommended_base_nightly = 2500 / 3 = $833
        rec_base = self.engine.translate_to_recommended_base_rate(1000.0, nights=3)
        self.assertEqual(rec_base, 833.0)

    def test_priority_tier_classification(self):
        """Urgent when >25% off target, Review when 10-25%, On Target when <10%."""
        # Case 1: Urgent (>25% underpriced)
        segment_urgent = {
            "check_in": "2026-09-15",
            "check_out": "2026-09-18",
            "nights": 3,
            "lead_time_days": 12,
            "our_base_nightly": 399.0,
            "our_cleaning_fee": 500.0,
            "our_total_price": 1697.0,
            "our_effective_nightly": 565.67,
        }
        comp_rates = [1000.0, 1100.0, 1150.0, 1200.0, 1250.0, 1300.0]
        eval_urgent = self.engine.evaluate_segment(segment_urgent, comp_rates)

        self.assertEqual(eval_urgent["priority_tier"], "URGENT_ACTION")
        self.assertEqual(eval_urgent["status"], "UNDERPRICED")
        self.assertTrue(eval_urgent["recommended_base_nightly"] > 399.0)
        self.assertIn("↑ Increase", eval_urgent["action_summary"])

        # Case 2: Moderate / Review (10-35% overpriced)
        segment_review = {
            "check_in": "2026-10-15",
            "check_out": "2026-10-18",
            "nights": 3,
            "lead_time_days": 45,
            "our_base_nightly": 1000.0,
            "our_cleaning_fee": 500.0,
            "our_total_price": 3500.0,
            "our_effective_nightly": 1166.67,
        }
        # Target ~ $970 -> diff ~ +20%
        comp_rates_review = [850.0, 900.0, 950.0, 970.0, 1000.0]
        eval_review = self.engine.evaluate_segment(segment_review, comp_rates_review)
        self.assertEqual(eval_review["priority_tier"], "MODERATE_ADJUSTMENT")
        self.assertIn("↓ Reduce", eval_review["action_summary"])

        # Case 3: On Target (<10% difference)
        segment_ontarget = {
            "check_in": "2026-11-15",
            "check_out": "2026-11-18",
            "nights": 3,
            "lead_time_days": 75,
            "our_base_nightly": 800.0,
            "our_cleaning_fee": 500.0,
            "our_total_price": 2900.0,
            "our_effective_nightly": 966.67,
        }
        comp_rates_ontarget = [920.0, 940.0, 960.0, 970.0, 980.0]
        eval_ontarget = self.engine.evaluate_segment(segment_ontarget, comp_rates_ontarget)
        self.assertEqual(eval_ontarget["priority_tier"], "INFORMATIONAL")
        self.assertEqual(eval_ontarget["action_summary"], "")

    def test_market_compression_sold_out(self):
        """When N <= 4, should flag near sold out market compression."""
        segment = {
            "check_in": "2027-02-12",
            "check_out": "2027-02-15",
            "nights": 3,
            "lead_time_days": 160,
            "our_base_nightly": 599.0,
            "our_cleaning_fee": 500.0,
            "our_total_price": 2297.0,
            "our_effective_nightly": 765.67,
        }
        # Only 2 comps left
        comp_rates = [1800.0, 2200.0]
        result = self.engine.evaluate_segment(segment, comp_rates)
        self.assertEqual(result["n_comps"], 2)
        self.assertEqual(result["sample_significance"], "VERY_LOW")

    def test_evaluate_segment_strictly_ignores_unregistered_comps(self):
        """Curated registry should strictly discard unvetted organic search listings."""
        segment = {
            "check_in": "2026-11-20",
            "check_out": "2026-11-23",
            "nights": 3,
            "lead_time_days": 75,
            "our_base_nightly": 800.0,
            "our_cleaning_fee": 500.0,
            "our_total_price": 2900.0,
            "our_effective_nightly": 966.67,
        }
        comp_metadata = [
            {
                "listing_id": "1077813310260513265",  # Registered comp (Desert Diamond)
                "name": "Desert Diamond",
                "effective_nightly": 1250.0,
            },
            {
                "listing_id": "999999999999999",  # Unregistered organic search hit
                "name": "Random Organic House",
                "effective_nightly": 400.0,
            },
        ]
        eval_res = self.engine.evaluate_segment(segment, [1250.0, 400.0], comp_metadata=comp_metadata)
        # Unregistered listing must be completely excluded from enriched comps list
        comp_ids = [str(c["listing_id"]) for c in eval_res["comps_list"]]
        self.assertIn("1077813310260513265", comp_ids)
        self.assertNotIn("999999999999999", comp_ids)
        # Only the 1 registered comp is evaluated
        self.assertEqual(eval_res["n_comps_adj"], 1)

    def test_evaluate_segment_strictly_ignores_disqualified_comps(self):
        """Disqualified comps must not skew raw percentiles, target values, or comp counts."""
        segment = {
            "check_in": "2026-11-20",
            "check_out": "2026-11-23",
            "nights": 3,
            "lead_time_days": 75,
            "our_base_nightly": 800.0,
            "our_cleaning_fee": 500.0,
            "our_total_price": 2900.0,
            "our_effective_nightly": 966.67,
        }
        self.engine.comp_registry["valid_comp_1"] = {
            "listing_id": "valid_comp_1",
            "name": "Valid Luxury Estate",
            "is_valid_comp": True,
            "desirability_ratio": 1.0,
        }
        self.engine.comp_registry["disq_comp_1"] = {
            "listing_id": "disq_comp_1",
            "name": "Disqualified Listing",
            "is_valid_comp": False,
            "desirability_ratio": 0.5,
            "validity_reason": "Low rating",
        }
        comp_metadata = [
            {"listing_id": "valid_comp_1", "effective_nightly": 800.0},
            {"listing_id": "disq_comp_1", "effective_nightly": 2500.0},
        ]
        rates = [800.0, 2500.0]
        eval_res = self.engine.evaluate_segment(segment, rates, comp_metadata=comp_metadata)

        # Raw counts and percentiles must reflect only the valid comp
        self.assertEqual(eval_res["n_comps"], 1)
        self.assertEqual(eval_res["comps_count"], 1)
        self.assertEqual(eval_res["comps_raw_count"], 1)
        self.assertEqual(eval_res["comp_target_eff"], 800.0)
        self.assertEqual(eval_res["comp_p50_eff"], 800.0)
        self.assertEqual(eval_res["comp_min_eff"], 800.0)
        self.assertEqual(eval_res["comp_max_eff"], 800.0)

        # Disqualified comp is retained in comps_list for UI with is_valid_comp=False
        comps_by_id = {c["listing_id"]: c for c in eval_res["comps_list"]}
        self.assertTrue(comps_by_id["valid_comp_1"]["is_valid_comp"])
        self.assertFalse(comps_by_id["disq_comp_1"]["is_valid_comp"])

    def test_evaluate_segment_market_compression_boost(self):
        """When market compression triggers (scarcity or velocity), target_pct should be boosted by +15% (capped at 90%)."""
        class MockSalesTracker:
            def get_target_percentile(self, lead_days, segment_type="weekend", aggressive=False):
                return 80.0 if aggressive else 65.0

            def load_registered_comps(self):
                return {f"comp_{i}": {} for i in range(100)}

            def detect_market_compression(self, check_in, check_out=None, total_cohort_count=None, current_available_count=None):
                is_comp = bool(current_available_count is not None and total_cohort_count and (current_available_count / total_cohort_count) < 0.20)
                return {
                    "is_compressed": is_comp,
                    "available_count": current_available_count,
                    "total_cohort_count": total_cohort_count,
                    "available_ratio": (current_available_count / total_cohort_count) if total_cohort_count else 1.0,
                    "reason": "High scarcity compression" if is_comp else "Normal availability",
                }

        self.engine.sales_tracker = MockSalesTracker()
        segment = {
            "check_in": "2026-11-01",
            "check_out": "2026-11-04",
            "nights": 3,
            "lead_time_days": 45,
            "segment_type": "weekend",
            "our_base_nightly": 900.0,
            "our_cleaning_fee": 500.0,
            "our_total_price": 3200.0,
            "our_effective_nightly": 1066.67,
        }
        # 15 comps available out of 100 registered comps -> 15% available (<20%) -> High Compression!
        rates = [700.0 + i * 20 for i in range(15)]
        eval_res = self.engine.evaluate_segment(segment, rates)
        self.assertTrue(eval_res["is_compression_surge"])
        self.assertEqual(eval_res["target_percentile"], 80.0)
        self.assertIn("compression_details", eval_res)
        self.assertIn("High compression", eval_res["action_summary"])

    def test_compression_surge_midweek_last_minute_with_sales_tracker(self):
        """Last-minute midweek interval (baseline 30%) retrieves aggressive p75 target 38% when compressed."""
        class MockSalesTracker:
            def get_target_percentile(self, lead_time_days, segment_type="weekend", aggressive=False):
                return 38.0 if aggressive else 30.0

            def load_registered_comps(self):
                return {f"comp_{i}": {} for i in range(100)}

            def detect_market_compression(self, check_in, check_out=None, total_cohort_count=None, current_available_count=None):
                return {
                    "is_compressed": True,
                    "available_count": 10,
                    "total_cohort_count": 100,
                    "available_ratio": 0.10,
                    "reason": "High scarcity compression: only 10/100 comps available",
                }

        self.engine.sales_tracker = MockSalesTracker()
        segment = {
            "check_in": "2026-09-20",
            "check_out": "2026-09-24",
            "nights": 4,
            "lead_time_days": 0,
            "segment_type": "midweek",
            "our_base_nightly": 399.0,
            "our_cleaning_fee": 500.0,
            "our_total_price": 2096.0,
            "our_effective_nightly": 524.0,
            "is_live_scan": True,
        }
        rates = [500.0 + i * 15 for i in range(10)]
        eval_res = self.engine.evaluate_segment(segment, rates)
        self.assertTrue(eval_res["is_compression_surge"])
        self.assertEqual(eval_res["target_percentile"], 38.0)

    def test_compression_surge_midweek_last_minute_without_sales_tracker(self):
        """Scarcity fallback uses last-minute midweek aggressive p75 target 38% even when sales_tracker is None."""
        self.engine.sales_tracker = None
        segment = {
            "check_in": "2026-09-20",
            "check_out": "2026-09-24",
            "nights": 4,
            "lead_time_days": 0,
            "segment_type": "midweek",
            "our_base_nightly": 399.0,
            "our_cleaning_fee": 500.0,
            "our_total_price": 2096.0,
            "our_effective_nightly": 524.0,
            "is_live_scan": True,
        }
        # 10 comps available (<20 comps threshold)
        comps_meta = [{"listing_id": f"comp_{i}", "effective_nightly": 500.0 + i * 15, "is_valid_comp": True} for i in range(10)]
        rates = [c["effective_nightly"] for c in comps_meta]
        eval_res = self.engine.evaluate_segment(segment, rates, comp_metadata=comps_meta)
        self.assertTrue(eval_res["is_compression_surge"])
        self.assertEqual(eval_res["target_percentile"], 38.0)
        self.assertEqual(eval_res["compression_details"]["available_count"], 10)

    def test_compression_surge_standard_two_arg_invocation_without_sales_tracker(self):
        """Standard evaluate_segment(segment, rates) without sales_tracker or comp_metadata triggers scarcity fallback to aggressive p75 target 38%."""
        self.engine.sales_tracker = None
        segment = {
            "check_in": "2026-09-20",
            "check_out": "2026-09-24",
            "nights": 4,
            "lead_time_days": 0,
            "segment_type": "midweek",
            "our_base_nightly": 399.0,
            "our_cleaning_fee": 500.0,
            "our_total_price": 2096.0,
            "our_effective_nightly": 524.0,
        }
        rates = [500.0 + i * 15 for i in range(10)]
        eval_res = self.engine.evaluate_segment(segment, rates)
        self.assertTrue(eval_res["is_compression_surge"])
        self.assertEqual(eval_res["target_percentile"], 38.0)
        self.assertEqual(eval_res["compression_details"]["available_count"], 10)

    def test_compression_boundary_at_20_comps(self):
        """Boundary at 20 comps: 20 available comps is NOT compressed (<20 comps required)."""
        self.engine.sales_tracker = None
        segment = {
            "check_in": "2026-09-20",
            "check_out": "2026-09-24",
            "nights": 4,
            "lead_time_days": 0,
            "segment_type": "midweek",
            "our_base_nightly": 399.0,
            "our_cleaning_fee": 500.0,
            "our_total_price": 2096.0,
            "our_effective_nightly": 524.0,
        }
        rates = [500.0 + i * 10 for i in range(20)]
        eval_res = self.engine.evaluate_segment(segment, rates)
        self.assertFalse(eval_res["is_compression_surge"])
        self.assertEqual(eval_res["target_percentile"], 30.0)

    def test_get_floor_for_interval_regular(self):
        """Regular midweek and weekend intervals should return $300 and $450 operational floors."""
        floor_mid = self.engine.get_floor_for_interval("2026-09-21", "2026-09-24", "midweek")
        self.assertEqual(floor_mid, 300.0)

        floor_wkd = self.engine.get_floor_for_interval("2026-09-24", "2026-09-27", "weekend")
        self.assertEqual(floor_wkd, 450.0)

    def test_get_floor_for_interval_holidays(self):
        """Holiday intervals should enforce holiday floors over standard operational floors."""
        # Thanksgiving 2026 (floor_rate 499)
        floor_tg = self.engine.get_floor_for_interval("2026-11-26", "2026-11-29", "weekend", period_name="Thanksgiving 26")
        self.assertEqual(floor_tg, 499.0)

        # Christmas & New Year 2026 (floor_rate 499)
        floor_xmas = self.engine.get_floor_for_interval("2026-12-24", "2026-12-28", "midweek", period_name="Christmas & New Year 2026")
        self.assertEqual(floor_xmas, 499.0)

        # Holy Week 2027 (split pricing: midweek 699, weekend 1049)
        floor_hw_mid = self.engine.get_floor_for_interval("2027-03-22", "2027-03-25", "midweek", period_name="Holy Week 27")
        self.assertEqual(floor_hw_mid, 699.0)

        floor_hw_wkd = self.engine.get_floor_for_interval("2027-03-25", "2027-03-28", "weekend", period_name="Holy Week 27")
        self.assertEqual(floor_hw_wkd, 1049.0)

        # Memorial Day 2027 (floor_rate 699)
        floor_mem = self.engine.get_floor_for_interval("2027-05-28", "2027-05-31", "weekend", period_name="Memorial Day 27")
        self.assertEqual(floor_mem, 699.0)

    def test_evaluate_segment_clamps_to_floor(self):
        """Interval recommendation must be clamped to effective floor when target is below floor."""
        # September midweek interval with low comps that would result in ~$177 base
        low_comps = [250.0, 260.0, 270.0, 280.0]
        segment = {
            "check_in": "2026-09-21",
            "check_out": "2026-09-24",
            "nights": 3,
            "lead_time_days": 1,
            "segment_type": "midweek",
            "our_base_nightly": 399.0,
            "our_cleaning_fee": 500.0,
            "our_total_price": 1697.0,
            "our_effective_nightly": 565.67,
        }
        res = self.engine.evaluate_segment(segment, low_comps)
        self.assertEqual(res["floor_rate"], 300.0)
        self.assertEqual(res["recommended_base_nightly"], 300.0)
        self.assertIn("↓ Reduce $399 → $300", res["action_summary"])

    def test_evaluate_segment_churn_threshold(self):
        """Interval recommendation should hold current base if proposed change is < 5%."""
        # Our base $400 (above $300 midweek floor), comp prices calculate rec base of ~$403 (+0.75% < 5%)
        comps = [560.0, 565.0, 570.0, 575.0]
        segment = {
            "check_in": "2026-10-15",
            "check_out": "2026-10-18",
            "nights": 3,
            "lead_time_days": 25,
            "segment_type": "midweek",
            "our_base_nightly": 400.0,
            "our_cleaning_fee": 500.0,
            "our_total_price": 1700.0,
            "our_effective_nightly": 566.67,
        }
        res = self.engine.evaluate_segment(segment, comps)
        # Should hold at $400 rather than proposing a minor churn change
        self.assertEqual(res["recommended_base_nightly"], 400.0)

    def test_get_floor_for_interval_calendar_fallback(self):
        """Calendar date fallback should match holidays even when period_name is omitted."""
        # Christmas & New Year in December (from_date/period_name empty)
        floor_xmas_dec = self.engine.get_floor_for_interval("2026-12-24", "2026-12-28", "midweek", period_name="")
        self.assertEqual(floor_xmas_dec, 499.0)

        # Christmas & New Year spanning into January
        floor_xmas_jan = self.engine.get_floor_for_interval("2027-01-01", "2027-01-03", "weekend", period_name="")
        self.assertEqual(floor_xmas_jan, 499.0)

        # Thanksgiving in late November
        floor_tg = self.engine.get_floor_for_interval("2026-11-26", "2026-11-29", "weekend", period_name="")
        self.assertEqual(floor_tg, 499.0)

    def test_get_floor_for_interval_datetime_instances(self):
        """Datetime and date objects should be normalized without raising TypeError."""
        from datetime import datetime, date
        dt_in = datetime(2026, 11, 26, 15, 0)
        dt_out = datetime(2026, 11, 29, 11, 0)
        floor_dt = self.engine.get_floor_for_interval(dt_in, dt_out, "weekend")
        self.assertEqual(floor_dt, 499.0)

        d_in = date(2026, 9, 21)
        d_out = date(2026, 9, 24)
        floor_d = self.engine.get_floor_for_interval(d_in, d_out, "midweek")
        self.assertEqual(floor_d, 300.0)

    def test_evaluate_segment_sub_floor_shows_action(self):
        """When our base is below floor, action to reach the floor is displayed even if abs_diff < 10%."""
        # Our base $280 (< $300 floor), comp prices suggest target eff around $433 (base $266 -> clamped to $300)
        comps = [430.0, 435.0, 440.0]
        segment = {
            "check_in": "2026-09-21",
            "check_out": "2026-09-24",
            "nights": 3,
            "lead_time_days": 1,
            "segment_type": "midweek",
            "our_base_nightly": 280.0,
            "our_cleaning_fee": 500.0,
            "our_total_price": 1340.0,
            "our_effective_nightly": 446.67,
        }
        res = self.engine.evaluate_segment(segment, comps)
        self.assertEqual(res["floor_rate"], 300.0)
        self.assertEqual(res["recommended_base_nightly"], 300.0)
        self.assertIn("↑ Increase $280 → $300", res["action_summary"])

    def test_evaluate_segment_churn_deadband_post_floor_clamping(self):
        """Churn deadband must evaluate against the floor-clamped target rate."""
        # Our base $310, floor $300. Comps crash to $200.
        # Clamped target is $300. Change from $310 to $300 is 3.22% (< 5%), so holds at $310.
        crash_comps = [200.0, 205.0, 210.0]
        segment = {
            "check_in": "2026-09-21",
            "check_out": "2026-09-24",
            "nights": 3,
            "lead_time_days": 1,
            "segment_type": "midweek",
            "our_base_nightly": 310.0,
            "our_cleaning_fee": 500.0,
            "our_total_price": 1430.0,
            "our_effective_nightly": 476.67,
        }
        res = self.engine.evaluate_segment(segment, crash_comps)
        self.assertEqual(res["floor_rate"], 300.0)
        # Clamped rec base before deadband is 300.0; diff is 10/310 = 3.22% < 5%, so holds at 310.0
        self.assertEqual(res["recommended_base_nightly"], 310.0)

    def test_get_floor_for_interval_post_holiday_drop(self):
        """Post-holiday midweek intervals must drop back to the $300 operational floor."""
        # Tuesday after Memorial Day 2027 (June 1 - June 3)
        self.assertEqual(self.engine.get_floor_for_interval("2027-06-01", "2027-06-03", "midweek"), 300.0)
        # Tuesday after Labor Day 2026 (Sept 8 - Sept 11)
        self.assertEqual(self.engine.get_floor_for_interval("2026-09-08", "2026-09-11", "midweek"), 300.0)
        # Monday after Thanksgiving 2026 (Nov 30 - Dec 3)
        self.assertEqual(self.engine.get_floor_for_interval("2026-11-30", "2026-12-03", "midweek"), 300.0)
        # Tuesday after Columbus Day 2026 (Oct 13 - Oct 16)
        self.assertEqual(self.engine.get_floor_for_interval("2026-10-13", "2026-10-16", "midweek"), 300.0)
        # April standard intervals in 2027 (not Holy Week)
        self.assertEqual(self.engine.get_floor_for_interval("2027-04-01", "2027-04-04", "weekend"), 450.0)
        self.assertEqual(self.engine.get_floor_for_interval("2027-04-04", "2027-04-08", "midweek"), 300.0)

    def test_get_easter_calculation(self):
        """Verify Western Easter Sunday algorithm matches verified calendar dates."""
        from src.analytics import _get_easter
        from datetime import date
        self.assertEqual(_get_easter(2026), date(2026, 4, 5))
        self.assertEqual(_get_easter(2027), date(2027, 3, 28))
        self.assertEqual(_get_easter(2028), date(2028, 4, 16))

    def test_evaluate_segment_zero_comps_sub_floor(self):
        """Zero-comp fallback must raise sub-floor rates to effective floor."""
        segment = {
            "check_in": "2026-09-21",
            "check_out": "2026-09-24",
            "nights": 3,
            "lead_time_days": 1,
            "segment_type": "midweek",
            "our_base_nightly": 280.0,
            "our_cleaning_fee": 500.0,
            "our_total_price": 1340.0,
            "our_effective_nightly": 446.67,
        }
        res = self.engine.evaluate_segment(segment, [])
        self.assertEqual(res["floor_rate"], 300.0)
        self.assertEqual(res["recommended_base_nightly"], 300.0)
        self.assertIn("↑ Increase $280 → $300", res["action_summary"])


if __name__ == "__main__":
    unittest.main()

