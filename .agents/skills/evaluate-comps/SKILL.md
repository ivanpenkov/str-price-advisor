---
name: evaluate-comps
description: >-
  Evaluates luxury short-term rental competitor listings against Villa del Sol (Tempe, AZ),
  assessing validity, category scores, a desirability ratio, and clear justification.
  Use this skill whenever you need to evaluate new comps, audit existing comps, or update
  adjustment ratios in config/comps_registry.json.
---

# Luxury Comp Evaluation & Desirability Adjustment Skill

This skill guides the AI assistant in systematically evaluating short-term rental listings against **Villa del Sol** (920 E Carver Rd, Tempe, AZ) to assign:
1. **Comp Validity**: `is_valid_comp` (`true` or `false`).
2. **Category Scores**: Ratings (0–100) across 5 weighted categories.
3. **Desirability Ratio**: Multiplier representing competitor value relative to Villa del Sol ($1.00 = \text{Equal Quality}$).
4. **Agent Rationale**: Concise, professional justification for the ratio.

---

## 1. Our Baseline Property Profile (Villa del Sol)

Always compare competitor listings against Villa del Sol's verified specs from `data/our_property_profile.json`:

- **Location**: Quiet, gated luxury enclave in **South Tempe, AZ** (minutes from ASU Research Park, Sky Harbor, East Valley corridors, 15–20 min to Old Town Scottsdale).
- **Lot & Space**: Gated **¾-acre private compound** with main house + detached 1BR/1BA luxury guest casita.
- **Capacity**: **6 Bedrooms**, **6 Bathrooms** (5.5 on Airbnb), **11 Beds**, **16 Guests**.
- **Outdoor Resort Amenities (30,000-gal saltwater pool)**:
  - Massive heated saltwater pool with custom rock waterfall grotto.
  - In-ground heated spa.
  - Regulation half basketball court with high-grade hoop.
  - Putting green with multiple holes.
  - Covered outdoor chef's kitchen, built-in BBQ grill, dining pavilion, gas fire pit.
- **Interior & Entertainment**:
  - Championship billiards table in dedicated game room.
  - Chef's kitchen with GE stainless steel appliances and seating for 16.
  - 4K smart TVs in every bedroom, high-speed 433+ Mbps mesh WiFi.
- **Reputation**: 4.83+ rating across 76+ reviews, professional Kivoya property management.

---

## 2. Weighted Evaluation Rubric

Evaluate each comp across five distinct dimensions on a scale of **0 to 100**:

### A. Outdoor Resort Yard & Amenities (Weight: 30%)
- **100**: Outstanding private resort yard: large heated pool with grotto/slide, spa, dedicated sports court (basketball or pickleball), putting green, expansive covered patio, outdoor kitchen, high privacy.
- **80–90**: Great pool & spa, nice turf yard, outdoor games (cornhole/ping pong), BBQ, but lacks full sports court (basketball/pickleball) or has smaller lot.
- **60–75**: Standard backyard pool and patio, basic furniture, no spa or extra sports amenities.
- **< 60**: Small or unheated plunge pool, cramped yard, close neighbors with zero privacy, or no pool (Disqualify).

### B. Bedrooms, Bathrooms & Capacity (Weight: 25%)
- **100**: 6+ large bedrooms, 6+ bathrooms (nearly all ensuites), sleeps 16+ comfortably in real beds (kings/queens), detached casita for multi-family privacy.
- **80–90**: 5–6 bedrooms, 4–5 bathrooms, sleeps 14–16, good balance of king/queen beds.
- **60–75**: 5 bedrooms with fewer bathrooms (e.g. 3 baths for 14 guests), heavy reliance on triple bunks or sofa beds.
- **< 60**: < 5 bedrooms, < 3 bathrooms, or unable to host 12+ adults comfortably (Disqualify).

### C. Interior Luxury & Finishes (Weight: 20%)
- **100**: Modern designer remodel, chef-grade kitchen, high-end stone/quartz counters, game room with pool table/arcade, luxury furnishings and linens.
- **80–90**: Clean, contemporary aesthetic, stainless appliances, dedicated game space or billiards.
- **60–75**: Standard builder-grade finishes, older furniture, minimal indoor entertainment.
- **< 60**: Outdated 1990s interiors, worn furnishings, low ceilings.

### D. Location Corridor & Neighborhood (Weight: 15%)
- **100**: Prime Paradise Valley or central Old Town Scottsdale luxury corridor (+5% to +15% peak seasonal market premium).
- **85–90**: South Tempe (Villa del Sol baseline) / North Central Chandler / South Scottsdale / Arcadia periphery.
- **70–80**: Gilbert / Central Mesa / South Chandler.
- **< 70**: Peripheral suburbs (far East Mesa, Queen Creek, Apache Junction) located >35 minutes from airport/events.

### E. Reviews & Track Record (Weight: 10%)
- **100**: 4.95+ rating with 50+ reviews, Guest Favorite / Superhost status.
- **85–95**: 4.80–4.94 rating with 20+ reviews (matches Villa del Sol).
- **70–84**: 4.60–4.79 rating, or new listing with < 5 reviews.
- **< 70**: < 4.60 rating, or reviews mentioning cleanliness, noise, or maintenance issues.

---

## 3. Mathematical Desirability Ratio

Compute the holistic composite score:
$$\text{Comp Score} = 0.30 A + 0.25 B + 0.20 C + 0.15 D + 0.10 E$$

Villa del Sol baseline score is approximately **88 / 100**.

Calculate the **Desirability Ratio**:
$$\text{Ratio} = \frac{\text{Comp Score}}{\text{Our Score (88)}}$$
*(Clamped to a realistic luxury comp range of 0.65 to 1.35)*.

### Adjustment Price Formula
When guests evaluate prices on Airbnb:
$$\text{Adjusted Comp Rate} = \frac{\text{Raw Effective Rate}}{\text{Ratio}}$$

- **Inferior Comp (Ratio 0.85)**: The comp is 15% less desirable than Villa del Sol. Its \$850 rate adjusts to:
  $$\frac{\$850}{0.85} = \$1,000$$
- **Superior Comp (Ratio 1.15)**: The comp is 15% more luxurious than Villa del Sol (e.g. PV estate). Its \$1,150 rate adjusts to:
  $$\frac{\$1,150}{1.15} = \$1,000$$

---

## 4. Disqualification Rules (`is_valid_comp = false`)

Mark a listing as `is_valid_comp: false` if:
1. **Property Type**: Townhouse, condo, duplex, or shared home.
2. **Missing Essential Amenity**: No private swimming pool.
3. **Severe Capacity Mismatch**: Fewer than 5 true bedrooms or maximum capacity < 12 guests.
4. **Extreme Location Outlier**: >30 miles away from Tempe/Scottsdale corridor (e.g., Surprise, Buckeye, Casa Grande).

For disqualified comps:
- Set `is_valid_comp: false`.
- Set `validity_reason`: Concise sentence explaining why (e.g. "Disqualified: Only 4 bedrooms and lacks a private swimming pool").
- Set `desirability_ratio: 0.50` (or estimate, but note that it will be excluded from adjusted pricing calculations).

---

## 5. Output Schema

For each listing in `config/comps_registry.json`, generate or update:

```json
{
  "is_valid_comp": true,
  "validity_reason": "Valid luxury estate comp in Scottsdale corridor.",
  "desirability_ratio": 0.94,
  "category_scores": {
    "outdoor": 85,
    "capacity": 90,
    "interior": 85,
    "location": 95,
    "reputation": 92
  },
  "composite_score": 88.2,
  "rationale": "High-end Scottsdale remodel with pool/spa and 6 bedrooms, but lacks Villa del Sol's full basketball court and ¾-acre gated lot."
}
```

---

## 6. Execution Workflow

When instructed to evaluate or update comp scores:
1. Ensure `data/our_property_profile.json` exists (run `python -m src.listing_enricher` if missing).
2. Read the comp's enriched profile from `data/enriched_comps/{listing_id}.json`.
3. Apply the 5-factor rubric against Villa del Sol's profile.
4. Update the comp entry in `config/comps_registry.json`.
5. Run `.venv/bin/python -m src.cli generate-html` to refresh the dashboard.
