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
- **Lot & Space**: Gated **¾-acre private compound** with main house + detached 1BR/1BA luxury guest casita, totaling **5,400 sq ft**.
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
Outdoor scoring distinguishes between **Winter (Oct 1 – Apr 30)** and **Summer (May 1 – Sep 30)** due to Phoenix water temperature dynamics (unheated pools drop to ~55°F in winter, making heating essential, while summer water reaches 85°F–92°F naturally):

#### 1. Pool Heating Status (Villa del Sol = Free Heated Year-Round):
- **Winter (Oct 1 – Apr 30)**:
  - **Free / Included Pool Heat**: **+18 pts** (Double the boost of a hot tub)
  - **Standard Heated (no fee disclosed)**: **+12 pts**
  - **Fee-Based Pool Heat (\$50–\$150/night)**: **+8 pts** (Guest incurs extra cost/friction)
  - **Unheated Pool**: **0 pts** + **-10 pts Winter Penalty** (Pool is essentially unusable)
  - **Heated Spa / Hot Tub**: **+9 pts**
- **Summer (May 1 – Sep 30)**:
  - **Free Heated**: **+6 pts**
  - **Standard / Fee Heated**: **+4 pts**
  - **Unheated**: **0 pts** (No winter penalty)
  - **Heated Spa / Hot Tub**: **+5 pts**

#### 2. Pool Size & Volume (Villa del Sol = 30,000-gal Saltwater Resort Pool with Rock Grotto):
- **Large / Resort-Scale ($\ge 25,000$ gal or $\ge 35'$ length or waterfall/grotto/slide)**: **+6 pts**
- **Standard Residential Pool**: **0 pts** (Neutral)
- **Small / Cocktail / Plunge Pool (< 10,000 gal or labeled plunge)**: **-8 pts** (Severe capacity constraint for 16 guests)

#### 3. Sports Courts & Other Yard Features:
- **Full Tennis Court**: **+14 pts**
- **Dedicated Sports Court (Basketball / Pickleball)**: **+10 pts**
- **Multi-Sport Complex (Tennis + Pickleball / Basketball)**: **+16 pts**
- **Private Sauna / Cold Plunge / Wellness**: **+6 pts**
- **Putting Green**: **+5 pts**
- **Covered BBQ Pavilion / Gas Fire Pit**: **+5 pts**
- **Custom Waterfall Grotto / Slide**: **+5 pts**

### B. Bedrooms, Bathrooms & Capacity (Weight: 25%)
- **100**: 7+ large bedrooms, 6+ bathrooms (nearly all ensuites), sleeps 16+ comfortably in real beds (kings/queens), **$\ge 6,500$ sq ft expansive estate footprint**, detached casita for multi-family privacy.
- **85–95**: 6 bedrooms, 5–6 bathrooms, sleeps 16, **5,000–6,400 sq ft** (Villa del Sol ground truth), detached casita or spacious suites.
- **70–84**: 5 bedrooms with fewer bathrooms (e.g. 3–4 baths for 14 guests), **$< 4,000$ sq ft** dense layout, heavy reliance on bunks.
- **< 60**: < 4 bedrooms, < 3 bathrooms, or unable to host 12+ adults comfortably (Disqualify).

### C. Interior Luxury, Entertainment & Finishes (Weight: 20%)
- **100**: Modern designer estate remodel, **private movie theater / cinema**, championship billiards table, arcade / game room, chef-grade kitchen with SubZero / Miele / Viking / Wolf appliances, Savant / Sonos audio, luxury linens.
- **85–95**: Clean contemporary luxury aesthetic, billiards or dedicated game room, stainless appliances, quartz/granite counters.
- **70–84**: Standard builder-grade finishes, older furniture, basic TV setup, minimal indoor entertainment.
- **< 60**: Outdated 1990s interiors, worn furnishings, low ceilings.

### D. Location Corridor & Neighborhood (Weight: 15%)
- **100**: Prime Paradise Valley or central Old Town Scottsdale luxury corridor (+10% to +25% peak seasonal market demand).
- **85–90**: South Tempe (Villa del Sol baseline) / North Central Chandler / South Scottsdale / Arcadia periphery.
- **70–80**: Gilbert / Central Mesa / South Chandler.
- **< 70**: Peripheral suburbs (far East Mesa, Queen Creek, Apache Junction) located >35 minutes from airport/events.

### E. Reviews & Track Record (Weight: 10%)
- **100**: 4.95+ rating with 30+ reviews, Guest Favorite / Superhost status.
- **85–95**: 4.80–4.94 rating with 20+ reviews (matches Villa del Sol: 4.83 with 76 reviews).
- **70–84**: 4.60–4.79 rating, or new listing with < 5 reviews.
- **< 70**: < 4.60 rating, or reviews mentioning cleanliness, noise, or maintenance issues.

---

## 3. Mathematical Desirability Ratio

Compute the holistic composite score:
$$\text{Comp Score} = 0.30 A + 0.25 B + 0.20 C + 0.15 D + 0.10 E$$

Villa del Sol baseline score is **88.0 / 100**.

### Sensitivity-Scaled Expansion Formula
To avoid artificial mathematical compression where scores near 100 max out at only 1.14x, the system applies a **sensitivity factor ($\text{Sensitivity} = 2.0$)** centered at Villa del Sol's 88.0 benchmark:

$$\text{Delta} = \frac{\text{Comp Score} - 88.0}{88.0}$$
$$\text{Ratio} = \text{round}\left(\max\left(0.65, \min\left(1.35, 1.0 + 2.0 \times \text{Delta}\right)\right), 2\right)$$

- **Equal Quality Comp (Score 88.0)**:
  $$\text{Ratio} = 1.0 + 2.0 \times \frac{0}{88.0} = \mathbf{1.00x} \quad \text{(Peer)}$$
- **Superior Luxury Estate (Score 98.8, e.g. 7BR, 7k sq ft, Tennis, Sauna)**:
  $$\text{Delta} = \frac{98.8 - 88.0}{88.0} = +0.1227 \implies \text{Ratio} = 1.0 + 2.0 \times 0.1227 = \mathbf{1.25x} \quad \text{(Superior)}$$
- **Top Tier Mega Compound (Score 100.0, e.g. 9BR, 10k sq ft PV Resort)**:
  $$\text{Delta} = \frac{100.0 - 88.0}{88.0} = +0.1364 \implies \text{Ratio} = 1.0 + 2.0 \times 0.1364 = \mathbf{1.27x} \quad \text{(Top Tier)}$$
- **Moderate Comp (Score 76.0, e.g. 5BR Mesa basic house)**:
  $$\text{Delta} = \frac{76.0 - 88.0}{88.0} = -0.1364 \implies \text{Ratio} = 1.0 + 2.0 \times (-0.1364) = \mathbf{0.73x} \quad \text{(Discount)}$$

### Adjustment Price Formula
When guests evaluate prices on Airbnb:
$$\text{Adjusted Comp Rate} = \frac{\text{Raw Effective Rate}}{\text{Ratio}}$$

- **Discount Comp (Ratio 0.75)**: The comp is 25% less desirable than Villa del Sol. Its \$600 rate adjusts to:
  $$\frac{\$600}{0.75} = \$800$$
- **Superior Comp (Ratio 1.25)**: The comp is 25% more desirable than Villa del Sol (e.g. 7k sq ft tennis estate). Its \$1,250 rate adjusts to:
  $$\frac{\$1,250}{1.25} = \$1,000$$

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
  "validity_reason": "Valid 6BR luxury estate comp in Scottsdale.",
  "desirability_ratio": 1.05,
  "winter_ratio": 1.05,
  "summer_ratio": 1.01,
  "pool_specs": {
    "has_pool": true,
    "heating": "free",
    "heating_source": "Explicit free / complimentary pool heat mentioned in listing text",
    "pool_size": "large",
    "size_source": "Resort-scale pool with waterfall grotto",
    "gallons": 28000
  },
  "composite_score": 90.2,
  "winter_composite_score": 90.2,
  "summer_composite_score": 88.4,
  "category_scores": {
    "outdoor": 91,
    "capacity": 90,
    "interior": 87,
    "location": 95,
    "reputation": 92
  },
  "winter_category_scores": { ... },
  "summer_category_scores": { ... },
  "rationale": "Premium comp (5% superior desirability, Winter). Features free heated pool, resort-scale pool, private tennis court.",
  "winter_rationale": "...",
  "summer_rationale": "..."
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
