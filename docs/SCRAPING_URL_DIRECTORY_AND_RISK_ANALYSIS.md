# STR Price Advisor — Web Scraping URL Directory & Risk Analysis

This document provides a comprehensive, production-grade reference of all URL types generated, requested, and scraped by the **STR Price Advisor** system. It details how each URL is constructed, its exact operational purpose in the pricing pipeline, request frequencies during Daily and Weekly scans, bot-detection risk profiles, and the multi-layered mitigation strategies employed to ensure reliable, unblockable intelligence gathering.

---

## 1. High-Level URL Taxonomy & Master Reference Matrix

| # | URL Category | Target Domain / Endpoint | Daily Scan Requests | Weekly Scan Requests | Risk Level | Primary Mitigations |
|:---|:---|:---|:---:|:---:|:---:|:---|
| **1** | **Airbnb Corridor Search (Page 1)** | `airbnb.com/s/{corridor}--AZ/homes` | 32–64 | 140–280 | **Medium** | Feeder Proxy Pool, Strict US/Non-PHX, Jitter, 24h Cache |
| **2** | **Airbnb Paginated Corridor (Pages 2–3)** | `airbnb.com/s/...&cursor={token}` | 32–96 | 140–420 | **Medium-High** | Randomized 2–3 Page Depth, Human Delay, No Cursor Spikes |
| **3** | **Airbnb Targeted Direct Listing Fallback** | `airbnb.com/rooms/{comp_id}?check_in=...` | 0–450 *(0 if cached)* | 0–2,800 *(0 if cached)* | **Medium** | Multi-IP Context Leasing, Bot Challenge Abort, 3-Way State |
| **4** | **Airbnb Our Property Live Quote** | `airbnb.com/rooms/573857947793833342?...` | 1–8 | 30–45 | **Low-Medium** | Feeder Proxy, Cache Pre-Check, GraphQL Sniffing |
| **5** | **Airbnb Deep Listing Metadata & Rules** | `airbnb.com/rooms/{listing_id}` | **0** *(On-demand only)* | **0** *(On-demand only)* | **Low** | Permanent Local Profile Cache, Generous Settling Cushion |
| **6** | **VRBO Our Property Live Quote** | `vrbo.com/2685684?chkin=...` | 1–8 | 30–45 | **High** | Multi-City IP Rotation, Navigation Retries, Settling Wait |
| **7** | **Booking.com Our Property Live Quote** | `booking.com/hotel/us/villa-del-sol...` | 1–8 | 30–45 | **Medium-High** | Strict US Proxy, Table Dom Wait, Currency Locking |
| **8** | **Kivoya / Streamline VRS Core API** | `kivoya.com/wp-admin/admin-ajax.php` | 2–3 | 2–3 | **Very Low** | In-Memory Class Cache, Standard JSON API |
| **9** | **Streamline OwnerX JSON API** | `ownerx.streamlinevrs.com/api/...` | 3 | 3 | **Low** | Authenticated Handshake, Cookie Jar, Low Frequency |
| **10** | **Stealth Pre-Flight Health Probes** | `google.com` (via proxy forwarders) | 10 | 10 | **Negligible** | Fast Loopback Sockets, Dynamic Candidate Hot-Swapping |

---

## 2. Deep-Dive Specification for Each URL Type

---

### Category 1: Airbnb Location Corridor Searches (Base & Page 1)

#### URL Structure
```http
https://www.airbnb.com/s/{corridor}--AZ/homes?adults=16&min_bedrooms=6&checkin={check_in}&checkout={check_out}&locale=en&currency=USD
```
*(Tier B comp sweep modifies to `adults=12&min_bedrooms=5`)*

* **Where in Code**: `src/airbnb_collector.py:AirbnbCollector._scrape_corridor()`
* **Corridors Monitored**: `Tempe--AZ`, `Scottsdale--AZ`, `Chandler--AZ`, `Mesa--AZ`
* **Purpose**: Discovers all active, unbooked luxury competitor homes (5–6+ bedrooms, 12–16+ guests) across the East Valley for a specific guest stay window. Matches returned listing IDs against `config/comps_registry.json`.

#### Usage Frequencies
* **Daily Quick Scan (`--quick`, 8 intervals)**:
  - 8 intervals × 4 corridors × 1–2 tiers = **32 to 64 page loads**.
  - With local disk caching (`data/cache/search_{c_in}_{c_out}_{loc}_{tier}_{hash}.json`), if run multiple times within 24 hours, network calls drop to **0**.
* **Weekly Full Scan (`--weekly`, 35–45 intervals)**:
  - 35 intervals × 4 corridors = **140 to 280 page loads** (executed concurrently across 4 out-of-state feeder proxies).

#### Detection Risk Analysis
* **Risk Level: Medium**
* **Trigger Factors**:
  - Scraping broad search queries across multiple future dates in rapid succession triggers Akamai / DataDome rate limits.
  - If queried from a Phoenix IP, Airbnb's anti-fraud system identifies it as local host reconnaissance (local residents do not search for 16-guest mansions across 10 upcoming weekends).
  - Without currency/locale parameters, international exit nodes return Euro (€) pricing and European date formats, corrupting data extraction.

#### Mitigation Architecture
1. **Feeder Market Routing**: Strictly routed through out-of-state tourist feeder hubs (Los Angeles, San Francisco, Dallas, Chicago). Zero Phoenix proxy traffic.
2. **Localization Lockdown**: Explicit `&locale=en&currency=USD` forces clean USD pricing regardless of proxy exit hub.
3. **Anti-Fingerprinting Chromium**: Headless flag obscured via `--disable-blink-features=AutomationControlled` (`navigator.webdriver = false`), desktop viewport `1366x850`, and macOS Safari/Chrome User-Agent.
4. **DOM Settling & Politeness**: Waits for `domcontentloaded`, followed by a 1.0s buffer to allow React search cards to mount cleanly.

---

### Category 2: Airbnb Paginated Corridor Searches (Pages 2–3)

#### URL Structure
```http
https://www.airbnb.com/s/{corridor}--AZ/homes?adults=16&min_bedrooms=6&checkin={check_in}&checkout={check_out}&locale=en&currency=USD&cursor={base64_cursor_token}
```
Where `cursor` is a URL-encoded Base64 JSON payload:
- **Page 2**: `eyz...` → `{"section_offset":0,"items_offset":18,"version":1}`
- **Page 3**: `eyz...` → `{"section_offset":0,"items_offset":36,"version":1}`

* **Where in Code**: `src/airbnb_collector.py:AirbnbCollector.get_search_cursor()` and `_scrape_corridor()`
* **Purpose**: Deepens search discovery beyond the initial 18 cards to capture competitors that rank on Page 2 or Page 3 during high-inventory periods.

#### Usage Frequencies
* **Daily Quick Scan**:
  - Automatically randomizes depth per corridor: `random.choice([2, 3])` pages.
  - 8 intervals × 4 corridors × 1.5 paginated pages = **~48 requests**.
* **Weekly Full Scan**:
  - 35 intervals × 4 corridors × 1.5 paginated pages = **~210 requests**.

#### Detection Risk Analysis
* **Risk Level: Medium-High**
* **Trigger Factors**:
  - Rigid, automated deep pagination (e.g. always requesting exactly 5 pages with millisecond intervals) is a primary bot signature.
  - Airbnb tracks cursor progression speed. Rapid cursor advancement without intermediate DOM interaction triggers Cloudflare and PerimeterX captcha challenges.

#### Mitigation Architecture
1. **Randomized 2–3 Page Depth**: Non-deterministic page count mimics human users who browse 2 or 3 pages before refining filters or clicking a property.
2. **Inter-Page Randomized Jitter**: Polite sleep of `random.uniform(0.8, 1.8)` seconds between page transitions.
3. **Early Termination**: If Page 2 returns fewer than 18 cards, the scraper detects that the corridor has run out of inventory and immediately halts further pagination requests.
4. **Intra-Corridor Deduplication**: Uses `seen_in_loc` set to ignore identical listing cards rendered across search page boundaries.

---

### Category 3: Airbnb Targeted Direct Listing Fallback (Single-Comp Check)

#### URL Structure
```http
https://www.airbnb.com/rooms/{listing_id}?check_in={check_in}&check_out={check_out}&adults={accommodates}&locale=en&currency=USD
```

* **Where in Code**: `src/airbnb_collector.py:AirbnbCollector.fetch_direct_comp_price()` & `fetch_missing_comps_fallback()`
* **Purpose**: Guarantees **100% comp market coverage**. For every registered comp in `config/comps_registry.json` that was not caught in the top 2–3 corridor search pages, this URL checks direct availability and extracts the exact live checkout price.

#### Usage Frequencies
* **Daily Quick Scan**:
  - In our 97-comp portfolio, ~35 appear on search pages; ~62 missing comps are checked.
  - **First Scan of Day**: Up to 62 checks × 8 intervals = **~496 PDP visits** (distributed across 10 rotating proxy workers).
  - **Subsequent Scans**: **0 requests**. Responses are cached to `data/cache/search_{c_in}_{c_out}_comp_{lid}.json` (available) or `unavailable_{c_in}_{c_out}_comp_{lid}.json` (booked) and return in <5ms.
* **Weekly Full Scan**:
  - Uncached intervals query direct checks across the 10-node feeder pool with concurrency bounded to worker count.

#### Detection Risk Analysis
* **Risk Level: Medium**
* **Trigger Factors**:
  - Opening dozens of PDP listing pages in rapid succession from one IP address triggers HTTP 429 (Too Many Requests) or bot verification modals (*"Please verify you are human"*, *"Press & Hold"*).
  - Risk of false booking classification: If a proxy block (HTTP 403) occurs and is misinterpreted as "listing is booked", the pricing database is corrupted.

#### Mitigation Architecture
1. **10-Worker Feeder Leasing**: Workload distributed across 10 distinct out-of-state proxy IPs via `async with self.lease_context() as context:`. Each IP receives only a small burst of requests.
2. **HTTP ≥ 400 Guard**: `resp_nav = await page.goto(...)`. If status is 403, 429, or 503, aborts immediately without touching cache.
3. **Bot Challenge Shielding**: Scans inner text for `"verify you are human"`, `"press and hold"`, `"access denied"`. If detected, aborts cleanly without recording the comp as booked.
4. **3-Way State Separation**:
   - *Confirmed Price (>0)*: Saves to `search_*.json` and unlinks any stale `unavailable_*.json`.
   - *Confirmed Unavailable*: Explicit GraphQL/DOM unavailability message saves to `unavailable_*.json` and unlinks stale `search_*.json`.
   - *Indeterminate*: Transient timeout returns `None` without destroying valid caches or poisoning the ledger.
5. **Event-Driven GraphQL Sniffing**: Attaches `page.on("response")` listening for `StaysPdpSections`. When pricing arrives, `done_event.set()` fires, allowing the scraper to exit in <1.5s without waiting for heavy media/images to load.
6. **Defensive Integer Parsing**: Accommodation parameters safely sanitize string/numeric inputs (`min(_parse_int(raw_acc), 16)`).

---

### Category 4: Airbnb Our Property Live Quote (Platform Parity Sweep)

#### URL Structure
```http
https://www.airbnb.com/rooms/573857947793833342?check_in={check_in}&check_out={check_out}&adults=16&locale=en&currency=USD
```

* **Where in Code**: `src/platform_comparator.py:PlatformComparator.scrape_airbnb_quote()`
* **Listing**: Villa del Sol (`573857947793833342`)
* **Purpose**: Audits guest-facing Airbnb pricing for Villa del Sol against Kivoya direct and VRBO to detect channel rate disparities, unauthorized host discounts, or OTA fee inflation.

#### Usage Frequencies
* **Daily Quick Scan**: 8 requests (1 per unbooked interval), or fewer if filtered by `--only-recent-changes`.
* **Weekly Full Scan**: 30–45 requests (1 per unbooked interval).
* **Caching**: Reuses `data/cache/our_property_{check_in}_{check_out}.json` if already scraped during comp sweeps.

#### Detection Risk Analysis
* **Risk Level: Low-Medium**
* **Trigger Factors**: Scraping the *exact same listing ID* 40 times in 2 minutes from a single IP looks like host rate monitoring.
* **Mitigation**:
  - Leases out-of-state proxy IPs.
  - Evaluates `our_property` cache prior to network navigation.
  - Intercepts internal GraphQL payload and exits page immediately upon pricing resolution.

---

### Category 5: Airbnb Deep Listing Metadata & House Rules (Comp Enrichment)

#### URL Structure
```http
https://www.airbnb.com/rooms/{listing_id}
```
*(No check-in / check-out date parameters)*

* **Where in Code**: `src/listing_enricher.py:ListingEnricher.extract_listing_data()` and `enrich_house_rules_only()`
* **Purpose**: Extracts static comp specifications: verified bedroom count, bed arrangements, bathroom ratios, private heated pool details, JSON-LD schema, Apollo/Niobe deferred state, and house rules (quiet hours, pet fees, event bans).

#### Usage Frequencies
* **Daily Quick Scan**: **0 requests**.
* **Weekly Full Scan**: **0 requests**.
* **On-Demand / Management Only**: Triggered only when a new comp is registered (`add-comp` or `enrich-comps`). Cached permanently in `data/enriched_comps/{listing_id}.json`.

#### Detection Risk Analysis
* **Risk Level: Low**
* **Trigger Factors**: General listing browsing without dates carries low risk.
* **Mitigation**:
  - Permanent disk storage ensures each listing is scraped only once in its lifecycle unless `--force` is specified.
  - Relaxed settling delays (2.5s – 3.0s) allow all deferred JavaScript bundles to unpack safely.

---

### Category 6: VRBO Our Property Live Quote (Platform Parity Sweep)

#### URL Structure
```http
https://www.vrbo.com/2685684?chkin={check_in}&chkout={check_out}&adults=16
```

* **Where in Code**: `src/platform_comparator.py:PlatformComparator.scrape_vrbo_quote()`
* **Listing**: Villa del Sol on VRBO (`2685684`)
* **Purpose**: Retrieves live traveler checkout rates on VRBO, verifying that Streamline PMS rate push matches Airbnb and direct pricing.

#### Usage Frequencies
* **Daily Quick Scan**: Up to 8 requests (1 per interval).
* **Weekly Full Scan**: 30–45 requests.

#### Detection Risk Analysis
* **Risk Level: High**
* **Trigger Factors**:
  - VRBO (Expedia Group) employs strict Akamai Bot Manager and Cloudflare WAF protection.
  - Highly prone to dropping TCP connections (`net::ERR_EMPTY_RESPONSE`) or issuing HTTP 403 when automated headless browsers or recognized VPN IP ranges connect.
  - Client-side React pricing widgets load asynchronously; premature DOM inspection results in missing pricing data.

#### Mitigation Architecture
1. **Multi-City Proxy Rotation**: Concurrently leases distinct feeder hubs across intervals. If Los Angeles encounters `ERR_EMPTY_RESPONSE`, the next interval runs through San Francisco, Dallas, or Chicago.
2. **`_navigate_with_retry`**: Automatically executes exponential backoff retries upon navigation failure.
3. **Polite DOM Settling Cushion**:
   - Waits for `domcontentloaded`.
   - Pauses 5.0s, scrolls to 800px, and pauses an additional 3.0s to ensure dynamic pricing tables hydrate.
4. **Fault Isolation**: If VRBO fails after retries, `PlatformComparator` records an isolated channel warning, falls back to Streamline channel projection formulas, and continues comparing Airbnb and Booking.com without halting the scan.

---

### Category 7: Booking.com Our Property Live Quote (Platform Parity Sweep)

#### URL Structure
```http
https://www.booking.com/hotel/us/villa-del-sol-amazing-house-by-kivoya.html?checkin={check_in}&checkout={check_out}&group_adults=16&no_rooms=1
```

* **Where in Code**: `src/platform_comparator.py:PlatformComparator.scrape_booking_quote()`
* **Listing**: Villa del Sol on Booking.com (`villa-del-sol-amazing-house-by-kivoya.html`)
* **Purpose**: Inspects live guest pricing and room table availability on Booking.com.

#### Usage Frequencies
* **Daily Quick Scan**: Up to 8 requests.
* **Weekly Full Scan**: 30–45 requests.

#### Detection Risk Analysis
* **Risk Level: Medium-High**
* **Trigger Factors**:
  - Booking.com uses PerimeterX / HUMAN Security anti-bot protection.
  - **Currency & Tax Traps**: If queried from non-US proxies, Booking.com flips currency to EUR/GBP and applies mandatory EU VAT display logic, causing regex price parsing to fail.
  - Displays interstitial date-picker modals if query parameters are missing.

#### Mitigation Architecture
1. **Strict US Feeder Enforcement**: All 10 proxy endpoints are strictly located within the United States, locking currency to USD and US lodging tax presentation.
2. **Deterministic Room Table Selectors**: Specifically awaits `#hprt-table` or `.hprt-table` and checks for sold-out notices (*"no availability for your dates"*, *"sold out on your dates"*).
3. **Isolated Exception Handling**: Network or captcha errors are captured cleanly without disrupting other channel comparisons.

---

### Category 8: Kivoya Direct Booking & Streamline VRS AJAX API

#### URL Structures
* **Direct Property Page**: `https://www.kivoya.com/503802/` (Informational / UI link)
* **Core AJAX API Endpoint**:
  ```http
  POST https://www.kivoya.com/wp-admin/admin-ajax.php?action=streamlinecore-api-request&params={"methodName":"GetBlockedPeriods","params":{"property_id":503802}}
  ```
  *(Other method names: `GetSeasonalRates`, `GetPropertyAvailability`, `GetQuote`)*

* **Where in Code**: `src/kivoya_client.py:KivoyaClient._call_api()`
* **Purpose**: Directly queries Kivoya's property management engine for real-time calendar reservations, unbooked segment boundaries, and published seasonal base rates.

#### Usage Frequencies
* **Daily Quick Scan**: Exactly **2 to 3 HTTP POST requests** total.
* **Weekly Full Scan**: Exactly **2 to 3 HTTP POST requests** total.
* **In-Memory Caching**: `KivoyaClient._cache_seasonal_rates` and `_cache_blocked_periods` store the responses in memory. All 8–45 intervals reuse this single payload with **zero additional network overhead**.

#### Detection Risk Analysis
* **Risk Level: Very Low**
* **Trigger Factors**: Standard WordPress AJAX API designed for public booking engines.
* **Mitigation**:
  - In-memory process caching prevents repeated hits.
  - Standard desktop User-Agent header.
  - Resilient SSL context (`SSL_CONTEXT = ssl._create_unverified_context()`) prevents handshake errors with legacy server certificates.

---

### Category 9: Streamline OwnerX JSON API (Owner Portal Sync)

#### URL Structures
* **CSRF Handshake**: `GET https://ownerx.streamlinevrs.com/csrf-token`
* **Authentication**: `POST https://ownerx.streamlinevrs.com/api/authenticateProcessor`
* **Reservation Contract Retrieval**: `POST https://ownerx.streamlinevrs.com/api/streamline`

* **Where in Code**: `src/ownerx_client.py:OwnerXClient`
* **Purpose**: Authenticates against Streamline PMS to ingest confirmed guest reservation details, gross rents, owner net payouts, and historical bookings into `data/reservations.db`.

#### Usage Frequencies
* **Daily Quick Scan**: **3 HTTP calls** executed during pre-scan PMS synchronization (`scripts/launchd/run_pms_sync.sh` or `cli.py sync-pms`).
* **Weekly Full Scan**: **3 HTTP calls** during PMS sync.

#### Detection Risk Analysis
* **Risk Level: Low**
* **Trigger Factors**: Authenticated owner API session. Calling it excessively (e.g. every second) could lock the owner account.
* **Mitigation**:
  - Scheduled run only once or twice per day.
  - Session cookie preservation via Python's `http.cookiejar.CookieJar`.
  - Credentials securely isolated in `.env` (`STREAMLINE_OWNER_USERNAME`, `STREAMLINE_OWNER_PASSWORD`).

---

### Category 10: Stealth Pre-Flight Health Probes

#### URL Structure
```http
GET https://www.google.com
```

* **Where in Code**: `src/stealth_connection.py:StealthConnectionManager.start_pool()` and `src/cli.py test-stealth`
* **Purpose**: Tests every freshly spawned `pproxy` local forwarder bridge with an end-to-end TCP/TLS handshake before any scraping traffic hits Airbnb, VRBO, or Booking.com.

#### Usage Frequencies
* **Daily Quick Scan**: **10 requests** (1 probe per proxy endpoint at pool startup).
* **Weekly Full Scan**: **10 requests**.

#### Detection Risk Analysis
* **Risk Level: Negligible (Zero)**
* **Trigger Factors**: Google handles billions of HTTP requests per minute.
* **Mitigation**:
  - Ultra-fast loopback socket check (~270ms latency).
  - **Dynamic Candidate Hot-Swapping**: If any NordVPN endpoint fails or drops packets, the manager automatically kills that bridge, selects a healthy candidate node from a 40+ candidate pool, and re-verifies.

---

## 3. Daily Scan vs. Weekly Scan Request Volume Audit

The following table contrasts the actual network footprint between a standard **Daily Quick Scan** (8 intervals, lead time ≤ 90 days) and a **Weekly Full Scan** (35–45 intervals across the entire 12-month calendar):

```
+---------------------------------------------------+--------------------+--------------------+
| Operation / URL Type                              | Daily Quick Scan   | Weekly Full Scan   |
+---------------------------------------------------+--------------------+--------------------+
| 1. Stealth Pre-Flight Google Probes               | 10 requests        | 10 requests        |
| 2. Kivoya PMS Calendar & Seasonal Rates           | 2–3 requests       | 2–3 requests       |
| 3. Streamline OwnerX Reservation Sync             | 3 requests         | 3 requests         |
| 4. Corridor Searches Page 1 (Tempe/Scotts/etc.)   | 32–64 requests     | 140–280 requests   |
| 5. Paginated Corridor Searches (Pages 2–3)        | 32–96 requests     | 140–420 requests   |
| 6. Targeted Direct Comp Fallback (Uncached)       | ~400 requests*     | ~2,200 requests*   |
|    Targeted Direct Comp Fallback (24h Cached)     | 0 requests         | 0 requests         |
| 7. Airbnb Our Property Live Quote                 | 1–8 requests       | 30–45 requests     |
| 8. VRBO Our Property Live Quote                   | 1–8 requests       | 30–45 requests     |
| 9. Booking.com Our Property Live Quote            | 1–8 requests       | 30–45 requests     |
+---------------------------------------------------+--------------------+--------------------+
| TOTAL HTTP REQUESTS (Cold Cache / First Run)      | ~480 – 590         | ~2,700 – 3,400     |
| TOTAL HTTP REQUESTS (Warm Cache / 2nd Run)        | ~80 – 190          | ~500 – 850         |
| NETWORK DATA TRANSFER                             | ~12 – 25 MB        | ~60 – 120 MB       |
| TOTAL WALL-CLOCK DURATION                         | ~25 – 45 seconds   | ~3.5 – 6 minutes   |
+---------------------------------------------------+--------------------+--------------------+
```
*\* Note: Direct comp fallback requests occur only for missing comps not found in corridor sweeps on cold cache. Subsequent runs read directly from `data/cache/search_*.json` or `data/cache/unavailable_*.json` in <5ms.*

---

## 4. The 7 Layers of Anti-Detection Defense

```
Layer 1: Geolocation Defense (Zero Phoenix Policy)
         - Strict exclusion of local Phoenix/Tempe residential IPs
         - All traffic routes through 10 out-of-state tourist feeder cities (LA, SF, Dallas, Chicago, Denver, Seattle, Atlanta, Miami, Austin, NYC)

Layer 2: Pre-Flight Health Probing & Dynamic Hot-Swapping
         - Loopback socket probes to Google test forwarders before touching Airbnb/VRBO
         - Defective nodes automatically hot-swapped from 40+ candidate node pool in <1s

Layer 3: Browser Fingerprint Neutralization
         - Chromium flag: --disable-blink-features=AutomationControlled (navigator.webdriver = false)
         - Standard macOS Desktop Safari/Chrome User-Agent and 1366x850 viewport

Layer 4: Non-Deterministic Timing & Randomized Pagination
         - Corridor search depth randomized per run (random.choice([2, 3]))
         - Inter-page sleep jitter (0.8s – 1.8s) and natural scrolling (window.scrollTo(0, 1500))

Layer 5: Multi-Context Worker Leasing
         - 1 Playwright browser instance with up to 10 isolated BrowserContext instances
         - Managed via asynchronous FIFO lease queue (lease_context()) with zero cross-talk

Layer 6: Response Validation & Bot Challenge Auto-Abort
         - HTTP status >= 400 immediately aborts without touching cache
         - Bot challenge detection ("verify you are human", "press and hold") aborts cleanly
         - 3-way state separation prevents false booking classifications

Layer 7: Intelligent Multi-Tier Caching
         - In-memory process cache for Kivoya PMS rates
         - Fast JSON disk cache: search_*.json (available) vs. unavailable_*.json (booked)
         - Permanent JSON profiles for static comps
         - SQLite database for historical sales and rate snapshots
```

---

## 5. Operational Verification Commands

```bash
# 1. Audit health and latency across all 10 stealth proxy workers
python -m src.cli test-stealth --count 10

# 2. Test stealth connection against a specific target (e.g. Airbnb)
python -m src.cli test-stealth --count 4 --target https://www.airbnb.com

# 3. Run a lightweight 1-interval test scan with live technical telemetry
python -m src.cli run --quick --limit 1

# 4. Inspect current cached competitor availability for a specific interval
ls -la data/cache/*2026-09-13_2026-09-17*
```
