---
name: manage-comps
description: >-
  Standard operational playbook for adding, removing, and scraping interval prices for individual competitor listings.
  Use whenever a user asks to add a new comp, delete/remove a comp, or scrape/refresh pricing for a specific property.
---

# Competitor Listing Lifecycle Management Skill

This skill guides the AI assistant on how to add, remove, and scrape pricing data for individual competitor properties in the STR Price Advisor.

All commands **MUST** use the mandatory **NordVPN SOCKS5 proxy** (`ProxyManager(required=True)`) whenever accessing Airbnb.

---

## 1. Adding a New Competitor Listing

When a user requests adding a new listing (by providing an Airbnb URL or listing ID):

### Standard Operating Sequence:
1. **Add & Enrich Comp**:
   Run the `add-comp` CLI command:
   ```bash
   .venv/bin/python -m src.cli add-comp <listing_id_or_url> --scrape-prices
   ```
   *Alternative two-step workflow*:
   ```bash
   # Step 1: Add profile, evaluate against Villa del Sol, and register
   .venv/bin/python -m src.cli add-comp <listing_id_or_url>

   # Step 2: Immediately scrape checkout pricing across open intervals
   .venv/bin/python -m src.cli scrape-comp-prices <listing_id_or_url>
   ```

2. **What Happens Under the Hood**:
   - **Check Existing**: Checks if the listing is already in `config/comps_registry.json`. If it already exists, alerts the host and avoids redundant profile scraping.
   - **Profile Deep Scrape**: Launches Playwright with the NordVPN proxy to scrape full metadata: title, bedrooms, beds, bathrooms, guest capacity, photo, and all 70+ amenities into `data/enriched_comps/{listing_id}.json`.
   - **Quality Evaluation**: Runs `CompEvaluator` using the 5-factor luxury rubric (Outdoor 30%, Capacity 25%, Interior 20%, Location 15%, Reputation 10%) to compute category scores, composite score, and desirability ratio (e.g. 1.05x).
   - **Catalog Registration**: Saves the record to `config/comps_registry.json` and updates `config/listing_specs.json`.
   - **Interval Pricing Extraction**: Queries Airbnb with NordVPN proxy for each open Kivoya calendar interval, intercepting `StaysPdpSections` to capture live stay pricing (or flag as booked/unavailable).
   - **Dashboard Refresh**: Automatically regenerates `docs/index.html` so the new comp and its live prices appear immediately.

3. **Optional Flags**:
   - `--tier tier_a` or `--tier tier_b`: Force tier assignment (default: auto-detected based on bedrooms and guest capacity).
   - `--limit N`: Limit price scraping to the first N open intervals.
   - `--force`: Force re-scraping profile even if cached.
   - `--push`: Commit and push to GitHub (only when user asks to push).

---

## 2. Removing a Competitor Listing

When a user asks to remove or delete a comp:

### Standard Operating Sequence:
1. **Execute `remove-comp`**:
   ```bash
   .venv/bin/python -m src.cli remove-comp <listing_id_or_url>
   ```

2. **What Happens Under the Hood**:
   - Unregisters the listing ID from `tier_a`, `tier_b`, and `disqualified` in `config/comps_registry.json`.
   - Unregisters the listing from `config/listing_specs.json`.
   - Purges all single-comp cached pricing files in `data/cache/search_*_comp_{listing_id}.json`.
   - Removes `data/enriched_comps/{listing_id}.json`.
   - Automatically regenerates `docs/index.html`.

---

## 3. Scraping Prices for a Single Comp

When the user asks to scrape or refresh rates for an existing comp without re-evaluating its profile:

### Standard Operating Sequence:
1. **Execute `scrape-comp-prices`**:
   ```bash
   # Scrape all open intervals
   .venv/bin/python -m src.cli scrape-comp-prices <listing_id_or_url>

   # Scrape first N upcoming intervals
   .venv/bin/python -m src.cli scrape-comp-prices <listing_id_or_url> --limit 5

   # Scrape within a specific date window
   .venv/bin/python -m src.cli scrape-comp-prices <listing_id_or_url> --start-date 2026-10-01 --end-date 2026-12-31
   ```

2. **What Happens Under the Hood**:
   - Pulls unbooked intervals from `CalendarSegmenter(KivoyaClient())`.
   - Directs Playwright through the NordVPN proxy to `https://www.airbnb.com/rooms/{listing_id}?check_in={c_in}&check_out={c_out}&adults={capacity}`.
   - Intercepts Airbnb's internal GraphQL `StaysPdpSections` response.
   - Extracts exact total price and computes effective nightly rate (`total_price / nights`).
   - If blocked or unavailable, records status as `BOOKED / UNAVAILABLE`.
   - Caches into `data/cache/search_{c_in}_{c_out}_comp_{listing_id}.json`.
   - Automatically re-renders `docs/index.html`.

---

## 4. Operational Guardrails

> [!IMPORTANT]
> - **Proxy Enforcement**: Every request to Airbnb must route through the NordVPN proxy. Direct residential IP scraping is prohibited.
> - **Polite Delays**: Single-comp interval scraping includes human-like pauses (1.5–3.0s) between interval lookups to safeguard IP reputation.
> - **Chaining**: Whenever the user asks to "add a comp", always chain `add-comp` and `scrape-comp-prices` (or use `--scrape-prices`) so the comp has live dates in the report.

