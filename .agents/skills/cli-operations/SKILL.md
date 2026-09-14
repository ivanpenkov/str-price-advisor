---
name: cli-operations
description: >-
  Comprehensive reference and operational playbook for all STR Price Advisor CLI commands (src.cli).
  Use whenever executing pricing audits, quick checks, date range scans, comp bootstrapping,
  deep listing feature enrichment, quality ratio evaluation, Kivoya PMS diagnostics, or HTML dashboard regeneration.
---

# STR Price Advisor CLI Operations Guide

This skill provides a complete reference for all command-line operations in `src.cli`. All commands must be executed using the project virtual environment (`.venv/bin/python -m src.cli ...`) from the workspace root.

---

## 1. Quick Reference & Command Matrix

| Command | Primary Purpose | Network / Proxy Required | Key Flags |
| :--- | :--- | :--- | :--- |
| `run` | Full weekly or quick pricing advisory audit | **Yes** (Kivoya + Airbnb proxy) | `--weekly`, `--quick`, `--limit`, `--start-date`, `--end-date`, `--compare-platforms`, `--no-compare-platforms`, `--force`, `--max-cache-age`, `--sequential`, `--push` |
| `test-stealth` | Audit health and latency of 10-node stealth VPN pool | **Yes** (NordVPN SOCKS5 + Google probe) | `--count`, `--target` |
| `compare-platforms` | Real-time cross-platform parity (Airbnb, VRBO, Booking, Kivoya) | **Yes** (10-node proxy pool) | `--quick`, `--limit`, `--start-date`, `--end-date`, `--force`, `--sequential`, `--push` |
| `sync-reservations` | Ingest Streamline OwnerX reservations to SQLite & JSON | **Yes** (Direct OwnerX API) | `--full`, `--days-back`, `--dashboard`, `--push` |
| `track-competitor-sales` | Detect comp bookings via snapshot diffing & compute 2D strategy grid | **No** (Local snapshots) | `--backfill`, `--verify`, `--dashboard`, `--push` |
| `generate-html` | Re-render static 10-tab HTML dashboard from data | **No** (Local only) | `--push` |
| `evaluate-comps`| Compute 6-factor luxury quality scores & desirability ratios | **No** (Local evaluation) | `--no-save` |
| `enrich-comps` | Deep scrape / sync listing features (beds, baths, amenities) | **Yes** for live (`--sync-cached` is offline) | `--concurrency`, `--limit`, `--force`, `--sync-cached`, `--our-property` |
| `add-comp` | Deep scrape, evaluate, and register new competitor listing | **Yes** (NordVPN proxy) | `--tier`, `--scrape-prices`, `--limit`, `--force`, `--push` |
| `remove-comp` | Remove comp from registry, purge cache, and update dashboard | **No** (Local only) | `--push` |
| `scrape-comp-prices` | Scrape live checkout rates for a single comp across intervals | **Yes** (NordVPN proxy) | `--limit`, `--start-date`, `--end-date`, `--push` |
| `audit-comps` | Audit active comps against 6-factor luxury rubric | **No** (Local analysis) | `--auto-disqualify`, `--push` |
| `disqualify-comp` | Move comp to disqualified section, purge cache, and refresh | **No** (Local registry) | `--reason`, `--push` |
| `requalify-comp` | Restore disqualified comp to active tiers & refresh dashboard | **No** (Local registry) | `--tier`, `--push` |
| `discover-comps` | Discover high-potential luxury comps matching Villa del Sol | **Yes** (Airbnb proxy) | `--location`, `--limit`, `--min-bedrooms`, `--min-guests` |
| `snapshot-rates` | Record snapshot of current Kivoya nightly rates to SQLite | **Yes** (Kivoya API) | `--push` |
| `bootstrap-comps`| Discover and curate initial competitor registry | **Yes** (Airbnb proxy) | `--limit` |
| `test-kivoya` | Verify Kivoya / Streamline VRS PMS connection | **Yes** (Direct Kivoya API) | None |
| `sync-ratings` | Ingest and synchronize ratings/reviews across 4 channels | **Yes** for live (offline/mock fallback) | `--platform`, `--force`, `--backfill`, `--no-dashboard` |
| `show-ratings` | Display ratings scorecards and recent reviews (CLI / mobile / JSON) | **No** (Local store) | `--mobile`, `--json` |
| `audit-reviews`| Print operational review triage report with severity highlights | **No** (Local markdown) | None |

---

## 2. Command Details & Flag Reference

### `run`: Automated Pricing Audit

Executes the 5-step pricing advisory pipeline:
1. **Kivoya Ingestion**: Connects to Kivoya / Streamline VRS AJAX API for Unit `503802` to retrieve all active reservations and blocked dates.
2. **Date Segmentation**: Generates open weekend (3-night Thu–Sun / Fri–Mon) and midweek (3-night Mon–Thu) stay intervals over the next 12 months.
3. **Comp Data Collection**: Launches Playwright with the 10-worker NordVPN stealth pool to scrape real-time guest checkout prices across all competitor comps for each open interval.
4. **Dual-Percentile Analytics**: Calculates raw and quality-adjusted market percentiles (incorporating lead-time tapering, 30% midweek discount, and fee normalization).
5. **Cross-Platform Parity (Optional/Weekly)**: When `--compare-platforms` is enabled, audits live checkout prices across Airbnb, VRBO, Booking.com, and Kivoya Direct.
6. **Report Generation**: Outputs `data/latest_report.md`, `data/latest_sheet.csv`, `data/pricing_data_YYYY-MM-DD.json`, and updates `docs/index.html`.

#### Usage & Examples:
```bash
# Full 12-month weekly audit with cross-platform channel comparison
.venv/bin/python -m src.cli run --weekly --compare-platforms

# Quick check (evaluates first 10-12 upcoming intervals)
.venv/bin/python -m src.cli run --quick --limit 12

# Filter by specific date window (e.g., peak spring season)
.venv/bin/python -m src.cli run --quick --start-date 2027-02-01 --end-date 2027-04-30

# Force live scraping even if disk cache is younger than 20 hours
.venv/bin/python -m src.cli run --quick --limit 12 --force

# With automated GitHub push (use ONLY if user explicitly requested)
.venv/bin/python -m src.cli run --quick --push
```

---

### `test-stealth`: Audit 10-Worker Multi-IP NordVPN Stealth Fleet

Audits the RFC 1928 SOCKS5 authentication, port binding, and live packet latency of all parallel stealth forwarders:
- Probes target URL (`https://www.google.com` by default) through each active `pproxy` local forwarder.
- Automatically hot-swaps unready or packet-dropping candidate nodes.
- Prints a formatted latency audit table with city hubs, local ports, remote nodes, and online status badges.

#### Usage:
```bash
# Audit all 10 stealth forwarders
.venv/bin/python -m src.cli test-stealth --count 10

# Audit specific count or test target
.venv/bin/python -m src.cli test-stealth --count 5 --target https://www.google.com
```

---

### `compare-platforms`: Real-Time Cross-Platform Price Parity

Performs live multi-channel guest checkout price comparison for Villa del Sol across **Airbnb**, **VRBO**, **Booking.com**, and **Kivoya Direct**:
- Identifies syndication markups, fee discrepancies, and rate divergence.
- Leases isolated proxy contexts concurrently from the 10-worker pool.
- Automatically falls back to verified Step 3 price cache (Airbnb) or Kivoya PMS rate projections (Booking/VRBO) on transient platform errors.

#### Usage:
```bash
# Run comparison across first 12 open intervals
.venv/bin/python -m src.cli compare-platforms --quick --limit 12

# Run full 12-month comparison
.venv/bin/python -m src.cli compare-platforms

# Filter by date range
.venv/bin/python -m src.cli compare-platforms --start-date 2026-10-01 --end-date 2026-12-31
```

---

### `generate-html`: Static Dashboard Generator

Re-renders the interactive HTML dashboard (`docs/index.html`) using the latest pricing data, comp registry, listing specifications, and reservation intelligence engine (`src.reservation_intelligence`). Automatically compiles:
- 12-month schedule with historical track record benchmarks ($\pm 15$ days, realized median & range).
- Annual Weekend vs. Midweek strategy shift table (2022–2027) & narrative.
- Seasonal advance booking windows & booking pace indicators.
- 2D empirical strategy matrix and recent competitor sales ledger.

#### When to use:
- After updating comp evaluation scores (`evaluate-comps`).
- After syncing fresh reservations (`sync-reservations`).
- After modifying UI layout, styles, or JavaScript in `src/html_generator.py`.
- To refresh the dashboard without re-running an expensive web scrape.

#### Usage:
```bash
.venv/bin/python -m src.cli generate-html
```

---

### `evaluate-comps`: Quality Scoring & Desirability Ratios

Runs the 6-factor evaluation rubric across all competitor listings in `config/comps_registry.json`:
- **Resort Amenities & Outdoor Living (25%)**: Private heated pool, spa, sports courts, outdoor kitchen.
- **Bedrooms, Bathrooms & Capacity (20%)**: King suites, bedroom count, ensuite bath ratio, 16+ guest scale.
- **Market Asset Valuation & Scale (15%)**: Property market valuation benchmarked against Villa del Sol ($2.0M baseline), lot acreage (0.75 acre), and living square footage (5,400 sq ft).
- **Interior Luxury, Finishes & Entertainment (15%)**: Chef's kitchen, custom game rooms, movie theater, luxury appointments.
- **Location & Corridor (15%)**: Proximity to Tempe/South Scottsdale corridor vs peripheral East Valley.
- **Reputation & Review Quality (10%)**: Star rating (4.70★ minimum threshold), review volume, Guest Favorite / Superhost status.

Calculates:
- `composite_score` (0–100) benchmarked against Villa del Sol's baseline score (88.0).
- `desirability_ratio` ($\text{Score} / 88.0$).
- `is_valid_comp` (`true` or `false`) and `validity_reason`.

#### Usage:
```bash
# Evaluate and save back to config/comps_registry.json
.venv/bin/python -m src.cli evaluate-comps

# Dry-run evaluation without overwriting registry
.venv/bin/python -m src.cli evaluate-comps --no-save
```

---

### `enrich-comps`: Feature & Amenity Deep Scraping

Enriches competitor listings with verified data extracted from Apollo client deferred state (`<script id="data-deferred-state-0">`), JSON-LD schemas, and room layouts. Extracts:
- Verified title (cleaning out `503 Service Unavailable` or generic placeholders).
- Exact bed count, bedroom count, bathroom count, and guest capacity.
- Complete categorized amenities list (pool, spa, basketball, putting green, billiards, etc.).
- High-resolution hero photo URLs.

#### Usage:
```bash
# 1. Instant local sync from existing cached profiles in data/enriched_comps/ (NO WEB REQUESTS)
.venv/bin/python -m src.cli enrich-comps --sync-cached

# 2. Live proxy enrichment of uncached comps (2 concurrent workers)
.venv/bin/python -m src.cli enrich-comps --concurrency 2

# 3. Live test batch on first 10 comps
.venv/bin/python -m src.cli enrich-comps --limit 10 --concurrency 2

# 4. Force re-scraping even for comps already in local cache
.venv/bin/python -m src.cli enrich-comps --force --concurrency 2

# 5. Refresh Villa del Sol's own property profile (data/our_property_profile.json)
.venv/bin/python -m src.cli enrich-comps --our-property --force
```

---

### `add-comp`: Add, Evaluate, and Register Competitor Listing

Scrapes listing profile via NordVPN proxy, scores quality against Villa del Sol using the 6-factor luxury rubric, registers into `config/comps_registry.json` and `config/listing_specs.json`, and optionally triggers interval price scraping.

#### Usage:
```bash
# Add comp and automatically scrape prices across open intervals
.venv/bin/python -m src.cli add-comp 1493069124077219890 --scrape-prices

# Add comp with explicit tier assignment
.venv/bin/python -m src.cli add-comp https://www.airbnb.com/rooms/1493069124077219890 --tier tier_a

# Add comp and scrape first 5 upcoming intervals
.venv/bin/python -m src.cli add-comp 1493069124077219890 --scrape-prices --limit 5
```

---

### `remove-comp`: Clean Competitor Removal & Dashboard Refresh

Completely unregisters a competitor listing from `config/comps_registry.json` and `config/listing_specs.json`, purges its single-comp cache (`data/cache/search_*_comp_{id}.json`), deletes `data/enriched_comps/{id}.json`, and updates `docs/index.html`.

#### Usage:
```bash
.venv/bin/python -m src.cli remove-comp 1493069124077219890
```

---

### `scrape-comp-prices`: Single-Comp Interval Checkout Price Scraper

Directly checks live guest checkout prices for a single comp across open calendar intervals using the mandatory NordVPN proxy, intercepting Airbnb GraphQL pricing and saving to `data/cache/search_{c_in}_{c_out}_comp_{id}.json`. Automatically refreshes `docs/index.html`.

#### Usage:
```bash
# Scrape all open intervals
.venv/bin/python -m src.cli scrape-comp-prices 1493069124077219890

# Scrape first N intervals
.venv/bin/python -m src.cli scrape-comp-prices 1493069124077219890 --limit 5

# Scrape within specific date range
.venv/bin/python -m src.cli scrape-comp-prices 1493069124077219890 --start-date 2026-10-01 --end-date 2026-12-31
```

---

### `bootstrap-comps`: Market Discovery & Registry Seeding

Discovers luxury comps across Phoenix East Valley (Scottsdale, Tempe, Chandler, Mesa, Gilbert) via Airbnb search cards and populates initial `config/comps_registry.json`.

#### Usage:
```bash
.venv/bin/python -m src.cli bootstrap-comps --limit 40
```

---

### `test-kivoya`: Kivoya PMS API Diagnostics

Tests live connection to Kivoya's Streamline VRS WordPress AJAX API:
- Retrieves active reservations / blocked periods for Unit `503802`.
- Retrieves all seasonal base rate periods.
- Prints the next 3 reservations and upcoming seasonal base rates.

#### Usage:
```bash
.venv/bin/python -m src.cli test-kivoya
```

---

### `sync-reservations`: Streamline OwnerX PMS Synchronization

Synchronizes reservations and owner payout financials from Streamline OwnerX directly into SQLite (`data/reservations.db: reservations`) and JSON export (`data/reservations.json`).
- Auto-calculates accrual daily revenue across calendar years.
- Updates interactive availability calendar and cumulative revenue pace curves.

#### Usage:
```bash
# Incremental sync (past 60 days + future) and dashboard update
.venv/bin/python -m src.cli sync-reservations --days-back 60 --dashboard

# Full historical sync (2022 to present) with GitHub Pages push
.venv/bin/python -m src.cli sync-reservations --full --dashboard --push
```

---

### `track-competitor-sales`: Competitor Sales & Absorption Velocity Engine

Diffs consecutive daily pricing snapshots (`pricing_data_YYYY-MM-DD.json`) to detect when competitor listings stop being available for open intervals. Records verified sales in SQLite (`data/reservations.db: competitor_sales`), calculates booking lead-time days and market percentile rank at time of sale, and aggregates into the **2D Strategy Grid** (4 Lead Horizons $\times$ Weekend/Midweek) with Bayesian shrinkage ($k=3$).

#### Usage:
```bash
# Diff latest two snapshots and update dashboard:
.venv/bin/python -m src.cli track-competitor-sales --dashboard

# Backfill historical sales across all existing daily snapshots:
.venv/bin/python -m src.cli track-competitor-sales --backfill --dashboard

# Backfill and automatically commit & push to GitHub Pages:
.venv/bin/python -m src.cli track-competitor-sales --backfill --dashboard --push
```

---

### `audit-comps`: Comp Portfolio Quality & Rubric Audit

Audits the active competitor portfolio in `config/comps_registry.json` against Villa del Sol's 6-factor luxury rubric:
- Inspects room counts, bathroom ratios, on-site owner presence, capacity caps (<12 guests), and low review ratings (<4.70★).
- Identifies invalid or low-quality comps and prints a structured proposal.
- Passing `--auto-disqualify` moves proposed comps directly to the disqualified section.

#### Usage:
```bash
# Audit active comps and display findings
.venv/bin/python -m src.cli audit-comps

# Audit and automatically move failing comps to disqualified
.venv/bin/python -m src.cli audit-comps --auto-disqualify
```

---

### `disqualify-comp` & `requalify-comp`: Competitor Status Lifecycle

Manages the active vs. disqualified status of competitor listings:
- **`disqualify-comp`**: Moves listing to the `disqualified` dictionary in `config/comps_registry.json`, purges its price cache and sales ledger records, and refreshes `docs/index.html`.
- **`requalify-comp`**: Restores listing from `disqualified` back to active (`tier_a` or `tier_b`), re-runs evaluation, and refreshes `docs/index.html`.

#### Usage:
```bash
# Disqualify comp with custom reason
.venv/bin/python -m src.cli disqualify-comp 12345678 --reason "Capacity cap 10 guests; owner on site"

# Restore disqualified comp back to Tier B
.venv/bin/python -m src.cli requalify-comp 12345678 --tier tier_b
```

---

### `discover-comps`: Market Expansion Discovery

Discovers high-potential luxury comps matching Villa del Sol's profile (5+ bedrooms, 12+ guests, pool, 4.85+ rating) across Phoenix East Valley (Tempe, Chandler, Ahwatukee, South Scottsdale):
- Pulls live search cards via NordVPN proxy.
- Filters out already registered listings and ranks candidates by feature similarity.

#### Usage:
```bash
# Discover candidates in Chandler/Tempe corridor
.venv/bin/python -m src.cli discover-comps --location "Chandler, AZ" --limit 15

# Discover large direct comps (16+ guests, 6+ bedrooms)
.venv/bin/python -m src.cli discover-comps --min-bedrooms 6 --min-guests 16
```

---

### `snapshot-rates`: Kivoya Nightly Rate Snapshot Ledger

Records an instantaneous timestamped snapshot of current Kivoya / Streamline VRS published base nightly rates across all seasons into SQLite (`data/reservations.db: rate_snapshots`):
- Provides historical auditability when Kivoya updates rates.

#### Usage:
```bash
.venv/bin/python -m src.cli snapshot-rates
```

---

## 3. Standard Operational Workflows (Recipes)

### Workflow 1: Complete Comp Feature Update & Scoring Pipeline
Run this workflow whenever listing features have been scraped or parser logic is updated:
```bash
# Step 1: Sync or scrape features
.venv/bin/python -m src.cli enrich-comps --sync-cached  # or: --concurrency 2

# Step 2: Re-evaluate quality scores and ratios
.venv/bin/python -m src.cli evaluate-comps

# Step 3: Rebuild dashboard
.venv/bin/python -m src.cli generate-html
```

### Workflow 2: Weekly Pricing Audit Cycle
Run this weekly or when seasonal market rates need to be refreshed:
```bash
# Step 1: Quick verification of Kivoya availability
.venv/bin/python -m src.cli test-kivoya

# Step 2: Execute weekly advisory sweep (proxied)
.venv/bin/python -m src.cli run --weekly

# Step 3: Verify dashboard generation
# docs/index.html, docs/latest_sheet.csv, docs/latest_report.md are updated
```

### Workflow 3: Fast Near-Term Price Check (10–12 Intervals)
When the user wants a quick turnaround on upcoming dates without running the entire 12-month calendar:
```bash
.venv/bin/python -m src.cli run --quick --limit 12
```

### Workflow 4: Targeted Season or Event Scan
To inspect pricing around major local demand drivers (e.g. WM Phoenix Open, Spring Training):
```bash
.venv/bin/python -m src.cli run --quick --start-date 2027-02-01 --end-date 2027-03-31
```

### Workflow 5: Adding a Competitor Listing and Populating Dates
When a user requests adding a new listing by URL or ID:
```bash
# Add comp profile, evaluate quality, and populate checkout pricing across upcoming intervals
.venv/bin/python -m src.cli add-comp <airbnb_url_or_id> --scrape-prices --limit 10
```

### Workflow 6: Tracking Competitor Sales & Updating Absorption Strategy
Run this whenever historical snapshots have accumulated or after daily market scans:
```bash
# Step 1: Detect sales from consecutive daily snapshots
.venv/bin/python -m src.cli track-competitor-sales --backfill

# Step 2: Refresh static HTML dashboard with updated 2D strategy matrix & bookings feed
.venv/bin/python -m src.cli generate-html --push
```

### Workflow 7: Ratings & Reviews Synchronization & Analysis
Run this to update cross-platform guest sentiment or review recent guest feedback:
```bash
# Step 1: Sync ratings across Airbnb, VRBO, Booking.com, and Kivoya Direct
.venv/bin/python -m src.cli sync-ratings

# Step 2: View terminal ratings overview or mobile push format
.venv/bin/python -m src.cli show-ratings
.venv/bin/python -m src.cli show-ratings --mobile

# Step 3: Print operational review triage report with severity highlights
.venv/bin/python -m src.cli audit-reviews
```

---

## 4. Mandatory Rules & Best Practices

1. **Always Use `.venv`**: Execute commands using `.venv/bin/python -m src.cli ...` so all project dependencies and Playwright binaries resolve correctly.
2. **Never Bypass the Proxy on Live Scrapes**: `run`, `enrich-comps` (without `--sync-cached`), and `bootstrap-comps` query Airbnb. Ensure `.env` has valid `NORDVPN_USER` and `NORDVPN_PASS` before starting.
3. **Safe Concurrency**: Keep `--concurrency` between `2` and `3`. Do not exceed 4 to prevent triggering anti-bot heuristics.
4. **Git Operations Safeguard**: Do not pass `--push` unless the user has explicitly requested automated git commits and pushes.


