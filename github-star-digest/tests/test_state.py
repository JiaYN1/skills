from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.state import BudgetExceeded, StateStore


class StateStoreTest(unittest.TestCase):
    def test_records_download_usage_and_sent_dates(self) -> None:
        with TemporaryDirectory() as tmpdir:
            state = StateStore(Path(tmpdir) / "state.json")
            state.load()
            state.record_download("2026-06", estimated_bytes=100, downloaded_bytes=80)
            state.mark_sent("2026-06-01")

            loaded = StateStore(Path(tmpdir) / "state.json")
            loaded.load()

        self.assertTrue(loaded.has_sent("2026-06-01"))
        self.assertEqual(loaded.month_usage("2026-06").estimated_download_bytes, 100)
        self.assertEqual(loaded.month_usage("2026-06").downloaded_bytes, 80)

    def test_budget_guard_uses_downloaded_bytes(self) -> None:
        with TemporaryDirectory() as tmpdir:
            state = StateStore(Path(tmpdir) / "state.json")
            state.record_download("2026-06", estimated_bytes=90, downloaded_bytes=90)

            with self.assertRaises(BudgetExceeded):
                state.assert_month_budget("2026-06", next_bytes=11, budget_bytes=100)


if __name__ == "__main__":
    unittest.main()
