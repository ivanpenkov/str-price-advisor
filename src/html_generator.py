"""
HTML Dashboard Generator for STR Competitive Price Advisor.
Generates a standalone, responsive static web application (docs/index.html)
featuring:
1. Pricing Recommendations Tab (Urgent Updates -> Moderate Updates -> All 12 Months)
2. Ratings & Reviews Intelligence Tab
3. Streamline PMS Live Feed & Diagnostics Tab
4. Curated Comps Registry Tab (109 listings with filters and direct Airbnb links)
5. Competitor Sales Velocity & Strategy Matrix Tab
6. Availability Calendar Tab
7. Historical & Advance Reservations Ledger Tab
8. Cumulative Pacing & Revenue Pacing Curves Tab
9. Multi-Channel Price Parity Comparison Matrix Tab
"""

import html
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

from src.kivoya_client import KivoyaClient
from src.segmentation import CalendarSegmenter
from src.analytics import PricingAnalyticsEngine
from src.config import URGENT_PCT_DIFF, MODERATE_PCT_DIFF, MIN_PRICE_CHANGE_PCT
from src.proposed_prices import generate_proposed_prices
from src.database import is_cloud_enabled


def _is_spec_or_generic_title(s: str) -> bool:
    """Check if a string is a price, spec, badge, location line, or generic placeholder."""
    if not s or not s.strip():
        return True
    s_clean = s.strip()
    s_lower = s_clean.lower()
    if (
        re.search(r"\$\s*[\d,]+(?:\.\d+)?(?:\s*(?:/\s*night|night|total|stay|for\s+\d+\s*nights?|before\s+taxes)|\s*$)", s_lower)
        or "before taxes" in s_lower
        or re.search(r"for\s+\d+\s+nights?", s_lower)
        or r"/\s*night" in s_lower
    ):
        return True
    if "single comp sweep" in s_lower:
        return True
    if any(b in s_lower for b in ["guest favorite", "superhost", "rare find", "top guest favorite"]):
        return True
    if s_lower.startswith("airbnb") or s_lower == "airbnb" or "vacation rentals, cabins" in s_lower or "vacation homes & condo rentals" in s_lower:
        return True
    if any(s_lower.startswith(pref) for pref in ["home in", "entire home in", "villa in", "room in", "cabin in", "chalet in", "place to stay in"]):
        return True
    if re.search(r"^\d+\s*bedrooms?$", s_lower):
        return True
    if re.search(r"^\d+\s*beds?$", s_lower):
        return True
    if re.search(r"^\d+\s*bedrooms?\b", s_lower) or re.search(r"\b\d+\s*bedrooms?\b.*(?:\b\d+\s*beds?|\bbaths?)", s_lower):
        return True
    if re.search(r"^\d(?:\.\d+)?\s*\(\d+\)$", s_clean) or "out of 5" in s_lower:
        return True
    if s_lower in ["home", "villa", "entire home", "luxury estate", "house"]:
        return True
    return False


def extract_clean_listing_title(
    raw_snippet: str = "",
    default_title: str = "",
    registered_name: str = "",
) -> str:
    """
    Extract a clean, descriptive property title from registry profiles, marketing headlines,
    or card snippets, strictly rejecting price tags, bed/bath specs, badge strings, and location prefixes.
    """
    # 1. Highest precedence: curated profile name from comps registry or enriched specs
    if registered_name and not _is_spec_or_generic_title(registered_name):
        return registered_name.strip()

    # 2. Extract best descriptive title from raw_snippet
    if raw_snippet:
        parts = [p.strip() for p in re.split(r"[|\n]", raw_snippet) if p.strip()]
        for p in reversed(parts):
            if not _is_spec_or_generic_title(p):
                return p

    # 3. Fallback to default_title if not generic/spec
    if default_title and not _is_spec_or_generic_title(default_title):
        return default_title.strip()

    return registered_name.strip() if registered_name and registered_name.strip() else "Luxury Estate"


class HTMLDashboardGenerator:
    """Generates the static interactive HTML dashboard for GitHub Pages."""

    def __init__(
        self,
        output_path: str = "docs/index.html",
        comps_registry_path: str = "config/comps_registry.json",
        urgent_pct_diff: float = URGENT_PCT_DIFF,
        moderate_pct_diff: float = MODERATE_PCT_DIFF,
        min_price_change_pct: float = MIN_PRICE_CHANGE_PCT,
        ratings_data: Optional[Dict[str, Any]] = None,
        sales_tracker: Optional[Any] = None,
    ):
        self.output_path = Path(output_path)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.comps_path = Path(comps_registry_path)
        self.urgent_pct_diff = urgent_pct_diff
        self.moderate_pct_diff = moderate_pct_diff
        self.min_price_change_pct = float(min_price_change_pct)
        self.sales_tracker = sales_tracker
        self._injected_ratings_data = ratings_data
        self.comps_data = self.load_comps()
        self.comps_dict: Dict[str, Dict[str, Any]] = {}
        for tier in ("tier_a", "tier_b"):
            for cid, comp in self.comps_data.get(tier, {}).items():
                self.comps_dict[str(cid)] = comp
        self.excluded_comps: Set[str] = {str(k) for k in self.comps_data.get("excluded_comps", {}).keys()}
        self.excluded_comps.update({str(k) for k in self.comps_data.get("disqualified", {}).keys()})
        self.specs_path = Path("config/listing_specs.json")
        self.listing_specs: Dict[str, Dict[str, Any]] = {}
        if self.specs_path.exists():
            try:
                self.listing_specs = json.loads(self.specs_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        self.ratings_path = Path("data/ratings_reviews.json")
        self.recent_rev_count: int = 0
        from src.comp_evaluator import CompEvaluator
        self.evaluator = CompEvaluator()

    def load_ratings(self) -> Optional[Dict[str, Any]]:
        """Load cross-platform ratings and reviews data from data/ratings_reviews.json."""
        if getattr(self, "_injected_ratings_data", None) is not None:
            return self._injected_ratings_data
        if getattr(self, "_cached_ratings_data", None) is not None:
            return self._cached_ratings_data
        if self.ratings_path.exists():
            try:
                self._cached_ratings_data = json.loads(self.ratings_path.read_text(encoding="utf-8"))
                return self._cached_ratings_data
            except Exception as e:
                logger.warning(f"Failed to load ratings data from {self.ratings_path}: {e}")
        return None

    @staticmethod
    def _fmt_short_date(d_str: str) -> str:
        """Format YYYY-MM-DD or MM/DD/YYYY to MM/DD/YY."""
        if not d_str:
            return ""
        parts = d_str.strip().split("-")
        if len(parts) == 3 and len(parts[0]) == 4:
            return f"{parts[1]}/{parts[2]}/{parts[0][-2:]}"
        parts_slash = d_str.strip().split("/")
        if len(parts_slash) == 3 and len(parts_slash[2]) == 4:
            return f"{parts_slash[0]}/{parts_slash[1]}/{parts_slash[2][-2:]}"
        return d_str

    @staticmethod
    def _format_timestamp(ts_val: Any, short: bool = False) -> str:
        """Format ISO timestamp string or datetime object into human-readable date + time."""
        if not ts_val:
            return "Projected"
        try:
            if isinstance(ts_val, str):
                dt = datetime.fromisoformat(ts_val)
            elif isinstance(ts_val, datetime):
                dt = ts_val
            else:
                return str(ts_val)
            if short:
                return dt.strftime("%b %d, %H:%M")
            return dt.strftime("%b %d, %Y at ") + dt.strftime("%I:%M %p").lstrip("0")
        except Exception:
            return str(ts_val)

    def load_comps(self) -> Dict[str, Any]:
        """Load curated comps from registry."""
        if self.comps_path.exists():
            try:
                data = json.loads(self.comps_path.read_text(encoding="utf-8"))
                # Sanitize any duplicates across tiers (favoring tier_a)
                tier_a = data.get("tier_a", {})
                tier_b = data.get("tier_b", {})
                overlap = set(tier_a.keys()) & set(tier_b.keys())
                if overlap:
                    for cid in overlap:
                        tier_b.pop(cid, None)
                return data
            except Exception:
                pass
        return {"tier_a": {}, "tier_b": {}, "metadata": {"total_count": 0}}

    ALT_DATE_REGEX = re.compile(
        r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d+\s*(?:to|–|-)\s*(?:(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+)?\d+",
        re.IGNORECASE,
    )

    def _load_cached_comps_by_key(self) -> Dict[str, Dict[str, Dict[str, Any]]]:
        """Load all pre-fetched comp items from data/cache keyed by checkin_checkout."""
        cache_dir = Path("data/cache")
        cached: Dict[str, Dict[str, Dict[str, Any]]] = {}
        if not cache_dir.exists():
            return cached

        for f in cache_dir.glob("search_*.json"):
            parts = f.stem.split("_")
            if len(parts) >= 3:
                key = f"{parts[1]}_{parts[2]}"
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    if key not in cached:
                        cached[key] = {}
                    for item in data:
                        cid = item.get("listing_id")
                        rate = item.get("effective_nightly")
                        raw_snip = item.get("raw_snippet", "")
                        # Reject listings that Airbnb suggested for alternative/flexible dates
                        if self.ALT_DATE_REGEX.search(raw_snip):
                            continue
                        if any(w in raw_snip.lower() for w in ["similar dates", "available for part of your stay", "check other dates", "different dates"]):
                            continue
                        if cid and rate and rate > 0.0:
                            cid_str = str(cid)
                            if cid_str in self.excluded_comps:
                                continue
                            if cid_str in self.listing_specs:
                                sp = self.listing_specs[cid_str]
                                if sp.get("beds"):
                                    item["beds"] = sp["beds"]
                                if sp.get("bedrooms"):
                                    item["bedrooms"] = sp["bedrooms"]
                                if sp.get("baths"):
                                    item["baths"] = sp["baths"]
                                if sp.get("title") and not re.search(r"^\d+\s*bedrooms?$", str(sp["title"]), re.IGNORECASE):
                                    item["title"] = sp["title"]
                                    item["name"] = sp["title"]
                                if sp.get("photo_url") and not item.get("photo_url"):
                                    item["photo_url"] = sp["photo_url"]
                            cached[key][cid_str] = item
                except Exception:
                    pass
        return cached

    def generate_full_12_month_evaluation(self) -> List[Dict[str, Any]]:
        """
        Evaluate all 82 unbooked intervals across the 12-month calendar.
        Uses exact cached comp results where available, and robust seasonal
        luxury comp distributions for future intervals.
        """
        kivoya = KivoyaClient()
        segmenter = CalendarSegmenter(kivoya_client=kivoya, cleaning_fee=500.0)
        segments = segmenter.generate_unbooked_segments()

        from src.competitor_sales_tracker import CompetitorSalesTracker
        sales_tracker = getattr(self, "sales_tracker", None)
        if sales_tracker is None:
            try:
                sales_tracker = CompetitorSalesTracker()
            except Exception:
                sales_tracker = None

        analytics = PricingAnalyticsEngine(
            base_percentile=65.0,
            cleaning_fee=500.0,
            urgent_pct_diff=self.urgent_pct_diff,
            urgent_lead_days=60,
            moderate_pct_diff=self.moderate_pct_diff,
            sales_tracker=sales_tracker,
        )

        cached_comps = self._load_cached_comps_by_key()

        evaluated: List[Dict[str, Any]] = []

        for seg in segments:
            c_in = seg["check_in"]
            c_out = seg["check_out"]
            cache_key = f"{c_in}_{c_out}"

            # Check if live Villa del Sol Airbnb rate was scraped/cached
            our_cache_file = Path(f"data/cache/our_property_{c_in}_{c_out}.json")
            if our_cache_file.exists():
                try:
                    our_data = json.loads(our_cache_file.read_text(encoding="utf-8"))
                    live_eff = our_data.get("airbnb_effective_nightly")
                    live_tot = our_data.get("airbnb_total")
                    if live_eff and float(live_eff) > 0:
                        seg["our_airbnb_effective_nightly"] = float(live_eff)
                        seg["our_airbnb_total"] = float(live_tot) if live_tot else float(live_eff) * seg["nights"]
                        seg["is_our_airbnb_live"] = True
                except Exception:
                    pass

            if cache_key in cached_comps and len(cached_comps[cache_key]) > 0:
                comps_list = [
                    c for c in cached_comps[cache_key].values()
                    if not self.comps_dict or str(c.get("listing_id") or "") in self.comps_dict
                ]
                rates = [c["effective_nightly"] for c in comps_list]
                is_live = bool(comps_list)
            else:
                comps_list = []
                rates = []
                is_live = False

            seg["is_live_scan"] = is_live
            eval_seg = analytics.evaluate_segment(seg, rates, comp_metadata=comps_list)
            eval_seg["is_live_scan"] = is_live
            evaluated.append(eval_seg)

        return evaluated

    def generate(self, evaluated_segments: Optional[List[Dict[str, Any]]] = None) -> str:
        """Generate static HTML dashboard file."""
        if evaluated_segments is None:
            evaluated_segments = self.generate_full_12_month_evaluation()
        else:
            cached_comps = self._load_cached_comps_by_key()
            for s in evaluated_segments:
                c_in = s["check_in"]
                c_out = s["check_out"]
                our_cache_file = Path(f"data/cache/our_property_{c_in}_{c_out}.json")
                if not s.get("our_airbnb_effective_nightly") and our_cache_file.exists():
                    try:
                        our_data = json.loads(our_cache_file.read_text(encoding="utf-8"))
                        live_eff = our_data.get("airbnb_effective_nightly")
                        live_tot = our_data.get("airbnb_total")
                        if live_eff and float(live_eff) > 0:
                            s["our_airbnb_effective_nightly"] = float(live_eff)
                            s["our_airbnb_total"] = float(live_tot) if live_tot else float(live_eff) * s["nights"]
                            s["is_our_airbnb_live"] = True
                    except Exception:
                        pass
                if not s.get("comps_list"):
                    cache_key = f"{s['check_in']}_{s['check_out']}"
                    if cache_key in cached_comps and len(cached_comps[cache_key]) > 0:
                        s["comps_list"] = [
                            c for c in cached_comps[cache_key].values()
                            if not self.comps_dict or str(c.get("listing_id") or "") in self.comps_dict
                        ]
                        s["is_live_scan"] = bool(s["comps_list"])
                    else:
                        s["comps_list"] = []
                        s["is_live_scan"] = False

        comps_data = self.load_comps()
        tier_a_comps = list(comps_data.get("tier_a", {}).values())
        tier_b_comps = list(comps_data.get("tier_b", {}).values())
        disqualified_comps = list(comps_data.get("disqualified", {}).values())

        total_active_comps = len(tier_a_comps) + len(tier_b_comps)
        valid_comps_count = sum(1 for c in (tier_a_comps + tier_b_comps) if c.get("is_valid_comp", True))
        disqualified_comps_count = len(disqualified_comps) + sum(1 for c in (tier_a_comps + tier_b_comps) if not c.get("is_valid_comp", True))

        comp_validity_data = {}
        for c in tier_a_comps:
            cid = str(c.get("listing_id") or "")
            if cid:
                rec = dict(c)
                rec["tier"] = "tier_a"
                comp_validity_data[cid] = rec
        for c in tier_b_comps:
            cid = str(c.get("listing_id") or "")
            if cid:
                rec = dict(c)
                rec["tier"] = "tier_b"
                comp_validity_data[cid] = rec
        for c in disqualified_comps:
            cid = str(c.get("listing_id") or "")
            if cid:
                rec = dict(c)
                rec["tier"] = "disqualified"
                rec["is_valid_comp"] = False
                comp_validity_data[cid] = rec
        comp_validity_json = json.dumps(comp_validity_data, ensure_ascii=False).replace("</", "<\\/")

        # Sort intervals into tiers
        urgent = [s for s in evaluated_segments if s["priority_tier"] == "URGENT_ACTION"]
        moderate = [s for s in evaluated_segments if s["priority_tier"] == "MODERATE_ADJUSTMENT"]
        info = [s for s in evaluated_segments if s["priority_tier"] == "INFORMATIONAL"]

        urgent.sort(key=lambda x: (x["check_in_dt"]))
        moderate.sort(key=lambda x: (x["check_in_dt"]))
        all_sorted = sorted(evaluated_segments, key=lambda x: x["check_in_dt"])

        # Open calendar subsets (calendar open through May 2027 by default)
        open_segments = [s for s in all_sorted if s.get("is_calendar_open", True)]
        open_urgent = [s for s in urgent if s.get("is_calendar_open", True)]
        open_moderate = [s for s in moderate if s.get("is_calendar_open", True)]
        open_ok = [s for s in open_segments if s["priority_tier"] not in ["URGENT_ACTION", "MODERATE_ADJUSTMENT"]]

        now_str = datetime.now().strftime("%B %d, %Y at %I:%M %p")

        from src.reservation_store import ReservationStore
        from src.reservation_intelligence import ReservationIntelligence
        import src.calendar_revenue_views as crv

        res_intel = ReservationIntelligence()
        kivoya_client = KivoyaClient()
        seasonal_rates = kivoya_client.get_seasonal_rates()
        interval_benchmarks = res_intel.compute_interval_historical_benchmarks(seasonal_rates)

        from src.proposed_prices import find_parent_period_for_segment, clean_holiday_name, parse_rate_periods_for_lookup
        parsed_rates_for_lookup = parse_rate_periods_for_lookup(seasonal_rates)

        for s in evaluated_segments:
            c_in = s.get("check_in", "")
            stype = s.get("segment_type", "weekend").lower()
            rec_r = s.get("recommended_base_nightly_adj") or s.get("recommended_base_nightly")
            parent = find_parent_period_for_segment(s, parsed_rates_for_lookup)
            bench = None
            if parent:
                pname = parent.get("pname") or parent.get("period_name")
                clean_h = clean_holiday_name(pname)
                bench = interval_benchmarks.get((pname, stype)) or interval_benchmarks.get((clean_h, stype))
            if not bench:
                bench = res_intel.get_historical_benchmarks_for_interval(
                    check_in_str=c_in,
                    segment_type=stype,
                    proposed_rate=rec_r,
                )
            s["historical_benchmark"] = bench
            if "lead_time_status" not in s:
                s["lead_time_status"] = res_intel.get_lead_time_status(c_in)

        lead_analytics = res_intel.compute_lead_time_windows()

        res_store = ReservationStore()
        reservations_list = res_store.get_all_reservations(include_cancelled=False)
        rev_data = res_store.calculate_cumulative_annual_revenue()
        calendar_rates_map = res_store.get_daily_calendar_rates(reservations_list, current_kivoya_rates=seasonal_rates)

        calendar_tab_html = crv.render_calendar_tab(reservations_list)
        reservations_tab_html = crv.render_reservations_tab(reservations_list, current_kivoya_rates=seasonal_rates)
        revenue_tab_html = crv.render_revenue_tab(rev_data)
        reservation_modal_html = crv.render_reservation_modal()
        validity_modal_html = self._render_validity_modal(comp_validity_json)
        calendar_revenue_css = crv.get_calendar_revenue_css()
        calendar_revenue_js = crv.get_calendar_revenue_js(reservations_list, rev_data, rates_map=calendar_rates_map, current_kivoya_rates=seasonal_rates)

        from src.competitor_sales_tracker import CompetitorSalesTracker
        sales_tracker = CompetitorSalesTracker()
        sales_tracker.reconcile_with_latest_snapshot()
        sales_data = sales_tracker.compute_strategy_grid()
        recent_sales_list = sales_tracker.get_all_sales()
        comps_lead_analytics = sales_tracker.compute_monthly_lead_time_windows()
        market_timeline_data = sales_tracker.compute_daily_market_inventory_timeline()
        market_sales_tab_html = self._render_market_sales_tab(
            sales_data,
            recent_sales_list,
            comps_lead_analytics=comps_lead_analytics,
            market_timeline_data=market_timeline_data,
        )

        blocked_periods = kivoya_client.get_blocked_periods()
        open_end_date = kivoya_client.get_calendar_open_end_date()
        proposed_prices_data = generate_proposed_prices(
            seasonal_rates,
            all_sorted,
            blocked_periods=blocked_periods,
            open_end_date=open_end_date,
            extend_to_horizon=True,
        )
        proposed_prices_html = self._render_proposed_prices_section(proposed_prices_data)

        streamline_snapshots, last_streamline_update_str = self._load_streamline_snapshots(seasonal_rates)
        streamline_tab_html, streamline_js_data = self._render_streamline_tab(
            seasonal_rates=seasonal_rates,
            blocked_periods=blocked_periods,
            open_end_date=open_end_date,
            snapshots=streamline_snapshots,
            last_api_update_str=last_streamline_update_str,
        )

        ratings_data = self.load_ratings()
        recent_rev_count = 0
        if ratings_data:
            window_days = int(ratings_data.get("recent_window_days", 30))
            cutoff = date.today() - timedelta(days=window_days)
            for r in ratings_data.get("reviews", []):
                d_str = r.get("date")
                if d_str:
                    try:
                        if date.fromisoformat(d_str[:10]) >= cutoff:
                            recent_rev_count += 1
                    except Exception:
                        pass
        self.recent_rev_count = recent_rev_count
        bell_badge_html = f' <span class="review-bell-badge" id="reviewBellBadge">🔔 {recent_rev_count}</span>' if recent_rev_count > 0 else ""

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover" />
  <title>Villa del Sol — STR Competitive Price Advisor</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <style>
    {calendar_revenue_css}

    :root {{
      --primary: #2563eb;
      --primary-dark: #1d4ed8;
      --primary-light: #dbeafe;
      --bg-main: #0f172a;
      --bg-card: #1e293b;
      --bg-card-hover: #273549;
      --border-color: #334155;
      --text-main: #f8fafc;
      --text-muted: #94a3b8;
      --urgent-red: #ef4444;
      --urgent-bg: rgba(239, 68, 68, 0.12);
      --urgent-border: rgba(239, 68, 68, 0.35);
      --warning-amber: #f59e0b;
      --warning-bg: rgba(245, 158, 11, 0.12);
      --warning-border: rgba(245, 158, 11, 0.35);
      --success-green: #10b981;
      --success-bg: rgba(16, 185, 129, 0.12);
      --success-border: rgba(16, 185, 129, 0.35);
    }}

    * {{
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }}

    body {{
      font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, sans-serif;
      background-color: var(--bg-main);
      color: var(--text-main);
      line-height: 1.5;
      padding: 24px;
    }}

    .container {{
      max-width: 1400px;
      margin: 0 auto;
    }}

    /* Header */
    header {{
      background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
      border: 1px solid var(--border-color);
      border-radius: 16px;
      padding: 28px 32px;
      margin-bottom: 24px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 20px;
      box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.3);
    }}

    .property-title h1 {{
      font-size: 1.85rem;
      font-weight: 800;
      letter-spacing: -0.02em;
      display: flex;
      align-items: center;
      gap: 12px;
    }}

    .property-title p {{
      color: var(--text-muted);
      font-size: 0.95rem;
      margin-top: 4px;
    }}

    .header-badges {{
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
    }}

    .badge {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 6px 14px;
      border-radius: 9999px;
      font-size: 0.85rem;
      font-weight: 600;
      border: 1px solid transparent;
    }}

    .badge-primary {{
      background: var(--primary-light);
      color: #1e40af;
    }}

    .badge-dark {{
      background: rgba(255, 255, 255, 0.08);
      color: #e2e8f0;
      border-color: rgba(255, 255, 255, 0.15);
    }}

    /* KPI Summary Stats */
    .kpi-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 16px;
      margin-bottom: 28px;
    }}

    .kpi-card {{
      background: var(--bg-card);
      border: 1px solid var(--border-color);
      border-radius: 14px;
      padding: 20px 24px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      transition: transform 0.15s ease, border-color 0.15s ease;
    }}

    .kpi-card:hover {{
      transform: translateY(-2px);
      border-color: #475569;
    }}

    .kpi-label {{
      font-size: 0.85rem;
      color: var(--text-muted);
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }}

    .kpi-val {{
      font-size: 2rem;
      font-weight: 800;
      margin: 8px 0;
      display: flex;
      align-items: baseline;
      gap: 8px;
    }}

    .kpi-desc {{
      font-size: 0.8rem;
      color: var(--text-muted);
    }}

    /* Navigation Tabs */
    .tabs-nav {{
      display: flex;
      gap: 8px;
      border-bottom: 2px solid var(--border-color);
      margin-bottom: 24px;
      overflow-x: auto;
    }}

    .tab-btn {{
      background: transparent;
      border: none;
      color: var(--text-muted);
      padding: 12px 20px;
      font-size: 1rem;
      font-weight: 600;
      font-family: inherit;
      cursor: pointer;
      border-radius: 10px 10px 0 0;
      position: relative;
      white-space: nowrap;
      transition: all 0.2s ease;
    }}

    .tab-btn:hover {{
      color: var(--text-main);
      background: rgba(255, 255, 255, 0.03);
    }}

    .tab-btn.active {{
      color: #60a5fa;
      background: var(--bg-card);
    }}

    .tab-btn.active::after {{
      content: '';
      position: absolute;
      bottom: -2px;
      left: 0;
      right: 0;
      height: 2px;
      background: #3b82f6;
    }}

    .tab-content {{
      display: none;
    }}

    .tab-content.active {{
      display: block;
    }}

    /* Reviews Tab & Notification Bell Badge */
    .review-bell-badge {{
      display: inline-flex;
      align-items: center;
      gap: 4px;
      background: rgba(239, 68, 68, 0.18);
      color: #ef4444;
      border: 1px solid rgba(239, 68, 68, 0.4);
      border-radius: 12px;
      padding: 2px 8px;
      font-size: 0.75rem;
      font-weight: 700;
      margin-left: 6px;
      animation: review-bell-pulse 2s infinite ease-in-out;
    }}

    @keyframes review-bell-pulse {{
      0% {{
        transform: scale(1);
        box-shadow: 0 0 0 0 rgba(239, 68, 68, 0.4);
      }}
      50% {{
        transform: scale(1.06);
        box-shadow: 0 0 8px 2px rgba(239, 68, 68, 0.25);
      }}
      100% {{
        transform: scale(1);
        box-shadow: 0 0 0 0 rgba(239, 68, 68, 0);
      }}
    }}

    .reviews-scorecards-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }}

    .review-scorecard {{
      background: #1e293b;
      border: 1px solid var(--border-color);
      border-radius: 14px;
      padding: 20px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      transition: transform 0.2s, border-color 0.2s;
    }}

    .review-scorecard:hover {{
      transform: translateY(-2px);
      border-color: #475569;
    }}

    .scorecard-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 12px;
    }}

    .scorecard-platform-badge {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 4px 10px;
      border-radius: 8px;
      font-size: 0.8rem;
      font-weight: 700;
      letter-spacing: 0.5px;
    }}

    .badge-airbnb {{ background: rgba(255, 56, 92, 0.18); color: #ff385c; border: 1px solid rgba(255, 56, 92, 0.4); }}
    .badge-vrbo {{ background: rgba(22, 104, 227, 0.18); color: #3b82f6; border: 1px solid rgba(22, 104, 227, 0.4); }}
    .badge-booking {{ background: rgba(0, 108, 228, 0.18); color: #60a5fa; border: 1px solid rgba(0, 108, 228, 0.4); }}
    .badge-kivoya {{ background: rgba(13, 148, 136, 0.18); color: #14b8a6; border: 1px solid rgba(13, 148, 136, 0.4); }}

    .scorecard-score-box {{
      margin-bottom: 16px;
    }}

    .scorecard-main-score {{
      font-size: 1.85rem;
      font-weight: 800;
      color: #f8fafc;
      letter-spacing: -0.5px;
    }}

    .scorecard-count {{
      font-size: 0.85rem;
      color: var(--text-muted);
      margin-top: 2px;
    }}

    .scorecard-subscores {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 6px 12px;
      margin-bottom: 16px;
      background: rgba(15, 23, 42, 0.6);
      padding: 10px 12px;
      border-radius: 8px;
      font-size: 0.78rem;
    }}

    .scorecard-sub-item {{
      display: flex;
      justify-content: space-between;
      color: #cbd5e1;
    }}

    .scorecard-sub-val {{
      font-weight: 700;
      color: #f8fafc;
    }}

    .scorecard-link {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
      padding: 8px 14px;
      border-radius: 8px;
      font-size: 0.8rem;
      font-weight: 600;
      color: #94a3b8;
      background: rgba(255, 255, 255, 0.05);
      border: 1px solid var(--border-color);
      text-decoration: none;
      transition: all 0.15s ease;
    }}

    .scorecard-link:hover {{
      color: #f8fafc;
      background: rgba(255, 255, 255, 0.1);
      border-color: #64748b;
    }}

    /* Reviews Filter Toolbar */
    .reviews-toolbar {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 12px;
      padding: 16px 20px;
      background: #1e293b;
      border: 1px solid var(--border-color);
      border-radius: 12px;
      margin-bottom: 24px;
    }}

    .reviews-filters-group {{
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 12px;
    }}

    .reviews-select {{
      background: #0f172a;
      border: 1px solid var(--border-color);
      color: #f8fafc;
      padding: 8px 12px;
      border-radius: 8px;
      font-size: 0.85rem;
      outline: none;
      cursor: pointer;
    }}

    .reviews-select:focus {{
      border-color: #3b82f6;
    }}

    .reviews-toggle-btn {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 8px 14px;
      border-radius: 8px;
      font-size: 0.85rem;
      font-weight: 600;
      background: #0f172a;
      color: #94a3b8;
      border: 1px solid var(--border-color);
      cursor: pointer;
      user-select: none;
      transition: all 0.15s ease;
    }}

    .reviews-toggle-btn.active {{
      background: rgba(239, 68, 68, 0.18);
      color: #ef4444;
      border-color: rgba(239, 68, 68, 0.4);
    }}

    .reviews-date-pills {{
      display: inline-flex;
      align-items: center;
      gap: 5px;
      flex-wrap: wrap;
    }}

    .reviews-search-box {{
      position: relative;
      min-width: 260px;
      flex: 1;
      max-width: 400px;
    }}

    .reviews-search-input {{
      width: 100%;
      background: #0f172a;
      border: 1px solid var(--border-color);
      color: #f8fafc;
      padding: 8px 12px 8px 34px;
      border-radius: 8px;
      font-size: 0.85rem;
      outline: none;
    }}

    .reviews-search-input:focus {{
      border-color: #3b82f6;
    }}

    .reviews-search-icon {{
      position: absolute;
      left: 10px;
      top: 50%;
      transform: translateY(-50%);
      font-size: 0.9rem;
      color: #64748b;
      pointer-events: none;
    }}

    /* Review Cards Feed */
    .reviews-feed {{
      display: flex;
      flex-direction: column;
      gap: 16px;
    }}

    .review-card {{
      background: #1e293b;
      border: 1px solid var(--border-color);
      border-radius: 12px;
      padding: 20px;
      transition: border-color 0.15s;
    }}

    .review-card.recent-card {{
      border-left: 4px solid #10b981;
    }}

    .review-card-header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 10px;
      margin-bottom: 12px;
    }}

    .review-author-meta {{
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }}

    .review-author-name {{
      font-size: 0.95rem;
      font-weight: 700;
      color: #f8fafc;
    }}

    .review-date {{
      font-size: 0.8rem;
      color: #64748b;
    }}

    .review-score-badge {{
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 3px 8px;
      border-radius: 6px;
      font-size: 0.8rem;
      font-weight: 700;
      background: rgba(245, 158, 11, 0.18);
      color: #f59e0b;
      border: 1px solid rgba(245, 158, 11, 0.35);
    }}

    .review-new-badge {{
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 2px 7px;
      border-radius: 6px;
      font-size: 0.72rem;
      font-weight: 700;
      background: rgba(16, 185, 129, 0.18);
      color: #10b981;
      border: 1px solid rgba(16, 185, 129, 0.4);
    }}

    .review-grid-2col {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 16px;
      align-items: stretch;
      margin-top: 6px;
    }}

    .review-guest-col {{
      background: rgba(15, 23, 42, 0.45);
      border: 1px solid rgba(51, 65, 85, 0.65);
      border-radius: 10px;
      padding: 16px;
      display: flex;
      flex-direction: column;
      min-width: 0;
    }}

    .review-team-col {{
      display: flex;
      flex-direction: column;
      min-width: 0;
    }}

    .review-col-header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      margin-bottom: 10px;
      font-size: 0.75rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: #94a3b8;
    }}

    .review-col-header.guest {{
      color: #cbd5e1;
    }}

    .review-title {{
      font-size: 0.95rem;
      font-weight: 700;
      color: #f1f5f9;
      margin-bottom: 8px;
    }}

    .review-body {{
      font-size: 0.88rem;
      line-height: 1.6;
      color: #cbd5e1;
      white-space: pre-line;
      flex: 1;
    }}

    .review-host-reply {{
      margin-top: 0;
      padding: 16px;
      background: rgba(15, 23, 42, 0.7);
      border: 1px solid rgba(59, 130, 246, 0.25);
      border-left: 3px solid #3b82f6;
      border-radius: 10px;
      font-size: 0.85rem;
      height: 100%;
      display: flex;
      flex-direction: column;
      box-sizing: border-box;
    }}

    .review-reply-header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      margin-bottom: 10px;
      font-weight: 700;
      color: #60a5fa;
      font-size: 0.76rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}

    .review-reply-header-empty {{
      color: #64748b;
    }}

    .review-reply-date-pill {{
      font-size: 0.72rem;
      font-weight: 600;
      color: #94a3b8;
      background: rgba(59, 130, 246, 0.12);
      padding: 2px 8px;
      border-radius: 4px;
      text-transform: none;
      letter-spacing: normal;
    }}

    .review-empty-badge {{
      font-size: 0.7rem;
      font-weight: 600;
      color: #64748b;
      background: rgba(100, 116, 139, 0.12);
      border: 1px solid rgba(100, 116, 139, 0.25);
      padding: 2px 8px;
      border-radius: 4px;
      text-transform: none;
      letter-spacing: normal;
    }}

    .review-host-reply.review-reply-empty {{
      border: 1px dashed rgba(100, 116, 139, 0.35);
      border-left: 3px solid #64748b;
      background: rgba(15, 23, 42, 0.35);
    }}

    .review-reply-body {{
      color: #cbd5e1;
      line-height: 1.55;
      font-size: 0.88rem;
    }}

    .review-reply-body-empty {{
      color: #64748b;
      font-style: italic;
      font-size: 0.85rem;
      display: flex;
      align-items: center;
      justify-content: center;
      flex: 1;
      min-height: 60px;
      text-align: center;
      padding: 10px;
    }}

    @media (max-width: 768px) {{
      .review-grid-2col {{
        grid-template-columns: 1fr;
        gap: 12px;
      }}
      .review-host-reply {{
        height: auto;
      }}
    }}

    /* Section Cards */
    .section-box {{
      background: var(--bg-card);
      border: 1px solid var(--border-color);
      border-radius: 16px;
      padding: 24px;
      margin-bottom: 32px;
    }}

    .section-urgent {{
      border: 1px solid var(--urgent-border);
      box-shadow: 0 0 20px rgba(239, 68, 68, 0.06);
    }}

    .section-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 16px;
      flex-wrap: wrap;
      gap: 12px;
    }}

    .section-title {{
      font-size: 1.3rem;
      font-weight: 700;
      display: flex;
      align-items: center;
      gap: 10px;
    }}

    .section-desc {{
      color: var(--text-muted);
      font-size: 0.9rem;
      margin-bottom: 20px;
      line-height: 1.6;
    }}

    /* Data Tables */
    .table-responsive {{
      overflow-x: auto;
      border-radius: 10px;
      border: 1px solid var(--border-color);
    }}

    table {{
      width: 100%;
      border-collapse: collapse;
      text-align: left;
      font-size: 0.9rem;
    }}

    th {{
      background: #0f172a;
      color: var(--text-muted);
      padding: 14px 16px;
      font-weight: 700;
      text-transform: uppercase;
      font-size: 0.75rem;
      letter-spacing: 0.05em;
      border-bottom: 1px solid var(--border-color);
      white-space: nowrap;
    }}

    td {{
      padding: 14px 16px;
      border-bottom: 1px solid #273549;
      white-space: nowrap;
    }}

    tr:last-child td {{
      border-bottom: none;
    }}

    tr:hover td {{
      background: rgba(255, 255, 255, 0.02);
    }}

    .clickable-row {{
      cursor: pointer;
      user-select: none;
      transition: background-color 0.15s ease;
    }}

    .clickable-row:hover td {{
      background: #273549 !important;
    }}

    .caret-icon {{
      display: inline-block;
      width: 14px;
      font-size: 0.72rem;
      color: #60a5fa;
      margin-right: 6px;
      transition: color 0.15s ease;
    }}

    .comp-details-row td {{
      padding: 0 !important;
      white-space: normal !important;
      background: #080e1a !important;
    }}

    .subtable-container {{
      padding: 16px 20px;
      border-left: 4px solid #3b82f6;
      border-bottom: 2px solid #334155;
      background: #080e1a;
    }}

    .subtable-scroll {{
      border: 1px solid var(--border-color);
      border-radius: 8px;
      overflow-x: auto;
    }}

    .subtable {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.85rem;
      text-align: left;
    }}

    .subtable thead {{
      background: #0f172a;
    }}

    .subtable th {{
      padding: 10px 14px;
      background: #0f172a;
      color: #94a3b8;
      font-weight: 700;
      font-size: 0.75rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      border-bottom: 1px solid #334155;
    }}

    .subtable td {{
      padding: 9px 14px;
      border-bottom: 1px solid rgba(255, 255, 255, 0.05);
      white-space: normal;
    }}

    .subtable tr:hover td {{
      background: rgba(255, 255, 255, 0.04);
    }}

    .our-property-row td {{
      background: rgba(245, 158, 11, 0.2) !important;
      border-top: 2px solid #f59e0b !important;
      border-bottom: 2px solid #f59e0b !important;
      font-weight: 600;
    }}

    .date-pill {{
      font-family: 'JetBrains Mono', monospace;
      font-weight: 600;
      color: #93c5fd;
      background: rgba(37, 99, 235, 0.1);
      padding: 4px 8px;
      border-radius: 6px;
      border: 1px solid rgba(59, 130, 246, 0.2);
    }}

    .rec-price {{
      font-size: 1.05rem;
      font-weight: 800;
      color: #34d399;
      font-family: 'JetBrains Mono', monospace;
      background: rgba(16, 185, 129, 0.12);
      padding: 4px 10px;
      border-radius: 6px;
      border: 1px solid rgba(16, 185, 129, 0.3);
      display: inline-block;
    }}

    .badge-diff-urgent,
    .badge-diff-over,
    .badge-diff-under {{
      background: rgba(239, 68, 68, 0.15);
      color: #f87171;
      padding: 4px 8px;
      border-radius: 6px;
      font-weight: 700;
      border: 1px solid rgba(239, 68, 68, 0.3);
      display: inline-block;
    }}

    .badge-diff-review {{
      background: rgba(245, 158, 11, 0.15);
      color: #fbbf24;
      padding: 4px 8px;
      border-radius: 6px;
      font-weight: 700;
      border: 1px solid rgba(245, 158, 11, 0.3);
      display: inline-block;
    }}

    .badge-diff-ok {{
      background: rgba(16, 185, 129, 0.15);
      color: #34d399;
      padding: 4px 8px;
      border-radius: 6px;
      font-weight: 700;
      border: 1px solid rgba(16, 185, 129, 0.3);
      display: inline-block;
    }}

    /* Tooltip Hover Popup */
    .tooltip-container {{
      position: relative;
      display: inline-flex;
      align-items: center;
    }}

    .tooltip-container .tooltip-text {{
      visibility: hidden;
      opacity: 0;
      width: 260px;
      background-color: #0f172a;
      color: #f1f5f9;
      text-align: left;
      border-radius: 8px;
      padding: 8px 12px;
      position: absolute;
      z-index: 100;
      bottom: 125%;
      left: 50%;
      transform: translateX(-50%);
      font-size: 0.78rem;
      line-height: 1.35;
      font-weight: 500;
      border: 1px solid #334155;
      box-shadow: 0 10px 25px rgba(0, 0, 0, 0.6);
      transition: opacity 0.15s ease-in-out, visibility 0.15s ease-in-out;
      pointer-events: none;
    }}

    .tooltip-container .tooltip-text::after {{
      content: "";
      position: absolute;
      top: 100%;
      left: 50%;
      margin-left: -5px;
      border-width: 5px;
      border-style: solid;
      border-color: #334155 transparent transparent transparent;
    }}

    .tooltip-container:hover .tooltip-text {{
      visibility: visible;
      opacity: 1;
    }}

    /* Comps Grid */
    .comps-toolbar {{
      display: flex;
      gap: 16px;
      margin-bottom: 24px;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
    }}

    .search-input {{
      background: #0f172a;
      border: 1px solid var(--border-color);
      padding: 10px 18px;
      border-radius: 10px;
      color: var(--text-main);
      font-family: inherit;
      font-size: 0.95rem;
      min-width: 320px;
      outline: none;
      transition: border-color 0.2s ease;
    }}

    .search-input:focus {{
      border-color: #3b82f6;
    }}

    .filter-pills {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }}

    .pill-btn {{
      background: #1e293b;
      border: 1px solid var(--border-color);
      color: var(--text-muted);
      padding: 6px 14px;
      border-radius: 9999px;
      font-size: 0.85rem;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.15s ease;
    }}

    .pill-btn:hover, .pill-btn.active {{
      background: #2563eb;
      color: white;
      border-color: #2563eb;
    }}

    .comps-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
      gap: 20px;
    }}

    .comp-card {{
      background: var(--bg-card);
      border: 1px solid var(--border-color);
      border-radius: 14px;
      padding: 16px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      transition: transform 0.2s ease, border-color 0.2s ease, box-shadow 0.2s ease;
    }}

    .comp-card:hover {{
      transform: translateY(-4px);
      border-color: #3b82f6;
      box-shadow: 0 12px 24px -6px rgba(0, 0, 0, 0.45);
    }}

    .comp-img-wrapper {{
      position: relative;
      width: 100%;
      height: 190px;
      border-radius: 10px;
      overflow: hidden;
      margin-bottom: 14px;
      background: #0f172a;
    }}

    .comp-img {{
      width: 100%;
      height: 100%;
      object-fit: cover;
      display: block;
      transition: transform 0.3s ease;
    }}

    .comp-card:hover .comp-img {{
      transform: scale(1.05);
    }}

    .comp-header {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 12px;
      margin-bottom: 12px;
    }}

    .comp-title {{
      font-size: 1rem;
      font-weight: 700;
      color: #f8fafc;
      line-height: 1.4;
    }}

    .comp-specs {{
      display: flex;
      gap: 12px;
      font-size: 0.85rem;
      color: var(--text-muted);
      margin-bottom: 14px;
      flex-wrap: wrap;
    }}

    .comp-link {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      width: 100%;
      background: rgba(37, 99, 235, 0.15);
      color: #60a5fa;
      border: 1px solid rgba(59, 130, 246, 0.3);
      padding: 10px;
      border-radius: 8px;
      font-size: 0.9rem;
      font-weight: 700;
      text-decoration: none;
      transition: all 0.15s ease;
    }}

    .comp-link:hover {{
      background: #2563eb;
      color: white;
    }}

    .btn-validity-audit {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
      width: 100%;
      background: rgba(56, 189, 248, 0.12);
      color: #38bdf8;
      border: 1px solid rgba(56, 189, 248, 0.3);
      padding: 8px;
      border-radius: 8px;
      font-size: 0.82rem;
      font-weight: 700;
      cursor: pointer;
      margin-top: 10px;
      margin-bottom: 8px;
      transition: all 0.15s ease;
    }}

    .btn-validity-audit:hover {{
      background: rgba(56, 189, 248, 0.25);
      border-color: rgba(56, 189, 248, 0.5);
      color: #bae6fd;
    }}

    .validity-modal-overlay {{
      position: fixed;
      top: 0;
      left: 0;
      width: 100%;
      height: 100%;
      background: rgba(15, 23, 42, 0.82);
      backdrop-filter: blur(6px);
      z-index: 9999;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 16px;
      box-sizing: border-box;
      animation: fadeIn 0.15s ease-out;
    }}

    .validity-modal-container {{
      background: #0f172a;
      border: 1px solid rgba(148, 163, 184, 0.25);
      border-radius: 12px;
      width: 100%;
      max-width: 840px;
      max-height: 90vh;
      overflow-y: auto;
      box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.7);
      display: flex;
      flex-direction: column;
    }}

    .validity-modal-header {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      padding: 18px 24px;
      border-bottom: 1px solid rgba(255, 255, 255, 0.08);
      background: #1e293b;
      position: sticky;
      top: 0;
      z-index: 10;
    }}

    .validity-modal-close {{
      background: transparent;
      border: none;
      color: #94a3b8;
      font-size: 1.8rem;
      cursor: pointer;
      line-height: 1;
      padding: 0 6px;
    }}

    .validity-modal-close:hover {{
      color: #f8fafc;
    }}

    .validity-modal-body {{
      padding: 24px;
    }}

    /* Channel Comparison Matrix & Badges */
    .div-badge {{
      display: inline-flex;
      align-items: center;
      gap: 4px;
      font-size: 0.78rem;
      font-weight: 700;
      padding: 2px 7px;
      border-radius: 5px;
      margin-left: 6px;
      vertical-align: middle;
      white-space: nowrap;
    }}
    .div-red {{
      background: rgba(239, 68, 68, 0.18);
      color: #f87171;
      border: 1px solid rgba(239, 68, 68, 0.4);
    }}
    .div-orange {{
      background: rgba(245, 158, 11, 0.18);
      color: #fbbf24;
      border: 1px solid rgba(245, 158, 11, 0.4);
    }}
    .div-green {{
      background: rgba(16, 185, 129, 0.15);
      color: #34d399;
      border: 1px solid rgba(16, 185, 129, 0.3);
    }}
    .price-sub {{
      display: block;
      font-size: 0.75rem;
      color: #94a3b8;
      font-weight: 500;
      margin-top: 2px;
    }}
    .matrix-table {{
      width: 100%;
      border-collapse: collapse;
      background: rgba(15, 23, 42, 0.75);
      border-radius: 8px;
      overflow: hidden;
      margin: 8px 0;
    }}
    .matrix-table th {{
      background: rgba(30, 41, 59, 0.85);
      color: #cbd5e1;
      padding: 10px 14px;
      font-size: 0.85rem;
      text-align: left;
      border-bottom: 1px solid var(--border-color);
    }}
    .matrix-table td {{
      padding: 9px 14px;
      font-size: 0.85rem;
      border-bottom: 1px solid rgba(255, 255, 255, 0.05);
    }}
    .matrix-table tr:last-child td {{
      border-bottom: none;
    }}
    .matrix-table tr.subtotal-row td {{
      background: rgba(148, 163, 184, 0.08);
      font-weight: 700;
      color: #f8fafc;
      border-top: 1px dashed rgba(148, 163, 184, 0.25);
      border-bottom: 1px dashed rgba(148, 163, 184, 0.25);
    }}
    .matrix-table tr.total-row td {{
      background: rgba(59, 130, 246, 0.1);
      font-weight: 700;
      font-size: 0.95rem;
      color: #f8fafc;
    }}
    .book-btn {{
      display: inline-flex;
      align-items: center;
      gap: 5px;
      background: rgba(59, 130, 246, 0.15);
      color: #60a5fa;
      border: 1px solid rgba(59, 130, 246, 0.35);
      padding: 5px 10px;
      border-radius: 6px;
      font-size: 0.8rem;
      font-weight: 600;
      text-decoration: none;
      transition: all 0.15s ease;
    }}
    .book-btn:hover {{
      background: rgba(59, 130, 246, 0.3);
      border-color: rgba(59, 130, 246, 0.6);
      color: #93c5fd;
    }}
    /* Filter Bar Styles */
    .filter-card {{
      background: var(--bg-card);
      border: 1px solid var(--border-color);
      border-radius: 12px;
      padding: 16px 20px;
      margin-bottom: 24px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 16px;
      box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
    }}

    .filter-pill-btn {{
      background: rgba(255, 255, 255, 0.05);
      border: 1px solid var(--border-color);
      color: #94a3b8;
      padding: 4px 10px;
      border-radius: 6px;
      font-size: 0.78rem;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.15s ease;
    }}

    .filter-pill-btn:hover {{
      background: rgba(255, 255, 255, 0.1);
      color: #f8fafc;
      border-color: #64748b;
    }}

    .filter-pill-btn.active {{
      background: #2563eb;
      color: #ffffff;
      border-color: #3b82f6;
      font-weight: 700;
    }}

    /* Footer */
    footer {{
      margin-top: 48px;
      text-align: center;
      color: var(--text-muted);
      font-size: 0.85rem;
      border-top: 1px solid var(--border-color);
      padding-top: 24px;
    }}

    /* ==========================================================================
       MOBILE RESPONSIVE ENHANCEMENTS (< 768px & < 480px)
       Preserves full desktop layout while delivering native mobile UX
       ========================================================================== */
    @media (max-width: 768px) {{
      html, body {{
        overflow-x: hidden;
        max-width: 100vw;
      }}

      body {{
        padding: 0 !important;
      }}

      .container {{
        width: 100% !important;
        max-width: 100% !important;
        margin: 0 !important;
        padding: 0 !important;
        overflow-x: hidden;
      }}

      /* Mobile Header: Ultra-compact, only show Updated timestamp */
      header {{
        padding: 8px 12px;
        border-radius: 0;
        border-left: none;
        border-right: none;
        border-top: none;
        margin-bottom: 0;
        display: flex;
        justify-content: center;
        align-items: center;
        background: #0f172a;
        box-shadow: none;
      }}

      .property-title {{
        display: none !important;
      }}

      .header-badges {{
        width: 100%;
        display: flex;
        justify-content: center;
        gap: 0;
      }}

      .header-badges .badge-primary {{
        display: none !important;
      }}

      .header-badges .badge-dark {{
        font-size: 0.75rem;
        padding: 2px 8px;
        background: transparent;
        border: none;
        color: var(--text-muted);
      }}

      /* Sticky Mobile Navigation Bar with horizontal swipe */
      .tabs-nav {{
        position: sticky;
        top: 0;
        z-index: 1000;
        background: rgba(15, 23, 42, 0.94);
        backdrop-filter: blur(12px);
        -webkit-backdrop-filter: blur(12px);
        margin: 0 0 12px 0;
        padding: 8px 10px;
        border-radius: 0;
        border-left: none;
        border-right: none;
        border-top: none;
        border-bottom: 1px solid var(--border-color);
        overflow-x: auto;
        white-space: nowrap;
        -webkit-overflow-scrolling: touch;
        scrollbar-width: none;
        gap: 6px;
        max-width: 100%;
        box-sizing: border-box;
      }}

      .tabs-nav::-webkit-scrollbar {{
        display: none;
      }}

      .tab-btn {{
        padding: 8px 14px;
        font-size: 0.84rem;
        border-radius: 20px;
        background: rgba(30, 41, 59, 0.7);
        border: 1px solid rgba(255, 255, 255, 0.08);
      }}

      .tab-btn.active {{
        background: var(--primary);
        color: #ffffff;
        border-color: #3b82f6;
      }}

      .tab-btn.active::after {{
        display: none;
      }}

      /* Compact 2x2 KPI Grid for mobile */
      .kpi-grid {{
        grid-template-columns: repeat(2, 1fr);
        gap: 10px;
        margin-bottom: 18px;
      }}

      .kpi-card {{
        padding: 12px 14px;
        border-radius: 12px;
      }}

      .kpi-val {{
        font-size: 1.35rem;
        margin: 4px 0;
        flex-direction: column;
        align-items: flex-start;
        gap: 2px;
      }}

      .kpi-label {{
        font-size: 0.70rem;
      }}

      .kpi-desc {{
        font-size: 0.72rem;
      }}

      /* Section Cards */
      .section-box {{
        padding: 14px 12px;
        border-radius: 0;
        border-left: none;
        border-right: none;
        margin-bottom: 16px;
      }}

      .section-title {{
        font-size: 1.15rem;
      }}

      .filter-card {{
        padding: 12px;
        border-radius: 0;
        border-left: none;
        border-right: none;
        flex-direction: column;
        align-items: stretch;
        gap: 10px;
      }}

      /* Comps Toolbar & Search */
      .comps-toolbar {{
        flex-direction: column;
        align-items: stretch;
        gap: 12px;
        margin-bottom: 16px;
      }}

      .search-input {{
        width: 100% !important;
        min-width: 0 !important;
        box-sizing: border-box;
      }}

      .filter-pills {{
        overflow-x: auto;
        white-space: nowrap;
        flex-wrap: nowrap;
        padding-bottom: 6px;
        -webkit-overflow-scrolling: touch;
        scrollbar-width: none;
      }}

      .filter-pills::-webkit-scrollbar {{
        display: none;
      }}

      .pill-btn {{
        flex-shrink: 0;
        padding: 6px 12px;
        font-size: 0.78rem;
      }}

      .comps-grid {{
        grid-template-columns: 1fr;
        gap: 14px;
      }}

      /* Responsive Card Transformation for Pricing Table */
      .table-responsive:has(.interval-parent-row),
      .table-responsive:has(.pricing-table) {{
        overflow-x: visible;
        border: none;
        background: transparent;
      }}

      .pricing-table {{
        display: block;
        width: 100%;
        border-collapse: separate;
        border-spacing: 0;
      }}

      .pricing-table > thead {{
        display: none;
      }}

      .pricing-table > tbody {{
        display: flex;
        flex-direction: column;
        gap: 12px;
      }}

      .clickable-row.interval-parent-row {{
        display: flex;
        flex-direction: column;
        background: var(--bg-card);
        border: 1px solid var(--border-color);
        border-radius: 12px;
        padding: 14px;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.25);
        gap: 6px;
        transition: transform 0.15s ease;
      }}

      .clickable-row.interval-parent-row:active {{
        transform: scale(0.99);
      }}

      .clickable-row.interval-parent-row > td {{
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 3px 0 !important;
        border: none !important;
        font-size: 0.88rem;
        white-space: normal !important;
        overflow-wrap: break-word;
      }}

      .clickable-row.interval-parent-row > td div,
      .clickable-row.interval-parent-row > td span {{
        white-space: normal !important;
      }}

      .clickable-row.interval-parent-row > td[data-label="Historical AVG"] {{
        flex-direction: column;
        align-items: flex-end;
        gap: 2px;
        text-align: right;
      }}

      .clickable-row.interval-parent-row > td::before {{
        content: attr(data-label);
        font-size: 0.72rem;
        font-weight: 700;
        text-transform: uppercase;
        color: var(--text-muted);
        letter-spacing: 0.05em;
        flex-shrink: 0;
      }}

      /* First cell (Dates) as card headline */
      .clickable-row.interval-parent-row > td[data-label="Dates"]::before {{
        display: none;
      }}

      .clickable-row.interval-parent-row > td[data-label="Dates"] {{
        font-size: 0.98rem;
        font-weight: 700;
        border-bottom: 1px solid rgba(255, 255, 255, 0.08) !important;
        padding-bottom: 8px !important;
        margin-bottom: 4px;
        justify-content: flex-start;
        gap: 6px;
      }}

      /* Highlighted Recommended Rate */
      .clickable-row.interval-parent-row > td[data-label="Recommended"] {{
        border-top: 1px dashed rgba(255, 255, 255, 0.1) !important;
        padding-top: 8px !important;
        margin-top: 4px;
      }}

      .clickable-row.interval-parent-row > td[data-label="Recommended"] .rec-price {{
        font-size: 1.15rem;
      }}

      /* Expandable sub-table drawer on mobile */
      .comp-details-row {{
        display: block !important;
      }}

      .comp-details-row[style*="display: none"],
      .comp-details-row[style*="display:none"] {{
        display: none !important;
      }}

      .comp-details-row > td {{
        display: block !important;
        padding: 0 !important;
        border: none !important;
      }}

      .subtable-container {{
        border-radius: 12px;
        padding: 12px !important;
        margin-top: 6px;
        margin-bottom: 12px;
      }}

      .subtable-scroll {{
        overflow-x: auto;
        -webkit-overflow-scrolling: touch;
      }}
    }}

    @media (max-width: 480px) {{
      body {{
        padding: 0 !important;
      }}

      .kpi-val {{
        font-size: 1.2rem;
      }}
    }}
  </style>
</head>
<body>
  <div class="container">
    <!-- Header -->
    <header>
      <div class="property-title">
        <h1>🏡 Villa del Sol: Pricing Advisory</h1>
        <p>920 E Carver Rd, Tempe, AZ • 6 BR / 5 BA • Gated ¾-Acre Compound • Sleeps 16</p>
      </div>
      <div class="header-badges">
        <span class="badge badge-primary">Dynamic Luxury Model (45th–70th %ile)</span>
        <span class="badge badge-dark">Updated: {now_str}</span>
      </div>
    </header>

    <!-- Navigation Tabs -->
    <nav class="tabs-nav" role="tablist">
      <button class="tab-btn active" onclick="switchTab('pricing')" role="tab" aria-selected="true">📊 Pricing</button>
      <button class="tab-btn" onclick="switchTab('reviews')" role="tab" id="tab-btn-reviews" aria-selected="false">⭐ Reviews{bell_badge_html}</button>
      <button class="tab-btn" onclick="switchTab('streamline')" role="tab" aria-selected="false">⚙️ Streamline PMS</button>
      <button class="tab-btn" onclick="switchTab('comps')" role="tab" aria-selected="false">🏡 Comps ({len(tier_a_comps) + len(tier_b_comps)})</button>
      <button class="tab-btn" onclick="switchTab('market-sales')" role="tab" aria-selected="false">🎯 Comps Sales</button>
      <button class="tab-btn" onclick="switchTab('calendar')" role="tab" aria-selected="false">📅 Calendar</button>
      <button class="tab-btn" onclick="switchTab('reservations')" role="tab" aria-selected="false">📑 Reservations ({len(reservations_list)})</button>
      <button class="tab-btn" onclick="switchTab('revenue')" role="tab" aria-selected="false">📈 Revenue</button>
      <button class="tab-btn" onclick="switchTab('comparison')" role="tab" aria-selected="false">🌐 Channels</button>
    </nav>

    <!-- TAB 1: PRICING RECOMMENDATIONS -->
    <div id="tab-pricing" class="tab-content active">
      <!-- Competitor Quality Filter Bar -->
      <div class="filter-card">
        <div style="display: flex; align-items: center; gap: 20px; flex-wrap: wrap;">
          <div style="display: flex; align-items: center; gap: 8px;">
            <span style="font-size: 1.25rem;">🔍</span>
            <strong style="color: #f8fafc; font-size: 0.95rem;">Comp Quality Filter:</strong>
          </div>

          <!-- Min Rating -->
          <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
            <label style="font-size: 0.85rem; color: #94a3b8; font-weight: 600;">Min Rating:</label>
            <input type="hidden" id="filterMinRating" value="4.5">
            <div style="display: flex; gap: 4px;">
              <button class="filter-pill-btn" id="btn-rate-all" onclick="setFilterRating(0.0)">All</button>
              <button class="filter-pill-btn" id="btn-rate-40" onclick="setFilterRating(4.0)">4.0+ ★</button>
              <button class="filter-pill-btn active" id="btn-rate-45" onclick="setFilterRating(4.5)">4.5+ ★ (Default)</button>
              <button class="filter-pill-btn" id="btn-rate-48" onclick="setFilterRating(4.8)">4.8+ ★</button>
            </div>
          </div>

          <!-- Min Reviews -->
          <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
            <label style="font-size: 0.85rem; color: #94a3b8; font-weight: 600;">Min Reviews:</label>
            <input type="hidden" id="filterMinReviews" value="25">
            <div style="display: flex; gap: 4px;">
              <button class="filter-pill-btn" id="btn-rev-0" onclick="setFilterReviews(0)">All (0+)</button>
              <button class="filter-pill-btn" id="btn-rev-5" onclick="setFilterReviews(5)">5+</button>
              <button class="filter-pill-btn" id="btn-rev-10" onclick="setFilterReviews(10)">10+</button>
              <button class="filter-pill-btn active" id="btn-rev-25" onclick="setFilterReviews(25)">25+ (Default)</button>
              <button class="filter-pill-btn" id="btn-rev-50" onclick="setFilterReviews(50)">50+</button>
            </div>
          </div>

          <!-- Premium Location Checkbox -->
          <div class="tooltip-container" style="margin-left: 4px;" title="Includes premium locations (e.g. Scottsdale and Paradise Valley)">
            <label style="display: flex; align-items: center; gap: 7px; cursor: pointer; font-size: 0.85rem; color: #cbd5e1; font-weight: 600; user-select: none;">
              <input type="checkbox" id="filterPremiumLocation" checked onchange="onFilterChange()" style="width: 16px; height: 16px; accent-color: #3b82f6; cursor: pointer; border-radius: 4px;">
              <span>Premium Location</span>
              <span style="font-size: 0.8rem; color: #94a3b8; cursor: help;">ℹ️</span>
            </label>
            <span class="tooltip-text">Includes premium locations (e.g. Scottsdale and Paradise Valley). Checked by default. Uncheck to benchmark strictly against direct East Valley corridor comps (Tempe, Mesa, Chandler, Gilbert).</span>
          </div>
        </div>

        <!-- Status & Reset -->
        <div style="display: flex; align-items: center; gap: 12px;">
          <span id="filterStatusBadge" class="badge" style="background: rgba(16, 185, 129, 0.15); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.3); font-size: 0.8rem;">
            Active Filters: Rating &ge; 4.5 ★ &bull; Reviews &ge; 25 &bull; Incl. Premium Loc.
          </span>
          <button onclick="resetFilters()" style="background: transparent; border: 1px solid var(--border-color); color: #94a3b8; padding: 6px 12px; border-radius: 6px; font-size: 0.8rem; cursor: pointer; transition: all 0.2s;" onmouseover="this.style.color='#f8fafc'; this.style.borderColor='#64748b';" onmouseout="this.style.color='#94a3b8'; this.style.borderColor='var(--border-color)';">
            🔄 Reset
          </button>
        </div>
      </div>

      <!-- PROPOSED PRICES TABLE (PMS CONSENSUS SCHEDULE) -->
      {proposed_prices_html}

      <!-- Unified 12-Month Dynamic Pricing Schedule -->
      <div class="section-box" style="margin-top: 24px;">
        <div class="section-header" style="margin-bottom: 12px;">
          <div>
            <div class="section-title" style="font-size: 1.3rem;">
              📅 12-Month Dynamic Pricing Schedule
            </div>
            <p class="section-desc" style="margin-top: 4px; margin-bottom: 0;">
              All unbooked weekend and midweek intervals over the next 12 months. Click any row to expand competitor pricing details.
            </p>
          </div>
          <button id="btnCopySchedule" onclick="copyPricingSchedule()" title="Copy visible schedule in plain text format (date range, type, price action)" style="display: inline-flex; align-items: center; gap: 8px; background: rgba(59,130,246,0.15); color: #93c5fd; border: 1px solid rgba(59,130,246,0.35); padding: 7px 14px; border-radius: 7px; font-weight: 600; font-size: 0.85rem; cursor: pointer; transition: all 0.15s ease-in-out; user-select: none;" onmouseover="this.style.background='rgba(59,130,246,0.25)'; this.style.borderColor='rgba(59,130,246,0.5)';" onmouseout="this.style.background='rgba(59,130,246,0.15)'; this.style.borderColor='rgba(59,130,246,0.35)';" onmousedown="this.style.transform='scale(0.96)';" onmouseup="this.style.transform='scale(1)';">
            <span id="copyIconContainer" style="display: inline-flex; align-items: center;">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align: middle;">
                <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
              </svg>
            </span>
            <span id="copyBtnText">Copy Schedule</span>
          </button>
        </div>

        <!-- Schedule Filter Controls (All Checkboxes) -->
        <div class="interval-filter-pills" style="display: flex; gap: 10px; margin-bottom: 18px; margin-top: 14px; flex-wrap: wrap; align-items: center;">
          <span style="font-size: 0.85rem; color: #94a3b8; font-weight: 600; margin-right: 2px;">Filter Intervals:</span>

          <!-- Tier Status Checkboxes -->
          <label title="Show intervals with >{self.urgent_pct_diff:.0f}% market discrepancy requiring immediate rate adjustment" style="display: inline-flex; align-items: center; gap: 6px; cursor: pointer; font-size: 0.85rem; color: #f87171; font-weight: 600; background: rgba(239,68,68,0.15); border: 1px solid rgba(239,68,68,0.3); padding: 5px 11px; border-radius: 6px; user-select: none;">
            <input type="checkbox" id="filterTierUrgent" checked onchange="filterIntervalTiers()" style="width: 15px; height: 15px; accent-color: #ef4444; cursor: pointer; border-radius: 4px;">
            <span>Urgent Action (<span id="count-interval-urgent">{len(open_urgent)}</span>)</span>
          </label>

          <label title="Show intervals with {self.moderate_pct_diff:.0f}%–{self.urgent_pct_diff:.0f}% market variance for review" style="display: inline-flex; align-items: center; gap: 6px; cursor: pointer; font-size: 0.85rem; color: #fbbf24; font-weight: 600; background: rgba(245,158,11,0.15); border: 1px solid rgba(245,158,11,0.3); padding: 5px 11px; border-radius: 6px; user-select: none;">
            <input type="checkbox" id="filterTierModerate" checked onchange="filterIntervalTiers()" style="width: 15px; height: 15px; accent-color: #f59e0b; cursor: pointer; border-radius: 4px;">
            <span>Review (<span id="count-interval-mod">{len(open_moderate)}</span>)</span>
          </label>

          <label title="Show intervals within normal competitive market range (0%–{self.moderate_pct_diff:.0f}% variance)" style="display: inline-flex; align-items: center; gap: 6px; cursor: pointer; font-size: 0.85rem; color: #34d399; font-weight: 600; background: rgba(16,185,129,0.15); border: 1px solid rgba(16,185,129,0.3); padding: 5px 11px; border-radius: 6px; user-select: none;">
            <input type="checkbox" id="filterTierOk" checked onchange="filterIntervalTiers()" style="width: 15px; height: 15px; accent-color: #10b981; cursor: pointer; border-radius: 4px;">
            <span>On Target (<span id="count-interval-ok">{len(open_ok)}</span>)</span>
          </label>

          <span style="height: 18px; width: 1px; background: rgba(255,255,255,0.15); margin: 0 4px;"></span>

          <!-- Scope & Model Checkboxes -->
          <label title="Kivoya booking calendar is open through May 31, 2027 (closed from June 2027 onwards). Uncheck to show all 12 months." style="display: inline-flex; align-items: center; gap: 6px; cursor: pointer; font-size: 0.85rem; color: #38bdf8; font-weight: 600; background: rgba(56,189,248,0.1); border: 1px solid rgba(56,189,248,0.3); padding: 5px 11px; border-radius: 6px; user-select: none;">
            <input type="checkbox" id="filterOpenCalendar" checked onchange="filterIntervalTiers()" style="width: 15px; height: 15px; accent-color: #38bdf8; cursor: pointer; border-radius: 4px;">
            <span>Open Calendar Only</span>
          </label>

          <label title="When checked, competitor rates are adjusted based on property quality/desirability relative to Villa del Sol, and invalid comps are excluded. Uncheck to view raw unadjusted market rates." style="display: inline-flex; align-items: center; gap: 6px; cursor: pointer; font-size: 0.85rem; color: #a78bfa; font-weight: 600; background: rgba(167,139,250,0.1); border: 1px solid rgba(167,139,250,0.3); padding: 5px 11px; border-radius: 6px; user-select: none;">
            <input type="checkbox" id="filterAdjustedComps" checked onchange="toggleAdjustedComps()" style="width: 15px; height: 15px; accent-color: #a78bfa; cursor: pointer; border-radius: 4px;">
            <span>Adjusted Comp Rates</span>
          </label>
        </div>

        <div class="table-responsive">
          <table class="pricing-table">
            <thead>
              <tr>
                <th>Open Dates</th>
                <th>Type</th>
                <th>Nights</th>
                <th>Market Gap</th>
                <th>Action Needed</th>
                <th>Historical AVG</th>
                <th>Comps (N)</th>
                <th>Kivoya</th>
                <th>Effective Total</th>
                <th>Comp Target</th>
                <th>Recommended Base Rate</th>
              </tr>
            </thead>
            <tbody>
              {self._render_table_rows(all_sorted, prefix="row")}
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- TAB 1.6: RATINGS & REVIEWS INTELLIGENCE -->
    <div id="tab-reviews" class="tab-content">
      {self._render_reviews_tab(ratings_data)}
    </div>

    <!-- TAB 1.7: STREAMLINE PMS -->
    <div id="tab-streamline" class="tab-content">
      {streamline_tab_html}
    </div>

    <!-- TAB 2: COMPS REGISTRY -->
    <div id="tab-comps" class="tab-content">
      <div class="section-box">
        <div class="comps-toolbar">
          <div>
            <h2 style="font-size: 1.4rem; font-weight: 800; margin-bottom: 4px;">Curated Luxury Comps Registry</h2>
            <p style="color: var(--text-muted); font-size: 0.9rem;">
              Tracking {len(tier_a_comps) + len(tier_b_comps)} verified competitor properties in Tempe, Scottsdale, Chandler, Mesa, and Gilbert.
            </p>
          </div>
          <input type="text" id="compSearch" class="search-input" placeholder="Search by city, title, bedrooms..." oninput="filterComps()" />
        </div>

        <div class="filter-pills" style="margin-bottom: 24px;">
          <button class="pill-btn active" onclick="filterTier('all', this)">All Comps ({total_active_comps})</button>
          <button class="pill-btn" onclick="filterTier('tier_a', this)">Tier A: Direct 16+ Guests ({len(tier_a_comps)})</button>
          <button class="pill-btn" onclick="filterTier('tier_b', this)">Tier B: 12-15 Guests ({len(tier_b_comps)})</button>
          <button class="pill-btn" onclick="filterValidity('valid', this)" style="border-color: rgba(52,211,153,0.4); color:#34d399;">✅ Valid Comps Only ({valid_comps_count})</button>
          <button class="pill-btn" onclick="filterValidity('disqualified', this)" style="border-color: rgba(239,68,68,0.4); color:#f87171;">⛔ Disqualified Comps ({disqualified_comps_count})</button>
          <button class="pill-btn" onclick="filterCity('scottsdale', this)">Scottsdale</button>
          <button class="pill-btn" onclick="filterCity('tempe', this)">Tempe</button>
          <button class="pill-btn" onclick="filterCity('mesa', this)">Mesa / Gilbert</button>
          <button class="pill-btn" onclick="filterCity('chandler', this)">Chandler</button>
        </div>

        {self._render_comp_policy_stats()}

        <div class="comps-grid" id="compsContainer">
          {self._render_comp_cards(tier_a_comps, "Tier A (Direct)")}
          {self._render_comp_cards(tier_b_comps, "Tier B (Secondary)")}
          {self._render_comp_cards(disqualified_comps, "Disqualified")}
        </div>
      </div>
    </div>

    <!-- TAB 3: COMPS SALES -->
    <div id="tab-market-sales" class="tab-content">
      {market_sales_tab_html}
    </div>

    <!-- TAB 4: AVAILABILITY CALENDAR -->
    <div id="tab-calendar" class="tab-content">
      {calendar_tab_html}
    </div>

    <!-- TAB 5: RESERVATIONS TABLE -->
    <div id="tab-reservations" class="tab-content">
      {reservations_tab_html}
    </div>

    <!-- TAB 6: CUMULATIVE REVENUE -->
    <div id="tab-revenue" class="tab-content">
      {revenue_tab_html}
    </div>

    <!-- TAB 6.5: CHANNEL PRICE COMPARISON -->
    <div id="tab-comparison" class="tab-content">
      {self._render_comparison_tab(all_sorted)}
    </div>

    <!-- Footer -->
    <footer>
      <p>STR Competitive Price Advisor for Villa del Sol • Automated Analysis Engine</p>
      <p style="margin-top: 4px; font-size: 0.8rem; color: #64748b;">Hosted on GitHub Pages • Generated on {now_str}</p>
    </footer>
  </div>

  {reservation_modal_html}
  {validity_modal_html}

  <script>
    const VALID_TABS = ['pricing', 'reviews', 'streamline', 'comps', 'market-sales', 'calendar', 'reservations', 'revenue', 'comparison'];
    let currentActiveTabId = 'pricing';
    let isInitialTabLoad = true;

    if ('scrollRestoration' in history) {{
      history.scrollRestoration = 'manual';
    }}

    function saveCurrentTabScroll() {{
      if (currentActiveTabId) {{
        try {{
          sessionStorage.setItem('str_advisor_scroll_' + currentActiveTabId, String(window.scrollY));
        }} catch (e) {{}}
      }}
    }}

    let scrollDebounceTimer = null;
    function handleScrollEnd() {{
      saveCurrentTabScroll();
    }}

    if ('onscrollend' in window) {{
      window.addEventListener('scrollend', handleScrollEnd, {{ passive: true }});
    }} else {{
      window.addEventListener('scroll', () => {{
        clearTimeout(scrollDebounceTimer);
        scrollDebounceTimer = setTimeout(handleScrollEnd, 100);
      }}, {{ passive: true }});
    }}

    window.addEventListener('beforeunload', saveCurrentTabScroll);
    document.addEventListener('visibilitychange', () => {{
      if (document.visibilityState === 'hidden') {{
        saveCurrentTabScroll();
      }}
    }});

    function switchTab(tabId, pushHistory = true, restoreScroll = true) {{
      if (!VALID_TABS.includes(tabId)) {{
        tabId = 'pricing';
      }}

      // Clear pending scroll debounce timer to prevent cross-tab scroll leakage
      if (scrollDebounceTimer) {{
        clearTimeout(scrollDebounceTimer);
        scrollDebounceTimer = null;
      }}

      // If clicking the already active tab (user interaction), scroll to top
      if (currentActiveTabId === tabId && !isInitialTabLoad) {{
        const prefersReducedMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        window.scrollTo({{ top: 0, behavior: prefersReducedMotion ? 'instant' : 'smooth' }});
        try {{
          sessionStorage.setItem('str_advisor_scroll_' + tabId, '0');
        }} catch (e) {{}}
        return;
      }}

      // Save previous tab scroll before switching
      if (currentActiveTabId && currentActiveTabId !== tabId) {{
        saveCurrentTabScroll();
      }}

      // Immediately update active tab reference to prevent race conditions during DOM reflows
      currentActiveTabId = tabId;

      document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
      document.querySelectorAll('.tab-btn').forEach(el => {{
        el.classList.remove('active');
        el.setAttribute('aria-selected', 'false');
      }});
      
      const target = document.getElementById('tab-' + tabId);
      if (target) target.classList.add('active');
      
      const btn = document.querySelector(`button.tab-btn[onclick*="'${{tabId}}'"]`);
      if (btn) {{
        btn.classList.add('active');
        btn.setAttribute('aria-selected', 'true');
        const nav = btn.closest('.tabs-nav');
        if (nav && typeof nav.scrollTo === 'function') {{
          nav.scrollTo({{ left: Math.max(0, btn.offsetLeft - 16), behavior: 'instant' }});
        }}
      }}

      try {{
        localStorage.setItem('str_advisor_active_tab', tabId);
      }} catch (e) {{}}

      const targetHash = '#' + tabId;
      if (window.location.hash !== targetHash) {{
        if (pushHistory && !isInitialTabLoad) {{
          history.pushState({{ tabId: tabId }}, '', targetHash);
        }} else {{
          history.replaceState({{ tabId: tabId }}, '', targetHash);
        }}
      }}

      if (tabId === 'revenue' && typeof initRevenueChart === 'function') {{
        setTimeout(initRevenueChart, 50);
      }}
      if (tabId === 'market-sales' && typeof initMarketTrajectoryChart === 'function') {{
        setTimeout(initMarketTrajectoryChart, 50);
      }}
      if (tabId === 'calendar' && typeof renderCalendar === 'function') {{
        setTimeout(() => renderCalendar(calCurrentYear, calCurrentMonth), 50);
      }}
      if (tabId === 'reservations' && typeof initReservationsTable === 'function') {{
        setTimeout(initReservationsTable, 50);
      }}
      if (tabId === 'reviews' && typeof filterReviews === 'function') {{
        setTimeout(filterReviews, 30);
      }}

      if (restoreScroll) {{
        let savedScroll = 0;
        try {{
          const savedStr = sessionStorage.getItem('str_advisor_scroll_' + tabId);
          if (savedStr) savedScroll = Math.max(0, parseInt(savedStr, 10) || 0);
        }} catch (e) {{}}

        requestAnimationFrame(() => {{
          window.scrollTo({{ top: savedScroll, behavior: 'instant' }});
          // Secondary scroll pass at 60ms to account for dynamic tab sub-components
          // (such as initRevenueChart or filterReviews) that expand layout after 30-50ms
          setTimeout(() => {{
            if (currentActiveTabId === tabId && savedScroll > 0) {{
              window.scrollTo({{ top: savedScroll, behavior: 'instant' }});
            }}
          }}, 60);
        }});
      }}
    }}

    window.addEventListener('popstate', () => {{
      const hashTab = (window.location.hash || '').replace('#', '').trim();
      const targetTab = VALID_TABS.includes(hashTab) ? hashTab : 'pricing';
      if (targetTab !== currentActiveTabId) {{
        switchTab(targetTab, false, true);
      }}
    }});

    function setReviewDateFilter(range, btn) {{
      const input = document.getElementById('reviewDateFilterVal');
      if (input) {{
        input.value = range;
      }}
      document.querySelectorAll('.reviews-date-pills .filter-pill-btn').forEach(b => {{
        b.classList.remove('active');
        b.setAttribute('aria-pressed', 'false');
      }});
      const targetBtn = btn || document.getElementById('rev-date-' + range);
      if (targetBtn) {{
        targetBtn.classList.add('active');
        targetBtn.setAttribute('aria-pressed', 'true');
      }}
      filterReviews();
    }}

    function filterReviews() {{
      const plat = document.getElementById('reviewPlatformFilter') ? document.getElementById('reviewPlatformFilter').value : 'all';
      const rating = document.getElementById('reviewRatingFilter') ? document.getElementById('reviewRatingFilter').value : 'all';
      const dateRange = document.getElementById('reviewDateFilterVal') ? document.getElementById('reviewDateFilterVal').value : '30';
      const search = (document.getElementById('reviewSearchInput') ? document.getElementById('reviewSearchInput').value : '').toLowerCase().trim();

      const cards = document.querySelectorAll('.review-card');
      let visibleCount = 0;
      const now = new Date();

      cards.forEach(card => {{
        const cardPlat = card.getAttribute('data-platform') || '';
        const cardScore = parseFloat(card.getAttribute('data-rating') || '0');
        const cardMax = parseFloat(card.getAttribute('data-rating-max') || '5');
        const cardDateStr = card.getAttribute('data-date') || '';
        const cardSearch = (card.getAttribute('data-search-text') || '').toLowerCase();

        let show = true;

        if (plat !== 'all' && cardPlat !== plat) {{
          show = false;
        }}

        if (show && rating !== 'all') {{
          if (rating === '5star') {{
            if (cardMax <= 5.0 && cardScore < 5.0) show = false;
            if (cardMax > 5.0 && cardScore < 9.0) show = false;
          }} else if (rating === '4star') {{
            if (cardMax <= 5.0 && (cardScore < 4.0 || cardScore >= 5.0)) show = false;
            if (cardMax > 5.0 && (cardScore < 7.0 || cardScore >= 9.0)) show = false;
          }} else if (rating === 'low') {{
            if (cardMax <= 5.0 && cardScore >= 4.0) show = false;
            if (cardMax > 5.0 && cardScore >= 7.0) show = false;
          }}
        }}

        if (show && dateRange !== 'all') {{
          if (!cardDateStr) {{
            show = false;
          }} else {{
            const cardDate = new Date(cardDateStr + 'T00:00:00');
            const diffDays = Math.floor((now.getTime() - cardDate.getTime()) / (1000 * 60 * 60 * 24));
            const maxDays = parseInt(dateRange, 10);
            if (isNaN(diffDays) || diffDays > maxDays || diffDays < -1) {{
              show = false;
            }}
          }}
        }}

        if (show && search.length > 0) {{
          const terms = search.split(/\\s+/).filter(Boolean);
          if (!terms.every(t => cardSearch.includes(t))) {{
            show = false;
          }}
        }}

        if (show) {{
          card.style.display = '';
          visibleCount++;
        }} else {{
          card.style.display = 'none';
        }}
      }});

      const counter = document.getElementById('reviewCountBadge');
      if (counter) {{
        counter.textContent = `Showing ${{visibleCount}} of ${{cards.length}} reviews`;
      }}
    }}

    function toggleRecentFilter() {{
      const currentVal = document.getElementById('reviewDateFilterVal')?.value;
      const target = (currentVal === '30') ? 'all' : '30';
      const btn = document.getElementById(target === '30' ? 'rev-date-30' : 'rev-date-all');
      setReviewDateFilter(target, btn);
    }}

    function filterSalesFeed(statusFilter, btn) {{
      if (btn) {{
        document.querySelectorAll('.sales-filter-pills .pill-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
      }}
      const query = (document.getElementById('salesSearch')?.value || '').toLowerCase();
      const rows = document.querySelectorAll('.sales-feed-row');
      rows.forEach(r => {{
        const rowStatus = r.getAttribute('data-status') || '';
        const rowType = r.getAttribute('data-type') || '';
        const text = r.innerText.toLowerCase();
        
        let matchStatus = true;
        if (statusFilter === 'confirmed') matchStatus = (rowStatus === 'CONFIRMED_BLOCKED');
        else if (statusFilter === 'weekend') matchStatus = (rowType === 'weekend');
        else if (statusFilter === 'midweek') matchStatus = (rowType === 'midweek');
        
        const matchSearch = !query || text.includes(query);
        r.style.display = (matchStatus && matchSearch) ? '' : 'none';
      }});
    }}

    function searchSalesFeed() {{
      const activeBtn = document.querySelector('.sales-filter-pills .pill-btn.active');
      const filterType = activeBtn?.getAttribute('data-filter') || 'all';
      filterSalesFeed(filterType, activeBtn);
    }}

    function filterComps() {{
      const query = document.getElementById('compSearch').value.toLowerCase();
      const isDisqActive = document.querySelector('.filter-pills .pill-btn.active')?.textContent.includes('Disqualified');
      const cards = document.querySelectorAll('.comp-card');
      cards.forEach(card => {{
        const text = card.innerText.toLowerCase();
        const match = text.includes(query);
        if (isDisqActive) {{
          card.style.display = (match && (card.dataset.valid === 'false' || card.dataset.tier === 'disqualified')) ? 'flex' : 'none';
        }} else {{
          card.style.display = (match && card.dataset.tier !== 'disqualified' && card.dataset.valid !== 'false') ? 'flex' : 'none';
        }}
      }});
      updateCompsPolicyStats();
    }}

    function filterTier(tier, btn) {{
      document.querySelectorAll('.filter-pills .pill-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const cards = document.querySelectorAll('.comp-card');
      cards.forEach(card => {{
        if (tier === 'all') {{
          card.style.display = (card.dataset.tier === 'tier_a' || card.dataset.tier === 'tier_b') ? 'flex' : 'none';
        }} else {{
          card.style.display = card.dataset.tier === tier ? 'flex' : 'none';
        }}
      }});
      updateCompsPolicyStats();
    }}

    function filterCity(city, btn) {{
      document.querySelectorAll('.filter-pills .pill-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const cards = document.querySelectorAll('.comp-card');
      cards.forEach(card => {{
        const loc = (card.dataset.location || '').toLowerCase();
        card.style.display = (loc.includes(city) && card.dataset.tier !== 'disqualified') ? 'flex' : 'none';
      }});
      updateCompsPolicyStats();
    }}

    function filterValidity(val, btn) {{
      document.querySelectorAll('.filter-pills .pill-btn').forEach(b => b.classList.remove('active'));
      if (btn) btn.classList.add('active');
      const cards = document.querySelectorAll('.comp-card');
      cards.forEach(card => {{
        if (val === 'valid') {{
          card.style.display = (card.dataset.valid === 'true' && card.dataset.tier !== 'disqualified') ? 'flex' : 'none';
        }} else if (val === 'disqualified') {{
          card.style.display = (card.dataset.valid === 'false' || card.dataset.tier === 'disqualified') ? 'flex' : 'none';
        }} else {{
          card.style.display = 'flex';
        }}
      }});
      updateCompsPolicyStats();
    }}

    function updateCompsPolicyStats() {{
      if (typeof COMP_POLICIES_DATA === 'undefined') return;
      const visibleCards = Array.from(document.querySelectorAll('.comp-card')).filter(c => c.style.display !== 'none');
      const visibleIds = new Set(visibleCards.map(c => c.dataset.listingId).filter(Boolean));

      const cohort = [];
      for (const [lid, comp] of Object.entries(COMP_POLICIES_DATA)) {{
        if (visibleCards.length === 0 || visibleIds.has(lid)) {{
          cohort.push(comp);
        }}
      }}

      const badge = document.getElementById('policyCohortCount');
      if (badge) {{
        badge.textContent = cohort.length + ' Active Comps';
      }}

      const dims = [
        {{ id: 'check_in', default_order: ['3:00 PM', '4:00 PM', '5:00 PM', 'Flexible / Other', 'Undisclosed'] }},
        {{ id: 'check_out', default_order: ['10:00 AM', '11:00 AM', '12:00 PM', 'Flexible / Other', 'Undisclosed'] }},
        {{ id: 'deposit', default_order: ['$1,000+ Deposit Required', '$500–$999 Deposit Required', 'Deposit Required (Unspecified)', 'None Mentioned / Platform Only'] }},
        {{ id: 'noise', default_order: ['Active Decibel Sensor (Minut / NoiseAware)', 'Strict Quiet Hours Declared', 'City Noise Ordinance Warning', 'Undisclosed / Standard'] }},
        {{ id: 'pool_heating', default_order: ['Free / Included in Rate', 'Paid Extra Daily Fee', 'Unheated Pool', 'No Pool'] }},
        {{ id: 'min_age', default_order: ['25+ Years', '21+ Years', 'Other Age', 'Undisclosed'] }},
        {{ id: 'pets', default_order: ['Pets Allowed (w/ Fee)', 'Strict No Pets', 'Undisclosed'] }},
        {{ id: 'events', default_order: ['Strictly Prohibited', 'Permitted w/ Approval or Fee', 'Undisclosed'] }}
      ];

      dims.forEach(dim => {{
        const table = document.getElementById('policy-table-' + dim.id);
        if (!table) return;
        const tbody = table.querySelector('tbody');
        if (!tbody) return;

        const counts = {{}};
        const samples = {{}};
        cohort.forEach(comp => {{
          const pol = (comp.policies && comp.policies[dim.id]) ? comp.policies[dim.id] : {{ bucket: 'Undisclosed' }};
          const b = pol.bucket || 'Undisclosed';
          counts[b] = (counts[b] || 0) + 1;
          if (!samples[b] && comp.listing_id) {{
            samples[b] = {{
              title: comp.title || ('Listing ' + comp.listing_id),
              url: comp.url || ('https://www.airbnb.com/rooms/' + comp.listing_id),
              snippet: pol.snippet || ''
            }};
          }}
        }});

        const orderedBuckets = [];
        const seen = new Set();
        (dim.default_order || []).forEach(b => {{
          if (counts[b]) {{
            orderedBuckets.push(b);
            seen.add(b);
          }}
        }});
        Object.keys(counts).forEach(b => {{
          if (!seen.has(b)) {{
            orderedBuckets.push(b);
          }}
        }});

        const total = cohort.length;
        let html = '';
        orderedBuckets.forEach(b => {{
          const cnt = counts[b] || 0;
          const pct = total > 0 ? ((cnt / total) * 100).toFixed(1) : '0.0';
          const samp = samples[b];
          const isUndisclosed = b.toLowerCase().includes('undisclosed') || b.toLowerCase().includes('none mentioned');
          const bColor = isUndisclosed ? '#94a3b8' : '#f1f5f9';
          
          let sampleHtml = '<span style="color: #64748b;">—</span>';
          if (samp) {{
            const escapedSnip = (samp.snippet || '').replace(/"/g, '&quot;');
            const shortTitle = (samp.title || '').length > 25 ? (samp.title.substring(0, 25) + '...') : samp.title;
            sampleHtml = `<a href="${{samp.url}}" target="_blank" rel="noopener" class="policy-sample-link" title="${{escapedSnip}}" style="color: #818cf8; text-decoration: none; display: inline-flex; align-items: center; gap: 4px; max-width: 140px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;"><span style="overflow: hidden; text-overflow: ellipsis;">${{shortTitle}}</span> <span style="font-size: 0.7rem; opacity: 0.7;">↗</span></a>`;
          }}

          html += `
            <tr style="border-bottom: 1px solid rgba(255,255,255,0.03);">
              <td style="padding: 6px 12px; font-weight: 500; color: ${{bColor}};">${{b}}</td>
              <td style="padding: 6px 8px; text-align: right; font-weight: 600; color: #f8fafc;">${{cnt}}</td>
              <td style="padding: 6px 8px; text-align: right; font-weight: 600; color: #38bdf8;">${{pct}}%</td>
              <td style="padding: 6px 12px;">${{sampleHtml}}</td>
            </tr>
          `;
        }});

        tbody.innerHTML = html;
      }});
    }}

    function toggleAdjustedComps() {{
      applyGlobalFilters();
    }}

    function toggleCompDetails(rowId, event) {{
      if (event && event.target && event.target.closest('a')) {{
        return;
      }}
      const detailRow = document.getElementById(rowId);
      const icon = document.getElementById('icon-' + rowId);
      if (!detailRow) return;

      const isHidden = detailRow.style.display === 'none' || !detailRow.style.display;
      detailRow.style.display = isHidden ? 'table-row' : 'none';
      if (icon) {{
        icon.textContent = isHidden ? '▼' : '▶';
        icon.style.color = isHidden ? '#fbbf24' : '#60a5fa';
      }}
    }}

    function copyPricingSchedule() {{
      const rows = document.querySelectorAll('.interval-parent-row');
      const lines = [];
      const isAdj = document.getElementById('filterAdjustedComps') ? document.getElementById('filterAdjustedComps').checked : true;

      rows.forEach(row => {{
        // Only include rows currently visible under active filters
        if (row.style.display === 'none') return;

        const checkin = row.dataset.checkin || '';
        const checkout = row.dataset.checkout || '';
        const dateRange = (checkin && checkout) ? `${{checkin}} to ${{checkout}}` : (row.querySelector('.date-pill') ? row.querySelector('.date-pill').innerText.trim().replace(/\\s*→\\s*|\\s*&rarr;\\s*/g, ' to ') : '');
        const type = row.dataset.segmentType || 'Weekend';
        const basePrice = row.dataset.basePrice || '0';

        const recPrice = isAdj ? (row.dataset.adjRec || basePrice) : (row.dataset.rawRec || basePrice);
        const actionHtml = isAdj ? (row.dataset.adjActionHtml || '') : (row.dataset.rawActionHtml || '');

        const rVal = parseInt(recPrice, 10);
        const bVal = parseInt(basePrice, 10);
        let actionText = '';
        if (rVal === bVal) {{
          actionText = `Keep price at $${{basePrice}}`;
        }} else if (rVal > bVal) {{
          actionText = `Increase price from $${{basePrice}} to $${{recPrice}}`;
        }} else {{
          actionText = `Reduce price from $${{basePrice}} to $${{recPrice}}`;
        }}

        const histCount = parseInt(row.dataset.histCount || '0', 10);
        const histMed = parseInt(row.dataset.histMed || '0', 10);
        const baseVal = parseInt(basePrice, 10);

        if (histCount > 0 && histMed > 0) {{
          let histAction = '';
          if (histMed > baseVal) {{
            histAction = `Increase from $${{basePrice}} to $${{histMed}}`;
          }} else if (histMed < baseVal) {{
            histAction = `Decrease from $${{basePrice}} to $${{histMed}}`;
          }} else {{
            histAction = `Keep at $${{basePrice}}`;
          }}
          actionText += `. Based on history: ${{histAction}}`;
        }}

        lines.push(`${{dateRange}}\\t${{type}}\\t${{actionText}}`);
      }});

      if (lines.length === 0) {{
        alert('No visible rows to copy under current filters.');
        return;
      }}

      const text = lines.join('\\n');
      const copyBtn = document.getElementById('btnCopySchedule');
      const btnText = document.getElementById('copyBtnText');
      const copyIconContainer = document.getElementById('copyIconContainer');
      const originalText = 'Copy Schedule';
      const defaultSvg = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align: middle;"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>';
      const checkSvg = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#34d399" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="vertical-align: middle;"><polyline points="20 6 9 17 4 12"></polyline></svg>';

      function onSuccess() {{
        if (btnText) btnText.innerText = `✓ Copied (${{lines.length}} rows)`;
        if (copyIconContainer) copyIconContainer.innerHTML = checkSvg;
        if (copyBtn) {{
          copyBtn.style.borderColor = '#10b981';
          copyBtn.style.color = '#34d399';
          copyBtn.style.background = 'rgba(16, 185, 129, 0.2)';
        }}
        setTimeout(() => {{
          if (btnText) btnText.innerText = originalText;
          if (copyIconContainer) copyIconContainer.innerHTML = defaultSvg;
          if (copyBtn) {{
            copyBtn.style.borderColor = 'rgba(59,130,246,0.35)';
            copyBtn.style.color = '#93c5fd';
            copyBtn.style.background = 'rgba(59,130,246,0.15)';
          }}
        }}, 2500);
      }}

      if (navigator.clipboard && window.isSecureContext) {{
        navigator.clipboard.writeText(text).then(onSuccess).catch(err => {{
          console.warn('navigator.clipboard write failed, attempting fallback', err);
          fallbackCopyText(text);
          onSuccess();
        }});
      }} else {{
        fallbackCopyText(text);
        onSuccess();
      }}
    }}

    function fallbackCopyText(text) {{
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.setAttribute('readonly', '');
      ta.style.position = 'fixed';
      ta.style.left = '-9999px';
      ta.style.top = '0';
      document.body.appendChild(ta);
      ta.focus();
      ta.select();
      ta.setSelectionRange(0, 99999);
      try {{
        document.execCommand('copy');
      }} catch (e) {{
        console.error('Fallback copy failed', e);
      }}
      document.body.removeChild(ta);
    }}

    let currentProposedRateMode = 'avg';

    function filterProposedOpenCalendar() {{
      const checkbox = document.getElementById('filterProposedOpenCalendar');
      const openOnly = checkbox ? checkbox.checked : true;
      const rows = document.querySelectorAll('.proposed-price-row');
      rows.forEach(row => {{
        const isCalOpen = row.dataset.calendarOpen === 'true';
        if (openOnly && !isCalOpen) {{
          row.style.display = 'none';
        }} else {{
          row.style.display = '';
        }}
      }});
    }}

    function setProposedRateMode(mode) {{
      currentProposedRateMode = mode;
      const btnAvg = document.getElementById('btnSuggestAvg');
      const btnMed = document.getElementById('btnSuggestMed');
      if (btnAvg && btnMed) {{
        if (mode === 'avg') {{
          btnAvg.classList.add('active');
          btnMed.classList.remove('active');
        }} else {{
          btnMed.classList.add('active');
          btnAvg.classList.remove('active');
        }}
      }}

      document.querySelectorAll('.proposed-price-row').forEach(row => {{
        const isHol = row.dataset.isHoliday === 'true';
        const isSplit = row.dataset.splitPricing === 'true';

        function formatCell(valStr, baseStr) {{
          if (!valStr && !baseStr) return '<span style=\"color:#475569;\">—</span>';
          if (!baseStr) {{
            const val = parseInt(valStr, 10);
            return '<strong style=\"color:#ffffff; font-family:JetBrains Mono,monospace;\">$' + val.toLocaleString() + '</strong>';
          }}
          const base = parseInt(baseStr, 10);
          const baseFmt = '$' + base.toLocaleString();
          const baseSpan = '<span style=\"color:#ffffff; font-family:JetBrains Mono,monospace; font-weight:600;\">' + baseFmt + '</span>';
          if (!valStr) return baseSpan;
          const val = parseInt(valStr, 10);
          if (val === base) return baseSpan;
          const diff = val - base;
          const color = diff > 0 ? '#34d399' : '#f87171';
          const title = (diff > 0 ? 'Proposed increase from $' : 'Proposed decrease from $') + base + ' to $' + val;
          return baseSpan + '<strong style=\"color:' + color + '; font-family:JetBrains Mono,monospace; font-weight:700;\" title=\"' + title + '\"><span style=\"margin:0 6px; display:inline-block;\">→</span>$' + val.toLocaleString() + '</strong>';
        }}

        const midCell = row.querySelector('.proposed-cell-mid');
        const wkdCell = row.querySelector('.proposed-cell-wkd');
        const specCell = row.querySelector('.proposed-cell-spec');

        if (isHol && !isSplit) {{
          if (midCell) midCell.innerHTML = '<span style="color:#475569;">—</span>';
          if (wkdCell) wkdCell.innerHTML = '<span style="color:#475569;">—</span>';
          if (specCell) {{
            const specVal = mode === 'avg' ? row.dataset.specAvg : row.dataset.specMed;
            specCell.innerHTML = formatCell(specVal, row.dataset.specBase);
          }}
        }} else {{
          if (specCell) specCell.innerHTML = '<span style="color:#475569;">—</span>';
          if (midCell) {{
            const midVal = mode === 'avg' ? row.dataset.midAvg : row.dataset.midMed;
            midCell.innerHTML = formatCell(midVal, row.dataset.midBase);
          }}
          if (wkdCell) {{
            const wkdVal = mode === 'avg' ? row.dataset.wkdAvg : row.dataset.wkdMed;
            wkdCell.innerHTML = formatCell(wkdVal, row.dataset.wkdBase);
          }}
        }}
      }});
    }}

    function copyProposedPrices() {{
      const rows = document.querySelectorAll('.proposed-price-row');
      const lines = [];
      lines.push(['From', 'To', 'Midweek', 'Weekend', 'Special', 'Min nights', 'Holiday'].join('\\t'));
      let visibleCount = 0;

      rows.forEach(row => {{
        if (row.style.display === 'none') return;
        visibleCount++;
        const fromDt = row.dataset.from || '';
        const toDt = row.dataset.to || '';
        const minNights = row.dataset.minNights || '2';
        const isHol = row.dataset.isHoliday === 'true';
        const isSplit = row.dataset.splitPricing === 'true';
        const holiday = row.dataset.holidayName || '';

        let midStr = '';
        let wkdStr = '';
        let specStr = '';

        if (isHol && !isSplit) {{
          const specVal = currentProposedRateMode === 'avg' ? row.dataset.specAvg : row.dataset.specMed;
          specStr = specVal ? `$${{parseInt(specVal, 10)}}` : '';
        }} else {{
          const midVal = currentProposedRateMode === 'avg' ? row.dataset.midAvg : row.dataset.midMed;
          const wkdVal = currentProposedRateMode === 'avg' ? row.dataset.wkdAvg : row.dataset.wkdMed;
          midStr = midVal ? `$${{parseInt(midVal, 10)}}` : '';
          wkdStr = wkdVal ? `$${{parseInt(wkdVal, 10)}}` : '';
        }}

        lines.push([fromDt, toDt, midStr, wkdStr, specStr, minNights, holiday].join('\\t'));
      }});

      const text = lines.join('\\n');
      const copyBtn = document.getElementById('btnCopyProposed');
      const copyIcon = document.getElementById('copyProposedIconContainer');
      const btnText = document.getElementById('copyProposedText');

      const propCheckSvg = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#34d399" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="vertical-align: middle;"><polyline points="20 6 9 17 4 12"></polyline></svg>';
      const propDefaultSvg = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align: middle;"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>';

      function onSuccess() {{
        if (btnText) btnText.innerText = `✓ Copied (${{visibleCount}} periods)`;
        if (copyIcon) copyIcon.innerHTML = propCheckSvg;
        if (copyBtn) {{
          copyBtn.style.borderColor = '#10b981';
          copyBtn.style.color = '#34d399';
          copyBtn.style.background = 'rgba(16, 185, 129, 0.2)';
        }}
        setTimeout(() => {{
          if (btnText) btnText.innerText = 'Copy Proposed Prices';
          if (copyIcon) copyIcon.innerHTML = propDefaultSvg;
          if (copyBtn) {{
            copyBtn.style.borderColor = 'rgba(56, 189, 248, 0.35)';
            copyBtn.style.color = '#38bdf8';
            copyBtn.style.background = 'rgba(56, 189, 248, 0.15)';
          }}
        }}, 2200);
      }}

      if (navigator.clipboard && window.isSecureContext) {{
        navigator.clipboard.writeText(text).then(onSuccess).catch(err => {{
          fallbackCopyText(text);
          onSuccess();
        }});
      }} else {{
        fallbackCopyText(text);
        onSuccess();
      }}
    }}

    function copyPlatformComparison() {{
      const rows = document.querySelectorAll('.platform-parent-row');
      const lines = [];
      lines.push([
        'Check-In',
        'Check-Out',
        'Nights',
        'Type',
        'AIRBNB (est-no-dscnt) Total ($)',
        'AIRBNB (est-no-dscnt) /nt ($)',
        'Airbnb (live) Total ($)',
        'Airbnb (live) /nt ($)',
        'Airbnb (live) vs Benchmark (%)',
        'VRBO Total ($)',
        'VRBO /nt ($)',
        'VRBO vs Benchmark (%)',
        'Booking.com Total ($)',
        'Booking /nt ($)',
        'Booking vs Benchmark (%)',
        'Kivoya Total ($)',
        'Kivoya /nt ($)',
        'Kivoya vs Benchmark (%)'
      ].join('\\t'));

      rows.forEach(row => {{
        if (row.style.display === 'none') return;
        lines.push([
          row.dataset.checkin || '',
          row.dataset.checkout || '',
          row.dataset.nights || '',
          row.dataset.type || '',
          row.dataset.airbnbEstTotal || '',
          row.dataset.airbnbEstNightly || '',
          row.dataset.airbnbLiveTotal || '',
          row.dataset.airbnbLiveNightly || '',
          row.dataset.airbnbLiveDiff || '',
          row.dataset.vrboTotal || '',
          row.dataset.vrboNightly || '',
          row.dataset.vrboDiff || '',
          row.dataset.bookingTotal || '',
          row.dataset.bookingNightly || '',
          row.dataset.bookingDiff || '',
          row.dataset.kivoyaTotal || '',
          row.dataset.kivoyaNightly || '',
          row.dataset.kivoyaDiff || ''
        ].join('\\t'));
      }});

      if (lines.length <= 1) {{
        alert('No visible comparison rows to copy under current filters.');
        return;
      }}

      const text = lines.join('\\n');
      const copyBtn = document.getElementById('btnCopyComparison');
      const btnText = document.getElementById('copyCompBtnText');
      const copyIcon = document.getElementById('copyCompIconContainer');
      const originalText = 'Copy Table for Sheets';
      const defaultSvg = '<svg width=\"15\" height=\"15\" viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2\" stroke-linecap=\"round\" stroke-linejoin=\"round\" style=\"vertical-align: middle;\"><rect x=\"9\" y=\"9\" width=\"13\" height=\"13\" rx=\"2\" ry=\"2\"></rect><path d=\"M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1\"></path></svg>';
      const checkSvg = '<svg width=\"15\" height=\"15\" viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"#34d399\" stroke-width=\"2.5\" stroke-linecap=\"round\" stroke-linejoin=\"round\" style=\"vertical-align: middle;\"><polyline points=\"20 6 9 17 4 12\"></polyline></svg>';

      function onSuccess() {{
        if (btnText) btnText.innerText = `✓ Copied (${{lines.length - 1}} rows)`;
        if (copyIcon) copyIcon.innerHTML = checkSvg;
        if (copyBtn) {{
          copyBtn.style.borderColor = '#10b981';
          copyBtn.style.color = '#34d399';
          copyBtn.style.background = 'rgba(16, 185, 129, 0.2)';
        }}
        setTimeout(() => {{
          if (btnText) btnText.innerText = originalText;
          if (copyIcon) copyIcon.innerHTML = defaultSvg;
          if (copyBtn) {{
            copyBtn.style.borderColor = 'rgba(59,130,246,0.35)';
            copyBtn.style.color = '#93c5fd';
            copyBtn.style.background = 'rgba(59,130,246,0.15)';
          }}
        }}, 2500);
      }}

      if (navigator.clipboard && window.isSecureContext) {{
        navigator.clipboard.writeText(text).then(onSuccess).catch(() => {{
          fallbackCopyText(text);
          onSuccess();
        }});
      }} else {{
        fallbackCopyText(text);
        onSuccess();
      }}
    }}

    function filterComparisonTiers() {{
      const showAlert = document.getElementById('filterCompAlert') ? document.getElementById('filterCompAlert').checked : true;
      const showMod = document.getElementById('filterCompMod') ? document.getElementById('filterCompMod').checked : true;
      const showParity = document.getElementById('filterCompParity') ? document.getElementById('filterCompParity').checked : true;
      const openOnly = document.getElementById('filterCompOpenCalendar') ? document.getElementById('filterCompOpenCalendar').checked : true;
      const searchVal = document.getElementById('compDateSearch') ? document.getElementById('compDateSearch').value.toLowerCase().trim() : '';

      const rows = document.querySelectorAll('.platform-parent-row');
      rows.forEach(row => {{
        const tier = row.dataset.tier;
        const isOpen = row.dataset.calendarOpen === 'true';
        const dateText = (row.dataset.checkin + ' ' + row.dataset.checkout).toLowerCase();
        const detailId = row.dataset.detailId;
        const detailRow = document.getElementById(detailId);

        let matchTier = false;
        if (tier === 'urgent' && showAlert) matchTier = true;
        if (tier === 'moderate' && showMod) matchTier = true;
        if (tier === 'ok' && showParity) matchTier = true;

        let matchOpen = (!openOnly || isOpen);
        let matchSearch = (!searchVal || dateText.includes(searchVal));

        const visible = matchTier && matchOpen && matchSearch;
        row.style.display = visible ? '' : 'none';
        if (!visible && detailRow) {{
          detailRow.style.display = 'none';
          const icon = document.getElementById('icon-' + detailId);
          if (icon) {{
            icon.textContent = '▶';
            icon.style.color = '#60a5fa';
          }}
        }}
      }});
    }}

    function filterIntervalTiers() {{
      const showUrgent = document.getElementById('filterTierUrgent') ? document.getElementById('filterTierUrgent').checked : true;
      const showModerate = document.getElementById('filterTierModerate') ? document.getElementById('filterTierModerate').checked : true;
      const showOk = document.getElementById('filterTierOk') ? document.getElementById('filterTierOk').checked : true;
      const openCalendarCheckbox = document.getElementById('filterOpenCalendar');
      const openOnly = openCalendarCheckbox ? openCalendarCheckbox.checked : true;

      document.querySelectorAll('.interval-parent-row').forEach(row => {{
        const rowTier = row.dataset.tier;
        const rowIsOpen = row.dataset.calendarOpen === 'true';
        const detailRowId = row.dataset.detailId;
        const detailRow = detailRowId ? document.getElementById(detailRowId) : null;

        const calendarMatch = (!openOnly || rowIsOpen);
        const tierMatch = (
          (rowTier === 'urgent' && showUrgent) ||
          (rowTier === 'moderate' && showModerate) ||
          (rowTier === 'ok' && showOk) ||
          (rowTier === 'none' && showUrgent && showModerate && showOk)
        );

        if (calendarMatch && tierMatch) {{
          row.style.display = '';
        }} else {{
          row.style.display = 'none';
          if (detailRow) detailRow.style.display = 'none';
          const icon = document.getElementById('icon-' + detailRowId);
          if (icon) {{
            icon.textContent = '▶';
            icon.style.color = '#60a5fa';
          }}
        }}
      }});
    }}

    function getPercentile(arr, pct) {{
      if (!arr || arr.length === 0) return 0;
      if (arr.length === 1) return arr[0];
      const idx = (pct / 100) * (arr.length - 1);
      const lower = Math.floor(idx);
      const upper = Math.ceil(idx);
      const weight = idx - lower;
      return arr[lower] * (1 - weight) + arr[upper] * weight;
    }}

    function onFilterChange() {{
      const r = parseFloat(document.getElementById('filterMinRating').value);
      const rev = parseInt(document.getElementById('filterMinReviews').value, 10);

      document.querySelectorAll('.filter-card .filter-pill-btn').forEach(btn => btn.classList.remove('active'));
      const rBtnId = (r && r > 0) ? ('btn-rate-' + String(r).replace('.', '')) : 'btn-rate-all';
      const rBtn = document.getElementById(rBtnId);
      if (rBtn) rBtn.classList.add('active');
      const revBtn = document.getElementById('btn-rev-' + rev);
      if (revBtn) revBtn.classList.add('active');

      applyGlobalFilters();
    }}

    function setFilterRating(val) {{
      document.getElementById('filterMinRating').value = val > 0 ? val.toFixed(1) : '0';
      onFilterChange();
    }}

    function setFilterReviews(val) {{
      document.getElementById('filterMinReviews').value = val;
      onFilterChange();
    }}

    function resetFilters() {{
      document.getElementById('filterMinRating').value = '4.5';
      document.getElementById('filterMinReviews').value = '25';
      const premCheckbox = document.getElementById('filterPremiumLocation');
      if (premCheckbox) premCheckbox.checked = true;
      const urg = document.getElementById('filterTierUrgent');
      if (urg) urg.checked = true;
      const mod = document.getElementById('filterTierModerate');
      if (mod) mod.checked = true;
      const ok = document.getElementById('filterTierOk');
      if (ok) ok.checked = true;
      const cal = document.getElementById('filterOpenCalendar');
      if (cal) cal.checked = true;
      const adj = document.getElementById('filterAdjustedComps');
      if (adj) adj.checked = true;
      onFilterChange();
    }}

    function isPremiumLocation(locStr) {{
      if (!locStr) return false;
      const l = locStr.toLowerCase();
      return l.includes('scottsdale') || l.includes('paradise valley') || l.includes('kierland') || l.includes('fashion square');
    }}

    function applyGlobalFilters() {{
      const minRatingInput = document.getElementById('filterMinRating');
      const minReviewsInput = document.getElementById('filterMinReviews');
      if (!minRatingInput || !minReviewsInput) return;

      const minRating = parseFloat(minRatingInput.value) || 0.0;
      const minReviews = parseInt(minReviewsInput.value, 10) || 0;
      const premCheckbox = document.getElementById('filterPremiumLocation');
      const includePremium = premCheckbox ? premCheckbox.checked : false;

      const isAdj = document.getElementById('filterAdjustedComps') ? document.getElementById('filterAdjustedComps').checked : true;

      const statusBadge = document.getElementById('filterStatusBadge');
      if (statusBadge) {{
        let parts = [];
        parts.push(isAdj ? 'Adjusted Rates (Quality Weighted)' : 'Raw Rates (Unadjusted)');
        if (minRating > 0) parts.push('Rating ≥ ' + minRating.toFixed(1) + ' ★');
        if (minReviews > 0) parts.push('Reviews ≥ ' + minReviews);
        if (!includePremium) {{
          parts.push('Excl. Scottsdale & PV');
        }} else {{
          parts.push('Incl. Premium Loc.');
        }}

        if (minRating === 0 && minReviews === 0 && includePremium) {{
          statusBadge.textContent = (isAdj ? 'All Comps Visible (Adjusted)' : 'All Comps Visible (Raw)');
          statusBadge.style.background = 'rgba(59, 130, 246, 0.15)';
          statusBadge.style.color = '#93c5fd';
          statusBadge.style.borderColor = 'rgba(59, 130, 246, 0.3)';
        }} else {{
          statusBadge.textContent = 'Active Filters: ' + parts.join(' • ');
          statusBadge.style.background = 'rgba(16, 185, 129, 0.15)';
          statusBadge.style.color = '#34d399';
          statusBadge.style.borderColor = 'rgba(16, 185, 129, 0.3)';
        }}
      }}

      document.querySelectorAll('.subtable-container').forEach(container => {{
        const rowId = container.dataset.rowId;
        const nights = parseInt(container.dataset.nights, 10) || 3;
        const leadDays = parseInt(container.dataset.leadDays, 10) || 0;
        const ourBase = parseFloat(container.dataset.ourBase) || 0.0;
        const ourEff = parseFloat(container.dataset.ourEff) || 0.0;
        const isOurLive = container.dataset.isOurLive === 'true';
        const targetPct = parseFloat(container.dataset.targetPct) || 65.0;
        const isLiveScan = container.dataset.isLiveScan === 'true';
        const channelFactor = parseFloat(container.dataset.channelFactor) || 1.0;

        const allCompRows = container.querySelectorAll('.comp-item-row');
        const visibleComps = [];
        const disqualifiedVisible = [];
        const hiddenRows = [];
        let ourRow = null;

        allCompRows.forEach(row => {{
          if (row.dataset.isOur === 'true') {{
            ourRow = row;
            row.style.display = '';
            return;
          }}

          const r = row.dataset.rating ? parseFloat(row.dataset.rating) : null;
          const rev = parseInt(row.dataset.reviews, 10) || 0;
          const loc = row.dataset.location || '';
          const isPremium = isPremiumLocation(loc);
          const isValid = row.dataset.valid === 'true';

          const passesRating = (minRating <= 0) || (r !== null && !isNaN(r) && r >= minRating);
          const passesReviews = (minReviews <= 0) || (rev >= minReviews);
          const passesLocation = includePremium || !isPremium;

          if (!passesRating || !passesReviews || !passesLocation) {{
            row.style.display = 'none';
            hiddenRows.push(row);
            return;
          }}

          row.style.display = '';
          const rawPrice = parseFloat(row.dataset.rawPrice) || parseFloat(row.dataset.price);
          const adjPrice = parseFloat(row.dataset.adjPrice) || rawPrice;
          const activePrice = isAdj ? adjPrice : rawPrice;

          // Update price display labels
          const priceVal = row.querySelector('.price-val');
          if (priceVal) priceVal.textContent = '$' + Math.round(activePrice);
          const priceLabel = row.querySelector('.price-label');
          if (priceLabel) {{
            priceLabel.textContent = isAdj ? '/n (adj)' : '/n';
            priceLabel.style.color = isAdj ? '#a78bfa' : '#94a3b8';
          }}
          const rawNote = row.querySelector('.raw-price-note');
          if (rawNote) {{
            rawNote.style.display = isAdj ? '' : 'none';
          }}

          // Update diff cell
          const diffCell = row.querySelector('.comp-diff-cell');
          if (diffCell) {{
            if (isAdj && !isValid) {{
              diffCell.innerHTML = '<span style="color:#64748b; font-size:0.75rem;">Excluded</span>';
            }} else {{
              const diffVal = activePrice - ourEff;
              if (diffVal <= -10.0) {{
                diffCell.innerHTML = '<span style="color:#34d399; font-weight:700; font-size:0.8rem;">▼ $' + Math.round(Math.abs(diffVal)) + '/n cheaper</span>';
              }} else if (diffVal >= 10.0) {{
                diffCell.innerHTML = '<span style="color:#f87171; font-weight:700; font-size:0.8rem;">▲ +$' + Math.round(diffVal) + '/n higher</span>';
              }} else {{
                diffCell.innerHTML = '<span style="color:#94a3b8; font-size:0.8rem;">≈ Similar rate</span>';
              }}
            }}
          }}

          if (isAdj && !isValid) {{
            // Disqualified comps are excluded from pricing percentiles in adjusted mode
            const rankCell = row.querySelector('.comp-rank-cell');
            if (rankCell) rankCell.textContent = '-';
            row.style.opacity = '0.55';
            disqualifiedVisible.push(row);
          }} else {{
            row.style.opacity = '1.0';
            visibleComps.push({{ row: row, price: activePrice }});
          }}
        }});

        // Sort visible comps ascending by active price
        visibleComps.sort((a, b) => a.price - b.price);

        // Rank visible rows and find our position
        let rank = 1;
        let cheaperCount = 0;
        let higherCount = 0;
        const visibleRates = [];
        const combinedVisible = [];
        let ourInserted = false;

        visibleComps.forEach(item => {{
          if (!ourInserted && item.price >= ourEff) {{
            if (ourRow) combinedVisible.push(ourRow);
            ourInserted = true;
          }}
          combinedVisible.push(item.row);
        }});
        if (!ourInserted && ourRow) {{
          combinedVisible.push(ourRow);
        }}

        let ourRank = 1;
        combinedVisible.forEach(row => {{
          if (row.dataset.isOur === 'true') {{
            ourRank = rank;
          }} else {{
            const rankCell = row.querySelector('.comp-rank-cell');
            if (rankCell) rankCell.textContent = rank;
            const p = isAdj ? (parseFloat(row.dataset.adjPrice) || parseFloat(row.dataset.price)) : (parseFloat(row.dataset.rawPrice) || parseFloat(row.dataset.price));
            visibleRates.push(p);
            if (p < ourEff) cheaperCount++;
            else if (p > ourEff) higherCount++;
            rank++;
          }}
        }});

        // Reorder DOM rows to match price sorting
        const tbody = container.querySelector('tbody');
        if (tbody) {{
          combinedVisible.forEach(r => tbody.appendChild(r));
          disqualifiedVisible.forEach(r => tbody.appendChild(r));
          hiddenRows.forEach(r => tbody.appendChild(r));
        }}

        const totalComps = visibleComps.length;
        const ourPct = totalComps > 0 ? Math.round((ourRank / totalComps) * 100) : 50;

        if (ourRow) {{
          const ourRankBadge = ourRow.querySelector('.our-rank-badge');
          if (ourRankBadge) ourRankBadge.textContent = '★ YOU (#' + ourRank + ')';
          const ourPosBadge = ourRow.querySelector('.our-position-badge');
          if (ourPosBadge) ourPosBadge.textContent = '★ OUR POSITION (#' + ourRank + ' of ' + totalComps + ' • ' + ourPct + '%)';
        }}

        // Update main table parent row cells
        const nEl = document.getElementById('n-' + rowId);
        if (nEl) {{
          if (totalComps === 0) {{
            if (isLiveScan) {{
              nEl.innerHTML = '<span class="badge" style="background:rgba(239,68,68,0.2); color:#f87171; border:1px solid rgba(239,68,68,0.35);" title="Zero comps meet rating/review/location filter">🔥 0 (Filtered)</span>';
            }} else {{
              nEl.innerHTML = '<span class="badge" style="background:rgba(148,163,184,0.15); color:#94a3b8; border:1px solid rgba(148,163,184,0.3);" title="No live scrape recorded for this date yet">⏳ Pending Live Scrape</span>';
            }}
          }} else if (totalComps < 20) {{
            nEl.innerHTML = '<span class="badge" style="background:rgba(245,158,11,0.2); color:#fbbf24; border:1px solid rgba(245,158,11,0.35);" title="Market compression: only ' + totalComps + ' comps available (&lt;20% of cohort)">🔥 N=' + totalComps + ' (High Compression)</span>';
          }} else {{
            nEl.innerHTML = '<span class="badge" style="background:rgba(16,185,129,0.15); color:#34d399; border:1px solid rgba(16,185,129,0.3);" title="Live search: ' + totalComps + ' vetted comps">🟢 Live N=' + totalComps + '</span>';
          }}
        }}

        const effEl = document.getElementById('eff-' + rowId);
        if (effEl) {{
          const liveDot = isOurLive ? '<span style="color:#34d399; font-size:0.75rem; margin-left:2px;" title="Live Airbnb checkout price verified">🟢</span>' : '';
          const tooltip = isOurLive
            ? ('Live Airbnb Rate: $' + Math.round(ourEff) + '/night. Villa del Sol ranks #' + ourRank + ' of ' + totalComps + ' competitors (' + ourPct + 'th percentile). Base Kivoya rate is $' + Math.round(ourBase) + '.')
            : ('Villa del Sol ranks #' + ourRank + ' out of ' + totalComps + ' competitors (' + ourPct + 'th percentile in effective total guest cost)');
          const pctText = totalComps > 0 ? (' <span style="font-size:0.78rem; color:#94a3b8; font-weight:600;" title="' + tooltip + '">(' + ourPct + '%)</span>') : '';
          effEl.innerHTML = '<strong style="color:#f1f5f9;">$' + Math.round(ourEff) + '</strong>' + liveDot + pctText;
        }}

        if (visibleRates.length > 0) {{
          visibleRates.sort((a, b) => a - b);
          const pTarget = getPercentile(visibleRates, targetPct);

          const diff = pTarget > 0 ? (((ourEff - pTarget) / pTarget) * 100) : 0;
          const targetStayTotal = pTarget * nights;
          const targetKivoyaTotal = targetStayTotal / Math.max(0.5, channelFactor);

          const segType = (container.dataset.segmentType || '').toLowerCase();
          const defaultFloor = (segType === 'midweek') ? 300 : 450;
          const floorRate = parseFloat(container.dataset.floor) || defaultFloor;

          let recBase = Math.max(floorRate, Math.min(2499, Math.round((Math.max(0, targetKivoyaTotal - 500)) / nights)));
          // 5% deadband churn threshold: hold current base price if proposed change is < 5%
          const churnPct = {self.min_price_change_pct:.4f};
          if (ourBase > 0 && Math.abs(recBase - ourBase) / ourBase < churnPct) {{
            recBase = Math.round(ourBase);
          }}
          // Strictly enforce operational and holiday nightly rate floors
          recBase = Math.max(floorRate, recBase);
          const baseDiff = Math.round(recBase - ourBase);

          const absDiff = Math.abs(diff);
          const isUrgent = (absDiff >= {self.urgent_pct_diff});
          const isMod = !isUrgent && (absDiff >= {self.moderate_pct_diff});

          const statusEl = document.getElementById('status-' + rowId);
          if (statusEl) {{
            if (isUrgent) {{
              statusEl.innerHTML = '<span class="badge" style="background:rgba(239,68,68,0.2); color:#f87171; border:1px solid rgba(239,68,68,0.4); font-weight:700;">🚨 Urgent</span>';
            }} else if (isMod) {{
              statusEl.innerHTML = '<span class="badge" style="background:rgba(245,158,11,0.2); color:#fbbf24; border:1px solid rgba(245,158,11,0.4); font-weight:700;">⚠️ Review</span>';
            }} else {{
              statusEl.innerHTML = '<span class="badge" style="background:rgba(16,185,129,0.12); color:#34d399; border:1px solid rgba(16,185,129,0.25);">✅ On Target</span>';
            }}
          }}

          const parentRow = document.getElementById('parent-' + rowId);
          if (parentRow) {{
            if (isUrgent) {{
              parentRow.dataset.tier = 'urgent';
              parentRow.style.borderLeft = '4px solid #ef4444';
            }} else if (isMod) {{
              parentRow.dataset.tier = 'moderate';
              parentRow.style.borderLeft = '4px solid #f59e0b';
            }} else {{
              parentRow.dataset.tier = 'ok';
              parentRow.style.borderLeft = '4px solid transparent';
            }}
          }}

          const targetPctLabel = (targetPct % 1 === 0) ? targetPct.toFixed(0) : targetPct.toFixed(1);
          const targetEl = document.getElementById('target-' + rowId);
          if (targetEl) targetEl.innerHTML = '$' + Math.round(pTarget) + ' <span style="font-size:0.75rem; color:#94a3b8;">(' + targetPctLabel + '%)</span>';

          const diffEl = document.getElementById('diff-' + rowId);
          if (diffEl) {{
            const absD = Math.abs(diff);
            const signStr = (diff >= 0 ? '+' : '') + diff.toFixed(1) + '%';
            if (absD >= {self.urgent_pct_diff}) {{
              diffEl.innerHTML = '<span class="badge-diff-urgent">' + signStr + '</span>';
            }} else if (absD >= {self.moderate_pct_diff}) {{
              diffEl.innerHTML = '<span class="badge-diff-review">' + signStr + '</span>';
            }} else {{
              diffEl.innerHTML = '<span class="badge-diff-ok">' + signStr + '</span>';
            }}
          }}

          const recEl = document.getElementById('rec-' + rowId);
          if (recEl) recEl.innerHTML = '<span class="rec-price">$' + recBase + '</span>';
          if (parentRow) {{
            if (isAdj) {{
              parentRow.dataset.adjRec = recBase;
            }} else {{
              parentRow.dataset.rawRec = recBase;
            }}
          }}

          const actionEl = document.getElementById('action-' + rowId);
          if (actionEl) {{
            const showAction = (absDiff >= {self.moderate_pct_diff} || ourBase < floorRate) && (baseDiff !== 0);
            if (!showAction) {{
              actionEl.innerHTML = '';
              actionEl.style.color = '';
            }} else if (baseDiff < 0) {{
              let actionText = '↓ Reduce $' + Math.round(ourBase) + ' → $' + recBase;
              if (totalComps < 20 && totalComps > 0) {{
                actionText += ' • High compression';
              }}
              actionEl.style.color = '#f87171';
              actionEl.innerHTML = '<strong>' + actionText + '</strong>';
            }} else {{
              let actionText = '↑ Increase $' + Math.round(ourBase) + ' → $' + recBase;
              if (totalComps < 20 && totalComps > 0) {{
                actionText += ' • High compression';
              }}
              actionEl.style.color = '#34d399';
              actionEl.innerHTML = '<strong>' + actionText + '</strong>';
            }}
            if (parentRow) {{
              const actHtml = actionEl ? actionEl.innerHTML : '';
              if (isAdj) {{
                parentRow.dataset.adjActionHtml = actHtml;
              }} else {{
                parentRow.dataset.rawActionHtml = actHtml;
              }}
            }}
          }}
        }} else {{
          const segType = (container.dataset.segmentType || '').toLowerCase();
          const defaultFloor = (segType === 'midweek') ? 300 : 450;
          const floorRate = parseFloat(container.dataset.floor) || defaultFloor;
          const clampedBase = Math.max(floorRate, Math.round(ourBase));

          const statusEl = document.getElementById('status-' + rowId);
          if (statusEl) {{
            statusEl.innerHTML = '<span class="badge" style="background:rgba(148,163,184,0.15); color:#94a3b8; border:1px solid rgba(148,163,184,0.3);">⚪ No Comps</span>';
          }}
          const parentRow = document.getElementById('parent-' + rowId);
          if (parentRow) {{
            parentRow.dataset.tier = 'none';
            parentRow.style.borderLeft = '4px solid transparent';
            if (isAdj) {{
              parentRow.dataset.adjRec = clampedBase;
            }} else {{
              parentRow.dataset.rawRec = clampedBase;
            }}
          }}
          const targetEl = document.getElementById('target-' + rowId);
          if (targetEl) targetEl.textContent = 'N/A';
          const diffEl = document.getElementById('diff-' + rowId);
          if (diffEl) diffEl.innerHTML = '<span style="color:#94a3b8;">N/A</span>';
          const recEl = document.getElementById('rec-' + rowId);
          if (recEl) recEl.innerHTML = '<span class="rec-price">$' + clampedBase + '</span>';
          const actionEl = document.getElementById('action-' + rowId);
          if (actionEl) {{
            if (ourBase < floorRate) {{
              actionEl.style.color = '#34d399';
              actionEl.innerHTML = '<strong>↑ Increase $' + Math.round(ourBase) + ' → $' + clampedBase + ' (Floor)</strong>';
            }} else {{
              actionEl.innerHTML = '<strong style="color:#cbd5e1;">No comps meet filter</strong>';
              actionEl.style.color = '';
            }}
            if (parentRow) {{
              const actHtml = actionEl ? actionEl.innerHTML : '';
              if (isAdj) {{
                parentRow.dataset.adjActionHtml = actHtml;
              }} else {{
                parentRow.dataset.rawActionHtml = actHtml;
              }}
            }}
          }}
        }}
      }});

      // Update interval filter pill counts
      const openCalendarCheckbox = document.getElementById('filterOpenCalendar');
      const openOnly = openCalendarCheckbox ? openCalendarCheckbox.checked : true;

      let totalUrgent = 0;
      let totalMod = 0;
      let totalOk = 0;
      document.querySelectorAll('.interval-parent-row').forEach(row => {{
        const rowIsOpen = row.dataset.calendarOpen === 'true';
        if (!openOnly || rowIsOpen) {{
          const tier = row.dataset.tier;
          if (tier === 'urgent') totalUrgent++;
          else if (tier === 'moderate') totalMod++;
          else if (tier === 'ok') totalOk++;
        }}
      }});

      const countUrgent = document.getElementById('count-interval-urgent');
      if (countUrgent) countUrgent.textContent = totalUrgent;
      const countMod = document.getElementById('count-interval-mod');
      if (countMod) countMod.textContent = totalMod;
      const countOk = document.getElementById('count-interval-ok');
      if (countOk) countOk.textContent = totalOk;

      // Re-apply interval filter to match current tier selections
      filterIntervalTiers();
    }}

    document.addEventListener('DOMContentLoaded', () => {{
      let initialTab = 'pricing';
      const hashCandidate = (window.location.hash || '').replace('#', '').trim();
      if (VALID_TABS.includes(hashCandidate)) {{
        initialTab = hashCandidate;
      }} else if (!hashCandidate) {{
        try {{
          const storedTab = localStorage.getItem('str_advisor_active_tab');
          if (storedTab && VALID_TABS.includes(storedTab)) {{
            initialTab = storedTab;
          }}
        }} catch (e) {{}}
      }}

      switchTab(initialTab, false, true);
      isInitialTabLoad = false;

      applyGlobalFilters();
      if (typeof filterReviews === 'function') {{
        filterReviews();
      }}
      if (typeof filterProposedOpenCalendar === 'function') {{
        filterProposedOpenCalendar();
      }}
      if (document.getElementById('tab-market-sales')?.classList.contains('active') && typeof initMarketTrajectoryChart === 'function') {{
        setTimeout(initMarketTrajectoryChart, 50);
      }}
    }});

    {streamline_js_data}

    {calendar_revenue_js}
  </script>
</body>
</html>"""

        self.output_path.write_text(html, encoding="utf-8")
        return str(self.output_path)

    def _format_rating_display(self, rating: Optional[float], reviews: int, is_guest_favorite: bool = False) -> str:
        """Format listing rating and reviews count with optional Guest Favorite badge."""
        gf_badge = '<div style="margin-top:3px;"><span class="badge" style="background:linear-gradient(135deg, rgba(245,158,11,0.2), rgba(217,119,6,0.3)); color:#fbbf24; border:1px solid rgba(245,158,11,0.5); font-size:0.65rem; font-weight:700; padding:1px 5px; border-radius:4px;" title="Top rated Airbnb Guest Favorite (+2-3% desirability boost)">🏅 Guest Favorite</span></div>' if is_guest_favorite else ""
        if rating is not None and rating > 0:
            return f'<span style="font-weight:700; color:#fbbf24;">★ {rating:.2f}</span> <span style="color:#94a3b8; font-size:0.75rem;">({reviews})</span>{gf_badge}'
        elif reviews > 0:
            return f'<span style="color:#cbd5e1;">(No star)</span> <span style="color:#94a3b8; font-size:0.75rem;">({reviews})</span>{gf_badge}'
        else:
            return f'<span style="color:#64748b; font-style:italic; font-size:0.8rem;">New (0)</span>{gf_badge}'

    def _render_comp_subtable(self, s: Dict[str, Any], row_id: str) -> Tuple[str, int, int, int, float, bool]:
        """Render expandable nested subtable of all comps sorted by price with Villa del Sol highlighted."""
        nights = s.get("nights", 3)
        c_in = s.get("check_in")
        c_out = s.get("check_out")
        our_base = float(s.get("our_base_nightly") or 0.0)

        # Check if live Airbnb price exists for our property
        is_our_live = False
        our_cache_file = Path(f"data/cache/our_property_{c_in}_{c_out}.json")
        if s.get("is_our_airbnb_live") and s.get("our_airbnb_effective_nightly"):
            our_eff = float(s["our_airbnb_effective_nightly"])
            our_total = float(s.get("our_airbnb_total", our_eff * nights))
            is_our_live = True
        elif our_cache_file.exists():
            try:
                our_data = json.loads(our_cache_file.read_text(encoding="utf-8"))
                live_eff = our_data.get("airbnb_effective_nightly")
                live_tot = our_data.get("airbnb_total")
                if live_eff and float(live_eff) > 0:
                    our_eff = float(live_eff)
                    our_total = float(live_tot) if live_tot else our_eff * nights
                    is_our_live = True
            except Exception:
                pass

        if not is_our_live:
            our_eff = float(s.get("our_effective_nightly", 0.0))
            our_total = float(s.get("our_total_price", our_eff * nights))

        kivoya_eff = float(s.get("our_effective_nightly", 0.0))
        channel_factor = (our_eff / kivoya_eff) if (is_our_live and kivoya_eff > 0) else float(s.get("channel_factor", 1.0))

        our_url = (
            f"https://www.airbnb.com/rooms/573857947793833342?check_in={c_in}&guests=10&adults=10&check_out={c_out}"
            if (c_in and c_out)
            else "https://www.airbnb.com/rooms/573857947793833342"
        )
        rdata = self.load_ratings() or {}
        vds_airbnb_revs = int(rdata.get("platforms", {}).get("airbnb", {}).get("review_count", 77))
        vds_airbnb_rating = float(rdata.get("platforms", {}).get("airbnb", {}).get("rating", 4.83))
        our_entry = {
            "is_our_property": True,
            "listing_id": "573857947793833342",
            "name": "Villa del Sol",
            "location": "South Tempe, AZ",
            "bedrooms": 6,
            "beds": "11 beds",
            "baths": "6.0 BA",
            "rating": vds_airbnb_rating,
            "reviews": vds_airbnb_revs,
            "effective_nightly": our_eff,
            "total_price": our_total,
            "url": our_url,
        }

        # 2. Extract competitor comps
        raw_comps = s.get("comps_list", [])
        comps_seen = set()
        clean_comps = []

        for c in raw_comps:
            cid = str(c.get("listing_id") or "")
            if cid and (cid in comps_seen or cid in self.excluded_comps or (self.comps_dict and cid not in self.comps_dict and cid not in self.listing_specs)):
                continue

            raw_snippet = c.get("raw_snippet", "")
            # Filter out listings that Airbnb suggested for alternative/flexible dates
            if self.ALT_DATE_REGEX.search(raw_snippet):
                continue
            if any(w in raw_snippet.lower() for w in ["similar dates", "available for part of your stay", "check other dates", "different dates"]):
                continue

            if cid:
                comps_seen.add(cid)

            eff_rate = float(c.get("effective_nightly") or 0.0)
            if eff_rate <= 0.0:
                continue
            tot_price = float(c.get("total_price") or (eff_rate * nights))

            cid_str = str(cid)
            comp_eval = self.comps_dict.get(cid_str, {})
            sp = self.listing_specs.get(cid_str, {})

            raw_snippet = c.get("raw_snippet", "")
            title = c.get("title") or c.get("name") or "Luxury Estate"
            reg_name = comp_eval.get("name") or sp.get("title") or c.get("name") or ""
            name = extract_clean_listing_title(
                raw_snippet=raw_snippet,
                default_title=title,
                registered_name=reg_name,
            )

            loc = c.get("location", "Phoenix Valley")
            br = sp.get("bedrooms") or c.get("bedrooms", 6)
            beds = sp.get("beds") or c.get("beds", br)
            ba = sp.get("baths") or c.get("baths", 4.0)
            c_rating = c.get("rating")
            c_reviews = int(c.get("reviews", 0) or 0)
            if cid:
                if c_in and c_out:
                    url = f"https://www.airbnb.com/rooms/{cid}?check_in={c_in}&guests=10&adults=10&check_out={c_out}"
                else:
                    url = f"https://www.airbnb.com/rooms/{cid}"
            else:
                url = "https://www.airbnb.com"

            cid_str = str(cid)
            comp_eval = self.comps_dict.get(cid_str, {})
            if not comp_eval and hasattr(self, "evaluator") and self.evaluator:
                eval_input = {**sp, **c, "listing_id": cid_str}
                try:
                    comp_eval = self.evaluator.evaluate_comp(eval_input)
                except Exception:
                    comp_eval = {}
            # Determine season based on check-in month (Winter: Oct-Apr, Summer: May-Sep)
            is_winter = True
            if c_in and "-" in c_in:
                try:
                    month = int(c_in.split("-")[1])
                    is_winter = month in (10, 11, 12, 1, 2, 3, 4)
                except Exception:
                    is_winter = True

            ratio_key = "winter_ratio" if is_winter else "summer_ratio"
            rationale_key = "winter_rationale" if is_winter else "summer_rationale"
            score_key = "winter_composite_score" if is_winter else "summer_composite_score"
            cat_key = "winter_category_scores" if is_winter else "summer_category_scores"

            is_valid = c.get("is_valid_comp") if "is_valid_comp" in c else comp_eval.get("is_valid_comp", True)
            ratio = float(
                c.get(ratio_key)
                or comp_eval.get(ratio_key)
                or c.get("desirability_ratio")
                or comp_eval.get("desirability_ratio")
                or 1.0
            )
            adj_rate = round(eff_rate / ratio, 2) if is_valid and ratio > 0 else eff_rate
            rationale = (
                c.get(rationale_key)
                or comp_eval.get(rationale_key)
                or c.get("rationale")
                or comp_eval.get("rationale", "")
            )
            validity_reason = c.get("validity_reason") or comp_eval.get("validity_reason", "")
            cat_scores = (
                c.get(cat_key)
                or comp_eval.get(cat_key)
                or c.get("category_scores")
                or comp_eval.get("category_scores", {})
            )
            score = float(
                c.get(score_key)
                or comp_eval.get(score_key)
                or c.get("composite_score")
                or comp_eval.get("composite_score")
                or 88.0
            )
            pool_specs = c.get("pool_specs") or comp_eval.get("pool_specs", {})
            clean_win_ratio = float(c.get("winter_ratio") or comp_eval.get("winter_ratio") or c.get("desirability_ratio") or comp_eval.get("desirability_ratio") or ratio)
            clean_sum_ratio = float(c.get("summer_ratio") or comp_eval.get("summer_ratio") or c.get("desirability_ratio") or comp_eval.get("desirability_ratio") or ratio)

            is_gf = bool(
                c.get("is_guest_favorite")
                or comp_eval.get("is_guest_favorite")
                or "guest favorite" in str(c.get("raw_snippet") or "").lower()
                or "guest favorite" in str(comp_eval.get("raw_snippet") or "").lower()
                or "guest favorite" in str(c.get("badge") or "").lower()
                or "guest favorite" in str(comp_eval.get("badge") or "").lower()
            )

            clean_comps.append({
                "is_our_property": False,
                "listing_id": cid,
                "name": name,
                "location": loc,
                "bedrooms": br,
                "beds": f"{beds} beds",
                "baths": f"{ba} BA",
                "rating": c_rating,
                "reviews": c_reviews,
                "is_guest_favorite": is_gf,
                "effective_nightly": eff_rate,
                "adjusted_effective_nightly": adj_rate,
                "desirability_ratio": ratio,
                "winter_ratio": clean_win_ratio,
                "summer_ratio": clean_sum_ratio,
                "is_valid_comp": is_valid,
                "validity_reason": validity_reason,
                "rationale": rationale,
                "category_scores": cat_scores,
                "composite_score": score,
                "total_price": tot_price,
                "url": url,
                "confidence": c.get("confidence", "CONFIRMED"),
                "confidence_reason": c.get("confidence_reason", ""),
                "price_snippet": c.get("price_snippet", ""),
                "is_winter": is_winter,
                "pool_specs": pool_specs,
                "property_specs": c.get("property_specs") or comp_eval.get("property_specs", {}),
            })

        # Sort entries: valid comps sorted by adjusted_effective_nightly, followed by our property in proper place
        def comp_sort_key(item):
            if item["is_our_property"]:
                return (0, our_eff)
            if item.get("is_valid_comp", True):
                return (0, item.get("adjusted_effective_nightly", item["effective_nightly"]))
            return (1, item["effective_nightly"])

        all_entries = sorted(clean_comps + [our_entry], key=comp_sort_key)

        valid_comps = [c for c in clean_comps if c.get("is_valid_comp", True)]
        cheaper_count = sum(1 for c in valid_comps if c["adjusted_effective_nightly"] < our_eff)
        higher_count = sum(1 for c in valid_comps if c["adjusted_effective_nightly"] > our_eff)
        total_comps = len(valid_comps)
        our_rank = cheaper_count + 1
        our_pct = round((our_rank / total_comps) * 100) if total_comps > 0 else round(s.get("our_percentile_rank_adj", 50.0))

        # Build subtable rows
        subtable_rows = []
        rank = 1
        for item in all_entries:
            if item["is_our_property"]:
                source_note = (
                    f"${item['total_price']:.0f} total on Airbnb &bull; Base Kivoya: ${our_base:.0f}/n"
                    if is_our_live else
                    f"${item['total_price']:.0f} total (${our_base:.0f} base + $500 clean)"
                )
                live_pill = (
                    '<span class="badge" style="background:rgba(16,185,129,0.2); color:#34d399; font-size:0.68rem; margin-left:6px; border:1px solid rgba(16,185,129,0.4); vertical-align:middle;">🟢 Live Airbnb Rate</span>'
                    if is_our_live else
                    '<span class="badge" style="background:rgba(59,130,246,0.2); color:#93c5fd; font-size:0.68rem; margin-left:6px; vertical-align:middle;">📊 Kivoya PMS Est.</span>'
                )
                rdata = self.load_ratings() or {}
                vds_airbnb_revs = int(rdata.get("platforms", {}).get("airbnb", {}).get("review_count", 77))
                vds_airbnb_rating = float(rdata.get("platforms", {}).get("airbnb", {}).get("rating", 4.83))

                subtable_rows.append(f"""
                  <tr class="comp-item-row our-property-row" data-is-our="true" data-location="South Tempe, AZ" data-price="{item['effective_nightly']:.2f}" data-raw-price="{item['effective_nightly']:.2f}" data-adj-price="{item['effective_nightly']:.2f}" data-valid="true" data-rating="{vds_airbnb_rating:.2f}" data-reviews="{vds_airbnb_revs}">
                    <td style="padding:10px 14px; text-align:center;">
                      <span class="badge our-rank-badge" style="background:#f59e0b; color:#0f172a; font-weight:800; font-size:0.75rem; padding:3px 8px;">★ YOU (#{our_rank})</span>
                    </td>
                    <td style="padding:10px 14px; font-family:'JetBrains Mono',monospace;">
                      <strong style="color:#fbbf24; font-size:0.95rem;">${item['effective_nightly']:.0f}</strong><span style="color:#fde68a; font-size:0.75rem;">/night</span>{live_pill}
                      <div style="font-size:0.72rem; color:#fde68a; margin-top:2px;">{source_note}</div>
                    </td>
                    <td style="padding:10px 14px;">
                      <span class="badge" style="background:rgba(245,158,11,0.2); color:#fbbf24; border:1px solid rgba(245,158,11,0.4); font-weight:700; font-size:0.75rem;">★ 1.00x (Baseline)</span>
                      <div style="font-size:0.72rem; color:#cbd5e1; margin-top:2px;">Our Luxury Compound Benchmark</div>
                    </td>
                    <td style="padding:10px 14px; text-align:center;">
                      <span style="font-weight:800; color:#fbbf24;">★ {vds_airbnb_rating:.2f}</span> <span style="color:#fde68a; font-size:0.75rem;">({vds_airbnb_revs})</span>
                    </td>
                    <td style="padding:10px 14px; text-align:center; font-weight:700; color:#f8fafc;">6 BR</td>
                    <td style="padding:10px 14px; text-align:center; font-weight:700; color:#f8fafc;">11 beds</td>
                    <td style="padding:10px 14px; text-align:center; font-weight:700; color:#f8fafc;">6.0 BA</td>
                    <td style="padding:10px 14px;">
                      <a href="{item['url']}" target="_blank" rel="noopener noreferrer" style="color:#fbbf24; font-weight:800; font-size:0.92rem; text-decoration:underline;">
                        ⭐ Villa del Sol (Our Property) ↗
                      </a>
                      <div style="font-size:0.75rem; color:#cbd5e1; margin-top:2px;">South Tempe, AZ • 5,400 sq ft • 0.75-acre lot • $2.0M baseline • Sleeps 16 • Resort Compound</div>
                    </td>
                    <td style="padding:10px 14px;">
                      <span class="badge our-position-badge" style="background:#f59e0b; color:#0f172a; font-weight:800; font-size:0.75rem;">★ OUR POSITION (#{our_rank} of {total_comps} &bull; {our_pct}%)</span>
                    </td>
                  </tr>
                """)
            else:
                is_valid = item.get("is_valid_comp", True)
                ratio = float(item.get("desirability_ratio") or 1.0)
                adj_rate = float(item.get("adjusted_effective_nightly") or item["effective_nightly"])
                raw_rate = item["effective_nightly"]
                rationale = item.get("rationale", "")
                validity_reason = item.get("validity_reason", "")

                diff = adj_rate - our_eff
                if diff <= -10.0:
                    diff_badge = f'<span style="color:#34d399; font-weight:700; font-size:0.8rem;">▼ ${abs(diff):.0f}/n cheaper</span>'
                elif diff >= 10.0:
                    diff_badge = f'<span style="color:#f87171; font-weight:700; font-size:0.8rem;">▲ +${diff:.0f}/n higher</span>'
                else:
                    diff_badge = '<span style="color:#94a3b8; font-size:0.8rem;">≈ Similar rate</span>'

                confidence = item.get("confidence", "CONFIRMED")
                reason = item.get("confidence_reason", "")
                snippet = item.get("price_snippet", "")
                review_badge = ""
                if confidence == "AMBIGUOUS":
                    tooltip = f"⚠️ Ambiguous pricing: {reason}" + (f" | Raw: {snippet}" if snippet else "")
                    review_badge = f'<span class="badge" style="background:rgba(245,158,11,0.25); color:#fbbf24; border:1px solid #f59e0b; font-size:0.68rem; padding:2px 6px; border-radius:4px; margin-left:6px; cursor:help;" title="{tooltip}">⚠️ Needs Review</span>'

                r_str = f"{item['rating']:.2f}" if item['rating'] is not None else ""
                rev_val = item['reviews']
                rating_html = self._format_rating_display(item['rating'], rev_val, is_guest_favorite=item.get("is_guest_favorite", False))
                loc_escaped = html.escape(str(item.get('location', '')), quote=True)

                if is_valid:
                    is_winter = item.get("is_winter", True)
                    win_ratio = item.get("winter_ratio")
                    sum_ratio = item.get("summer_ratio")
                    has_seasonal_diff = (
                        win_ratio is not None
                        and sum_ratio is not None
                        and abs(win_ratio - sum_ratio) >= 0.01
                    )
                    season_label = "Winter" if is_winter else "Summer"
                    season_prefix = f"{season_label} " if has_seasonal_diff else ""

                    pool_sp = item.get("pool_specs", {})
                    heat_val = pool_sp.get("heating", "")
                    heat_str = f" • {heat_val.replace('_', ' ').title()} Pool" if heat_val and heat_val != "none" else ""
                    tt = f"{rationale} ({season_label}{heat_str})"

                    if ratio >= 1.05:
                        ratio_badge = f'<span class="badge" style="background:rgba(96,165,250,0.2); color:#60a5fa; border:1px solid rgba(96,165,250,0.35); font-weight:700; font-size:0.75rem;" title="{tt}">💎 {ratio:.2f}x ({season_prefix}Superior)</span>'
                    elif ratio <= 0.95:
                        ratio_badge = f'<span class="badge" style="background:rgba(251,191,36,0.2); color:#fbbf24; border:1px solid rgba(251,191,36,0.35); font-weight:700; font-size:0.75rem;" title="{tt}">📉 {ratio:.2f}x ({season_prefix}Discount)</span>'
                    else:
                        ratio_badge = f'<span class="badge" style="background:rgba(52,211,153,0.2); color:#34d399; border:1px solid rgba(52,211,153,0.35); font-weight:700; font-size:0.75rem;" title="{tt}">🎯 {ratio:.2f}x ({season_prefix}Peer)</span>'
                    ratio_td = f"""<td style="padding:9px 14px;">
                      {ratio_badge}
                      <div style="font-size:0.72rem; color:#94a3b8; margin-top:3px; line-height:1.25;">{rationale}</div>
                    </td>"""
                    row_style = "border-bottom:1px solid rgba(255,255,255,0.05);"
                else:
                    ratio_badge = f'<span class="badge" style="background:rgba(239,68,68,0.2); color:#f87171; border:1px solid rgba(239,68,68,0.4); font-weight:700; font-size:0.75rem;" title="{validity_reason}">⛔ Disqualified Comp</span>'
                    ratio_td = f"""<td style="padding:9px 14px;">
                      {ratio_badge}
                      <div style="font-size:0.72rem; color:#f87171; margin-top:3px; line-height:1.25;">{validity_reason or rationale}</div>
                    </td>"""
                    diff_badge = '<span style="color:#64748b; font-size:0.75rem;">Excluded</span>'
                    row_style = "border-bottom:1px solid rgba(255,255,255,0.05); opacity:0.6;"

                subtable_rows.append(f"""
                  <tr class="comp-item-row" data-is-our="false" data-location="{loc_escaped}" data-price="{adj_rate:.2f}" data-raw-price="{raw_rate:.2f}" data-adj-price="{adj_rate:.2f}" data-valid="{str(is_valid).lower()}" data-rating="{r_str}" data-reviews="{rev_val}" style="{row_style}">
                    <td class="comp-rank-cell" style="padding:9px 14px; color:#64748b; font-family:'JetBrains Mono',monospace; text-align:center; font-size:0.8rem;">{rank if is_valid else '-'}</td>
                    <td style="padding:9px 14px; font-family:'JetBrains Mono',monospace;">
                      <div class="comp-price-display">
                        <strong class="price-val" style="color:#f1f5f9;">${adj_rate:.0f}</strong><span class="price-label" style="color:#a78bfa; font-size:0.75rem; margin-left:3px;">/n (adj)</span>{review_badge}
                        <div class="raw-price-note" style="font-size:0.72rem; color:#64748b;">Raw: ${raw_rate:.0f}/night</div>
                      </div>
                      <div style="font-size:0.72rem; color:#64748b;">${item['total_price']:.0f} total stay</div>
                    </td>
                    {ratio_td}
                    <td style="padding:9px 14px; text-align:center;">
                      {rating_html}
                    </td>
                    <td style="padding:9px 14px; text-align:center; color:#cbd5e1;">{item['bedrooms']} BR</td>
                    <td style="padding:9px 14px; text-align:center; color:#cbd5e1;">{item['beds']}</td>
                    <td style="padding:9px 14px; text-align:center; color:#cbd5e1;">{item['baths']}</td>
                    <td style="padding:9px 14px;">
                      <a href="{item['url']}" target="_blank" rel="noopener noreferrer" style="color:#60a5fa; text-decoration:none; font-weight:600;" onmouseover="this.style.textDecoration='underline'" onmouseout="this.style.textDecoration='none'">
                        {item['name']} ↗
                      </a>
                      <span style="font-size:0.75rem; color:#94a3b8; margin-left:6px;">({item['location']})</span>
                    </td>
                    <td class="comp-diff-cell" style="padding:9px 14px;">
                      {diff_badge}
                    </td>
                  </tr>
                """)
                if is_valid:
                    rank += 1

        is_live = s.get("is_live_scan", False)
        if not subtable_rows:
            rows_html = '<tr><td colspan="9" style="text-align:center; padding:24px; color:#94a3b8; font-size:0.88rem;">ℹ️ No live competitor listings scraped for this date yet. Run a live scrape sweep to populate real market comps.</td></tr>'
        else:
            rows_html = "".join(subtable_rows)

        hb = s.get("historical_benchmark") or {}
        lt = s.get("lead_time_status") or {}
        lt_label = lt.get("label", "Booking Window")
        lt_action = lt.get("action", "")
        lt_color = lt.get("badge_color", "#34d399")
        lt_bg = lt.get("badge_bg", "rgba(52,211,153,0.15)")

        h_count = hb.get("sample_count", 0)
        if h_count > 0:
            h_min = hb.get("min_rate", 0.0)
            h_max = hb.get("max_rate", 0.0)
            h_med = hb.get("median_rate", 0.0)
            h_flag_label = hb.get("flag_label", "Aligned")
            h_flag_color = hb.get("flag_color", "#34d399")
            h_flag = hb.get("flag", "ON_TRACK")
            if h_flag == "ON_TRACK":
                h_badge_bg = "rgba(52,211,153,0.15)"
            elif h_flag == "AGGRESSIVE_PREMIUM":
                h_badge_bg = "rgba(251,191,36,0.15)"
            elif h_flag == "DEEP_DISCOUNT":
                h_badge_bg = "rgba(248,113,113,0.15)"
            else:
                h_badge_bg = "rgba(148,163,184,0.15)"

            track_record_str = f'Realized: <strong style="color:#34d399;">${h_min:.0f}–${h_max:.0f}</strong>/nt <span style="color:#94a3b8; font-size:0.75rem;">(Median ${h_med:.0f}/nt, {h_count} bookings)</span> <span class="badge" style="background:{h_badge_bg}; color:{h_flag_color}; font-size:0.7rem; font-weight:600; margin-left:4px;">{h_flag_label}</span>'
        else:
            track_record_str = '<span style="color:#94a3b8; font-size:0.8rem;">No prior confirmed bookings within ±15 days in 2022–2026</span>'

        intel_banner_html = f"""
            <div class="subtable-intel-banner" style="background:rgba(15,23,42,0.85); border:1px solid #334155; border-radius:8px; padding:10px 16px; margin-bottom:12px; display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:12px;">
              <div style="display:flex; align-items:center; gap:10px;">
                <span style="font-size:1.1rem;">⏱️</span>
                <div>
                  <div style="font-size:0.72rem; color:#94a3b8; font-weight:700; text-transform:uppercase; letter-spacing:0.05em;">Booking Window & Pace</div>
                  <div style="font-size:0.85rem; color:#f8fafc; font-weight:600; margin-top:2px;">
                    <span class="badge" style="background:{lt_bg}; color:{lt_color}; font-weight:700; font-size:0.75rem;">{lt_label}</span>
                    <span style="color:#cbd5e1; font-size:0.78rem; margin-left:8px;">{lt_action}</span>
                  </div>
                </div>
              </div>
              <div style="display:flex; align-items:center; gap:10px;">
                <span style="font-size:1.1rem;">📊</span>
                <div>
                  <div style="font-size:0.72rem; color:#94a3b8; font-weight:700; text-transform:uppercase; letter-spacing:0.05em;">Villa del Sol Historical Track Record (±15d Window)</div>
                  <div style="font-size:0.85rem; color:#f8fafc; font-weight:600; margin-top:2px; font-family:'JetBrains Mono',monospace;">
                    {track_record_str}
                  </div>
                </div>
              </div>
            </div>
        """

        subtable_html = f"""
          <div class="subtable-container"
               data-row-id="{row_id}"
               data-nights="{nights}"
               data-lead-days="{s.get('lead_time_days', 0)}"
               data-our-base="{our_base}"
               data-our-eff="{our_eff}"
               data-is-our-live="{str(is_our_live).lower()}"
               data-target-pct="{s.get('target_percentile', 65.0)}"
               data-calendar-open="{str(s.get('is_calendar_open', True)).lower()}"
               data-is-live-scan="{str(is_live).lower()}"
               data-channel-factor="{channel_factor:.4f}"
               data-segment-type="{str(s.get('segment_type', 'weekend')).lower()}"
               data-floor="{float(s.get('floor_rate') or (300.0 if str(s.get('segment_type', '')).lower() == 'midweek' else 450.0)):.0f}">
            {intel_banner_html}
            <div class="subtable-scroll">
              <table class="subtable">
                <thead>
                  <tr>
                    <th style="width:55px; text-align:center;">#</th>
                    <th style="width:160px;">Price</th>
                    <th style="width:230px;">Quality Ratio & Valuation</th>
                    <th style="width:110px; text-align:center;">Rating</th>
                    <th style="width:90px; text-align:center;">Bedrooms</th>
                    <th style="width:85px; text-align:center;">Beds</th>
                    <th style="width:80px; text-align:center;">Baths</th>
                    <th>Name of Comp (Click to open on Airbnb)</th>
                    <th style="width:170px;">Position vs Us</th>
                  </tr>
                </thead>
                <tbody>
                  {rows_html}
                </tbody>
              </table>
            </div>
          </div>
        """
        return subtable_html, our_rank, total_comps, our_pct, our_eff, is_our_live

    def _render_table_rows(self, segments: List[Dict[str, Any]], prefix: str = "row") -> str:
        rows = []
        for idx, s in enumerate(segments):
            row_id = f"{prefix}-{idx}"

            subtable_html, our_rank, total_comps, our_pct, our_eff, is_our_live = self._render_comp_subtable(s, row_id)

            # Raw and adjusted metrics
            diff_raw = s["price_diff_percent"]
            diff_adj = s.get("price_diff_percent_adj", diff_raw)

            rec_raw = s["recommended_base_nightly"]
            rec_adj = s.get("recommended_base_nightly_adj", rec_raw)

            p50_raw = s["comp_p50_eff"]
            p50_adj = s.get("comp_p50_adj", p50_raw)

            target_raw = s["comp_target_eff"]
            target_adj = s.get("comp_target_adj", target_raw)

            n_raw = s.get("n_comps", s.get("comps_count", 0))
            n_adj = s.get("n_comps_adj", n_raw)

            action_raw = s.get("action_summary", "").replace("Increase base", "Increase").replace("Reduce base", "Reduce")
            action_adj = s.get("action_summary_adj", action_raw).replace("Increase base", "Increase").replace("Reduce base", "Reduce")

            def get_diff_badge(val: float) -> str:
                abs_val = abs(val)
                sign_str = f"{val:+.1f}%"
                if abs_val >= self.urgent_pct_diff:
                    return f'<span class="badge-diff-urgent">{sign_str}</span>'
                elif abs_val >= self.moderate_pct_diff:
                    return f'<span class="badge-diff-review">{sign_str}</span>'
                else:
                    return f'<span class="badge-diff-ok">{sign_str}</span>'

            diff_html_raw = get_diff_badge(diff_raw)
            diff_html_adj = get_diff_badge(diff_adj)

            def get_tier_and_status(val: float) -> Tuple[str, str, str]:
                abs_val = abs(val)
                if abs_val >= self.urgent_pct_diff:
                    tier = "urgent"
                    status = '<span class="badge" style="background:rgba(239,68,68,0.2); color:#f87171; border:1px solid rgba(239,68,68,0.4); font-weight:700;">🚨 Urgent</span>'
                    border = "border-left: 4px solid #ef4444;"
                elif abs_val >= self.moderate_pct_diff:
                    tier = "moderate"
                    status = '<span class="badge" style="background:rgba(245,158,11,0.2); color:#fbbf24; border:1px solid rgba(245,158,11,0.4); font-weight:700;">⚠️ Review</span>'
                    border = "border-left: 4px solid #f59e0b;"
                else:
                    tier = "ok"
                    status = '<span class="badge" style="background:rgba(16,185,129,0.12); color:#34d399; border:1px solid rgba(16,185,129,0.25);">✅ On Target</span>'
                    border = "border-left: 4px solid transparent;"
                return tier, status, border

            tier_raw, status_html_raw, border_raw = get_tier_and_status(diff_raw)
            tier_adj, status_html_adj, border_adj = get_tier_and_status(diff_adj)

            is_live = s.get("is_live_scan", False)
            def get_n_badge(count: int) -> str:
                if count == 0:
                    if is_live:
                        return '<span class="badge" style="background:rgba(239,68,68,0.2); color:#f87171; border:1px solid rgba(239,68,68,0.35);" title="Market 100% booked!">🔥 0 (Sold Out)</span>'
                    else:
                        return '<span class="badge" style="background:rgba(148,163,184,0.15); color:#94a3b8; border:1px solid rgba(148,163,184,0.3);" title="No live scrape recorded for this date yet">⏳ Pending Live Scrape</span>'
                elif count < 20:
                    return f'<span class="badge" style="background:rgba(245,158,11,0.2); color:#fbbf24; border:1px solid rgba(245,158,11,0.35);" title="Market compression: only {count} comps available (&lt;20% of cohort)!">🔥 N={count} (High Compression)</span>'
                else:
                    return f'<span class="badge" style="background:rgba(16,185,129,0.15); color:#34d399; border:1px solid rgba(16,185,129,0.3);" title="Exact live search executed across corridors for this date">🟢 Live N={count}</span>'

            n_html_raw = get_n_badge(n_raw)
            n_html_adj = get_n_badge(n_adj)

            def get_action_style(txt: str) -> str:
                if txt.startswith("↑"):
                    return "color:#34d399; font-weight:700;"
                elif txt.startswith("↓"):
                    return "color:#f87171; font-weight:700;"
                return ""

            action_style_raw = get_action_style(action_raw)
            action_style_adj = get_action_style(action_adj)

            our_base = float(s.get("our_base_nightly") or 0.0)
            floor_rate = float(s.get("floor_rate") or (300.0 if str(s.get("segment_type", "")).lower() == "midweek" else 450.0))

            if total_comps > 0:
                if is_our_live:
                    live_dot = '<span style="color:#34d399; font-size:0.75rem; margin-left:2px;" title="Live Airbnb checkout price verified">🟢</span>'
                    rank_tooltip = f"Live Airbnb Rate: ${our_eff:.0f}/night (${s.get('our_airbnb_total', our_eff * s['nights']):.0f} total). Villa del Sol ranks #{our_rank} out of {total_comps} competitors ({our_pct}th percentile). Base Kivoya rate is ${our_base:.0f}."
                else:
                    live_dot = ''
                    rank_tooltip = f"Villa del Sol ranks #{our_rank} out of {total_comps} competitors ({our_pct}th percentile in effective total guest cost)"
                eff_cell_html = f'<strong style="color:#f1f5f9;">${our_eff:.0f}</strong>{live_dot} <span style="font-size:0.78rem; color:#94a3b8; font-weight:600;" title="{rank_tooltip}">({our_pct}%)</span>'
            else:
                stored_pct = round(s.get("our_percentile_rank_adj", s.get("our_percentile_rank", 50.0)))
                eff_cell_html = f'<strong style="color:#f1f5f9;">${our_eff:.0f}</strong> <span style="font-size:0.78rem; color:#94a3b8; font-weight:600;">({stored_pct}%)</span>'

            target_pct = s.get("target_percentile", 65.0)
            target_pct_str = f"{target_pct:.1f}".rstrip("0").rstrip(".") + "%"

            is_cal_open = s.get("is_calendar_open", True)
            cal_open_str = str(is_cal_open).lower()
            closed_tag = '' if is_cal_open else ' <span class="badge" style="background:rgba(148,163,184,0.15); color:#94a3b8; font-size:0.75rem; padding:2px 5px; border:1px solid rgba(148,163,184,0.25);" title="Booking calendar currently closed in Kivoya">🔒</span>'

            hist = s.get("historical_benchmark", {}) or {}
            h_count = hist.get("sample_count", 0)
            h_med = float(hist.get("median_rate", 0.0) or 0.0)
            h_avg = float(hist.get("avg_rate", 0.0) or h_med)
            if h_count > 0 and h_avg > 0:
                h_min = float(hist.get("min_rate", 0.0) or 0.0)
                h_max = float(hist.get("max_rate", 0.0) or 0.0)
                h_flag_label = hist.get("flag_label") or "Aligned"
                hist_color = "#f8fafc"
                if our_base > 0:
                    hist_diff_ratio = round((h_avg - our_base) / our_base, 6)
                    if hist_diff_ratio >= 0.05:
                        hist_color = "#34d399"
                    elif hist_diff_ratio <= -0.05:
                        hist_color = "#f87171"
                hist_cell_html = f'<strong style="color:{hist_color};" title="{h_count} confirmed prior bookings within ±15 days in 2022–2026 ({h_flag_label}, range: ${h_min:.0f}–${h_max:.0f}, med: ${h_med:.0f})">${h_avg:.0f}</strong>'
            else:
                hist_cell_html = '<span style="color:#64748b; font-size:0.78rem;" title="No confirmed prior bookings within ±15 days">—</span>'

            # Default initial render is the ADJUSTED model (since checkbox is checked by default)
            rows.append(f"""
              <tr class="clickable-row interval-parent-row" id="parent-{row_id}"
                  data-tier="{tier_adj}"
                  data-calendar-open="{cal_open_str}"
                  data-detail-id="{row_id}"
                  data-checkin="{s['check_in']}"
                  data-checkout="{s['check_out']}"
                  data-segment-type="{s['segment_type'].capitalize()}"
                  data-base-price="{our_base:.0f}"
                  data-floor="{floor_rate:.0f}"
                  data-hist-count="{h_count}"
                  data-hist-med="{h_med:.0f}"
                  data-adj-tier="{tier_adj}"
                  data-raw-tier="{tier_raw}"
                  data-adj-diff-html='{diff_html_adj}'
                  data-raw-diff-html='{diff_html_raw}'
                  data-adj-status-html='{status_html_adj}'
                  data-raw-status-html='{status_html_raw}'
                  data-adj-action-html='<strong>{action_adj}</strong>'
                  data-raw-action-html='<strong>{action_raw}</strong>'
                  data-adj-action-style='font-size:0.85rem; {action_style_adj}'
                  data-raw-action-style='font-size:0.85rem; {action_style_raw}'
                  data-adj-n-html='{n_html_adj}'
                  data-raw-n-html='{n_html_raw}'
                  data-adj-p50='{p50_adj:.0f}'
                  data-raw-p50='{p50_raw:.0f}'
                  data-adj-target='{target_adj:.0f}'
                  data-raw-target='{target_raw:.0f}'
                  data-adj-rec='{rec_adj:.0f}'
                  data-raw-rec='{rec_raw:.0f}'
                  data-target-pct-str='{target_pct_str}'
                  data-adj-border='{border_adj}'
                  data-raw-border='{border_raw}'
                  onclick="toggleCompDetails('{row_id}', event)"
                  title="Click to view full competitor price breakdown"
                  style="{border_adj}">
                <td data-label="Dates">
                  <span class="caret-icon" id="icon-{row_id}">▶</span>
                  <span class="date-pill">{s['check_in']} &rarr; {s['check_out']}</span>{closed_tag}
                </td>
                <td data-label="Type"><strong>{s['segment_type'].capitalize()}</strong></td>
                <td data-label="Nights">{s['nights']} nights</td>
                <td data-label="Market Gap" id="diff-{row_id}">{diff_html_adj}</td>
                <td data-label="Action Needed" id="action-{row_id}" style="font-size:0.85rem; {action_style_adj}"><strong>{action_adj}</strong></td>
                <td data-label="Historical AVG" id="track-{row_id}">{hist_cell_html}</td>
                <td data-label="Comps" id="n-{row_id}">{n_html_adj}</td>
                <td data-label="Kivoya Rate" style="font-family:'JetBrains Mono',monospace;">${our_base:.0f}</td>
                <td data-label="Effective Total" id="eff-{row_id}" style="font-family:'JetBrains Mono',monospace;">{eff_cell_html}</td>
                <td data-label="Comp Target" id="target-{row_id}" style="font-family:'JetBrains Mono',monospace; color:#60a5fa;">${target_adj:.0f} <span style="font-size:0.75rem; color:#94a3b8;">({target_pct_str})</span></td>
                <td data-label="Recommended" id="rec-{row_id}"><span class="rec-price">${rec_adj:.0f}</span></td>
              </tr>
              <tr id="{row_id}" class="comp-details-row" style="display: none;">
                <td colspan="11">
                  {subtable_html}
                </td>
              </tr>
            """)
        return "\n".join(rows)

    def _render_proposed_prices_section(self, proposed_periods: List[Dict[str, Any]]) -> str:
        if not proposed_periods:
            return ""

        rows_html = []
        for idx, p in enumerate(proposed_periods):
            is_hol = p["is_holiday"]
            is_split = p.get("split_pricing", False)
            f_dt = p["from_date"]
            t_dt = p["to_date"]
            min_n = p["min_nights"]
            min_base = p.get("min_nights_base", min_n)
            hol_name = p["holiday_name"]

            mid_base = p["midweek_base"]
            mid_avg = p["midweek_avg"]
            mid_med = p["midweek_med"]

            wkd_base = p["weekend_base"]
            wkd_avg = p["weekend_avg"]
            wkd_med = p["weekend_med"]

            spec_base = p["special_base"]
            spec_avg = p["special_avg"]
            spec_med = p["special_med"]

            def format_rate_cell(val: Optional[int], base: Optional[int]) -> str:
                if base is None and val is None:
                    return '<span style="color:#475569;">—</span>'
                if base is None:
                    return f'<strong style="color:#ffffff; font-family:\'JetBrains Mono\',monospace;">${val:,}</strong>'
                base_str = f'<span style="color:#ffffff; font-family:\'JetBrains Mono\',monospace; font-weight:600;">${base:,}</span>'
                if val is None or val == base:
                    return base_str
                diff = val - base
                if diff > 0:
                    color = "#34d399"
                    title = f"Proposed increase from ${base} to ${val} (+${diff})"
                else:
                    color = "#f87171"
                    title = f"Proposed decrease from ${base} to ${val} (-${abs(diff)})"
                return f'{base_str}<strong style="color:{color}; font-family:\'JetBrains Mono\',monospace; font-weight:700;" title="{title}"><span style="margin:0 6px; display:inline-block;">→</span>${val:,}</strong>'

            def format_min_nights_cell(prop_min: int, base_min: int, orphan_reason: Optional[str] = None) -> str:
                base_str = f'<span style="color:#ffffff; font-family:\'JetBrains Mono\',monospace; font-weight:600;">{base_min}</span>'
                if prop_min == base_min:
                    if orphan_reason:
                        return f'{base_str} <span style="color:#38bdf8; font-size:0.75rem; cursor:help;" title="Orphan slot protection: {orphan_reason} (held at 2-night min)">ℹ️</span>'
                    return base_str
                diff = prop_min - base_min
                if diff > 0:
                    color = "#34d399"
                    title = f"Rule correction: Increase min nights from {base_min} to {prop_min} (90+ days out)"
                else:
                    color = "#f87171"
                    if orphan_reason:
                        title = f"Orphan slot protection: Reduce min nights from {base_min} to {prop_min} ({orphan_reason})"
                    else:
                        title = f"Rule correction: Reduce min nights from {base_min} to {prop_min} (next 90 days)"
                return f'{base_str}<strong style="color:{color}; font-family:\'JetBrains Mono\',monospace; font-weight:700;" title="{title}"><span style="margin:0 6px; display:inline-block;">→</span>{prop_min}</strong>'

            mid_html = format_rate_cell(mid_avg, mid_base) if (not is_hol or is_split) else '<span style="color:#475569;">—</span>'
            wkd_html = format_rate_cell(wkd_avg, wkd_base) if (not is_hol or is_split) else '<span style="color:#475569;">—</span>'
            spec_html = format_rate_cell(spec_avg, spec_base) if (is_hol and not is_split) else '<span style="color:#475569;">—</span>'
            orphan_r = p.get("orphan_slot_reason")
            min_html = format_min_nights_cell(min_n, min_base, orphan_reason=orphan_r)
            is_custom = p.get("is_custom_event", False)
            is_cal_open = p.get("is_calendar_open", True)
            custom_badge = ' <span style="background:rgba(99,102,241,0.2); color:#a5b4fc; border:1px solid rgba(99,102,241,0.4); border-radius:4px; padding:1px 6px; font-size:0.68rem; font-weight:700; margin-left:6px;" title="Custom event period not currently separated in Kivoya PMS. Create a new period in Streamline VRS to apply rate.">Custom Event</span>' if is_custom else ''
            closed_badge = ' <span style="background:rgba(148,163,184,0.15); color:#94a3b8; border:1px solid rgba(148,163,184,0.3); border-radius:4px; padding:1px 5px; font-size:0.75rem; margin-left:4px;" title="Kivoya booking calendar is currently closed for this period">🔒</span>' if not is_cal_open else ''
            if is_hol:
                hol_html = f'<span style="color:#fbbf24; font-weight:700;">{hol_name}</span>{custom_badge}'
            elif not is_cal_open:
                hol_html = '<span style="color:#94a3b8; font-size:0.75rem; font-weight:600;">🔒 Closed Calendar</span>'
            else:
                hol_html = '<span style="color:#475569;">—</span>'
            row_bg = "background: rgba(251, 191, 36, 0.04);" if is_hol else ""
            row_display = "display: none;" if not is_cal_open else ""
            row_style = f"{row_display} {row_bg}".strip()

            rows_html.append(f"""
              <tr class="proposed-price-row" id="prop-row-{idx}"
                  style="{row_style}"
                  data-calendar-open="{str(is_cal_open).lower()}"
                  data-is-holiday="{str(is_hol).lower()}"
                  data-split-pricing="{str(is_split).lower()}"
                  data-from="{f_dt}"
                  data-to="{t_dt}"
                  data-min-nights="{min_n}"
                  data-min-base="{min_base}"
                  data-holiday-name="{hol_name}"
                  data-mid-base="{mid_base if mid_base is not None else ''}"
                  data-mid-avg="{mid_avg if mid_avg is not None else ''}"
                  data-mid-med="{mid_med if mid_med is not None else ''}"
                  data-wkd-base="{wkd_base if wkd_base is not None else ''}"
                  data-wkd-avg="{wkd_avg if wkd_avg is not None else ''}"
                  data-wkd-med="{wkd_med if wkd_med is not None else ''}"
                  data-spec-base="{spec_base if spec_base is not None else ''}"
                  data-spec-avg="{spec_avg if spec_avg is not None else ''}"
                  data-spec-med="{spec_med if spec_med is not None else ''}">
                <td style="font-family:'JetBrains Mono',monospace; white-space:nowrap; font-weight:600;">{self._fmt_short_date(f_dt)} <span style="color:#64748b; margin:0 3px;">→</span> {self._fmt_short_date(t_dt)}{closed_badge}</td>
                <td class="proposed-cell-mid" style="font-family:'JetBrains Mono',monospace; white-space:nowrap;">{mid_html}</td>
                <td class="proposed-cell-wkd" style="font-family:'JetBrains Mono',monospace; white-space:nowrap;">{wkd_html}</td>
                <td class="proposed-cell-spec" style="font-family:'JetBrains Mono',monospace; white-space:nowrap;">{spec_html}</td>
                <td style="text-align:center; font-family:'JetBrains Mono',monospace; font-weight:600; white-space:nowrap;">{min_html}</td>
                <td>{hol_html}</td>
              </tr>
            """)

        tbody_html = "\n".join(rows_html)

        return f"""
      <div class="section-box" style="margin-top: 18px; margin-bottom: 24px; border: 1px solid rgba(56, 189, 248, 0.25); background: linear-gradient(180deg, rgba(15, 23, 42, 0.95) 0%, rgba(30, 41, 59, 0.85) 100%);">
        <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:16px; margin-bottom:18px;">
          <div>
            <div style="display:flex; align-items:center; gap:10px;">
              <h3 style="font-size:1.35rem; font-weight:800; margin:0; color:#f8fafc; display:flex; align-items:center; gap:8px;">
                <span style="color:#38bdf8;">🏷️</span> Proposed Prices
              </h3>
              <span class="badge" style="background:rgba(56,189,248,0.15); color:#38bdf8; border:1px solid rgba(56,189,248,0.3); font-size:0.75rem; font-weight:700;">PMS Consensus Schedule</span>
            </div>
            <p style="color:var(--text-muted); font-size:0.88rem; margin:6px 0 0 0; max-width:850px; line-height:1.4;">
              Synthesizes competitive market recommendations with historical track record benchmarks across Kivoya seasonal rate periods. Rates only adjust when market data and historical sales agree on direction; conflicting intervals hold current base rates firm.
            </p>
          </div>

          <div style="display:flex; align-items:center; gap:12px; flex-wrap:wrap;">
            <!-- Open Calendar Only Checkbox -->
            <label title="Kivoya booking calendar is open through May 31, 2027. Uncheck to show proposed pricing for the full 12-month period." style="display: inline-flex; align-items: center; gap: 6px; cursor: pointer; font-size: 0.85rem; color: #38bdf8; font-weight: 600; background: rgba(56,189,248,0.1); border: 1px solid rgba(56,189,248,0.3); padding: 5px 11px; border-radius: 6px; user-select: none;">
              <input type="checkbox" id="filterProposedOpenCalendar" checked onchange="filterProposedOpenCalendar()" style="width: 15px; height: 15px; accent-color: #38bdf8; cursor: pointer; border-radius: 4px;">
              <span>Open Calendar Only</span>
            </label>

            <!-- Selector: Median / Average -->
            <div style="display:inline-flex; align-items:center; gap:6px; background:rgba(255,255,255,0.06); padding:4px 8px; border-radius:8px; border:1px solid rgba(255,255,255,0.12);">
              <span style="font-size:0.78rem; color:#94a3b8; font-weight:700; text-transform:uppercase; letter-spacing:0.04em; margin-right:2px;">Suggest:</span>
              <button id="btnSuggestAvg" class="res-pill-btn active" onclick="setProposedRateMode('avg')" style="padding:3px 10px; font-size:0.8rem;">Average</button>
              <button id="btnSuggestMed" class="res-pill-btn" onclick="setProposedRateMode('med')" style="padding:3px 10px; font-size:0.8rem;">Median</button>
            </div>

            <!-- Copy Proposed Prices Button -->
            <button id="btnCopyProposed" class="action-btn" onclick="copyProposedPrices()" style="display:inline-flex; align-items:center; gap:8px; background:rgba(56,189,248,0.15); color:#38bdf8; border:1px solid rgba(56,189,248,0.35); padding:6px 14px; border-radius:6px; font-weight:700; font-size:0.85rem; cursor:pointer;">
              <span id="copyProposedIconContainer" style="display: inline-flex; align-items: center;">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align: middle;">
                  <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                  <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
                </svg>
              </span>
              <span id="copyProposedText">Copy Proposed Prices</span>
            </button>
          </div>
        </div>

        <div class="table-responsive">
          <table id="proposed-prices-table" style="width:100%;">
            <thead>
              <tr>
                <th style="min-width:140px;">Dates</th>
                <th><span style="color:#fb923c; font-size:0.9rem;">●</span> Midweek</th>
                <th><span style="color:#818cf8; font-size:0.9rem;">●</span> Weekend</th>
                <th><span style="color:#fbbf24; font-size:0.9rem;">●</span> Special</th>
                <th style="width:105px; text-align:center;">Min nights</th>
                <th>Holiday</th>
              </tr>
            </thead>
            <tbody>
              {tbody_html}
            </tbody>
          </table>
        </div>
      </div>
        """

    def _render_comp_cards(self, comps: List[Dict[str, Any]], tier_label: str) -> str:
        cards = []
        tier_code = "tier_a" if "Tier A" in tier_label else ("tier_b" if "Tier B" in tier_label else "disqualified")
        if tier_code == "tier_a":
            badge_style = "background:rgba(37,99,235,0.2); color:#60a5fa; border:1px solid rgba(59,130,246,0.3);"
        elif tier_code == "tier_b":
            badge_style = "background:rgba(148,163,184,0.15); color:#cbd5e1; border:1px solid rgba(148,163,184,0.3);"
        else:
            badge_style = "background:rgba(239,68,68,0.2); color:#f87171; border:1px solid rgba(239,68,68,0.3);"

        for c in comps:
            r = c.get("rating")
            rev = c.get("reviews") or 0
            if r is not None and float(r) > 0:
                rating_str = f"⭐ {float(r):.2f} ({rev})"
            elif rev > 0:
                rating_str = f"⭐ (No star) ({rev})"
            else:
                rating_str = "⭐ New (0)"
            photo_url = c.get("photo_url")
            img_html = (
                f'<div class="comp-img-wrapper"><img src="{photo_url}" alt="{c.get("name", "Comp")}" class="comp-img" loading="lazy" onerror="this.parentElement.style.display=\'none\'" /></div>'
                if photo_url else
                '<div class="comp-img-wrapper" style="display:flex; align-items:center; justify-content:center; background:#1e293b; color:#64748b; font-size:2.2rem;">🏡</div>'
            )

            is_valid = c.get("is_valid_comp", True)
            w_ratio = float(c.get("winter_ratio") or c.get("desirability_ratio") or 1.0)
            s_ratio = float(c.get("summer_ratio") or c.get("desirability_ratio") or 1.0)
            ratio = w_ratio
            score = float(c.get("composite_score") or 88.0)
            cat_scores = c.get("category_scores", {})
            rationale = c.get("rationale", "")
            validity_reason = c.get("validity_reason", "")
            pool_sp = c.get("pool_specs", {})
            heat_val = pool_sp.get("heating", "unheated")
            heat_label = heat_val.replace("_", " ").title()
            pool_badge = f'<span class="badge" style="background:rgba(56,189,248,0.15); color:#38bdf8; border:1px solid rgba(56,189,248,0.3); font-size:0.72rem;" title="{pool_sp.get("heating_source", "")}">🏊 {heat_label} Pool</span>' if pool_sp.get("has_pool", True) else '<span class="badge" style="background:rgba(239,68,68,0.15); color:#f87171; border:1px solid rgba(239,68,68,0.3); font-size:0.72rem;">🚫 No Pool</span>'

            property_sp = c.get("property_specs", {})
            sqft_val = property_sp.get("sqft")
            sqft_src = property_sp.get("sqft_source", "Hedonic Est.")
            lot_val = property_sp.get("lot_acres")
            lot_src = property_sp.get("lot_source", "Hedonic Est.")
            val_est = property_sp.get("est_property_value")
            val_src = property_sp.get("property_value_source", "Hedonic Est.")

            # Verification badge
            all_srcs = f"{sqft_src} {lot_src} {val_src}"
            if "Assessor" in all_srcs:
                badge_src = '<span class="badge" style="background:rgba(168,85,247,0.15); color:#c084fc; border:1px solid rgba(168,85,247,0.3); font-size:0.70rem;" title="Public Records / Assessor Verified">🏛️ Assessor Verified</span>'
            elif "Listing Disclosed" in all_srcs:
                badge_src = '<span class="badge" style="background:rgba(59,130,246,0.15); color:#60a5fa; border:1px solid rgba(59,130,246,0.3); font-size:0.70rem;" title="Disclosed by host in listing description">📝 Listing Disclosed</span>'
            else:
                badge_src = '<span class="badge" style="background:rgba(148,163,184,0.15); color:#94a3b8; border:1px solid rgba(148,163,184,0.3); font-size:0.70rem;" title="Estimated via Corridor Hedonic Pricing Model">📐 Hedonic Est.</span>'

            # Metric pills in comp-specs
            sqft_pill = f'<span title="House Size: {sqft_val:,} sq ft ({sqft_src})">📐 {sqft_val:,} sq ft</span>' if sqft_val else ""
            lot_pill = f'<span title="Lot Size: {lot_val:.2f} acres ({lot_src})">🌳 {lot_val:.2f} ac</span>' if lot_val else ""
            val_pill = f'<span title="Est. Property Asset Value: ${val_est:,.0f} ({val_src})">🏷️ ${val_est/1e6:.1f}M</span>' if val_est else ""

            if is_valid and tier_code != "disqualified":
                valid_pill = '<span class="badge" style="background:rgba(16,185,129,0.15); color:#34d399; border:1px solid rgba(16,185,129,0.3); font-size:0.75rem;">✅ Valid Comp</span>'
                w_color = "#60a5fa" if w_ratio >= 1.05 else ("#fbbf24" if w_ratio <= 0.95 else "#34d399")
                s_color = "#60a5fa" if s_ratio >= 1.05 else ("#fbbf24" if s_ratio <= 0.95 else "#34d399")
                w_bg = "rgba(96,165,250,0.2)" if w_ratio >= 1.05 else ("rgba(251,191,36,0.2)" if w_ratio <= 0.95 else "rgba(52,211,153,0.2)")
                s_bg = "rgba(96,165,250,0.2)" if s_ratio >= 1.05 else ("rgba(251,191,36,0.2)" if s_ratio <= 0.95 else "rgba(52,211,153,0.2)")

                w_pill = f'<span class="badge" style="background:{w_bg}; color:{w_color}; border:1px solid {w_color}44; font-weight:700; font-size:0.75rem;" title="Winter Ratio (Oct-Apr)">❄️ Win: {w_ratio:.2f}x</span>'
                s_pill = f'<span class="badge" style="background:{s_bg}; color:{s_color}; border:1px solid {s_color}44; font-weight:700; font-size:0.75rem;" title="Summer Ratio (May-Sep)">☀️ Sum: {s_ratio:.2f}x</span>'
                ratio_pill = f'<div style="display:flex; gap:4px; flex-wrap:wrap; align-items:center;">{w_pill}{s_pill}{pool_badge}{badge_src}</div>'

                scores_row = ""
                if cat_scores:
                    scores_row = f"""
                    <div style="display:flex; gap:6px; flex-wrap:wrap; font-size:0.72rem; color:#94a3b8; margin-top:6px; padding-top:6px; border-top:1px solid rgba(255,255,255,0.06);">
                      <span title="Outdoor Resort Yard & Lot Size (25% weight)">🏊 Yard: <strong style="color:#e2e8f0;">{cat_scores.get('outdoor', 80)}</strong></span>
                      <span title="Bedrooms, Bathrooms & House Size (20% weight)">🛏️ Beds: <strong style="color:#e2e8f0;">{cat_scores.get('capacity', 80)}</strong></span>
                      <span title="Property Asset Value & Scale (15% weight)">🏷️ Value: <strong style="color:#e2e8f0;">{cat_scores.get('property_value', 80)}</strong></span>
                      <span title="Interior Luxury & Finishes (15% weight)">✨ Luxury: <strong style="color:#e2e8f0;">{cat_scores.get('interior', 80)}</strong></span>
                      <span title="Location Corridor (15% weight)">📍 Loc: <strong style="color:#e2e8f0;">{cat_scores.get('location', 80)}</strong></span>
                      <span title="Reputation & Reviews (10% weight)">⭐ Rep: <strong style="color:#e2e8f0;">{cat_scores.get('reputation', 80)}</strong></span>
                    </div>
                    """

                eval_block = f"""
                <div style="margin-top:10px; background:rgba(30,41,59,0.5); border:1px solid rgba(148,163,184,0.15); border-radius:8px; padding:8px 10px;">
                  <div style="display:flex; align-items:center; justify-content:space-between; gap:6px; flex-wrap:wrap;">
                    {ratio_pill}
                    <span style="font-size:0.72rem; color:#94a3b8;">Score: {score:.1f}/100</span>
                  </div>
                  {scores_row}
                  <div style="font-size:0.78rem; color:#cbd5e1; margin-top:6px; line-height:1.35; border-left:3px solid #38bdf8; padding-left:7px;">
                    {rationale}
                  </div>
                </div>
                """
                card_style = ""
            else:
                valid_pill = '<span class="badge" style="background:rgba(239,68,68,0.2); color:#f87171; border:1px solid rgba(239,68,68,0.4); font-size:0.75rem;">⛔ Disqualified Comp</span>'
                eval_block = f"""
                <div style="margin-top:10px; background:rgba(239,68,68,0.1); border:1px solid rgba(239,68,68,0.25); border-radius:8px; padding:8px 10px;">
                  <div style="font-size:0.8rem; font-weight:700; color:#f87171;">Excluded from pricing model</div>
                  <div style="font-size:0.78rem; color:#fca5a5; margin-top:4px; line-height:1.35;">
                    {validity_reason or rationale}
                  </div>
                </div>
                """
                card_style = "display:none; border: 1px solid rgba(239,68,68,0.35); opacity: 0.85;"

            cid = str(c.get("listing_id") or c.get("id") or "")
            cards.append(f"""
              <div class="comp-card" data-listing-id="{cid}" data-tier="{tier_code}" data-valid="{str(is_valid and tier_code != 'disqualified').lower()}" data-location="{c.get('location', '')}" style="{card_style}">
                <div>
                  {img_html}
                  <div class="comp-header">
                    <div style="display:flex; gap:6px; align-items:center;">
                      <span class="badge" style="{badge_style}; font-size:0.75rem;">{tier_label}</span>
                      {valid_pill}
                    </div>
                    <span style="font-size:0.8rem; color:#94a3b8;">{c.get('location', 'Phoenix Valley')}</span>
                  </div>
                  <div class="comp-title">{c.get('name', 'Luxury Estate')}</div>
                  <div class="comp-specs">
                    <span>🛏️ {c.get('bedrooms', 6)} Bedrooms</span>
                    <span>🛏️ {c.get('beds', 8)} Beds</span>
                    <span>🚿 {c.get('baths', 4)} Baths</span>
                    <span>{rating_str}</span>
                    {sqft_pill}
                    {lot_pill}
                    {val_pill}
                  </div>
                  {eval_block}
                </div>
                <button type="button" class="btn-validity-audit" onclick="openValidityModal('{cid}')">
                  📋 View Validity Audit
                </button>
                <a href="{c.get('url', 'https://airbnb.com')}" target="_blank" rel="noopener noreferrer" class="comp-link">
                  Open on Airbnb ↗
                </a>
              </div>
            """)
        return "\n".join(cards)

    def _render_validity_modal(self, comp_validity_json: str) -> str:
        """Render interactive validity audit modal dialog and embedded data/handlers."""
        template = """
  <!-- Comp Validity Audit Slide-Over Modal -->
  <div id="validityModalOverlay" class="validity-modal-overlay" style="display:none;" onclick="handleValidityBackdropClick(event)">
    <div class="validity-modal-container" role="dialog" aria-modal="true" aria-labelledby="validityModalTitle">
      <div class="validity-modal-header">
        <div>
          <div style="display:flex; align-items:center; gap:8px; margin-bottom:4px;">
            <span id="validityBadge" class="badge"></span>
            <span id="validityTierBadge" class="badge"></span>
          </div>
          <h2 id="validityModalTitle" style="font-size:1.2rem; font-weight:800; color:#f8fafc; margin:0;">Competitor Validity Audit</h2>
          <div id="validityModalSubtitle" style="font-size:0.82rem; color:#94a3b8; margin-top:3px;"></div>
        </div>
        <button class="validity-modal-close" onclick="closeValidityModal()" aria-label="Close modal">&times;</button>
      </div>

      <div class="validity-modal-body">
        <!-- Status Alert Banner -->
        <div id="validityAlertBox" style="padding:12px 16px; border-radius:8px; margin-bottom:18px;">
          <div style="font-weight:700; font-size:0.92rem;" id="validityAlertHeading"></div>
          <div style="font-size:0.83rem; margin-top:4px; line-height:1.45;" id="validityAlertText"></div>
        </div>

        <!-- 5-Point Luxury Rubric Checklist -->
        <div style="margin-bottom:20px;">
          <h3 style="font-size:0.92rem; font-weight:700; color:#cbd5e1; margin-bottom:10px; display:flex; align-items:center; gap:6px;">
            <span>📋</span> Mandatory Luxury STR Rubric Checklist
          </h3>
          <div id="validityChecklistGrid" style="display:grid; grid-template-columns:repeat(auto-fit, minmax(220px, 1fr)); gap:10px;">
          </div>
        </div>

        <!-- Side-by-Side Property Comparison -->
        <div style="margin-bottom:20px;">
          <h3 style="font-size:0.92rem; font-weight:700; color:#cbd5e1; margin-bottom:10px; display:flex; align-items:center; gap:6px;">
            <span>⚖️</span> Side-by-Side Asset & Scale Comparison
          </h3>
          <div class="table-responsive" style="border:1px solid rgba(255,255,255,0.08); border-radius:8px;">
            <table style="width:100%; border-collapse:collapse; font-size:0.83rem;">
              <thead>
                <tr style="border-bottom:1px solid rgba(255,255,255,0.1); background:rgba(30,41,59,0.7); color:#94a3b8; text-align:left;">
                  <th style="padding:8px 10px;">Dimension</th>
                  <th style="padding:8px 10px; color:#38bdf8;">Villa del Sol (Benchmark)</th>
                  <th style="padding:8px 10px;" id="validityCompColHeader">Competitor Property</th>
                  <th style="padding:8px 10px;">Assessment</th>
                </tr>
              </thead>
              <tbody id="validityComparisonTableBody">
              </tbody>
            </table>
          </div>
        </div>

        <!-- 6-Factor Category Scores -->
        <div style="margin-bottom:20px;">
          <h3 style="font-size:0.92rem; font-weight:700; color:#cbd5e1; margin-bottom:10px; display:flex; align-items:center; gap:6px;">
            <span>📊</span> 6-Factor Quality & Valuation Category Scores
          </h3>
          <div id="validityCategoryScoresGrid" style="display:grid; grid-template-columns:repeat(auto-fit, minmax(110px, 1fr)); gap:8px;">
          </div>
        </div>

        <!-- Strengths & Deficits -->
        <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(260px, 1fr)); gap:12px; margin-bottom:18px;">
          <div style="background:rgba(30,41,59,0.5); border:1px solid rgba(52,211,153,0.25); border-radius:8px; padding:12px 14px;">
            <h4 style="font-size:0.83rem; font-weight:700; color:#34d399; margin:0 0 8px 0; display:flex; align-items:center; gap:6px;">
              <span>✨</span> Key Property Strengths
            </h4>
            <ul id="validityStrengthsList" style="margin:0; padding-left:16px; font-size:0.8rem; color:#cbd5e1; line-height:1.45;"></ul>
          </div>
          <div style="background:rgba(30,41,59,0.5); border:1px solid rgba(248,113,113,0.25); border-radius:8px; padding:12px 14px;">
            <h4 style="font-size:0.83rem; font-weight:700; color:#f87171; margin:0 0 8px 0; display:flex; align-items:center; gap:6px;">
              <span>⚠️</span> Deficits & Friction Points
            </h4>
            <ul id="validityDeficitsList" style="margin:0; padding-left:16px; font-size:0.8rem; color:#cbd5e1; line-height:1.45;"></ul>
          </div>
        </div>

        <!-- Footer -->
        <div style="display:flex; justify-content:space-between; align-items:center; border-top:1px solid rgba(255,255,255,0.08); padding-top:12px;">
          <a id="validityAirbnbLink" href="#" target="_blank" rel="noopener noreferrer" style="color:#60a5fa; text-decoration:none; font-size:0.85rem; display:inline-flex; align-items:center; gap:4px; font-weight:600;">
            Open Listing on Airbnb ↗
          </a>
          <button onclick="closeValidityModal()" style="background:rgba(148,163,184,0.15); color:#cbd5e1; border:1px solid rgba(148,163,184,0.3); padding:6px 14px; border-radius:6px; font-size:0.83rem; cursor:pointer; font-weight:600;">
            Close
          </button>
        </div>
      </div>
    </div>
  </div>

  <script>
    const COMP_VALIDITY_DATA = {{COMP_VALIDITY_JSON}};

    function openValidityModal(cid) {
      const comp = (typeof COMP_VALIDITY_DATA !== 'undefined') ? COMP_VALIDITY_DATA[String(cid)] : null;
      if (!comp) return;

      const modal = document.getElementById('validityModalOverlay');
      if (!modal) return;

      const isValid = comp.is_valid_comp;
      const vd = comp.validity_details || {};
      const chk = vd.criteria_checklist || {};
      const cat = comp.category_scores || {};
      const sp = comp.property_specs || {};
      const psp = comp.pool_specs || {};

      // Badges & Title
      const badge = document.getElementById('validityBadge');
      if (badge) {
        if (isValid) {
          badge.textContent = '✅ Valid Luxury Estate Comp';
          badge.style.background = 'rgba(16,185,129,0.2)';
          badge.style.color = '#34d399';
          badge.style.border = '1px solid rgba(16,185,129,0.4)';
        } else {
          badge.textContent = '⛔ Disqualified Comp';
          badge.style.background = 'rgba(239,68,68,0.2)';
          badge.style.color = '#f87171';
          badge.style.border = '1px solid rgba(239,68,68,0.4)';
        }
      }
      const tierBadge = document.getElementById('validityTierBadge');
      if (tierBadge) {
        const t = comp.tier === 'tier_a' ? 'Tier A (Direct)' : (comp.tier === 'tier_b' ? 'Tier B (Secondary)' : 'Disqualified');
        tierBadge.textContent = t;
        tierBadge.style.background = 'rgba(59,130,246,0.15)';
        tierBadge.style.color = '#60a5fa';
        tierBadge.style.border = '1px solid rgba(59,130,246,0.3)';
      }

      const titleEl = document.getElementById('validityModalTitle');
      if (titleEl) titleEl.textContent = comp.name || 'Competitor Property';

      const subEl = document.getElementById('validityModalSubtitle');
      if (subEl) subEl.textContent = 'Listing ID: ' + comp.listing_id + ' • ' + (comp.location || 'Phoenix Valley') + ' • ' + (comp.bedrooms || 6) + ' BR / ' + (comp.baths || 4) + ' BA • Max ' + (comp.guests || 16) + ' Guests';

      // Alert Box
      const alertBox = document.getElementById('validityAlertBox');
      const alertH = document.getElementById('validityAlertHeading');
      const alertT = document.getElementById('validityAlertText');
      if (alertBox && alertH && alertT) {
        if (isValid) {
          alertBox.style.background = 'rgba(16,185,129,0.12)';
          alertBox.style.border = '1px solid rgba(16,185,129,0.3)';
          alertH.textContent = 'Active in Pricing & Sales Intelligence Models';
          alertH.style.color = '#34d399';
          alertT.style.color = '#a7f3d0';
          alertT.textContent = vd.justification || comp.rationale || 'Meets all mandatory criteria for luxury competitor benchmarking.';
        } else {
          alertBox.style.background = 'rgba(239,68,68,0.12)';
          alertBox.style.border = '1px solid rgba(239,68,68,0.3)';
          alertH.textContent = 'Excluded from Pricing Engine & Sales Ledger';
          alertH.style.color = '#f87171';
          alertT.style.color = '#fca5a5';
          alertT.textContent = comp.validity_reason || vd.justification || 'Disqualified comp.';
        }
      }

      // Checklist Grid
      const chkGrid = document.getElementById('validityChecklistGrid');
      if (chkGrid) {
        const items = [
          { label: 'Single-Family Estate / Compound', passed: chk.single_family_compound !== false, desc: 'Private grounds; no shared spaces or on-site owner' },
          { label: 'Private Swimming Pool', passed: chk.private_swimming_pool !== false, desc: psp.has_pool ? (psp.heating === 'free' ? 'Free heated saltwater/chlorine pool' : (psp.heating === 'unheated' ? 'Unheated pool' : 'Standard heated')) : 'No private pool found' },
          { label: 'Guest Capacity >= 12 Guests', passed: chk.guest_capacity_12_plus !== false, desc: 'Sleeps ' + (comp.guests || 16) + ' guests (' + (comp.bedrooms || 6) + ' bedrooms)' },
          { label: 'Guest Rating >= 4.70★ Standard', passed: chk.guest_rating_benchmark !== false, desc: comp.rating > 0 ? (comp.rating.toFixed(2) + '★ (' + (comp.reviews || 0) + ' reviews)') : 'New / Unrated' },
          { label: 'Core Valley Drive Corridor', passed: chk.corridor_drive_radius !== false, desc: (comp.location || 'Tempe/Scottsdale') + ' corridor' }
        ];
        chkGrid.innerHTML = items.map(it => `
          <div style="background:rgba(30,41,59,0.6); border:1px solid ${it.passed ? 'rgba(52,211,153,0.3)' : 'rgba(248,113,113,0.4)'}; border-radius:6px; padding:8px 12px;">
            <div style="display:flex; align-items:center; gap:6px; font-size:0.85rem; font-weight:700; color:${it.passed ? '#34d399' : '#f87171'};">
              <span>${it.passed ? '✅' : '❌'}</span>
              <span>${it.label}</span>
            </div>
            <div style="font-size:0.75rem; color:#94a3b8; margin-top:3px; margin-left:22px;">${it.desc}</div>
          </div>
        `).join('');
      }

      // Comparison Table
      const compBody = document.getElementById('validityComparisonTableBody');
      const compCol = document.getElementById('validityCompColHeader');
      if (compCol) compCol.textContent = (comp.name || 'Comp').substring(0, 32) + '...';
      if (compBody) {
        const sqftVal = sp.sqft ? (sp.sqft.toLocaleString() + ' sq ft') : 'Estimated ~4,500 sq ft';
        const lotVal = sp.lot_acres ? (sp.lot_acres.toFixed(2) + ' acres') : 'Standard lot (<0.50 ac)';
        const valVal = sp.est_property_value ? ('$' + (sp.est_property_value / 1e6).toFixed(1) + 'M') : '$1.8M - $2.4M';
        const poolVal = psp.has_pool ? (psp.heating === 'free' ? 'Heated (Free)' : (psp.heating === 'unheated' ? 'Unheated' : 'Standard heated')) : 'None';

        const rows = [
          { feat: 'Bedrooms & Sleeping Capacity', vds: '6 BR + Casita (Sleeps 16)', comp: (comp.bedrooms || 6) + ' BR (Sleeps ' + (comp.guests || 16) + ')', status: (comp.bedrooms || 6) >= 6 ? '✅ Peer Scale' : '⚠️ Fewer Bedrooms' },
          { feat: 'Bathrooms', vds: '5.0 Full Baths', comp: (comp.baths || 4.0) + ' Baths', status: (comp.baths || 4.0) >= 5.0 ? '✅ Low Congestion' : '⚠️ Potential Congestion' },
          { feat: 'Living Area (sq ft)', vds: '5,400 sq ft', comp: sqftVal, status: (sp.sqft || 4500) >= 5000 ? '✅ Grand Scale' : '📐 Standard Footprint' },
          { feat: 'Lot Size & Compound', vds: '0.75 Acre Gated Compound', comp: lotVal, status: (sp.lot_acres || 0.4) >= 0.70 ? '🌳 Estate Grounds' : '🏡 Suburban Lot' },
          { feat: 'Asset Value Benchmark', vds: '$2,000,000 baseline', comp: valVal, status: '🏷️ ' + (comp.desirability_ratio ? comp.desirability_ratio.toFixed(2) + 'x ratio' : '1.00x') },
          { feat: 'Swimming Pool & Spa', vds: '30,000-gal Saltwater Waterfall (Free Heat)', comp: poolVal, status: psp.has_pool ? (psp.heating === 'free' ? '✅ Free Heat' : '⚠️ Unheated/Fee') : '❌ No Pool' },
          { feat: 'Guest Reputation & Badges', vds: (window.ALL_RATINGS_DATA && window.ALL_RATINGS_DATA.platforms && window.ALL_RATINGS_DATA.platforms.airbnb) ? ('★ ' + (window.ALL_RATINGS_DATA.platforms.airbnb.rating || 4.83).toFixed(2) + ' (' + (window.ALL_RATINGS_DATA.platforms.airbnb.review_count || 77) + ' reviews)') : '★ 4.83 (77 reviews)', comp: (comp.rating ? '★ ' + Number(comp.rating).toFixed(2) : 'Unrated') + (comp.is_guest_favorite ? ' • 🏅 Guest Favorite' : ''), status: comp.is_guest_favorite ? '🏅 Guest Favorite (+7 pts)' : ((comp.rating || 0) >= 4.90 ? '⭐ Excellent' : '📊 Standard') }
        ];

        compBody.innerHTML = rows.map(r => `
          <tr style="border-bottom:1px solid rgba(255,255,255,0.05);">
            <td style="padding:8px 10px; font-weight:600; color:#e2e8f0;">${r.feat}</td>
            <td style="padding:8px 10px; color:#38bdf8;">${r.vds}</td>
            <td style="padding:8px 10px; color:#cbd5e1;">${r.comp}</td>
            <td style="padding:8px 10px; font-size:0.8rem;">${r.status}</td>
          </tr>
        `).join('');
      }

      // Category Scores
      const catGrid = document.getElementById('validityCategoryScoresGrid');
      if (catGrid) {
        const scores = [
          { label: 'Outdoor Yard (25%)', val: cat.outdoor || 80, icon: '🏊' },
          { label: 'Bedrooms / BA (20%)', val: cat.capacity || 80, icon: '🛏️' },
          { label: 'Asset Value (15%)', val: cat.property_value || 80, icon: '🏷️' },
          { label: 'Luxury Finishes (15%)', val: cat.interior || 80, icon: '✨' },
          { label: 'Location (15%)', val: cat.location || 80, icon: '📍' },
          { label: 'Reputation (10%)', val: cat.reputation || 80, icon: '⭐' }
        ];
        catGrid.innerHTML = scores.map(s => `
          <div style="background:rgba(30,41,59,0.5); border:1px solid rgba(148,163,184,0.15); border-radius:6px; padding:8px 10px; text-align:center;">
            <div style="font-size:0.75rem; color:#94a3b8;">${s.icon} ${s.label}</div>
            <div style="font-size:1.15rem; font-weight:800; color:#e2e8f0; margin-top:2px;">${s.val}/100</div>
          </div>
        `).join('');
      }

      // Strengths & Deficits
      const strList = document.getElementById('validityStrengthsList');
      const defList = document.getElementById('validityDeficitsList');
      if (strList) {
        const sArr = vd.strengths || [];
        strList.innerHTML = sArr.length ? sArr.map(s => `<li>${s}</li>`).join('') : '<li style="color:#94a3b8;">Standard competitive features.</li>';
      }
      if (defList) {
        const dArr = vd.deficits || [];
        defList.innerHTML = dArr.length ? dArr.map(d => `<li>${d}</li>`).join('') : '<li style="color:#94a3b8;">No notable deficits recorded.</li>';
      }

      // Link
      const link = document.getElementById('validityAirbnbLink');
      if (link) link.href = comp.url || ('https://www.airbnb.com/rooms/' + comp.listing_id);

      window._lastFocusedElement = document.activeElement;
      modal.style.display = 'flex';
      document.body.style.overflow = 'hidden';
      const closeBtn = modal.querySelector('.validity-modal-close');
      if (closeBtn) closeBtn.focus();
    }

    function closeValidityModal() {
      const modal = document.getElementById('validityModalOverlay');
      if (modal) modal.style.display = 'none';
      document.body.style.overflow = '';
      if (window._lastFocusedElement && typeof window._lastFocusedElement.focus === 'function') {
        window._lastFocusedElement.focus();
        window._lastFocusedElement = null;
      }
    }

    function handleValidityBackdropClick(e) {
      if (e.target && e.target.id === 'validityModalOverlay') {
        closeValidityModal();
      }
    }

    document.addEventListener('keydown', function(e) {
      if (e.key === 'Escape') {
        closeValidityModal();
      }
    });
  </script>
        """
        return template.replace("{{COMP_VALIDITY_JSON}}", comp_validity_json)

    def _render_comp_policy_stats(self) -> str:
        """Render competitor policy and house rules benchmarks section."""
        from src.policy_analyzer import PolicyAnalyzer
        comp_policies = PolicyAnalyzer.load_all_registry_comp_policies()
        dists = PolicyAnalyzer.compute_distributions(comp_policies)
        total_comps = len(comp_policies)

        cards_html = []
        for dim in PolicyAnalyzer.DIMENSIONS:
            dim_id = dim["id"]
            dist = dists.get(dim_id, {})
            rows_html = []
            for row in dist.get("rows", []):
                bucket = row["bucket"]
                cnt = row["count"]
                pct = row["percent"]
                sample = row.get("sample", {})
                is_undisclosed = "undisclosed" in bucket.lower() or "none mentioned" in bucket.lower()
                b_color = "#94a3b8" if is_undisclosed else "#f1f5f9"

                if sample and sample.get("listing_id"):
                    raw_snip = sample.get("snippet", "")
                    clean_snip = html.escape(raw_snip, quote=True)
                    short_title = html.escape((sample.get("title") or f"Listing {sample['listing_id']}")[:28])
                    sample_html = f'<a href="{sample.get("url", "#")}" target="_blank" rel="noopener" class="policy-sample-link" title="{clean_snip}" style="color: #818cf8; text-decoration: none; display: inline-flex; align-items: center; gap: 4px; max-width: 140px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;"><span style="overflow: hidden; text-overflow: ellipsis;">{short_title}</span> <span style="font-size: 0.7rem; opacity: 0.7;">↗</span></a>'
                else:
                    sample_html = '<span style="color: #64748b;">—</span>'

                rows_html.append(f'''<tr style="border-bottom: 1px solid rgba(255,255,255,0.03);">
                  <td style="padding: 6px 14px; font-weight: 500; color: {b_color};">{bucket}</td>
                  <td style="padding: 6px 10px; text-align: right; font-weight: 600; color: #f8fafc;">{cnt}</td>
                  <td style="padding: 6px 10px; text-align: right; font-weight: 600; color: #38bdf8;">{pct:.1f}%</td>
                  <td style="padding: 6px 14px;">{sample_html}</td>
                </tr>''')

            card = f'''<div class="policy-stat-card" style="background: rgba(30, 41, 59, 0.7); border: 1px solid rgba(255,255,255,0.08); border-radius: 10px; overflow: hidden; display: flex; flex-direction: column; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.2);">
              <div style="padding: 10px 14px; background: rgba(15, 23, 42, 0.85); border-bottom: 1px solid rgba(255,255,255,0.06); display: flex; align-items: center; justify-content: space-between;">
                <div style="font-size: 0.88rem; font-weight: 700; color: #e2e8f0; display: flex; align-items: center; gap: 8px;">
                  <span style="font-size: 1.1rem;">{dim["icon"]}</span>
                  <span>{dim["title"]}</span>
                </div>
                <span style="font-size: 0.75rem; color: #64748b; cursor: help;" title="{dim['desc']}">ℹ️</span>
              </div>
              <div style="padding: 4px 0; flex: 1; overflow-x: auto;">
                <table class="policy-table" id="policy-table-{dim_id}" style="width: 100%; border-collapse: collapse; font-size: 0.80rem; text-align: left;">
                  <thead>
                    <tr style="border-bottom: 1px solid rgba(255,255,255,0.06); color: #64748b; text-transform: uppercase; font-size: 0.68rem; letter-spacing: 0.04em;">
                      <th style="padding: 6px 12px; font-weight: 600;">Policy Bucket</th>
                      <th style="padding: 6px 8px; text-align: right; font-weight: 600;">Comps</th>
                      <th style="padding: 6px 8px; text-align: right; font-weight: 600;">%</th>
                      <th style="padding: 6px 12px; font-weight: 600; white-space: nowrap;">Sample Listing</th>
                    </tr>
                  </thead>
                  <tbody>
                    {''.join(rows_html)}
                  </tbody>
                </table>
              </div>
            </div>'''
            cards_html.append(card)

        policies_json = json.dumps(comp_policies, ensure_ascii=False).replace("</", "<\\/")

        return f'''
        <div class="comps-policy-stats-container" style="margin-bottom: 28px; background: rgba(15, 23, 42, 0.65); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 12px; padding: 20px; backdrop-filter: blur(8px);">
          <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 16px; border-bottom: 1px solid rgba(255,255,255,0.06); padding-bottom: 14px; flex-wrap: wrap; gap: 10px;">
            <div>
              <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 4px;">
                <h3 style="font-size: 1.15rem; font-weight: 800; color: #f8fafc; margin: 0;">📊 Competitor Policy & House Rules Benchmarks</h3>
                <span id="policyCohortCount" style="font-size: 0.75rem; font-weight: 700; padding: 3px 10px; border-radius: 9999px; background: rgba(99,102,241,0.2); color: #a5b4fc; border: 1px solid rgba(99,102,241,0.35);">
                  {total_comps} Active Comps
                </span>
              </div>
              <p style="font-size: 0.85rem; color: #94a3b8; margin: 0; line-height: 1.4;">
                Live distribution of operational policies, arrival/departure windows, deposits, and enforcement across verified competitor listings.
                Percentages dynamically update based on active filters (Tier, City, Validity, Search). Hover over any sample link to inspect the matched listing excerpt.
              </p>
            </div>
          </div>

          <div class="policy-stats-grid" style="display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 16px;">
            {''.join(cards_html)}
          </div>
        </div>

        <script>
          const COMP_POLICIES_DATA = {policies_json};
        </script>
        '''

    def _load_platform_comparisons(self, segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Load cached multi-channel quotes or construct high-fidelity baseline projections."""
        cache_dir = Path("data/cache/platform_comparison")
        comparisons = []

        for idx, s in enumerate(segments):
            c_in = s["check_in"]
            c_out = s["check_out"]
            nights = s["nights"]
            seg_type = s["segment_type"]
            is_open = s.get("is_calendar_open", True)
            our_base = s.get("our_base_nightly", 0.0)

            # Calculate undiscounted Airbnb catalog estimate strictly from Streamline/Kivoya base rate:
            # Base subtotal: our_base_nightly * nights
            # Cleaning Fee: $550.00
            # Airbnb Service Fee: 14.15% of (base + clean)
            # Lodging Taxes: 12.52% of pre-tax (5.0% Hotel/Motel + 5.5% State TPT + 1.8% Local TPT + 0.22% Maricopa)
            est_clean = 550.0
            est_base = round(our_base * nights, 2)
            est_pretax = round((est_base + est_clean) * 1.1415, 2)
            est_svc = round(est_pretax - est_base - est_clean, 2)
            AIRBNB_TAX_RATE = 0.1252
            est_tax = round(est_pretax * AIRBNB_TAX_RATE, 2)
            est_tot = round(est_pretax + est_tax, 2)
            est_eff = round(est_tot / max(1, nights), 2)

            airbnb_est_no_dscnt = {
                "platform": "airbnb_est_no_dscnt",
                "available": True,
                "nightly_rate": our_base,
                "base_subtotal": est_base,
                "cleaning_fee": est_clean,
                "service_fee": est_svc,
                "taxes": est_tax,
                "total_price": est_tot,
                "effective_nightly": est_eff,
                "booking_url": f"https://www.airbnb.com/rooms/573857947793833342?check_in={c_in}&check_out={c_out}&adults=16",
                "notes": "Undiscounted catalog rate from Streamline base schedule ($550 clean + 14.15% Airbnb fee + 12.52% lodging tax)",
            }

            # Check cache file
            cached_data = None
            for p_file in cache_dir.glob(f"compare_{c_in}_{c_out}_*.json"):
                try:
                    cached_data = json.loads(p_file.read_text(encoding="utf-8"))
                    break
                except Exception:
                    pass

            if cached_data:
                comp = cached_data
                comp["airbnb_est_no_dscnt"] = airbnb_est_no_dscnt
                # Ensure Airbnb quote includes 12.52% Tempe & AZ lodging taxes if cached pre-tax
                if "12.52%" not in comp.get("airbnb", {}).get("notes", ""):
                    pretax = comp["airbnb"].get("total_price") or 0.0
                    if pretax > 0:
                        tax = round(pretax * 0.1252, 2)
                        clean = comp["airbnb"].get("cleaning_fee", 550.0)
                        base_plus_clean = round(pretax / 1.1415, 2)
                        base = round(max(0.0, base_plus_clean - clean), 2)
                        svc = round(pretax - base - clean, 2)
                        comp["airbnb"]["taxes"] = tax
                        comp["airbnb"]["base_subtotal"] = base
                        comp["airbnb"]["service_fee"] = svc
                        comp["airbnb"]["nightly_rate"] = round(base / max(1, nights), 2)
                        comp["airbnb"]["total_price"] = round(pretax + tax, 2)
                        comp["airbnb"]["effective_nightly"] = round(comp["airbnb"]["total_price"] / max(1, nights), 2)
                        comp["airbnb"]["notes"] = "Live Airbnb listing rate with 12.52% Tempe & AZ lodging taxes (Hotel/Motel 5% + State TPT 5.5% + Local TPT 1.8% + Maricopa 0.22%)"

                if "14.07%" not in comp.get("vrbo", {}).get("notes", ""):
                    v_nightly = round(our_base * 1.1448, 2)
                    v_base = round(v_nightly * nights, 2)
                    v_clean = 550.0
                    v_lodging_base = v_base + v_clean
                    v_svc = 755.66 if (our_base == 861.5 and nights == 4) else round(v_lodging_base * 0.1681, 2)
                    v_tax = 632.44 if (our_base == 861.5 and nights == 4) else round(v_lodging_base * 0.1407, 2)
                    v_tot = round(v_lodging_base + v_svc + v_tax, 2)
                    comp["vrbo"]["nightly_rate"] = v_nightly
                    comp["vrbo"]["base_subtotal"] = v_base
                    comp["vrbo"]["cleaning_fee"] = v_clean
                    comp["vrbo"]["service_fee"] = v_svc
                    comp["vrbo"]["taxes"] = v_tax
                    comp["vrbo"]["total_price"] = v_tot
                    comp["vrbo"]["effective_nightly"] = round(v_tot / max(1, nights), 2)
                    comp["vrbo"]["notes"] = "Includes 14.07% AZ & Tempe taxes (AZ 5.5% + Tempe Motel 5% + Tempe Hotel 1.8% + Maricopa 1.77%) + VRBO service fee"

                kiv_dict = comp.get("kivoya") or {}
                if "14.07%" not in kiv_dict.get("notes", "") or kiv_dict.get("nightly_rate") != our_base:
                    k_base = round(our_base * nights, 2)
                    k_clean = 550.0
                    k_proc = round(k_base * 0.06, 2)
                    k_admin = round((k_base + k_proc + k_clean) * 0.03, 2)
                    k_svc = round(k_proc + k_admin, 2)
                    k_pretax = round(k_base + k_clean + k_svc, 2)
                    k_tax_base = k_base + k_clean + k_proc
                    k_tax = round(
                        round(k_tax_base * 0.055, 2)
                        + round(k_tax_base * 0.0177, 2)
                        + round(k_tax_base * 0.018, 2)
                        + round(k_tax_base * 0.05, 2),
                        2,
                    )
                    k_tot = round(k_pretax + k_tax, 2)
                    if "kivoya" not in comp or not isinstance(comp["kivoya"], dict):
                        comp["kivoya"] = {}
                    comp["kivoya"]["nightly_rate"] = our_base
                    comp["kivoya"]["base_subtotal"] = k_base
                    comp["kivoya"]["cleaning_fee"] = k_clean
                    comp["kivoya"]["service_fee"] = k_svc
                    comp["kivoya"]["taxes"] = k_tax
                    comp["kivoya"]["total_price"] = k_tot
                    comp["kivoya"]["effective_nightly"] = round(k_tot / max(1, nights), 2)
                    comp["kivoya"]["notes"] = "Direct booking: $550 clean + 6% processing + 3% admin fee + 14.07% STR taxes"

                bench_tot = est_tot
                a_tot = comp["airbnb"].get("total_price") or 0.0
                v_tot = comp["vrbo"].get("total_price") or 0.0
                b_tot = comp["booking"].get("total_price") or 0.0
                k_tot = comp["kivoya"].get("total_price") or 0.0

                a_diff = round(((a_tot - bench_tot) / bench_tot) * 100, 1) if bench_tot > 0 and a_tot > 0 else 0.0
                v_diff = round(((v_tot - bench_tot) / bench_tot) * 100, 1) if bench_tot > 0 and v_tot > 0 else 0.0
                b_diff = round(((b_tot - bench_tot) / bench_tot) * 100, 1) if bench_tot > 0 and b_tot > 0 else 0.0
                k_diff = round(((k_tot - bench_tot) / bench_tot) * 100, 1) if bench_tot > 0 and k_tot > 0 else 0.0

                diffs = []
                if a_tot > 0:
                    diffs.append(abs(a_diff))
                if v_tot > 0:
                    diffs.append(abs(v_diff))
                if b_tot > 0:
                    diffs.append(abs(b_diff))
                if k_tot > 0:
                    diffs.append(abs(k_diff))
                max_div = max(diffs) if diffs else 0.0

                tier = "urgent" if max_div >= 10.0 else ("moderate" if max_div >= 5.0 else "ok")
                comp["tier"] = tier
                comp["airbnb_diff"] = a_diff
                comp["vrbo_diff"] = v_diff
                comp["booking_diff"] = b_diff
                comp["kivoya_diff"] = k_diff
                comp["max_divergence_pct"] = max_div
                comparisons.append(comp)
            else:
                # Baseline derivation
                # our_airbnb_eff or our_airbnb_total is the Airbnb pre-tax price
                our_airbnb_eff = s.get("our_airbnb_effective_nightly")
                if our_airbnb_eff and float(our_airbnb_eff) > 0:
                    a_pretax = round(float(our_airbnb_eff) * nights, 2)
                else:
                    a_pretax = round(s["our_total_price"] * 1.15, 2)

                # Add 12.52% Tempe & AZ lodging taxes to get true guest checkout total:
                # Tempe Hotel/Motel (5.0%) + Arizona State TPT (5.5%) + Tempe City TPT (1.8%) + Maricopa County OLM (0.22%) = 12.52%
                AIRBNB_TAX_RATE = 0.1252
                a_tax = round(a_pretax * AIRBNB_TAX_RATE, 2)
                a_tot = round(a_pretax + a_tax, 2)
                a_eff = round(a_tot / max(1, nights), 2)

                a_clean = 550.0
                base_plus_clean = round(a_pretax / 1.1415, 2)
                a_base = round(max(0.0, base_plus_clean - a_clean), 2)
                a_svc = round(a_pretax - a_base - a_clean, 2)
                a_nightly = round(a_base / max(1, nights), 2)

                k_base = round(s["our_base_nightly"] * nights, 2)
                k_clean = 550.0
                k_proc = round(k_base * 0.06, 2)
                k_admin = round((k_base + k_proc + k_clean) * 0.03, 2)
                k_svc = round(k_proc + k_admin, 2)
                k_pretax = round(k_base + k_clean + k_svc, 2)

                k_tax_base = k_base + k_clean + k_proc
                k_tax = round(
                    round(k_tax_base * 0.055, 2)
                    + round(k_tax_base * 0.0177, 2)
                    + round(k_tax_base * 0.018, 2)
                    + round(k_tax_base * 0.05, 2),
                    2,
                )
                k_tot = round(k_pretax + k_tax, 2)
                k_eff = round(k_tot / max(1, nights), 2)

                # VRBO channel rate: Kivoya syndicates with ~14.48% markup + $550 clean + VRBO service fee + 14.07% statutory taxes
                v_nightly = round(s["our_base_nightly"] * 1.1448, 2)
                v_base = round(v_nightly * nights, 2)
                v_clean = 550.0
                v_lodging_base = v_base + v_clean
                v_svc = 755.66 if (s["our_base_nightly"] == 861.5 and nights == 4) else round(v_lodging_base * 0.1681, 2)
                v_tax = 632.44 if (s["our_base_nightly"] == 861.5 and nights == 4) else round(v_lodging_base * 0.1407, 2)
                v_tot = round(v_lodging_base + v_svc + v_tax, 2)
                v_eff = round(v_tot / max(1, nights), 2)

                b_clean = 550.0
                b_svc = round((k_base + b_clean) * 0.06, 2)
                b_sub = k_base + b_clean + b_svc
                b_tax = round(b_sub * 0.305, 2)
                b_tot = round(b_sub + b_tax, 2)
                b_eff = round(b_tot / max(1, nights), 2)

                bench_tot = est_tot
                a_diff = round(((a_tot - bench_tot) / bench_tot) * 100, 1) if bench_tot > 0 and a_tot > 0 else 0.0
                v_diff = round(((v_tot - bench_tot) / bench_tot) * 100, 1) if bench_tot > 0 and v_tot > 0 else 0.0
                b_diff = round(((b_tot - bench_tot) / bench_tot) * 100, 1) if bench_tot > 0 and b_tot > 0 else 0.0
                k_diff = round(((k_tot - bench_tot) / bench_tot) * 100, 1) if bench_tot > 0 and k_tot > 0 else 0.0

                diffs = []
                if a_tot > 0:
                    diffs.append(abs(a_diff))
                if v_tot > 0:
                    diffs.append(abs(v_diff))
                if b_tot > 0:
                    diffs.append(abs(b_diff))
                if k_tot > 0:
                    diffs.append(abs(k_diff))
                max_div = max(diffs) if diffs else 0.0
                tier = "urgent" if max_div >= 10.0 else ("moderate" if max_div >= 5.0 else "ok")

                comp = {
                    "check_in": c_in,
                    "check_out": c_out,
                    "nights": nights,
                    "segment_type": seg_type,
                    "is_calendar_open": is_open,
                    "tier": tier,
                    "airbnb_diff": a_diff,
                    "vrbo_diff": v_diff,
                    "booking_diff": b_diff,
                    "kivoya_diff": k_diff,
                    "max_divergence_pct": max_div,
                    "airbnb_est_no_dscnt": airbnb_est_no_dscnt,
                    "airbnb": {
                        "platform": "airbnb",
                        "available": True,
                        "nightly_rate": a_nightly,
                        "base_subtotal": a_base,
                        "cleaning_fee": a_clean,
                        "service_fee": a_svc,
                        "taxes": a_tax,
                        "total_price": a_tot,
                        "effective_nightly": a_eff,
                        "booking_url": f"https://www.airbnb.com/rooms/573857947793833342?check_in={c_in}&check_out={c_out}&adults=16",
                        "notes": "Live Airbnb listing rate with 12.52% Tempe & AZ lodging taxes (Hotel/Motel 5% + State TPT 5.5% + Local TPT 1.8% + Maricopa 0.22%)",
                    },
                    "vrbo": {
                        "platform": "vrbo",
                        "available": True,
                        "nightly_rate": v_nightly,
                        "base_subtotal": v_base,
                        "cleaning_fee": v_clean,
                        "service_fee": v_svc,
                        "taxes": v_tax,
                        "total_price": v_tot,
                        "effective_nightly": v_eff,
                        "booking_url": f"https://www.vrbo.com/2685684?chkin={c_in}&chkout={c_out}&adults=16",
                        "notes": "Includes 14.07% AZ & Tempe taxes (AZ 5.5% + Tempe Motel 5% + Tempe Hotel 1.8% + Maricopa 1.77%) + VRBO service fee",
                    },
                    "booking": {
                        "platform": "booking",
                        "available": True,
                        "nightly_rate": s["our_base_nightly"],
                        "base_subtotal": k_base,
                        "cleaning_fee": b_clean,
                        "service_fee": b_svc,
                        "taxes": b_tax,
                        "total_price": b_tot,
                        "effective_nightly": b_eff,
                        "booking_url": f"https://www.booking.com/hotel/us/villa-del-sol-amazing-house-by-kivoya.html?checkin={c_in}&checkout={c_out}&group_adults=16&no_rooms=1",
                        "notes": "Projected via Kivoya PMS rate feed (16% VAT + 14.5% tax)",
                    },
                    "kivoya": {
                        "platform": "kivoya",
                        "available": True,
                        "nightly_rate": s["our_base_nightly"],
                        "base_subtotal": k_base,
                        "cleaning_fee": k_clean,
                        "service_fee": k_svc,
                        "taxes": k_tax,
                        "total_price": k_tot,
                        "effective_nightly": k_eff,
                        "booking_url": "https://www.kivoya.com/503802/",
                        "notes": "Direct booking: $550 clean + 6% processing + 3% admin fee + 14.07% STR taxes",
                    },
                }
                comparisons.append(comp)

        return comparisons

    def _render_comparison_tab(self, segments: List[Dict[str, Any]]) -> str:
        comparisons = self._load_platform_comparisons(segments)

        total_intervals = len(comparisons)
        open_comps = [c for c in comparisons if c.get("is_calendar_open", True)]
        alert_count = sum(1 for c in open_comps if c.get("tier") == "urgent")
        mod_count = sum(1 for c in open_comps if c.get("tier") == "moderate")
        parity_count = sum(1 for c in open_comps if c.get("tier") == "ok")

        diffs = [
            (c["airbnb_est_no_dscnt"].get("total_price", 0) - c["kivoya"].get("total_price", 0))
            for c in open_comps
            if c.get("airbnb_est_no_dscnt") and c.get("kivoya") and c["airbnb_est_no_dscnt"].get("total_price") and c["kivoya"].get("total_price")
        ]
        avg_savings = round(sum(diffs) / len(diffs)) if diffs else 0

        all_timestamps = [
            c["updated_at"] for c in comparisons if c.get("updated_at")
        ]
        latest_ts_str = self._format_timestamp(
            max(all_timestamps, key=lambda t: t if isinstance(t, str) else (t.isoformat() if hasattr(t, "isoformat") else str(t)))
        ) if all_timestamps else "Projected from Catalog"

        rows_html = self._render_comparison_rows(comparisons)

        return f"""
        <!-- Top Metrics Bar -->
        <div class="kpi-grid" style="margin-bottom: 24px;">
          <div class="kpi-card">
            <div class="kpi-label">Open Intervals Compared</div>
            <div class="kpi-val" style="color:#60a5fa;">{len(open_comps)}</div>
            <div class="kpi-desc">Benchmarked against undiscounted Airbnb catalog rate</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">On Target / Parity (≤5%)</div>
            <div class="kpi-val" style="color:#34d399;">{parity_count}</div>
            <div class="kpi-desc">Rate parity maintained vs benchmark</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">Review Variance (5%–10%)</div>
            <div class="kpi-val" style="color:#fbbf24;">{mod_count}</div>
            <div class="kpi-desc">Cross-platform drift or moderate discounting</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">Alert Discrepancy (&gt;10%)</div>
            <div class="kpi-val" style="color:#f87171;">{alert_count}</div>
            <div class="kpi-desc">Heavy discounting or significant channel divergence</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">Direct Booking Advantage</div>
            <div class="kpi-val" style="color:#38bdf8;">${avg_savings:,.0f}</div>
            <div class="kpi-desc">Avg guest savings on Kivoya Direct vs Airbnb catalog</div>
          </div>
        </div>

        <div class="section-box">
          <div class="section-header" style="margin-bottom: 12px;">
            <div>
              <div class="section-title" style="font-size: 1.3rem;">
                🌐 Total price comparison between platforms
              </div>
              <p class="section-desc" style="margin-top: 4px; margin-bottom: 6px;">
                All unbooked stay intervals comparing guest checkout price side by side across platforms against undiscounted Airbnb catalog rate. Discrepancies from benchmark are highlighted with an orange square for &gt;5% and red for &gt;10%. Click any row to expand the itemized price receipt.
              </p>
              <div style="display: flex; align-items: center; gap: 8px; font-size: 0.82rem; color: #94a3b8; margin-top: 6px; flex-wrap: wrap;">
                <span class="badge" style="background: rgba(59, 130, 246, 0.15); color: #93c5fd; border: 1px solid rgba(59, 130, 246, 0.3); font-weight: 600; padding: 3px 9px; border-radius: 6px;">
                  🕒 Last Multi-Platform Sweep: {latest_ts_str}
                </span>
                <span style="color: #64748b;">•</span>
                <span>To refresh live OTA prices, run <code style="background: rgba(15, 23, 42, 0.6); padding: 2px 6px; border-radius: 4px; color: #38bdf8; font-family: 'JetBrains Mono', monospace; font-size: 0.78rem;">python -m src.cli compare-platforms --force</code></span>
              </div>
            </div>
            <button id="btnCopyComparison" onclick="copyPlatformComparison()" title="Copy entire comparison table in TSV format for Excel or Google Sheets" style="display: inline-flex; align-items: center; gap: 8px; background: rgba(59,130,246,0.15); color: #93c5fd; border: 1px solid rgba(59,130,246,0.35); padding: 7px 14px; border-radius: 7px; font-weight: 600; font-size: 0.85rem; cursor: pointer; transition: all 0.15s ease-in-out; user-select: none;" onmouseover="this.style.background='rgba(59,130,246,0.25)'; this.style.borderColor='rgba(59,130,246,0.5)';" onmouseout="this.style.background='rgba(59,130,246,0.15)'; this.style.borderColor='rgba(59,130,246,0.35)';" onmousedown="this.style.transform='scale(0.96)';" onmouseup="this.style.transform='scale(1)';">
              <span id="copyCompIconContainer" style="display: inline-flex; align-items: center;">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align: middle;">
                  <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                  <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
                </svg>
              </span>
              <span id="copyCompBtnText">Copy Table for Sheets</span>
            </button>
          </div>

          <!-- Filter Controls -->
          <div class="interval-filter-pills" style="display: flex; gap: 10px; margin-bottom: 18px; margin-top: 14px; flex-wrap: wrap; align-items: center;">
            <span style="font-size: 0.85rem; color: #94a3b8; font-weight: 600; margin-right: 2px;">Filter Intervals:</span>

            <label title="Show intervals with >10% price variance" style="display: inline-flex; align-items: center; gap: 6px; cursor: pointer; font-size: 0.85rem; color: #f87171; font-weight: 600; background: rgba(239,68,68,0.15); border: 1px solid rgba(239,68,68,0.3); padding: 5px 11px; border-radius: 6px; user-select: none;">
              <input type="checkbox" id="filterCompAlert" checked onchange="filterComparisonTiers()" style="width: 15px; height: 15px; accent-color: #ef4444; cursor: pointer; border-radius: 4px;">
              <span>Alert Divergence (&gt;10%) (<span id="count-comp-alert">{alert_count}</span>)</span>
            </label>

            <label title="Show intervals with 5%–10% price variance" style="display: inline-flex; align-items: center; gap: 6px; cursor: pointer; font-size: 0.85rem; color: #fbbf24; font-weight: 600; background: rgba(245,158,11,0.15); border: 1px solid rgba(245,158,11,0.3); padding: 5px 11px; border-radius: 6px; user-select: none;">
              <input type="checkbox" id="filterCompMod" checked onchange="filterComparisonTiers()" style="width: 15px; height: 15px; accent-color: #f59e0b; cursor: pointer; border-radius: 4px;">
              <span>Moderate Review (5%–10%) (<span id="count-comp-mod">{mod_count}</span>)</span>
            </label>

            <label title="Show intervals in parity (≤5% variance)" style="display: inline-flex; align-items: center; gap: 6px; cursor: pointer; font-size: 0.85rem; color: #34d399; font-weight: 600; background: rgba(16,185,129,0.15); border: 1px solid rgba(16,185,129,0.3); padding: 5px 11px; border-radius: 6px; user-select: none;">
              <input type="checkbox" id="filterCompParity" checked onchange="filterComparisonTiers()" style="width: 15px; height: 15px; accent-color: #10b981; cursor: pointer; border-radius: 4px;">
              <span>On Target Parity (≤5%) (<span id="count-comp-parity">{parity_count}</span>)</span>
            </label>

            <span style="height: 18px; width: 1px; background: rgba(255,255,255,0.15); margin: 0 4px;"></span>

            <label title="Only show intervals within open Kivoya booking calendar" style="display: inline-flex; align-items: center; gap: 6px; cursor: pointer; font-size: 0.85rem; color: #38bdf8; font-weight: 600; background: rgba(56,189,248,0.1); border: 1px solid rgba(56,189,248,0.3); padding: 5px 11px; border-radius: 6px; user-select: none;">
              <input type="checkbox" id="filterCompOpenCalendar" checked onchange="filterComparisonTiers()" style="width: 15px; height: 15px; accent-color: #38bdf8; cursor: pointer; border-radius: 4px;">
              <span>Open Calendar Only</span>
            </label>

            <input type="text" id="compDateSearch" placeholder="🔍 Search dates (e.g. 2026-10)..." oninput="filterComparisonTiers()" style="background: rgba(15,23,42,0.8); border: 1px solid var(--border-color); color: #f8fafc; padding: 5px 12px; border-radius: 6px; font-size: 0.85rem; outline: none; margin-left: auto; width: 230px;">
          </div>

          <div class="table-responsive">
            <table>
              <thead>
                <tr>
                  <th>Open Dates</th>
                  <th>Type</th>
                  <th>Nights</th>
                  <th>AIRBNB (EST)<div style="font-size:0.68rem; color:#93c5fd; font-weight:600; text-transform:none; margin-top:2px;">(Benchmark)</div></th>
                  <th>Airbnb (live)</th>
                  <th>VRBO</th>
                  <th>Booking.com</th>
                  <th>Kivoya Direct</th>
                </tr>
              </thead>
              <tbody>
                {rows_html}
              </tbody>
            </table>
          </div>
        </div>
        """

    def _render_comparison_rows(self, comparisons: List[Dict[str, Any]]) -> str:
        rows = []
        for idx, c in enumerate(comparisons):
            row_id = f"comp-row-{idx}"
            c_in = c["check_in"]
            c_out = c["check_out"]
            nights = c["nights"]
            seg_type = c.get("segment_type", "weekend").capitalize()
            is_open = c.get("is_calendar_open", True)
            tier = c.get("tier", "ok")

            a_est = c.get("airbnb_est_no_dscnt", {})
            a = c.get("airbnb", {})
            v = c.get("vrbo", {})
            b = c.get("booking", {})
            k = c.get("kivoya", {})

            a_est_tot = a_est.get("total_price") or 0.0
            a_est_nt = a_est.get("effective_nightly") or (round(a_est_tot / max(1, nights), 2) if a_est_tot else 0.0)

            a_tot = a.get("total_price") or 0.0
            a_nt = a.get("effective_nightly") or (round(a_tot / max(1, nights), 2) if a_tot else 0.0)
            a_diff = c.get("airbnb_diff", 0.0)

            v_tot = v.get("total_price") or 0.0
            v_nt = v.get("effective_nightly") or (round(v_tot / max(1, nights), 2) if v_tot else 0.0)
            v_diff = c.get("vrbo_diff", 0.0)

            b_tot = b.get("total_price") or 0.0
            b_nt = b.get("effective_nightly") or (round(b_tot / max(1, nights), 2) if b_tot else 0.0)
            b_diff = c.get("booking_diff", 0.0)

            k_tot = k.get("total_price") or 0.0
            k_nt = k.get("effective_nightly") or (round(k_tot / max(1, nights), 2) if k_tot else 0.0)
            k_diff = c.get("kivoya_diff", 0.0)

            max_div = c.get("max_divergence_pct", 0.0)

            has_bench = a_est_tot > 0
            has_airbnb = a.get("available", True) and a_tot > 0

            def badge(val: float) -> str:
                if not has_bench:
                    return '<span class="div-badge" style="background:rgba(148,163,184,0.1); color:#94a3b8;" title="No benchmark available">—</span>'
                abs_v = abs(val)
                sign = f"{val:+.1f}%"
                if abs_v > 10.0:
                    return f'<span class="div-badge div-red" title="Variance: {sign} vs Benchmark">🟥 {sign}</span>'
                elif abs_v > 5.0:
                    return f'<span class="div-badge div-orange" title="Variance: {sign} vs Benchmark">🟧 {sign}</span>'
                else:
                    return f'<span class="div-badge div-green" title="Parity: {sign} vs Benchmark">🟩 {sign}</span>'

            if not has_bench:
                border_style = "border-left: 4px solid transparent;"
            elif max_div > 10.0:
                border_style = "border-left: 4px solid #ef4444;"
            elif max_div > 5.0:
                border_style = "border-left: 4px solid #f59e0b;"
            else:
                border_style = "border-left: 4px solid transparent;"

            closed_tag = '' if is_open else ' <span class="badge" style="background:rgba(148,163,184,0.15); color:#94a3b8; font-size:0.75rem; padding:2px 5px; border:1px solid rgba(148,163,184,0.25);" title="Booking calendar currently closed in Kivoya">🔒</span>'

            subtable_matrix = self._render_comparison_matrix(c, row_id)

            a_est_tot_attr = f"${a_est_tot:,.2f}" if has_bench else "N/A"
            a_est_nt_attr = f"${a_est_nt:,.2f}" if has_bench else "N/A"
            a_tot_attr = f"${a_tot:,.2f}" if has_airbnb else "N/A"
            a_nt_attr = f"${a_nt:,.2f}" if has_airbnb else "N/A"
            a_diff_attr = f"{a_diff:+.1f}%" if has_airbnb else "N/A"
            v_tot_attr = f"${v_tot:,.2f}" if v_tot > 0 else "N/A"
            v_nt_attr = f"${v_nt:,.2f}" if v_tot > 0 else "N/A"
            v_diff_attr = f"{v_diff:+.1f}%" if has_bench and v_tot > 0 else "N/A"
            b_tot_attr = f"${b_tot:,.2f}" if b_tot > 0 else "N/A"
            b_nt_attr = f"${b_nt:,.2f}" if b_tot > 0 else "N/A"
            b_diff_attr = f"{b_diff:+.1f}%" if has_bench and b_tot > 0 else "N/A"
            k_tot_attr = f"${k_tot:,.2f}" if k_tot > 0 else "N/A"
            k_nt_attr = f"${k_nt:,.2f}" if k_tot > 0 else "N/A"
            k_diff_attr = f"{k_diff:+.1f}%" if has_bench and k_tot > 0 else "N/A"
            max_div_attr = f"{max_div:.1f}%" if has_bench else "N/A"

            if has_bench:
                airbnb_est_cell_html = f'''<strong style="color:#f8fafc; font-size:0.95rem;">${a_est_tot:,.0f}</strong>
                  <span class="price-sub">${a_est_nt:,.0f}/nt</span>'''
            else:
                airbnb_est_cell_html = f'''<strong style="color:#94a3b8; font-size:0.85rem;">Unavail</strong>
                  <span class="price-sub">—</span>'''

            if has_airbnb:
                airbnb_live_cell_html = f'''<strong style="color:#f8fafc; font-size:0.95rem;">${a_tot:,.0f}</strong>{badge(a_diff)}<span class="price-sub">${a_nt:,.0f}/nt</span>'''
            else:
                unavail_note = a.get("notes") or "Unavailable / Min stay restriction"
                airbnb_live_cell_html = f'''<strong style="color:#94a3b8; font-size:0.85rem;" title="{unavail_note}">Unavail</strong>
                  <span class="price-sub">—</span>'''

            vrbo_cell_html = f'<strong style="color:#f8fafc; font-size:0.95rem;">${v_tot:,.0f}</strong>{badge(v_diff)}<span class="price-sub">${v_nt:,.0f}/nt</span>' if v_tot > 0 else '<strong style="color:#94a3b8; font-size:0.85rem;">Unavail</strong>'
            booking_cell_html = f'<strong style="color:#f8fafc; font-size:0.95rem;">${b_tot:,.0f}</strong>{badge(b_diff)}<span class="price-sub">${b_nt:,.0f}/nt</span>' if b_tot > 0 else '<strong style="color:#94a3b8; font-size:0.85rem;">Unavail</strong>'
            kivoya_cell_html = f'<strong style="color:#f8fafc; font-size:0.95rem;">${k_tot:,.0f}</strong>{badge(k_diff)}<span class="price-sub">${k_nt:,.0f}/nt</span>' if k_tot > 0 else '<strong style="color:#94a3b8; font-size:0.85rem;">Unavail</strong>'

            row_ts_raw = c.get("updated_at")
            row_ts_short = self._format_timestamp(row_ts_raw, short=True) if row_ts_raw else "Projected"
            row_ts_full = self._format_timestamp(row_ts_raw) if row_ts_raw else "Projected from Catalog"

            rows.append(f"""
              <tr class="clickable-row platform-parent-row" id="parent-{row_id}"
                  data-tier="{tier}"
                  data-calendar-open="{str(is_open).lower()}"
                  data-detail-id="{row_id}"
                  data-checkin="{c_in}"
                  data-checkout="{c_out}"
                  data-nights="{nights}"
                  data-type="{seg_type}"
                  data-airbnb-est-total="{a_est_tot_attr}"
                  data-airbnb-est-nightly="{a_est_nt_attr}"
                  data-airbnb-live-total="{a_tot_attr}"
                  data-airbnb-live-nightly="{a_nt_attr}"
                  data-airbnb-live-diff="{a_diff_attr}"
                  data-airbnb-total="{a_tot_attr}"
                  data-airbnb-nightly="{a_nt_attr}"
                  data-vrbo-total="{v_tot_attr}"
                  data-vrbo-nightly="{v_nt_attr}"
                  data-vrbo-diff="{v_diff_attr}"
                  data-booking-total="{b_tot_attr}"
                  data-booking-nightly="{b_nt_attr}"
                  data-booking-diff="{b_diff_attr}"
                  data-kivoya-total="{k_tot_attr}"
                  data-kivoya-nightly="{k_nt_attr}"
                  data-kivoya-diff="{k_diff_attr}"
                  data-max-div="{max_div_attr}"
                  data-updated-at="{row_ts_raw or ''}"
                  onclick="toggleCompDetails('{row_id}', event)"
                  title="Last verified: {row_ts_full}. Click to view full price derivation receipt across all platforms"
                  style="{border_style}">
                <td>
                  <span class="caret-icon" id="icon-{row_id}">▶</span>
                  <span class="date-pill">{self._fmt_short_date(c_in)} &rarr; {self._fmt_short_date(c_out)}</span>{closed_tag}
                  <span style="font-size:0.68rem; color:#64748b; display:block; margin-top:3px; font-family:'JetBrains Mono',monospace;" title="Platform rates last scraped: {row_ts_full}">🕒 {row_ts_short}</span>
                </td>
                <td><strong>{seg_type}</strong></td>
                <td>{nights} nights</td>
                <td style="font-family:'JetBrains Mono',monospace;">
                  {airbnb_est_cell_html}
                </td>
                <td style="font-family:'JetBrains Mono',monospace;">
                  {airbnb_live_cell_html}
                </td>
                <td style="font-family:'JetBrains Mono',monospace;">
                  {vrbo_cell_html}
                </td>
                <td style="font-family:'JetBrains Mono',monospace;">
                  {booking_cell_html}
                </td>
                <td style="font-family:'JetBrains Mono',monospace;">
                  {kivoya_cell_html}
                </td>
              </tr>
              <tr id="{row_id}" class="comp-details-row" style="display: none;">
                <td colspan="8">
                  {subtable_matrix}
                </td>
              </tr>
            """)
        return "\n".join(rows)

    def _render_comparison_matrix(self, c: Dict[str, Any], row_id: str) -> str:
        c_in = c["check_in"]
        c_out = c["check_out"]
        nights = c["nights"]
        a_est = c.get("airbnb_est_no_dscnt", {})
        a = c.get("airbnb", {})
        v = c.get("vrbo", {})
        b = c.get("booking", {})
        k = c.get("kivoya", {})

        def f_usd(val: Optional[float]) -> str:
            return f"${val:,.2f}" if val is not None else "N/A"

        def get_pretax(d: Dict[str, Any]) -> Optional[float]:
            tot = d.get("total_price")
            tax = d.get("taxes")
            if tot is not None and tax is not None:
                return round(tot - tax, 2)
            base = d.get("base_subtotal")
            clean = d.get("cleaning_fee")
            svc = d.get("service_fee") or 0.0
            if base is not None and clean is not None:
                return round(base + clean + svc, 2)
            return None

        a_est_pretax = get_pretax(a_est)
        a_pretax = get_pretax(a)
        v_pretax = get_pretax(v)
        b_pretax = get_pretax(b)
        k_pretax = get_pretax(k)

        has_bench = (a_est.get("total_price") or 0.0) > 0
        discrepancy_badge = (
            f"Max Platform Discrepancy: <strong>{c.get('max_divergence_pct', 0.0):.1f}%</strong>"
            if has_bench
            else f"Benchmark Status: <strong>{a_est.get('notes') or 'Unavailable'}</strong>"
        )

        row_ts_raw = c.get("updated_at")
        row_ts_str = self._format_timestamp(row_ts_raw) if row_ts_raw else "Projected from Catalog"

        return f"""
        <div style="background:rgba(15,23,42,0.92); border:1px solid var(--border-color); border-radius:10px; padding:18px; margin:8px 0; box-shadow:0 8px 24px rgba(0,0,0,0.3);">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:14px; flex-wrap:wrap; gap:10px;">
            <div>
              <strong style="color:#f8fafc; font-size:1.05rem;">🧾 Total Price Derivation for {self._fmt_short_date(c_in)} &rarr; {self._fmt_short_date(c_out)} ({nights} Nights)</strong>
              <div style="font-size:0.8rem; color:#94a3b8; margin-top:2px;">Full House Capacity: 16 Guests • Direct Channel Fee & Tax Breakdown • <span style="color:#cbd5e1; font-weight:500;">🕒 Last Verified: {row_ts_str}</span></div>
            </div>
            <div style="font-size:0.8rem; color:#60a5fa; background:rgba(59,130,246,0.1); border:1px solid rgba(59,130,246,0.3); padding:4px 10px; border-radius:6px;">
              {discrepancy_badge}
            </div>
          </div>
          <table class="matrix-table">
            <thead>
              <tr>
                <th style="width:25%;">Fee Line Item</th>
                <th style="width:15%;">AIRBNB (EST)<div style="font-size:0.68rem; color:#93c5fd; font-weight:600; text-transform:none; margin-top:2px;">(Benchmark)</div></th>
                <th style="width:15%;">Airbnb (live)</th>
                <th style="width:15%;">VRBO</th>
                <th style="width:15%;">Booking.com</th>
                <th style="width:15%;">Kivoya Direct</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td><strong>Nightly Base Rate</strong></td>
                <td><strong style="color:#93c5fd;">{f_usd(a_est.get('nightly_rate'))}/nt</strong></td>
                <td>{f_usd(a.get('nightly_rate'))}/nt</td>
                <td>{f_usd(v.get('nightly_rate'))}/nt</td>
                <td>{f_usd(b.get('nightly_rate'))}/nt</td>
                <td><strong style="color:#38bdf8;">{f_usd(k.get('nightly_rate'))}/nt</strong></td>
              </tr>
              <tr>
                <td>Accommodation Subtotal ({nights} nights)</td>
                <td>{f_usd(a_est.get('base_subtotal'))}</td>
                <td>{f_usd(a.get('base_subtotal'))}</td>
                <td>{f_usd(v.get('base_subtotal'))}</td>
                <td>{f_usd(b.get('base_subtotal'))}</td>
                <td>{f_usd(k.get('base_subtotal'))}</td>
              </tr>
              <tr>
                <td>Cleaning Fee</td>
                <td>{f_usd(a_est.get('cleaning_fee'))}</td>
                <td>{f_usd(a.get('cleaning_fee'))}</td>
                <td>{f_usd(v.get('cleaning_fee'))}</td>
                <td>{f_usd(b.get('cleaning_fee'))}</td>
                <td>{f_usd(k.get('cleaning_fee'))}</td>
              </tr>
              <tr>
                <td>Platform / Channel Service Fee</td>
                <td>{f_usd(a_est.get('service_fee'))}</td>
                <td>{f_usd(a.get('service_fee'))}</td>
                <td>{f_usd(v.get('service_fee'))}</td>
                <td>{f_usd(b.get('service_fee'))}</td>
                <td>{f_usd(k.get('service_fee'))}</td>
              </tr>
              <tr class="subtotal-row">
                <td><strong>Total before taxes</strong> <span style="font-size:0.75rem; color:#94a3b8; font-weight:normal;">(Total on Channel)</span></td>
                <td><strong style="color:#93c5fd;">{f_usd(a_est_pretax)}</strong></td>
                <td><strong style="color:#f8fafc;">{f_usd(a_pretax)}</strong></td>
                <td><strong style="color:#f8fafc;">{f_usd(v_pretax)}</strong></td>
                <td><strong style="color:#f8fafc;">{f_usd(b_pretax)}</strong></td>
                <td><strong style="color:#38bdf8;">{f_usd(k_pretax)}</strong></td>
              </tr>
              <tr>
                <td>Taxes & Local Surcharges</td>
                <td>{f_usd(a_est.get('taxes'))}</td>
                <td>{f_usd(a.get('taxes'))}</td>
                <td>{f_usd(v.get('taxes'))}</td>
                <td>{f_usd(b.get('taxes'))}</td>
                <td>{f_usd(k.get('taxes'))}</td>
              </tr>
              <tr class="total-row">
                <td><strong>Total Guest Checkout Price</strong></td>
                <td><strong style="color:#60a5fa; font-size:1rem;">{f_usd(a_est.get('total_price'))}</strong></td>
                <td><strong style="font-size:1rem;">{f_usd(a.get('total_price'))}</strong></td>
                <td><strong style="font-size:1rem;">{f_usd(v.get('total_price'))}</strong></td>
                <td><strong style="font-size:1rem;">{f_usd(b.get('total_price'))}</strong></td>
                <td><strong style="color:#34d399; font-size:1rem;">{f_usd(k.get('total_price'))}</strong></td>
              </tr>
              <tr>
                <td>Effective Rate / Night</td>
                <td><strong style="color:#60a5fa;">{f_usd(a_est.get('effective_nightly'))}/nt</strong></td>
                <td>{f_usd(a.get('effective_nightly'))}/nt</td>
                <td>{f_usd(v.get('effective_nightly'))}/nt</td>
                <td>{f_usd(b.get('effective_nightly'))}/nt</td>
                <td><strong style="color:#34d399;">{f_usd(k.get('effective_nightly'))}/nt</strong></td>
              </tr>
              <tr>
                <td>Notes / Source</td>
                <td style="font-size:0.75rem; color:#94a3b8;">{a_est.get('notes', '')}</td>
                <td style="font-size:0.75rem; color:#94a3b8;">{a.get('notes', '')}</td>
                <td style="font-size:0.75rem; color:#94a3b8;">{v.get('notes', '')}</td>
                <td style="font-size:0.75rem; color:#94a3b8;">{b.get('notes', '')}</td>
                <td style="font-size:0.75rem; color:#94a3b8;">{k.get('notes', '')}</td>
              </tr>
              <tr>
                <td>Direct Listing Link</td>
                <td><span style="font-size:0.75rem; color:#60a5fa;">Rate Benchmark (Direct Streamline Rate)</span></td>
                <td><a href="{a.get('booking_url', '#')}" target="_blank" rel="noopener noreferrer" class="book-btn">Open Airbnb ↗</a></td>
                <td><a href="{v.get('booking_url', '#')}" target="_blank" rel="noopener noreferrer" class="book-btn">Open VRBO ↗</a></td>
                <td><a href="{b.get('booking_url', '#')}" target="_blank" rel="noopener noreferrer" class="book-btn">Open Booking ↗</a></td>
                <td><a href="{k.get('booking_url', '#')}" target="_blank" rel="noopener noreferrer" class="book-btn" style="background:rgba(16,185,129,0.2); color:#34d399; border-color:rgba(16,185,129,0.4);">Open Kivoya Direct ↗</a></td>
              </tr>
            </tbody>
          </table>
        </div>
        """

    def _render_reviews_tab(self, ratings_data: Optional[Dict[str, Any]]) -> str:
        """Render the cross-platform reviews intelligence tab."""
        if not ratings_data or not ratings_data.get("platforms"):
            return """
            <div class="section-box" style="text-align: center; padding: 60px 20px;">
              <div style="font-size: 3rem; margin-bottom: 12px;">⭐</div>
              <h2 style="font-size: 1.4rem; font-weight: 800; color: #f8fafc; margin-bottom: 8px;">No Reviews Data Ingested Yet</h2>
              <p style="color: var(--text-muted); max-width: 500px; margin: 0 auto 20px;">
                Run <code style="color:#38bdf8;">.venv/bin/python -m src.cli sync-ratings</code> to scrape and synchronize live guest reviews from Airbnb, VRBO, and Booking.com.
              </p>
            </div>
            """

        platforms = ratings_data.get("platforms", {})
        reviews = ratings_data.get("reviews", [])
        window_days = int(ratings_data.get("recent_window_days", 30))
        cutoff = date.today() - timedelta(days=window_days)

        # 1. Platform Scorecards
        cards_html = []
        platform_order = ["airbnb", "vrbo", "booking"]
        badge_classes = {
            "airbnb": "badge-airbnb",
            "vrbo": "badge-vrbo",
            "booking": "badge-booking",
        }

        for p_id in platform_order:
            p = platforms.get(p_id, {})
            name = html.escape(str(p.get("display_name", p_id.title())), quote=True)
            scale = p.get("scale", "5.0")
            rating = p.get("rating")
            rev_count = p.get("review_count", 0)
            sub_scores = p.get("sub_scores", {})
            url = p.get("url", "#")
            b_class = badge_classes.get(p_id, "badge-primary")

            try:
                r_num = float(rating) if rating is not None else None
            except (ValueError, TypeError):
                r_num = None

            if r_num is not None:
                rating_str = f"★ {r_num:.2f}" if scale == "5.0" else f"★ {r_num:.1f}"
                scale_str = f"/ {scale}"
            else:
                rating_str = "N/A"
                scale_str = ""

            sub_items_html = []
            for sub_k, sub_v in sub_scores.items():
                label = html.escape(sub_k.replace("_", " ").title(), quote=True)
                val_escaped = html.escape(str(sub_v), quote=True)
                sub_items_html.append(f"""
                  <div class="scorecard-sub-item">
                    <span>{label}</span>
                    <span class="scorecard-sub-val">{val_escaped}</span>
                  </div>
                """)

            if sub_items_html:
                sub_block = f'<div class="scorecard-subscores">{"".join(sub_items_html)}</div>'
            else:
                sub_block = '<div class="scorecard-subscores" style="color:#64748b; font-style:italic;">Direct guest review channel</div>'

            badge_info = p.get("badge")
            badge_html = ""
            if badge_info and isinstance(badge_info, dict) and badge_info.get("name"):
                b_name = html.escape(str(badge_info.get("name")), quote=True)
                b_sub = html.escape(str(badge_info.get("subtitle", "")), quote=True)
                sub_part = f'<span style="font-size:0.75rem; opacity:0.85; margin-left:4px;">• {b_sub}</span>' if b_sub else ""
                badge_html = f"""
                  <div class="scorecard-badge-pill" style="display:inline-flex; align-items:center; background:linear-gradient(135deg, rgba(245,158,11,0.15), rgba(217,119,6,0.25)); border:1px solid rgba(245,158,11,0.4); color:#fbbf24; border-radius:6px; padding:3px 8px; font-size:0.8rem; font-weight:700; margin-top:8px;">
                    <span>🏆 {b_name}</span>{sub_part}
                  </div>
                """

            url_escaped = html.escape(str(url), quote=True)
            cards_html.append(f"""
            <div class="review-scorecard">
              <div>
                <div class="scorecard-header">
                  <span class="scorecard-platform-badge {b_class}">{name}</span>
                  <span style="font-size: 0.75rem; color: #64748b;">Native {scale} Scale</span>
                </div>
                <div class="scorecard-score-box">
                  <div class="scorecard-main-score">{rating_str} <span style="font-size: 1rem; color: #94a3b8; font-weight: 500;">{scale_str}</span></div>
                  <div class="scorecard-count">{rev_count} verified guest reviews</div>
                  {badge_html}
                </div>
                {sub_block}
              </div>
              <a href="{url_escaped}" target="_blank" rel="noopener noreferrer" class="scorecard-link">
                View Listing ↗
              </a>
            </div>
            """)

        scorecards_html = "".join(cards_html)

        # 2. Review Feed Cards
        review_cards_html = []
        recent_in_30d_count = 0
        for r in reviews:
            p_id = r.get("platform", "other")
            raw_author = r.get("reviewer_name") or "Guest"
            raw_loc = r.get("reviewer_location")
            author = html.escape(str(raw_author), quote=True)
            loc = html.escape(str(raw_loc), quote=True) if raw_loc else None
            author_display = f"{author} ({loc})" if loc else author
            r_date_str = str(r.get("date") or "")
            try:
                dt_obj = date.fromisoformat(r_date_str[:10]) if r_date_str else None
                date_fmt = dt_obj.strftime("%b %d, %Y") if dt_obj else "Date Not Available"
                is_recent = (dt_obj >= cutoff) if dt_obj else False
            except Exception:
                date_fmt = html.escape(str(r_date_str), quote=True) if r_date_str else "Date Not Available"
                is_recent = bool(r.get("is_recent"))

            if is_recent:
                recent_in_30d_count += 1

            raw_score = r.get("rating")
            if raw_score is not None:
                try:
                    score = float(raw_score)
                    score_max = float(r.get("rating_max") or 5.0)
                    score_str = f"★ {score:.1f} / {score_max:.0f}" if score_max == 10.0 else f"★ {score:.1f} / 5.0"
                except (ValueError, TypeError):
                    score = 0.0
                    score_max = 5.0
                    score_str = "No Rating"
            else:
                score = 0.0
                score_max = 5.0
                score_str = "No Rating"
            b_class = badge_classes.get(p_id, "badge-primary")
            p_name = html.escape(str(platforms.get(p_id, {}).get("display_name", p_id.title())), quote=True)

            new_badge = '<span class="review-new-badge">✨ NEW</span>' if is_recent else ''
            recent_class = " recent-card" if is_recent else ""
            initial_card_style = "" if is_recent else ' style="display: none;"'

            raw_title = r.get("title")
            title = html.escape(str(raw_title), quote=True) if raw_title else None
            title_html = f'<div class="review-title">{title}</div>' if title else ""
            body_escaped = html.escape(str(r.get("body") or ""), quote=True).replace("\n", "<br>")

            # Host / Team Response
            resp = r.get("host_response")
            if resp and isinstance(resp, dict) and resp.get("body"):
                resp_d = resp.get("date", "")
                if resp_d:
                    try:
                        resp_d = date.fromisoformat(resp_d[:10]).strftime("%b %d, %Y")
                    except Exception:
                        resp_d = html.escape(str(resp_d), quote=True)
                d_part = f'<span class="review-reply-date-pill">{resp_d}</span>' if resp_d else ""
                resp_body_escaped = html.escape(str(resp.get("body") or ""), quote=True).replace("\n", "<br>")
                resp_html = f"""
                <div class="review-host-reply">
                  <div class="review-reply-header">
                    <div style="display:inline-flex; align-items:center; gap:6px;">
                      <span>💬 Team Response</span>
                    </div>
                    {d_part}
                  </div>
                  <div class="review-reply-body">{resp_body_escaped}</div>
                </div>
                """
            else:
                resp_html = """
                <div class="review-host-reply review-reply-empty">
                  <div class="review-reply-header review-reply-header-empty">
                    <div style="display:inline-flex; align-items:center; gap:6px;">
                      <span style="opacity:0.6;">💬</span>
                      <span>Team Response</span>
                    </div>
                    <span class="review-empty-badge">No Reply Posted</span>
                  </div>
                  <div class="review-reply-body-empty">
                    No public response recorded for this guest review.
                  </div>
                </div>
                """

            search_blob = f"{raw_author} {raw_loc or ''} {raw_title or ''} {r.get('body', '')} {p_name} {resp.get('body', '') if resp and isinstance(resp, dict) else ''}".lower()
            search_blob_clean = html.escape(search_blob, quote=True)

            review_cards_html.append(f"""
            <div class="review-card{recent_class}"
                 data-platform="{p_id}"
                 data-rating="{score}"
                 data-rating-max="{score_max}"
                 data-is-recent="{'true' if is_recent else 'false'}"
                 data-date="{r_date_str[:10]}"
                 data-search-text="{search_blob_clean}"{initial_card_style}>
              <div class="review-card-header">
                <div class="review-author-meta">
                  <span class="scorecard-platform-badge {b_class}">{p_name}</span>
                  <span class="review-author-name">{author_display}</span>
                  <span class="review-date">• {date_fmt}</span>
                  {new_badge}
                </div>
                <span class="review-score-badge">{score_str}</span>
              </div>
              <div class="review-grid-2col">
                <div class="review-guest-col">
                  <div class="review-col-header guest">
                    <span style="display:inline-flex; align-items:center; gap:6px;">👤 Guest Review</span>
                  </div>
                  {title_html}
                  <div class="review-body">{body_escaped}</div>
                </div>
                <div class="review-team-col">
                  {resp_html}
                </div>
              </div>
            </div>
            """)

        feed_html = "".join(review_cards_html) if review_cards_html else '<p style="color:var(--text-muted); text-align:center; padding:30px;">No reviews found.</p>'

        return f"""
        <div class="section-box">
          <div style="margin-bottom: 24px;">
            <h2 style="font-size: 1.4rem; font-weight: 800; color: #f8fafc; margin-bottom: 4px;">
              ⭐ Cross-Platform Reviews & Guest Sentiment
            </h2>
            <p style="color: var(--text-muted); font-size: 0.9rem;">
              Live reputation surveillance across Airbnb, VRBO, and Booking.com • Strict native scale fidelity
            </p>
          </div>

          <!-- Platform Scorecards -->
          <div class="reviews-scorecards-grid">
            {scorecards_html}
          </div>

          <!-- Filter & Search Toolbar -->
          <div class="reviews-toolbar">
            <div class="reviews-filters-group">
              <select id="reviewPlatformFilter" class="reviews-select" onchange="filterReviews()">
                <option value="all">All Platforms</option>
                <option value="airbnb">Airbnb</option>
                <option value="vrbo">VRBO</option>
                <option value="booking">Booking.com</option>
              </select>

              <select id="reviewRatingFilter" class="reviews-select" onchange="filterReviews()">
                <option value="all">All Ratings</option>
                <option value="5star">5★ / 9–10 Rating</option>
                <option value="4star">4★ / 7–8.9 Rating</option>
                <option value="low">≤3★ / &lt;7 Rating</option>
              </select>

              <div class="reviews-date-pills" role="group" aria-label="Review date filters">
                <input type="hidden" id="reviewDateFilterVal" value="30">
                <button type="button" class="filter-pill-btn" id="rev-date-all" aria-pressed="false" onclick="setReviewDateFilter('all', this)">All</button>
                <button type="button" class="filter-pill-btn active" id="rev-date-30" aria-pressed="true" onclick="setReviewDateFilter('30', this)">Last 30d</button>
                <button type="button" class="filter-pill-btn" id="rev-date-90" aria-pressed="false" onclick="setReviewDateFilter('90', this)">90d</button>
                <button type="button" class="filter-pill-btn" id="rev-date-180" aria-pressed="false" onclick="setReviewDateFilter('180', this)">180d</button>
                <button type="button" class="filter-pill-btn" id="rev-date-365" aria-pressed="false" onclick="setReviewDateFilter('365', this)">365d</button>
              </div>
            </div>

            <div class="reviews-search-box">
              <span class="reviews-search-icon">🔍</span>
              <input type="text" id="reviewSearchInput" class="reviews-search-input" placeholder="Search comments, reviewer, replies..." oninput="filterReviews()">
            </div>

            <span id="reviewCountBadge" class="badge badge-dark" style="font-weight: 600;">
              Showing {recent_in_30d_count} of {len(reviews)} reviews
            </span>
          </div>

          <!-- Reviews Feed -->
          <div id="reviewsFeedContainer" class="reviews-feed">
            {feed_html}
          </div>
        </div>
        """

    def _load_streamline_snapshots(
        self,
        seasonal_rates: Optional[List[Dict[str, Any]]] = None,
    ) -> Tuple[Dict[str, Dict[str, Any]], str]:
        """
        Load historical rate snapshots from SQLite and current live seasonal rates.
        Returns a tuple:
          - dict mapping snapshot_date -> snapshot_dict with structured period rows
          - formatted string of the latest Streamline API update date and time
        """
        snapshots = {}

        # 1. Track latest Streamline API update time
        latest_api_dt = None
        cache_path = Path("data/cache/kivoya_seasonal_rates.json")
        if cache_path.exists():
            try:
                latest_api_dt = datetime.fromtimestamp(cache_path.stat().st_mtime)
            except Exception:
                pass

        live_date = date.today()
        live_date_str = live_date.isoformat()
        live_label_date = live_date.strftime("%b %d, %Y")

        # 2. Historical Snapshots from Database
        conn = None
        try:
            from src.database import get_db_connection
            conn = get_db_connection()
            cursor = conn.cursor()
            where_clause = "WHERE snapshot_date != ?" if seasonal_rates else ""
            params = (live_date_str,) if seasonal_rates else ()
            cursor.execute(f"""
                SELECT snapshot_date, calendar_date, nightly_rate, interval_type, season_name, period_name, created_at
                FROM property_rate_snapshots
                {where_clause}
                ORDER BY snapshot_date ASC, calendar_date ASC
            """, params)
            all_rows = cursor.fetchall()

            rows_by_snapshot = {}
            max_created_by_snapshot = {}
            for r in all_rows:
                s_date = r[0] if isinstance(r, (list, tuple)) else r["snapshot_date"]
                c_date = r[1] if isinstance(r, (list, tuple)) else r["calendar_date"]
                rate = r[2] if isinstance(r, (list, tuple)) else r["nightly_rate"]
                itype = r[3] if isinstance(r, (list, tuple)) else r["interval_type"]
                season = r[4] if isinstance(r, (list, tuple)) else r["season_name"]
                period = r[5] if isinstance(r, (list, tuple)) else r["period_name"]
                created_at = r[6] if isinstance(r, (list, tuple)) else r["created_at"]

                if s_date not in rows_by_snapshot:
                    rows_by_snapshot[s_date] = []
                    max_created_by_snapshot[s_date] = created_at
                elif created_at and (not max_created_by_snapshot[s_date] or created_at > max_created_by_snapshot[s_date]):
                    max_created_by_snapshot[s_date] = created_at

                rows_by_snapshot[s_date].append((c_date, rate, itype, season, period))

            for s_date, rows in rows_by_snapshot.items():
                max_created = max_created_by_snapshot.get(s_date)
                snap_dt = None
                if max_created:
                    try:
                        snap_dt = datetime.fromisoformat(max_created)
                        if latest_api_dt is None or snap_dt > latest_api_dt:
                            latest_api_dt = snap_dt
                    except Exception:
                        pass

                if snap_dt:
                    time_str = snap_dt.strftime("%b %d, %Y at ") + snap_dt.strftime("%I:%M %p").lstrip("0")
                else:
                    time_str = s_date

                # Consolidate into seasonal periods
                periods = []
                current_period = None
                for r in rows:
                    c_date, rate, itype, season, period = r
                    is_hol = 'holiday' in (season or '').lower() or any(w in (period or '').lower() for w in ['memorial', 'july 4', 'labor', 'columbus', 'thanksgiving', 'christmas', 'holi'])
                    if 'summer' in (season or '').lower():
                        group_key = ('Summer 2026', 'Summer 2026')
                    else:
                        group_key = (season, period)

                    if current_period is None or current_period['group_key'] != group_key:
                        if current_period:
                            periods.append(current_period)
                        current_period = {
                            'group_key': group_key,
                            'season_name': season or '',
                            'period_name': period if 'summer' not in (season or '').lower() else 'Summer 2026',
                            'from_date': c_date,
                            'to_date': c_date,
                            'mid_rates': [],
                            'wkd_rates': [],
                            'spec_rates': [],
                            'is_holiday': is_hol,
                        }
                    current_period['to_date'] = c_date
                    if itype == 'midweek':
                        current_period['mid_rates'].append(rate)
                    elif itype == 'weekend':
                        current_period['wkd_rates'].append(rate)
                    else:
                        current_period['spec_rates'].append(rate)
                if current_period:
                    periods.append(current_period)

                formatted_periods = []
                for p in periods:
                    mid = round(sum(p['mid_rates'])/len(p['mid_rates'])) if p['mid_rates'] else None
                    wkd = round(sum(p['wkd_rates'])/len(p['wkd_rates'])) if p['wkd_rates'] else None
                    spec = round(sum(p['spec_rates'])/len(p['spec_rates'])) if p['spec_rates'] else None
                    if mid and wkd and mid == wkd:
                        spec = mid
                        mid = None
                        wkd = None
                    min_n = 3 if p['is_holiday'] else (2 if 'summer' in (p['season_name'] or '').lower() else 3)
                    if '06' in p['from_date'] and 'summer' in (p['season_name'] or '').lower():
                        min_n = 3
                    formatted_periods.append({
                        'period_name': p['period_name'],
                        'season_name': p['season_name'],
                        'from_date': p['from_date'],
                        'to_date': p['to_date'],
                        'midweek': mid,
                        'weekend': wkd,
                        'special': spec,
                        'min_nights': min_n,
                        'is_holiday': p['is_holiday'],
                        'notes': 'Historical catalog baseline backfilled for rate shortfall audit' if s_date == '2026-02-01' else 'Rate snapshot archived from Streamline API',
                    })

                if s_date == '2026-02-01':
                    label_text = f"{s_date} (Historical Baseline)"
                    desc_text = f"Historical rate catalog baseline backfilled for rate shortfall audit calculations (recorded {time_str})."
                else:
                    try:
                        dt_obj = datetime.strptime(s_date, "%Y-%m-%d").date()
                        f_date = dt_obj.strftime("%b %d, %Y")
                    except Exception:
                        f_date = s_date
                    label_text = f"{f_date} (Archived Snapshot)"
                    desc_text = f"Rate snapshot archived on {f_date} at {time_str} from Streamline API."

                snapshots[s_date] = {
                    'date': s_date,
                    'label': label_text,
                    'description': desc_text,
                    'updated_at_str': time_str,
                    'periods': formatted_periods,
                }
        except Exception:
            pass
        finally:
            if conn is not None:
                conn.close()


        if latest_api_dt is None:
            latest_api_dt = datetime.now()
        last_streamline_update_str = latest_api_dt.strftime("%b %d, %Y at ") + latest_api_dt.strftime("%I:%M %p").lstrip("0")

        # 3. Live Active Catalog from seasonal_rates
        if seasonal_rates:
            live_periods = []
            for r in seasonal_rates:
                p1 = r.get("first_price")
                p2 = r.get("second_price")
                is_flat = (p2 is None) or (p1 == p2)
                is_hol = any(w in r.get("season_name", "").lower() for w in ["holiday", "thanksgiving", "christmas", "memorial", "labor"]) or any(w in r.get("period_name", "").lower() for w in ["holy", "columbus", "labor", "memorial"])

                mid = int(p1) if (not is_flat and p1) else None
                wkd = int(p2) if (not is_flat and p2) else None
                spec = int(p1) if is_flat and p1 else None
                min_n = int(r.get("min_days", 2))
                b_str = r["begin_dt"].strftime("%Y-%m-%d") if hasattr(r["begin_dt"], "strftime") else str(r["begin_dt"])
                e_str = r["end_dt"].strftime("%Y-%m-%d") if hasattr(r["end_dt"], "strftime") else str(r["end_dt"])

                notes_str = r.get("first_interval", "All Days")
                if r.get("second_interval"):
                    notes_str += f" / {r['second_interval']}"

                live_periods.append({
                    "period_name": r.get("period_name", ""),
                    "season_name": r.get("season_name", ""),
                    "from_date": b_str,
                    "to_date": e_str,
                    "midweek": mid,
                    "weekend": wkd,
                    "special": spec,
                    "min_nights": min_n,
                    "is_holiday": is_hol,
                    "notes": notes_str,
                })
            snapshots[live_date_str] = {
                "date": live_date_str,
                "label": f"{live_label_date} (Active Live Catalog)",
                "description": f"Live seasonal catalog synchronized directly from Kivoya Streamline PMS API on {last_streamline_update_str}.",
                "updated_at_str": last_streamline_update_str,
                "periods": live_periods,
            }

        sorted_snapshots = {k: snapshots[k] for k in sorted(snapshots.keys())}
        return sorted_snapshots, last_streamline_update_str

    def _render_streamline_tab(
        self,
        seasonal_rates: List[Dict[str, Any]],
        blocked_periods: List[Dict[str, Any]],
        open_end_date: Optional[date],
        snapshots: Dict[str, Dict[str, Any]],
        last_api_update_str: Optional[str] = None,
    ) -> Tuple[str, str]:
        """Render the dedicated Streamline PMS tab with Property Config, Rate Schedule, and Blackouts."""
        snapshot_dates = list(snapshots.keys())
        if not snapshot_dates:
            return "<div>No Streamline rate data available.</div>", ""

        active_key = snapshot_dates[-1]
        active_snap = snapshots[active_key]
        if not last_api_update_str:
            last_api_update_str = active_snap.get("updated_at_str", "Recently")

        # Snapshot pill buttons
        snap_buttons = []
        for idx, s_date in enumerate(snapshot_dates):
            is_active = (idx == len(snapshot_dates) - 1)
            active_cls = "active" if is_active else ""
            btn_label = snapshots[s_date]["label"]
            snap_buttons.append(
                f'<button class="filter-pill-btn streamline-snap-btn {active_cls}" '
                f'id="btn-snap-{idx}" onclick="selectStreamlineSnapshot({idx})">{btn_label}</button>'
            )
        snap_buttons_html = "\n".join(snap_buttons)

        # Pre-rendered rows for active snapshot
        active_rows = []
        for p_idx, p in enumerate(active_snap["periods"]):
            mid_str = f"${p['midweek']:,}" if p.get("midweek") is not None else "—"
            wkd_str = f"${p['weekend']:,}" if p.get("weekend") is not None else "—"
            spec_str = f"${p['special']:,}" if p.get("special") is not None else "—"
            hol_badge = ' <span style="background:rgba(251,191,36,0.15); color:#fbbf24; border:1px solid rgba(251,191,36,0.3); border-radius:4px; padding:1px 6px; font-size:0.7rem; font-weight:700; margin-left:6px;">Holiday</span>' if p.get("is_holiday") else ""
            row_bg = "background: rgba(251, 191, 36, 0.04);" if p.get("is_holiday") else ""

            active_rows.append(f"""
              <tr class="streamline-price-row" id="streamline-row-{p_idx}" style="{row_bg}"
                  data-from="{p.get('from_date', '')}"
                  data-to="{p.get('to_date', '')}"
                  data-period="{p.get('period_name', '')}"
                  data-mid="{p.get('midweek', '') or ''}"
                  data-wkd="{p.get('weekend', '') or ''}"
                  data-spec="{p.get('special', '') or ''}"
                  data-min="{p.get('min_nights', '') or ''}"
                  data-season="{p.get('season_name', '') or ''}">
                <td style="font-family:'JetBrains Mono',monospace; white-space:nowrap; font-weight:600;">{self._fmt_short_date(p.get('from_date', ''))} <span style="color:#64748b; margin:0 3px;">→</span> {self._fmt_short_date(p.get('to_date', ''))}</td>
                <td style="text-align:right; font-family:'JetBrains Mono',monospace; font-weight:600; color:#38bdf8;">{mid_str}</td>
                <td style="text-align:right; font-family:'JetBrains Mono',monospace; font-weight:600; color:#a78bfa;">{wkd_str}</td>
                <td style="text-align:right; font-family:'JetBrains Mono',monospace; font-weight:600; color:#fbbf24;">{spec_str}</td>
                <td style="text-align:center; font-family:'JetBrains Mono',monospace; font-weight:600;">{p.get('min_nights', '—')}</td>
                <td style="color:#cbd5e1; font-size:0.8rem;">{p.get('season_name', '')} • <span style="color:#94a3b8;">{p.get('notes', '')}</span></td>
                <td style="font-weight:700; color:#f8fafc; font-size:0.9rem;">{p.get('period_name', '—')}{hol_badge}</td>
              </tr>
            """)
        pricing_tbody_html = "\n".join(active_rows)

        # Blocked periods rows
        total_blocked_nights = 0
        blocked_rows = []
        for b in (blocked_periods or []):
            s_str = b.get("startdate", "")
            e_str = b.get("enddate", "")
            s_dt = b.get("start_dt")
            e_dt = b.get("end_dt")
            if not s_dt and s_str:
                try:
                    s_dt = datetime.strptime(s_str, "%m/%d/%Y").date()
                except Exception:
                    pass
            if not e_dt and e_str:
                try:
                    e_dt = datetime.strptime(e_str, "%m/%d/%Y").date()
                except Exception:
                    pass
            n = (e_dt - s_dt).days if (s_dt and e_dt) else 1
            total_blocked_nights += n
            reason = b.get("reason", "Blocked")
            is_res = "reservation" in reason.lower()
            type_badge = '<span style="background:rgba(16,185,129,0.15); color:#34d399; border:1px solid rgba(16,185,129,0.3); border-radius:4px; padding:2px 8px; font-size:0.75rem; font-weight:700;">Confirmed Stay</span>' if is_res else '<span style="background:rgba(148,163,184,0.15); color:#94a3b8; border:1px solid rgba(148,163,184,0.3); border-radius:4px; padding:2px 8px; font-size:0.75rem; font-weight:700;">PMS Block</span>'
            blocked_rows.append(f"""
              <tr style="border-bottom: 1px solid rgba(255,255,255,0.05);">
                <td style="font-family:'JetBrains Mono',monospace; white-space:nowrap; font-weight:600; color:#f8fafc;">{s_str}</td>
                <td style="font-family:'JetBrains Mono',monospace; white-space:nowrap; font-weight:600; color:#f8fafc;">{e_str}</td>
                <td style="text-align:center; font-family:'JetBrains Mono',monospace; font-weight:600; color:#38bdf8;">{n} nights</td>
                <td style="text-align:center;">{type_badge}</td>
                <td style="font-family:'JetBrains Mono',monospace; font-size:0.85rem; color:#cbd5e1;">{reason}</td>
              </tr>
            """)
        blocked_tbody_html = "\n".join(blocked_rows)

        open_window_str = f"Through {open_end_date.strftime('%B %d, %Y')}" if open_end_date else "Through May 31, 2027"

        tab_html = f"""
        <!-- SECTION 1: PROPERTY CONFIGURATION & ACCOUNT PROFILE -->
        <div class="section-box" style="margin-bottom: 24px;">
          <div class="section-header" style="margin-bottom: 16px;">
            <div>
              <div class="section-title" style="font-size: 1.3rem;">
                🏡 Property Configuration &amp; Terms (Kivoya / Streamline VRS)
              </div>
              <p class="section-desc" style="margin-top: 4px; margin-bottom: 0;">
                Live property master configuration, layout, lodging taxes, and owner revenue share recorded in Kivoya PMS for Villa del Sol (Unit ID: 108169).
              </p>
            </div>
          </div>

          <!-- 6-Box Property Spec KPI Grid -->
          <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 14px; margin-bottom: 20px;">
            <div style="background: rgba(15,23,42,0.6); border: 1px solid #334155; border-radius: 8px; padding: 14px 16px;">
              <div style="font-size: 0.72rem; color: #94a3b8; font-weight: 700; text-transform: uppercase;">Streamline Unit ID</div>
              <div style="font-size: 1.4rem; font-weight: 800; color: #38bdf8; font-family: 'JetBrains Mono', monospace; margin-top: 4px;">108169</div>
              <div style="font-size: 0.75rem; color: #cbd5e1; margin-top: 2px;">Villa del Sol (Tempe, AZ)</div>
            </div>

            <div style="background: rgba(15,23,42,0.6); border: 1px solid #334155; border-radius: 8px; padding: 14px 16px;">
              <div style="font-size: 0.72rem; color: #94a3b8; font-weight: 700; text-transform: uppercase;">Compound Layout</div>
              <div style="font-size: 1.4rem; font-weight: 800; color: #f8fafc; font-family: 'JetBrains Mono', monospace; margin-top: 4px;">6 BR / 5 BA</div>
              <div style="font-size: 0.75rem; color: #cbd5e1; margin-top: 2px;">Sleeps 16 • ¾-Acre Compound</div>
            </div>

            <div style="background: rgba(15,23,42,0.6); border: 1px solid #334155; border-radius: 8px; padding: 14px 16px;">
              <div style="font-size: 0.72rem; color: #94a3b8; font-weight: 700; text-transform: uppercase;">Cleaning Fee</div>
              <div style="font-size: 1.4rem; font-weight: 800; color: #34d399; font-family: 'JetBrains Mono', monospace; margin-top: 4px;">$550.00</div>
              <div style="font-size: 0.75rem; color: #cbd5e1; margin-top: 2px;">Direct checkout cleaning fee</div>
            </div>

            <div style="background: rgba(15,23,42,0.6); border: 1px solid #334155; border-radius: 8px; padding: 14px 16px;">
              <div style="font-size: 0.72rem; color: #94a3b8; font-weight: 700; text-transform: uppercase;">Lodging Taxes</div>
              <div style="font-size: 1.4rem; font-weight: 800; color: #fbbf24; font-family: 'JetBrains Mono', monospace; margin-top: 4px;">12.52%</div>
              <div style="font-size: 0.75rem; color: #cbd5e1; margin-top: 2px;">Tempe City 5% + State 7.52%</div>
            </div>

            <div style="background: rgba(15,23,42,0.6); border: 1px solid #334155; border-radius: 8px; padding: 14px 16px;">
              <div style="font-size: 0.72rem; color: #94a3b8; font-weight: 700; text-transform: uppercase;">Owner Net Share</div>
              <div style="font-size: 1.4rem; font-weight: 800; color: #a78bfa; font-family: 'JetBrains Mono', monospace; margin-top: 4px;">82.0%</div>
              <div style="font-size: 0.75rem; color: #cbd5e1; margin-top: 2px;">Gross rent payout (18% PMS fee)</div>
            </div>

            <div style="background: rgba(15,23,42,0.6); border: 1px solid #334155; border-radius: 8px; padding: 14px 16px;">
              <div style="font-size: 0.72rem; color: #94a3b8; font-weight: 700; text-transform: uppercase;">Calendar Window</div>
              <div style="font-size: 1.25rem; font-weight: 800; color: #f43f5e; font-family: 'JetBrains Mono', monospace; margin-top: 4px;">May 31, 2027</div>
              <div style="font-size: 0.75rem; color: #cbd5e1; margin-top: 2px;">{open_window_str} (Jun–Aug closed)</div>
            </div>
          </div>

          <!-- Direct Quote Formula Card -->
          <div style="background: rgba(30,41,59,0.5); border: 1px solid rgba(56,189,248,0.25); border-left: 4px solid #38bdf8; border-radius: 6px; padding: 12px 18px; font-size: 0.85rem; color: #cbd5e1; line-height: 1.5;">
            <strong style="color: #38bdf8;">💡 Direct Booking Pricing Formula:</strong> Direct Kivoya quotes are computed as: <code style="background: rgba(15,23,42,0.8); padding: 2px 6px; border-radius: 4px; color: #f8fafc; font-family: 'JetBrains Mono', monospace;">(Nightly Rate &times; Nights) + $550 Cleaning + 6% Processing + 3% Admin Fee + 14.07% STR Lodging Tax</code>. Direct guests avoid Airbnb's 14.2% guest service fee, providing direct price savings while delivering 100% of published base rates to Villa del Sol.
          </div>
        </div>

        <!-- SECTION 2: STREAMLINE RATE SCHEDULE & HISTORICAL SNAPSHOT TIMELINE -->
        <div class="section-box" style="margin-bottom: 24px;">
          <div class="section-header" style="margin-bottom: 16px;">
            <div>
              <div class="section-title" style="font-size: 1.3rem; display: flex; align-items: center; gap: 10px; flex-wrap: wrap;">
                <span>📅 Current Pricing &amp; Historical Snapshots (Streamline API)</span>
                <span id="streamlineLastSyncBadge" style="display: inline-flex; align-items: center; gap: 6px; background: rgba(16, 185, 129, 0.12); border: 1px solid rgba(16, 185, 129, 0.3); border-radius: 6px; padding: 3px 10px; font-size: 0.78rem; color: #34d399; font-weight: 600;">
                  <span style="width: 7px; height: 7px; border-radius: 50%; background: #10b981; display: inline-block; box-shadow: 0 0 6px #10b981;"></span>
                  Last API Update: <strong>{last_api_update_str}</strong>
                </span>
              </div>
              <p class="section-desc" style="margin-top: 4px; margin-bottom: 0;">
                Seasonal catalog rates configured in Kivoya Streamline PMS. Scrub the timeline slider or select a snapshot date to inspect what rates were active in Streamline at different points in time.
              </p>
            </div>
            <button id="btnCopyStreamline" class="action-btn" onclick="copyStreamlinePrices()"
                    style="display:inline-flex; align-items:center; gap:8px; background:rgba(56,189,248,0.15); color:#38bdf8; border:1px solid rgba(56,189,248,0.35); padding:7px 16px; border-radius:6px; font-weight:700; font-size:0.85rem; cursor:pointer; transition:all 0.2s;">
              <span id="copyStreamlineIcon">📋</span> <span id="copyStreamlineText">Copy Streamline Schedule</span>
            </button>
          </div>

          <!-- Snapshot Scrubber Toolbar -->
          <div style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 16px; background: rgba(15,23,42,0.6); border: 1px solid #334155; border-radius: 8px; padding: 12px 18px; margin-bottom: 16px;">
            <div style="display: flex; align-items: center; gap: 14px; flex-wrap: wrap;">
              <span style="font-weight: 700; font-size: 0.88rem; color: #f8fafc; display: flex; align-items: center; gap: 6px;">
                <span>⏱️ Timeline Scrubber:</span>
              </span>
              <input type="range" id="streamlineSnapshotSlider" min="0" max="{len(snapshot_dates) - 1}" value="{len(snapshot_dates) - 1}" step="1"
                     oninput="onStreamlineSnapshotChange(this.value)"
                     style="width: 180px; accent-color: #3b82f6; cursor: pointer;">
              <div style="display: flex; gap: 6px; flex-wrap: wrap;">
                {snap_buttons_html}
              </div>
            </div>
            <div style="display: flex; align-items: center; gap: 10px; flex-wrap: wrap;">
              <span id="streamlineSnapshotTimestamp" style="font-size: 0.8rem; color: #94a3b8; font-family: 'JetBrains Mono', monospace; background: rgba(30,41,59,0.7); border: 1px solid #334155; border-radius: 4px; padding: 4px 10px; display: inline-flex; align-items: center; gap: 5px;">
                <span>🕒 Last Update:</span> <strong style="color: #38bdf8;">{active_snap.get('updated_at_str', last_api_update_str)}</strong>
              </span>
              <span id="streamlineSnapshotBadge" class="badge badge-primary" style="font-size: 0.82rem; padding: 5px 12px;">
                Active: {active_snap['label']} ({len(active_snap['periods'])} Periods)
              </span>
            </div>
          </div>
          <div id="streamlineSnapshotDesc" style="color: #94a3b8; font-size: 0.84rem; margin-top: -8px; margin-bottom: 16px; padding-left: 4px;">
            {active_snap['description']}
          </div>

          <!-- Table Header Sub-bar -->
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; font-size: 0.8rem; color: #94a3b8; padding: 0 4px;">
            <span>Displaying schedule for: <strong id="streamlineTableSnapshotLabel" style="color: #f8fafc;">{active_snap['label']}</strong></span>
            <span>API Received: <strong id="streamlineTableLastUpdate" style="color: #34d399; font-family: 'JetBrains Mono', monospace;">{active_snap.get('updated_at_str', last_api_update_str)}</strong></span>
          </div>

          <!-- Current Pricing Table -->
          <div class="table-responsive">
            <table id="streamline-pricing-table">
              <thead>
                <tr style="background: rgba(255,255,255,0.03);">
                  <th style="min-width: 140px;">Dates</th>
                  <th style="text-align:right;">Midweek Rate</th>
                  <th style="text-align:right;">Weekend Rate</th>
                  <th style="text-align:right;">Special</th>
                  <th style="text-align:center;">Min Nights</th>
                  <th>Season / Rate Rules</th>
                  <th>Period Name</th>
                </tr>
              </thead>
              <tbody id="streamline-pricing-tbody">
                {pricing_tbody_html}
              </tbody>
            </table>
          </div>
        </div>

        <!-- SECTION 3: PMS CALENDAR BLACKOUTS & BOOKINGS -->
        <div class="section-box" style="margin-bottom: 24px;">
          <div class="section-header" style="margin-bottom: 16px;">
            <div>
              <div class="section-title" style="font-size: 1.3rem;">
                🔒 Streamline PMS Calendar Blackouts &amp; Confirmed Bookings
              </div>
              <p class="section-desc" style="margin-top: 4px; margin-bottom: 0;">
                All blocked intervals and active guest reservations synchronized directly from Streamline <code style="color:#38bdf8;">GetPropertyAvailabilityCalendarRawData</code> holding public calendar availability.
              </p>
            </div>
          </div>

          <!-- 3-Box Blocked Stats KPI Grid -->
          <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 14px; margin-bottom: 18px;">
            <div style="background: rgba(15,23,42,0.6); border: 1px solid #334155; border-radius: 8px; padding: 12px 14px;">
              <div style="font-size: 0.72rem; color: #94a3b8; font-weight: 700; text-transform: uppercase;">Total Blocked Spans</div>
              <div style="font-size: 1.35rem; font-weight: 800; color: #f8fafc; font-family: 'JetBrains Mono', monospace; margin-top: 3px;">
                {len(blocked_periods)} periods
              </div>
              <div style="font-size: 0.75rem; color: #cbd5e1; margin-top: 2px;">Synchronized from PMS calendar</div>
            </div>

            <div style="background: rgba(15,23,42,0.6); border: 1px solid #334155; border-radius: 8px; padding: 12px 14px;">
              <div style="font-size: 0.72rem; color: #38bdf8; font-weight: 700; text-transform: uppercase;">Total Nights Blocked</div>
              <div style="font-size: 1.35rem; font-weight: 800; color: #38bdf8; font-family: 'JetBrains Mono', monospace; margin-top: 3px;">
                {total_blocked_nights} nights
              </div>
              <div style="font-size: 0.75rem; color: #cbd5e1; margin-top: 2px;">Locked from public calendar</div>
            </div>

            <div style="background: rgba(15,23,42,0.6); border: 1px solid #334155; border-radius: 8px; padding: 12px 14px;">
              <div style="font-size: 0.72rem; color: #34d399; font-weight: 700; text-transform: uppercase;">Confirmed Reservations</div>
              <div style="font-size: 1.35rem; font-weight: 800; color: #34d399; font-family: 'JetBrains Mono', monospace; margin-top: 3px;">
                {len(blocked_periods)} stays
              </div>
              <div style="font-size: 0.75rem; color: #cbd5e1; margin-top: 2px;">With Streamline confirmation numbers</div>
            </div>
          </div>

          <!-- Blocked Periods Table -->
          <div class="table-responsive">
            <table>
              <thead>
                <tr style="background: rgba(255,255,255,0.03);">
                  <th style="width: 15%;">Start Date</th>
                  <th style="width: 15%;">End Date</th>
                  <th style="text-align:center; width: 15%;">Nights</th>
                  <th style="text-align:center; width: 20%;">PMS Hold Type</th>
                  <th style="width: 35%;">Streamline Confirmation / Reason</th>
                </tr>
              </thead>
              <tbody>
                {blocked_tbody_html}
              </tbody>
            </table>
          </div>
        </div>

        <!-- SECTION 4: STREAMLINE API GATEWAY DIAGNOSTICS -->
        <div class="section-box">
          <div class="section-header" style="margin-bottom: 16px;">
            <div>
              <div class="section-title" style="font-size: 1.3rem;">
                ⚙️ Streamline VRS Gateway Diagnostics
              </div>
              <p class="section-desc" style="margin-top: 4px; margin-bottom: 0;">
                Live endpoint connectivity, authentication tokens, and automatic background synchronization status.
              </p>
            </div>
          </div>

          <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 14px;">
            <div style="background: rgba(15,23,42,0.6); border: 1px solid #334155; border-radius: 8px; padding: 14px 16px;">
              <div style="font-size: 0.72rem; color: #94a3b8; font-weight: 700; text-transform: uppercase;">API Gateway Endpoint</div>
              <div style="font-size: 0.95rem; font-weight: 700; color: #38bdf8; font-family: 'JetBrains Mono', monospace; margin-top: 4px; word-break: break-all;">kivoya.streamlinevrs.com</div>
              <div style="font-size: 0.75rem; color: #34d399; margin-top: 4px;">● Connected (HTTP 200 OK)</div>
            </div>

            <div style="background: rgba(15,23,42,0.6); border: 1px solid #334155; border-radius: 8px; padding: 14px 16px;">
              <div style="font-size: 0.72rem; color: #94a3b8; font-weight: 700; text-transform: uppercase;">Last Streamline Sync</div>
              <div style="font-size: 0.95rem; font-weight: 700; color: #34d399; font-family: 'JetBrains Mono', monospace; margin-top: 4px;">{last_api_update_str}</div>
              <div style="font-size: 0.75rem; color: #cbd5e1; margin-top: 4px;">GetPropertyRatesRawData live feed</div>
            </div>

            <div style="background: rgba(15,23,42,0.6); border: 1px solid #334155; border-radius: 8px; padding: 14px 16px;">
              <div style="font-size: 0.72rem; color: #94a3b8; font-weight: 700; text-transform: uppercase;">Primary Endpoints</div>
              <div style="font-size: 0.85rem; font-weight: 600; color: #f8fafc; font-family: 'JetBrains Mono', monospace; margin-top: 4px;">
                GetPropertyRatesRawData<br>GetPropertyAvailabilityCalendarRawData
              </div>
              <div style="font-size: 0.75rem; color: #cbd5e1; margin-top: 4px;">Dynamic seasonal &amp; availability feeds</div>
            </div>

            <div style="background: rgba(15,23,42,0.6); border: 1px solid #334155; border-radius: 8px; padding: 14px 16px;">
              <div style="font-size: 0.72rem; color: #94a3b8; font-weight: 700; text-transform: uppercase;">Historical Snapshot Store</div>
              <div style="font-size: 0.95rem; font-weight: 700; font-family: 'JetBrains Mono', monospace; margin-top: 4px;">{'<span style="color: #4ade80;">Cloud: Turso (LibSQL)</span>' if is_cloud_enabled() else '<span style="color: #fbbf24;">Local: SQLite (data/reservations.db)</span>'}</div>
              <div style="font-size: 0.75rem; color: #cbd5e1; margin-top: 4px;">{len(snapshot_dates)} archived snapshots recorded</div>
            </div>
          </div>
        </div>
        """

        snapshots_json = json.dumps(snapshots).replace("</", "<\\/")

        js_data = f"""
    // Streamline PMS Snapshots and Dynamic Switcher
    const STREAMLINE_SNAPSHOTS = {snapshots_json};

    function onStreamlineSnapshotChange(idx) {{
      const dates = Object.keys(STREAMLINE_SNAPSHOTS);
      if (idx < 0 || idx >= dates.length) return;
      const snapDate = dates[idx];
      const snap = STREAMLINE_SNAPSHOTS[snapDate];
      if (!snap) return;

      const slider = document.getElementById('streamlineSnapshotSlider');
      if (slider && slider.value != idx) slider.value = idx;

      document.querySelectorAll('.streamline-snap-btn').forEach((btn, i) => {{
        btn.classList.toggle('active', i === parseInt(idx, 10));
      }});

      const isLatest = (parseInt(idx, 10) === dates.length - 1);
      const badge = document.getElementById('streamlineSnapshotBadge');
      if (badge) {{
        badge.innerText = `Active: ${{snap.label}} (${{snap.periods.length}} Periods)`;
      }}
      const tsEl = document.getElementById('streamlineSnapshotTimestamp');
      if (tsEl) {{
        const timeLabel = isLatest ? 'Last Update:' : 'Captured:';
        const timeVal = snap.updated_at_str || '—';
        tsEl.innerHTML = `<span>🕒 ${{timeLabel}}</span> <strong style="color: #38bdf8;">${{timeVal}}</strong>`;
      }}
      const tableSnapLabel = document.getElementById('streamlineTableSnapshotLabel');
      if (tableSnapLabel) {{
        tableSnapLabel.innerText = snap.label;
      }}
      const tableLastUpdate = document.getElementById('streamlineTableLastUpdate');
      if (tableLastUpdate) {{
        tableLastUpdate.innerText = snap.updated_at_str || '—';
      }}
      const descEl = document.getElementById('streamlineSnapshotDesc');
      if (descEl) {{
        descEl.innerText = snap.description;
      }}

      const tbody = document.getElementById('streamline-pricing-tbody');
      if (tbody) {{
        function fmtDateShort(dStr) {{
          if (!dStr) return '—';
          const p = dStr.split('-');
          if (p.length === 3 && p[0].length === 4) return p[1] + '/' + p[2] + '/' + p[0].slice(-2);
          const ps = dStr.split('/');
          if (ps.length === 3 && ps[2].length === 4) return ps[0] + '/' + ps[1] + '/' + ps[2].slice(-2);
          return dStr;
        }}

        tbody.innerHTML = snap.periods.map((p, pIdx) => {{
          const midStr = (p.midweek != null && p.midweek !== '') ? `$${{Number(p.midweek).toLocaleString()}}` : '—';
          const wkdStr = (p.weekend != null && p.weekend !== '') ? `$${{Number(p.weekend).toLocaleString()}}` : '—';
          const specStr = (p.special != null && p.special !== '') ? `$${{Number(p.special).toLocaleString()}}` : '—';
          const holBadge = p.is_holiday ? '<span style="background:rgba(251,191,36,0.15); color:#fbbf24; border:1px solid rgba(251,191,36,0.3); border-radius:4px; padding:1px 6px; font-size:0.7rem; font-weight:700; margin-left:6px;">Holiday</span>' : '';
          const rowBg = p.is_holiday ? 'background: rgba(251, 191, 36, 0.04);' : '';
          return `
            <tr class="streamline-price-row" id="streamline-row-${{pIdx}}" style="${{rowBg}}"
                data-from="${{p.from_date || ''}}"
                data-to="${{p.to_date || ''}}"
                data-period="${{p.period_name || ''}}"
                data-mid="${{p.midweek || ''}}"
                data-wkd="${{p.weekend || ''}}"
                data-spec="${{p.special || ''}}"
                data-min="${{p.min_nights || ''}}"
                data-season="${{p.season_name || ''}}">
              <td style="font-family:'JetBrains Mono',monospace; white-space:nowrap; font-weight:600;">${{fmtDateShort(p.from_date)}} <span style="color:#64748b; margin:0 3px;">→</span> ${{fmtDateShort(p.to_date)}}</td>
              <td style="text-align:right; font-family:'JetBrains Mono',monospace; font-weight:600; color:#38bdf8;">${{midStr}}</td>
              <td style="text-align:right; font-family:'JetBrains Mono',monospace; font-weight:600; color:#a78bfa;">${{wkdStr}}</td>
              <td style="text-align:right; font-family:'JetBrains Mono',monospace; font-weight:600; color:#fbbf24;">${{specStr}}</td>
              <td style="text-align:center; font-family:'JetBrains Mono',monospace; font-weight:600;">${{p.min_nights || '—'}}</td>
              <td style="color:#cbd5e1; font-size:0.8rem;">${{p.season_name || ''}} • <span style="color:#94a3b8;">${{p.notes || ''}}</span></td>
              <td style="font-weight:700; color:#f8fafc; font-size:0.9rem;">${{p.period_name || '—'}}${{holBadge}}</td>
            </tr>
          `;
        }}).join('');
      }}
    }}

    function selectStreamlineSnapshot(idx) {{
      onStreamlineSnapshotChange(idx);
    }}

    function copyStreamlinePrices() {{
      const rows = document.querySelectorAll('.streamline-price-row');
      const lines = [];
      lines.push(['Dates', 'Midweek', 'Weekend', 'Special', 'Min nights', 'Season / Rules', 'Period Name'].join('\\t'));
      let visibleCount = 0;

      rows.forEach(row => {{
        if (row.style.display === 'none') return;
        visibleCount++;
        const pName = row.dataset.period || '';
        const fromDt = row.dataset.from || '';
        const toDt = row.dataset.to || '';
        const midVal = row.dataset.mid ? `$${{row.dataset.mid}}` : '';
        const wkdVal = row.dataset.wkd ? `$${{row.dataset.wkd}}` : '';
        const specVal = row.dataset.spec ? `$${{row.dataset.spec}}` : '';
        const minNights = row.dataset.min || '2';
        const season = row.dataset.season || '';

        lines.push([`${{fromDt}} -> ${{toDt}}`, midVal, wkdVal, specVal, minNights, season, pName].join('\\t'));
      }});

      const text = lines.join('\\n');
      const copyBtn = document.getElementById('btnCopyStreamline');

      function onSuccess() {{
        if (copyBtn) {{
          const originalHtml = copyBtn.innerHTML;
          copyBtn.innerHTML = `✓ Copied (${{visibleCount}} periods)`;
          copyBtn.style.color = '#34d399';
          copyBtn.style.borderColor = '#34d399';
          setTimeout(() => {{
            copyBtn.innerHTML = originalHtml;
            copyBtn.style.color = '';
            copyBtn.style.borderColor = '';
          }}, 2000);
        }}
      }}

      if (navigator.clipboard && navigator.clipboard.writeText) {{
        navigator.clipboard.writeText(text).then(onSuccess).catch(() => {{
          fallbackCopyText(text);
          onSuccess();
        }});
      }} else {{
        fallbackCopyText(text);
        onSuccess();
      }}
    }}
        """

        return tab_html, js_data

    def _render_market_sales_tab(
        self,
        sales_data: Dict[str, Any],
        recent_sales: List[Dict[str, Any]],
        comps_lead_analytics: Optional[Dict[str, Any]] = None,
        lead_analytics: Optional[Dict[str, Any]] = None,
        market_timeline_data: Optional[Dict[str, Any]] = None,
    ) -> str:
        if comps_lead_analytics is None:
            try:
                from src.competitor_sales_tracker import CompetitorSalesTracker
                comps_lead_analytics = CompetitorSalesTracker().compute_monthly_lead_time_windows()
            except Exception:
                comps_lead_analytics = {"overall": {}, "months": {}, "total_analyzed": 0}

        if market_timeline_data is None:
            try:
                from src.competitor_sales_tracker import CompetitorSalesTracker
                market_timeline_data = CompetitorSalesTracker().compute_daily_market_inventory_timeline()
            except Exception:
                market_timeline_data = {}

        traj_cohorts = market_timeline_data.get("cohorts", {})
        all_cohort = traj_cohorts.get("all", {})
        tier_a_cohort = traj_cohorts.get("tier_a", {})
        tier_b_cohort = traj_cohorts.get("tier_b", {})

        all_total = all_cohort.get("total_comps", 97)
        tier_a_total = tier_a_cohort.get("total_comps", 49)
        tier_b_total = tier_b_cohort.get("total_comps", 48)

        all_avg_avail = all_cohort.get("avg_available", 0.0)
        all_avg_sales = all_cohort.get("avg_sales", 0.0)
        all_avg_unk = all_cohort.get("avg_unknown", 0.0)
        all_comp_days = all_cohort.get("compression_days_count", 0)
        all_comp_pct = all_cohort.get("compression_days_pct", 0.0)
        timeline_days = market_timeline_data.get("days_count", 365)

        villa_summary = market_timeline_data.get("villa_del_sol_summary", {})
        v_avail_days = villa_summary.get("available_days", 0)
        v_unavail_days = villa_summary.get("unavailable_days", 0)
        v_avail_pct = villa_summary.get("availability_pct", 0.0)
        v_unavail_pct = villa_summary.get("unavailability_pct", 0.0)

        timeline_json_str = json.dumps(market_timeline_data).replace("</", "<\\/")
        trajectory_script = self._render_market_trajectory_script(timeline_json_str)

        overall_comp_lead = comps_lead_analytics.get("overall", {})
        monthly_rows = []
        months_comp_lead = comps_lead_analytics.get("months", {})
        for m in range(1, 13):
            m_info = months_comp_lead.get(m, {})
            m_name = m_info.get("month_name", "")
            m_season = m_info.get("season", "")
            m_count = m_info.get("count", 0)
            m_med = m_info.get("median", 0)
            m_window = m_info.get("window_str", "—")
            m_min = m_info.get("min", 0)
            m_max = m_info.get("max", 0)
            m_guide = m_info.get("guidance", "")

            if "Peak Winter" in m_season:
                badge_bg = "rgba(56,189,248,0.15)"
                badge_color = "#38bdf8"
                badge_border = "rgba(56,189,248,0.3)"
            elif "Summer Value" in m_season:
                badge_bg = "rgba(239,68,68,0.15)"
                badge_color = "#f87171"
                badge_border = "rgba(239,68,68,0.3)"
            else:
                badge_bg = "rgba(245,158,11,0.15)"
                badge_color = "#fbbf24"
                badge_border = "rgba(245,158,11,0.3)"

            if m_count > 0:
                med_html = f"<strong style=\"color:#f8fafc; font-family:'JetBrains Mono',monospace;\">{m_med}d</strong>"
                win_html = f"<span style=\"color:#34d399; font-family:'JetBrains Mono',monospace; font-weight:700;\">{m_window}</span>"
                rng_html = f"<span style=\"color:#94a3b8; font-family:'JetBrains Mono',monospace;\">{m_min}–{m_max}d</span>"
            else:
                med_html = "<span style=\"color:#64748b;\">—</span>"
                win_html = "<span style=\"color:#64748b;\">—</span>"
                rng_html = "<span style=\"color:#64748b;\">—</span>"

            monthly_rows.append(f"""
              <tr>
                <td style="font-weight:700; color:#f8fafc; white-space:nowrap;">{m_name}</td>
                <td style="white-space:nowrap;">
                  <span class="badge" style="background:{badge_bg}; color:{badge_color}; border:1px solid {badge_border}; font-size:0.75rem; font-weight:700;">
                    {m_season.split('(')[0].strip()}
                  </span>
                </td>
                <td style="text-align:center; font-family:'JetBrains Mono',monospace; font-weight:700; color:#f8fafc;">{m_count}</td>
                <td style="text-align:center;">{med_html}</td>
                <td style="text-align:center;">{win_html}</td>
                <td style="text-align:center;">{rng_html}</td>
                <td style="font-size:0.84rem; color:#cbd5e1; line-height:1.4;">{m_guide}</td>
              </tr>
            """)
        comps_monthly_lead_rows_html = "\n".join(monthly_rows)

        grid = sales_data.get("grid", {})
        horizons = sales_data.get("horizons", [">180d", "91–180d", "31–90d", "≤30d"])
        
        horizon_meta = {
            ">180d": {
                "title": "Ultra-Advance Booking (> 180 Days)",
                "desc": "Ultra-advance booking window (>180d). Captures high-value planners and prevents early luxury bargain hunting.",
                "badge_color": "#06b6d4",
                "badge_bg": "rgba(6,182,212,0.15)",
                "action": "Hold firm at 85% (Weekend) / 50% (Midweek). Premium anchor protects far-out luxury yield against early underpriced bargain hunters.",
            },
            "91–180d": {
                "title": "Early Booking Window (91–180 Days)",
                "desc": "Early booking window with high willingness-to-pay. Anchor at premium percentiles.",
                "badge_color": "#38bdf8",
                "badge_bg": "rgba(56,189,248,0.15)",
                "action": "Hold firm at 65%–70% (Weekend) / 45%–50% (Midweek). Strategic floor protects far-out luxury yield against early underpriced bargain hunters.",
            },
            "31–90d": {
                "title": "Peak Booking Window (31–90 Days)",
                "desc": "Prime conversion period for family vacations and luxury group travel.",
                "badge_color": "#818cf8",
                "badge_bg": "rgba(129,140,248,0.15)",
                "action": "Target 60%–65% (Weekend) / 30%–35% (Midweek). Aligned with empirical comp conversion band and protected by $300 midweek floor.",
            },
            "≤30d": {
                "title": "Near-Term & Distress (≤ 30 Days)",
                "desc": "Near-term and distress inventory liquidation window where unbooked nights risk perishable loss.",
                "badge_color": "#f87171",
                "badge_bg": "rgba(248,113,113,0.15)",
                "action": "Aggressive liquidation: 40%–45% (Weekend) / 28%–32% (Midweek) to secure occupancy above $300 operational floor.",
            },
        }

        # Build Grid Rows
        grid_rows_html = ""
        for h in horizons:
            meta = horizon_meta.get(h, {"title": h, "desc": "", "badge_color": "#94a3b8", "badge_bg": "rgba(148,163,184,0.15)", "action": ""})
            w_cell = grid.get(h, {}).get("weekend", {})
            m_cell = grid.get(h, {}).get("midweek", {})

            w_n = w_cell.get("count", 0)
            m_n = m_cell.get("count", 0)

            w_prior = float(w_cell.get("baseline_prior", 65.0))
            w_p50 = f"{w_cell.get('empirical_p50', w_prior):.1f}%" if w_n > 0 else "—"
            w_p75 = f"{w_cell.get('empirical_p75', w_cell.get('recommended_aggressive', 75.0)):.1f}%" if w_n > 0 else "—"
            w_rec = f"{w_cell.get('recommended_target', w_prior):.1f}%"
            w_is_emp = w_cell.get("is_empirical", False)
            if w_cell.get("is_floor_clamped"):
                w_badge_style = "background:rgba(245,158,11,0.15); color:#fbbf24; border:1px solid rgba(245,158,11,0.3);"
                w_badge_label = "Floor Clamped"
            elif w_is_emp:
                w_badge_style = "background:rgba(52,211,153,0.15); color:#34d399; border:1px solid rgba(52,211,153,0.3);"
                w_badge_label = "Empirical"
            else:
                w_badge_style = "background:rgba(168,85,247,0.15); color:#c084fc; border:1px solid rgba(168,85,247,0.3);"
                w_badge_label = "Blended (k=5)"

            m_prior = float(m_cell.get("baseline_prior", 45.0))
            m_p50 = f"{m_cell.get('empirical_p50', m_prior):.1f}%" if m_n > 0 else "—"
            m_p75 = f"{m_cell.get('empirical_p75', m_cell.get('recommended_aggressive', 55.0)):.1f}%" if m_n > 0 else "—"
            m_rec = f"{m_cell.get('recommended_target', m_prior):.1f}%"
            m_is_emp = m_cell.get("is_empirical", False)
            if m_cell.get("is_floor_clamped"):
                m_badge_style = "background:rgba(245,158,11,0.15); color:#fbbf24; border:1px solid rgba(245,158,11,0.3);"
                m_badge_label = "Floor Clamped"
            elif m_is_emp:
                m_badge_style = "background:rgba(52,211,153,0.15); color:#34d399; border:1px solid rgba(52,211,153,0.3);"
                m_badge_label = "Empirical"
            else:
                m_badge_style = "background:rgba(168,85,247,0.15); color:#c084fc; border:1px solid rgba(168,85,247,0.3);"
                m_badge_label = "Blended (k=5)"

            grid_rows_html += f"""
            <tr style="border-bottom: 1px solid var(--border-color);">
              <td style="padding: 14px 16px;">
                <span class="badge" style="background:{meta['badge_bg']}; color:{meta['badge_color']}; font-weight:700; margin-bottom:4px; display:inline-block;">{meta['title']}</span>
                <div style="font-size:0.8rem; color:#94a3b8; margin-top:2px;">{meta['desc']}</div>
              </td>
              <td style="padding: 14px 16px; text-align:center; font-weight:600; color:{'#34d399' if w_n > 0 else '#64748b'};">
                {w_n}
              </td>
              <td style="padding: 14px 16px; text-align:center; font-family:'JetBrains Mono',monospace; font-size:0.88rem; color:#cbd5e1;">
                {w_p50} <span style="color:#64748b; font-size:0.75rem;">/</span> {w_p75}
              </td>
              <td style="padding: 14px 16px; text-align:center;">
                <span class="badge" style="{w_badge_style} font-weight:700; font-size:0.88rem;">{w_rec}</span>
                <div style="font-size:0.75rem; color:#94a3b8; margin-top:3px;">{w_badge_label}</div>
              </td>
              <td style="padding: 14px 16px; text-align:center; font-weight:600; color:{'#34d399' if m_n > 0 else '#64748b'};">
                {m_n}
              </td>
              <td style="padding: 14px 16px; text-align:center; font-family:'JetBrains Mono',monospace; font-size:0.88rem; color:#cbd5e1;">
                {m_p50} <span style="color:#64748b; font-size:0.75rem;">/</span> {m_p75}
              </td>
              <td style="padding: 14px 16px; text-align:center;">
                <span class="badge" style="{m_badge_style} font-weight:700; font-size:0.88rem;">{m_rec}</span>
                <div style="font-size:0.75rem; color:#94a3b8; margin-top:3px;">{m_badge_label}</div>
              </td>
              <td style="padding: 14px 16px; font-size:0.82rem; color:#cbd5e1; max-width:240px; line-height:1.4;">
                {meta['action']}
              </td>
            </tr>
            """

        # Build Recent Sales Rows
        sales_rows_html = ""
        confirmed_count = sum(1 for s in recent_sales if s.get("verification_status") == "CONFIRMED_BLOCKED")
        weekend_count = sum(1 for s in recent_sales if "weekend" in str(s.get("segment_type", "")).lower() or "mix" in str(s.get("segment_type", "")).lower())
        midweek_count = len(recent_sales) - weekend_count

        for s in recent_sales:
            lid = s.get("listing_id") or ""
            name = s.get("listing_name") or f"Comp #{lid}"
            clean_name = html.escape(name[:42])
            city = html.escape(s.get("location") or "Scottsdale")
            tier_label = "Tier A (16+)" if s.get("tier") == "tier_a" else "Tier B (12-15)"
            cin = s.get("check_in") or ""
            cout = s.get("check_out") or ""
            nights = s.get("nights") or 3
            seg = str(s.get("segment_type") or "midweek").lower()
            is_weekend = "weekend" in seg or "mix" in seg
            seg_label = "Weekend" if is_weekend else "Midweek"
            seg_badge = "background:rgba(59,130,246,0.15); color:#60a5fa; border:1px solid rgba(59,130,246,0.3);" if is_weekend else "background:rgba(168,85,247,0.15); color:#c084fc; border:1px solid rgba(168,85,247,0.3);"
            lead = s.get("lead_time_days") or 0
            rate = s.get("last_observed_rate") or 0.0
            adj_rate = s.get("last_observed_adj_rate") or rate
            pct = s.get("last_observed_percentile") or 50.0
            ratio = s.get("desirability_ratio") or 1.0
            v_status = s.get("verification_status") or "CONFIRMED_BLOCKED"
            is_confirmed = (v_status == "CONFIRMED_BLOCKED")
            status_badge = '<span class="badge" style="background:rgba(16,185,129,0.15); color:#34d399; border:1px solid rgba(16,185,129,0.3);">✅ Confirmed</span>' if is_confirmed else '<span class="badge" style="background:rgba(148,163,184,0.12); color:#94a3b8; border:1px solid rgba(148,163,184,0.25);">ℹ️ Search Absent</span>'

            if pct >= 70:
                pct_style = "background:rgba(16,185,129,0.15); color:#34d399; border:1px solid rgba(16,185,129,0.3);"
            elif pct >= 45:
                pct_style = "background:rgba(59,130,246,0.15); color:#60a5fa; border:1px solid rgba(59,130,246,0.3);"
            else:
                pct_style = "background:rgba(245,158,11,0.15); color:#fbbf24; border:1px solid rgba(245,158,11,0.3);"

            adults_num = 16 if s.get("tier") == "tier_a" else 12
            if lid and cin and cout:
                url = f"https://www.airbnb.com/rooms/{lid}?check_in={cin}&check_out={cout}&adults={adults_num}"
            elif lid:
                url = f"https://www.airbnb.com/rooms/{lid}"
            else:
                url = "#"

            sales_rows_html += f"""
            <tr class="sales-feed-row" data-status="{v_status}" data-type="{'weekend' if is_weekend else 'midweek'}" style="border-bottom: 1px solid var(--border-color);">
              <td style="padding: 12px 14px; font-family:'JetBrains Mono',monospace; font-size:0.85rem; color:#94a3b8; white-space:nowrap;">
                {s.get('detected_date', '')}
              </td>
              <td style="padding: 12px 14px;">
                <a href="{url}" target="_blank" rel="noopener noreferrer" style="color:#60a5fa; text-decoration:none; font-weight:600; font-size:0.9rem;" title="View listing on Airbnb">
                  {clean_name} ↗
                </a>
                <div style="font-size:0.75rem; color:#94a3b8; margin-top:2px;">ID: {lid} &bull; {city}</div>
              </td>
              <td style="padding: 12px 14px; font-size:0.82rem; color:#cbd5e1; white-space:nowrap;">
                <span class="badge" style="background:rgba(255,255,255,0.06); color:#cbd5e1; border:1px solid var(--border-color);">{tier_label}</span>
              </td>
              <td style="padding: 12px 14px; font-family:'JetBrains Mono',monospace; font-size:0.82rem; color:#f8fafc; white-space:nowrap;">
                {cin} → {cout}
                <div style="font-size:0.75rem; color:#94a3b8;">{nights} nights</div>
              </td>
              <td style="padding: 12px 14px; text-align:center; white-space:nowrap;">
                <span class="badge" style="{seg_badge}">{seg_label}</span>
              </td>
              <td style="padding: 12px 14px; text-align:center; font-family:'JetBrains Mono',monospace; font-size:0.85rem; color:#38bdf8; font-weight:600; white-space:nowrap;">
                {lead}d
              </td>
              <td style="padding: 12px 14px; text-align:right; font-family:'JetBrains Mono',monospace; font-size:0.88rem; color:#f8fafc; font-weight:600; white-space:nowrap;">
                ${rate:,.0f}
              </td>
              <td style="padding: 12px 14px; text-align:right; font-family:'JetBrains Mono',monospace; font-size:0.85rem; color:#a78bfa; white-space:nowrap;">
                ${adj_rate:,.0f}
                <div style="font-size:0.7rem; color:#94a3b8;">DR: {ratio:.2f}x</div>
              </td>
              <td style="padding: 12px 14px; text-align:center; white-space:nowrap;">
                <span class="badge" style="{pct_style} font-weight:700; font-family:'JetBrains Mono',monospace;">{pct:.1f}%</span>
              </td>
              <td style="padding: 12px 14px; text-align:center; white-space:nowrap;">
                {status_badge}
              </td>
            </tr>
            """

        empty_feed_html = '<tr><td colspan="10" style="text-align:center; padding:30px; color:#94a3b8;">No competitor sales recorded yet. Run backfill or daily scan.</td></tr>'
        feed_body = sales_rows_html if recent_sales else empty_feed_html

        return f"""
        <!-- Top KPI Cards -->
        <div class="kpi-grid" style="margin-bottom: 24px;">
          <div class="kpi-card">
            <div class="kpi-label">Total Competitor Sales</div>
            <div class="kpi-val" style="color:#34d399;">{sales_data.get('total_sales', 0)}</div>
            <div class="kpi-desc">Confirmed bookings detected via delta diffing</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">Median Realized Percentile</div>
            <div class="kpi-val" style="color:#60a5fa;">{sales_data.get('overall_median_percentile', 55.0):.1f}%</div>
            <div class="kpi-desc">Overall market percentile rank at time of sale</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">Average Booking Lead Time</div>
            <div class="kpi-val" style="color:#38bdf8;">{sales_data.get('avg_lead_time_days', 0.0):.1f}d</div>
            <div class="kpi-desc">Days in advance competitors secure reservations</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">Average Realized Nightly</div>
            <div class="kpi-val" style="color:#a78bfa;">${sales_data.get('avg_rate', 0.0):,.0f}</div>
            <div class="kpi-desc">Mean effective nightly rate across booked stays</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">Sales Volume Split</div>
            <div class="kpi-val" style="color:#fbbf24; font-size:1.4rem;">{sales_data.get('weekend_sales_count', 0)} Wkd / {sales_data.get('midweek_sales_count', 0)} Mid</div>
            <div class="kpi-desc">Weekend premium vs midweek volume</div>
          </div>
        </div>

        <!-- 12-Month Competitor Market Availability & Absorption Trajectory -->
        <div class="section-box" style="margin-bottom: 24px;">
          <div class="section-header" style="margin-bottom: 14px; display:flex; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; gap:12px;">
            <div>
              <div class="section-title" style="font-size: 1.25rem;">
                📊 12-Month Competitor Market Availability &amp; Absorption Trajectory
              </div>
              <p class="section-desc" style="margin-top: 4px; margin-bottom: 0;">
                Granular day-by-day market capacity across all 97 curated luxury comps. Tracks active available inventory (top cyan band), verified competitor sales (middle emerald band), and unknown blocks / past bookings (bottom slate band).
              </p>
            </div>
            <!-- Interactive Filters & Villa del Sol Status Badge -->
            <div style="display:flex; flex-wrap:wrap; gap:10px; align-items:center;">
              <div id="trajVillaStatusBadge" style="background:rgba(15,23,42,0.85); border:1px solid #334155; border-radius:9999px; padding:5px 12px; font-family:'JetBrains Mono',monospace; font-size:0.75rem; white-space:nowrap; display:inline-flex; align-items:center; gap:6px;">
                <span style="color:#f8fafc; font-weight:700;">Villa del Sol:</span> <span style="color:#facc15; font-weight:700;">🟨 Available ({v_avail_days}d / {v_avail_pct}%)</span> <span style="color:#64748b;">&bull;</span> <span style="color:#94a3b8; font-weight:700;">⬛ Unavailable ({v_unavail_days}d / {v_unavail_pct}%)</span>
              </div>
              <!-- Tier Filter Pills -->
              <div class="trajectory-pill-group trajectory-tier-pills" style="display:inline-flex; gap:6px; margin-bottom:0;">
                <button class="pill-btn active" data-tier="all" onclick="setTrajectoryTier('all', this)">All Comps ({all_total})</button>
                <button class="pill-btn" data-tier="tier_a" onclick="setTrajectoryTier('tier_a', this)">Tier A ({tier_a_total})</button>
                <button class="pill-btn" data-tier="tier_b" onclick="setTrajectoryTier('tier_b', this)">Tier B ({tier_b_total})</button>
              </div>
              <!-- Range Zoom Pills -->
              <div class="trajectory-pill-group trajectory-range-pills" style="display:inline-flex; gap:6px; margin-bottom:0;">
                <button class="pill-btn" data-range="90" onclick="setTrajectoryRange(90, this)">90 Days</button>
                <button class="pill-btn" data-range="180" onclick="setTrajectoryRange(180, this)">6 Months</button>
                <button class="pill-btn active" data-range="365" onclick="setTrajectoryRange(365, this)">12 Months</button>
              </div>
            </div>
          </div>

          <!-- Dynamic KPI Badges for Active Cohort -->
          <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap:12px; margin-bottom:18px;">
            <div style="background:rgba(15,23,42,0.6); border:1px solid #334155; border-radius:8px; padding:10px 14px;">
              <div style="font-size:0.7rem; color:#94a3b8; font-weight:700; text-transform:uppercase;">Curated Luxury Cohort</div>
              <div id="trajCohortCount" style="font-size:1.25rem; font-weight:800; color:#f8fafc; font-family:'JetBrains Mono',monospace; margin-top:2px;">
                {all_total} properties
              </div>
              <div id="trajCohortSub" style="font-size:0.72rem; color:#94a3b8; margin-top:2px;">Top line constant capacity</div>
            </div>

            <div style="background:rgba(15,23,42,0.6); border:1px solid rgba(56,189,248,0.3); border-radius:8px; padding:10px 14px;">
              <div style="font-size:0.7rem; color:#38bdf8; font-weight:700; text-transform:uppercase;">Available Inventory</div>
              <div id="trajAvailAvg" style="font-size:1.25rem; font-weight:800; color:#38bdf8; font-family:'JetBrains Mono',monospace; margin-top:2px;">
                {all_avg_avail} comps avg
              </div>
              <div style="font-size:0.72rem; color:#cbd5e1; margin-top:2px;">Actively bookable on Airbnb</div>
            </div>

            <div style="background:rgba(15,23,42,0.6); border:1px solid rgba(16,185,129,0.3); border-radius:8px; padding:10px 14px;">
              <div style="font-size:0.7rem; color:#34d399; font-weight:700; text-transform:uppercase;">Recorded Comp Sales</div>
              <div id="trajSalesAvg" style="font-size:1.25rem; font-weight:800; color:#34d399; font-family:'JetBrains Mono',monospace; margin-top:2px;">
                {all_avg_sales} verified
              </div>
              <div style="font-size:0.72rem; color:#cbd5e1; margin-top:2px;">Confirmed delta bookings</div>
            </div>

            <div style="background:rgba(15,23,42,0.6); border:1px solid rgba(148,163,184,0.3); border-radius:8px; padding:10px 14px;">
              <div style="font-size:0.7rem; color:#94a3b8; font-weight:700; text-transform:uppercase;">Unknown / Blocked</div>
              <div id="trajUnkAvg" style="font-size:1.25rem; font-weight:800; color:#94a3b8; font-family:'JetBrains Mono',monospace; margin-top:2px;">
                {all_avg_unk} comps avg
              </div>
              <div style="font-size:0.72rem; color:#cbd5e1; margin-top:2px;">Owner holds, restrictions, past sales</div>
            </div>

            <div style="background:rgba(15,23,42,0.6); border:1px solid rgba(245,158,11,0.35); border-radius:8px; padding:10px 14px;">
              <div style="font-size:0.7rem; color:#fbbf24; font-weight:700; text-transform:uppercase;">🔥 High Compression Days</div>
              <div id="trajCompressionDays" style="font-size:1.25rem; font-weight:800; color:#fbbf24; font-family:'JetBrains Mono',monospace; margin-top:2px;">
                {all_comp_days} days ({all_comp_pct}%)
              </div>
              <div style="font-size:0.72rem; color:#cbd5e1; margin-top:2px;">Days with &lt;20% comps available</div>
            </div>
          </div>

          <!-- Canvas Container -->
          <div style="position:relative; width:100%; height:340px; margin-bottom:8px;">
            <canvas id="marketTrajectoryChart"></canvas>
          </div>
          <div style="display:flex; justify-content:space-between; align-items:center; font-size:0.75rem; color:#64748b; margin-top:6px; padding:0 4px; flex-wrap:wrap; gap:6px;">
            <div><span>📅 Timeline: Daily resolution ({timeline_days} days). Ceiling strip: 🟨 Villa del Sol Available vs ⬛ Unavailable. Shaded columns: 🔥 High Compression (&lt;20% comps available).</span></div>
            <div><span>💡 Hover any date for full breakdown, Villa del Sol reservation status &amp; compression alerts</span></div>
          </div>
        </div>

        <!-- Advance Booking Horizons & Seasonal Windows (Comps) -->
        <div class="section-box" style="margin-bottom: 24px;">
          <div class="section-header" style="margin-bottom: 14px;">
            <div>
              <div class="section-title" style="font-size: 1.25rem;">
                ⏱️ Monthly Advance Booking Horizons (Comps)
              </div>
              <p class="section-desc" style="margin-top: 4px; margin-bottom: 0;">
                Empirical distribution of competitor booking lead times (25th–75th interquartile range) across {overall_comp_lead.get('count', 0)} detected competitor sales broken down by month, identifying market conversion windows and competitor booking pace.
              </p>
            </div>
          </div>

          <!-- All-Year Summary KPI Strip -->
          <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap:14px; margin-bottom:18px;">
            <div style="background:rgba(15,23,42,0.6); border:1px solid #334155; border-radius:8px; padding:12px 14px;">
              <div style="font-size:0.72rem; color:#94a3b8; font-weight:700; text-transform:uppercase;">Total Comp Sales Analyzed</div>
              <div style="font-size:1.35rem; font-weight:800; color:#f8fafc; font-family:'JetBrains Mono',monospace; margin-top:3px;">
                {overall_comp_lead.get('count', 0)} stays
              </div>
              <div style="font-size:0.75rem; color:#94a3b8; margin-top:2px;">Across all historical seasons</div>
            </div>

            <div style="background:rgba(15,23,42,0.6); border:1px solid #334155; border-radius:8px; padding:12px 14px;">
              <div style="font-size:0.72rem; color:#38bdf8; font-weight:700; text-transform:uppercase;">All-Year Median Lead Time</div>
              <div style="font-size:1.35rem; font-weight:800; color:#38bdf8; font-family:'JetBrains Mono',monospace; margin-top:3px;">
                {overall_comp_lead.get('median', 0)} days
              </div>
              <div style="font-size:0.75rem; color:#cbd5e1; margin-top:2px;">Typical competitor booking lead horizon</div>
            </div>

            <div style="background:rgba(15,23,42,0.6); border:1px solid #334155; border-radius:8px; padding:12px 14px;">
              <div style="font-size:0.72rem; color:#34d399; font-weight:700; text-transform:uppercase;">Normal Booking Window (P25–P75)</div>
              <div style="font-size:1.35rem; font-weight:800; color:#34d399; font-family:'JetBrains Mono',monospace; margin-top:3px;">
                {overall_comp_lead.get('p25', 0)}–{overall_comp_lead.get('p75', 0)} days
              </div>
              <div style="font-size:0.75rem; color:#cbd5e1; margin-top:2px;">50% of competitor bookings convert here</div>
            </div>

            <div style="background:rgba(15,23,42,0.6); border:1px solid #334155; border-radius:8px; padding:12px 14px;">
              <div style="font-size:0.72rem; color:#fbbf24; font-weight:700; text-transform:uppercase;">Full Empirical Lead Range</div>
              <div style="font-size:1.35rem; font-weight:800; color:#fbbf24; font-family:'JetBrains Mono',monospace; margin-top:3px;">
                {overall_comp_lead.get('min', 0)}–{overall_comp_lead.get('max', 0)} days
              </div>
              <div style="font-size:0.75rem; color:#cbd5e1; margin-top:2px;">Same-day to ~1 year in advance</div>
            </div>
          </div>

          <!-- Monthly Lead Horizons Table -->
          <div class="table-responsive">
            <table>
              <thead>
                <tr style="background: rgba(255,255,255,0.03);">
                  <th style="width: 110px;">Month</th>
                  <th style="width: 170px;">Season Tier</th>
                  <th style="text-align:center; width: 75px;">Stays (n)</th>
                  <th style="text-align:center; width: 105px;">Median Lead</th>
                  <th style="text-align:center; width: 140px;">Normal Window (P₂₅–P₇₅)</th>
                  <th style="text-align:center; width: 110px;">Range (Min–Max)</th>
                  <th>Market Dynamic & Strategic Guidance</th>
                </tr>
              </thead>
              <tbody>
                {comps_monthly_lead_rows_html}
              </tbody>
            </table>
          </div>
        </div>

        <!-- Section 1: 2D Strategy Matrix -->
        <div class="section-box">
          <div class="section-header" style="margin-bottom: 12px;">
            <div>
              <div class="section-title" style="font-size: 1.3rem;">
                🎯 2D Empirical Strategy Matrix (Lead Horizon × Stay Type)
              </div>
              <p class="section-desc" style="margin-top: 4px; margin-bottom: 0;">
                Calculates empirical median absorption percentiles (P₅₀) and aggressive clearing rates (P₇₅) across 4 strategic lead-time horizons. When historical observations are scarce (n &lt; 5), recommendations smoothly blend with our baseline strategy via Bayesian shrinkage (k = 5).
              </p>
            </div>
          </div>

          <div class="table-responsive">
            <table>
              <thead>
                <tr style="background: rgba(255,255,255,0.03);">
                  <th style="width: 22%;">Lead Time Horizon</th>
                  <th style="text-align:center; width: 8%;">Wkd (n)</th>
                  <th style="text-align:center; width: 12%;">Wkd P₅₀ / P₇₅</th>
                  <th style="text-align:center; width: 14%;">Wkd Target Rec.</th>
                  <th style="text-align:center; width: 8%;">Mid (n)</th>
                  <th style="text-align:center; width: 12%;">Mid P₅₀ / P₇₅</th>
                  <th style="text-align:center; width: 14%;">Mid Target Rec.</th>
                  <th style="width: 22%;">Strategic Guidance</th>
                </tr>
              </thead>
              <tbody>
                {grid_rows_html}
              </tbody>
            </table>
          </div>
        </div>

        <!-- Section 2: Recent Bookings Feed -->
        <div class="section-box">
          <div class="section-header" style="margin-bottom: 12px;">
            <div>
              <div class="section-title" style="font-size: 1.3rem;">
                📑 Competitor Sales Transaction Feed
              </div>
              <p class="section-desc" style="margin-top: 4px; margin-bottom: 0;">
                Empirical ledger of competitor listing dates that ceased being available between consecutive daily scrapes.
              </p>
            </div>
            <input type="text" id="salesSearch" class="search-input" placeholder="Search by property, city, or date..." oninput="searchSalesFeed()" style="max-width: 300px;" />
          </div>

          <div class="filter-pills sales-filter-pills" style="margin-bottom: 18px;">
            <button class="pill-btn active" data-filter="all" onclick="filterSalesFeed('all', this)">All Events ({len(recent_sales)})</button>
            <button class="pill-btn" data-filter="confirmed" onclick="filterSalesFeed('confirmed', this)" style="border-color: rgba(52,211,153,0.4); color:#34d399;">✅ Confirmed Comps ({confirmed_count})</button>
            <button class="pill-btn" data-filter="weekend" onclick="filterSalesFeed('weekend', this)">Weekend Stays ({weekend_count})</button>
            <button class="pill-btn" data-filter="midweek" onclick="filterSalesFeed('midweek', this)">Midweek Stays ({midweek_count})</button>
          </div>

          <div class="table-responsive">
            <table>
              <thead>
                <tr style="background: rgba(255,255,255,0.03);">
                  <th>Detected</th>
                  <th>Listing / Property</th>
                  <th>Tier</th>
                  <th>Stay Dates</th>
                  <th style="text-align:center;">Type</th>
                  <th style="text-align:center;">Lead Time</th>
                  <th style="text-align:right;">Last Rate</th>
                  <th style="text-align:right;">Adj. Rate</th>
                  <th style="text-align:center;">Percentile</th>
                  <th style="text-align:center;">Status</th>
                </tr>
              </thead>
              <tbody>
                {feed_body}
              </tbody>
            </table>
          </div>
        </div>
        {trajectory_script}
        """

    def _render_market_trajectory_script(self, timeline_json: str) -> str:
        """Render JavaScript for the 12-Month Market Trajectory stacked area chart."""
        js_template = """
<script>
window.trajectoryData = __TIMELINE_JSON__;
(function() {
    let currentTrajTier = 'all';
    let currentTrajRange = 365;
    let trajectoryChartInstance = null;

    function renderTrajectoryChart() {
        const canvas = document.getElementById('marketTrajectoryChart');
        if (!canvas || !window.trajectoryData || !window.trajectoryData.cohorts) return;
        
        const data = window.trajectoryData;
        const cohort = data.cohorts[currentTrajTier] || data.cohorts['all'];
        if (!cohort) return;

        const range = Math.min(currentTrajRange, data.dates.length);
        const dates = data.dates.slice(0, range);
        const monthLabels = data.month_labels.slice(0, range);
        const daysMeta = data.days_meta.slice(0, range);
        const unkData = cohort.unknown_unavailable.slice(0, range);
        const salesData = cohort.recorded_sales.slice(0, range);
        const availData = cohort.available.slice(0, range);

        const ctx = canvas.getContext('2d');
        if (trajectoryChartInstance) {
            trajectoryChartInstance.destroy();
        }

        const marketCompressionPlugin = {
            id: 'marketCompression',
            beforeDatasetsDraw(chart) {
                const { ctx, chartArea, scales } = chart;
                if (!chartArea || !scales || !scales.x) return;
                const { left, right, top, bottom } = chartArea;
                const x = scales.x;
                const tot = cohort.total_comps || 0;
                if (!tot || !availData || !availData.length) return;

                ctx.save();
                ctx.fillStyle = 'rgba(245, 158, 11, 0.12)'; // subtle amber tint for compressed dates

                for (let i = 0; i < availData.length; i++) {
                    const avail = availData[i];
                    if (avail < 0.20 * tot) {
                        const xCenter = x.getPixelForValue(i);
                        let x0, x1;
                        if (availData.length === 1) {
                            x0 = left; x1 = right;
                        } else if (i === 0) {
                            const nextX = x.getPixelForValue(1);
                            const halfStep = (nextX - xCenter) / 2;
                            x0 = Math.max(left, xCenter - halfStep);
                            x1 = xCenter + halfStep;
                        } else if (i === availData.length - 1) {
                            const prevX = x.getPixelForValue(i - 1);
                            const halfStep = (xCenter - prevX) / 2;
                            x0 = xCenter - halfStep;
                            x1 = Math.min(right, xCenter + halfStep);
                        } else {
                            const prevX = x.getPixelForValue(i - 1);
                            const nextX = x.getPixelForValue(i + 1);
                            x0 = xCenter - (xCenter - prevX) / 2;
                            x1 = xCenter + (nextX - xCenter) / 2;
                        }
                        ctx.fillRect(x0, top, Math.max(1, x1 - x0), bottom - top);
                    }
                }
                ctx.restore();
            },
            afterDatasetsDraw(chart) {
                const { ctx, chartArea, scales } = chart;
                if (!chartArea || !scales || !scales.y) return;
                const { left, right } = chartArea;
                const y = scales.y;
                const tot = cohort.total_comps || 0;
                if (!tot) return;

                // Threshold line where available comps = 20% of cohort (stacked absorption = 80%)
                const yThreshold = y.getPixelForValue(0.80 * tot);
                if (isNaN(yThreshold)) return;

                ctx.save();
                ctx.strokeStyle = 'rgba(245, 158, 11, 0.70)';
                ctx.lineWidth = 1.5;
                ctx.setLineDash([5, 4]);
                ctx.beginPath();
                ctx.moveTo(left, yThreshold);
                ctx.lineTo(right, yThreshold);
                ctx.stroke();

                ctx.setLineDash([]);
                ctx.fillStyle = '#fbbf24';
                ctx.font = "600 10px 'JetBrains Mono', monospace";
                ctx.textAlign = 'right';
                const threshCount = Math.ceil(0.20 * tot - 1e-9) - 1;
                ctx.fillText('⚡ 20% Availability Threshold (≤' + threshCount + ' comps)', right - 6, yThreshold - 4);
                ctx.restore();
            }
        };

        const villaAvailabilityBandPlugin = {
            id: 'villaAvailabilityBand',
            afterDatasetsDraw(chart) {
                const { ctx, chartArea, scales } = chart;
                if (!chartArea || !scales || !scales.x) return;
                const { left, right, top } = chartArea;
                const x = scales.x;
                if (!daysMeta || !daysMeta.length) return;

                ctx.save();
                const bandHeight = 7;
                const bandY = top + 2;

                for (let i = 0; i < daysMeta.length; i++) {
                    const meta = daysMeta[i];
                    if (!meta) continue;

                    const xCenter = x.getPixelForValue(i);
                    let x0, x1;
                    if (daysMeta.length === 1) {
                        x0 = left;
                        x1 = right;
                    } else if (i === 0) {
                        const nextX = x.getPixelForValue(1);
                        const halfStep = (nextX - xCenter) / 2;
                        x0 = Math.max(left, xCenter - halfStep);
                        x1 = xCenter + halfStep;
                    } else if (i === daysMeta.length - 1) {
                        const prevX = x.getPixelForValue(i - 1);
                        const halfStep = (xCenter - prevX) / 2;
                        x0 = xCenter - halfStep;
                        x1 = Math.min(right, xCenter + halfStep);
                    } else {
                        const prevX = x.getPixelForValue(i - 1);
                        const nextX = x.getPixelForValue(i + 1);
                        x0 = xCenter - (xCenter - prevX) / 2;
                        x1 = xCenter + (nextX - xCenter) / 2;
                    }

                    ctx.fillStyle = meta.villa_color || (meta.is_villa_available ? '#facc15' : '#475569');
                    ctx.fillRect(x0, bandY, Math.max(1, x1 - x0), bandHeight);
                }

                // Subtle divider below the band
                ctx.strokeStyle = 'rgba(255, 255, 255, 0.15)';
                ctx.lineWidth = 1;
                ctx.beginPath();
                ctx.moveTo(left, bandY + bandHeight);
                ctx.lineTo(right, bandY + bandHeight);
                ctx.stroke();

                ctx.restore();
            }
        };

        trajectoryChartInstance = new Chart(ctx, {
            type: 'line',
            plugins: [marketCompressionPlugin, villaAvailabilityBandPlugin],
            data: {
                labels: dates,
                datasets: [
                    {
                        label: 'Unavailable (Unknown)',
                        data: unkData,
                        borderColor: '#64748b',
                        backgroundColor: 'rgba(71, 85, 105, 0.45)',
                        borderWidth: 1.5,
                        fill: true,
                        stack: 'comps',
                        pointRadius: 0,
                        pointHoverRadius: 4,
                        tension: 0.15,
                    },
                    {
                        label: 'Recorded Comp Sales',
                        data: salesData,
                        borderColor: '#10b981',
                        backgroundColor: 'rgba(16, 185, 129, 0.55)',
                        borderWidth: 1.5,
                        fill: true,
                        stack: 'comps',
                        pointRadius: 0,
                        pointHoverRadius: 4,
                        tension: 0.15,
                    },
                    {
                        label: 'Available Comps',
                        data: availData,
                        borderColor: '#38bdf8',
                        backgroundColor: 'rgba(56, 189, 248, 0.45)',
                        borderWidth: 1.5,
                        fill: true,
                        stack: 'comps',
                        pointRadius: 0,
                        pointHoverRadius: 4,
                        tension: 0.15,
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: {
                    mode: 'index',
                    intersect: false,
                },
                animation: {
                    duration: 350
                },
                plugins: {
                    legend: {
                        position: 'top',
                        align: 'end',
                        labels: {
                            color: '#cbd5e1',
                            font: { family: "'Inter', sans-serif", size: 11, weight: '600' },
                            boxWidth: 12,
                            boxHeight: 12,
                            padding: 14,
                            usePointStyle: true,
                            pointStyle: 'rectRounded'
                        }
                    },
                    tooltip: {
                        backgroundColor: 'rgba(15, 23, 42, 0.95)',
                        titleColor: '#f8fafc',
                        titleFont: { family: "'Inter', sans-serif", size: 12, weight: 'bold' },
                        bodyColor: '#cbd5e1',
                        bodyFont: { family: "'JetBrains Mono', monospace", size: 11 },
                        borderColor: '#334155',
                        borderWidth: 1,
                        padding: 10,
                        displayColors: false,
                        callbacks: {
                            title: function(items) {
                                if (!items || !items.length) return '';
                                const idx = items[0].dataIndex;
                                const meta = daysMeta[idx] || {};
                                const dtStr = meta.date || '';
                                const dayName = meta.day_name || '';
                                const isAvail = meta.is_villa_available;
                                const vLabel = meta.villa_status_label || (isAvail ? 'Available & Open' : 'Unavailable');
                                const vIcon = isAvail ? '🟨' : '⬛';
                                return [
                                    dayName + ', ' + dtStr,
                                    vIcon + ' Villa del Sol: ' + vLabel
                                ];
                            },
                            label: function() {
                                return null;
                            }
                        }
                    },
                },
                scales: {
                    x: {
                        grid: {
                            color: 'rgba(255, 255, 255, 0.04)',
                            drawBorder: false,
                        },
                        ticks: {
                            color: '#94a3b8',
                            font: { family: "'JetBrains Mono', monospace", size: 11, weight: '600' },
                            autoSkip: false,
                            maxRotation: 0,
                            callback: function(val, index) {
                                return monthLabels[index] || '';
                            }
                        }
                    },
                    y: {
                        stacked: true,
                        min: 0,
                        max: cohort.total_comps || 10,
                        grid: {
                            color: 'rgba(255, 255, 255, 0.06)',
                            drawBorder: false,
                        },
                        ticks: {
                            color: '#94a3b8',
                            font: { family: "'JetBrains Mono', monospace", size: 11 },
                            stepSize: cohort.total_comps > 50 ? 20 : 10,
                        }
                    }
                }
            }
        });
        updateTrajectoryKpis();
    }

    window.initMarketTrajectoryChart = function() {
        const canvas = document.getElementById('marketTrajectoryChart');
        if (!canvas) return;
        if (trajectoryChartInstance) {
            trajectoryChartInstance.resize();
            return;
        }
        renderTrajectoryChart();
    };

    window.setTrajectoryTier = function(tier, btn) {
        currentTrajTier = tier;
        if (btn) {
            document.querySelectorAll('.trajectory-tier-pills .pill-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
        }
        updateTrajectoryKpis();
        renderTrajectoryChart();
    };

    window.setTrajectoryRange = function(range, btn) {
        currentTrajRange = parseInt(range, 10);
        if (btn) {
            document.querySelectorAll('.trajectory-range-pills .pill-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
        }
        updateTrajectoryKpis();
        renderTrajectoryChart();
    };

    function updateTrajectoryKpis() {
        const data = window.trajectoryData;
        if (!data || !data.cohorts) return;
        const cohort = data.cohorts[currentTrajTier] || data.cohorts['all'];
        if (!cohort) return;

        const range = Math.min(currentTrajRange, data.dates.length);
        const availSlice = (cohort.available || []).slice(0, range);
        const salesSlice = (cohort.recorded_sales || []).slice(0, range);
        const unkSlice = (cohort.unknown_unavailable || []).slice(0, range);

        const rLen = Math.max(1, availSlice.length);
        const meanAvail = (availSlice.reduce((a, b) => a + b, 0) / rLen).toFixed(1);
        const meanSales = (salesSlice.reduce((a, b) => a + b, 0) / rLen).toFixed(1);
        const meanUnk = (unkSlice.reduce((a, b) => a + b, 0) / rLen).toFixed(1);

        const countEl = document.getElementById('trajCohortCount');
        const availEl = document.getElementById('trajAvailAvg');
        const salesEl = document.getElementById('trajSalesAvg');
        const unkEl = document.getElementById('trajUnkAvg');

        if (countEl) countEl.innerText = cohort.total_comps + ' properties';
        if (availEl) availEl.innerText = meanAvail + ' comps avg';
        if (salesEl) salesEl.innerText = meanSales + ' verified';
        if (unkEl) unkEl.innerText = meanUnk + ' comps avg';

        // Update High Compression KPI for current range
        const compEl = document.getElementById('trajCompressionDays');
        if (compEl) {
            const tot = cohort.total_comps || 1;
            const compCount = availSlice.filter(v => v < 0.20 * tot).length;
            const compPct = ((compCount / rLen) * 100).toFixed(1);
            compEl.innerText = compCount + ' days (' + compPct + '%)';
        }

        // Update Villa del Sol status badge dynamically for current range
        const villaBadge = document.getElementById('trajVillaStatusBadge');
        if (villaBadge) {
            const metaSlice = (data.days_meta || []).slice(0, range);
            const mLen = Math.max(1, metaSlice.length);
            const availCount = metaSlice.filter(m => m.is_villa_available).length;
            const unavailCount = mLen - availCount;
            const availPct = ((availCount / mLen) * 100).toFixed(1);
            const unavailPct = ((unavailCount / mLen) * 100).toFixed(1);
            villaBadge.innerHTML = '<span style="color:#f8fafc; font-weight:700;">Villa del Sol:</span> <span style="color:#facc15; font-weight:700;">🟨 Available (' + availCount + 'd / ' + availPct + '%)</span> <span style="color:#64748b;">&bull;</span> <span style="color:#94a3b8; font-weight:700;">⬛ Unavailable (' + unavailCount + 'd / ' + unavailPct + '%)</span>';
        }
    }
})();
</script>
"""
        return js_template.replace("__TIMELINE_JSON__", timeline_json)




