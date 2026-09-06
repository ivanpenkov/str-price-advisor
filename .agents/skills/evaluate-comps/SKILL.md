---
name: evaluate-comps
description: >-
  Evaluates luxury short-term rental competitor listings against Villa del Sol (Tempe, AZ),
  assessing validity, category scores, a desirability ratio, and clear justification using
  a 6-factor quality, sizing, and market asset valuation rubric. Use this skill whenever you need
  to evaluate new comps, audit existing comps, or update adjustment ratios in config/comps_registry.json.
---

# Luxury Comp Evaluation & Desirability Adjustment Skill

This skill guides the AI assistant in systematically evaluating short-term rental listings against **Villa del Sol** (920 E Carver Rd, Tempe, AZ) to assign:
1. **Comp Validity**: `is_valid_comp` (`true` or `false`).
2. **Category Scores**: Ratings (0–100) across 6 weighted categories.
3. **Property Sizing & Asset Valuation**: Verified living area (sq ft), lot acreage, and estimated market asset value via public records and the Corridor Hedonic Pricing Model.
4. **Desirability Ratio**: Multiplier representing competitor value relative to Villa del Sol ($1.00 = \text{Equal Quality}$).
5. **Agent Rationale**: Concise, professional justification mentioning yard size, house footprint, and asset valuation tiers.

---

## 1. Our Baseline Property Profile (Villa del Sol)

Always compare competitor listings against Villa del Sol's verified specs from `data/our_property_profile.json`:

- **Location**: Quiet, gated luxury enclave in **South Tempe, AZ** (minutes from ASU Research Park, Sky Harbor, East Valley corridors, 15–20 min to Old Town Scottsdale).
- **Lot & Space**: Gated **¾-acre (0.75-acre) private compound** with main house + detached 1BR/1BA luxury guest casita, totaling **5,400 sq ft**.
- **Asset Valuation Anchor**: **\$2,000,000** public records benchmark (composite score anchor: **88.0 pts**).
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

## 2. 6-Category Weighted Evaluation Rubric

Evaluate each comp across six distinct dimensions on a scale of **0 to 100**:

### A. Outdoor Resort Yard & Lot Size (Weight: 25%)
Outdoor scoring distinguishes between **Winter (Oct 1 – Apr 30)** and **Summer (May 1 – Sep 30)**:

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

#### 2. Pool Size & Volume (Villa del Sol = 30,000-gal Saltwater Pool with Grotto):
- **Large / Resort-Scale ($\ge 25,000$ gal or $\ge 35'$ length or waterfall/grotto/slide)**: **+6 pts**
- **Standard Residential Pool**: **0 pts** (Neutral)
- **Small / Cocktail / Plunge Pool (< 10,000 gal or plunge)**: **-8 pts**

#### 3. Lot Acreage (Villa del Sol = 0.75-acre gated lot):
- **$\ge 1.0$ acre**: **+8 pts** (Massive private grounds)
- **$0.65 - 0.99$ acre**: **+5 pts** (Villa del Sol 0.75-acre tier)
- **$0.35 - 0.64$ acre**: **+2 pts** (Quarter-to-half acre standard)
- **$< 0.20$ acre**: **-6 pts** (Cramped suburban tract lot)

#### 4. Sports Courts & Other Yard Features:
- **Full Tennis Court**: **+14 pts**
- **Dedicated Sports Court (Basketball / Pickleball)**: **+10 pts**
- **Multi-Sport Complex (Tennis + Pickleball / Basketball)**: **+16 pts**
- **Private Sauna / Cold Plunge / Wellness**: **+6 pts**
- **Putting Green**: **+5 pts**
- **Covered BBQ Pavilion / Gas Fire Pit**: **+5 pts**
- **Custom Waterfall Grotto / Slide**: **+5 pts**

### B. Bedrooms, Bathrooms & House Size (Weight: 20%)
- **Bedrooms**: $\ge 7$ BR (+20), 6 BR (+15), 5 BR (+8).
- **Bathrooms**: $\ge 5.5$ BA (+15), $\ge 4.5$ BA (+10), $\ge 3.5$ BA (+5).
- **Guest Capacity**: $\ge 16$ guests (+10), $\ge 14$ guests (+6).
- **Casita Presence**: Detached guest house / suite (+5).
- **Living Area (House Sq Ft, Villa del Sol = 5,400 sq ft)**:
  - **$\ge 7,000$ sq ft**: **+10 pts** (Mega estate)
  - **$5,000 - 6,999$ sq ft**: **+6 pts** (Villa del Sol 5,400 sq ft tier)
  - **$4,000 - 4,999$ sq ft**: **+2 pts** (Mid-size luxury)
  - **$< 3,000$ sq ft**: **-8 pts** (Under-sized living space)

### C. Property Scale & Market Asset Value (Weight: 15%)
Grades estimated market asset valuation against Villa del Sol's **\$2.0M baseline anchor** (88.0 pts):
- **$\ge \$4.0\text{M}$**: **98 pts** (Ultra-luxury trophy asset)
- **$\ge \$3.5\text{M}$**: **96 pts**
- **$\ge \$2.8\text{M}$**: **93 pts**
- **$\ge \$2.2\text{M}$**: **90 pts**
- **$\ge \$1.8\text{M}$**: **88 pts** (Villa del Sol \$2.0M baseline anchor)
- **$\ge \$1.5\text{M}$**: **82 pts**
- **$\ge \$1.2\text{M}$**: **75 pts**
- **$< \$1.2\text{M}$**: **68 pts** (Entry-level rental)

#### Hedonic Corridor Price Benchmarks ($\$/\text{sq ft}$):
- Paradise Valley: \$800/sq ft
- Scottsdale / Old Town: \$550/sq ft
- North Scottsdale: \$575/sq ft
- South Scottsdale: \$525/sq ft
- Arcadia: \$475/sq ft
- South Tempe: \$400/sq ft
- Chandler / Ahwatukee: \$380/sq ft
- Gilbert: \$350/sq ft
- Mesa: \$340/sq ft
- Default: \$380/sq ft
- *Lot Multiplier*: $\ge 1.0$ ac (1.25x), $\ge 0.65$ ac (1.15x), $\ge 0.45$ ac (1.08x), $< 0.20$ ac (0.90x).

### D. Interior Luxury, Entertainment & Finishes (Weight: 15%)
- **Billiards Table**: +8 pts
- **Private Movie Theater / Cinema**: +8 pts
- **Game Room / Arcade / Ping Pong**: +6 pts
- **Chef's Kitchen (SubZero/Wolf/Viking/Stainless)**: +7 pts
- **Designer Luxury Remodel / Estate Vibe**: +5 pts

### E. Location Corridor & Neighborhood (Weight: 15%)
- **95 pts**: Paradise Valley / Old Town / Central Scottsdale.
- **88 pts**: South Tempe (Villa del Sol baseline) / Arcadia periphery.
- **82 pts**: Chandler / Gilbert / Ahwatukee.
- **78 pts**: Mesa.
- **75 pts**: Other Phoenix Valley corridors.

### F. Reviews & Track Record (Weight: 10%)
- **98 pts**: 4.95+ rating with 20+ reviews.
- **94 pts**: 4.90–4.94 rating with 15+ reviews.
- **88 pts**: 4.80–4.89 rating (matches Villa del Sol: 4.83 with 76 reviews).
- **78 pts**: 4.70–4.79 rating.
- **70 pts**: < 4.70 rating.

---

## 3. Mathematical Desirability Ratio

Compute the holistic composite score:
$$\text{Comp Score} = 0.25 A + 0.20 B + 0.15 C + 0.15 D + 0.15 E + 0.10 F$$

Villa del Sol baseline score is **88.0 / 100**.

### Sensitivity-Scaled Expansion Formula
$$\text{Delta} = \frac{\text{Comp Score} - 88.0}{88.0}$$
$$\text{Ratio} = \text{round}\left(\max\left(0.65, \min\left(1.35, 1.0 + 2.0 \times \text{Delta}\right)\right), 2\right)$$

### Adjustment Price Formula
When guests evaluate prices on Airbnb:
$$\text{Adjusted Comp Rate} = \frac{\text{Raw Effective Rate}}{\text{Ratio}}$$

---

## 4. Disqualification Rules (`is_valid_comp = false`)

Mark a listing as `is_valid_comp: false` if:
1. **Property Type**: Townhouse, condo, duplex, or shared home.
2. **Missing Essential Amenity**: No private swimming pool.
3. **Severe Capacity Mismatch**: Fewer than 5 true bedrooms or maximum capacity < 12 guests.
4. **Extreme Location Outlier**: Outside competitive drive corridor (e.g., Surprise, Buckeye, Casa Grande, Maricopa).

---

## 5. Output Schema

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
    "heating_source": "Explicit free pool heat mentioned in listing",
    "pool_size": "large",
    "size_source": "Resort-scale pool with waterfall grotto",
    "gallons": 28000
  },
  "property_specs": {
    "sqft": 5800,
    "sqft_source": "Listing Disclosed",
    "lot_acres": 0.85,
    "lot_source": "Listing Disclosed",
    "est_property_value": 3190000.0,
    "property_value_source": "Corridor Hedonic Est.",
    "str_license": "STR-001234"
  },
  "composite_score": 90.2,
  "winter_composite_score": 90.2,
  "summer_composite_score": 88.4,
  "category_scores": {
    "outdoor": 91,
    "capacity": 90,
    "property_value": 93,
    "interior": 87,
    "location": 95,
    "reputation": 92
  },
  "rationale": "Premium comp (5% superior desirability, Winter). Features 0.85-acre lot, free heated pool, $3.2M asset tier."
}
```
