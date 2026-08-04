import types
import unittest
from unittest.mock import patch

from pet_runtime import PetWindow


class PetRuntimeStateTests(unittest.TestCase):
    @staticmethod
    def _window(state="idle"):
        window = object.__new__(PetWindow)
        window.state = state
        window.state_started = 0.0
        window.state_duration = 1.0
        window.last_interaction = 100.0
        window.config = types.SimpleNamespace(sleep_after_seconds=60)
        window.frame_index = 0
        window.next_frame_at = 0.0
        return window

    def test_idle_does_not_randomly_start_walking(self):
        window = self._window()
        with patch("pet_runtime.random.random", return_value=0.0):
            window._maybe_change_state(110.0)
        self.assertEqual(window.state, "idle")

    def test_idle_enters_sleep_after_inactivity(self):
        window = self._window()
        window._maybe_change_state(170.0)
        self.assertEqual(window.state, "sleep")

    def test_sleep_stays_until_interaction(self):
        window = self._window("sleep")
        window._maybe_change_state(20.0)
        self.assertEqual(window.state, "sleep")


if __name__ == "__main__":
    unittest.main()
