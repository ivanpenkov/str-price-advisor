# Web Scraping Architecture, Anti-Detection & Feeder Concurrency Guide

This document details the scraping infrastructure, anti-bot evasion algorithms, proxy topology, and multi-IP concurrency used by the STR Price Advisor to monitor competitor pricing and multi-channel parity across Airbnb, Booking.com, VRBO, and Kivoya.

---

## 1. High-Level Architecture Overview

The STR Price Advisor gathers competitive intelligence through a multi-tier proxy forwarder bridge and headless Playwright Chromium automation:

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                       Local Scraper (Playwright Chromium)                   │
└──────┬──────────────────────┬──────────────────────┬────────────────────────┘
       │ Context 1 (LA)       │ Context 2 (SF)       │ Context 3 (Dallas)     │ Context 4 (Chicago)
       ▼                      ▼                      ▼                        ▼
┌──────────────┐       ┌──────────────┐       ┌──────────────┐         ┌──────────────┐
│ pproxy Bridge│       │ pproxy Bridge│       │ pproxy Bridge│         │ pproxy Bridge│
│ 127.0.0.1:   │       │ 127.0.0.1:   │       │ 127.0.0.1:   │         │ 127.0.0.1:   │
│ 56001 (HTTP) │       │ 56002 (HTTP) │       │ 56003 (HTTP) │         │ 56004 (HTTP) │
└──────┬───────┘       └──────┬───────┘       └──────┬───────┘         └──────┬───────┘
       │                      │                      │                        │
       ▼                      ▼                      ▼                        ▼
┌──────────────┐       ┌──────────────┐       ┌──────────────┐         ┌──────────────┐
│ NordVPN SOCKS│       │ NordVPN SOCKS│       │ NordVPN SOCKS│         │ NordVPN SOCKS│
│ Los Angeles  │       │San Francisco │       │    Dallas    │         │   Chicago    │
│  (Exit IP 1) │       │  (Exit IP 2) │       │ (Exit IP 3)  │         │ (Exit IP 4)  │
└──────┬───────┘       └──────┬───────┘       └──────┬───────┘         └──────┬───────┘
       │                      │                      │                        │
       └──────────────────────┼──────────────────────┴────────────────────────┘
                              │ Encrypted HTTPS Traffic
                              ▼
        ┌──────────────────────────────────────────────┐
        │  Target STR Platforms (Airbnb, VRBO, etc.)   │
        └──────────────────────────────────────────────┘
```

---

## 2. Zero-Unproxied Mandate & `pproxy` Forwarder Bridge

### A. Credential Isolation & Strict Guard
- External scraping traffic routes through authenticated NordVPN SOCKS5 proxy endpoints. Credentials are isolated inside `.env` (`NORDVPN_USER`, `NORDVPN_PASS`, `NORDVPN_SERVER`, `STEALTH_MAX_CONNECTIONS`).
- Handled by [`src/stealth_connection.py`](file:///Users/ivanpe/str-price-advisor/src/stealth_connection.py).
- **Hard Guard**: If credentials are missing or the forwarder fails to bind, the system aborts immediately with a `RuntimeError` rather than ever falling back to an unproxied connection. Your residential and server IP addresses are never exposed to Airbnb, VRBO, or Booking.com.

### B. Why `pproxy` Is Required
- Chromium and Playwright natively support HTTP proxies with basic auth, but **do not support authenticated SOCKS5 proxies** (`socks5://user:pass@host:port`).
- To bridge this gap, `StealthConnectionManager` starts an ephemeral local forwarder bridge using Python's `pproxy`:
  $$\text{Playwright (Chromium)} \xrightarrow{\text{HTTP Proxy}} \text{127.0.0.1:port} \xrightarrow{\text{Auth SOCKS5}} \text{NordVPN} \xrightarrow{\text{HTTPS}} \text{Target Platform}$$
- Each bridge listens on a dynamically allocated, conflict-free localhost port and handles authentication with NordVPN's upstream infrastructure.

---

## 3. Out-of-State Feeder Market IP Strategy

### A. The Phoenix Metro Surveillance Dilemma
Villa del Sol is located in Tempe, Arizona (Phoenix metropolitan area). In the short-term rental and hospitality industry, bot protection networks (Akamai, DataDome, Cloudflare) monitor IP behavioral signatures:
- **Host Surveillance Signature**: A residential or local Phoenix IP querying dozens of 5–8 bedroom luxury listings across Scottsdale, Tempe, and Mesa multiple times a day triggers host competitor surveillance tripwires.
- **Local Resident Anomaly**: True local residents rarely search for large 16-guest luxury homes in their own metro area across 12 distinct future dates.

### B. Why Feeder Markets Are Optimal
The top tourist feeder markets for Phoenix/Scottsdale luxury vacation rentals are:
1. **Los Angeles / Southern California**
2. **San Francisco / Bay Area**
3. **Dallas / North Texas**
4. **Chicago / Midwest**

By routing web traffic through exit nodes in these feeder cities:
- To Airbnb's anti-fraud systems, requests appear as **authentic out-of-state travelers** browsing Phoenix vacation homes for upcoming family trips or corporate retreats.
- Local host surveillance heuristic tripwires are completely bypassed.

### C. Strict Phoenix Exclusion Logic
In [`src/stealth_connection.py`](file:///Users/ivanpe/str-price-advisor/src/stealth_connection.py), Phoenix proxy servers are strictly excluded. If an environment variable or configuration accidentally specifies `phoenix.us.socks.nordhold.net`, it is automatically intercepted and redirected to Los Angeles:
```python
if "phoenix" in host.lower():
    logger.warning(
        f"Phoenix proxy detected for {name} ({host}). "
        f"Redirecting to Los Angeles feeder server to avoid local detection."
    )
    host = "los-angeles.us.socks.nordhold.net:1080"
```

---

## 4. Browser Stealth & Anti-Fingerprinting

Target platforms inspect client browser environments using deep JavaScript fingerprinting. We neutralize these signals in [`src/airbnb_collector.py`](file:///Users/ivanpe/str-price-advisor/src/airbnb_collector.py) and [`src/platform_comparator.py`](file:///Users/ivanpe/str-price-advisor/src/platform_comparator.py):

1. **`--disable-blink-features=AutomationControlled`**:
   - Chromium's default automated mode exposes `navigator.webdriver = true` to page scripts.
   - Disabling this Blink feature removes the flag, making headless Chromium indistinguishable from a standard user-driven browser.
2. **Realistic Desktop User-Agent**:
   - Prevents headless user-agent indicators (`HeadlessChrome`) by presenting a standard macOS desktop signature:
     ```text
     Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36
     ```
3. **Authentic Desktop Viewport**:
   - Headless browsers default to `800x600`. We enforce standard laptop dimensions (`1366x850`).
4. **Humanized Viewport Scrolling**:
   - On listing detail and room checkout pages, the scraper executes `window.scrollTo(0, 1500)`. This mimics human scrolling and triggers lazy-loaded GraphQL payloads (e.g. `StaysPdpSections`) without issuing raw synthetic API requests.

---

## 5. Timing, Delays & Humanized Politeness

Bot detection filters look for perfectly uniform intervals between HTTP requests. We counter this with non-deterministic timing algorithms:

* **Inter-Page Pagination Jitter (within a corridor)**:
  - When querying Pages 1, 2, and 3:
    ```python
    await asyncio.sleep(random.uniform(1.0, 2.5))
    ```
* **Inter-Corridor Delays**:
  - In sequential mode, pauses of `3.5s` to `6.5s` separate corridor sweeps.
* **DOM Settling Delays**:
  - After navigating to search pages, Playwright waits for `domcontentloaded`, followed by a selector wait for `[data-testid="card-container"]` and an extra `1.0s` settling cushion to allow asynchronous image cards and price tags to render.
* **Interval Spacing**:
  - A polite `1.0s` delay separates distinct check-in intervals during batch sweeps.

---

## 6. Multi-IP Parallelization Architecture & 10-Worker Stealth Fleet

### A. NordVPN SOCKS5 Infrastructure & Clean US Datacenter Fleet
NordVPN allows up to **10 simultaneous connections** per account. [`src/stealth_connection.py`](file:///Users/ivanpe/str-price-advisor/src/stealth_connection.py) leverages verified, active out-of-state US metropolitan feeder markets (strictly excluding Phoenix):
1. **San Francisco 1** (`feeder-sf-1`: `san-francisco.us.socks.nordhold.net:1080` -> HostRoyale, CA)
2. **Dallas 1** (`feeder-dal-1`: `dallas.us.socks.nordhold.net:1080` -> M247, TX)
3. **Chicago 1** (`feeder-chi-1`: `chicago.us.socks.nordhold.net:1080` -> HostRoyale / Datacamp, IL)
4. **New York 1** (`feeder-ny-1`: `new-york.us.socks.nordhold.net:1080` -> M247, NY)
5. **US Anycast 1** (`feeder-us-1`: `us.socks.nordhold.net:1080` -> M247, US)
6. **San Francisco 2** (`feeder-sf-2`: `socks-us46.nordvpn.com:1080`)
7. **San Francisco 3** (`feeder-sf-3`: `socks-us70.nordvpn.com:1080`)
8. **Dallas 2** (`feeder-dal-2`: `socks-us73.nordvpn.com:1080`)
9. **Standby Candidate 1** (`socks-us60.nordvpn.com:1080`)
10. **Standby Candidate 2** (`socks-us63.nordvpn.com:1080`)

#### Permanent Decommissioning of African Subnet Hostnames (`feeder-la-1`, `feeder-la-2`, `feeder-atl-1`)
- **Root Cause**: An authoritative WHOIS and DNS audit revealed that NordVPN hostnames `los-angeles.us.socks.nordhold.net` (`196.247.4.27`), `socks-us61.nordvpn.com` (`196.247.4.11`), and `socks-us68.nordvpn.com` (`196.196.27.147`) resolve to IP ranges registered to **AFRINIC (African Network Information Centre)** under netname `FIBERSA` (`country: ZA`, South Africa).
- **Impact**: Travel platforms (Airbnb, Vrbo, Booking.com) and edge CDNs (Akamai, Cloudflare) check the client IP's ASN/country registration. Accessing Airbnb from these IPs triggered automatic HTTP 302 redirects to `https://www.airbnb.co.za/` and immediate Cloudflare/Akamai bot challenges, causing page scraping to fail with navigation errors.
- **Remediation**: All African netblock hosts (`196.0.0.0/8`, `102.0.0.0/8`, `105.0.0.0/8`, `41.0.0.0/8`) are permanently blacklisted and purged from the active proxy pool.

### B. Pre-Flight GeoIP & E2E Google Probing
Rather than blindly starting scrapers with unverified forwarders:
- **Subnet CIDR Filter**: `StealthConnectionManager` resolves candidate hostnames prior to spawning and rejects any IP residing in blacklisted foreign subnets.
- **Automated Pre-Flight GeoIP Probe**: Probes `http://ip-api.com/json` through each local forwarder port. If `country != "United States"`, the node is flagged as compromised and discarded immediately.
- **Cross-Border TLD Navigation Guard**: In Playwright, navigation listeners monitor response URLs. If an OTA request for a US property redirects to a foreign ccTLD (`.co.za`, `.ru`, `.co.uk`), the session is immediately aborted and marked for proxy rotation.
- **Fast End-to-End Google Probing**: Before scraping begins, `StealthConnectionManager` issues asynchronous HTTP GET requests to `https://www.google.com` across all spawned forwarders via local loopback sockets (~270ms latency with true TLS handshake).
- **Dynamic Hot-Swapping**: If any forwarder fails RFC 1928 authentication or drops packets, the manager automatically selects a verified replacement node from `CANDIDATE_STEALTH_SERVERS`, kills the broken forwarder, spawns a new bridge, and verifies connectivity without disrupting the rest of the pool.
- **Pre-Flight Validation CLI**:
  ```bash
  python -m src.cli test-stealth --count 8
  ```
  Generates an instant latency audit table and confirms all endpoints are operational. This pre-flight check runs automatically before daily quick scans and weekly full scans.

### C. Playwright Multi-Context Worker Leasing
Launching multiple separate browser instances consumes excessive system resources. Instead, we launch **1 persistent Chromium browser** and instantiate **isolated `BrowserContext` instances** bound to distinct feeder forwarders:
- Contexts are managed via an asynchronous lease queue (`asyncio.Queue` / `@asynccontextmanager lease_worker()`).
- Workers lease a context, perform isolated navigation without cookie or session leakage, and return the context cleanly upon completion.

### D. Multi-Interval Batch Concurrency (`PlatformComparator`)
In [`src/platform_comparator.py`](file:///Users/ivanpe/str-price-advisor/src/platform_comparator.py), the 10-worker pool enables **multi-interval batch parallelism**:
- With 10 active contexts, the comparator processes up to **3 calendar intervals simultaneously**:
  $$\text{Batch Concurrency} = \lfloor 10 / 3 \rfloor = 3 \text{ intervals} \times 3 \text{ channels} = 9 \text{ concurrent page navigations}$$
- **Zero Cross-Talk**: Every platform query runs on an independent proxy IP.
- Total comparison time across 8 intervals drops from over **5 minutes** to under **25 seconds**.

### E. Two-Stage Luxury Comp Scraping & 3-Attempt Transient Retry (`AirbnbCollector`)
In [`src/airbnb_collector.py`](file:///Users/ivanpe/str-price-advisor/src/airbnb_collector.py), competitor data collection operates in two robust stages:
1. **Stage 1: Multi-Corridor Search Sweeps**:
   - All 4 regional location corridors (Tempe, Scottsdale, Chandler, Mesa) are queried concurrently across distinct feeder contexts.
   - Paginates across 2–3 pages per corridor using base64 cursor tokens (`get_search_cursor()`), extracting up to 72 cards per corridor without IP throttling.
2. **Stage 2: Targeted Multi-IP Direct Comp Fallback (100% Comp Accounting)**:
   - If any curated comp in `config/comps_registry.json` was not captured during Stage 1 search results, the collector triggers direct single-comp PDP checks (`fetch_single_comp_pricing`).
   - Direct PDP requests are distributed concurrently across the 10-node proxy pool via `@asynccontextmanager lease_context()`.
3. **3-Attempt Transient Navigation Retries**:
   - Remote SOCKS5 edge resets can occasionally cause transient `net::ERR_EMPTY_RESPONSE` or navigation timeouts.
   - The collector executes up to **3 attempts** (initial attempt + 2 retries) with polite `AIRBNB_RETRY_DELAY=1.0s` backoff.
   - Warning logs are suppressed until attempt 3 fails, ensuring console output remains clean during transient hiccups.
   - If all 3 attempts fail, cache file writing is strictly skipped to prevent **cache poisoning**, allowing subsequent runs to re-attempt the comp.

### F. Deep Listing Enrichment & Comp Sweeps (`CompManager` / `ListingEnricher`)
Batch enrichment of 30+ competitor listings and interval calendar verification run concurrently up to 10 workers wide, slashing multi-property refresh times by up to 10x.

---

## 7. Fault Isolation & Resource Safety

### A. Exception Isolation
In both parallel and sequential scraping modes, errors on one channel or corridor never abort healthy channels:
- If VRBO encounters a transient network timeout, Airbnb and Booking.com results are retained without disruption.
- Failed platforms log a diagnostic note (`PriceBreakdown(available=False, notes="Error: ...")`) and fall back to PMS channel parity projections.

### B. Process & Socket Hygiene
- **Dynamic Port Allocation**: `get_free_port(exclude=allocated_ports)` ensures that concurrently spawned forwarders never collide on local ports.
- **Process Liveness Verification**: `start_pool()` verifies that `ep.proc.returncode is None` after launch. If a forwarder crashes early, fallback servers are engaged.
- **Guaranteed Cleanup**:
  - Context setup is wrapped in `try ... except Exception: await self.close_browser(); raise`.
  - Batch loops are protected by `try ... finally: await collector.close_browser()`.
  - `close_browser()` deduplicates context references to prevent double-closure errors.
  - `stop_pool()` sends `SIGTERM` to all forwarders and awaits termination in parallel via `asyncio.gather()`, with escalation to `kill()` bounded to 2.0 seconds.
  - Synchronous fallback hooks are registered with `atexit` and `signal.signal(signal.SIGTERM, ...)`.

---

## 8. Currency & Localization Guard

> [!WARNING]
> **Strict US Server Requirement**:
> Never route short-term rental scraping through international (European or Asian) proxy servers (e.g. Amsterdam, Stockholm).
> 
> When platforms detect non-US client IP addresses:
> 1. Prices are converted to Euros (€) or Pounds (£) using dynamic bank exchange rates, breaking parsing regexes.
> 2. European VAT and tourist tax all-inclusive display laws alter nightly rate structures.
> 3. Check-in date formatting flips to `DD/MM/YYYY`.
> 
> To guarantee consistent pricing, all proxy endpoints are restricted to US servers, and all request URLs explicitly append:
> `&locale=en&currency=USD`

---

## 9. Request Minimization & Smart Caching

To avoid unnecessary network traffic and minimize our footprint:

1. **MD5-Hashed Local Disk Cache**:
   - Corridor searches are saved to `data/cache/search_{check_in}_{check_out}_{location}_{tier}_{hash}.json`.
   - Single-comp PDP sweeps are saved to `data/cache/comp_{id}_{check_in}_{check_out}.json`.
   - Within the configured TTL (e.g. 24h), repeated queries are read from disk in `<0.01s` without hitting the network.
2. **Smart Scrape Detection**:
   - In `compare_all_intervals()`, the comparator inspects cache files *before* launching Chromium or starting proxy forwarders. If all intervals in the requested date range are already cached, browser initialization is completely skipped.
3. **Lightweight Response Sniffing**:
   - Bandwidth telemetry inspects `content-length` response headers (`_track_response_bytes`) without buffering full media or video assets in memory.

---

## 10. WAF & Rate Limiting Protections (Booking.com & VRBO)

### A. Booking.com AWS WAF Challenge Auto-Resolution
- **WAF Response Signature**: Booking.com issues an HTTP `202 Accepted` status with an embedded AWS WAF JavaScript challenge (`token.awswaf.com/challenge.js` and `window.awsWafCookieDomainList = ['booking.com']`).
- **Auto-Token Acquisition**: Rather than failing immediately or scraping incomplete HTML, the scraper pauses to let the challenge script execute in the browser context. Once the `aws-waf-token` cookie is persisted, the page auto-reloads or navigates cleanly with HTTP `200 OK` and complete DOM hydration.

### B. VRBO OneTrust & Rate Limiting Protections
- **OneTrust Cookie Overlay Eviction**: Expedia/VRBO overlays full-screen consent modals (`#onetrust-banner-sdk`, `#onetrust-consent-sdk`, `.onetrust-pc-dark-filter`) that intercept DOM clicks. The scraper executes a DOM pre-cleaning script to remove these elements before clicking review buttons.
- **Apollo GraphQL Throttling Defense**: Direct calls to VRBO's `/graphql` endpoint may return HTTP `429 Too Many Requests` to automated browsers. The crawler implements dual-layer extraction: primary parsing against SSR JSON-LD / HTML state data (`window.__INITIAL_STATE__`), falling back to paginated DOM review card harvesting.

