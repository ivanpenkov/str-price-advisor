# STR Price Advisor: SQLite to Cloud Database Migration Requirements

- **Document Version**: 1.1.0
- **Status**: APPROVED (via Architecture Grill-Me Alignment)
- **Target Platform**: Turso (LibSQL / Managed Cloud SQLite)
- **Scope**: Tier 1 Relational Data Stores (`data/reservations.db`)
- **Author**: Antigravity Pair Programmer
- **Date**: 2026-09-19

---

## 1. Executive Summary & Problem Context

The **STR Competitive Price Advisor** for **Villa del Sol** (Tempe, AZ) currently stores its ground-truth financial reservations, competitor sales detections, and historical calendar rate snapshots in a local SQLite database (`data/reservations.db`). 

As detailed in [docs/storage.md](file:///Users/ivanpe/str-price-advisor/docs/storage.md), the system is evolving from a single-host deployment (Apple Silicon Mac Mini) to a multi-device operational topology consisting of:
1. **One Dedicated Host (Mac Mini)** running automated `launchd` background daemons for daily PMS synchronization, competitor rate scraping, sales detection, and dashboard compilation.
2. **Two Active Contributor Laptops** (Laptop A and Laptop B) used for code development, pricing strategy audits, manual ad-hoc scrapes, and local dashboard compilation.
3. **A Planned Mobile Client** (iOS / Android) for property managers to inspect real-time booking pacing, competitor sales velocity, and pricing health on the go.

### 1.1 The Core Problem
Because `data/reservations.db` is `.gitignore`d to prevent binary Git merge collisions:
- **Fresh-Clone Breakdown**: Secondary laptops cloning the repository have no database. Running `generate-html` renders blank reservation pacing charts and empty competitor absorption metrics unless users manually copy the 1.1 MB binary database over AirDrop/SCP or run a full PMS sync.
- **Split-Brain State Divergence**: Sales detections and rate snapshots discovered on one machine remain trapped on that local disk. If the Mac Mini detects 5 new competitor bookings during its Sunday full scan, laptops running local commands remain unaware of those sales.
- **Mobile Client Isolation**: A mobile application cannot query a local `.db` file located on an office Mac Mini without complex network tunnels, local daemon uptime dependencies, or fragile static exports.
- **Git Merge Collisions on JSON Backups**: Concurrently committing `data/reservations.json` across multiple machines produces multi-thousand-line Git merge conflicts.

This document defines the formal engineering and operational requirements for migrating all relational SQLite state and access patterns to a managed, edge-distributed cloud database.

---

## 2. Comprehensive Comparative Cloud Database Evaluation

To address zero-cost constraints, multi-device concurrency, mobile client readiness, and code refactoring risk, five candidate cloud database platforms were evaluated:

| Evaluation Dimension | **Turso (LibSQL)** *(Selected)* | **Supabase (Managed PostgreSQL)** | **Cloudflare D1 (Serverless SQLite)** | **Neon (Serverless Postgres)** | **Firebase Firestore (NoSQL)** |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Free Tier Storage** | **9 GB total** across 500 databases | **500 MB** total database storage | **5 GB** database storage | **0.5 GB** storage | **1 GB** storage |
| **Free Tier Usage** | **1 Billion row reads/mo**, 25 Million row writes/mo | **50,000 Monthly Active Users**, 2 GB egress | **5 Million reads/day**, 100k writes/day | Free compute hours (shared) | **50k reads/day**, 20k writes/day |
| **SQL Compatibility** | **100% SQLite 3 syntax & types** (LibSQL fork) | PostgreSQL syntax (requires dialect refactor) | SQLite 3 dialect (Cloudflare edge) | PostgreSQL dialect | None (Document NoSQL model) |
| **Python Driver Ergonomics** | Native `libsql-client` (pure HTTP/WebSocket client) | `psycopg2` / `asyncpg` / `supabase-py` | HTTP REST API / Cloudflare Workers | `psycopg2` / `asyncpg` | `firebase-admin` SDK |
| **Mobile Access** | Direct HTTPS LibSQL client (Swift, Kotlin, React Native) | Auto-generated REST (PostgREST) + Auth SDK | Requires Cloudflare Worker proxy | Requires custom API server | Native Firebase iOS/Android SDKs |
| **Mobile Scoped Tokens** | Native read-only tokens (`turso db tokens create --read-only`) | Row-Level Security (RLS) + Anon JWT key | Cloudflare API tokens (broad account scope) | Not natively supported at edge | Firebase Security Rules |
| **Inactivity / Pausing** | **Never pauses / No cold starts** | **Auto-pauses after 7 days** of inactivity | Never pauses / No cold starts | **Cold start delays (1–3s)** after idle | Never pauses |
| **Code Refactoring** | **Minimal**: Keep 100% of existing SQL schemas, indexes, and queries | **High**: Rewrite DDL, constraints, parameter markers (`%s`), upserts | **Medium**: Wrap queries in HTTP API calls | **High**: Rewrite DDL and Postgres queries | **Extreme**: Redesign relational tables to collections |
| **Cost Risk** | Zero (Current DB is 1.1 MB; uses 0.012% of free tier) | Zero (within 500 MB limit) | Zero (within 5 GB limit) | Risk of compute hour throttling | Zero (within daily read limits) |

### 2.1 Why Alternative Options Were Eliminated
1. **Supabase (Managed PostgreSQL)**:
   - *Elimination Factors*: Requires converting SQLite dialect to Postgres (e.g., parameter markers `%s` or `$1` instead of `?`, replacing `INSERT OR REPLACE` with `ON CONFLICT (...) DO UPDATE`, mapping `sqlite3.Row` semantics). In addition, Supabase free tier databases automatically **pause after 7 days of inactivity**, creating an operational reliability risk if Mac Mini daemons are temporarily paused or during holiday freezes.
2. **Cloudflare D1 (Serverless SQLite)**:
   - *Elimination Factors*: Cloudflare D1 is primarily designed to be queried inside Cloudflare Workers (JavaScript/TypeScript runtime). It lacks a native Python DB-API driver or standard TCP/WebSocket wire protocol. Interacting from Python CLI or mobile apps requires executing ad-hoc HTTP REST calls against Cloudflare's management API or deploying and maintaining a custom Cloudflare Worker proxy.
3. **Neon (Serverless Postgres)**:
   - *Elimination Factors*: Cold starts introduce 1–3 second connection latencies when the instance scales to zero. It also suffers from the same PostgreSQL SQL dialect refactoring penalty as Supabase without offering Supabase's turn-key mobile SDKs.
4. **Firebase Firestore (NoSQL)**:
   - *Elimination Factors*: Demands a complete paradigm shift from relational relational algebra (joins, window functions, B-tree indexes) to document collections. Financial booking ledgers and competitor sales analysis heavily rely on relational aggregations, unique compound constraints, and transactional consistency.

### 2.2 Selection Justification: Turso (LibSQL)
Turso was selected as the target cloud platform based on the following decisive factors:
1. **Zero SQL Rewrite**: Because LibSQL is a drop-in open-source fork of SQLite, all existing table schemas, indexes, constraints (`UNIQUE(listing_id, check_in, check_out)`), UPSERT clauses, and `?` parameter markers in `ReservationStore`, `CompetitorSalesTracker`, and `ReservationIntelligence` remain 100% intact.
2. **Stateless Direct Remote Connection**: Mac Mini, laptops, and mobile clients connect directly over encrypted HTTPS/WebSocket via `libsql-client` without creating local replica file locks or requiring intermediate sync daemons.
3. **Generous Headroom**: The STR Price Advisor database currently holds ~4,500 rows across 1.1 MB. Turso's free allocation of 9 GB and 1 Billion reads/month provides decades of operational headroom at zero cost.
4. **Independent Mobile Connectivity with Scoped Tokens**: Property managers using a native mobile client can query Turso directly using scoped, read-only API tokens (`turso db tokens create --read-only`) without relying on the desktop Mac Mini being powered on.

---

## 3. Data Scope & Invariants

### 3.1 Included Scope: Tier 1 Relational Tables
The migration is strictly scoped to the 4 relational tables currently hosted in `data/reservations.db`:

| Table Name | Current Rows | Current Size | Description & Role |
| :--- | :---: | :---: | :--- |
| **`reservations`** | 209 | ~350 KB | Ground-truth booking ledger for Villa del Sol scraped from Streamline OwnerX PMS, including gross rent, fees, payouts, guest counts, and dates. |
| **`sync_history`** | 31 | ~10 KB | Operational audit log of PMS ingestion jobs, tracking timestamps, sync modes, records fetched, and records upserted. |
| **`competitor_sales`** | 82 | ~120 KB | Empirical market absorption records detecting confirmed competitor bookings by diffing consecutive pricing snapshots. |
| **`property_rate_snapshots`** | 4,224 | ~620 KB | Longitudinal ledger of Kivoya published calendar rates for Villa del Sol across future calendar intervals. |

### 3.2 Deprecation & Removal: Local JSON Reservation Ledger
- **`data/reservations.json`**: Discontinue exporting and committing `data/reservations.json`. The cloud database serves as the single authoritative ground truth. Removing this file permanently eliminates multi-thousand-line Git merge conflicts on laptops.

### 3.3 Excluded Scope: Stores Retained in Current Architecture
Per architectural agreement, the following stores remain in their current form:
- **`config/comps_registry.json` & `config/listing_specs.json`**: Retained in Git repository as version-controlled JSON for human PR review and auditability.
- **`data/ratings_reviews.json`**: Retained in Git repository as a lightweight (88 KB) canonical review scorecard.
- **`data/pricing_data_YYYY-MM-DD.json`**: Retained as daily timestamped JSON snapshots on disk and tracked in Git. Decoupling snapshots into Cloudflare R2 (10 GB free object storage) is documented as a Tier 2 roadmap phase.
- **`data/cache/**`**: Retained as ephemeral local scraping cache on the scraping host.

---

## 4. Functional Requirements

### FR1: Centralized Cloud Relational Store
- **FR1.1**: The system MUST store `reservations`, `sync_history`, `competitor_sales`, and `property_rate_snapshots` in a managed Turso cloud database accessible over HTTPS.
- **FR1.2**: All primary keys, unique constraints (`UNIQUE(listing_id, check_in, check_out)`, `UNIQUE(calendar_date, snapshot_date)`), foreign keys, and indexes MUST match the production SQLite schema with zero semantic divergence.
- **FR1.3**: The cloud database MUST support atomic transactions (`BEGIN TRANSACTION`, `COMMIT`, `ROLLBACK`) across all mutating operations.

### FR2: Multi-Device Operational Parity
- **FR2.1 (Mac Mini - Automated Ingestion)**: Scheduled launchd scripts (`run_pms_sync.sh`, `run_daily_quickscan.sh`, `run_weekly_fullscan.sh`) running on the Mac Mini MUST write newly synced reservations, sales detections, and rate snapshots directly to Turso.
- **FR2.2 (Contributor Laptops - Zero-State Operation)**: A fresh clone of the repository on any laptop with valid `.env` credentials MUST be able to run `python -m src.cli generate-html`, `python -m src.cli status`, and `python -m src.cli run` without needing any pre-existing local `.db` file or manual file copies.
- **FR2.3 (Concurrent Query Safety)**: Simultaneous read operations across all 3 Macs MUST NOT encounter database locking errors (`OperationalError: database is locked`).
- **FR2.4 (Mutating Concurrency)**: If two machines record competitor sales or sync rates concurrently, Turso's cloud coordination MUST serialize transactions and respect unique constraints without data corruption.

### FR3: Direct Remote Connection Mode
- **FR3.1**: Production operations on all machines MUST connect directly to Turso Cloud over HTTPS/WebSocket via `libsql-client`.
- **FR3.2**: The system MUST NOT require or create persistent local embedded replica files (`data/reservations.db`) on developer laptops or the Mac Mini during standard operation.

### FR4: Full DB-API 2.0 / `sqlite3` Compatible Adapter
- **FR4.1 (Interface Parity)**: The unified database adapter (`src/database.py`) MUST provide an interface fully compatible with Python's standard `sqlite3` connection and cursor semantics:
  - `conn.cursor()` returning a functional cursor.
  - `cursor.execute(sql, params)` supporting both positional `?` and named `:param` bindings.
  - `cursor.executemany(sql, param_seq)` executing batch operations atomically.
  - `cursor.fetchone()` and `cursor.fetchall()`.
  - `cursor.description` returning a sequence of column descriptors matching DB-API 2.0.
  - `cursor.lastrowid` and `cursor.rowcount`.
  - Row factory wrapper providing both dict-key access (`row["id"]`) and index access (`row[0]`).
  - Context manager transaction support (`with conn:`).
- **FR4.2 (Zero Caller SQL Refactoring)**: Existing repository code in `ReservationStore`, `CompetitorSalesTracker`, `ReservationIntelligence`, and `KivoyaClient` MUST NOT require changes to their SQL query strings.

### FR5: Mobile Application Accessibility
- **FR5.1**: The database architecture MUST allow a native mobile client (iOS Swift, Android Kotlin, or React Native) to query Turso directly using HTTPS.
- **FR5.2**: The mobile client MUST authenticate using a dedicated **scoped read-only token** (`turso db tokens create --read-only`), preventing compromised mobile clients from mutating or dropping production financial data.
- **FR5.3**: Mobile queries MUST be able to fetch the latest reservation pacing summary, rate evolution, and competitor sales records in sub-second roundtrip latency.

### FR6: Hermetic Offline Testing & Fast-Testing Protocol
- **FR6.1 (Test Independence)**: All automated unit tests in `tests/` MUST run without requiring internet access, remote network calls, or valid Turso API tokens.
- **FR6.2 (Speed Invariant)**: In accordance with the project's [fast_development_and_testing.md](file:///Users/ivanpe/str-price-advisor/.gemini/config/rules/fast_development_and_testing.md) protocol, unit tests touching database stores MUST execute against in-memory (`:memory:`) or temporary local SQLite files in `<0.2s`.
- **FR6.3 (Automatic Route Isolation)**: Whenever a module instantiates a store with an explicit `db_path` that is `:memory:` or differs from the default database path, `get_db_connection()` MUST automatically route to standard library `sqlite3.connect()`.

### FR7: Deprecation of Local JSON Reservation Ledger
- **FR7.1**: `ReservationStore` MUST discontinue exporting `data/reservations.json` on sync.
- **FR7.2**: `push_to_github()` MUST NOT stage `data/reservations.json`.
- **FR7.3**: All analytical components (`generate-html`, `status`, `run`) MUST read reservations directly from the cloud database adapter rather than relying on `data/reservations.json`.

### FR8: Automated Cloud Database Backup & Disaster Recovery
- **FR8.1 (Daily Backup Script)**: A dedicated CLI command (`python -m src.cli backup-cloud-db`) MUST dump the entire Turso Cloud database into a local compressed SQL archive: `data/backups/turso_backup_YYYY-MM-DD.sql.gz`.
- **FR8.2 (Automated Rotation)**: The backup utility MUST automatically prune backups older than 30 days.
- **FR8.3 (Scheduled Execution)**: The Mac Mini `run_pms_sync.sh` daemon MUST execute `backup-cloud-db` daily immediately following the morning PMS sync.
- **FR8.4 (On-Demand Cloud Dumps)**: The system MUST support manual cloud database dumps via the Turso CLI (`turso db dump <db-name>`).

### FR9: Dynamic Configuration & Local SQLite Override
- **FR9.1**: Cloud connection parameters MUST be configured via environment variables:
  - `TURSO_DATABASE_URL`: `libsql://<db-name>-<org>.turso.io`
  - `TURSO_AUTH_TOKEN`: Admin Bearer token.
- **FR9.2 (Dual-Check Routing)**: The database adapter MUST connect to Turso Cloud only when `TURSO_DATABASE_URL` is set AND `USE_LOCAL_SQLITE` is NOT set to `1` or `true`.
- **FR9.3 (Local Override)**: Setting `USE_LOCAL_SQLITE=1` in `.env` MUST force the adapter to connect to local `data/reservations.db` using standard `sqlite3`, enabling zero-network offline work without modifying credentials.

### FR10: Diagnostic CLI Command & UI Status Indication
- **FR10.1 (Diagnostic Command)**: A new CLI command `python -m src.cli check-db` MUST test Turso Cloud connectivity, measure roundtrip network latency, verify schema existence, and display row counts across all 4 tables in a formatted terminal table.
- **FR10.2 (CLI Status Output)**: `python -m src.cli status` MUST display the active database backend (`Turso Cloud (libsql://...)` vs `Local SQLite (data/reservations.db)`).
- **FR10.3 (Dashboard UI Badge)**: The HTML dashboard (`docs/index.html`) MUST dynamically render the active data store badge:
  - Cloud: `<span class="badge bg-green">Cloud: Turso (LibSQL)</span>`
  - Local: `<span class="badge bg-yellow">Local: SQLite (data/reservations.db)</span>`

### FR11: Migration Tooling & Verification
- **FR11.1 (Automated Seeding)**: A dedicated CLI command (`python -m src.cli migrate-to-turso`) MUST extract all historical records from local `data/reservations.db` and upload them to Turso in batch transactions.
- **FR11.2 (Integrity Verification)**: The migration tool MUST compute and report row-count parity and column-level checksums between the source SQLite database and target Turso database before declaring migration success.
- **FR11.3 (Idempotence)**: Running the migration command multiple times MUST be safe and idempotent, utilizing UPSERT logic (`INSERT OR REPLACE` / `ON CONFLICT DO UPDATE`) to prevent duplicate row creation.
- **FR11.4 (Pre-Migration Snapshot)**: The migration tool MUST create a local backup of `data/reservations.db` (`data/reservations_pre_turso_YYYYMMDD.db`) before modifying data.

---

## 5. Non-Functional Requirements

### NFR1: Performance & Latency Budgets
- **NFR1.1 (Read Latency)**: CLI reporting commands (`generate-html`, `status`) querying Turso over HTTPS MUST execute and return all analytical records within **< 1.5 seconds** total network overhead.
- **NFR1.2 (Write Latency)**: Daily automated PMS syncs upserting 200+ reservations in a batch transaction MUST complete in **< 3.0 seconds**.
- **NFR1.3 (Connection Reuse)**: The Python client MUST maintain a persistent HTTP session / connection pool across queries within a single CLI command invocation rather than establishing a fresh TLS handshake for every query.

### NFR2: Availability & Resilience
- **NFR2.1 (Network Blip Resilience)**: The database adapter MUST incorporate automatic exponential backoff retry logic (up to 3 attempts with 0.5s–2.0s jitter) for transient HTTP 5xx or connection reset errors.
- **NFR2.2 (Offline Graceful Degradation)**: If a developer laptop runs `generate-html` while completely disconnected from the internet, the system MUST log a clear explanatory warning rather than crashing with an unhandled traceback.

### NFR3: Security & Secrets Management
- **NFR3.1**: Turso database credentials (`TURSO_DATABASE_URL` and `TURSO_AUTH_TOKEN`) MUST be stored exclusively in `.env` and `.env.example` templates, and MUST NEVER be committed to Git.
- **NFR3.2**: Separate tokens MUST be provisioned based on principle of least privilege:
  - `TURSO_ADMIN_TOKEN`: Full read/write access for Mac Mini daemons and developer workstations.
  - `TURSO_READONLY_TOKEN`: Strictly read-only access for mobile applications and public/semi-public audit scripts.

### NFR4: Capacity & Free Tier Headroom
- **NFR4.1 (Storage Footprint)**: Total relational storage in Turso MUST remain under 50 MB over a 3-year projection (current size: 1.1 MB; growth rate: ~10 MB/year from daily rate snapshots). This utilizes less than 0.6% of Turso's 9 GB free allowance.
- **NFR4.2 (Read/Write Operations)**: Daily operations (Mac Mini + 2 laptops + mobile) are projected to consume ~150,000 row reads/month and ~45,000 row writes/month, well below Turso's monthly free limits of 1,000,000,000 reads and 25,000,000 writes.

---

## 6. Traceability Matrix

| Requirement | Addressed In Architecture & Design | Verification Plan |
| :--- | :--- | :--- |
| **FR1 (Central Cloud Store)** | `docs/migrating_sqlite_to_cloud_design.md#2-cloud-database-schema-specification` | Connect to Turso Cloud and execute DDL; inspect table schemas. |
| **FR2 (Multi-Device Parity)** | `docs/migrating_sqlite_to_cloud_design.md#3-unified-database-adapter-design` | Clone to fresh temp dir; run `generate-html` using Turso env vars. |
| **FR3 (Direct Remote Mode)** | `docs/migrating_sqlite_to_cloud_design.md#3-unified-database-adapter-design` | Verify zero local `.db` files generated in `data/` on fresh run. |
| **FR4 (sqlite3 Adapter Parity)**| `docs/migrating_sqlite_to_cloud_design.md#3-unified-database-adapter-design` | Verify `conn.cursor()`, `cursor.description`, and `with conn:` on Turso. |
| **FR5 (Mobile App Access)** | `docs/migrating_sqlite_to_cloud_design.md#6-mobile-client-integration-architecture` | Test direct HTTPS queries using scoped read-only bearer token. |
| **FR6 (Hermetic Testing)** | `docs/migrating_sqlite_to_cloud_design.md#7-test-isolation-and-fast-development-protocol-compliance` | Run unit tests with no env vars; verify <0.2s pass. |
| **FR7 (JSON Deprecation)** | `docs/migrating_sqlite_to_cloud_design.md#4-component-refactoring-specification` | Confirm `data/reservations.json` is not modified or pushed on sync. |
| **FR8 (Automated Backups)** | `docs/migrating_sqlite_to_cloud_design.md#9-automated-backup-and-disaster-recovery-plan` | Run `python -m src.cli backup-cloud-db`; verify archive created and rotated. |
| **FR9 (Configuration & Override)**| `docs/migrating_sqlite_to_cloud_design.md#3-unified-database-adapter-design` | Verify `USE_LOCAL_SQLITE=1` routes to `data/reservations.db`. |
| **FR10 (Diagnostics & UI)** | `docs/migrating_sqlite_to_cloud_design.md#5-migration-and-diagnostic-tooling` | Run `python -m src.cli check-db`; verify dashboard badge. |
| **FR11 (Migration Tooling)** | `docs/migrating_sqlite_to_cloud_design.md#5-migration-and-diagnostic-tooling` | Run `python -m src.cli migrate-to-turso`; verify checksum and row counts. |
| **NFR1 (Performance)** | `docs/migrating_sqlite_to_cloud_design.md#3-unified-database-adapter-design` | Benchmark query execution time in `generate-html`. |
| **NFR3 (Security)** | `docs/migrating_sqlite_to_cloud_design.md#6-mobile-client-integration-architecture` | Verify read-only token rejects `INSERT` and `DELETE` queries. |
