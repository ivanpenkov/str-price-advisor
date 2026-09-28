# Technical Design: Scraper Resilience, Exponential Backoff & Proxy Rotation for Airbnb Comp Scraping

## 1. Problem Statement

During automated market scans, network fluctuations and proxy connection drops (`net::ERR_EMPTY_RESPONSE`, `net::ERR_CONNECTION_RESET`, or Playwright navigation timeouts) can cause Airbnb comp search queries to fail.

Previously, all retry attempts for a search page reused the exact same browser context and proxy connection. If that specific proxy bridge stalled or dropped connection, all retries failed against the same dead endpoint. Furthermore, retries used fixed short delays without exponential backoff or jitter.

## 2. Solution Overview

Harden `AirbnbCollector` when scraping competitive listings by introducing two targeted improvements:
1. **Per-Attempt Proxy Rotation**: Acquire a fresh browser context from the worker pool on each retry attempt. If an attempt encounters a transient network error or bot challenge, releasing the context and leasing anew ensures the next attempt routes through a different regional proxy bridge.
2. **Exponential Backoff with Jitter**: Scale delays exponentially between retries ($2\text{s}, 4\text{s}, 8\text{s}, 16\text{s}, 30\text{s}$ capped, plus uniform jitter) to allow upstream networks to stabilize without causing thundering herd collisions.

---

## 3. Core Architecture

```
                      PER-ATTEMPT PROXY ROTATION & BACKOFF
                      
                           Scrape Page Request
                                   │
                                   ▼
                        ┌─────────────────────┐
                        │ Attempt Loop (1..6) │◄────────────────────────┐
                        └──────────┬──────────┘                         │
                                   │                                    │
                                   ▼                                    │
                        Lease Fresh Context from FIFO Worker Queue      │
                        (San Francisco, Dallas, Chicago, NY, etc.)      │
                                   │                                    │
                                   ▼                                    │
                        Execute Navigation (Playwright)                 │
                                   │                                    │
                           ┌───────┴───────┐                            │
                           │   Success?    │                            │
                           └───┬───────┬───┘                            │
                          YES  │       │ NO (Network error, timeout)    │
                               │       │                                │
                               │       ▼                                │
                               │   Calculate Exponential Backoff        │
                               │   Delay = min(30s, base * 2^(att-1))   │
                               │           + uniform(0.1, 1.0)          │
                               │   (0.0s if retry_delay == 0.0)         │
                               │       │                                │
                               │       ▼                                │
                               │   Release Context back to Queue        │
                               │   (Next attempt leases a new proxy)    │
                               │       │                                │
                               │       └────────────────────────────────┘
                               ▼
                        Return Extracted Listings
```

### 3.1 Per-Attempt Proxy Rotation
In `AirbnbCollector._scrape_corridor`, `async with self.lease_context()` is scoped inside the retry loop rather than wrapping the entire page loop:

```python
for attempt in range(1, self.max_attempts + 1):
    async with self.lease_context() as context:
        page = await context.new_page()
        try:
            # Navigate and extract comp listings...
            fetch_success = True
            break
        except Exception as exc:
            if attempt < self.max_attempts:
                backoff = self._calculate_backoff(attempt)
                if backoff > 0:
                    await asyncio.sleep(backoff)
            else:
                logger.warning(f"Could not fetch {loc} page {page_num}: {exc}")
        finally:
            await page.close()
```

Because `self._worker_queue` operates as a FIFO queue across multiple regional proxy hubs, releasing the context on failure and re-leasing immediately assigns a different out-of-state proxy endpoint for the next retry.

### 3.2 Exponential Backoff with Jitter
The delay between retries scales exponentially with randomized jitter:

$$\text{delay}(a) = \min\left(T_{\max}, T_{\text{base}} \times 2^{a - 1}\right) + \text{Uniform}(0.1, 1.0)$$

Where:
- $T_{\text{base}} = 2.0\text{ seconds}$
- $T_{\max} = 30.0\text{ seconds}$
- Default `max_attempts = 6`
- Attempt 1 is immediate ($0\text{s}$). Backoff occurs before attempts 2 through 6.

### 3.3 Zero-Sleep Test Invariant
Automated test suites must never execute real wall-clock sleeps:

```python
def _calculate_backoff(self, attempt: int) -> float:
    if getattr(self, "retry_delay", 1.0) == 0.0 or os.getenv("SCRAPE_RETRY_BACKOFF") == "0.0":
        return 0.0
    base = getattr(self, "base_retry_delay", 2.0)
    max_delay = getattr(self, "max_retry_delay", 30.0)
    raw = min(max_delay, base * (2 ** max(0, attempt - 1)))
    return raw + random.uniform(0.1, 1.0)
```

---

## 4. Component Scope

The changes are isolated strictly to `src/airbnb_collector.py`:
1. **`AirbnbCollector.__init__`**: Default `max_attempts = 6`, `base_retry_delay = 2.0`, `max_retry_delay = 30.0`.
2. **`AirbnbCollector._calculate_backoff`**: Helper method implementing exponential backoff with jitter and zero-sleep override.
3. **`AirbnbCollector._scrape_corridor`**: Scope context lease per attempt so each retry rotates to the next available worker context in the pool.

---

## 5. Verification Plan

### Automated Tests
1. **Backoff Bounds & Zero-Sleep**: Test `_calculate_backoff` across attempts 1..6, verifying exponential doubling, max cap adherence ($30\text{s}$), jitter range, and immediate $0\text{s}$ return when `retry_delay=0.0`.
2. **Proxy Rotation on Retry**: Mock `_worker_queue` with multiple contexts. Simulate a transient failure on attempt 1 and verify attempt 2 leases the next distinct context from the queue.
3. **Regression Suite**: Run `tests/test_airbnb_collector.py` (< 1.5s execution) to verify all existing comp scraping behaviors continue passing with zero test delay.
