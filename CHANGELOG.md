# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
