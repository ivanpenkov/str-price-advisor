# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2026-09-12

### Added
- **10-Worker Multi-IP NordVPN Stealth Fleet (`src/stealth_connection.py`)**:
  - Engineered dedicated `StealthConnectionManager` orchestrating up to 10 parallel local `pproxy` SOCKS5 forwarder bridges.
  - Strictly routes through out-of-state feeder tourist hubs (Los Angeles, San Francisco, Dallas, Chicago, Atlanta) with zero Phoenix IP leakage.
  - Implemented pre-flight Google health probing (`https://www.google.com`, ~270ms latency) and dynamic candidate hot-swapping across a 40+ candidate node pool on packet loss or edge authentication desync.
  - Added FIFO Playwright worker context queue (`asyncio.Queue` / `@asynccontextmanager lease_worker()`) eliminating socket leaks, process clutter, and port collisions (`get_free_port()`).
  - Added standalone diagnostic CLI command: `python -m src.cli test-stealth [--count N]`.

- **Competitor Sales Absorption & Velocity Engine (`src/competitor_sales_tracker.py`)**:
  - Automated daily snapshot diffing engine tracking competitor bookings across stay intervals.
  - Enforced direct calendar verification (`StaysPdpSections`) and snapshot reconciliation to eliminate false sales from search ranking fluctuations.
  - Constructed **2D Empirical Strategy Matrix** (4 Lead Horizons $\times$ Weekend/Midweek) with Bayesian shrinkage ($k=3$).
  - Built Monthly Advance Booking Windows table and absorption velocity KPIs.
  - Added SQLite transaction ledger (`data/reservations.db: competitor_sales`) and CLI command `python -m src.cli track-competitor-sales`.

- **6-Factor Luxury Valuation & Quality Rubric (`src/comp_evaluator.py`, `src/comp_curator.py`)**:
  - Upgraded comp evaluation from 5-factor to a rigorous **6-factor luxury rubric**:
    1. Lot Acreage & Privacy (15%)
    2. Living Sq Footage & Scale (15%)
    3. Bedroom / King Suite Composition (20%)
    4. Bath Ratio & Ensuite Convenience (15%)
    5. Resort Amenities & Outdoor Living (25%)
    6. Rating, Reviews & Superhost Status (10%)
  - Integrated market asset valuation benchmarking comparing competitor properties against Villa del Sol's $2.0M baseline.
  - Added CLI command `python -m src.cli evaluate-comps`.

- **Cross-Platform Real-Time Price Parity Comparator (`src/platform_comparator.py`)**:
  - Concurrent multi-interval and multi-channel scraping across Airbnb, VRBO, Booking.com, and Kivoya Direct using leased proxy contexts.
  - Multi-attempt context rotation across distinct cities on transient network or `ERR_EMPTY_RESPONSE` errors.
  - Automatic fallback to verified Step 3 price cache (Airbnb) or Kivoya PMS rate feed projections (Booking.com / VRBO) with fee and tax normalization.
  - Added CLI command `python -m src.cli compare-platforms` and integration into `run --weekly --compare-platforms`.

- **Two-Stage Corridor Search & 3-Attempt Transient Retry (`src/airbnb_collector.py`)**:
  - Implemented Stage 1 multi-page cursor-based corridor pagination (`get_search_cursor()`) across 4 regional markets.
  - Implemented Stage 2 multi-IP direct comp fallback guaranteeing 100% comp accounting for all listings in `config/comps_registry.json`.
  - Added 3-attempt transient navigation retry with polite `AIRBNB_RETRY_DELAY=1.0s` backoff, warning suppression until attempt 3, and cache poisoning prevention.

- **Expanded 10-Tab Web Dashboard (`src/html_generator.py`)**:
  - Expanded dashboard navigation to 10 comprehensive tabs:
    1. 📊 Pricing Recommendations
    2. 🌐 Channel Comparison
    3. ⚙️ Streamline PMS Diagnostics
    4. 🏡 Curated Luxury Comps Registry (109 Comps)
    5. 🎯 Comps Sales & Absorption Matrix
    6. 📅 Rolling Availability Calendar
    7. 📑 Reservations Ledger
    8. 📈 Cumulative Revenue Pacing
    9. 📐 Methodology & PMS Guide
    10. 🛠️ Live Data & Debug

---

## [1.0.0] - 2026-09-06

### Added
- **Streamline OwnerX Reservation Sync**:
  - Implemented automated API ingestion (`src/ownerx_client.py`) connecting to Streamline OwnerX (`ownerx.streamlinevrs.com`).
  - Extracted 206 historical and future bookings for Villa del Sol (2022–2027) with CSRF session rotation.
  - Persisted normalized records into indexed SQLite database (`data/reservations.db`) and pretty-printed `data/reservations.json`.
  - Added CLI command `python -m src.cli sync-reservations` supporting `--days-back 60`, `--full`, and `--dashboard` triggers.

- **Interactive Availability Calendar Tab (`📅 Calendar`)**:
  - Replicated Streamline OwnerX's 6-month rolling calendar grid with past/future multi-month navigation.
  - Added **Dual Color Mode Switch (`Color by: Status | Channel`)** to toggle between reservation statuses and booking channels.
  - Added dynamic legend swap updating color swatches between Status and Channel modes.
  - Made reservations interactive: clicking any booked date opens the detailed reservation modal.

- **Interactive Reservation Details Modal**:
  - Displays reservation details, confirmation ID, full dates, nights, guests, status, and financial breakdown.
  - Added channel badge and parsed channel source (Airbnb, Vrbo, Booking.com, Expedia, Kivoya Direct, Kivoya Admin, Owner Block).
  - Added full raw JSON inspector viewer with collapsible view for auditing Streamline API payloads.
  - Optimized modal display for instant rendering (0ms lag) without animation delay.

- **Dedicated Reservations Tab (`📋 Reservations`)**:
  - Interactive table displaying all past, current, and future reservations.
  - Columns: Dates & Confirmation ID, Stay Type (Weekend, Midweek, Mix), Nights, Gross Rent (Owner + Kivoya Commission), Channel, Total on Channel (Est.), and Guest Checkout Price (Est.).
  - Added live text search (confirmation ID, dates, status, type, channel) and quick filter pills (`All`, `Future`, `Past`, `Airbnb`, `Vrbo`, `Direct Website`, `Booking.com`, `Expedia`).
  - Added multi-column bidirectional sorting across dates, stay types, nights, and monetary columns.

- **Cumulative Annual Revenue Pacing (`📈 Revenue`)**:
  - Daily accrual revenue pacing engine distributing multi-night stays evenly per night stayed.
  - Interactive Chart.js cumulative pacing curves comparing 2022, 2023, 2024, 2025, 2026, and 2027 advance pipeline.
  - Executive KPI summary cards: YTD Revenue, YoY % growth, On-the-Books Total, Nights Booked, and Net Owner ADR.

- **Precision Channel Fee & Tax Engine**:
  - **Airbnb**: Includes 14.15% guest service fee in pre-tax Total on Channel, plus 12.52% statutory lodging taxes in checkout price.
  - **Booking.com**: Includes 6.0% service charge in pre-tax Total on Channel, plus 30.5% statutory taxes (16% VAT + 14.5% local tax) in checkout price.
  - **Vrbo**: Includes 14.48% syndication markup + $550 cleaning in Total on Channel, plus 11.5% traveler fee + 14.07% STR taxes in checkout price.
  - **Kivoya Direct**: Aligned with live Streamline VRS quote engine:
    - Base accommodation subtotal
    - $550.00 cleaning fee
    - 6.0% processing fee
    - 3.0% administrative fee
    - Total before taxes = Base + Cleaning + Fees
    - 14.07% statutory taxes (5.5% AZ State + 1.77% Maricopa County + 1.8% Tempe Hotel + 5.0% Tempe Hotel/Motel)
    - Total Guest Checkout Price matching live `kivoya.com` checkout to the penny ($2,810.56 on sample 4-night stay).
  - Added `get_pre_reservation_quote()` and `calculate_direct_quote()` in `src/kivoya_client.py`.
  - Added hover tooltips on table column headers and cells showing the algebraic formulas and full variable substitution calculations for verification and debugging.
  - Added dedicated **Total before taxes (Total on Channel)** line item to the Channel Comparison derivation matrix.
