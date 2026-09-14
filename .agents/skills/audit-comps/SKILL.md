---
name: audit-comps
description: >-
  Audits the active competitor portfolio in config/comps_registry.json against Villa del Sol's 6-factor luxury rubric.
  Inspects listing descriptions, room counts, bathroom ratios, on-site owner presence, capacity caps (<12 guests),
  and low review ratings (<4.70★) to identify invalid comps and propose exclusions.
---

# Competitor Listing Audit & Exclusion Skill

This skill guides the AI assistant in systematically reviewing and auditing competitor listings in `config/comps_registry.json` to identify invalid, misleading, or poor-quality comps that distort pricing and absorption analytics, and preparing formal proposals for disqualification.

---

## 1. Disqualification & Exclusion Criteria

A listing must be flagged for disqualification (`is_valid_comp: false`, status: `EXCLUDE`) if it triggers any of the following mandatory disqualification rules:

1. **Loss of Complete Privacy / Owner Presence**:
   - Host/owner resides on the premises (e.g. basement, guest house, attached wing) or shares spaces.
   - Dual-house setups rented together across a public street rather than a single estate compound.
2. **Severe Capacity Restriction**:
   - Strictly capped at fewer than 12 guests (e.g., max 7–10 guests). Cannot serve Villa del Sol's core audience (12–16+ guests).
3. **Severe Bathroom Bottleneck**:
   - Fewer than 3.0 full bathrooms for groups $\ge 14$ guests (e.g. 2.5 BA for 16 guests creates extreme morning congestion).
4. **Poor Track Record & Low Guest Satisfaction**:
   - Review rating $< 4.70★$ (below luxury STR standards, representing chronic cleanliness, maintenance, or host communication issues).
5. **Property Type Mismatch**:
   - Mislabeled rental units, townhouses, duplexes, or apartments without private estate grounds.
6. **Missing Private Pool**:
   - Properties lacking a private in-ground swimming pool (e.g. community pool only or no pool).
7. **Extreme Location Outlier**:
   - Outside the Phoenix East Valley competitive drive corridors (e.g., Buckeye, Surprise, Maricopa, Casa Grande).

---

## 2. Standard Operational Workflow

### Step 1: Execute Automated Audit Sweep
Run the CLI audit command:
```bash
.venv/bin/python -m src.cli audit-comps
```
Optional flags:
- `--save-report`: Exports a full markdown report artifact to `data/comp_portfolio_audit.md`.
- `--json`: Outputs structured JSON for automated ingestion.

### Step 2: Review Findings & Proposal
- Review the proposed disqualified listings and their specific reasons.
- Verify photo galleries and listing descriptions in `data/enriched_comps/{listing_id}.json`.
- Present the disqualification proposal to the host for confirmation.

### Step 3: Execute Disqualification
Once the host confirms, disqualify the listings:
```bash
.venv/bin/python -m src.cli disqualify-comp <listing_id> --reason "<detailed_reason>"
```
This command automatically:
1. Moves the listing from `tier_a` or `tier_b` to the `"disqualified"` section in `config/comps_registry.json`.
2. Updates `config/listing_specs.json`.
3. Purges single-comp cache files (`data/cache/search_*_comp_{id}.json`).
4. Purges historical competitor sales from `data/reservations.db`.
5. Refreshes the live dashboard (`docs/index.html`).

---

## 3. Requalifying a Comp
If a listing was misclassified or has been upgraded by the host:
```bash
.venv/bin/python -m src.cli requalify-comp <listing_id> --tier <tier_a|tier_b>
```
This restores the listing to active status and triggers re-evaluation.
