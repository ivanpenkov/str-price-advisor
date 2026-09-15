# Villa del Sol Review Scraping Engine
**Product Requirements Document (PRD) & Functional Specification**  
`review_scraping_engine_requirements.md`

---

## 1. Executive Summary & Problem Statement

### 1.1 Background & Context
**Villa del Sol** (Tempe, AZ) maintains an active guest reputation portfolio across four primary short-term rental channels:
- **Airbnb**: 77 lifetime reviews (Rating: 4.83 / 5.0)
- **VRBO / Expedia**: 34 lifetime reviews (Rating: 9.6 / 10.0, *Loved by Guests • Top 10%*)
- **Booking.com**: 18 lifetime reviews (Rating: 9.4 / 10.0)
- **Kivoya Direct / Streamline**: 12 lifetime reviews (Rating: 5.0 / 5.0)
- **Total Portfolio**: **141 lifetime guest reviews**.

Currently, the local database (`data/ratings_reviews.json`) and dashboard review feed display only **8 baseline seed reviews**. When running `.venv/bin/python -m src.cli sync-ratings --backfill`, execution terminates immediately with 0 reviews ingested due to:
1. **Unwired Proxy Manager**: `run_sync_ratings` in `src/cli.py` initializes `RatingsCollector` without passing `proxy_mgr`, activating an offline safety guard that aborts network requests.
2. **Kivoya SSL Certificate Failure**: Direct HTTP requests fail under macOS Python environments without system CA certificate bundles (`SSL: CERTIFICATE_VERIFY_FAILED`).
3. **Unimplemented Live Crawler Loop**: Step 3 of `sync_channel` in `src/ratings_collector.py` is a placeholder stub that returns `new_reviews_added: 0` without launching browser automation or paginating review dialogs.

### 1.2 System Purpose & Objectives
The **Review Scraping Engine** is the automated web ingestion subsystem responsible for harvesting live platform ratings, category sub-scores, and complete historical guest feedback text across all four platforms without human intervention.

**Primary Objectives**:
1. **End-to-End Live Crawling**: Implement robust Playwright browser automation and network response interception for Airbnb, VRBO, Booking.com, and Kivoya Direct.
2. **Historical Backfill (`--backfill`)**: Automatically open review modals and paginate through all 141+ historical reviews, capturing the entire review archive.
3. **Fast Daily Incremental Sync (Default)**: Rapidly sync latest ratings and newest reviews, terminating pagination as soon as known review IDs are encountered (<15s per platform).
4. **Mandatory Stealth Proxy Protection**: Route all scraping requests through out-of-state NordVPN proxy forwarders (`feeder-sf`, `feeder-dal`, `feeder-chi`), enforcing the project's Zero Phoenix Policy to prevent IP bans and detection.
5. **Deterministic Deduplication & In-Place Host Response Updates**: Use MD5 hashing to prevent duplicate records and automatically update existing reviews when property management posts new host responses.
6. **Zero-Sleep Test Environment Compliance**: Provide 100% test coverage using mocked network responses and Playwright fixtures executing in $< 2.0\text{s}$.

### 1.3 Confirmed Architectural Decisions from Interactive Alignment (`/grill-me`)
Through the interactive `/grill-me` alignment review, all key design trade-offs and operational decisions were finalized and confirmed:
1. **Shared Chromium Instance with Dedicated Contexts (`FR-ORCH-01`)**: Multi-channel crawling utilizes a single shared Playwright Chromium browser process with isolated `BrowserContext` objects (each configured with its respective corridor proxy: SF, Dallas, Chicago). Kivoya Direct runs via direct HTTPS without a browser context. This limits memory consumption to $\le 350\text{MB}$ and maintains $< 1\text{s}$ startup overhead.
2. **Transparent Candidate Failover & AFRINIC Blacklist (`FR-PRX-04`)**: Hostnames resolving to African subnets (`196.0.0.0/8`, etc.) are permanently excluded. If a platform's designated regional corridor (e.g. Dallas for VRBO or SF for Airbnb) is unhealthy or unreachable, the engine transparently reassigns an active standby node from `proxy_mgr.endpoints` (e.g., Chicago, New York, or US Anycast) while maintaining strict out-of-state Zero Phoenix compliance.
3. **Incremental Early Stopping & Modal Sort Order (`FR-DAT-03`)**: In daily incremental sync, the scraper attempts to select "Most recent" sorting in the review modal if present, terminating pagination as soon as 3 consecutive reviews match existing records with identical host replies. If sorting cannot be modified, incremental pagination is bounded to at most 1–2 scroll batches.
4. **Kivoya Direct Ingestion Priority (`FR-KV-01`–`03`)**: Kivoya Direct always executes the direct Streamline REST POST query first using resilient CA SSL context (`certifi.where()` with fallback to unverified context for macOS Python) in $\approx 300\text{ms}$. Playwright AngularJS evaluation is invoked strictly as a fallback if the REST endpoint fails or returns 0 reviews.
5. **Dry-Run Full Preview Mode (`FR-CLI-01`)**: `--dry-run` performs live crawling through leased proxies and displays a rich terminal preview table of parsed scorecards, newly detected reviews, and in-place host response changes, while strictly skipping file writes to `data/ratings_reviews.json` and skipping HTML regeneration.
6. **Zero-Sleep Test Environment Invariant (`NFR-TEST-01`, `SEC-01`)**: Mock fixtures are established under `tests/fixtures/` and executed with `stealth_delay = 0.0`, ensuring the complete test suite runs in $< 2.0\text{s}$ prior to dispatching the mandatory 2-round subagent code review.

---

## 2. Channel Registry & Ingestion Target Matrix

| Channel | Identifier / Endpoint URL | Native Scale | Target Review Volume | Primary Ingestion Protocol | Proxy Corridor |
| :--- | :--- | :---: | :---: | :--- | :--- |
| **Airbnb** | [Room 573857947793833342](https://www.airbnb.com/rooms/573857947793833342) | 1.0 – 5.0 | ~77 reviews | Playwright + GraphQL Interception (`PdpReviews` / `StayProductDetailPage`) + Modal Pagination | Mandatory NordVPN San Francisco (`feeder-sf`) |
| **VRBO** | [Listing 2685684](https://www.vrbo.com/2685684) | 1.0 – 10.0 | ~34 reviews | Playwright + Apollo GraphQL Interception (`propertyReviewSummary`, `reviewList`) + DOM | Mandatory NordVPN Dallas (`feeder-dal`) |
| **Booking.com** | [Villa del Sol House by Kivoya](https://www.booking.com/hotel/us/villa-del-sol-amazing-house-by-kivoya.html) | 1.0 – 10.0 | ~18 reviews | Playwright + AWS WAF Auto-Resolution + Reviews Modal Navigation | Mandatory NordVPN Chicago (`feeder-chi`) |
| **Kivoya Direct** | [Property 503802](https://www.kivoya.com/503802/) | 1.0 – 5.0 | ~12 reviews | Direct REST AJAX (`wp-admin/admin-ajax.php?action=streamlinecore-api-request`) with CA SSL Context + Playwright Fallback | Direct HTTPS (Fallback: NordVPN Anycast) |

---

## 3. Functional Requirements

### 3.1 Proxy Architecture & Stealth Routing
- **FR-PRX-01 (Mandatory Proxy Routing)**: All external browser requests to Airbnb, VRBO, and Booking.com MUST route through local HTTP forwarders (`pproxy`) backed by out-of-state NordVPN SOCKS5 servers. Direct, unproxied connections to these OTA platforms are strictly prohibited.
- **FR-PRX-02 (Feeder Corridor Isolation)**: Distinct geographical feeder hubs must be leased per platform:
  - Airbnb: San Francisco corridor (`feeder-sf-1` / `san-francisco.us.socks.nordhold.net:1080`).
  - VRBO: Dallas corridor (`feeder-dal-1` / `dallas.us.socks.nordhold.net:1080`).
  - Booking.com: Chicago corridor (`feeder-chi-1` / `chicago.us.socks.nordhold.net:1080`).
- **FR-PRX-03 (Automated Proxy Lifecycle)**: The CLI and engine must manage proxy leasing asynchronously: starting forwarders before browser launch and cleanly terminating all child forwarder processes in a `finally` block upon completion or failure.
- **FR-PRX-04 (Transparent Candidate Failover)**: If a platform's designated regional proxy corridor fails pre-flight verification or is temporarily unreachable, the engine transparently fails over to an active standby out-of-state hub (e.g. New York or US Anycast from `CANDIDATE_STEALTH_SERVERS`), preserving Zero Phoenix compliance without aborting the crawl.
- **FR-PRX-05 (AFRINIC Subnet Blacklist & GeoIP Pre-Flight Verification)**: The engine must strictly reject candidate hostnames resolving to African IP blocks (`196.0.0.0/8`, `102.0.0.0/8`, `105.0.0.0/8`, `41.0.0.0/8`). During startup, forwarder loopbacks must execute an automated pre-flight GeoIP check (`http://ip-api.com/json`) confirming `country == "United States"` prior to accepting the bridge.

### 3.2 Platform-Specific Scraping Mechanics

#### 3.2.1 Airbnb Scraper
- **FR-AB-01 (Network Interception)**: Attach an async response listener to capture GraphQL calls matching `/api/v3/` or `/graphql` containing operation names `StayProductDetailPage`, `StaysPdpSections`, or `PdpReviews`.
- **FR-AB-02 (Aggregate & Sub-Scores Extraction)**: Extract overall rating (e.g. `4.83`), review count (e.g. `77`), and category sub-scores (`cleanliness`, `accuracy`, `communication`, `location`, `check_in`, `value`).
- **FR-AB-03 (Modal Trigger & Interaction)**: If reviews on the initial page load are incomplete, locate and click the reviews modal button (`button[data-testid*="pdp-show-all-reviews-button"]`, `button:has-text("reviews")`, or `button:has-text("Show all")`).
- **FR-AB-04 (Historical Backfill Scrolling & Dual-Condition Bounding)**: When `--backfill` is enabled, scroll the review modal container (`div[data-testid="pdp-reviews-modal-scrollable-panel"]` or equivalent) in intervals to trigger paginated GraphQL batches. Pagination terminates when captured reviews reach the platform's announced aggregate review count (e.g. 77 reviews), OR after 3 consecutive scrolls with no new reviews, capped at a maximum safety threshold of 30 scroll cycles (60s max per platform).
- **FR-AB-05 (DOM & JSON-LD Fallback)**: If GraphQL interception is delayed, extract ratings and review snippets from `script[type="application/ld+json"]` and `div#data-deferred-state-0`.

#### 3.2.2 VRBO Scraper
- **FR-VR-01 (Native 10.0 Scale Extraction)**: Accurately capture VRBO's native 10.0 scale rating (e.g. `9.6 / 10.0`) without normalization or downscaling.
- **FR-VR-02 (Badge & Sub-Score Capture)**: Extract the `"Loved by Guests"` badge and subtitle (*"Top 10% of guest reviews in this area"*), alongside category sub-scores (`cleanliness`, `accuracy`, `communication`, `location`, `check_in`).
- **FR-VR-03 (Apollo GraphQL Interception)**: Intercept responses containing `propertyReviewSummary` and `reviewList`.
- **FR-VR-04 (Pagination & DOM Extraction)**: Parse review items including author name, submission date, review headline/title, body text, and host/management response. Support clicking "More reviews" or scrolling to paginate.

#### 3.2.3 Booking.com Scraper
- **FR-BK-01 (Native 10.0 Scale Extraction)**: Capture Booking.com's native 10.0 rating (e.g. `9.4 / 10.0`) and sub-scores (`staff`, `facilities`, `cleanliness`, `comfort`, `value_for_money`, `location`, `free_wifi`).
- **FR-BK-02 (Review Block Pairing)**: Parse reviewer name, date, country flag/location, title, and intelligently concatenate positive feedback (`pros`) and negative feedback (`cons`) into a coherent review body.
- **FR-BK-03 (Modal / Review Tab Traversal)**: Navigate to `#reviews-tab` or trigger the review modal to inspect all review pages during backfill.

#### 3.2.4 Kivoya Direct Scraper
- **FR-KV-01 (SSL Verification Resilience)**: Construct an SSL context utilizing `certifi.where()` CA bundles, falling back to `ssl._create_unverified_context()` to guarantee zero certificate failures on macOS Python environments.
- **FR-KV-02 (Direct REST Streamline Query)**: Issue an AJAX query directly to Kivoya's Streamline endpoint:
  ```http
  POST /wp-admin/admin-ajax.php?action=streamlinecore-api-request
  Payload: {"methodName": "GetAllFeedback", "params": {"unit_id": "503802", "show_booking_dates": 1, "madetype_id": 2}}
  ```
- **FR-KV-03 (Playwright Fallback)**: If the REST endpoint is unavailable or returns 0 reviews, launch Playwright to navigate to `https://www.kivoya.com/503802/` and evaluate AngularJS scope data (`angular.element('#property-reviews').scope().reviews`).

### 3.3 Deduplication, Merge & Persistence
- **FR-DAT-01 (Deterministic Review ID)**: Compute a 32-character hexadecimal MD5 hash for every review record:
  $$\text{ReviewID} = \text{MD5}(\text{platform} + \text{reviewer\_name} + \text{date} + \text{text}[:60])$$
- **FR-DAT-02 (In-Place Host Response Update)**: When a scraped review matches an existing `ReviewID` in `data/ratings_reviews.json`:
  - If the existing record has `host_response: null` and the new record contains a team response, update the `host_response` in-place.
  - Preserve the record without duplicating it.
- **FR-DAT-03 (Incremental Early Stopping)**: During non-backfill runs, if 3 consecutive scraped reviews already exist in the database with identical content and host replies, terminate channel pagination early.
- **FR-DAT-04 (Chronological Ordering & Recency)**: Sort all reviews in `data/ratings_reviews.json` descending by date (`newest first`). Recalculate `is_recent = True` for reviews $\le 30$ calendar days old.
- **FR-DAT-05 (Atomic Persistence)**: Persist updates using an atomic write pattern (`tempfile` + `os.replace`) to prevent database corruption during sudden process termination.
- **FR-DAT-06 (Harvest Completeness Ratio & Granular Status)**:
  Compute $\text{Harvest Ratio} = \text{len}(\text{stored\_reviews}) / \max(\text{announced\_count}, 1)$.
  Assign granular channel status: `ok` ($\ge 90\%$), `partial_harvest` ($< 90\%$), `metadata_only` ($0\%$ texts with announced $> 0$), or `stale_error`.

### 3.4 Operational Modes & CLI Controls
- **FR-CLI-01 (Command Syntax & Full Preview Dry-Run)**: Support comprehensive execution flags under `.venv/bin/python -m src.cli sync-ratings`:
  - `--platform [all|airbnb|vrbo|booking|kivoya]`: Selectively crawl one channel or all channels.
  - `--backfill`: Enable full historical pagination across all review pages with dual-condition bounding.
  - `--force`: Ignore local recency caches and re-evaluate all channels.
  - `--headless [true|false]`: Run browser visibly for interactive debugging or headless for automation.
  - `--dry-run`: Full preview mode — executes live crawling with leased proxies and displays a rich terminal preview table of parsed metrics and new review previews, but strictly skips writing to `data/ratings_reviews.json` and skips dashboard regeneration.
  - `--strict`: Enforce strict exit codes (return code 1 if any platform enters `stale_error` or has missing reviews).
  - `--no-dashboard`: Suppress automatic HTML dashboard regeneration.
- **FR-CLI-02 (Truthful Headline & Volume Delta Assertion)**: The CLI summary must not output `✅ Sync complete` if errors or partial harvests exist. Output `⚠️ Partial Sync` or `❌ Sync Failed`, and print the net review delta (`+X total reviews persisted to database`).
- **FR-ORCH-01 (Concurrent Multi-Channel Execution)**: When multiple platforms are targeted (e.g. `--platform all`), the engine leases all 3 corridor proxies simultaneously and executes crawling for Airbnb, VRBO, Booking.com, and Kivoya Direct concurrently via `asyncio.gather` to satisfy NFR-PERF-01 ($< 60\text{s}$ incremental sync).

### 3.5 Fault Tolerance & Error Isolation
- **FR-ERR-01 (Channel Isolation)**: A network error, CAPTCHA challenge, or timeout on one platform MUST NOT abort scraping for remaining platforms.
- **FR-ERR-02 (State Preservation)**: If a channel fails to scrape, its previous ratings, review counts, and reviews in `data/ratings_reviews.json` MUST be preserved intact. The channel status is marked `stale_error`.
- **FR-ERR-03 (Graceful Timeout Management)**: Each navigation step must enforce an explicit timeout ($15\text{s} – 30\text{s}$).

---

## 4. Non-Functional Requirements

- **NFR-PERF-01 (Incremental Crawl Speed)**: Standard daily incremental sync across all 4 channels must complete in $< 60$ seconds total.
- **NFR-PERF-02 (Backfill Crawl Speed)**: Full historical backfill across 141+ reviews must complete in $< 180$ seconds.
- **NFR-SEC-01 (Credential Hygiene)**: NordVPN service credentials must be read strictly from `.env` and never logged or serialized to disk.
- **NFR-TEST-01 (Zero-Sleep Invariant)**: Unit tests must mock network responses and Playwright contexts, completing the entire collector test suite in $< 2.0$ seconds without wall-clock sleeps.

---

## 5. Implementation Phasing & Verification Protocol

- **SEC-01 (Verification First Invariant)**: All production scraping logic, parsers, and proxy orchestration must be implemented and validated against the $< 2.0\text{s}$ zero-sleep test suite and verified through a strict minimum of 2 clean-context subagent code review rounds prior to initiating live network operations.
- **SEC-02 (On-Demand Live Backfill Trigger)**: Full live backfill across all 141+ historical reviews will be offered as an optional interactive command prompt once offline test verification passes.

