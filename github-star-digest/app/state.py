from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any


class BudgetExceeded(RuntimeError):
    """Raised when running a query would exceed the configured app budget."""


@dataclass(frozen=True)
class MonthUsage:
    estimated_download_bytes: int
    downloaded_bytes: int


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, Any] = {"months": {}, "sent_dates": []}

    def load(self) -> None:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as fh:
            loaded = json.load(fh)
        if not isinstance(loaded, dict):
            return
        self.data = loaded
        self.data.setdefault("months", {})
        self.data.setdefault("sent_dates", [])

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent, delete=False) as fh:
            json.dump(self.data, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
            tmp_name = fh.name
        Path(tmp_name).replace(self.path)

    def has_sent(self, target_date_iso: str) -> bool:
        return target_date_iso in set(self.data.get("sent_dates", []))

    def mark_sent(self, target_date_iso: str) -> None:
        sent_dates = list(self.data.get("sent_dates", []))
        if target_date_iso not in sent_dates:
            sent_dates.append(target_date_iso)
        self.data["sent_dates"] = sent_dates[-90:]
        self.save()

    def month_usage(self, month_key: str) -> MonthUsage:
        month = self.data.get("months", {}).get(month_key, {})
        return MonthUsage(
            estimated_download_bytes=int(month.get("estimated_download_bytes", 0)),
            downloaded_bytes=int(month.get("downloaded_bytes", 0)),
        )

    def assert_month_budget(self, month_key: str, next_bytes: int, budget_bytes: int) -> None:
        usage = self.month_usage(month_key)
        if usage.downloaded_bytes + next_bytes > budget_bytes:
            raise BudgetExceeded(
                "Monthly archive download budget would be exceeded: "
                f"current_downloaded={usage.downloaded_bytes}, "
                f"next_download={next_bytes}, budget={budget_bytes}"
            )

    def record_download(self, month_key: str, estimated_bytes: int, downloaded_bytes: int) -> None:
        months = self.data.setdefault("months", {})
        month = months.setdefault(month_key, {})
        month["estimated_download_bytes"] = int(month.get("estimated_download_bytes", 0)) + int(estimated_bytes)
        month["downloaded_bytes"] = int(month.get("downloaded_bytes", 0)) + int(downloaded_bytes)
        self.save()
