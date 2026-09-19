# Villa del Sol: Comprehensive Audit of Suspect Reservations & Revenue Leakage

**Property:** Villa del Sol (920 E Carver Rd, Tempe, AZ 85284)  
**Management Partner:** Kivoya (Streamline VRS Unit ID: `503802`)  
**Audit Period:** May 21, 2024 to Present (September 16, 2026)  
**Total Completed Bookings Analyzed:** 114 Reservations  
**Document Purpose:** Master forensic audit record cataloging all suspect reservations for which Streamline Guest Folios and ledgers must be reviewed. Establishes the documented revenue leaks across cleaning fee spreads, uncredited guest folio fees, and administrative surcharges.

---

## 1. Governing Business Agreement & Revenue Model

Per the binding agreement between Property Owner (Ivan Penkov) and Kivoya (Alma Chavez) formalized on **May 21, 2024**:

1. **Cleaning Fee Pass-Through Invariant**:
   - Kivoya charges guests the **exact same amount** that is disbursed directly to the cleaning vendor (**Nuvia**).
   - Cleaning is a strict dollar-for-dollar pass-through expense ($0 company profit).
   - Property maintenance, restocking, and cleaning supplies are billed separately to the Owner as operating expenses.
2. **Historical Cleaning Rates**:
   - **May 21, 2024 – Mid-2025**: Nuvia's agreed rate was **$450.00** per turnover.
   - **Mid-2025 – Present**: Nuvia's agreed rate was raised to **$500.00** per turnover.
3. **Surplus Revenue Sharing**:
   - If guests were charged more than Nuvia's actual rate (e.g., $550 charged vs. $500 paid), the spread is distributable rental revenue and **must be split 82% to Owner / 18% to Kivoya**.
   - Under no circumstances is Kivoya permitted to retain a spread or markup on cleaning above the Gross Rent line.
4. **Ancillary Guest Fees & Additional Occupancy**:
   - Any guest charges for early check-in, late check-out, extra guests, or pet fees represent compensation for property usage, utility consumption, and wear-and-tear.
   - These fees belong to the revenue pool and must be credited to the Owner (or split 82/18%). They cannot be pocketed 100% by Kivoya as internal "Company Income".

---

## 2. Summary of the Three Confirmed Revenue Leak Vectors

### Leak Vector 1: Uncredited Guest Folio Charges ($3,233.00 Across 23 Bookings)
* **The Admission**: On Reservation #18801 (July 3–6, 2026), a **$120.00 guest fee** (`$120.00 G`) was charged to the guest but omitted from the Owner Statement. Eduardo Pérez admitted this in writing on Sep 16, 2026, agreeing to credit the $120.00 only after it was flagged.
* **The Systemic Finding**: In Streamline VRS, **23 reservations** since May 21, 2024 contain recorded `folio_items` totaling **$3,233.00** (ranging from $40 to $465 per stay). On every monthly owner statement, `Additional Owner Income` was recorded as **$0.00**.

### Leak Vector 2: Cleaning Fee Markup Spread ($50/Stay Admitted in Writing)
* **The Admission**: On Sep 16, 2026, Eduardo Pérez explicitly stated:  
  > *"The cleaning fee charged to the guest is $550. Nuvia’s current rate for performing the cleaning is $500. Kivoya manages the cleaning service, including the overall coordination and oversight of the turnover."*
* **The Breach**: Kivoya is retaining a **$50.00 spread** per booking above Gross Rent. Turnover coordination is already compensated by Kivoya’s 18% management fee.
* **The Impact**:
  - Across the **66 reservations** in Cohort 1 (Mid-2025 to Present), Kivoya retained **$3,300.00** in cleaning markups. The Owner's 82% share is **$2,706.00**.
  - Across the **48 reservations** in Cohort 2 (May 2024 to Mid-2025), if guests were billed $500 while Nuvia was paid $450, Kivoya retained another **$2,400.00** ($1,968.00 Owner share).

### Leak Vector 3: The 3% "Administrative Fee" Siphon (Effective Fee = 25.0%)
* **The Admission**: Eduardo Pérez confirmed Kivoya adds a **3% Administrative Fee** to reservation ledgers to cover *"processing and administration associated with managing the reservation, payment processing, and PMS systems"*.
* **The Breach**:
  - On Airbnb bookings (`SC-ABnB-CO`), Airbnb is the merchant of record that handles transactions and software infrastructure.
  - Reservation administration and PMS software are core responsibilities covered by the 18% commission.
  - By taking an 18% commission ($297.20 on #17830), plus 3% admin fee ($66.03), plus $50 cleaning spread ($50.00), Kivoya's total cut on a $1,651 booking was **$413.23 (25.03% effective commission)** instead of 18.0%.

---

## 3. Complete Historical Audit: All 32 Reservations with Uncredited Guest Fees (2022 to Present)

* **Governing Rule**: While the cleaning fee agreement was formalized on May 21, 2024, **at no point in the history of the partnership did the Owner agree to Kivoya retaining early check-in, late check-out, or extra guest fees**. Under the core Property Management Agreement, all guest-paid charges for home occupancy are distributable revenue split **82% Owner / 18% Kivoya**.
* **The Evidence**: In Streamline VRS, **32 reservations** across the entire partnership history contain documented `folio_items` totaling **$5,892.39** that were never credited to the Owner on monthly statements (`Additional Owner Income: $0.00`).
* **Alma's Written Concession (Sep 18, 2026)**: *"So yes, if we charge this we should be doing the split... I was under the impression that we were doing it but the system does not automatically put it as a line item in the owner statement... I will work with eduardo to review any additional charges we may have done such as the one you saw to make those adjustments."*

### Complete Historical Ledger Table (32 Bookings · $5,892.39 Total)

| Conf # | Check-In | Check-Out | Nights | Channel | Reported Gross Rent | Streamline Folio Item(s) | Extra Guest Charge | Owner 82% Share |
| :--- | :---: | :---: | :---: | :--- | :---: | :---: | :---: | :---: |
| **#6816** | 2022-05-12 | 2022-05-15 | 3 | Kivoya Direct | $4,352.61 | `[779.39, 300]` | **$1,079.39** | $885.10 |
| **#6854** | 2022-05-19 | 2022-05-22 | 3 | VRBO | $3,934.36 | `[390]` | **$390.00** | $319.80 |
| **#7787** | 2022-07-29 | 2022-07-31 | 2 | VRBO | $1,458.00 | `[80]` | **$80.00** | $65.60 |
| **#7295** | 2022-10-06 | 2022-10-09 | 3 | Kivoya Direct | $4,455.00 | `[150]` | **$150.00** | $123.00 |
| **#9959** | 2023-05-25 | 2023-05-28 | 3 | Airbnb | $2,248.62 | `[120]` | **$120.00** | $98.40 |
| **#10965** | 2023-09-22 | 2023-09-25 | 3 | Kivoya Direct | $2,145.00 | `[300]` | **$300.00** | $246.00 |
| **#10852** | 2024-01-12 | 2024-01-15 | 3 | Airbnb | $1,911.66 | `[160]` | **$160.00** | $131.20 |
| **#11609** | 2024-03-09 | 2024-03-14 | 5 | VRBO | $5,704.97 | `[130]` | **$130.00** | $106.60 |
| **#12351** | 2024-04-17 | 2024-04-21 | 4 | Kivoya Direct | $2,847.00 | `[250]` | **$250.00** | $205.00 |
| *--* | *--* | *--* | *--* | *May 21, 2024* | *Cleaning Agreement* | *Takes Effect* | *--* | *--* |
| **#13194** | 2024-06-28 | 2024-06-30 | 2 | Airbnb | $1,129.31 | `[120]` | **$120.00** | $98.40 |
| **#13088** | 2024-09-13 | 2024-09-16 | 3 | Airbnb | $1,707.64 | `[40]` | **$40.00** | $32.80 |
| **#13426** | 2024-09-19 | 2024-09-22 | 3 | Kivoya Direct | $2,397.00 | `[120]` | **$120.00** | $98.40 |
| **#14233** | 2025-03-16 | 2025-03-21 | 5 | Kivoya Direct | $5,595.00 | `[-900, 500, 500]` | **$100.00** | $82.00 |
| **#14286** | 2025-03-21 | 2025-03-24 | 3 | VRBO | $3,804.25 | `[270]` | **$270.00** | $221.40 |
| **#14471** | 2025-03-27 | 2025-03-30 | 3 | VRBO | $3,804.25 | `[120]` | **$120.00** | $98.40 |
| **#14119** | 2025-04-10 | 2025-04-13 | 3 | Airbnb | $3,273.67 | `[120]` | **$120.00** | $98.40 |
| **#14474** | 2025-05-01 | 2025-05-04 | 3 | Airbnb | $2,409.15 | `[160]` | **$160.00** | $131.20 |
| **#16051** | 2025-06-23 | 2025-06-25 | 2 | Airbnb | $780.78 | `[100]` | **$100.00** | $82.00 |
| **#16092** | 2025-07-04 | 2025-07-06 | 2 | Booking.com | $720.02 | `[100]` | **$100.00** | $82.00 |
| **#16025** | 2025-08-22 | 2025-08-25 | 3 | Kivoya Direct | $1,497.00 | `[120]` | **$120.00** | $98.40 |
| **#16220** | 2025-09-12 | 2025-09-14 | 2 | Airbnb | $1,310.04 | `[50]` | **$50.00** | $41.00 |
| **#14962** | 2025-11-06 | 2025-11-09 | 3 | VRBO | $2,110.23 | `[168]` | **$168.00** | $137.76 |
| **#16394** | 2025-11-09 | 2025-11-13 | 4 | System default | $1,956.00 | `[140]` | **$140.00** | $114.80 |
| **#17260** | 2026-01-23 | 2026-01-26 | 3 | Airbnb | $2,367.54 | `[80]` | **$80.00** | $65.60 |
| **#17370** | 2026-02-02 | 2026-02-06 | 4 | Airbnb | $3,111.39 | `[120]` | **$120.00** | $98.40 |
| **#16774** | 2026-02-26 | 2026-03-01 | 3 | Booking.com | $2,570.59 | `[365, 100]` | **$465.00** | $381.30 |
| **#17152** | 2026-03-05 | 2026-03-08 | 3 | Airbnb | $3,599.48 | `[70]` | **$70.00** | $57.40 |
| **#17488** | 2026-03-26 | 2026-03-29 | 3 | Airbnb | $3,590.75 | `[50]` | **$50.00** | $41.00 |
| **#17862** | 2026-03-31 | 2026-04-07 | 7 | Airbnb | $4,382.99 | `[150]` | **$150.00** | $123.00 |
| **#17696** | 2026-04-23 | 2026-04-26 | 3 | Airbnb | $2,487.89 | `[150]` | **$150.00** | $123.00 |
| **#17112** | 2026-05-01 | 2026-05-05 | 4 | Airbnb | $2,862.04 | `[300]` | **$300.00** | $246.00 |
| **#18801** | 2026-07-03 | 2026-07-06 | 3 | Airbnb | $1,624.03 | `[120]` | **$120.00** *(Credit promised)* | $98.40 |
| **TOTALS** | | | | | | | **$5,892.39** | **$4,831.76** |

### Historical Subtotals:
* **Pre-May 21, 2024 (9 Bookings)**: **$2,659.39** Total Guest Fees $\rightarrow$ **$2,180.70** Owner 82% Share
* **Post-May 21, 2024 (23 Bookings)**: **$3,233.00** Total Guest Fees $\rightarrow$ **$2,651.06** Owner 82% Share
* **Grand Total (32 Bookings)**: **$5,892.39** Total Guest Fees $\rightarrow$ **$4,831.76** Owner 82% Share *(or $5,892.39 if 100% credited)*

---

## 4. Cohort 1: Mid-2025 to Present (June 1, 2025 – September 16, 2026)

* **Parameters**: Nuvia rate = **$500.00** | Guest cleaning billed = **$550.00** | Retained spread = **$50.00/booking**
* **Total Completed Bookings**: 66 Reservations  
* **Total Reported Gross Rent**: $135,567.16  
* **Cleaning Fee Spread Retained by Kivoya**: 66 × $50.00 = **$3,300.00** (Owner 82% share: **$2,706.00**)  
* **Estimated 3% Administrative Fees Retained**: ~$4,500.00 (Owner 82% share: **$3,690.00**)

| Conf # | Check-In | Check-Out | Nights | Channel | Gross Rent | Owner Payout (82%) | Nuvia Rate | Folio Item(s) | Extra Guest Charge |
| :--- | :---: | :---: | :---: | :--- | :---: | :---: | :---: | :---: | :---: |
| **#16036** | 2025-06-19 | 2025-06-23 | 4 | Airbnb | $1,695.68 | $1,390.46 | $500 | — | — |
| **#16051** | 2025-06-23 | 2025-06-25 | 2 | Airbnb | $780.78 | $640.24 | $500 | `[100]` | **$100.00** |
| **#14619** | 2025-06-25 | 2025-06-28 | 3 | Airbnb | $2,104.02 | $1,725.30 | $500 | — | — |
| **#16092** | 2025-07-04 | 2025-07-06 | 2 | Booking.com | $720.02 | $590.42 | $500 | `[100]` | **$100.00** |
| **#14316** | 2025-07-11 | 2025-07-13 | 2 | Airbnb | $1,190.52 | $976.23 | $500 | — | — |
| **#16326** | 2025-07-17 | 2025-07-20 | 3 | Booking.com | $1,316.15 | $1,079.24 | $500 | — | — |
| **#16317** | 2025-07-20 | 2025-07-23 | 3 | VRBO | $1,321.46 | $1,083.60 | $500 | — | — |
| **#14774** | 2025-07-23 | 2025-07-27 | 4 | Airbnb | $2,200.93 | $1,804.76 | $500 | — | — |
| **#15238** | 2025-08-07 | 2025-08-10 | 3 | Airbnb | $1,795.98 | $1,472.70 | $500 | — | — |
| **#16025** | 2025-08-22 | 2025-08-25 | 3 | Kivoya Direct | $1,497.00 | $1,227.54 | $500 | `[120]` | **$120.00** |
| **#16357** | 2025-08-25 | 2025-08-29 | 4 | VRBO | $1,996.52 | $1,637.15 | $500 | — | — |
| **#15789** | 2025-09-04 | 2025-09-07 | 3 | Airbnb | $2,284.75 | $1,873.50 | $500 | — | — |
| **#16220** | 2025-09-12 | 2025-09-14 | 2 | Airbnb | $1,310.04 | $1,074.23 | $500 | `[50]` | **$50.00** |
| **#16391** | 2025-09-30 | 2025-10-02 | 2 | Airbnb | $811.85 | $665.72 | $500 | — | — |
| **#15627** | 2025-10-02 | 2025-10-06 | 4 | Airbnb | $2,811.18 | $2,305.17 | $500 | — | — |
| **#16085** | 2025-10-10 | 2025-10-12 | 2 | Airbnb | $1,594.45 | $1,307.45 | $500 | — | — |
| **#16477** | 2025-10-16 | 2025-10-19 | 3 | VRBO | $1,987.29 | $1,629.58 | $500 | — | — |
| **#16364** | 2025-10-19 | 2025-10-25 | 6 | Airbnb | $3,194.48 | $2,619.47 | $500 | — | — |
| **#15393** | 2025-10-25 | 2025-10-30 | 5 | Airbnb | $2,758.45 | $2,261.93 | $500 | — | — |
| **#14962** | 2025-11-06 | 2025-11-09 | 3 | VRBO | $2,110.23 | $1,730.39 | $500 | `[168]` | **$168.00** |
| **#16394** | 2025-11-09 | 2025-11-13 | 4 | System default | $1,956.00 | $1,603.92 | $500 | `[140]` | **$140.00** |
| **#14874** | 2025-11-14 | 2025-11-18 | 4 | Airbnb | $2,526.78 | $2,071.96 | $500 | — | — |
| **#16656** | 2025-11-20 | 2025-11-24 | 4 | Airbnb | $2,617.01 | $2,145.95 | $500 | — | — |
| **#15130** | 2025-11-24 | 2025-11-29 | 5 | VRBO | $4,233.30 | $3,471.31 | $500 | — | — |
| **#16203** | 2025-12-03 | 2025-12-06 | 3 | VRBO | $1,751.70 | $1,436.39 | $500 | — | — |
| **#17273** | 2025-12-06 | 2025-12-14 | 8 | System default | $0.00 | $0.00 | $500 | — | — |
| **#14634** | 2025-12-14 | 2025-12-19 | 5 | Airbnb | $2,252.82 | $1,847.31 | $500 | — | — |
| **#15621** | 2025-12-23 | 2025-12-27 | 4 | Airbnb | $4,438.52 | $3,639.59 | $500 | — | — |
| **#17471** | 2026-01-16 | 2026-01-19 | 3 | Airbnb | $1,811.45 | $1,485.39 | $500 | — | — |
| **#17260** | 2026-01-23 | 2026-01-26 | 3 | Airbnb | $2,367.54 | $1,941.38 | $500 | `[80]` | **$80.00** |
| **#17370** | 2026-02-02 | 2026-02-06 | 4 | Airbnb | $3,111.39 | $2,551.34 | $500 | `[120]` | **$120.00** |
| **#16067** | 2026-02-06 | 2026-02-09 | 3 | VRBO | $3,032.17 | $2,486.38 | $500 | — | — |
| **#17770** | 2026-02-13 | 2026-02-15 | 2 | Airbnb | $1,049.76 | $860.80 | $500 | — | — |
| **#17566** | 2026-02-15 | 2026-02-19 | 4 | Airbnb | $2,102.31 | $1,723.89 | $500 | — | — |
| **#16329** | 2026-02-19 | 2026-02-22 | 3 | Airbnb | $3,016.49 | $2,473.52 | $500 | — | — |
| **#16774** | 2026-02-26 | 2026-03-01 | 3 | Booking.com | $2,570.59 | $2,107.88 | $500 | `[365, 100]` | **$465.00** |
| **#17152** | 2026-03-05 | 2026-03-08 | 3 | Airbnb | $3,599.48 | $2,951.57 | $500 | `[70]` | **$70.00** |
| **#17874** | 2026-03-08 | 2026-03-13 | 5 | Airbnb | $3,157.33 | $2,589.01 | $500 | — | — |
| **#17788** | 2026-03-13 | 2026-03-16 | 3 | Airbnb | $2,633.11 | $2,159.15 | $500 | — | — |
| **#17611** | 2026-03-16 | 2026-03-20 | 4 | VRBO | $3,027.35 | $2,482.43 | $500 | — | — |
| **#16909** | 2026-03-20 | 2026-03-23 | 3 | Airbnb | $3,878.75 | $3,180.58 | $500 | — | — |
| **#17488** | 2026-03-26 | 2026-03-29 | 3 | Airbnb | $3,590.75 | $2,944.42 | $500 | `[50]` | **$50.00** |
| **#17862** | 2026-03-31 | 2026-04-07 | 7 | Airbnb | $4,382.99 | $3,594.05 | $500 | `[150]` | **$150.00** |
| **#17158** | 2026-04-09 | 2026-04-12 | 3 | Airbnb | $3,599.73 | $2,951.78 | $500 | — | — |
| **#17575** | 2026-04-16 | 2026-04-19 | 3 | Airbnb | $2,074.17 | $1,700.82 | $500 | — | — |
| **#17696** | 2026-04-23 | 2026-04-26 | 3 | Airbnb | $2,487.89 | $2,040.07 | $500 | `[150]` | **$150.00** |
| **#17112** | 2026-05-01 | 2026-05-05 | 4 | Airbnb | $2,862.04 | $2,346.87 | $500 | `[300]` | **$300.00** |
| **#16304** | 2026-05-09 | 2026-05-12 | 3 | Airbnb | $1,979.62 | $1,623.29 | $500 | — | — |
| **#17733** | 2026-05-12 | 2026-05-15 | 3 | Airbnb | $1,288.49 | $1,056.56 | $500 | — | — |
| **#18600** | 2026-05-22 | 2026-05-24 | 2 | Kivoya Direct | $1,198.00 | $982.36 | $500 | — | — |
| **#18464** | 2026-05-26 | 2026-05-29 | 3 | Airbnb | $1,153.94 | $946.23 | $500 | — | — |
| **#18539** | 2026-06-07 | 2026-06-12 | 5 | Airbnb | $1,939.06 | $1,590.03 | $500 | — | — |
| **#17843** | 2026-06-12 | 2026-06-14 | 2 | Airbnb | $857.79 | $703.39 | $500 | — | — |
| **#17960** | 2026-06-19 | 2026-06-22 | 3 | Airbnb | $1,336.07 | $1,095.58 | $500 | — | — |
| **#17523** | 2026-06-25 | 2026-06-29 | 4 | Airbnb | $2,158.26 | $1,769.77 | $500 | — | — |
| **#18801** | 2026-07-03 | 2026-07-06 | 3 | Airbnb | $1,624.03 | $1,331.70 | $500 | `[120]` | **$120.00** |
| **#17830** | 2026-07-08 | 2026-07-12 | 4 | Unknown | $1,651.10 | $1,353.90 | $500 | — | — |
| **#16602** | 2026-07-16 | 2026-07-19 | 3 | Unknown | $1,051.27 | $862.04 | $500 | — | — |
| **#18215** | 2026-07-24 | 2026-07-26 | 2 | Unknown | $857.79 | $703.39 | $500 | — | — |
| **#18841** | 2026-07-30 | 2026-08-02 | 3 | Unknown | $1,364.79 | $1,119.13 | $500 | — | — |
| **#18055** | 2026-08-07 | 2026-08-09 | 2 | Unknown | $915.21 | $750.47 | $500 | — | — |
| **#18553** | 2026-08-11 | 2026-08-15 | 4 | Unknown | $1,719.19 | $1,409.74 | $500 | — | — |
| **#19142** | 2026-08-21 | 2026-08-23 | 2 | Unknown | $864.51 | $708.90 | $500 | — | — |
| **#17581** | 2026-08-29 | 2026-08-31 | 2 | Unknown | $809.96 | $664.17 | $500 | — | — |
| **#19130** | 2026-09-03 | 2026-09-06 | 3 | Unknown | $1,537.88 | $1,261.06 | $500 | — | — |
| **#17927** | 2026-09-10 | 2026-09-13 | 3 | Unknown | $1,647.00 | $1,350.54 | $500 | — | — |

---

## 5. Cohort 2: Agreement Date to Mid-2025 (May 21, 2024 – May 31, 2025)

* **Parameters**: Nuvia rate = **$450.00** | Audit objective: Verify whether guests were charged $450, $500, or $550.
* **Total Completed Bookings**: 48 Reservations  
* **Total Reported Gross Rent**: $128,357.26  
* **Potential Cleaning Spread Exposure**:
  - If billed at $500: 48 × $50.00 = **$2,400.00** (Owner 82% share: **$1,968.00**)
  - If billed at $550: 48 × $100.00 = **$4,800.00** (Owner 82% share: **$3,936.00**)

| Conf # | Check-In | Check-Out | Nights | Channel | Gross Rent | Owner Payout (82%) | Nuvia Rate | Folio Item(s) | Extra Guest Charge |
| :--- | :---: | :---: | :---: | :--- | :---: | :---: | :---: | :---: | :---: |
| **#12846** | 2024-05-23 | 2024-05-27 | 4 | Airbnb | $3,119.22 | $2,557.76 | $450 | — | — |
| **#12931** | 2024-05-31 | 2024-06-02 | 2 | Booking.com | $933.63 | $765.58 | $450 | — | — |
| **#12388** | 2024-06-06 | 2024-06-09 | 3 | Expedia | $2,204.43 | $1,807.63 | $450 | — | — |
| **#12934** | 2024-06-21 | 2024-06-23 | 2 | Airbnb | $1,190.52 | $976.23 | $450 | — | — |
| **#13211** | 2024-06-23 | 2024-06-24 | 1 | Kivoya Direct | $509.15 | $417.50 | $450 | — | — |
| **#13194** | 2024-06-28 | 2024-06-30 | 2 | Airbnb | $1,129.31 | $926.03 | $450 | `[120]` | **$120.00** |
| **#13296** | 2024-07-05 | 2024-07-07 | 2 | VRBO | $1,193.10 | $978.34 | $450 | — | — |
| **#13199** | 2024-07-19 | 2024-07-21 | 2 | Kivoya Direct | $1,198.00 | $982.36 | $450 | — | — |
| **#13044** | 2024-07-26 | 2024-07-28 | 2 | Airbnb | $1,190.52 | $976.23 | $450 | — | — |
| **#13546** | 2024-08-14 | 2024-08-20 | 6 | Kivoya Direct | $3,194.00 | $2,619.08 | $450 | — | — |
| **#12316** | 2024-08-23 | 2024-08-26 | 3 | Airbnb | $2,277.82 | $1,867.81 | $450 | — | — |
| **#13679** | 2024-08-30 | 2024-09-01 | 2 | Airbnb | $1,129.69 | $926.35 | $450 | — | — |
| **#13464** | 2024-09-06 | 2024-09-08 | 2 | Airbnb | $1,597.36 | $1,309.84 | $450 | — | — |
| **#13088** | 2024-09-13 | 2024-09-16 | 3 | Airbnb | $1,707.64 | $1,400.26 | $450 | `[40]` | **$40.00** |
| **#13426** | 2024-09-19 | 2024-09-22 | 3 | Kivoya Direct | $2,397.00 | $1,965.54 | $450 | `[120]` | **$120.00** |
| **#12743** | 2024-09-26 | 2024-09-30 | 4 | Airbnb | $3,048.73 | $2,499.96 | $450 | — | — |
| **#14052** | 2024-10-05 | 2024-10-11 | 6 | Airbnb | $3,166.31 | $2,596.37 | $450 | — | — |
| **#14013** | 2024-10-14 | 2024-10-21 | 7 | Airbnb | $3,681.26 | $3,018.63 | $450 | — | — |
| **#13648** | 2024-10-22 | 2024-10-26 | 4 | Booking.com | $2,436.91 | $1,998.27 | $450 | — | — |
| **#14009** | 2024-10-26 | 2024-10-31 | 5 | Airbnb | $2,456.23 | $2,014.11 | $450 | — | — |
| **#13839** | 2024-11-06 | 2024-11-10 | 4 | Kivoya Direct | $2,876.40 | $2,358.65 | $450 | — | — |
| **#14063** | 2024-11-11 | 2024-11-16 | 5 | Airbnb | $3,320.76 | $2,723.02 | $450 | — | — |
| **#14265** | 2024-11-22 | 2024-11-24 | 2 | VRBO | $1,193.10 | $978.34 | $450 | — | — |
| **#13442** | 2024-11-27 | 2024-11-30 | 3 | VRBO | $3,038.01 | $2,491.17 | $450 | — | — |
| **#13115** | 2024-12-05 | 2024-12-07 | 2 | VRBO | $1,193.10 | $978.34 | $450 | — | — |
| **#13774** | 2024-12-27 | 2025-01-02 | 6 | VRBO | $6,106.03 | $5,006.94 | $450 | — | — |
| **#14208** | 2025-01-06 | 2025-01-08 | 2 | Booking.com | $626.59 | $513.80 | $450 | — | — |
| **#13639** | 2025-01-15 | 2025-01-19 | 4 | Airbnb | $3,058.57 | $2,508.03 | $450 | — | — |
| **#14538** | 2025-01-19 | 2025-01-26 | 7 | Airbnb | $3,681.26 | $3,018.63 | $450 | — | — |
| **#13805** | 2025-01-30 | 2025-02-02 | 3 | Airbnb | $2,577.24 | $2,113.34 | $450 | — | — |
| **#13549** | 2025-02-07 | 2025-02-10 | 3 | Airbnb | $2,577.24 | $2,113.34 | $450 | — | — |
| **#14242** | 2025-02-13 | 2025-02-16 | 3 | VRBO | $3,035.96 | $2,489.49 | $450 | — | — |
| **#14261** | 2025-02-18 | 2025-02-25 | 7 | Airbnb | $5,855.28 | $4,801.33 | $450 | — | — |
| **#14784** | 2025-02-27 | 2025-03-01 | 2 | Airbnb | $2,001.28 | $1,641.05 | $450 | — | — |
| **#14124** | 2025-03-06 | 2025-03-09 | 3 | VRBO | $3,804.25 | $3,119.49 | $450 | — | — |
| **#14623** | 2025-03-14 | 2025-03-16 | 2 | Airbnb | $2,512.74 | $2,060.45 | $450 | — | — |
| **#14233** | 2025-03-16 | 2025-03-21 | 5 | Kivoya Direct | $5,595.00 | $4,587.90 | $450 | `[-900, 500, 500]` | **$100.00** |
| **#14286** | 2025-03-21 | 2025-03-24 | 3 | VRBO | $3,804.25 | $3,119.49 | $450 | `[270]` | **$270.00** |
| **#14471** | 2025-03-27 | 2025-03-30 | 3 | VRBO | $3,804.25 | $3,119.49 | $450 | `[120]` | **$120.00** |
| **#14616** | 2025-04-04 | 2025-04-09 | 5 | Airbnb | $5,609.20 | $4,599.54 | $450 | — | — |
| **#14119** | 2025-04-10 | 2025-04-13 | 3 | Airbnb | $3,273.67 | $2,684.41 | $450 | `[120]` | **$120.00** |
| **#14753** | 2025-04-18 | 2025-04-21 | 3 | VRBO | $3,032.17 | $2,486.38 | $450 | — | — |
| **#14585** | 2025-04-24 | 2025-04-27 | 3 | Airbnb | $3,019.40 | $2,475.91 | $450 | — | — |
| **#14474** | 2025-05-01 | 2025-05-04 | 3 | Airbnb | $2,409.15 | $1,975.50 | $450 | `[160]` | **$160.00** |
| **#14988** | 2025-05-09 | 2025-05-15 | 6 | VRBO | $3,951.04 | $3,239.85 | $450 | — | — |
| **#14618** | 2025-05-15 | 2025-05-18 | 3 | Airbnb | $2,409.15 | $1,975.50 | $450 | — | — |
| **#15717** | 2025-05-20 | 2025-05-25 | 5 | Kivoya Direct | $0.00 | $0.00 | $450 | — | — |
| **#14777** | 2025-05-29 | 2025-06-04 | 6 | Airbnb | $4,231.34 | $3,469.70 | $450 | — | — |

---

## 6. Financial Exposure & Cumulative Underpayment Estimate

| Leak Category | Inflow Scope | Formula | Estimated Total Retained by Kivoya | Estimated Owner 82% Underpayment |
| :--- | :--- | :--- | :---: | :---: |
| **Uncredited Folio Items** | 23 Confirmed Bookings | Direct guest add-on charges withheld | **$3,233.00** | **$2,651.06** *(or $3,233.00 if 100% credited)* |
| **Cleaning Spread (Cohort 1)** | 66 Bookings (Mid-2025 to Present) | $50.00 markup on Nuvia's $500 rate | **$3,300.00** | **$2,706.00** |
| **Cleaning Spread (Cohort 2)** | 48 Bookings (May 2024 to Mid-2025) | Estimated $50 markup on $450 rate | **$2,400.00** | **$1,968.00** |
| **3% Administrative Surcharges** | All 114 Bookings (~$263.9k Gross Rent) | 3% added to accommodation & clean | **~$8,350.00** | **~$6,847.00** |
| **ESTIMATED TOTALS** | | | **~$17,283.00** | **~$14,172.00 – $14,750.00** |

---

## 7. Strategic Communication & Negotiation Playbook (Preserving the Partnership)

### 7.1 Strategic Objective: Fix the Problem Without Blowing Up the Business
Your primary objective is to **reclaim underpaid revenue and enforce transparent pass-through accounting while keeping Kivoya motivated and managing Villa del Sol**. 

If you approach this as a criminal or adversarial fraud investigation, Kivoya's leadership will immediately become defensive, lawyer up, freeze communication, and terminate property management services—forcing an emergency operator transition and freezing funds.

To resolve this cooperatively:
1. **Never accuse individuals of bad faith or deceit**: When people feel accused of theft, they fight to the death to protect their ego and legal liability.
2. **Give them an honorable, face-saving "Software / Configuration Glitch" off-ramp**:
   - Frame the discrepancy as an **automated Streamline VRS system template oversight**.
   - Position it as: *"When we agreed on May 21, 2024 that cleaning is a direct 1-to-1 pass-through to Nuvia, Streamline’s default automated OTA pricing rules and fee routing were probably never updated. The software has just been automatically routing standard markups ($550 vs. $500) and guest add-ons to company accounts instead of distributable rent."*
   - This allows Alma and Eduardo to say: *"Oh wow, you're right! Our software settings were never updated after that agreement. Let's fix the system settings moving forward and issue a reconciliation credit on upcoming statements."*
   - Alma saves face, Eduardo doesn't get blamed, the error is corrected, and you recover your funds.
3. **Use the CPA as the Neutral "Third-Party Driver"**:
   - You are not personally nitpicking their ledgers; your CPA is simply conducting a standard annual tax factoring and pass-through audit under the May 2024 addendum and Arizona TPT rules.
4. **Avoid Internal Database Jargon**:
   - **Do NOT use terms like `"folio items"`**: This is internal Streamline VRS API/database schema terminology. Using it alerts them that you have reverse-engineered their raw backend feeds, causing them to panic and revoke API/portal access.
   - Replace `"folio items"` with natural terminology: *"guest add-on charges (early check-in, late check-out, extra guests, pets)"*.
   - Never use prosecutorial language like *"already conceded"* or *"confirmed violation"*. Use collaborative framing: *"as Eduardo kindly clarified"*.

---

### 7.2 Do You Need a Lawyer? (Legal & Economic Assessment)

**Recommendation: Not at this stage.**

1. **Economic Imbalance**:
   - The total estimated dispute is **$10,000 to $15,000**.
   - Retaining a commercial litigation or real estate attorney in Arizona typically requires an immediate **$5,000 to $10,000 retainer**, burning half of the disputed amount before formal discovery even begins.
2. **Immediate Partnership Destruction**:
   - The moment an attorney sends a demand letter, Kivoya is legally required to cease direct communication with you. They will terminate management of Villa del Sol, cancel pending reservations, hold your operating reserve/escrow in dispute, and you will have to scramble to find a new luxury property manager in Tempe.
3. **Unnecessary at Present**:
   - You already possess overwhelming leverage without legal counsel:
     - Eduardo admitted in writing that Kivoya charges $550 and pays Nuvia $500.
     - Eduardo admitted in writing that the $120 fee on #18801 was improper and promised a credit.
     - You hold the May 21, 2024 agreement and Arizona TPT tax filings.
4. **When to Retain Legal Counsel**:
   - Only escalate to an attorney if Alma completely repudiates the May 2024 agreement, refuses to reconcile obvious variances, or attempts retaliatory termination.

---

## 8. Recommended Collaborative Email Draft (Ready to Send)

### Timing & Prerequisites:
* **Wait for Alma’s Response First**: Do not send this immediately if you recently sent a private 1-on-1 message to Alma. Give her 12–24 hours to respond.
* Once she responds—or if she has not responded by the next business morning—send the following collaborative draft to **both Alma and Eduardo**:

```email
Subject: Villa del Sol – Mid-Year Pass-Through & Ledger Reconciliation

Hi Alma and Eduardo,

Thanks again for the breakdown and for catching that $120 credit on reservation #18801 for September—I appreciate your transparency on that.

My CPA is currently doing our mid-year reconciliation for Villa del Sol, specifically looking at how pass-through items (cleaning fees, guest add-ons, and lodging taxes) are routed under our May 21, 2024 agreement. 

Looking at the numbers Eduardo shared, it looks like Streamline’s automated fee templates may still be configured with default system markups (like the $550 cleaning fee vs. Nuvia's $500 rate, and the 3% administrative line item) rather than reflecting our agreed pass-through split. 

To help us easily reconcile this without asking you to manually pull dozens of individual reservation PDFs, could you please generate a Streamline Reservation Financial Export (Excel or CSV) for Villa del Sol from May 21, 2024 to Present?

Ideally, the report would include standard columns for:
  1. Confirmation ID & Booking Channel / Lead Source
  2. Accommodation / Room Rate Billed
  3. Cleaning Fee Billed to Guest vs. Amount Paid to Cleaners (Nuvia)
  4. Administrative / Reservation Fees
  5. Any Guest Add-on Charges (early check-in, late check-out, extra guests, pets)
  6. Distributed Gross Rent & Management Commission

To give my CPA a couple of benchmark examples to test the formula while you run that export, could you also send over the complete guest folios for just these two bookings:
  • #17112 (May 2026)
  • #16774 (Feb 2026)

I really appreciate your partnership in getting our system rules synchronized so our monthly reporting stays clean and seamless for both of our teams.

Best regards,
Ivan
```

### Why This Draft is Structurally Superior:
1. **Friendly & Constructive**: Opens by thanking Eduardo for catching the $120 credit instead of saying "fee already conceded."
2. **Preserves Technical Secrecy**: Eliminates all internal API jargon (`"folio item"`) while requesting the exact data under natural operational terms.
3. **Face-Saving Blame on Software**: Positions the issue as *"Streamline's automated fee templates may still be configured with default system markups"*.
4. **Low Pressure**: Requests only **2 sample folios** (#17112 has the $300 fee; #16774 has the $465 fee) instead of hitting them with an intimidating list of 8 bookings, while directing the bulk of the work to an automated master spreadsheet.

---

## 9. Multi-Phase Escalation Ladder

| Phase | Trigger | Action | Target Outcome |
| :--- | :--- | :--- | :--- |
| **Phase 1: Await 1-on-1 Reply** | Initial inquiry to Alma pending | Allow 12–24h for Alma’s private reply regarding Nuvia and early check-in fees. | Gauge whether executive leadership is cooperative or defensive. |
| **Phase 2: Master Export Request** | Next business morning | Send the collaborative email draft (Section 8) requesting the Master Export + 2 benchmark folios (#17112, #16774). | Secure automated ledger of all 114 bookings in Excel/CSV without friction. |
| **Phase 3: Targeted Folio Demand** | If Kivoya delays or refuses master export | Send targeted request for the **Top 8 High-Variance Folios** (Section 9.1 below). | Obtain hard documentary proof of $1,600+ in uncredited fees across multiple channels. |
| **Phase 4: Ledger Reconciliation & Settlement** | Once folios/export are in hand | Present itemized variance table showing ~$14.5k underpayment; propose scheduled credit across upcoming statements. | Full financial recovery without legal costs or relationship disruption. |

### 9.1 Fallback: Top 8 High-Variance Folios (Phase 3 Only)
If Kivoya claims they cannot run the master export, request individual folios for these 8 specific bookings:
1. **#18801** (07/03/2026 · Airbnb · $120.00 guest fee conceded)
2. **#17830** (07/08/2026 · Airbnb · $550.00 clean + $66.03 admin fee confirmed)
3. **#17112** (05/01/2026 · Airbnb · **$300.00** guest add-on)
4. **#16774** (02/26/2026 · Booking.com · **$465.00** guest add-on)
5. **#17696** (04/23/2026 · Airbnb · **$150.00** guest add-on)
6. **#14286** (03/21/2025 · VRBO · **$270.00** guest add-on)
7. **#16025** (08/22/2025 · Kivoya Direct · **$120.00** guest add-on)
8. **#13194** (06/28/2024 · Airbnb · **$120.00** guest add-on post-agreement)

---
*Generated by STR Price Advisor Audit Intelligence. Keep on file with monthly statements, vendor invoices, and the Property Management Agreement.*
