---
name: discover-comps
description: >-
  Discovers high-quality luxury competitor listings geographically close to Villa del Sol (Tempe, Chandler, Ahwatukee,
  South Scottsdale within 10–15 miles) that feature 5+ bedrooms, 12+ guest capacity, private heated pools, and 4.85+ ratings.
  Ranks candidates by feature similarity to Villa del Sol and generates curated proposals for addition.
---

# Nearby Luxury Comp Discovery Skill

This skill guides the AI assistant in discovering and curating top-tier competitor properties that closely match Villa del Sol's luxury compound profile and geographic catchment area.

---

## 1. Discovery Philosophy & Inclusion Criteria

Rather than scraping arbitrary distant properties, this skill prioritizes **close-proximity luxury competitors** that directly compete for large group travelers booking in the South Tempe / East Valley corridor.

### Mandatory Inclusion Baseline:
1. **Target Geographies**: South Tempe, Chandler, Ahwatukee, Gilbert, and South Scottsdale (within 10–15 miles / 15–20 minutes drive of 920 E Carver Rd).
2. **True Capacity**: $\ge 5$ true bedrooms, sleeping $\ge 12$ guests (optimally 6–8 BR sleeping 16+).
3. **Resort Yard & Private Pool**: Mandatory private in-ground swimming pool (heated preferred) and spa.
4. **Reputation**: $\ge 4.85★$ guest rating (Superhost or Guest Favorite status).
5. **Single-Family Compound**: Private standalone residential estate (no duplexes, no shared homes, no apartment complexes).

### Bonus Similarity Multipliers (Matches Villa del Sol):
- **Gated compound / large lot** ($\ge 0.5$ acre).
- **Detached guest house / casita**.
- **Sports courts** (Basketball half-court, pickleball court, putting green).
- **Indoor entertainment** (Billiards room, movie theater, arcade).

---

## 2. Standard Operational Workflow

### Step 1: Scan Nearby Market Corridors
Run the discovery CLI command:
```bash
.venv/bin/python -m src.cli discover-comps --corridors tempe,chandler,ahwatukee,scottsdale --limit 20
```
Optional flags:
- `--tier <tier_a|tier_b>`: Filter specifically for Tier A (16+ guests) or Tier B (12–15 guests).
- `--min-rating 4.85`: Minimum rating threshold (default: 4.85).

### Step 2: Automated Similarity Scoring
The discovery engine:
1. Filters out any listing already in `config/comps_registry.json` or `config/comps_registry.json: excluded_comps`.
2. Scrapes full metadata and amenities via NordVPN proxy.
3. Computes a **Feature Similarity Index (0–100%)** against Villa del Sol.
4. Ranks candidate properties and generates a structured candidate dossier.

### Step 3: Present Candidate Dossier to Host
Present the top candidates with photos, specs, pricing, and similarity rationale to the host for approval.

### Step 4: Register Approved Comps
Once approved, register the new comps:
```bash
.venv/bin/python -m src.cli add-comp <airbnb_url_or_id> --scrape-prices
```
