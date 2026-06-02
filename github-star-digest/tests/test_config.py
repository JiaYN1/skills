from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.config import Config, ConfigError, GIB


class ConfigTest(unittest.TestCase):
    def test_from_env_accepts_dry_run_without_webhook(self) -> None:
        env = {
            "NOTIFY_DRY_RUN": "true",
            "ARCHIVE_MAX_DOWNLOAD_BYTES": "12GiB",
            "ARCHIVE_MONTHLY_DOWNLOAD_BUDGET_BYTES": "120GiB",
        }
        with patch.dict(os.environ, env, clear=True):
            config = Config.from_env()

        self.assertTrue(config.notify_dry_run)
        self.assertEqual(config.archive_max_download_bytes, 12 * GIB)
        self.assertEqual(config.archive_monthly_download_budget_bytes, 120 * GIB)
        self.assertEqual(config.timezone_name, "Asia/Shanghai")
        self.assertEqual(config.run_time, "09:00")

    def test_requires_webhook_when_not_dry_run(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ConfigError):
                Config.from_env()

    def test_can_skip_webhook_for_estimate_only_config(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = Config.from_env(require_notifier=False)

        self.assertIsNone(config.wecom_webhook_url)

    def test_candidate_limit_must_cover_top_limit(self) -> None:
        env = {
            "NOTIFY_DRY_RUN": "true",
            "TOP_LIMIT": "20",
            "CANDIDATE_LIMIT": "10",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ConfigError):
                Config.from_env()


if __name__ == "__main__":
    unittest.main()
