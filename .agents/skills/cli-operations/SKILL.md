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
| `run` | Full weekly or quick pricing advisory audit | **Yes** (Kivoya + Airbnb proxy) | `--weekly`, `--quick`, `--limit`, `--start-date`, `--end-date`, `--push` |
| `generate-html` | Re-render static HTML dashboard from data | **No** (Local only) | `--push` |
| `evaluate-comps`| Compute 5-factor quality scores & desirability ratios | **No** (Local evaluation) | `--no-save` |
| `enrich-comps` | Deep scrape / sync listing features (beds, baths, amenities) | **Yes** for live (`--sync-cached` is offline) | `--concurrency`, `--limit`, `--force`, `--sync-cached`, `--our-property` |
| `add-comp` | Deep scrape, evaluate, and register new competitor listing | **Yes** (NordVPN proxy) | `--tier`, `--scrape-prices`, `--limit`, `--force`, `--push` |
| `remove-comp` | Remove comp from registry, purge cache, and update dashboard | **No** (Local only) | `--push` |
| `scrape-comp-prices` | Scrape live checkout rates for a single comp across intervals | **Yes** (NordVPN proxy) | `--limit`, `--start-date`, `--end-date`, `--push` |
| `bootstrap-comps`| Discover and curate initial competitor registry | **Yes** (Airbnb proxy) | `--limit` |
| `test-kivoya` | Verify Kivoya / Streamline VRS PMS connection | **Yes** (Direct Kivoya API) | None |

---

## 2. Command Details & Flag Reference

### `run`: Automated Pricing Audit

Executes the 5-step pricing advisory pipeline:
1. **Kivoya Ingestion**: Connects to Kivoya / Streamline VRS AJAX API for Unit `503802` to retrieve all active reservations and blocked dates.
2. **Date Segmentation**: Generates open weekend (3-night Thu–Sun / Fri–Mon) and midweek (3-night Mon–Thu) stay intervals over the next 12 months.
3. **Comp Data Collection**: Launches Playwright with NordVPN proxy to scrape real-time guest checkout prices across all competitor comps for each open interval.
4. **Dual-Percentile Analytics**: Calculates raw and quality-adjusted market percentiles (incorporating lead-time tapering, 30% midweek discount, and fee normalization).
5. **Report Generation**: Outputs `data/latest_report.md`, `data/latest_sheet.csv`, `data/pricing_data_YYYY-MM-DD.json`, and updates `docs/index.html`.

#### Usage & Examples:
```bash
# Full 12-month weekly audit (all open intervals)
.venv/bin/python -m src.cli run --weekly

# Quick check (evaluates first 10-12 upcoming intervals)
.venv/bin/python -m src.cli run --quick --limit 12

# Filter by specific date window (e.g., peak spring season)
.venv/bin/python -m src.cli run --quick --start-date 2027-02-01 --end-date 2027-04-30

# With automated GitHub push (use ONLY if user explicitly requested)
.venv/bin/python -m src.cli run --quick --push
```

---

### `generate-html`: Static Dashboard Generator

Re-renders the interactive HTML dashboard (`docs/index.html`) using the latest pricing data, comp registry, and listing specifications.

#### When to use:
- After updating comp evaluation scores (`evaluate-comps`).
- After modifying UI layout, styles, or JavaScript in `src/html_generator.py`.
- To refresh the dashboard without re-running an expensive web scrape.

#### Usage:
```bash
.venv/bin/python -m src.cli generate-html
```

---

### `evaluate-comps`: Quality Scoring & Desirability Ratios

Runs the 5-factor evaluation rubric across all competitor listings in `config/comps_registry.json`:
- **Outdoor Resort Yard & Pool (30%)**
- **Bedrooms, Bathrooms & Capacity (25%)**
- **Interior Luxury & Games (20%)**
- **Location & Corridor (15%)**
- **Reputation & Review Quality (10%)**

Calculates:
- `composite_score` (0–100) benchmarked against Villa del Sol's score (88.0).
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

Scrapes listing profile via NordVPN proxy, scores quality against Villa del Sol using the 5-factor luxury rubric, registers into `config/comps_registry.json` and `config/listing_specs.json`, and optionally triggers interval price scraping.

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

---

## 4. Mandatory Rules & Best Practices

1. **Always Use `.venv`**: Execute commands using `.venv/bin/python -m src.cli ...` so all project dependencies and Playwright binaries resolve correctly.
2. **Never Bypass the Proxy on Live Scrapes**: `run`, `enrich-comps` (without `--sync-cached`), and `bootstrap-comps` query Airbnb. Ensure `.env` has valid `NORDVPN_USER` and `NORDVPN_PASS` before starting.
3. **Safe Concurrency**: Keep `--concurrency` between `2` and `3`. Do not exceed 4 to prevent triggering anti-bot heuristics.
4. **Git Operations Safeguard**: Do not pass `--push` unless the user has explicitly requested automated git commits and pushes.

