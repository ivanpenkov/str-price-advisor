"""
Calendar and Cumulative Revenue Views for STR Price Advisor Dashboard.
Renders:
1. Streamline OwnerX Availability Calendar (6-month grid, half-day diagonal splits, clickable reservation modal)
2. Cumulative Annual Owner Revenue Pace Curves (2022-2027, Chart.js, KPI cards, summary table)
"""

from datetime import date, datetime, timedelta
import html
import json
from typing import Dict, List, Optional, Any


def get_calendar_revenue_css() -> str:
    """CSS styles for Availability Calendar, Reservation Modal, and Revenue tabs."""
    return """
    /* ==========================================================================
       CALENDAR TAB STYLES (Streamline OwnerX Availability View)
       ========================================================================== */
    .cal-wrapper {
      padding: 4px 0 20px 0;
    }
    .cal-controls-card {
      background: var(--bg-card);
      border: 1px solid var(--border-color);
      border-radius: 12px;
      padding: 16px 20px;
      margin-bottom: 20px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 16px;
    }
    .cal-nav-group {
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }
    .cal-btn {
      background: #334155;
      color: #f8fafc;
      border: 1px solid #475569;
      border-radius: 6px;
      padding: 6px 14px;
      font-size: 0.85rem;
      font-weight: 600;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      transition: all 0.15s ease;
    }
    .cal-btn:hover {
      background: #475569;
      border-color: #64748b;
    }
    .cal-select {
      background: #0f172a;
      color: #f8fafc;
      border: 1px solid #475569;
      border-radius: 6px;
      padding: 6px 12px;
      font-size: 0.85rem;
      font-weight: 600;
      cursor: pointer;
    }
    .cal-legend {
      display: flex;
      align-items: center;
      gap: 18px;
      flex-wrap: wrap;
    }
    .cal-legend-item {
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 0.85rem;
      font-weight: 600;
      color: #cbd5e1;
    }
    .cal-legend-swatch {
      width: 16px;
      height: 16px;
      border-radius: 3px;
      border: 1px solid rgba(255, 255, 255, 0.2);
    }
    .swatch-vacant {
      background: #1e293b;
      border: 1.5px solid #94a3b8;
    }
    .swatch-standard {
      background: #8CB811;
    }
    .swatch-owner {
      background: #8E24AA;
    }
    .swatch-maintenance {
      background: #D3761B;
    }
    .swatch-airbnb { background: #FF5A5F; }
    .swatch-vrbo { background: #2563EB; }
    .swatch-direct { background: #10B981; }
    .swatch-booking { background: #4F46E5; }
    .swatch-expedia { background: #D97706; }
    .swatch-admin { background: #F59E0B; }

    /* Calendar Color Toggle Switch */
    .cal-toggle-group {
      display: flex;
      align-items: center;
      gap: 10px;
      background: #0f172a;
      border: 1px solid #334155;
      border-radius: 20px;
      padding: 3px 6px;
    }
    .cal-toggle-label {
      font-size: 0.76rem;
      font-weight: 700;
      color: #94a3b8;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      margin-left: 6px;
    }
    .cal-toggle-pill {
      display: inline-flex;
      background: #1e293b;
      border-radius: 16px;
      padding: 2px;
      gap: 2px;
    }
    .cal-toggle-btn {
      background: transparent;
      border: none;
      color: #94a3b8;
      border-radius: 14px;
      padding: 4px 12px;
      font-size: 0.8rem;
      font-weight: 700;
      cursor: pointer;
      transition: all 0.15s ease;
    }
    .cal-toggle-btn.active {
      background: #2563eb;
      color: #ffffff;
      box-shadow: 0 2px 6px rgba(37, 99, 235, 0.4);
    }
    .cal-toggle-btn:hover:not(.active) {
      color: #f8fafc;
    }

    .cal-months-grid {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 20px;
      margin-bottom: 24px;
    }
    @media (max-width: 1200px) {
      .cal-months-grid {
        grid-template-columns: repeat(2, 1fr);
      }
    }
    @media (max-width: 768px) {
      .cal-months-grid {
        grid-template-columns: 1fr;
      }
    }

    .cal-month-card {
      background: var(--bg-card);
      border: 1px solid var(--border-color);
      border-radius: 12px;
      padding: 14px;
      box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
    }
    .cal-month-title {
      text-align: center;
      font-size: 1.05rem;
      font-weight: 700;
      color: #f8fafc;
      margin-bottom: 12px;
      padding-bottom: 8px;
      border-bottom: 1px solid #334155;
    }
    .cal-table {
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
    }
    .cal-table th {
      font-size: 0.72rem;
      font-weight: 700;
      text-align: center;
      padding: 4px 0;
      color: #94a3b8;
    }
    .cal-table td {
      position: relative;
      height: 44px;
      width: 14.28%;
      border: 1px solid #334155;
      padding: 2px;
      vertical-align: top;
      overflow: hidden;
      cursor: pointer;
      user-select: none;
      transition: transform 0.12s ease, box-shadow 0.12s ease;
      background: #1e293b;
    }
    .cal-table td:hover {
      transform: scale(1.08);
      z-index: 10;
      box-shadow: 0 4px 14px rgba(0, 0, 0, 0.7);
      outline: 2px solid #38bdf8;
    }
    .cal-day-num {
      position: absolute;
      top: 2px;
      left: 3px;
      font-size: 0.72rem;
      font-weight: 700;
      z-index: 3;
      pointer-events: none;
      color: #cbd5e1;
    }
    .cal-day-weekend {
      color: #f87171 !important;
    }
    .cal-day-booked {
      color: #ffffff !important;
      text-shadow: 0 1px 2px rgba(0, 0, 0, 0.8);
    }
    .cal-other-month {
      opacity: 0.25;
      background: rgba(15, 23, 42, 0.6) !important;
      cursor: default !important;
    }
    .cal-other-month:hover {
      transform: none !important;
      box-shadow: none !important;
      outline: none !important;
    }
    .cal-tooltip {
      position: fixed;
      background: #0f172a;
      border: 1px solid #475569;
      border-radius: 8px;
      padding: 12px 16px;
      color: #f8fafc;
      font-size: 0.85rem;
      box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.7);
      z-index: 9999;
      pointer-events: none;
      display: none;
      max-width: 320px;
      line-height: 1.45;
    }

    /* ==========================================================================
       RESERVATION DETAILS MODAL DIALOG
       ========================================================================== */
    .res-modal-overlay {
      position: fixed;
      inset: 0;
      background: rgba(15, 23, 42, 0.85);
      display: none;
      align-items: center;
      justify-content: center;
      z-index: 10000;
      padding: 16px;
    }
    .res-modal-overlay.active {
      display: flex;
    }
    .res-modal-card {
      background: #1e293b;
      border: 1px solid #475569;
      border-radius: 16px;
      width: 100%;
      max-width: 680px;
      max-height: 90vh;
      display: flex;
      flex-direction: column;
      box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.7);
      overflow: hidden;
    }
    .res-modal-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 18px 24px;
      border-bottom: 1px solid #334155;
      background: #0f172a;
    }
    .res-modal-title-group {
      display: flex;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
    }
    .res-modal-title {
      font-size: 1.25rem;
      font-weight: 800;
      color: #f8fafc;
      margin: 0;
    }
    .res-modal-close {
      background: transparent;
      border: none;
      color: #94a3b8;
      font-size: 1.6rem;
      line-height: 1;
      cursor: pointer;
      padding: 4px 8px;
      border-radius: 6px;
      transition: color 0.15s ease, background 0.15s ease;
    }
    .res-modal-close:hover {
      color: #f8fafc;
      background: #334155;
    }
    .res-modal-body {
      padding: 24px;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: 20px;
    }
    .res-fin-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
      gap: 12px;
    }
    .res-fin-card {
      background: #0f172a;
      border: 1px solid #334155;
      border-radius: 10px;
      padding: 12px 14px;
      text-align: center;
    }
    .res-fin-label {
      font-size: 0.72rem;
      font-weight: 700;
      color: #94a3b8;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      margin-bottom: 4px;
    }
    .res-fin-value {
      font-size: 1.15rem;
      font-weight: 800;
      font-family: 'JetBrains Mono', monospace;
    }
    .res-details-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.85rem;
    }
    .res-details-table tr {
      border-bottom: 1px solid #334155;
    }
    .res-details-table tr:last-child {
      border-bottom: none;
    }
    .res-details-table td {
      padding: 8px 10px;
    }
    .res-details-table td:first-child {
      color: #94a3b8;
      font-weight: 600;
      width: 38%;
    }
    .res-details-table td:last-child {
      color: #f8fafc;
      font-weight: 500;
    }
    .res-tab-pill-btn {
      background: #334155;
      color: #cbd5e1;
      border: 1px solid #475569;
      border-radius: 20px;
      padding: 5px 14px;
      font-size: 0.82rem;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.15s ease;
    }
    .res-tab-pill-btn.active {
      background: #2563eb;
      border-color: #3b82f6;
      color: #ffffff;
    }

    /* ==========================================================================
       REVENUE TAB STYLES
       ========================================================================== */
    .rev-wrapper {
      padding: 4px 0 20px 0;
    }
    .rev-kpi-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }
    .rev-kpi-card {
      background: var(--bg-card);
      border: 1px solid var(--border-color);
      border-radius: 12px;
      padding: 18px 20px;
    }
    .rev-kpi-label {
      font-size: 0.78rem;
      font-weight: 700;
      color: var(--text-muted);
      text-transform: uppercase;
      letter-spacing: 0.05em;
      margin-bottom: 6px;
    }
    .rev-kpi-value {
      font-size: 1.8rem;
      font-weight: 800;
      color: #f8fafc;
      font-family: 'JetBrains Mono', monospace;
    }
    .rev-kpi-sub {
      font-size: 0.82rem;
      color: #94a3b8;
      margin-top: 4px;
    }
    .rev-chart-container {
      background: var(--bg-card);
      border: 1px solid var(--border-color);
      border-radius: 14px;
      padding: 24px;
      margin-bottom: 24px;
      position: relative;
    }
    .rev-chart-header {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      margin-bottom: 16px;
      flex-wrap: wrap;
      gap: 12px;
    }
    .rev-chart-wrapper {
      position: relative;
      height: 480px;
      width: 100%;
    }
    .rev-toggles {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 14px;
      align-items: center;
      justify-content: center;
    }
    .rev-toggle-btn {
      background: #1e293b;
      border: 1.5px solid #334155;
      border-radius: 20px;
      padding: 5px 14px;
      font-size: 0.82rem;
      font-weight: 600;
      color: #cbd5e1;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      transition: all 0.15s ease;
    }
    .rev-toggle-btn.active {
      border-color: currentColor;
    }
    .rev-toggle-btn:hover {
      opacity: 0.85;
    }
    .rev-swatch {
      width: 10px;
      height: 10px;
      border-radius: 50%;
    }
    .rev-table-card {
      background: var(--bg-card);
      border: 1px solid var(--border-color);
      border-radius: 12px;
      padding: 20px;
      margin-top: 24px;
    }

    /* ==========================================================================
       RESERVATIONS TAB STYLES
       ========================================================================== */
    .res-tab-wrapper {
      padding: 4px 0 20px 0;
    }
    .res-kpi-bar {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 16px;
      margin-bottom: 20px;
    }
    .res-kpi-card {
      background: var(--bg-card);
      border: 1px solid var(--border-color);
      border-radius: 12px;
      padding: 16px 18px;
    }
    .res-kpi-card-label {
      font-size: 0.74rem;
      font-weight: 700;
      color: #94a3b8;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      margin-bottom: 4px;
    }
    .res-kpi-card-val {
      font-size: 1.6rem;
      font-weight: 800;
      color: #f8fafc;
      font-family: 'JetBrains Mono', monospace;
    }
    .res-kpi-card-sub {
      font-size: 0.78rem;
      color: #94a3b8;
      margin-top: 4px;
    }

    /* Annual Weekend vs. Midweek Performance Card */
    .res-shift-card {
      background: var(--bg-card);
      border: 1px solid var(--border-color);
      border-radius: 12px;
      padding: 18px 20px;
      margin-bottom: 20px;
    }
    .res-shift-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 10px;
      margin-bottom: 12px;
    }
    .res-shift-title {
      font-size: 1.02rem;
      font-weight: 700;
      color: #f8fafc;
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .res-shift-badge {
      font-size: 0.75rem;
      font-weight: 700;
      padding: 3px 8px;
      border-radius: 6px;
      background: rgba(56, 189, 248, 0.15);
      color: #38bdf8;
      border: 1px solid rgba(56, 189, 248, 0.3);
    }
    .res-shift-narrative {
      font-size: 0.84rem;
      color: #cbd5e1;
      background: rgba(15, 23, 42, 0.6);
      border-left: 3px solid #38bdf8;
      padding: 10px 14px;
      border-radius: 4px;
      margin-bottom: 16px;
      line-height: 1.5;
    }
    .res-shift-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.84rem;
    }
    .res-shift-table th {
      padding: 8px 12px;
      background: #0f172a;
      color: #94a3b8;
      font-weight: 700;
      font-size: 0.72rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      border-bottom: 1px solid #334155;
      text-align: right;
    }
    .res-shift-table th:first-child {
      text-align: left;
    }
    .res-shift-table td {
      padding: 9px 12px;
      border-bottom: 1px solid rgba(51, 65, 85, 0.4);
      color: #cbd5e1;
      text-align: right;
    }
    .res-shift-table td:first-child {
      text-align: left;
      font-weight: 700;
      color: #f8fafc;
    }
    .res-shift-bar-bg {
      background: #334155;
      border-radius: 4px;
      height: 6px;
      overflow: hidden;
      display: flex;
      margin-top: 4px;
    }
    .res-shift-bar-mid {
      background: #38bdf8;
      height: 100%;
    }
    .res-shift-bar-wknd {
      background: #818cf8;
      height: 100%;
    }

    .res-filter-toolbar {
      background: var(--bg-card);
      border: 1px solid var(--border-color);
      border-radius: 12px;
      padding: 14px 18px;
      margin-bottom: 20px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 14px;
    }
    .res-filter-group {
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }
    .res-pill-btn {
      background: #1e293b;
      color: #94a3b8;
      border: 1px solid #334155;
      border-radius: 20px;
      padding: 5px 14px;
      font-size: 0.82rem;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.15s ease;
    }
    .res-pill-btn:hover {
      color: #f8fafc;
      border-color: #475569;
    }
    .res-pill-btn.active {
      background: #2563eb;
      border-color: #3b82f6;
      color: #ffffff;
      box-shadow: 0 2px 8px rgba(37, 99, 235, 0.4);
    }
    .res-select {
      background: #0f172a;
      color: #f8fafc;
      border: 1px solid #334155;
      border-radius: 6px;
      padding: 6px 12px;
      font-size: 0.82rem;
      font-weight: 600;
      outline: none;
      cursor: pointer;
    }
    .res-search-box {
      background: #0f172a;
      border: 1px solid #334155;
      border-radius: 6px;
      color: #f8fafc;
      padding: 6px 12px;
      font-size: 0.82rem;
      outline: none;
      min-width: 240px;
    }
    .res-search-box:focus {
      border-color: #38bdf8;
    }

    .res-table-card {
      background: var(--bg-card);
      border: 1px solid var(--border-color);
      border-radius: 12px;
      overflow: hidden;
    }
    .res-table-wrapper {
      overflow-x: auto;
      max-height: 720px;
    }
    .res-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.86rem;
      text-align: left;
    }
    .res-table thead {
      position: sticky;
      top: 0;
      background: #0f172a;
      z-index: 10;
    }
    .res-table th {
      padding: 12px 14px;
      font-weight: 700;
      color: #94a3b8;
      text-transform: uppercase;
      font-size: 0.74rem;
      letter-spacing: 0.05em;
      border-bottom: 2px solid #334155;
      cursor: pointer;
      user-select: none;
      white-space: nowrap;
    }
    .res-table th:hover {
      color: #f8fafc;
      background: rgba(255, 255, 255, 0.03);
    }
    .res-table th .sort-arrow {
      font-size: 0.75rem;
      margin-left: 4px;
      opacity: 0.5;
    }
    .res-table th.sorted .sort-arrow {
      opacity: 1;
      color: #38bdf8;
    }
    .res-table td {
      padding: 12px 14px;
      border-bottom: 1px solid rgba(51, 65, 85, 0.5);
      color: #cbd5e1;
      vertical-align: middle;
    }
    .res-row {
      cursor: pointer;
      transition: background 0.15s ease;
    }
    .res-row:hover {
      background: rgba(56, 189, 248, 0.08) !important;
    }
    .res-row:nth-child(even) {
      background: rgba(15, 23, 42, 0.35);
    }

    /* Badges */
    .badge-stay-weekend {
      background: rgba(99, 102, 241, 0.18);
      color: #818cf8;
      border: 1px solid rgba(99, 102, 241, 0.35);
      font-weight: 700;
      padding: 2px 8px;
      border-radius: 6px;
      font-size: 0.75rem;
      display: inline-block;
    }
    .badge-stay-midweek {
      background: rgba(20, 184, 166, 0.18);
      color: #2dd4bf;
      border: 1px solid rgba(20, 184, 166, 0.35);
      font-weight: 700;
      padding: 2px 8px;
      border-radius: 6px;
      font-size: 0.75rem;
      display: inline-block;
    }
    .badge-stay-mix {
      background: rgba(245, 158, 11, 0.18);
      color: #fbbf24;
      border: 1px solid rgba(245, 158, 11, 0.35);
      font-weight: 700;
      padding: 2px 8px;
      border-radius: 6px;
      font-size: 0.75rem;
      display: inline-block;
    }
    .tooltip-help {
      border-bottom: 1px dotted rgba(148, 163, 184, 0.7);
      cursor: help;
      text-decoration: none;
      transition: border-color 0.15s ease, color 0.15s ease;
    }
    .tooltip-help:hover {
      border-bottom-color: #38bdf8;
      color: #f8fafc;
    }
    """


def classify_stay_type(start_date_str: Optional[str], end_date_str: Optional[str]) -> str:
    """
    Classify stay into:
    - 'Weekend': Thu, Fri, Sat nights (weekday 3, 4, 5)
    - 'Midweek': Sun, Mon, Tue, Wed nights (weekday 6, 0, 1, 2)
    - 'Mix': Any stay spanning both weekend and midweek nights
    """
    if not start_date_str or not end_date_str:
        return "Midweek"
    try:
        start_dt = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        end_dt = datetime.strptime(end_date_str, "%Y-%m-%d").date()
    except ValueError:
        return "Midweek"

    has_weekend = False
    has_midweek = False

    cur = start_dt
    while cur < end_dt:
        wd = cur.weekday()  # Mon=0, Tue=1, Wed=2, Thu=3, Fri=4, Sat=5, Sun=6
        if wd in (3, 4, 5):  # Thu, Fri, Sat
            has_weekend = True
        else:  # Sun (6), Mon (0), Tue (1), Wed (2)
            has_midweek = True
        cur += timedelta(days=1)

    if has_weekend and has_midweek:
        return "Mix"
    elif has_weekend:
        return "Weekend"
    else:
        return "Midweek"


def estimate_reservation_channel_pricing(res: Dict[str, Any]) -> Dict[str, Any]:
    """
    Estimate 'Total on Channel' (pre-tax search total) and 'Guest Checkout Price' (all-in guest total).
    Includes exact generic formula definitions and substituted calculations for auditing and debugging.
    - Airbnb: (Gross Rent + $550 Clean) + 14.15% Airbnb service fee (Pre-tax search total); + 12.52% Tempe & AZ lodging taxes (Guest Checkout Price)
    - VRBO: Gross Rent * 1.1448 (14.48% markup) + $550 Clean; + 11.5% Vrbo fee + 14.07% taxes
    - Booking.com: (Gross Rent + $550 Clean) + 6% Service Charge (Pre-tax search total); + 30.5% taxes (16% VAT + 14.5% local tax) (Guest Checkout Price)
    - Expedia: Gross Rent * 1.15 + $550 Clean; + 14.07% taxes
    - Direct: Gross Rent + $500 Clean; 0% service fee + 14.4% Tempe STR tax
    - Admin: Gross Rent (internal rate)
    - Owner: $0 (maintenance/owner stay)
    """
    gross_rent = float(res.get("gross_rent") or 0.0)
    madetype = (res.get("madetype_name") or "").upper()

    raw_str = res.get("raw_json") or "{}"
    if isinstance(raw_str, str):
        try:
            raw = json.loads(raw_str)
        except Exception:
            raw = {}
    elif isinstance(raw_str, dict):
        raw = raw_str
    else:
        raw = {}

    hear_about = (raw.get("hear_about_name") or "").lower()
    travel_agent = (raw.get("travelagent_name") or "").lower()
    type_desc = (res.get("type_description") or "").lower()

    if "owner" in type_desc or madetype == "OWN":
        channel_name = "Owner Block"
        channel_badge = "OWN"
        channel_color = "#8E24AA"
        total_on_channel = 0.0
        guest_checkout_price = 0.0
        formula_notes = "Owner stay / block ($0 guest charges)"
        tot_channel_formula = "Owner Stay: $0.00 guest charges"
        tot_channel_calc = "$0.00 (Owner stay, no guest charges)"
        guest_price_formula = "Owner Stay: $0.00 guest charges"
        guest_price_calc = "$0.00 (Owner stay, no guest charges)"
    elif "maintenance" in type_desc:
        channel_name = "Maintenance Block"
        channel_badge = "MAINT"
        channel_color = "#D3761B"
        total_on_channel = 0.0
        guest_checkout_price = 0.0
        formula_notes = "Maintenance block ($0 guest charges)"
        tot_channel_formula = "Maintenance Block: $0.00 guest charges"
        tot_channel_calc = "$0.00 (Maintenance block, no guest charges)"
        guest_price_formula = "Maintenance Block: $0.00 guest charges"
        guest_price_calc = "$0.00 (Maintenance block, no guest charges)"
    elif "booking" in hear_about or "booking" in travel_agent:
        channel_name = "Booking.com"
        channel_badge = "Booking.com"
        channel_color = "#4F46E5"
        clean_fee = 550.0
        lodging_subtotal = round(gross_rent + clean_fee, 2)
        service_fee = round(lodging_subtotal * 0.06, 2)
        total_on_channel = round(lodging_subtotal + service_fee, 2)
        vat_amt = round(total_on_channel * 0.16, 2)
        tax_local = round(total_on_channel * 0.145, 2)
        tax_amt = round(vat_amt + tax_local, 2)
        guest_checkout_price = round(total_on_channel + tax_amt, 2)
        formula_notes = f"Gross Rent (${gross_rent:,.2f}) + $550 Clean + 6% service fee (${service_fee:,.2f}) = ${total_on_channel:,.2f}; + 30.5% taxes (16% VAT ${vat_amt:,.2f} + 14.5% tax ${tax_local:,.2f}) = ${guest_checkout_price:,.2f}"
        tot_channel_formula = "(Gross Rent + $550 Cleaning Fee) + 6% Booking.com Service Charge"
        tot_channel_calc = f"Rent (${gross_rent:,.2f}) + Clean ($550.00) = Lodging (${lodging_subtotal:,.2f}) + Service Charge 6% (${service_fee:,.2f}) = ${total_on_channel:,.2f}"
        guest_price_formula = "Total on Channel + 30.5% Taxes (16% VAT + 14.5% Local Tax)"
        guest_price_calc = f"Total on Channel (${total_on_channel:,.2f}) + 16% VAT (${vat_amt:,.2f}) + 14.5% Tax (${tax_local:,.2f}) = ${guest_checkout_price:,.2f}"
    elif "expedia" in hear_about or "expedia" in travel_agent:
        channel_name = "Expedia"
        channel_badge = "Expedia"
        channel_color = "#D97706"
        clean_fee = 550.0
        channel_base = round(gross_rent * 1.15, 2)
        total_on_channel = round(channel_base + clean_fee, 2)
        guest_checkout_price = round(total_on_channel * 1.1407, 2)
        tax_amt = round(total_on_channel * 0.1407, 2)
        formula_notes = f"Gross Rent (${gross_rent:,.2f}) × 1.15 + $550 Clean; + 14.07% tax"
        tot_channel_formula = "(Gross Rent × 1.15 markup) + Cleaning Fee ($550.00)"
        tot_channel_calc = f"(${gross_rent:,.2f} × 1.15 = ${channel_base:,.2f}) + Cleaning Fee ($550.00) = ${total_on_channel:,.2f}"
        guest_price_formula = "Total on Channel × 1.1407 (+14.07% STR tax)"
        guest_price_calc = f"Total on Channel (${total_on_channel:,.2f}) + Tax 14.07% (${tax_amt:,.2f}) = ${guest_checkout_price:,.2f}"
    elif "airbnb" in hear_about or "airbnb" in travel_agent or madetype == "WSR":
        channel_name = "Airbnb"
        channel_badge = "Airbnb"
        channel_color = "#FF5A5F"
        clean_fee = 550.0
        lodging_subtotal = round(gross_rent + clean_fee, 2)
        service_fee = round(lodging_subtotal * 0.1415, 2)
        total_on_channel = round(lodging_subtotal + service_fee, 2)
        tax_amt = round(total_on_channel * 0.1252, 2)
        guest_checkout_price = round(total_on_channel + tax_amt, 2)
        formula_notes = f"Gross Rent (${gross_rent:,.2f}) + $550 Clean + 14.15% Airbnb fee (${service_fee:,.2f}) = ${total_on_channel:,.2f}; + 12.52% tax (${tax_amt:,.2f}) = ${guest_checkout_price:,.2f}"
        tot_channel_formula = "(Gross Rent + $550 Cleaning Fee) + 14.15% Airbnb Service Fee"
        tot_channel_calc = f"Rent (${gross_rent:,.2f}) + Clean ($550.00) = Lodging (${lodging_subtotal:,.2f}) + Airbnb Fee 14.15% (${service_fee:,.2f}) = ${total_on_channel:,.2f}"
        guest_price_formula = "Total on Channel + 12.52% Tempe & AZ Lodging Taxes"
        guest_price_calc = f"Total on Channel (${total_on_channel:,.2f}) + Lodging Tax 12.52% (${tax_amt:,.2f}) = ${guest_checkout_price:,.2f}"
    elif "vrbo" in hear_about or "ha-olb" in hear_about or "vrbo" in travel_agent or "homeaway" in travel_agent or madetype == "PDWTA":
        channel_name = "Vrbo"
        channel_badge = "VRBO"
        channel_color = "#2563EB"
        clean_fee = 550.0
        vrbo_base = round(gross_rent * 1.1448, 2)
        total_on_channel = round(vrbo_base + clean_fee, 2)
        guest_checkout_price = round(total_on_channel * 1.2557, 2)
        fee_amt = round(total_on_channel * 0.115, 2)
        tax_amt = round(total_on_channel * 0.1407, 2)
        formula_notes = f"Gross Rent (${gross_rent:,.2f}) × 1.1448 + $550 Clean; + 11.5% Vrbo fee + 14.07% tax"
        tot_channel_formula = "(Gross Rent × 1.1448 markup) + Cleaning Fee ($550.00)"
        tot_channel_calc = f"(${gross_rent:,.2f} × 1.1448 = ${vrbo_base:,.2f}) + Cleaning Fee ($550.00) = ${total_on_channel:,.2f}"
        guest_price_formula = "Total on Channel × 1.2557 (+11.5% Vrbo traveler fee + 14.07% STR tax)"
        guest_price_calc = f"Total on Channel (${total_on_channel:,.2f}) + Vrbo Fee 11.5% (${fee_amt:,.2f}) + Tax 14.07% (${tax_amt:,.2f}) = ${guest_checkout_price:,.2f}"
    elif madetype == "NET" or "kivoya.com" in hear_about:
        channel_name = "Direct Website"
        channel_badge = "Direct"
        channel_color = "#10B981"
        clean_fee = 550.0
        proc_fee = round(gross_rent * 0.06, 2)
        admin_fee = round((gross_rent + proc_fee + clean_fee) * 0.03, 2)
        service_fee = round(proc_fee + admin_fee, 2)
        total_on_channel = round(gross_rent + clean_fee + service_fee, 2)
        tax_base = gross_rent + clean_fee + proc_fee
        tax_amt = round(
            round(tax_base * 0.055, 2)
            + round(tax_base * 0.0177, 2)
            + round(tax_base * 0.018, 2)
            + round(tax_base * 0.05, 2),
            2,
        )
        guest_checkout_price = round(total_on_channel + tax_amt, 2)
        formula_notes = f"Gross Rent (${gross_rent:,.2f}) + $550 Clean + 6% Proc (${proc_fee:,.2f}) + 3% Admin (${admin_fee:,.2f}); + 14.07% STR taxes (${tax_amt:,.2f})"
        tot_channel_formula = "Gross Rent + Cleaning Fee ($550.00) + 6% Processing Fee + 3% Admin Fee"
        tot_channel_calc = f"Rent (${gross_rent:,.2f}) + Clean ($550.00) + 6% Proc (${proc_fee:,.2f}) + 3% Admin (${admin_fee:,.2f}) = ${total_on_channel:,.2f}"
        guest_price_formula = "Total on Channel + 14.07% STR Taxes (5.5% State + 1.77% Maricopa + 6.8% Tempe)"
        guest_price_calc = f"Total on Channel (${total_on_channel:,.2f}) + STR Taxes 14.07% (${tax_amt:,.2f}) = ${guest_checkout_price:,.2f}"
    elif madetype == "ADM":
        channel_name = "Kivoya Admin"
        channel_badge = "Admin"
        channel_color = "#F59E0B"
        total_on_channel = gross_rent
        guest_checkout_price = gross_rent
        formula_notes = f"Internal admin entry / custom rate (${gross_rent:,.2f})"
        tot_channel_formula = "Gross Rent (Internal admin rate / booking hold)"
        tot_channel_calc = f"Gross Rent = ${gross_rent:,.2f}"
        guest_price_formula = "Gross Rent (Internal admin rate, no traveler fee or tax added)"
        guest_price_calc = f"Gross Rent = ${gross_rent:,.2f}"
    else:
        channel_name = res.get("madetype_name") or "Direct"
        channel_badge = channel_name
        channel_color = "#10B981"
        clean_fee = 550.0
        proc_fee = round(gross_rent * 0.06, 2)
        admin_fee = round((gross_rent + proc_fee + clean_fee) * 0.03, 2)
        service_fee = round(proc_fee + admin_fee, 2)
        total_on_channel = round(gross_rent + clean_fee + service_fee, 2)
        tax_base = gross_rent + clean_fee + proc_fee
        tax_amt = round(
            round(tax_base * 0.055, 2)
            + round(tax_base * 0.0177, 2)
            + round(tax_base * 0.018, 2)
            + round(tax_base * 0.05, 2),
            2,
        )
        guest_checkout_price = round(total_on_channel + tax_amt, 2)
        formula_notes = f"Gross Rent (${gross_rent:,.2f}) + $550 Clean + 6% Proc (${proc_fee:,.2f}) + 3% Admin (${admin_fee:,.2f}); + 14.07% STR taxes (${tax_amt:,.2f})"
        tot_channel_formula = "Gross Rent + Cleaning Fee ($550.00) + 6% Processing Fee + 3% Admin Fee"
        tot_channel_calc = f"Rent (${gross_rent:,.2f}) + Clean ($550.00) + 6% Proc (${proc_fee:,.2f}) + 3% Admin (${admin_fee:,.2f}) = ${total_on_channel:,.2f}"
        guest_price_formula = "Total on Channel + 14.07% STR Taxes (5.5% State + 1.77% Maricopa + 6.8% Tempe)"
        guest_price_calc = f"Total on Channel (${total_on_channel:,.2f}) + STR Taxes 14.07% (${tax_amt:,.2f}) = ${guest_checkout_price:,.2f}"

    return {
        "channel_name": channel_name,
        "channel_badge": channel_badge,
        "channel_color": channel_color,
        "total_on_channel": total_on_channel,
        "guest_checkout_price": guest_checkout_price,
        "formula_notes": formula_notes,
        "tot_channel_formula": tot_channel_formula,
        "tot_channel_calc": tot_channel_calc,
        "guest_price_formula": guest_price_formula,
        "guest_price_calc": guest_price_calc,
    }


def render_reservation_modal() -> str:
    """Render master interactive Reservation Details Modal Dialog at page level."""
    return """
    <!-- Master Interactive Reservation Details Modal Dialog -->
    <div id="resModalOverlay" class="res-modal-overlay" onclick="closeResModal(event)">
      <div class="res-modal-card" onclick="event.stopPropagation()">
        <div class="res-modal-header">
          <div class="res-modal-title-group" id="resModalHeaderInfo">
            <span id="resModalBadge" class="badge">Booked</span>
            <h3 id="resModalTitle" class="res-modal-title">Reservation Details</h3>
          </div>
          <button class="res-modal-close" onclick="closeResModal()" title="Close">&times;</button>
        </div>
        <div class="res-modal-body" id="resModalBody">
          <!-- Dynamically populated on reservation click -->
        </div>
      </div>
    </div>
    """


def render_calendar_tab(reservations: List[Dict[str, Any]], current_date: Optional[date] = None) -> str:
    """Render Availability Calendar tab matching Streamline OwnerX."""
    if current_date is None:
        current_date = date.today()

    return f"""
      <div class="cal-wrapper">
        <div class="cal-controls-card">
          <div class="cal-nav-group">
            <h2 style="font-size: 1.3rem; font-weight: 800; color: #f8fafc; margin-right: 8px;">Calendar</h2>
            <span style="font-size: 0.85rem; color: #94a3b8; font-weight: 600; margin-right: 12px;">Villa del Sol (Unit #503802)</span>
            <button class="cal-btn" onclick="calNavigate(-6)">◀ Prev 6 Months</button>
            <select id="calMonthSelect" class="cal-select" onchange="calJumpToMonth(this.value)">
              <!-- Options injected by JS -->
            </select>
            <button class="cal-btn" onclick="calNavigate(6)">Next 6 Months ▶</button>
            <button class="cal-btn" onclick="calJumpToToday()">Today</button>
          </div>

          <!-- Color Mode Dual Toggle Switch -->
          <div class="cal-toggle-group">
            <span class="cal-toggle-label">Color By:</span>
            <div class="cal-toggle-pill">
              <button id="calColorStatusBtn" class="cal-toggle-btn active" onclick="setCalColorMode('status')">Status</button>
              <button id="calColorChannelBtn" class="cal-toggle-btn" onclick="setCalColorMode('channel')">Channel</button>
            </div>
          </div>

          <!-- Legend: Status Mode -->
          <div id="calLegendStatus" class="cal-legend">
            <div class="cal-legend-item">
              <span class="cal-legend-swatch swatch-vacant"></span>
              <span>Vacant</span>
            </div>
            <div class="cal-legend-item">
              <span class="cal-legend-swatch swatch-standard"></span>
              <span>Standard (Guest Booking)</span>
            </div>
            <div class="cal-legend-item">
              <span class="cal-legend-swatch swatch-owner"></span>
              <span>Owner Block</span>
            </div>
            <div class="cal-legend-item">
              <span class="cal-legend-swatch swatch-maintenance"></span>
              <span>Maintenance Block</span>
            </div>
          </div>

          <!-- Legend: Channel Mode -->
          <div id="calLegendChannel" class="cal-legend" style="display: none;">
            <div class="cal-legend-item">
              <span class="cal-legend-swatch swatch-vacant"></span>
              <span>Vacant</span>
            </div>
            <div class="cal-legend-item">
              <span class="cal-legend-swatch swatch-airbnb"></span>
              <span>Airbnb</span>
            </div>
            <div class="cal-legend-item">
              <span class="cal-legend-swatch swatch-vrbo"></span>
              <span>Vrbo / HomeAway</span>
            </div>
            <div class="cal-legend-item">
              <span class="cal-legend-swatch swatch-direct"></span>
              <span>Direct Website</span>
            </div>
            <div class="cal-legend-item">
              <span class="cal-legend-swatch swatch-booking"></span>
              <span>Booking.com</span>
            </div>
            <div class="cal-legend-item">
              <span class="cal-legend-swatch swatch-expedia"></span>
              <span>Expedia</span>
            </div>
            <div class="cal-legend-item">
              <span class="cal-legend-swatch swatch-admin"></span>
              <span>Kivoya Admin</span>
            </div>
            <div class="cal-legend-item">
              <span class="cal-legend-swatch swatch-owner"></span>
              <span>Owner Block</span>
            </div>
          </div>
        </div>

        <div id="calMonthsGrid" class="cal-months-grid">
          <!-- 6 Month Grid Cards rendered via JS -->
        </div>

        <div id="calTooltip" class="cal-tooltip"></div>
      </div>
    """


def render_reservations_tab(reservations: List[Dict[str, Any]], today: Optional[date] = None) -> str:
    """Render Reservations tab with KPI summary bar and interactive sortable/filterable table."""
    if today is None:
        today = date.today()

    valid_res = [r for r in reservations if str(r.get("status_name", "")).lower() != "cancelled"]
    valid_res.sort(key=lambda x: str(x.get("start_date") or ""), reverse=True)

    total_count = len(valid_res)
    future_count = sum(1 for r in valid_res if r.get("is_future") == 1)
    past_count = total_count - future_count

    total_gross = sum(float(r.get("gross_rent") or 0.0) for r in valid_res)
    total_owner = sum(float(r.get("owner_payout") or 0.0) for r in valid_res)
    total_mgmt = sum(float(r.get("management_fee") or 0.0) for r in valid_res)
    total_nights = sum(int(r.get("days_number") or 0) for r in valid_res)
    avg_adr = total_gross / max(1, total_nights)

    future_gross = sum(float(r.get("gross_rent") or 0.0) for r in valid_res if r.get("is_future") == 1)
    future_nights = sum(int(r.get("days_number") or 0) for r in valid_res if r.get("is_future") == 1)

    rows_html = []
    for r in valid_res:
        res_id = r.get("id")
        cid = r.get("confirmation_id") or res_id
        start_str = r.get("start_date") or ""
        end_str = r.get("end_date") or ""
        nights = int(r.get("days_number") or 0)
        gross_rent = float(r.get("gross_rent") or 0.0)
        owner_payout = float(r.get("owner_payout") or 0.0)
        is_future = int(r.get("is_future") or 0)

        raw = {}
        if r.get("raw_json"):
            try:
                raw = json.loads(r["raw_json"]) if isinstance(r["raw_json"], str) else r["raw_json"]
            except Exception:
                pass

        stay_type = classify_stay_type(start_str, end_str)
        pricing = estimate_reservation_channel_pricing(r)
        ch_name = pricing["channel_name"]
        ch_color = pricing["channel_color"]
        tot_channel = pricing["total_on_channel"]
        gst_price = pricing["guest_checkout_price"]
        notes = pricing["formula_notes"]
        tot_formula = pricing["tot_channel_formula"]
        tot_calc = pricing["tot_channel_calc"]
        gst_formula = pricing["guest_price_formula"]
        gst_calc = pricing["guest_price_calc"]

        tot_tooltip = html.escape(f"Formula: {tot_formula}\nCalculation: {tot_calc}", quote=True)
        gst_tooltip = html.escape(f"Formula: {gst_formula}\nCalculation: {gst_calc}", quote=True)

        cross_code = raw.get("cross_reference_code") or ""
        guest_name = raw.get("guest_name") or f"{raw.get('first_name', '')} {raw.get('last_name', '')}".strip()

        try:
            s_dt = datetime.strptime(start_str, "%Y-%m-%d").date()
            e_dt = datetime.strptime(end_str, "%Y-%m-%d").date()
            if s_dt.year == e_dt.year:
                date_fmt = f"{s_dt.strftime('%b %d')} – {e_dt.strftime('%b %d, %Y')}"
            else:
                date_fmt = f"{s_dt.strftime('%b %d, %Y')} – {e_dt.strftime('%b %d, %Y')}"
        except Exception:
            date_fmt = f"{start_str} to {end_str}"

        status_badge = '<span class="badge" style="background:rgba(56,189,248,0.2); color:#38bdf8; font-size:0.7rem; padding:1px 6px;">Future</span>' if is_future else '<span class="badge" style="background:rgba(148,163,184,0.15); color:#94a3b8; font-size:0.7rem; padding:1px 6px;">Past</span>'

        cross_html = f'<div style="font-size:0.75rem; color:#38bdf8; font-family:\'JetBrains Mono\', monospace; margin-top:2px;">#{cross_code}</div>' if cross_code else ''
        guest_html = f' • <span style="color:#cbd5e1;">{guest_name}</span>' if guest_name else ''

        search_str = f"{cid} {cross_code} {guest_name} {start_str} {end_str} {ch_name} {stay_type} {r.get('type_description', '')}".lower()

        rows_html.append(f"""
          <tr class="res-row" onclick="openResModalById({res_id})" 
              data-id="{res_id}" 
              data-cid="{cid}" 
              data-future="{is_future}" 
              data-channel="{ch_name}" 
              data-type="{stay_type}" 
              data-start="{start_str}"
              data-nights="{nights}"
              data-gross="{gross_rent:.2f}"
              data-totchannel="{tot_channel:.2f}"
              data-guestprice="{gst_price:.2f}"
              data-search="{search_str}">
            <td>
              <div style="font-weight:700; color:#f8fafc; font-size:0.9rem;">{date_fmt}</div>
              <div style="font-size:0.75rem; color:#94a3b8; margin-top:3px;">
                {status_badge} • Conf <strong>#{cid}</strong>{guest_html}
              </div>
            </td>
            <td>
              <span class="badge-stay-{stay_type.lower()}">{stay_type}</span>
            </td>
            <td>
              <strong style="color:#f8fafc; font-family:'JetBrains Mono'; font-size:0.92rem;">{nights}</strong>
              <span style="color:#94a3b8; font-size:0.75rem;"> nts</span>
            </td>
            <td>
              <div style="font-weight:800; color:#34d399; font-family:'JetBrains Mono'; font-size:0.95rem;">${gross_rent:,.2f}</div>
              <div style="font-size:0.72rem; color:#94a3b8; margin-top:2px;">
                Owner: <span style="color:#cbd5e1; font-weight:600;">${owner_payout:,.2f}</span> (82%)
              </div>
            </td>
            <td>
              <span class="badge" style="background:{ch_color}22; color:{ch_color}; border:1px solid {ch_color}55; font-weight:700;">● {ch_name}</span>
              {cross_html}
            </td>
            <td>
              <div style="font-weight:700; color:#f8fafc; font-family:'JetBrains Mono'; font-size:0.92rem;">
                <span class="tooltip-help" title="{tot_tooltip}">${tot_channel:,.2f}</span>
              </div>
              <div style="font-size:0.72rem; color:#94a3b8; margin-top:2px;">
                <span class="tooltip-help" title="{tot_tooltip}">Pre-tax est. ⓘ</span>
              </div>
            </td>
            <td>
              <div style="font-weight:800; color:#38bdf8; font-family:'JetBrains Mono'; font-size:0.95rem;">
                <span class="tooltip-help" title="{gst_tooltip}">${gst_price:,.2f}</span>
              </div>
              <div style="font-size:0.72rem; color:#94a3b8; margin-top:2px;">
                <span class="tooltip-help" title="{gst_tooltip}">All-in guest est. ⓘ</span>
              </div>
            </td>
          </tr>
        """)

    table_body = "\n".join(rows_html)

    # Calculate historical shifts & lead times
    try:
        from src.reservation_intelligence import ReservationIntelligence
        res_intel = ReservationIntelligence()
        shift_data = res_intel.compute_weekend_midweek_annual_shift()
        lead_analytics = res_intel.compute_lead_time_windows()
    except Exception:
        shift_data = {"years": [], "strategic_narrative": ""}
        lead_analytics = {"overall": {}, "seasons": {}, "total_analyzed": 0}

    overall_lead = lead_analytics.get("overall", {})
    seasons_lead = lead_analytics.get("seasons", {})
    winter_lead = seasons_lead.get("Peak Winter / Spring (Feb–Apr)", {})
    summer_lead = seasons_lead.get("Summer Value Season (Jun–Aug)", {})
    fall_lead = seasons_lead.get("Fall / Shoulder Season (Sep–Jan, May)", {})

    shift_rows_html = []
    for yr_item in shift_data.get("years", []):
        yr = yr_item["year"]
        tot_n = yr_item["total_nights"]
        wknd_n = yr_item["weekend_nights"]
        mid_n = yr_item["midweek_nights"]
        wknd_pct = yr_item["weekend_pct"]
        mid_pct = yr_item["midweek_pct"]
        wknd_adr = yr_item["weekend_adr"]
        mid_adr = yr_item["midweek_adr"]
        spread = wknd_adr - mid_adr
        spread_sign = "+" if spread > 0 else ""
        spread_str = f"{spread_sign}${spread:,.0f}" if (wknd_adr > 0 and mid_adr > 0) else "—"

        shift_rows_html.append(f"""
          <tr>
            <td><strong>{yr}</strong></td>
            <td><strong>{tot_n:,}</strong> nts</td>
            <td>{wknd_n} nts <span style="color:#94a3b8; font-size:0.75rem;">({wknd_pct:.1f}%)</span></td>
            <td>{mid_n} nts <span style="color:#38bdf8; font-weight:600; font-size:0.75rem;">({mid_pct:.1f}%)</span></td>
            <td style="min-width:140px;">
              <div style="font-size:0.75rem; display:flex; justify-content:space-between; margin-bottom:2px;">
                <span style="color:#818cf8;">Wknd {wknd_pct:.0f}%</span>
                <span style="color:#38bdf8;">Mid {mid_pct:.0f}%</span>
              </div>
              <div class="res-shift-bar-bg">
                <div class="res-shift-bar-wknd" style="width:{wknd_pct}%;"></div>
                <div class="res-shift-bar-mid" style="width:{mid_pct}%;"></div>
              </div>
            </td>
            <td style="font-family:'JetBrains Mono',monospace; color:#818cf8; font-weight:700;">${wknd_adr:,.0f}</td>
            <td style="font-family:'JetBrains Mono',monospace; color:#38bdf8; font-weight:700;">${mid_adr:,.0f}</td>
            <td style="font-family:'JetBrains Mono',monospace; color:#cbd5e1;">{spread_str}</td>
          </tr>
        """)
    shift_table_body = "\n".join(shift_rows_html)

    return f"""
      <div class="res-tab-wrapper">
        <!-- KPI Summary Cards -->
        <div class="res-kpi-bar">
          <div class="res-kpi-card">
            <div class="res-kpi-card-label">Total Reservations</div>
            <div class="res-kpi-card-val">{total_count}</div>
            <div class="res-kpi-card-sub">{past_count} Completed • {future_count} Upcoming</div>
          </div>

          <div class="res-kpi-card">
            <div class="res-kpi-card-label">Future Pipeline (OTB)</div>
            <div class="res-kpi-card-val" style="color:#38bdf8;">${future_gross:,.2f}</div>
            <div class="res-kpi-card-sub">{future_count} bookings • {future_nights} upcoming nights</div>
          </div>

          <div class="res-kpi-card">
            <div class="res-kpi-card-label">Total Gross Revenue</div>
            <div class="res-kpi-card-val" style="color:#34d399;">${total_gross:,.2f}</div>
            <div class="res-kpi-card-sub">Owner (82%): ${total_owner:,.2f} • Kivoya: ${total_mgmt:,.2f}</div>
          </div>

          <div class="res-kpi-card">
            <div class="res-kpi-card-label">Total Nights Booked</div>
            <div class="res-kpi-card-val">{total_nights:,}</div>
            <div class="res-kpi-card-sub">Across 2022–2027 calendar nights</div>
          </div>

          <div class="res-kpi-card">
            <div class="res-kpi-card-label">Overall Gross ADR</div>
            <div class="res-kpi-card-val" style="color:#fbbf24;">${avg_adr:,.2f}</div>
            <div class="res-kpi-card-sub">Average gross rate per booked night</div>
          </div>
        </div>

        <!-- Annual Shift & Advance Booking Windows Intelligence Section -->
        <div class="res-shift-card">
          <div class="res-shift-header">
            <div class="res-shift-title">
              <span>📈 Annual Weekend vs. Midweek Performance & Strategy Shift</span>
              <span class="res-shift-badge">2022–2027 Realized Analytics</span>
            </div>
            <div style="display:flex; gap:8px; align-items:center;">
              <span class="badge" style="background:rgba(52,211,153,0.15); color:#34d399; border:1px solid rgba(52,211,153,0.3); font-size:0.75rem; font-weight:600;">
                Overall Advance Booking Window: {overall_lead.get('p25', 10)}–{overall_lead.get('p75', 131)}d (Median {overall_lead.get('median', 50)}d)
              </span>
            </div>
          </div>

          <div class="res-shift-narrative">
            <div style="margin-bottom:6px;"><strong>💡 Strategic Finding:</strong> {shift_data.get('strategic_narrative', '')}</div>
            <div style="font-size:0.8rem; color:#94a3b8; border-top:1px solid rgba(255,255,255,0.08); padding-top:6px; margin-top:6px;">
              <strong style="color:#cbd5e1;">Advance Booking Windows by Season:</strong> 
              Winter/Spring (Feb–Apr): <span style="color:#38bdf8; font-weight:600;">{winter_lead.get('window_str', '20–147 days out')} (med {winter_lead.get('median', 79)}d)</span> &bull; 
              Summer (Jun–Aug): <span style="color:#f87171; font-weight:600;">{summer_lead.get('window_str', '6–28 days out')} (med {summer_lead.get('median', 20)}d)</span> &bull; 
              Fall/Shoulder (Sep–Jan, May): <span style="color:#fbbf24; font-weight:600;">{fall_lead.get('window_str', '12–134 days out')} (med {fall_lead.get('median', 50)}d)</span>
            </div>
          </div>

          <div style="overflow-x:auto;">
            <table class="res-shift-table">
              <thead>
                <tr>
                  <th>Year</th>
                  <th>Total Nights</th>
                  <th>Weekend Nights (Thu–Sat)</th>
                  <th>Midweek Nights (Sun–Wed)</th>
                  <th style="text-align:center;">Demand Distribution</th>
                  <th>Realized Weekend ADR</th>
                  <th>Realized Midweek ADR</th>
                  <th>ADR Premium</th>
                </tr>
              </thead>
              <tbody>
                {shift_table_body}
              </tbody>
            </table>
          </div>
        </div>

        <!-- Filter & Search Toolbar -->
        <div class="res-filter-toolbar">
          <div class="res-filter-group">
            <button id="resFilterAll" class="res-pill-btn active" onclick="setResFilterStatus('all')">All ({total_count})</button>
            <button id="resFilterFuture" class="res-pill-btn" onclick="setResFilterStatus('future')">Future / In-House ({future_count})</button>
            <button id="resFilterPast" class="res-pill-btn" onclick="setResFilterStatus('past')">Past ({past_count})</button>
          </div>

          <div class="res-filter-group">
            <select id="resFilterChannel" class="res-select" onchange="applyReservationFilters()">
              <option value="all">All Channels</option>
              <option value="Airbnb">Airbnb</option>
              <option value="Vrbo">Vrbo / HomeAway</option>
              <option value="Direct Website">Direct Website (kivoya.com)</option>
              <option value="Booking.com">Booking.com</option>
              <option value="Expedia">Expedia</option>
              <option value="Kivoya Admin">Kivoya Admin</option>
              <option value="Owner Block">Owner Block</option>
            </select>

            <select id="resFilterType" class="res-select" onchange="applyReservationFilters()">
              <option value="all">All Stay Types</option>
              <option value="Weekend">Weekend (Thu–Sat)</option>
              <option value="Midweek">Midweek (Sun–Wed)</option>
              <option value="Mix">Mix (Weekday + Weekend)</option>
            </select>

            <input type="text" id="resSearchInput" class="res-search-box" placeholder="🔍 Search conf #, code, guest, dates..." oninput="applyReservationFilters()">

            <button class="res-pill-btn" onclick="resetReservationFilters()" title="Reset all filters">Clear</button>
          </div>

          <div id="resFilterCount" style="font-size:0.82rem; color:#94a3b8; font-weight:600;">
            Showing {total_count} of {total_count} reservations
          </div>
        </div>

        <!-- Interactive Reservations Table -->
        <div class="res-table-card">
          <div class="res-table-wrapper">
            <table class="res-table" id="resTable">
              <thead>
                <tr>
                  <th onclick="sortResTable(0, 'date')" id="resTh0" class="sorted">Dates <span class="sort-arrow">▼</span></th>
                  <th onclick="sortResTable(1, 'str')" id="resTh1">Type <span class="sort-arrow">↕</span></th>
                  <th onclick="sortResTable(2, 'num')" id="resTh2">Nights <span class="sort-arrow">↕</span></th>
                  <th onclick="sortResTable(3, 'num')" id="resTh3">Gross Rent <span class="sort-arrow">↕</span></th>
                  <th onclick="sortResTable(4, 'str')" id="resTh4">Channel <span class="sort-arrow">↕</span></th>
                  <th onclick="sortResTable(5, 'num')" id="resTh5">
                    <span class="tooltip-help" title="Formula for Total on Channel (Pre-tax Search Total):&#10;• Airbnb: (Gross Rent + $550 Cleaning) + 14.15% Airbnb Service Fee&#10;• Vrbo: (Gross Rent × 1.1448 markup) + $550 Cleaning Fee&#10;• Booking.com: (Gross Rent + $550 Cleaning) + 6% Service Charge&#10;• Expedia: (Gross Rent × 1.15 markup) + $550 Cleaning Fee&#10;• Direct Website: Gross Rent + $550 Cleaning + 6% Processing + 3% Admin Fee&#10;• Kivoya Admin: Gross Rent (internal rate)&#10;• Owner / Maintenance: $0.00">Total on Channel (Est.) ⓘ</span> <span class="sort-arrow">↕</span>
                  </th>
                  <th onclick="sortResTable(6, 'num')" id="resTh6">
                    <span class="tooltip-help" title="Formula for Guest Checkout Price (All-in Guest Total):&#10;• Airbnb: Total on Channel + 12.52% Tempe & AZ Lodging Taxes&#10;• Vrbo: Total on Channel × 1.2557 (+11.5% Vrbo fee + 14.07% STR tax)&#10;• Booking.com: Total on Channel + 30.5% Taxes (16% VAT + 14.5% Tax)&#10;• Expedia: Total on Channel × 1.1407 (+14.07% STR tax)&#10;• Direct Website: Total on Channel + 14.07% STR Taxes (5.5% State + 1.77% Maricopa + 6.8% Tempe)&#10;• Kivoya Admin: Gross Rent (internal rate)&#10;• Owner / Maintenance: $0.00">Guest Checkout Price (Est.) ⓘ</span> <span class="sort-arrow">↕</span>
                  </th>
                </tr>
              </thead>
              <tbody id="resTableBody">
                {table_body}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    """


def render_revenue_tab(rev_data: Dict[str, Any]) -> str:
    """Render Cumulative Revenue tab with KPI cards and Annual Pace chart."""
    kpis = rev_data.get("kpis", {})
    cy = kpis.get("current_year", 2026)
    py = cy - 1

    by_year = kpis.get("by_year", {})
    cy_stats = by_year.get(cy, {})

    ytd_rev = kpis.get("ytd_revenue", 0.0)
    prior_ytd_rev = kpis.get("prior_ytd_revenue", 0.0)
    growth = kpis.get("ytd_growth_pct", 0.0)
    growth_sign = "+" if growth > 0 else ""
    growth_color = "#10b981" if growth >= 0 else "#ef4444"

    tot_booked = cy_stats.get("total_revenue", 0.0)
    future_pipeline = max(0.0, tot_booked - ytd_rev)
    tot_nights = cy_stats.get("total_nights", 0)
    adr = cy_stats.get("adr", 0.0)

    ny = cy + 1
    ny_stats = by_year.get(ny, {})
    ny_booked = ny_stats.get("total_revenue", 0.0)
    ny_nights = ny_stats.get("total_nights", 0)

    # Build annual performance table rows
    table_rows = []
    for y in sorted(by_year.keys()):
        y_info = by_year[y]
        t_rev = y_info.get("total_revenue", 0.0)
        t_nts = y_info.get("total_nights", 0)
        t_adr = y_info.get("adr", 0.0)
        
        # Completed vs Future for current year
        if y == cy:
            comp_str = f"${ytd_rev:,.2f}"
            fut_str = f"${future_pipeline:,.2f}"
        elif y < cy:
            comp_str = f"${t_rev:,.2f}"
            fut_str = "$0.00"
        else:
            comp_str = "$0.00"
            fut_str = f"${t_rev:,.2f}"

        status_badge = ""
        if y == cy:
            status_badge = f'<span class="badge" style="background:rgba(251,140,0,0.2); color:#fb8c00; border:1px solid rgba(251,140,0,0.4); margin-left:6px;">In Progress</span>'
        elif y > cy:
            status_badge = f'<span class="badge" style="background:rgba(0,172,193,0.2); color:#00acc1; border:1px solid rgba(0,172,193,0.4); margin-left:6px;">Advance Pipeline</span>'
        else:
            status_badge = f'<span class="badge" style="background:rgba(148,163,184,0.15); color:#94a3b8; margin-left:6px;">Finalized</span>'

        table_rows.append(f"""
          <tr>
            <td><strong style="color:#f8fafc; font-size:0.95rem;">{y}</strong> {status_badge}</td>
            <td style="color:#34d399; font-weight:600;">{comp_str}</td>
            <td style="color:#38bdf8; font-weight:600;">{fut_str}</td>
            <td><strong style="color:#f8fafc; font-size:1rem;">${t_rev:,.2f}</strong></td>
            <td><span style="font-weight:600;">{t_nts}</span> nights</td>
            <td><strong style="color:#fbbf24;">${t_adr:,.2f}</strong>/nt</td>
          </tr>
        """)

    table_rows_html = "\n".join(table_rows)

    return f"""
      <div class="rev-wrapper">
        <!-- KPI Cards -->
        <div class="rev-kpi-grid">
          <div class="rev-kpi-card">
            <div class="rev-kpi-label">{cy} YTD Owner Revenue</div>
            <div class="rev-kpi-value" style="color: #34d399;">${ytd_rev:,.2f}</div>
            <div class="rev-kpi-sub">
              Completed stays through today • <span style="color:{growth_color}; font-weight:700;">{growth_sign}{growth}% YoY</span> vs {py} YTD (${prior_ytd_rev:,.2f})
            </div>
          </div>

          <div class="rev-kpi-card">
            <div class="rev-kpi-label">{cy} Total On-the-Books</div>
            <div class="rev-kpi-value" style="color: #fb8c00;">${tot_booked:,.2f}</div>
            <div class="rev-kpi-sub">
              Actuals (${ytd_rev:,.0f}) + Future Bookings (${future_pipeline:,.0f})
            </div>
          </div>

          <div class="rev-kpi-card">
            <div class="rev-kpi-label">{cy} Booked Nights</div>
            <div class="rev-kpi-value">{tot_nights} <span style="font-size: 1.1rem; font-weight: 500; color: #94a3b8;">nights</span></div>
            <div class="rev-kpi-sub">
              {round((tot_nights / 365) * 100, 1)}% booked calendar occupancy
            </div>
          </div>

          <div class="rev-kpi-card">
            <div class="rev-kpi-label">Average Net Owner Rate (ADR)</div>
            <div class="rev-kpi-value" style="color: #fbbf24;">${adr:,.2f}</div>
            <div class="rev-kpi-sub">
              Owner payout (82% net) per night booked
            </div>
          </div>

          <div class="rev-kpi-card">
            <div class="rev-kpi-label">{ny} Advance Pipeline</div>
            <div class="rev-kpi-value" style="color: #38bdf8;">${ny_booked:,.2f}</div>
            <div class="rev-kpi-sub">
              {ny_nights} advance nights already secured
            </div>
          </div>
        </div>

        <!-- Cumulative Annual Pace Chart -->
        <div class="rev-chart-container">
          <div class="rev-chart-header">
            <div>
              <h2 style="font-size: 1.3rem; font-weight: 800; color: #f8fafc; margin-bottom: 4px;">Income and Sum — Cumulative Annual Owner Net Revenue</h2>
              <p style="color: var(--text-muted); font-size: 0.9rem;">
                Accumulated owner portion of rent (82% net payout) starting at $0 on January 1st and rising through December 31st.
                Solid line indicates completed stays; dashed indicates upcoming booked revenue.
              </p>
            </div>
          </div>

          <div class="rev-chart-wrapper">
            <canvas id="revenueChart"></canvas>
          </div>

          <!-- Quick Toggle Buttons -->
          <div class="rev-toggles" id="revToggles">
            <!-- Injected by JS -->
          </div>
        </div>

        <!-- Annual Performance Table -->
        <div class="rev-table-card">
          <h3 style="font-size: 1.1rem; font-weight: 700; color: #f8fafc; margin-bottom: 14px;">Historical Annual Performance Comparison (2022 – {ny})</h3>
          <div class="table-responsive">
            <table>
              <thead>
                <tr>
                  <th>Calendar Year</th>
                  <th>Completed Net Payout</th>
                  <th>Future Pipeline</th>
                  <th>Total On-the-Books</th>
                  <th>Total Nights Booked</th>
                  <th>Owner Net ADR</th>
                </tr>
              </thead>
              <tbody>
                {table_rows_html}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    """


def get_calendar_revenue_js(reservations: List[Dict[str, Any]], rev_data: Dict[str, Any], today: Optional[date] = None) -> str:
    """Generate JavaScript for interactive calendar grid, reservation modal dialog, and Chart.js cumulative curve."""
    if today is None:
        today = date.today()

    today_str = today.strftime("%Y-%m-%d")
    clean_res = []
    for r in reservations:
        if str(r.get("status_name", "")).lower() == "cancelled":
            continue

        raw_streamline = {}
        if r.get("raw_json"):
            try:
                raw_streamline = json.loads(r["raw_json"])
            except Exception:
                pass

        stay_type = classify_stay_type(r.get("start_date"), r.get("end_date"))
        pricing = estimate_reservation_channel_pricing(r)

        item = {
            "id": r.get("id"),
            "confirmation_id": r.get("confirmation_id"),
            "reservation_hash": raw_streamline.get("reservation_hash", ""),
            "creation_date": r.get("creation_date") or raw_streamline.get("creation_date", ""),
            "start_date": r.get("start_date"),
            "end_date": r.get("end_date"),
            "days_number": r.get("days_number"),
            "type_id": r.get("type_id"),
            "type_name": r.get("type_name", "STA"),
            "type_description": r.get("type_description", "Standard"),
            "status_name": r.get("status_name", "Booked"),
            "madetype_name": raw_streamline.get("madetype_name", ""),
            "occupants": r.get("occupants", 0),
            "occupants_small": r.get("occupants_small", 0),
            "pets": r.get("pets", 0),
            "unit_id": r.get("unit_id"),
            "unit_name": r.get("unit_name", "Villa del Sol"),
            "owner_payout": r.get("owner_payout", 0.0),
            "management_fee": r.get("management_fee", 0.0),
            "gross_rent": r.get("gross_rent", 0.0),
            "is_future": r.get("is_future", 0),
            "stay_type": stay_type,
            "channel_name": pricing["channel_name"],
            "channel_badge": pricing["channel_badge"],
            "channel_color": pricing["channel_color"],
            "total_on_channel": pricing["total_on_channel"],
            "guest_checkout_price": pricing["guest_checkout_price"],
            "formula_notes": pricing["formula_notes"],
            "tot_channel_formula": pricing["tot_channel_formula"],
            "tot_channel_calc": pricing["tot_channel_calc"],
            "guest_price_formula": pricing["guest_price_formula"],
            "guest_price_calc": pricing["guest_price_calc"],
            "commission_information": raw_streamline.get("commission_information", {}),
            "raw_streamline": raw_streamline,
        }
        clean_res.append(item)

    res_json = json.dumps(clean_res)
    rev_json = json.dumps(rev_data)

    return f"""
    // =========================================================================
    // CALENDAR & REVENUE DATA OBJECTS
    // =========================================================================
    const CAL_RESERVATIONS = {res_json};
    const REVENUE_DATA = {rev_json};
    const CURRENT_TODAY_STR = '{today_str}';

    // Map date strings 'YYYY-MM-DD' to reservation events
    const CAL_DATE_MAP = {{}};
    (function buildDateMap() {{
      CAL_RESERVATIONS.forEach(r => {{
        if (!r.start_date || !r.end_date) return;
        
        // Checkin date
        if (!CAL_DATE_MAP[r.start_date]) CAL_DATE_MAP[r.start_date] = {{ checkin: [], checkout: [], staying: [] }};
        CAL_DATE_MAP[r.start_date].checkin.push(r);

        // Checkout date
        if (!CAL_DATE_MAP[r.end_date]) CAL_DATE_MAP[r.end_date] = {{ checkin: [], checkout: [], staying: [] }};
        CAL_DATE_MAP[r.end_date].checkout.push(r);

        // Staying nights (between start_date and end_date - 1 day)
        let s = new Date(r.start_date + 'T00:00:00');
        let e = new Date(r.end_date + 'T00:00:00');
        s.setDate(s.getDate() + 1);
        while (s < e) {{
          let dStr = s.toISOString().split('T')[0];
          if (!CAL_DATE_MAP[dStr]) CAL_DATE_MAP[dStr] = {{ checkin: [], checkout: [], staying: [] }};
          CAL_DATE_MAP[dStr].staying.push(r);
          s.setDate(s.getDate() + 1);
        }}
      }});
    }})();

    // =========================================================================
    // AVAILABILITY CALENDAR LOGIC (6-Month Rolling Grid)
    // =========================================================================
    let calCurrentYear = {today.year};
    let calCurrentMonth = {today.month}; // 1-indexed (1..12)

    const MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
    const DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

    let calColorMode = 'status'; // 'status' or 'channel'

    function setCalColorMode(mode) {{
      calColorMode = mode;
      const statusBtn = document.getElementById('calColorStatusBtn');
      const channelBtn = document.getElementById('calColorChannelBtn');
      const legendStatus = document.getElementById('calLegendStatus');
      const legendChannel = document.getElementById('calLegendChannel');
      if (statusBtn && channelBtn) {{
        if (mode === 'channel') {{
          statusBtn.classList.remove('active');
          channelBtn.classList.add('active');
          if (legendStatus) legendStatus.style.display = 'none';
          if (legendChannel) legendChannel.style.display = 'flex';
        }} else {{
          channelBtn.classList.remove('active');
          statusBtn.classList.add('active');
          if (legendStatus) legendStatus.style.display = 'flex';
          if (legendChannel) legendChannel.style.display = 'none';
        }}
      }}
      renderCalendar(calCurrentYear, calCurrentMonth);
    }}

    function getTypeColor(typeDesc) {{
      const t = (typeDesc || '').toLowerCase();
      if (t.includes('owner')) return '#8E24AA'; // Purple
      if (t.includes('maintenance')) return '#D3761B'; // Amber
      return '#8CB811'; // Lime Green Standard
    }}

    function getEventColor(res) {{
      if (!res) return '#8CB811';
      if (calColorMode === 'channel') {{
        return res.channel_color || '#FF5A5F';
      }}
      return getTypeColor(res.type_description);
    }}

    function initCalendarDropdown() {{
      const sel = document.getElementById('calMonthSelect');
      if (!sel) return;
      sel.innerHTML = '';
      
      // Populate 2022 to 2027
      for (let y = 2022; y <= 2027; y++) {{
        for (let m = 1; m <= 12; m++) {{
          const opt = document.createElement('option');
          opt.value = y + '-' + (m < 10 ? '0' + m : m);
          opt.textContent = MONTH_NAMES[m - 1] + ' ' + y;
          if (y === calCurrentYear && m === calCurrentMonth) {{
            opt.selected = true;
          }}
          sel.appendChild(opt);
        }}
      }}
    }}

    function renderCalendar(startYear, startMonth) {{
      calCurrentYear = startYear;
      calCurrentMonth = startMonth;

      const container = document.getElementById('calMonthsGrid');
      if (!container) return;
      container.innerHTML = '';

      // Sync dropdown
      const sel = document.getElementById('calMonthSelect');
      if (sel) {{
        const targetVal = calCurrentYear + '-' + (calCurrentMonth < 10 ? '0' + calCurrentMonth : calCurrentMonth);
        sel.value = targetVal;
      }}

      // Render 6 consecutive months
      let y = startYear;
      let m = startMonth;

      for (let i = 0; i < 6; i++) {{
        const card = document.createElement('div');
        card.className = 'cal-month-card';

        const title = document.createElement('div');
        title.className = 'cal-month-header';
        title.textContent = MONTH_NAMES[m - 1] + ' ' + y;
        card.appendChild(title);

        const table = document.createElement('table');
        table.className = 'cal-table';

        // Weekday header
        const thead = document.createElement('thead');
        const trH = document.createElement('tr');
        DAY_NAMES.forEach((d, idx) => {{
          const th = document.createElement('th');
          th.textContent = d;
          if (idx === 0 || idx === 6) th.style.color = '#f87171';
          trH.appendChild(th);
        }});
        thead.appendChild(trH);
        table.appendChild(thead);

        // Month body
        const tbody = document.createElement('tbody');
        const firstDay = new Date(y, m - 1, 1).getDay(); // 0=Sun..6=Sat
        const daysInMonth = new Date(y, m, 0).getDate();
        const prevMonthDays = new Date(y, m - 1, 0).getDate();

        let dayCounter = 1;
        let nextMonthDay = 1;

        for (let week = 0; week < 6; week++) {{
          const tr = document.createElement('tr');
          let hasCurrentMonthDays = false;

          for (let dow = 0; dow < 7; dow++) {{
            const td = document.createElement('td');
            const dayIndex = week * 7 + dow;

            if (dayIndex < firstDay) {{
              // Prev month trailing days
              const pNum = prevMonthDays - (firstDay - dayIndex - 1);
              td.className = 'cal-other-month';
              td.innerHTML = `<span class="cal-day-num">${{pNum}}</span>`;
            }} else if (dayCounter <= daysInMonth) {{
              hasCurrentMonthDays = true;
              const dateStr = y + '-' + (m < 10 ? '0' + m : m) + '-' + (dayCounter < 10 ? '0' + dayCounter : dayCounter);
              const events = CAL_DATE_MAP[dateStr] || {{ checkin: [], checkout: [], staying: [] }};
              
              const isWeekend = (dow === 0 || dow === 6);
              const dayNumClass = isWeekend ? 'cal-day-num cal-day-weekend' : 'cal-day-num';
              
              td.dataset.date = dateStr;

              // Check booking patterns for diagonal split
              const hasIn = events.checkin.length > 0;
              const hasOut = events.checkout.length > 0;
              const hasMid = events.staying.length > 0;

              if (hasOut && hasIn) {{
                // Turnover day: morning guest leaves, afternoon guest arrives!
                const colorOut = getEventColor(events.checkout[0]);
                const colorIn = getEventColor(events.checkin[0]);
                td.style.background = `linear-gradient(135deg, ${{colorOut}} calc(50% - 1px), #ffffff calc(50% - 1px), #ffffff calc(50% + 1px), ${{colorIn}} calc(50% + 1px))`;
                td.innerHTML = `<span class="${{dayNumClass}} cal-day-booked">${{dayCounter}}</span>`;
                td.dataset.info = JSON.stringify({{ date: dateStr, out: events.checkout[0], in: events.checkin[0] }});
              }} else if (hasIn) {{
                // Arrival day: vacant morning, guest afternoon
                const colorIn = getEventColor(events.checkin[0]);
                td.style.background = `linear-gradient(135deg, #1e293b 50%, ${{colorIn}} 50%)`;
                td.innerHTML = `<span class="${{dayNumClass}} cal-day-booked">${{dayCounter}}</span>`;
                td.dataset.info = JSON.stringify({{ date: dateStr, in: events.checkin[0] }});
              }} else if (hasOut) {{
                // Departure day: guest morning, vacant afternoon
                const colorOut = getEventColor(events.checkout[0]);
                td.style.background = `linear-gradient(135deg, ${{colorOut}} 50%, #1e293b 50%)`;
                td.innerHTML = `<span class="${{dayNumClass}} cal-day-booked">${{dayCounter}}</span>`;
                td.dataset.info = JSON.stringify({{ date: dateStr, out: events.checkout[0] }});
              }} else if (hasMid) {{
                // Full stayed night
                const colorMid = getEventColor(events.staying[0]);
                td.style.backgroundColor = colorMid;
                td.innerHTML = `<span class="${{dayNumClass}} cal-day-booked">${{dayCounter}}</span>`;
                td.dataset.info = JSON.stringify({{ date: dateStr, stay: events.staying[0] }});
              }} else {{
                // Vacant day
                td.innerHTML = `<span class="${{dayNumClass}}">${{dayCounter}}</span>`;
                td.dataset.info = JSON.stringify({{ date: dateStr, vacant: true }});
              }}

              // Hover tooltip handlers
              td.onmouseenter = showCalTooltip;
              td.onmouseleave = hideCalTooltip;

              // Click handler to open detailed modal dialog
              td.onclick = function() {{
                openResModalFromDate(dateStr);
              }};

              dayCounter++;
            }} else {{
              // Next month leading days
              td.className = 'cal-other-month';
              td.innerHTML = `<span class="cal-day-num">${{nextMonthDay}}</span>`;
              nextMonthDay++;
            }}

            tr.appendChild(td);
          }}

          if (hasCurrentMonthDays || week < 5) {{
            tbody.appendChild(tr);
          }}
        }}

        table.appendChild(tbody);
        card.appendChild(table);
        container.appendChild(card);

        // Advance to next month
        m++;
        if (m > 12) {{
          m = 1;
          y++;
        }}
      }}
    }}

    function calNavigate(deltaMonths) {{
      let newMonth = calCurrentMonth + deltaMonths;
      let newYear = calCurrentYear;
      while (newMonth < 1) {{
        newMonth += 12;
        newYear -= 1;
      }}
      while (newMonth > 12) {{
        newMonth -= 12;
        newYear += 1;
      }}
      if (newYear < 2022) {{ newYear = 2022; newMonth = 1; }}
      if (newYear > 2027) {{ newYear = 2027; newMonth = 7; }}
      renderCalendar(newYear, newMonth);
    }}

    function calJumpToMonth(val) {{
      if (!val) return;
      const parts = val.split('-');
      const y = parseInt(parts[0], 10);
      const m = parseInt(parts[1], 10);
      renderCalendar(y, m);
    }}

    function calJumpToToday() {{
      const parts = CURRENT_TODAY_STR.split('-');
      renderCalendar(parseInt(parts[0], 10), parseInt(parts[1], 10));
    }}

    function showCalTooltip(e) {{
      const td = e.currentTarget;
      const infoStr = td.dataset.info;
      if (!infoStr) return;
      const info = JSON.parse(infoStr);
      const tip = document.getElementById('calTooltip');
      if (!tip) return;

      let html = `<div style="font-weight:700; color:#f8fafc; margin-bottom:6px; border-bottom:1px solid #334155; padding-bottom:4px;">📅 ${{info.date}} <span style="font-size:0.75rem; color:#38bdf8; float:right;">(Click to view)</span></div>`;

      if (info.vacant) {{
        html += `<div style="color:#94a3b8;">Status: <strong style="color:#38bdf8;">Vacant</strong> (Open for booking)</div>`;
      }} else {{
        if (info.out) {{
          const ch = info.out.channel_name ? `<span style="color:${{info.out.channel_color || '#38bdf8'}}; font-weight:700;">● ${{info.out.channel_name}}</span> • ` : '';
          html += `<div style="margin-bottom:6px;">
            <span style="color:#f87171; font-weight:700;">Departure ↗</span>: 
            <strong>#${{info.out.confirmation_id || info.out.id}}</strong> (${{info.out.type_description}})<br>
            <span style="color:#94a3b8; font-size:0.75rem;">${{ch}}Check-in: ${{info.out.start_date}} • ${{info.out.days_number}} nts</span>
          </div>`;
        }}
        if (info.in) {{
          const ch = info.in.channel_name ? `<span style="color:${{info.in.channel_color || '#38bdf8'}}; font-weight:700;">● ${{info.in.channel_name}}</span> • ` : '';
          html += `<div style="margin-bottom:6px;">
            <span style="color:#34d399; font-weight:700;">Arrival ↘</span>: 
            <strong>#${{info.in.confirmation_id || info.in.id}}</strong> (${{info.in.type_description}})<br>
            <span style="color:#94a3b8; font-size:0.75rem;">${{ch}}${{info.in.days_number}} nts • Out: ${{info.in.end_date}}</span><br>
            <span style="color:#fbbf24; font-weight:600; font-size:0.8rem;">Net Payout: $${{parseFloat(info.in.owner_payout || 0).toLocaleString('en-US', {{minimumFractionDigits: 2}})}}</span>
          </div>`;
        }}
        if (info.stay) {{
          const ch = info.stay.channel_name ? `<span style="color:${{info.stay.channel_color || '#38bdf8'}}; font-weight:700;">● ${{info.stay.channel_name}}</span> • ` : '';
          html += `<div>
            <span style="color:#38bdf8; font-weight:700;">In-House</span>: 
            <strong>#${{info.stay.confirmation_id || info.stay.id}}</strong> (${{info.stay.type_description}})<br>
            <span style="color:#94a3b8; font-size:0.75rem;">${{ch}}${{info.stay.start_date}} to ${{info.stay.end_date}} (${{info.stay.days_number}} nts)</span><br>
            <span style="color:#fbbf24; font-weight:600; font-size:0.8rem;">Net Payout: $${{parseFloat(info.stay.owner_payout || 0).toLocaleString('en-US', {{minimumFractionDigits: 2}})}}</span>
          </div>`;
        }}
      }}

      tip.innerHTML = html;
      tip.style.display = 'block';

      const rect = td.getBoundingClientRect();
      let left = rect.right + 12;
      let top = rect.top;
      if (left + 320 > window.innerWidth) {{
        left = rect.left - 330;
      }}
      if (top + 160 > window.innerHeight) {{
        top = window.innerHeight - 170;
      }}
      tip.style.left = Math.max(10, left) + 'px';
      tip.style.top = Math.max(10, top) + 'px';
    }}

    function hideCalTooltip() {{
      const tip = document.getElementById('calTooltip');
      if (tip) tip.style.display = 'none';
    }}

    // =========================================================================
    // RESERVATION DETAILS MODAL (All Streamline Fields)
    // =========================================================================
    function openResModalFromDate(dateStr) {{
      hideCalTooltip();
      const events = CAL_DATE_MAP[dateStr] || {{ checkin: [], checkout: [], staying: [] }};
      const allActive = [...events.checkout, ...events.checkin, ...events.staying];
      const uniqueResMap = new Map();
      allActive.forEach(r => uniqueResMap.set(r.id, r));
      const uniqueResList = Array.from(uniqueResMap.values());

      const modal = document.getElementById('resModalOverlay');
      if (!modal) return;

      if (uniqueResList.length === 0) {{
        // Vacant date
        renderVacantModal(dateStr);
        modal.classList.add('active');
        return;
      }}

      if (uniqueResList.length === 1) {{
        renderResModalContent(uniqueResList[0], dateStr);
      }} else {{
        // Turnover day with 2 reservations: render tabbed switcher
        renderMultiResModalContent(uniqueResList, dateStr, events);
      }}

      modal.classList.add('active');
    }}

    function openResModalById(resId) {{
      hideCalTooltip();
      const r = CAL_RESERVATIONS.find(x => x.id == resId || x.confirmation_id == resId);
      if (!r) return;
      const modal = document.getElementById('resModalOverlay');
      if (!modal) return;
      renderResModalContent(r, r.start_date || '');
      modal.classList.add('active');
    }}

    function renderVacantModal(dateStr) {{
      const header = document.getElementById('resModalHeaderInfo');
      const body = document.getElementById('resModalBody');
      if (header) {{
        header.innerHTML = `
          <span class="badge badge-dark">Open Date</span>
          <h3 class="res-modal-title">📅 ${{dateStr}}</h3>
        `;
      }}
      if (body) {{
        body.innerHTML = `
          <div style="text-align: center; padding: 30px 20px;">
            <div style="font-size: 2.5rem; margin-bottom: 12px;">🏖️</div>
            <h4 style="font-size: 1.15rem; color: #f8fafc; margin-bottom: 8px;">Vacant & Available</h4>
            <p style="color: #94a3b8; font-size: 0.9rem; max-width: 400px; margin: 0 auto;">
              No guest reservation, owner block, or maintenance block is active on ${{dateStr}}.
              Villa del Sol is open for distribution across Airbnb, VRBO, Booking.com, and Kivoya Direct.
            </p>
          </div>
        `;
      }}
    }}

    function renderMultiResModalContent(resList, dateStr, events) {{
      const header = document.getElementById('resModalHeaderInfo');
      const body = document.getElementById('resModalBody');

      if (header) {{
        header.innerHTML = `
          <span class="badge" style="background:rgba(56,189,248,0.2); color:#38bdf8; border:1px solid rgba(56,189,248,0.4);">Turnover Day</span>
          <h3 class="res-modal-title">📅 ${{dateStr}} • 2 Bookings</h3>
        `;
      }}

      if (body) {{
        let tabsHtml = `<div style="display:flex; gap:10px; margin-bottom:16px; border-bottom:1px solid #334155; padding-bottom:12px;">`;
        resList.forEach((r, idx) => {{
          const isOut = events.checkout.some(o => o.id === r.id);
          const prefix = isOut ? "Departure ↗" : "Arrival ↘";
          const activeClass = idx === 0 ? "active" : "";
          tabsHtml += `<button class="res-tab-pill-btn ${{activeClass}}" onclick="switchResModalSubTab(${{idx}}, this)">${{prefix}}: #${{r.confirmation_id || r.id}}</button>`;
        }});
        tabsHtml += `</div>`;

        let panelsHtml = `<div id="resModalPanelsContainer">`;
        resList.forEach((r, idx) => {{
          const displayStyle = idx === 0 ? "block" : "none";
          panelsHtml += `<div id="resPanel-${{idx}}" class="res-panel" style="display:${{displayStyle}};">${{buildReservationHtmlSnippet(r)}}</div>`;
        }});
        panelsHtml += `</div>`;

        body.innerHTML = tabsHtml + panelsHtml;
      }}
    }}

    function switchResModalSubTab(idx, btn) {{
      document.querySelectorAll('.res-tab-pill-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      document.querySelectorAll('.res-panel').forEach(p => p.style.display = 'none');
      const target = document.getElementById('resPanel-' + idx);
      if (target) target.style.display = 'block';
    }}

    function renderResModalContent(res, dateStr) {{
      const header = document.getElementById('resModalHeaderInfo');
      const body = document.getElementById('resModalBody');

      const isFuture = res.is_future === 1;
      const statusClass = res.status_name.toLowerCase() === 'booked' ? 'badge-primary' : 'badge-dark';
      const futureBadge = isFuture 
        ? `<span class="badge" style="background:rgba(251,140,0,0.2); color:#fb8c00; border:1px solid rgba(251,140,0,0.4);">Future Reservation</span>`
        : `<span class="badge" style="background:rgba(148,163,184,0.15); color:#94a3b8;">Completed Stay</span>`;

      if (header) {{
        header.innerHTML = `
          <span class="badge ${{statusClass}}">${{res.status_name}}</span>
          ${{futureBadge}}
          <h3 class="res-modal-title">Reservation #${{res.confirmation_id || res.id}}</h3>
          <span style="font-size:0.85rem; color:#94a3b8; font-weight:600;">${{res.type_description}} (${{res.type_name}})</span>
        `;
      }}

      if (body) {{
        body.innerHTML = buildReservationHtmlSnippet(res);
      }}
    }}

    function buildReservationHtmlSnippet(r) {{
      const raw = r.raw_streamline || {{}};
      const comm = r.commission_information || {{}};

      const ownerPayout = parseFloat(r.owner_payout || comm.owner_commission_amount || 0);
      const mgmtFee = parseFloat(r.management_fee || comm.management_commission_amount || 0);
      const grossRent = parseFloat(r.gross_rent || (ownerPayout + mgmtFee));
      const nights = parseInt(r.days_number || 1, 10);
      const adr = nights > 0 ? (ownerPayout / nights) : 0;

      const fUSD = (v) => '$' + parseFloat(v || 0).toLocaleString('en-US', {{ minimumFractionDigits: 2, maximumFractionDigits: 2 }});

      return `
        <!-- Financial Summary Grid -->
        <div class="res-fin-grid">
          <div class="res-fin-card">
            <div class="res-fin-label">Owner Net (82%)</div>
            <div class="res-fin-value" style="color: #34d399;">${{fUSD(ownerPayout)}}</div>
          </div>
          <div class="res-fin-card">
            <div class="res-fin-label">Kivoya Fee (18%)</div>
            <div class="res-fin-value" style="color: #fb8c00;">${{fUSD(mgmtFee)}}</div>
          </div>
          <div class="res-fin-card">
            <div class="res-fin-label">Gross Distributable</div>
            <div class="res-fin-value" style="color: #f8fafc;">${{fUSD(grossRent)}}</div>
          </div>
          <div class="res-fin-card">
            <div class="res-fin-label">Net / Night</div>
            <div class="res-fin-value" style="color: #fbbf24;">${{fUSD(adr)}}</div>
          </div>
          <div class="res-fin-card" title="Formula: ${{escapeHtml(r.tot_channel_formula || '')}}&#10;Calculation: ${{escapeHtml(r.tot_channel_calc || '')}}">
            <div class="res-fin-label"><span class="tooltip-help" title="Formula: ${{escapeHtml(r.tot_channel_formula || '')}}">Total on Channel (Est.) ⓘ</span></div>
            <div class="res-fin-value" style="color: #f8fafc;"><span class="tooltip-help" title="Formula: ${{escapeHtml(r.tot_channel_formula || '')}}&#10;Calculation: ${{escapeHtml(r.tot_channel_calc || '')}}">${{fUSD(r.total_on_channel || 0)}}</span></div>
          </div>
          <div class="res-fin-card" title="Formula: ${{escapeHtml(r.guest_price_formula || '')}}&#10;Calculation: ${{escapeHtml(r.guest_price_calc || '')}}">
            <div class="res-fin-label"><span class="tooltip-help" title="Formula: ${{escapeHtml(r.guest_price_formula || '')}}">Guest Checkout (Est.) ⓘ</span></div>
            <div class="res-fin-value" style="color: #38bdf8;"><span class="tooltip-help" title="Formula: ${{escapeHtml(r.guest_price_formula || '')}}&#10;Calculation: ${{escapeHtml(r.guest_price_calc || '')}}">${{fUSD(r.guest_checkout_price || 0)}}</span></div>
          </div>
        </div>

        <!-- Structured Streamline Fields Table -->
        <div style="background: #0f172a; border: 1px solid #334155; border-radius: 12px; padding: 14px 18px;">
          <h4 style="font-size: 0.95rem; font-weight: 700; color: #f8fafc; margin-bottom: 10px; border-bottom: 1px solid #1e293b; padding-bottom: 6px;">
            Streamline VRS Booking Details
          </h4>
          <table class="res-details-table">
            <tbody>
              <tr>
                <td>Confirmation ID</td>
                <td><strong style="color:#38bdf8;">#${{r.confirmation_id || r.id}}</strong></td>
              </tr>
              <tr>
                <td>Stay Interval</td>
                <td><strong>${{r.start_date}}</strong> &rarr; <strong>${{r.end_date}}</strong> (${{nights}} nights)</td>
              </tr>
              <tr>
                <td>Booking Created</td>
                <td>${{r.creation_date || 'N/A'}}</td>
              </tr>
              <tr>
                <td>Reservation Type</td>
                <td>${{r.type_description}} (Code: <code style="color:#cbd5e1;">${{r.type_name}}</code>, ID: ${{r.type_id}})</td>
              </tr>
              <tr>
                <td>Booking Channel</td>
                <td>
                  ${{(() => {{
                    const m = (r.madetype_name || '').toUpperCase();
                    const rawH = (raw.hear_about_name || '').toLowerCase();
                    const rawTA = (raw.travelagent_name || '').toLowerCase();
                    if (rawH.includes('airbnb') || rawTA.includes('airbnb')) {{
                      return '<strong style="color:#ff5a5f;">● Airbnb</strong> <span style="color:#94a3b8; font-size:0.75rem;">(Streamline Connect WSR)</span>';
                    }} else if (rawH.includes('vrbo') || rawH.includes('ha-olb') || rawTA.includes('vrbo') || rawTA.includes('homeaway')) {{
                      return '<strong style="color:#38bdf8;">● Vrbo / HomeAway</strong> <span style="color:#94a3b8; font-size:0.75rem;">(Partner Distribution PDWTA)</span>';
                    }} else if (rawH.includes('booking') || rawTA.includes('booking')) {{
                      return '<strong style="color:#003580; background:#e0e7ff; padding:1px 6px; border-radius:4px;">● Booking.com</strong> <span style="color:#94a3b8; font-size:0.75rem;">(Partner Distribution PDWTA)</span>';
                    }} else if (rawH.includes('expedia') || rawTA.includes('expedia')) {{
                      return '<strong style="color:#facc15;">● Expedia</strong> <span style="color:#94a3b8; font-size:0.75rem;">(Streamline Connect WSR)</span>';
                    }} else if (rawH.includes('home2go') || rawTA.includes('home2go')) {{
                      return '<strong style="color:#38bdf8;">● HomeToGo</strong> <span style="color:#94a3b8; font-size:0.75rem;">(Partner Distribution PDWTA)</span>';
                    }} else if (m === 'WSR') {{
                      return '<strong style="color:#ff5a5f;">● Airbnb</strong> <span style="color:#94a3b8; font-size:0.75rem;">(Streamline Connect WSR)</span>';
                    }} else if (m === 'PDWTA') {{
                      return '<strong style="color:#38bdf8;">● Vrbo / Partner OTA</strong> <span style="color:#94a3b8; font-size:0.75rem;">(Partner Distribution PDWTA)</span>';
                    }} else if (m === 'NET') {{
                      return '<strong style="color:#34d399;">● Direct Website</strong> <span style="color:#94a3b8; font-size:0.75rem;">(kivoya.com)</span>';
                    }} else if (m === 'ADM') {{
                      return '<strong style="color:#fbbf24;">● Kivoya Admin</strong> <span style="color:#94a3b8; font-size:0.75rem;">(Internal Manual Entry)</span>';
                    }} else if (m === 'OWN') {{
                      return '<strong style="color:#c084fc;">● Owner Block</strong> <span style="color:#94a3b8; font-size:0.75rem;">(Streamline OwnerX)</span>';
                    }}
                    return '<strong style="color:#f8fafc;">' + (r.madetype_name || 'Direct') + '</strong>';
                  }})()}}
                </td>
              </tr>
              ${{raw.cross_reference_code ? `
              <tr>
                <td>Channel Confirmation #</td>
                <td><code style="color:#38bdf8; font-weight:700; font-size:0.9rem;">${{raw.cross_reference_code}}</code> <span style="color:#94a3b8; font-size:0.75rem;">(Guest Channel Reference)</span></td>
              </tr>` : ''}}
              <tr>
                <td>Status / Made Type</td>
                <td><span class="badge badge-primary">${{r.status_name}}</span> • Streamline Code: <code>${{r.madetype_name || 'WSR'}}</code></td>
              </tr>
              <tr>
                <td>Occupancy</td>
                <td>${{r.occupants || 0}} adults, ${{r.occupants_small || 0}} children, ${{r.pets || 0}} pets</td>
              </tr>
              <tr>
                <td>Property / Unit</td>
                <td>${{r.unit_name || 'Villa del Sol'}} (Streamline Unit ID: <code>${{r.unit_id || 503802}}</code>)</td>
              </tr>
              <tr>
                <td>Streamline Internal ID</td>
                <td><code>${{r.id}}</code></td>
              </tr>
              <tr>
                <td>Reservation Hash</td>
                <td><code style="font-size:0.75rem; word-break:break-all;">${{r.reservation_hash || 'N/A'}}</code></td>
              </tr>
              <tr>
                <td>Stay Classification</td>
                <td><span class="badge-stay-${{(r.stay_type || 'midweek').toLowerCase()}}">${{r.stay_type || 'Midweek'}}</span> (Weekend = Thu–Sat nights)</td>
              </tr>
              <tr>
                <td><span class="tooltip-help" title="Formula: ${{escapeHtml(r.tot_channel_formula || '')}}">Total on Channel (Est.) ⓘ</span></td>
                <td>
                  <strong style="color:#f8fafc;" class="tooltip-help" title="Formula: ${{escapeHtml(r.tot_channel_formula || '')}}&#10;Calculation: ${{escapeHtml(r.tot_channel_calc || '')}}">${{fUSD(r.total_on_channel || 0)}}</strong>
                  <div style="font-size:0.78rem; color:#94a3b8; margin-top:3px; line-height:1.4;">
                    <span style="color:#cbd5e1; font-weight:600;">Formula:</span> ${{escapeHtml(r.tot_channel_formula || '')}}<br/>
                    <span style="color:#34d399; font-weight:600;">Calculation:</span> ${{escapeHtml(r.tot_channel_calc || '')}}
                  </div>
                </td>
              </tr>
              <tr>
                <td><span class="tooltip-help" title="Formula: ${{escapeHtml(r.guest_price_formula || '')}}">Guest Checkout Price (Est.) ⓘ</span></td>
                <td>
                  <strong style="color:#38bdf8;" class="tooltip-help" title="Formula: ${{escapeHtml(r.guest_price_formula || '')}}&#10;Calculation: ${{escapeHtml(r.guest_price_calc || '')}}">${{fUSD(r.guest_checkout_price || 0)}}</strong>
                  <div style="font-size:0.78rem; color:#94a3b8; margin-top:3px; line-height:1.4;">
                    <span style="color:#cbd5e1; font-weight:600;">Formula:</span> ${{escapeHtml(r.guest_price_formula || '')}}<br/>
                    <span style="color:#38bdf8; font-weight:600;">Calculation:</span> ${{escapeHtml(r.guest_price_calc || '')}}
                  </div>
                </td>
              </tr>
              <tr>
                <td>Timeline Classification</td>
                <td>${{r.is_future === 1 ? '<strong style="color:#fb8c00;">Future Booking</strong> (Subject to guest changes)' : '<strong style="color:#94a3b8;">Past Booking</strong> (Completed stay)'}}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- Raw Streamline API Response Viewer -->
        <details style="background: #0b1120; border: 1px solid #334155; border-radius: 10px; padding: 12px 16px;">
          <summary style="cursor: pointer; font-weight: 700; color: #38bdf8; font-size: 0.85rem; user-select: none;">
            🔍 View Complete Raw Streamline JSON Payload
          </summary>
          <pre style="margin-top: 10px; font-family: 'JetBrains Mono', monospace; font-size: 0.76rem; color: #93c5fd; overflow-x: auto; max-height: 280px; background: #070c16; padding: 12px; border-radius: 8px;">${{escapeHtml(JSON.stringify(raw, null, 2))}}</pre>
        </details>
      `;
    }}

    function escapeHtml(str) {{
      return (str || '')
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
    }}

    function closeResModal(e) {{
      if (e && e.target && e.target.closest('.res-modal-card') && !e.target.closest('.res-modal-close')) {{
        return;
      }}
      const modal = document.getElementById('resModalOverlay');
      if (modal) modal.classList.remove('active');
    }}

    window.addEventListener('keydown', (e) => {{
      if (e.key === 'Escape') {{
        closeResModal();
      }}
    }});

    // =========================================================================
    // RESERVATIONS TABLE LOGIC (Filtering & Multi-column Sorting)
    // =========================================================================
    let currentResStatusFilter = 'all'; // 'all', 'future', 'past'
    let resSortCol = 0;
    let resSortAsc = false; // default newest check-in first

    function setResFilterStatus(status) {{
      currentResStatusFilter = status;
      ['All', 'Future', 'Past'].forEach(s => {{
        const btn = document.getElementById('resFilter' + s);
        if (btn) btn.classList.toggle('active', s.toLowerCase() === status);
      }});
      applyReservationFilters();
    }}

    function applyReservationFilters() {{
      const channelFilter = document.getElementById('resFilterChannel')?.value || 'all';
      const typeFilter = document.getElementById('resFilterType')?.value || 'all';
      const query = (document.getElementById('resSearchInput')?.value || '').toLowerCase().trim();

      const rows = document.querySelectorAll('#resTableBody .res-row');
      let visibleCount = 0;

      rows.forEach(row => {{
        const isFuture = row.dataset.future === '1';
        const channel = row.dataset.channel || '';
        const stayType = row.dataset.type || '';
        const searchText = row.dataset.search || '';

        let matchStatus = true;
        if (currentResStatusFilter === 'future') matchStatus = isFuture;
        else if (currentResStatusFilter === 'past') matchStatus = !isFuture;

        let matchChannel = (channelFilter === 'all') || (channel === channelFilter);
        let matchType = (typeFilter === 'all') || (stayType === typeFilter);
        let matchQuery = !query || searchText.includes(query);

        if (matchStatus && matchChannel && matchType && matchQuery) {{
          row.style.display = '';
          visibleCount++;
        }} else {{
          row.style.display = 'none';
        }}
      }});

      const countEl = document.getElementById('resFilterCount');
      if (countEl) {{
        countEl.textContent = `Showing ${{visibleCount}} of ${{rows.length}} reservations`;
      }}
    }}

    function resetReservationFilters() {{
      currentResStatusFilter = 'all';
      ['All', 'Future', 'Past'].forEach(s => {{
        const btn = document.getElementById('resFilter' + s);
        if (btn) btn.classList.toggle('active', s === 'All');
      }});
      const chSel = document.getElementById('resFilterChannel');
      if (chSel) chSel.value = 'all';
      const tySel = document.getElementById('resFilterType');
      if (tySel) tySel.value = 'all';
      const sInput = document.getElementById('resSearchInput');
      if (sInput) sInput.value = '';
      applyReservationFilters();
    }}

    function sortResTable(colIdx, sortType) {{
      if (resSortCol === colIdx) {{
        resSortAsc = !resSortAsc;
      }} else {{
        resSortCol = colIdx;
        resSortAsc = (sortType === 'str');
      }}

      for (let i = 0; i <= 6; i++) {{
        const th = document.getElementById('resTh' + i);
        if (th) {{
          th.classList.toggle('sorted', i === colIdx);
          const arrow = th.querySelector('.sort-arrow');
          if (arrow) {{
            arrow.textContent = (i === colIdx) ? (resSortAsc ? '▲' : '▼') : '↕';
          }}
        }}
      }}

      const tbody = document.getElementById('resTableBody');
      if (!tbody) return;
      const rows = Array.from(tbody.querySelectorAll('.res-row'));

      rows.sort((a, b) => {{
        let valA, valB;
        if (colIdx === 0) {{
          valA = a.dataset.start || '';
          valB = b.dataset.start || '';
        }} else if (colIdx === 1) {{
          valA = a.dataset.type || '';
          valB = b.dataset.type || '';
        }} else if (colIdx === 2) {{
          valA = parseFloat(a.dataset.nights || 0);
          valB = parseFloat(b.dataset.nights || 0);
        }} else if (colIdx === 3) {{
          valA = parseFloat(a.dataset.gross || 0);
          valB = parseFloat(b.dataset.gross || 0);
        }} else if (colIdx === 4) {{
          valA = a.dataset.channel || '';
          valB = b.dataset.channel || '';
        }} else if (colIdx === 5) {{
          valA = parseFloat(a.dataset.totchannel || 0);
          valB = parseFloat(b.dataset.totchannel || 0);
        }} else if (colIdx === 6) {{
          valA = parseFloat(a.dataset.guestprice || 0);
          valB = parseFloat(b.dataset.guestprice || 0);
        }}

        if (typeof valA === 'number') {{
          return resSortAsc ? (valA - valB) : (valB - valA);
        }} else {{
          return resSortAsc ? valA.localeCompare(valB) : valB.localeCompare(valA);
        }}
      }});

      rows.forEach(r => tbody.appendChild(r));
    }}

    function initReservationsTable() {{
      applyReservationFilters();
    }}

    // =========================================================================
    // CUMULATIVE REVENUE CHART ENGINE (Chart.js)
    // =========================================================================
    let revenueChartInstance = null;

    function initRevenueChart() {{
      if (typeof Chart === 'undefined') {{
        console.warn('Chart.js not loaded, cannot render revenue chart.');
        return;
      }}
      if (revenueChartInstance) {{
        revenueChartInstance.resize();
        return;
      }}

      const canvas = document.getElementById('revenueChart');
      if (!canvas) return;

      const seriesMap = REVENUE_DATA.yearly_series || {{}};
      const years = Object.keys(seriesMap).map(y => parseInt(y, 10)).sort();

      // Common 365 daily labels (Jan 01 .. Dec 31)
      const dayLabels = [];
      const refYear = 2025; // Non-leap year for uniform 365 day axis
      for (let d = 0; d < 365; d++) {{
        const cur = new Date(refYear, 0, 1 + d);
        dayLabels.push(cur.toLocaleDateString('en-US', {{ month: 'short', day: 'numeric' }}));
      }}

      // Build datasets for each calendar year
      const datasets = [];
      const toggleContainer = document.getElementById('revToggles');
      if (toggleContainer) toggleContainer.innerHTML = '';

      years.forEach(year => {{
        const s = seriesMap[year];
        if (!s || !s.points) return;

        const cumValues = new Array(365).fill(null);
        s.points.forEach(pt => {{
          const idx = pt.day - 1;
          if (idx >= 0 && idx < 365) {{
            cumValues[idx] = pt.cumulative;
          }}
        }});

        // Fill forward gaps if any
        let lastV = 0;
        for (let i = 0; i < 365; i++) {{
          if (cumValues[i] !== null) {{
            lastV = cumValues[i];
          }} else {{
            cumValues[i] = lastV;
          }}
        }}

        // Style configuration
        let borderColor = s.color || '#38bdf8';
        let borderDash = s.dash || [];
        let pointRadius = 0;
        let pointHoverRadius = 5;

        if (year === {today.year}) {{
          pointRadius = 1.5;
        }}

        const ds = {{
          label: year.toString(),
          data: cumValues,
          borderColor: borderColor,
          borderWidth: year === {today.year} ? 3.5 : 2.5,
          borderDash: borderDash,
          pointRadius: pointRadius,
          pointHoverRadius: pointHoverRadius,
          pointBackgroundColor: borderColor,
          tension: 0.15,
          fill: false,
          segment: year === {today.year} ? {{
            borderDash: ctx => {{
              const pIdx = ctx.p0DataIndex;
              const todayIdx = Math.floor((new Date('{today_str}').getTime() - new Date('{today.year}-01-01').getTime()) / (1000 * 60 * 60 * 24));
              return pIdx >= todayIdx ? [6, 6] : undefined;
            }}
          }} : undefined
        }};
        datasets.push(ds);

        // Build toggle pill
        if (toggleContainer) {{
          const btn = document.createElement('button');
          btn.className = 'rev-toggle-btn active';
          btn.style.color = borderColor;
          btn.innerHTML = `<span class="rev-swatch" style="background:${{borderColor}}"></span> ${{year}} (${{Math.round(s.total_revenue / 1000)}}k)`;
          btn.onclick = function() {{
            const meta = revenueChartInstance.getDatasetMeta(datasets.indexOf(ds));
            meta.hidden = meta.hidden === null ? !revenueChartInstance.data.datasets[datasets.indexOf(ds)].hidden : null;
            btn.classList.toggle('active', !meta.hidden);
            revenueChartInstance.update();
          }};
          toggleContainer.appendChild(btn);
        }}
      }});

      const ctx = canvas.getContext('2d');
      revenueChartInstance = new Chart(ctx, {{
        type: 'line',
        data: {{
          labels: dayLabels,
          datasets: datasets
        }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          interaction: {{
            mode: 'index',
            intersect: false,
          }},
          plugins: {{
            legend: {{
              display: false
            }},
            tooltip: {{
              backgroundColor: '#0f172a',
              titleColor: '#f8fafc',
              bodyColor: '#cbd5e1',
              borderColor: '#334155',
              borderWidth: 1,
              padding: 12,
              callbacks: {{
                label: function(context) {{
                  const val = context.parsed.y || 0;
                  return ' ' + context.dataset.label + ': $' + val.toLocaleString('en-US', {{ minimumFractionDigits: 0, maximumFractionDigits: 0 }});
                }}
              }}
            }}
          }},
          scales: {{
            x: {{
              grid: {{
                color: 'rgba(51, 65, 85, 0.4)'
              }},
              ticks: {{
                color: '#94a3b8',
                maxTicksLimit: 12,
                callback: function(val, index) {{
                  const label = dayLabels[index] || '';
                  return label.includes(' 1') ? label.replace(' 1', '') : '';
                }}
              }}
            }},
            y: {{
              grid: {{
                color: 'rgba(51, 65, 85, 0.4)'
              }},
              ticks: {{
                color: '#94a3b8',
                callback: function(val) {{
                  return '$' + (val / 1000) + 'k';
                }}
              }}
            }}
          }}
        }}
      }});
    }}

    // Auto-init calendar and listeners
    window.addEventListener('DOMContentLoaded', () => {{
      initCalendarDropdown();
      renderCalendar(calCurrentYear, calCurrentMonth);
    }});
    """
