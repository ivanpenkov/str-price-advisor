"""
Date Segmentation Engine.
Splits open calendar periods over the next 12 months into standard STR rental segments:
- Weekends (Thursday to Sunday, 3 nights; or Friday to Sunday, 2 nights)
- Mid-weeks (Sunday to Thursday, 4 nights)
Calculates lead time (days until check-in) and looks up our current rate for each interval.
"""

from datetime import date, timedelta
from typing import List, Dict, Any, Optional
from src.kivoya_client import KivoyaClient


class CalendarSegmenter:
    """Segments unbooked calendar intervals into weekend and midweek chunks."""

    def __init__(
        self,
        kivoya_client: Optional[KivoyaClient] = None,
        lookahead_days: int = 365,
        cleaning_fee: float = 500.0,
    ):
        self.client = kivoya_client or KivoyaClient()
        self.lookahead_days = lookahead_days
        self.cleaning_fee = cleaning_fee

    def get_booked_dates_set(self, blocked_periods: List[Dict[str, Any]]) -> set:
        """Expand blocked periods into a set of occupied nights (date objects)."""
        booked = set()
        for b in blocked_periods:
            cur = b["start_dt"]
            end = b["end_dt"]
            # Reservation occupies each night from start date up to (but not including checkout)
            while cur < end:
                booked.add(cur)
                cur += timedelta(days=1)
        return booked

    def generate_unbooked_segments(
        self,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> List[Dict[str, Any]]:
        """
        Scan calendar from start_date to end_date.
        For each open period, produce segmented weekend and midweek intervals.
        """
        today = start_date or date.today()
        horizon = end_date or (today + timedelta(days=self.lookahead_days))

        blocked = self.client.get_blocked_periods()
        rates = self.client.get_seasonal_rates()
        booked_nights = self.get_booked_dates_set(blocked)

        segments: List[Dict[str, Any]] = []

        # Iterate week by week across the horizon
        cur = today
        # Align cur to start of evaluation
        while cur < horizon:
            weekday = cur.weekday()  # Monday=0, Tuesday=1, Wednesday=2, Thursday=3, Friday=4, Saturday=5, Sunday=6

            # Candidate Weekend 1: Thursday to Sunday (3 nights)
            if weekday == 3:  # Thursday
                thu, fri, sat = cur, cur + timedelta(days=1), cur + timedelta(days=2)
                sun = cur + timedelta(days=3)
                if thu not in booked_nights and fri not in booked_nights and sat not in booked_nights:
                    segments.append(self._create_segment(thu, sun, "weekend", rates, today))
                elif fri not in booked_nights and sat not in booked_nights:
                    # 2-night weekend fallback
                    segments.append(self._create_segment(fri, sun, "weekend", rates, today))

            # Candidate Midweek: Sunday to Thursday (4 nights)
            elif weekday == 6:  # Sunday
                sun = cur
                mon, tue, wed = cur + timedelta(days=1), cur + timedelta(days=2), cur + timedelta(days=3)
                thu = cur + timedelta(days=4)
                if all(d not in booked_nights for d in [sun, mon, tue, wed]):
                    segments.append(self._create_segment(sun, thu, "midweek", rates, today))
                else:
                    # Check for partial midweek (Mon-Thu 3 nights, or Tue-Thu 2 nights)
                    open_midweek = [d for d in [sun, mon, tue, wed] if d not in booked_nights]
                    if len(open_midweek) >= 2:
                        # contiguous sub-interval
                        c_start = open_midweek[0]
                        c_end = c_start + timedelta(days=1)
                        for d in open_midweek[1:]:
                            if d == c_end:
                                c_end += timedelta(days=1)
                            else:
                                break
                        if (c_end - c_start).days >= 2:
                            segments.append(self._create_segment(c_start, c_end, "midweek", rates, today))

            cur += timedelta(days=1)

        return segments

    def _create_segment(
        self,
        check_in: date,
        check_out: date,
        seg_type: str,
        rates: List[Dict[str, Any]],
        today: date,
    ) -> Dict[str, Any]:
        """Build standard segment record with rate calculation."""
        nights = (check_out - check_in).days
        lead_days = (check_in - today).days

        # Compute average nightly rate across the stay dates
        total_base = 0.0
        cur = check_in
        while cur < check_out:
            total_base += self.client.get_rate_for_date(cur, rates)
            cur += timedelta(days=1)

        avg_base_rate = round(total_base / nights, 2)
        total_guest_price = round(total_base + self.cleaning_fee, 2)
        effective_nightly = round(total_guest_price / nights, 2)

        return {
            "check_in": check_in.strftime("%Y-%m-%d"),
            "check_out": check_out.strftime("%Y-%m-%d"),
            "check_in_dt": check_in,
            "check_out_dt": check_out,
            "segment_type": seg_type,
            "nights": nights,
            "lead_time_days": lead_days,
            "our_base_nightly": avg_base_rate,
            "our_cleaning_fee": self.cleaning_fee,
            "our_total_price": total_guest_price,
            "our_effective_nightly": effective_nightly,
        }
