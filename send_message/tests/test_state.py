from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.state import StateStore


class StateStoreTest(unittest.TestCase):
    def test_mark_seen_deduplicates_and_limits(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            store = StateStore(path, max_seen_per_uid=3)
            store.load()

            store.mark_seen("123", ["1", "2"])
            store.mark_seen("123", ["2", "3", "4"])

            self.assertTrue(store.has_uid("123"))
            self.assertEqual(store.seen_set("123"), {"2", "3", "4"})

            reloaded = StateStore(path, max_seen_per_uid=3)
            reloaded.load()
            self.assertEqual(reloaded.seen_set("123"), {"2", "3", "4"})


if __name__ == "__main__":
    unittest.main()

