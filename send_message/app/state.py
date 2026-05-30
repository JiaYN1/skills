from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class StateStore:
    def __init__(self, path: Path, max_seen_per_uid: int) -> None:
        self.path = path
        self.max_seen_per_uid = max_seen_per_uid
        self._data: dict[str, Any] = {"seen": {}}

    def load(self) -> None:
        if not self.path.exists():
            self._data = {"seen": {}}
            return

        with self.path.open("r", encoding="utf-8") as fh:
            loaded = json.load(fh)
        if not isinstance(loaded, dict):
            loaded = {}
        seen = loaded.get("seen")
        if not isinstance(seen, dict):
            seen = {}
        self._data = {"seen": seen}

    def has_uid(self, uid: str) -> bool:
        return uid in self._seen_dict()

    def seen_set(self, uid: str) -> set[str]:
        return set(self._seen_list(uid))

    def mark_seen(self, uid: str, dynamic_ids: list[str]) -> None:
        if not dynamic_ids:
            return

        seen = self._seen_dict()
        existing = self._seen_list(uid)
        merged = list(dict.fromkeys([*dynamic_ids, *existing]))
        seen[uid] = merged[: self.max_seen_per_uid]
        self._save()

    def _seen_dict(self) -> dict[str, list[str]]:
        seen = self._data.setdefault("seen", {})
        if not isinstance(seen, dict):
            seen = {}
            self._data["seen"] = seen
        return seen

    def _seen_list(self, uid: str) -> list[str]:
        values = self._seen_dict().get(uid, [])
        if not isinstance(values, list):
            return []
        return [str(value) for value in values]

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_name(f".{self.path.name}.tmp")
        with temp_path.open("w", encoding="utf-8") as fh:
            json.dump(self._data, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(temp_path, self.path)

