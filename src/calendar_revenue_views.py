"""
Calendar and Cumulative Revenue Views for STR Price Advisor Dashboard.
Renders:
1. Streamline OwnerX Availability Calendar (6-month grid, half-day diagonal splits, tooltips)
2. Cumulative Annual Owner Revenue Pace Curves (2022-2027, Chart.js, KPI cards, summary table)
"""

from datetime import date, datetime, timedelta
import json
from typing import Dict, List, Optional, Any


def get_calendar_revenue_css() -> str:
    """CSS styles for Availability Calendar and Revenue tabs."""
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
    """


def render_calendar_tab(reservations: List[Dict[str, Any]], current_date: Optional[date] = None) -> str:
    """Render Availability Calendar tab matching Streamline OwnerX."""
    if current_date is None:
        current_date = date.today()

    return f"""
      <div class="cal-wrapper">
        <div class="cal-controls-card">
          <div class="cal-nav-group">
            <h2 style="font-size: 1.3rem; font-weight: 800; color: #f8fafc; margin-right: 8px;">Availability Calendar</h2>
            <span style="font-size: 0.85rem; color: #94a3b8; font-weight: 600; margin-right: 12px;">Villa del Sol (Unit #503802)</span>
            <button class="cal-btn" onclick="calNavigate(-6)">◀ Prev 6 Months</button>
            <select id="calMonthSelect" class="cal-select" onchange="calJumpToMonth(this.value)">
              <!-- Options injected by JS -->
            </select>
            <button class="cal-btn" onclick="calNavigate(6)">Next 6 Months ▶</button>
            <button class="cal-btn" onclick="calJumpToToday()">Today</button>
          </div>

          <div class="cal-legend">
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
        </div>

        <div id="calMonthsGrid" class="cal-months-grid">
          <!-- 6 Month Grid Cards rendered via JS -->
        </div>

        <div id="calTooltip" class="cal-tooltip"></div>
      </div>
    """


def render_revenue_tab(rev_data: Dict[str, Any]) -> str:
    """Render Cumulative Revenue tab with KPI cards and Annual Pace chart."""
    kpis = rev_data.get("kpis", {})
    cy = kpis.get("current_year", 2026)
    py = cy - 1

    by_year = kpis.get("by_year", {})
    cy_stats = by_year.get(cy, {})
    py_stats = by_year.get(py, {})

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
        ytd_val = y_info.get("ytd_revenue", 0.0)
        
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
    """Generate JavaScript for interactive calendar grid navigation and Chart.js cumulative curve."""
    if today is None:
        today = date.today()

    today_str = today.strftime("%Y-%m-%d")
    clean_res = []
    for r in reservations:
        if str(r.get("status_name", "")).lower() == "cancelled":
            continue
        clean_res.append({
            "id": r.get("id"),
            "confirmation_id": r.get("confirmation_id"),
            "start_date": r.get("start_date"),
            "end_date": r.get("end_date"),
            "days_number": r.get("days_number"),
            "type_name": r.get("type_name", "STA"),
            "type_description": r.get("type_description", "Standard"),
            "status_name": r.get("status_name", "Booked"),
            "owner_payout": r.get("owner_payout", 0.0),
            "occupants": r.get("occupants", 0),
            "is_future": r.get("is_future", 0),
        })

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
    // dateMap[dateStr] = {{ checkin: [res], checkout: [res], staying: [res] }}
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

    function getTypeColor(typeDesc) {{
      const t = (typeDesc || '').toLowerCase();
      if (t.includes('owner')) return '#8E24AA'; // Purple
      if (t.includes('maintenance')) return '#D3761B'; // Amber
      return '#8CB811'; // Lime Green Standard
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
                const colorOut = getTypeColor(events.checkout[0].type_description);
                const colorIn = getTypeColor(events.checkin[0].type_description);
                td.style.background = `linear-gradient(135deg, ${{colorOut}} calc(50% - 1px), #ffffff calc(50% - 1px), #ffffff calc(50% + 1px), ${{colorIn}} calc(50% + 1px))`;
                td.innerHTML = `<span class="${{dayNumClass}} cal-day-booked">${{dayCounter}}</span>`;
                td.dataset.info = JSON.stringify({{ date: dateStr, out: events.checkout[0], in: events.checkin[0] }});
              }} else if (hasIn) {{
                // Arrival day: vacant morning, guest afternoon
                const colorIn = getTypeColor(events.checkin[0].type_description);
                td.style.background = `linear-gradient(135deg, #1e293b 50%, ${{colorIn}} 50%)`;
                td.innerHTML = `<span class="${{dayNumClass}} cal-day-booked">${{dayCounter}}</span>`;
                td.dataset.info = JSON.stringify({{ date: dateStr, in: events.checkin[0] }});
              }} else if (hasOut) {{
                // Departure day: guest morning, vacant afternoon
                const colorOut = getTypeColor(events.checkout[0].type_description);
                td.style.background = `linear-gradient(135deg, ${{colorOut}} 50%, #1e293b 50%)`;
                td.innerHTML = `<span class="${{dayNumClass}} cal-day-booked">${{dayCounter}}</span>`;
                td.dataset.info = JSON.stringify({{ date: dateStr, out: events.checkout[0] }});
              }} else if (hasMid) {{
                // Full stayed night
                const colorMid = getTypeColor(events.staying[0].type_description);
                td.style.backgroundColor = colorMid;
                td.innerHTML = `<span class="${{dayNumClass}} cal-day-booked">${{dayCounter}}</span>`;
                td.dataset.info = JSON.stringify({{ date: dateStr, stay: events.staying[0] }});
              }} else {{
                // Vacant day
                td.innerHTML = `<span class="${{dayNumClass}}">${{dayCounter}}</span>`;
                td.dataset.info = JSON.stringify({{ date: dateStr, vacant: true }});
              }}

              td.onmouseenter = showCalTooltip;
              td.onmouseleave = hideCalTooltip;
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
      // Clamp bounds between 2022 and 2027
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

      let html = `<div style="font-weight:700; color:#f8fafc; margin-bottom:6px; border-bottom:1px solid #334155; padding-bottom:4px;">📅 ${{info.date}}</div>`;

      if (info.vacant) {{
        html += `<div style="color:#94a3b8;">Status: <strong style="color:#38bdf8;">Vacant</strong> (Open for booking)</div>`;
      }} else {{
        if (info.out) {{
          html += `<div style="margin-bottom:6px;">
            <span style="color:#f87171; font-weight:700;">Departure ↗</span>: 
            <strong>#${{info.out.confirmation_id || info.out.id}}</strong> (${{info.out.type_description}})<br>
            <span style="color:#94a3b8; font-size:0.75rem;">Check-in: ${{info.out.start_date}} • ${{info.out.days_number}} nts</span>
          </div>`;
        }}
        if (info.in) {{
          html += `<div style="margin-bottom:6px;">
            <span style="color:#34d399; font-weight:700;">Arrival ↘</span>: 
            <strong>#${{info.in.confirmation_id || info.in.id}}</strong> (${{info.in.type_description}})<br>
            <span style="color:#94a3b8; font-size:0.75rem;">Nights: ${{info.in.days_number}} • Check-out: ${{info.in.end_date}}</span><br>
            <span style="color:#fbbf24; font-weight:600; font-size:0.8rem;">Net Payout: $${{parseFloat(info.in.owner_payout || 0).toLocaleString('en-US', {{minimumFractionDigits: 2}})}}</span>
          </div>`;
        }}
        if (info.stay) {{
          html += `<div>
            <span style="color:#38bdf8; font-weight:700;">In-House</span>: 
            <strong>#${{info.stay.confirmation_id || info.stay.id}}</strong> (${{info.stay.type_description}})<br>
            <span style="color:#94a3b8; font-size:0.75rem;">${{info.stay.start_date}} to ${{info.stay.end_date}} (${{info.stay.days_number}} nts)</span><br>
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
      const refStart = new Date(refYear, 0, 1);
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

        // Current year: highlight actuals vs future
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
          // Segment dash for current year past vs future
          segment: year === {today.year} ? {{
            borderDash: ctx => {{
              const pIdx = ctx.p0DataIndex;
              // Day index of today in 365 days
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
              display: false // We use our own customized interactive pills above
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
                  // Display 1st of each month
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
