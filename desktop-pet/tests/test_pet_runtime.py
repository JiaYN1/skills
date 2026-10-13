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


class PetRuntimeGaitTests(unittest.TestCase):
    """The walk cycle must follow the ground, not the wall clock."""

    @staticmethod
    def _walking_window(speed=2.2, x=400, width=320, screen_width=1920, direction=1):
        window = object.__new__(PetWindow)
        window.state = "walk"
        window.config = types.SimpleNamespace(speed=speed)
        window.direction = direction
        window.x = float(x)
        window.gait_phase = 0.0
        window.frame_index = 0
        window.distance_per_slot = 11.0 / 3.0
        window.canvas = types.SimpleNamespace(
            winfo_width=lambda: width,
            winfo_height=lambda: 320,
        )
        window.root = types.SimpleNamespace(
            winfo_screenwidth=lambda: screen_width,
        )
        window._set_geometry = lambda *_args, **_kwargs: None
        return window

    def test_moving_advances_the_phase_by_travel(self):
        window = self._walking_window()
        travelled = window._move()
        window._advance_walk_phase(travelled)

        self.assertAlmostEqual(travelled, 2.2)
        self.assertAlmostEqual(window.gait_phase, 2.2 / window.distance_per_slot)
        self.assertEqual(window.frame_index, int(window.gait_phase))

    def test_a_blocked_pet_stops_cycling_its_legs(self):
        # A pet as wide as the screen is clamped at the edge on every tick and
        # can never actually travel, so its legs must stop instead of walking in
        # place forever.
        window = self._walking_window(x=0, width=320, screen_width=320)

        for _ in range(10):
            travelled = window._move()
            window._advance_walk_phase(travelled)
            self.assertEqual(travelled, 0.0)

        self.assertEqual(window.gait_phase, 0.0)
        self.assertEqual(window.frame_index, 0)

    def test_the_phase_never_runs_backwards_when_the_pet_turns(self):
        window = self._walking_window(direction=-1)
        travelled = window._move()
        window._advance_walk_phase(travelled)
        forward_phase = window.gait_phase

        window.direction = 1
        travelled = window._move()
        window._advance_walk_phase(travelled)

        self.assertGreater(travelled, 0)
        self.assertGreater(window.gait_phase, forward_phase)

    def test_non_walk_states_do_not_move(self):
        window = self._walking_window()
        window.state = "idle"

        self.assertEqual(window._move(), 0.0)

    def test_faster_speed_advances_the_gait_faster_per_tick(self):
        slow = self._walking_window(speed=1.0)
        fast = self._walking_window(speed=4.0)
        for window in (slow, fast):
            window._advance_walk_phase(window._move())

        self.assertLess(slow.gait_phase, fast.gait_phase)


class PetRuntimeFrameLoadingTests(unittest.TestCase):
    """Hybrid must not pay for skeleton frames it never shows."""

    def _window(self, mode, skeleton_manifest, source_count=4):
        window = object.__new__(PetWindow)
        window.animation_mode = mode
        window.skeleton_manifest = skeleton_manifest
        window.skeleton_calls = []
        window._source_images = lambda: {
            role: [f"{role}-{index}" for index in range(source_count)]
            for role in ("idle", "walk", "sleep", "react")
        }
        window._frame_count = lambda role, available: available
        window._frame_repeat = lambda role: 1
        window._fit_image = lambda source: ("fit", source)
        window._animation_frame = lambda source, role, index: ("procedural", source)
        window._skeleton_frame = lambda source, role, index: (
            window.skeleton_calls.append(source) or ("skeleton", source)
        )
        return window

    def test_hybrid_never_computes_skeleton_frames(self):
        window = self._window("hybrid", {"animations": {}})

        frames = window._load_animation_frames()

        self.assertEqual(window.skeleton_calls, [])
        self.assertEqual(frames["walk"], [("fit", "walk-0"), ("fit", "walk-1"), ("fit", "walk-2"), ("fit", "walk-3")])

    def test_png_mode_never_computes_skeleton_frames(self):
        window = self._window("png", {"animations": {}})

        window._load_animation_frames()

        self.assertEqual(window.skeleton_calls, [])

    def test_skeleton_mode_uses_the_skeleton_frames(self):
        window = self._window("skeleton", {"animations": {}})

        frames = window._load_animation_frames()

        self.assertEqual(len(window.skeleton_calls), 16)
        self.assertEqual(frames["idle"][0], ("skeleton", "idle-0"))

    def test_skeleton_mode_falls_back_when_the_manifest_has_no_pose(self):
        window = self._window("skeleton", {"animations": {}})
        window._skeleton_frame = lambda source, role, index: None

        frames = window._load_animation_frames()

        self.assertEqual(frames["walk"][0], ("fit", "walk-0"))

    def test_single_source_roles_use_procedural_motion(self):
        window = self._window("hybrid", None, source_count=1)

        frames = window._load_animation_frames()

        self.assertEqual(window.skeleton_calls, [])
        self.assertEqual(frames["idle"][0], ("procedural", "idle-0"))


class PetRuntimeProceduralMotionTests(unittest.TestCase):
    """A photo fallback must keep the same ground contact as AI frames."""

    @staticmethod
    def _window():
        from PIL import Image, ImageEnhance

        window = object.__new__(PetWindow)
        window.Image = Image
        window.ImageEnhance = ImageEnhance
        window.config = types.SimpleNamespace(scale=1.0)
        return window

    @staticmethod
    def _sprite():
        from PIL import Image, ImageDraw

        image = Image.new("RGBA", (320, 320), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        # A body standing on the bottom of the canvas.
        draw.rectangle((100, 60, 220, 319), fill=(200, 130, 70, 255))
        return image

    @staticmethod
    def _ground_row(image):
        """Return the lowest row that contains any visible pixel."""

        alpha = image.getchannel("A")
        width, height = image.size
        for y in range(height - 1, -1, -1):
            if any(alpha.getpixel((x, y)) > 0 for x in range(0, width, 4)):
                return y
        return -1

    def test_idle_breathing_keeps_the_feet_on_the_ground(self):
        window = self._window()
        sprite = self._sprite()

        rows = [
            self._ground_row(window._animation_frame(sprite, "idle", step))
            for step in range(8)
        ]

        self.assertEqual(set(rows), {319})

    def test_walk_fallback_never_sinks_below_the_ground(self):
        window = self._window()
        sprite = self._sprite()

        rows = [
            self._ground_row(window._animation_frame(sprite, "walk", step))
            for step in range(4)
        ]

        self.assertTrue(all(row <= 319 for row in rows))
        self.assertIn(319, rows)

    def test_react_bounce_stays_grounded(self):
        window = self._window()
        sprite = self._sprite()

        rows = [
            self._ground_row(window._animation_frame(sprite, "react", step))
            for step in range(6)
        ]

        self.assertTrue(all(310 <= row <= 319 for row in rows), rows)

    def test_sleep_rotation_pivots_near_the_ground(self):
        window = self._window()
        sprite = self._sprite()

        rows = [
            self._ground_row(window._animation_frame(sprite, "sleep", step))
            for step in range(5)
        ]

        self.assertTrue(all(300 <= row <= 319 for row in rows), rows)

    def test_a_grown_pose_is_cropped_not_off_canvas(self):
        window = self._window()
        sprite = self._sprite()

        frame = window._grounded_frame(sprite, 1.25)

        self.assertEqual(frame.size, (320, 320))
        self.assertEqual(self._ground_row(frame), 319)


if __name__ == "__main__":
    unittest.main()
