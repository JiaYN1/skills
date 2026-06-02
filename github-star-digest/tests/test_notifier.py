from __future__ import annotations

import unittest

from app.models import RepoDigestItem
from app.notifier import MAX_WECOM_MARKDOWN_CHARS, format_digest
from app.time_window import build_window
from zoneinfo import ZoneInfo
from datetime import date


class NotifierFormatTest(unittest.TestCase):
    def test_format_digest_contains_rank_and_budget(self) -> None:
        window = build_window(date(2026, 6, 1), ZoneInfo("Asia/Shanghai"))
        item = RepoDigestItem(
            full_name="owner/repo",
            unique_stargazers=532,
            star_events=540,
            total_stars=12340,
            language="Python",
            description="Useful project <with> markdown-sensitive chars",
            html_url="https://github.com/owner/repo",
            forks_count=99,
            pushed_at="2026-06-01T12:00:00Z",
            fork=False,
            archived=False,
        )

        content = format_digest(
            window,
            [item],
            estimated_bytes=10 * 1024**3,
            downloaded_bytes=9 * 1024**3,
            monthly_downloaded_after=100 * 1024**3,
            monthly_budget=900 * 1024**3,
        )

        self.assertIn("GitHub 昨日 Star 增长 Top 1", content)
        self.assertIn("[owner/repo](https://github.com/owner/repo)", content)
        self.assertIn("+532", content)
        self.assertIn("&lt;with&gt;", content)
        self.assertLessEqual(len(content), MAX_WECOM_MARKDOWN_CHARS)


if __name__ == "__main__":
    unittest.main()
