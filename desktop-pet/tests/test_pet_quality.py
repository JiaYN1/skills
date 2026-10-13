"""Tests for the deterministic frame quality checks."""

import tempfile
import unittest
from pathlib import Path

try:
    from PIL import Image, ImageDraw

    PIL_AVAILABLE = True
except Exception:  # pragma: no cover - Pillow is a declared dependency
    Image = None
    ImageDraw = None
    PIL_AVAILABLE = False

import pet_quality
from pet_assets import normalize_pet_sequence

BODY = (200, 130, 70, 255)
OTHER_BODY = (70, 75, 80, 255)
LEG = (120, 80, 40, 255)

CANVAS = 400
BODY_ORIGIN = 80
BODY_SIZE = 240


def _frame(
    path: Path,
    leg=None,
    body_scale: float = 1.0,
    colour=BODY,
    shift=(0, 0),
    blank: bool = False,
    top_limb=None,
) -> Path:
    """Build one frame of a synthetic action sequence."""

    image = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    if top_limb is not None:
        draw.rectangle(top_limb, fill=LEG)
    if not blank:
        size = int(BODY_SIZE * body_scale)
        left = BODY_ORIGIN + shift[0] + (BODY_SIZE - size) // 2
        top = BODY_ORIGIN + shift[1] + (BODY_SIZE - size) // 2
        draw.ellipse((left, top, left + size - 1, top + size - 1), fill=colour)
        draw.ellipse(
            (left + int(size * 0.6), top - int(size * 0.12), left + size, top + int(size * 0.4)),
            fill=colour,
        )
        if leg is not None:
            draw.rectangle(leg, fill=LEG)
    image.save(path)
    return path


def _walk_legs(index: int):
    """Return a leg rectangle that swings like a walk cycle."""

    swing = int(60 * ((index % 5) / 4.0 - 0.5))
    return (150 + swing, 320, 190 + swing, 390)


class QualityJudgementTests(unittest.TestCase):
    def _measure(self, root: Path, builders):
        sources = [
            builders[index](root / f"src-{index}.png", index)
            for index in range(len(builders))
        ]
        destinations = [root / f"out-{index}.png" for index in range(len(sources))]
        report = {}
        normalize_pet_sequence(
            sources,
            destinations,
            canvas_size=320,
            background_mode="none",
            anchor="bottom",
            subject_scale=0.96,
            report=report,
        )
        signatures = pet_quality.subject_signatures(sources, background_mode="none")
        return pet_quality.judge_sequence(report, signatures)

    def test_report_carries_the_sequence_measurements(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sources = [_frame(root / f"src-{index}.png", _walk_legs(index)) for index in range(4)]
            destinations = [root / f"out-{index}.png" for index in range(4)]
            report = {}
            normalize_pet_sequence(
                sources,
                destinations,
                canvas_size=320,
                background_mode="none",
                anchor="bottom",
                report=report,
            )

            self.assertIn("render_scale", report)
            self.assertGreater(report["render_scale"], 0)
            self.assertEqual(report["canvas_size"], 320)
            self.assertFalse(report["pinned"])
            self.assertEqual(len(report["frames"]), 4)
            self.assertEqual(len(report["placements"]), 4)
            for frame in report["frames"]:
                self.assertGreater(frame["mass"], 0)
                self.assertIn("alignment_cost", frame)
                self.assertFalse(frame["clipped"])

    def test_clean_walk_cycle_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as temporary:
            verdicts = self._measure(
                Path(temporary),
                [lambda path, index: _frame(path, _walk_legs(index)) for index in range(6)],
            )

            self.assertEqual(pet_quality.flagged_frames(verdicts), [])
            self.assertEqual(pet_quality.describe(verdicts), "")

    def test_body_size_outlier_is_flagged(self):
        with tempfile.TemporaryDirectory() as temporary:
            def build(path, index):
                leg = _walk_legs(index)
                if index == 3:
                    return _frame(path, leg, body_scale=1.45)
                return _frame(path, leg)

            verdicts = self._measure(Path(temporary), [build] * 6)
            flagged = pet_quality.flagged_frames(verdicts)

            self.assertEqual([item["index"] for item in flagged], [3])
            self.assertIn("body-scale", flagged[0]["reasons"])
            self.assertGreater(flagged[0]["metrics"]["body_scale"], 1.2)
            self.assertIn("第 4 帧", pet_quality.describe(verdicts))

    def test_recoloured_body_is_flagged_as_identity_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            def build(path, index):
                colour = OTHER_BODY if index == 4 else BODY
                return _frame(path, _walk_legs(index), colour=colour)

            verdicts = self._measure(Path(temporary), [build] * 6)
            flagged = pet_quality.flagged_frames(verdicts)

            self.assertEqual([item["index"] for item in flagged], [4])
            self.assertIn("identity", flagged[0]["reasons"])
            self.assertGreater(flagged[0]["metrics"]["identity_distance"], 0.34)

    def test_shifted_body_is_not_flagged_because_registration_absorbs_it(self):
        """A frame the model drew off-center is normal, not a defect."""

        with tempfile.TemporaryDirectory() as temporary:
            def build(path, index):
                shift = (24, 12) if index == 2 else (0, 0)
                return _frame(path, _walk_legs(index), shift=shift)

            verdicts = self._measure(Path(temporary), [build] * 6)

            self.assertEqual([item["reasons"] for item in verdicts], [[] for _ in verdicts])

    def test_short_sequence_is_never_judged(self):
        with tempfile.TemporaryDirectory() as temporary:
            def build(path, index):
                if index == 1:
                    return _frame(path, _walk_legs(index), body_scale=1.8, colour=OTHER_BODY)
                return _frame(path, _walk_legs(index))

            verdicts = self._measure(Path(temporary), [build] * 2)

            self.assertEqual(verdicts, [])

    def test_unreadable_frame_is_flagged(self):
        with tempfile.TemporaryDirectory() as temporary:
            def build(path, index):
                return _frame(path, _walk_legs(index), blank=index == 2)

            sources = [
                build(Path(temporary) / f"src-{index}.png", index) for index in range(5)
            ]
            signatures = pet_quality.subject_signatures(sources, background_mode="none")
            report = {"frames": [{"index": index} for index in range(5)]}
            verdicts = pet_quality.judge_sequence(report, signatures)
            flagged = pet_quality.flagged_frames(verdicts)

            self.assertEqual([item["index"] for item in flagged], [2])
            self.assertIn("unreadable", flagged[0]["reasons"])

    def test_pinned_edit_reports_a_clipped_frame_instead_of_rescaling(self):
        """An oversized replacement is clipped and reported, not rescaled.

        Without the pinned contract the whole sequence shrinks to make room for
        the new frame, which is exactly the jump this contract prevents.
        """

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sources = [
                _frame(root / f"src-{index}.png", _walk_legs(index)) for index in range(8)
            ]
            destinations = [root / f"out-{index}.png" for index in range(8)]
            first = {}
            normalize_pet_sequence(
                sources,
                destinations,
                canvas_size=320,
                background_mode="none",
                anchor="bottom",
                subject_scale=0.96,
                report=first,
            )

            # A frame whose raised limb reaches above the body box.
            _frame(root / "src-5.png", _walk_legs(5), top_limb=(150, 0, 200, 120))

            unpinned = {}
            normalize_pet_sequence(
                sources,
                destinations,
                canvas_size=320,
                background_mode="none",
                anchor="bottom",
                subject_scale=0.96,
                report=unpinned,
            )
            pinned = {}
            normalize_pet_sequence(
                sources,
                destinations,
                canvas_size=320,
                background_mode="none",
                anchor="bottom",
                subject_scale=0.96,
                report=pinned,
                contract=first,
            )
            signatures = pet_quality.subject_signatures(sources, background_mode="none")
            clipped = [
                item
                for item in pet_quality.judge_sequence(pinned, signatures)
                if "clipped" in item["reasons"]
            ]

            # Without the contract the whole sequence is nudged (or rescaled) to
            # make room for the one new frame.
            self.assertNotEqual(unpinned["placements"][0], first["placements"][0])
            self.assertFalse(unpinned["frames"][5]["clipped"])
            self.assertIsNone(unpinned.get("contract_drift"))

            # With the contract every other frame stays exactly where it was.
            # ``contract_drift`` names every frame that moved; only the edited
            # frame may ever appear there.
            self.assertTrue(pinned["pinned"])
            self.assertEqual(pinned["render_scale"], first["render_scale"])
            self.assertEqual(pinned["reference_index"], first["reference_index"])
            self.assertEqual(pinned.get("contract_drift"), [5])
            self.assertEqual(
                [placement for index, placement in enumerate(pinned["placements"]) if index != 5],
                [placement for index, placement in enumerate(first["placements"]) if index != 5],
            )
            self.assertEqual([item["index"] for item in clipped], [5])

    def test_frame_flags_reads_one_index(self):
        verdicts = [
            {"index": 0, "reasons": []},
            {"index": 1, "reasons": ["body-scale"]},
        ]

        self.assertEqual(pet_quality.frame_flags(verdicts, 1), ["body-scale"])
        self.assertEqual(pet_quality.frame_flags(verdicts, 7), [])

    def test_identical_signatures_have_zero_distance(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = _frame(root / "a.png")
            second = _frame(root / "b.png")
            signatures = pet_quality.subject_signatures([first, second], background_mode="none")

            self.assertEqual(
                pet_quality.signature_distance(signatures[0], signatures[1]), 0.0
            )


if __name__ == "__main__":
    unittest.main()
