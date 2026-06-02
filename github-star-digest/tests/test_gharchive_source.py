from __future__ import annotations

from datetime import date
import unittest
from zoneinfo import ZoneInfo

from app.gharchive_source import GHArchiveSource, aggregate_events
from app.time_window import build_window


class GHArchiveSourceTest(unittest.TestCase):
    def test_hour_url_uses_gharchive_format(self) -> None:
        source = GHArchiveSource("https://data.gharchive.org", timeout=3, max_download_bytes=1024)
        window = build_window(date(2026, 6, 1), ZoneInfo("Asia/Shanghai"))

        self.assertEqual(source._hour_url(window.start_utc), "https://data.gharchive.org/2026-05-31-16.json.gz")

    def test_aggregate_events_ranks_distinct_stargazers(self) -> None:
        window = build_window(date(2026, 6, 1), ZoneInfo("Asia/Shanghai"))
        events = [
            _watch("owner/a", 1, "2026-05-31T16:01:00Z"),
            _watch("owner/a", 1, "2026-05-31T16:02:00Z"),
            _watch("owner/a", 2, "2026-05-31T16:03:00Z"),
            _watch("owner/b", 3, "2026-05-31T16:04:00Z"),
            _watch("owner/b", 4, "2026-05-31T16:05:00Z"),
            _watch("owner/b", 5, "2026-05-31T16:06:00Z"),
            _watch("owner/c", 6, "2026-05-31T15:59:59Z"),
            {"type": "PushEvent", "repo": {"name": "owner/d"}, "actor": {"id": 7}},
        ]

        candidates = aggregate_events(events, window, candidate_limit=5, min_unique_stargazers=2)

        self.assertEqual([item.full_name for item in candidates], ["owner/b", "owner/a"])
        self.assertEqual(candidates[0].unique_stargazers, 3)
        self.assertEqual(candidates[1].unique_stargazers, 2)
        self.assertEqual(candidates[1].star_events, 3)


def _watch(repo_name: str, actor_id: int, created_at: str) -> dict:
    return {
        "type": "WatchEvent",
        "created_at": created_at,
        "repo": {"name": repo_name},
        "actor": {"id": actor_id},
    }


if __name__ == "__main__":
    unittest.main()

