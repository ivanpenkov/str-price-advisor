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
- External scraping traffic routes through authenticated NordVPN SOCKS5 proxy endpoints. Credentials are isolated inside `.env` (`NORDVPN_USER`, `NORDVPN_PASS`, `NORDVPN_SERVER`).
- Handled by [`src/proxy_manager.py`](file:///Users/ivanpe/str-price-advisor/src/proxy_manager.py).
- **Hard Guard**: If credentials are missing or the forwarder fails to bind, the system aborts immediately with a `RuntimeError` rather than ever falling back to an unproxied connection. Your residential and server IP addresses are never exposed to Airbnb, VRBO, or Booking.com.

### B. Why `pproxy` Is Required
- Chromium and Playwright natively support HTTP proxies with basic auth, but **do not support authenticated SOCKS5 proxies** (`socks5://user:pass@host:port`).
- To bridge this gap, `ProxyManager` starts an ephemeral local forwarder bridge using Python's `pproxy`:
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
In [`src/proxy_manager.py`](file:///Users/ivanpe/str-price-advisor/src/proxy_manager.py), Phoenix proxy servers are strictly excluded. If an environment variable or configuration accidentally specifies `phoenix.us.socks.nordhold.net`, it is automatically intercepted and redirected to Los Angeles:
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

## 6. Multi-IP Parallelization Architecture

### A. NordVPN SOCKS5 Infrastructure
NordVPN operates **68 dedicated SOCKS5 proxy servers** with independent public exit IP addresses:
- **45 US SOCKS5 Servers** across 7 major metro hubs (Los Angeles, San Francisco, Dallas, Chicago, Atlanta, New York).
- Subscriptions support up to **10 simultaneous connections**.

### B. Playwright Multi-Context Concurrency
Launching multiple separate browser instances consumes substantial CPU and memory. Instead, we launch **1 persistent Chromium browser** and instantiate **isolated `BrowserContext` instances**, each bound to a distinct feeder proxy:

```python
# Launch 1 browser instance
self._browser = await p.chromium.launch(
    headless=True,
    args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
)

# Create isolated contexts for each feeder market
for idx, (chan, _, _) in enumerate(self.FEEDER_CHANNELS):
    cfg = proxy_configs[idx % len(proxy_configs)]
    ctx = await self._browser.new_context(
        viewport={"width": 1366, "height": 850},
        user_agent="Mozilla/5.0 ...",
        proxy={"server": cfg["server"]},
    )
    self.contexts[chan] = ctx
```

### C. Step 3: Luxury Comp Scraping Parallelization
In [`src/airbnb_collector.py`](file:///Users/ivanpe/str-price-advisor/src/airbnb_collector.py), all 4 location corridors are queried simultaneously:
- **Context 1 (LA)** $\rightarrow$ Scrapes `Tempe--AZ`
- **Context 2 (SF)** $\rightarrow$ Scrapes `Scottsdale--AZ`
- **Context 3 (Dallas)** $\rightarrow$ Scrapes `Chandler--AZ`
- **Context 4 (Chicago)** $\rightarrow$ Scrapes `Mesa--AZ`
- Executed via `asyncio.gather(*corridor_tasks, return_exceptions=True)`.
- **Result**: Step 3 runtime dropped from **~19m 40s** down to **2m 58s** (~6.6x faster).

### D. Step 4b: Cross-Platform Price Comparison Parallelization
In [`src/platform_comparator.py`](file:///Users/ivanpe/str-price-advisor/src/platform_comparator.py), each external channel is assigned a dedicated feeder market:
- **Airbnb**: Los Angeles (`feeder-la`)
- **Booking.com**: San Francisco (`feeder-sf`)
- **VRBO**: Dallas (`feeder-dal`)
- In `compare_interval()`, all 3 platforms are scraped in parallel via `asyncio.gather(_scrape_a(), _scrape_b(), _scrape_v(), return_exceptions=True)`.
- **Zero Cross-Talk**: No two platforms ever see requests from the same IP address at the same time.
- **Result**: Step 4b per-interval latency dropped from **~25s** down to **~5–7s**, reducing total batch comparison time from **5m 02s** down to **~1m 15s**.

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
