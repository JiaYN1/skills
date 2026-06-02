from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class TimeWindow:
    target_date: date
    timezone_name: str
    start_local: datetime
    end_local: datetime
    start_utc: datetime
    end_utc: datetime
    start_suffix: str
    end_suffix: str

    @property
    def label(self) -> str:
        return (
            f"{self.start_local:%Y-%m-%d %H:%M}"
            f"-{self.end_local:%Y-%m-%d %H:%M} {self.timezone_name}"
        )

    @property
    def target_date_iso(self) -> str:
        return self.target_date.isoformat()

    @property
    def month_key(self) -> str:
        return self.target_date.strftime("%Y-%m")


def yesterday_window(now: datetime, tz: ZoneInfo) -> TimeWindow:
    local_now = now.astimezone(tz)
    return build_window(local_now.date() - timedelta(days=1), tz)


def build_window(target_date: date, tz: ZoneInfo) -> TimeWindow:
    start_local = datetime.combine(target_date, time.min, tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    start_utc = start_local.astimezone(timezone.utc)
    end_utc = end_local.astimezone(timezone.utc)
    end_suffix_dt = end_utc - timedelta(microseconds=1)
    return TimeWindow(
        target_date=target_date,
        timezone_name=getattr(tz, "key", str(tz)),
        start_local=start_local,
        end_local=end_local,
        start_utc=start_utc,
        end_utc=end_utc,
        start_suffix=start_utc.strftime("%Y%m%d"),
        end_suffix=end_suffix_dt.strftime("%Y%m%d"),
    )


def next_run_at(now: datetime, tz: ZoneInfo, run_time: str) -> datetime:
    hour, minute = (int(part) for part in run_time.split(":"))
    local_now = now.astimezone(tz)
    candidate = datetime.combine(local_now.date(), time(hour, minute), tzinfo=tz)
    if candidate <= local_now:
        candidate += timedelta(days=1)
    return candidate

