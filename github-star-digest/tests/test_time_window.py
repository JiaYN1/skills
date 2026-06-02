from __future__ import annotations

from datetime import date, datetime, timezone
import unittest
from zoneinfo import ZoneInfo

from app.time_window import build_window, next_run_at


class TimeWindowTest(unittest.TestCase):
    def test_build_window_for_asia_shanghai_crosses_two_utc_days(self) -> None:
        window = build_window(date(2026, 6, 1), ZoneInfo("Asia/Shanghai"))

        self.assertEqual(window.start_utc.isoformat(), "2026-05-31T16:00:00+00:00")
        self.assertEqual(window.end_utc.isoformat(), "2026-06-01T16:00:00+00:00")
        self.assertEqual(window.start_suffix, "20260531")
        self.assertEqual(window.end_suffix, "20260601")
        self.assertEqual(window.month_key, "2026-06")

    def test_next_run_at_advances_to_tomorrow_after_run_time(self) -> None:
        now = datetime(2026, 6, 2, 2, 0, tzinfo=timezone.utc)
        run_at = next_run_at(now, ZoneInfo("Asia/Shanghai"), "09:00")

        self.assertEqual(run_at.isoformat(), "2026-06-03T09:00:00+08:00")


if __name__ == "__main__":
    unittest.main()

