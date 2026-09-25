# Diagnostic Investigation: Interactions Between Daily Quickscan and Weekly Fullscan

## Executive Summary

When viewing the **12-Month Competitor Market Availability & Absorption Trajectory** in the pricing dashboard, almost all competitor inventory beyond the 90-day horizon (after early January 2027) collapses to **0 available comps** (showing 9.5 comps average available, 91.7 comps unknown/blocked, and 280 high compression days / 76.7%).

This document provides a comprehensive post-mortem and forensic analysis of how the **Daily Quickscan**, **Weekly Fullscan**, **PMS Reservation Sync**, and **Dashboard Generation** interact. It details the exact failure mechanisms in the data pipeline and outlines the design decisions required to permanently resolve the issue.

```
                                  PIPELINE DATA FLOW & FAILURE MODES

   Weekly Full Scan (Sunday)                    Daily Quickscan (Daily 6:15 AM)
   Scrapes all 80 intervals                     Scrapes ONLY first 12 intervals (~90 days)
             │                                                │
             ▼                                                ▼
   data/pricing_data_{Sun}.json                 PriceReportGenerator.generate_all()
   (80 intervals, full comp lists)                            │
                                                              ▼
                                                ⚠️ OVERWRITES pricing_data_{today}.json
                                                with ONLY 12 intervals!
                                                (Intervals 13–80 are completely dropped!)
                                                              │
   PMS Sync (Daily 6:00 AM on Mac Mini)                       ▼
   Runs on node with EMPTY data/cache/          HTMLDashboardGenerator.generate()
             │                                                │
             ▼                                                ▼
   generate_full_12_month_evaluation()          CompetitorSalesTracker.compute_timeline()
   Cache miss on intervals 13–80!                             │
   Writes pricing_data_{today}.json                           ▼
   with 80 intervals, ALL COMPS EMPTY []!       Scans backwards across pricing_data_*.json
             │                                  Hits empty snapshot -> stops search!
             └─────────────────────────────────► All days after Jan 7 have 0 available comps!
```

---

## 1. Identified Root Causes

Our codebase audit revealed **four interlocking root causes** that compound to cause this availability collapse:

### Root Cause 1: Daily Quickscan Truncates the Daily Snapshot File
- **Location**: [`src/cli.py:427-505`](file:///Users/ivanpe/str-price-advisor/src/cli.py#L427-L505) and [`src/reporter.py:47-85`](file:///Users/ivanpe/str-price-advisor/src/reporter.py#L47-L85)
- **The Mechanism**:
  1. In `run_weekly_advisory(quick=True, max_segments=12)`:
     ```python
     active_segments = segments[:max_segments]  # Exactly 12 intervals
     evaluated_results, interval_metrics = await run_interval_evaluations(...)
     ```
  2. In Step 4 of the advisory run, `PriceReportGenerator.generate_all(evaluated_segments=evaluated_results)` is called directly with `evaluated_results` (which has only 12 intervals).
  3. `reporter.generate_all()` writes:
     - `data/pricing_data_{date_str}.json`
     - `data/latest_report.md`
     - `data/latest_sheet.csv`
  4. **The Impact**: All intervals beyond the 12th (approx. day 91 through day 365) are completely omitted from `data/pricing_data_{today}.json`. Furthermore, `latest_sheet.csv` and `latest_report.md` are truncated from 80 intervals down to 12.

---

### Root Cause 2: Backward Snapshot Overlay Suffers from "Empty Interval Poisoning"
- **Location**: [`src/competitor_sales_tracker.py:2196-2209`](file:///Users/ivanpe/str-price-advisor/src/competitor_sales_tracker.py#L2196-L2209)
- **The Mechanism**:
  To construct the 365-day trajectory chart, `CompetitorSalesTracker.compute_daily_market_inventory_timeline()` attempts to bridge quickscans with earlier full scans by overlaying prior snapshots:
  ```python
  report_date, curr_intervals = self.extract_intervals_from_snapshot(target_snapshot)
  merged_intervals = dict(curr_intervals)
  # Overlay prior snapshots backwards to cover any intervals missing from current quick scan
  all_snaps = sorted(self.data_dir.glob("pricing_data_*.json"))
  for p in reversed(all_snaps):
      if p == target_snapshot:
          continue
      try:
          _, p_ints = self.extract_intervals_from_snapshot(p)
          for k, v in p_ints.items():
              if k not in merged_intervals:
                  merged_intervals[k] = v
      except Exception:
          pass
  ```
- **The Failure**:
  The check is strictly `if k not in merged_intervals:`.
  If an intermediate snapshot `p` contains interval `k` but with an **empty** comps dictionary (`v["comps"] == {}`), `merged_intervals[k]` is populated with that empty record.
  Because `k` is now present in `merged_intervals`, the backward search **stops** and never inspects older, valid weekly full scans (e.g., Sunday's full scan with 50–70 comps per interval).
  When the daily timeline renders:
  ```python
  inv = get_nearest_interval(d)
  if inv and "comps" in inv:
      avail_on_day = {cid for cid in inv["comps"].keys() if cid in all_ids}
  else:
      avail_on_day = set()
  ```
  `inv["comps"]` is empty, so `avail_on_day` is `0`, and all comps are categorized as `unknown_unavailable`.

---

### Root Cause 3: PMS Sync Runs on Node with Gitignored/Empty Cache
- **Location**: [`scripts/launchd/run_pms_sync.sh:86`](file:///Users/ivanpe/str-price-advisor/scripts/launchd/run_pms_sync.sh#L86), [`src/cli.py:1300-1324`](file:///Users/ivanpe/str-price-advisor/src/cli.py#L1300-L1324), and [`.gitignore`](file:///Users/ivanpe/str-price-advisor/.gitignore)
- **The Mechanism**:
  1. `data/cache/` is gitignored. When the repository is cloned on the Mac Mini, `data/cache/` starts completely empty.
  2. Every morning at 6:00 AM, `run_pms_sync.sh` runs:
     ```bash
     python -m src.cli sync-reservations --days-back 60 --dashboard --push
     ```
  3. `sync-reservations --dashboard` invokes:
     ```python
     evaluated_segments = html_gen.generate_full_12_month_evaluation()
     reporter.generate_all(evaluated_segments)
     ```
  4. In `html_gen.generate_full_12_month_evaluation()`:
     It looks for cached competitor searches in `data/cache/search_*.json`.
     Because the Mac Mini only runs 12-interval quickscans, cache files for intervals 13 through 80 **do not exist**.
     It falls back to `comps_list = []` for all unscraped intervals.
  5. `reporter.generate_all()` writes `data/pricing_data_{today}.json` with **80 intervals where all `comps_list` are empty `[]`**!
  6. It commits and pushes this empty snapshot to GitHub (`Sync Streamline OwnerX reservations and update dashboard`).
  7. This creates the exact "poisoned" snapshot that breaks Root Cause 2!

---

### Root Cause 4: Order-of-Operations Inversion in Dashboard Generation
- **Location**: [`src/cli.py:1050-1057`](file:///Users/ivanpe/str-price-advisor/src/cli.py#L1050-L1057), [`src/cli.py:1089-1096`](file:///Users/ivanpe/str-price-advisor/src/cli.py#L1089-L1096), and [`src/cli.py:1309-1316`](file:///Users/ivanpe/str-price-advisor/src/cli.py#L1309-L1316)
- **The Mechanism**:
  In `generate-html`, `compare-platforms`, and `sync-reservations`:
  ```python
  # Line 1050 / 1090 / 1310:
  out = html_gen.generate(evaluated_segments)
  # Line 1057 / 1096 / 1316:
  reporter.generate_all(evaluated_segments=evaluated_segments)
  ```
  `html_gen.generate()` runs **before** `reporter.generate_all()`.
  Inside `html_gen.generate()`, line 450 executes:
  ```python
  market_timeline_data = sales_tracker.compute_daily_market_inventory_timeline()
  ```
  `compute_daily_market_inventory_timeline()` reads `pricing_data_*.json` from disk.
  Because `reporter.generate_all()` has not executed yet, `compute_daily_market_inventory_timeline()` reads the **previous stale snapshot** on disk.
  Even after `reporter.generate_all()` writes fresh data, `docs/index.html` has already been generated with the stale data.

---

## 2. Evidence from Data & Git History

1. **Commit `cb6a6ba` (Sep 23, 10:09 AM)**:
   - Generated by `run --quick --limit 12 --push`.
   - `data/pricing_data_2026-09-23.json` had **exactly 12 intervals** (check-in `2026-09-24` to `2027-01-04`).
   - The prior file `data/pricing_data_2026-09-22.json` (created by PMS sync in commit `b7dd083`) had 80 intervals, but **every interval had `comps_list: []`**.
   - Result: `docs/index.html` was generated with:
     - `Available Inventory: 9.5 comps avg`
     - `Unknown / Blocked: 91.7 comps avg`
     - `High Compression Days: 280 days (76.7%)`
     - Exactly matching the user's attached screenshot.
2. **Interval Endpoint Alignment**:
   - The 12th interval in the quick scan is `2027-01-04 -> 2027-01-07`.
   - In the chart, the cyan available band exists until early January 2027 and then immediately collapses to 0 for the remainder of the 12-month period.

---

## 3. Comparison: Daily Quickscan vs. Weekly Fullscan

| Metric / Dimension | Daily Quickscan (`--quick --limit 12`) | Weekly Fullscan (`--weekly`) |
| :--- | :--- | :--- |
| **Frequency & Schedule** | Daily at 6:15 AM PT | Every Sunday at 6:00 AM PT |
| **Interval Horizon** | 12 unbooked intervals (~90 days out) | All ~80 unbooked intervals (Full 365 days) |
| **Scrape Duration** | ~2–4 minutes | ~30–45 minutes |
| **Data Scraped** | 12 Airbnb stay searches + targeted OTA | 80 Airbnb stay searches + 4-channel comparison |
| **Purpose** | Fast, agile rate tuning; short-lead sales & surge detection | Comprehensive baseline for entire 12-month calendar |
| **Current Flaw** | Overwrites `pricing_data_*.json` with only 12 intervals, dropping intervals 13–80 | Scrapes everything, but its full-calendar data is wiped out by the next morning's quickscan! |

---

## 4. Key Architectural Decisions to Resolve

To eliminate this bug permanently, we need to address three key layers:

### Decision 1: Snapshot Preservation Strategy
When `daily-quickscan` (or `pms-sync`) finishes, how should `data/pricing_data_{today}.json` be structured?
- **Option A (Recommended)**: **Additive Cumulative Merge**:
  When saving `pricing_data_{today}.json`, load the most recent full snapshot. Update intervals 1–12 with the freshly scraped live comp data, while preserving intervals 13–80 from the most recent full scan. Never allow a quickscan or PMS sync to truncate the file.
- **Option B**: **Separate Snapshot Files**:
  Save quickscans as `quickscan_data_{today}.json` and keep `pricing_data_{today}.json` exclusively for weekly fullscans. The analytics engine queries both.
- **Option C**: **Cloud Database Snapshot Storage (Turso/SQLite)**:
  Persist all interval comp lists directly in Turso cloud database tables rather than relying on git-committed JSON files.

### Decision 2: Backward Overlay Hygiene & Empty Interval Protection
In `competitor_sales_tracker.py:compute_daily_market_inventory_timeline()` and `diff_and_verify_staged_comps()`:
- **Rule**: An interval `(cin, cout)` from a prior snapshot MUST NOT be accepted into `merged_intervals` if `len(v.get("comps", {})) == 0` when older snapshots exist that have `len(comps) > 0`.
- The search backwards must continue until it finds a snapshot with actual comp listings for that interval.

### Decision 3: Pipeline Execution Order
In `src/cli.py` across all commands (`generate-html`, `pms-sync`, `compare-platforms`, `run`):
- Ensure `reporter.generate_all()` always writes the merged snapshot to disk **before** `html_gen.generate()` runs and reads it.
