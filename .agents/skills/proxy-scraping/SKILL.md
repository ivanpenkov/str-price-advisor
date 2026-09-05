---
name: proxy-scraping
description: >-
  Enforces mandatory proxy routing using credentials from .env whenever scraping
  or reading data from Airbnb, Kivoya, VRBO, or other external short-term rental platforms
  to prevent IP rate limiting, geo-blocking, and HTTP 503 Service Unavailable errors.
---

# Mandatory Proxy Configuration & Web Scraping Skill

This skill governs all external network requests, Playwright automation, API querying, and web scraping against **Airbnb**, **Kivoya**, **VRBO**, and any short-term rental platforms.

> [!IMPORTANT]
> **Zero Unproxied Scraping Mandate**
> You must NEVER make raw, unproxied web scraping or automated browser requests to Airbnb, Kivoya, VRBO, or other short-term rental platforms. Airbnb aggressively rate-limits residential and cloud IP addresses with HTTP 503 (`Stay tuned / Airbnb is temporarily unavailable`). All external scraping must route through the configured NordVPN SOCKS5 proxy pool.

---

## 1. Proxy Architecture & Environment Configuration

### A. Credentials in `.env`
Credentials must be stored securely in the root `.env` file (which is tracked in `.gitignore` and must never be committed to git):

```bash
NORDVPN_USER=your_nordvpn_service_username
NORDVPN_PASS=your_nordvpn_service_password
NORDVPN_SERVER=phoenix.us.socks.nordhold.net:1080
```

> [!NOTE]
> - `nordvpn.com` proxy hostnames are deprecated/dead in DNS. Always use `nordhold.net` (e.g. `phoenix.us.socks.nordhold.net:1080` or `us.socks.nordhold.net:1080`).
> - The credentials are your **NordVPN manual service credentials** (not your general email/password account login).

### B. Chromium & Playwright Local Forwarder (`pproxy`)
Chromium does not natively support authenticated SOCKS5 proxies (`socks5://user:pass@host:port`).
To overcome this limitation, [`src/proxy_manager.py`](file:///Users/ivanpe/str-price-advisor/src/proxy_manager.py) starts an ephemeral local forwarder bridge using Python's `pproxy` module on an available localhost port:

$$\text{Chromium / Scraper} \xrightarrow{\text{HTTP Proxy}} \text{localhost:port} \xrightarrow{\text{Auth SOCKS5}} \text{NordVPN (phoenix.us.socks.nordhold.net)} \xrightarrow{\text{HTTPS}} \text{Airbnb / VRBO / Target}$$

When scraping concludes, the `pproxy` background forwarder process is automatically terminated.

---

## 2. Standard Implementation Patterns

### Pattern 1: Playwright Async Scraping (Default)
Always use [`ProxyManager`](file:///Users/ivanpe/str-price-advisor/src/proxy_manager.py) to acquire the proxy dictionary:

```python
from playwright.async_api import async_playwright
from src.proxy_manager import ProxyManager

async def scrape_target():
    proxy_mgr = ProxyManager(required=True)
    proxy_cfg = await proxy_mgr.start()  # {"server": "http://127.0.0.1:<port>"}

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            proxy=proxy_cfg,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        context = await browser.new_context(
            viewport={"width": 1366, "height": 850},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        )
        page = await context.new_page()
        try:
            await page.goto("https://www.airbnb.com/rooms/16363441", wait_until="domcontentloaded")
            # ... process page content ...
        finally:
            await context.close()
            await browser.close()
            await proxy_mgr.stop()
```

### Pattern 2: Python `urllib` / `requests` / `httpx`
When making raw HTTP requests to Kivoya or OTA endpoints:
```python
import os
from src.proxy_manager import ProxyManager

# If pproxy is running on localhost:port:
proxies = {
    "http": f"http://127.0.0.1:{proxy_port}",
    "https": f"http://127.0.0.1:{proxy_port}",
}
```

### Pattern 3: CLI Commands
All built-in project CLI commands that hit external platforms are already wired to use `ProxyManager(required=True)`:
- Weekly price audit: `python -m src.cli run --weekly`
- Quick interval sweep: `python -m src.cli run --quick`
- Bootstrap registry: `python -m src.cli bootstrap-comps`
- Deep comp enrichment: `python -m src.cli enrich-comps`

---

## 3. Pre-Flight Verification & Health Check

Whenever troubleshooting connectivity or setting up a new agent session, verify proxy health:

```bash
# 1. Verify credentials exist in .env
test -f .env && grep -E 'NORDVPN_USER|NORDVPN_PASS' .env

# 2. Test proxy bridge via pproxy and curl
.venv/bin/python -c "
import asyncio, os
from src.proxy_manager import ProxyManager
async def check():
    mgr = ProxyManager(required=True)
    cfg = await mgr.start()
    print('Proxy server config:', cfg)
    await mgr.stop()
asyncio.run(check())
"
```

---

## 4. Agent Checklist Before Any Web Fetching
1. [ ] Check that `.env` contains `NORDVPN_USER` and `NORDVPN_PASS`.
2. [ ] Ensure `ProxyManager(required=True)` is instantiated and started.
3. [ ] Pass `proxy=proxy_cfg` into Playwright's `chromium.launch()`.
4. [ ] In `finally:` blocks or context exit, always stop the proxy manager to avoid lingering processes.
5. [ ] NEVER fall back to direct unproxied connections if the proxy fails; raise a clean error instructing the user to check their proxy credentials or connectivity.

