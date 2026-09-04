"""
Multi-Format Report Generator.
Produces:
1. Executive Markdown / HTML report with 3-tier prioritization:
   - Section 1: Urgent Action Required (Weekly - Out of whack / Imminent)
   - Section 2: Moderate Adjustment (Monthly review)
   - Section 3: Informational / On-Target intervals
2. CSV file structured for direct copy-paste into Google Sheets / Kivoya PMS
3. Machine-readable JSON summary for historical tracking
"""

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional


class PriceReportGenerator:
    """Generates prioritized reports for the host and property manager."""

    def __init__(self, output_dir: str = "data"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_all(
        self,
        evaluated_segments: List[Dict[str, Any]],
        property_name: str = "Villa del Sol",
        run_timestamp: Optional[str] = None,
    ) -> Dict[str, str]:
        """Generate Markdown, CSV, and JSON reports. Returns paths to generated files."""
        ts = run_timestamp or datetime.now().strftime("%Y-%m-%d_%H%M%S")
        date_str = ts.split("_")[0]

        md_path = self.output_dir / f"pricing_report_{date_str}.md"
        csv_path = self.output_dir / f"pricing_sheet_{date_str}.csv"
        json_path = self.output_dir / f"pricing_data_{date_str}.json"
        latest_md = self.output_dir / "latest_report.md"
        latest_csv = self.output_dir / "latest_sheet.csv"

        # Split into the 3 tiers
        urgent = [s for s in evaluated_segments if s["priority_tier"] == "URGENT_ACTION"]
        moderate = [s for s in evaluated_segments if s["priority_tier"] == "MODERATE_ADJUSTMENT"]
        info = [s for s in evaluated_segments if s["priority_tier"] == "INFORMATIONAL"]

        # Sort urgent and moderate by absolute price diff descending, then by check-in date
        urgent.sort(key=lambda x: (abs(x.get("price_diff_percent", 0))), reverse=True)
        moderate.sort(key=lambda x: (abs(x.get("price_diff_percent", 0))), reverse=True)
        info.sort(key=lambda x: x["check_in_dt"])

        # 1. Generate Markdown Report
        md_content = self._build_markdown_report(
            property_name=property_name,
            report_date=date_str,
            urgent=urgent,
            moderate=moderate,
            info=info,
            total_segments=len(evaluated_segments),
        )
        md_path.write_text(md_content, encoding="utf-8")
        latest_md.write_text(md_content, encoding="utf-8")

        # 2. Generate CSV for Google Sheets
        self._build_csv_export(csv_path, evaluated_segments)
        self._build_csv_export(latest_csv, evaluated_segments)

        # 3. Generate JSON
        payload = {
            "property": property_name,
            "report_date": date_str,
            "generated_at": datetime.now().isoformat(),
            "summary": {
                "total_open_intervals": len(evaluated_segments),
                "urgent_count": len(urgent),
                "moderate_count": len(moderate),
                "competitive_count": len(info),
            },
            "urgent_intervals": urgent,
            "moderate_intervals": moderate,
            "informational_intervals": info,
        }
        json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

        return {
            "markdown": str(md_path),
            "csv": str(csv_path),
            "json": str(json_path),
        }

    def _build_markdown_report(
        self,
        property_name: str,
        report_date: str,
        urgent: List[Dict[str, Any]],
        moderate: List[Dict[str, Any]],
        info: List[Dict[str, Any]],
        total_segments: int,
    ) -> str:
        """Construct the formatted Markdown summary report."""
        lines = [
            f"# 🏷️ STR Competitive Pricing Advisory Report",
            f"**Property**: {property_name} (920 E Carver Rd, Tempe, AZ)",
            f"**Report Date**: {report_date}",
            f"**Strategy**: Dynamic Luxury Benchmark (75th–80th Percentile with Lead-Time Tapering)",
            f"",
            f"---",
            f"",
            f"## 📊 Executive Summary",
            f"- **Total Open Calendar Intervals**: {total_segments}",
            f"- 🚨 **Urgent Adjustments (This Week)**: **{len(urgent)}** intervals",
            f"- ⚠️ **Moderate Adjustments (Monthly Review)**: **{len(moderate)}** intervals",
            f"- ✅ **Competitive / On Target**: **{len(info)}** intervals",
            f"",
            f"> 💡 **Action Guidance for Kivoya Property Manager**:",
            f"> Review **Section 1** immediately. These intervals are substantially mispriced (>25% off market or imminent arrival) and directly impact booking conversion or leave significant revenue on the table. **Section 2** can be reviewed during monthly rate adjustments.",
            f"",
            f"---",
            f"",
            f"## 🚨 Section 1: Urgent Action Required (Action This Week)",
        ]

        if not urgent:
            lines.append("*(No urgent price discrepancies detected this week. Rates are well aligned!)*\n")
        else:
            lines.append("The following dates have major pricing anomalies that require immediate update in the PMS rate sheet:\n")
            lines.append(self._format_table(urgent))
            lines.append("")

        lines.extend([
            f"---",
            f"",
            f"## ⚠️ Section 2: Moderate Adjustments (Review Within 30 Days)",
        ])

        if not moderate:
            lines.append("*(No moderate price adjustments needed at this time.)*\n")
        else:
            lines.append("The following dates are 10%–25% off the target percentile for future dates:\n")
            lines.append(self._format_table(moderate))
            lines.append("")

        lines.extend([
            f"---",
            f"",
            f"## ℹ️ Section 3: All Open Intervals (Complete Schedule)",
            f"Complete 12-month calendar of unbooked intervals and market benchmarks:\n",
            self._format_table(urgent + moderate + info, full=True),
            f"",
            f"---",
            f"*Generated autonomously by STR Price Advisor Agent.*",
        ])

        return "\n".join(lines)

    def _format_table(self, segments: List[Dict[str, Any]], full: bool = False) -> str:
        """Render markdown table for segments."""
        headers = [
            "Dates",
            "Type",
            "Nights",
            "Lead (Days)",
            "Our Base",
            "Our Eff. Nightly",
            "Comp 50th",
            "Comp Target",
            "Market Diff",
            "Rec. Base Rate",
            "Action Needed",
        ]
        rows = [
            "| " + " | ".join(headers) + " |",
            "| " + " | ".join(["---"] * len(headers)) + " |",
        ]

        for s in segments:
            dates = f"`{s['check_in']} -> {s['check_out']}`"
            seg_type = s["segment_type"].capitalize()
            nights = str(s["nights"])
            lead = f"{s['lead_time_days']}d"
            our_base = f"${s['our_base_nightly']:.0f}"
            our_eff = f"${s['our_effective_nightly']:.0f}"
            p50 = f"${s['comp_p50_eff']:.0f}"
            target_pct = f"${s['comp_target_eff']:.0f} ({s['target_percentile']:.0f}%)"

            diff = s["price_diff_percent"]
            diff_str = f"{'+' if diff > 0 else ''}{diff:.1f}%"
            if diff >= 25.0:
                diff_str = f"🔴 **+{diff:.1f}%**"
            elif diff <= -25.0:
                diff_str = f"🔵 **{diff:.1f}%**"
            elif abs(diff) >= 10.0:
                diff_str = f"🟡 {diff_str}"

            rec_base = f"**${s['recommended_base_nightly']:.0f}**"
            action = s["action_summary"]

            row = [
                dates,
                seg_type,
                nights,
                lead,
                our_base,
                our_eff,
                p50,
                target_pct,
                diff_str,
                rec_base,
                action,
            ]
            rows.append("| " + " | ".join(row) + " |")

        return "\n".join(rows)

    def _build_csv_export(self, csv_path: Path, segments: List[Dict[str, Any]]) -> None:
        """Write clean CSV export suitable for Google Sheets import."""
        fieldnames = [
            "priority_tier",
            "status",
            "check_in",
            "check_out",
            "segment_type",
            "nights",
            "lead_time_days",
            "our_base_nightly",
            "our_cleaning_fee",
            "our_total_price",
            "our_effective_nightly",
            "our_percentile_rank",
            "target_percentile",
            "comp_p50_eff",
            "comp_p75_eff",
            "comp_p80_eff",
            "comp_target_eff",
            "price_diff_percent",
            "recommended_base_nightly",
            "base_diff",
            "action_summary",
        ]
        with open(csv_path, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for s in sorted(segments, key=lambda x: x["check_in_dt"]):
                writer.writerow(s)
