"""Tests for sprite-sheet role generation and its grid-checked fallback."""

import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, ImageDraw

from pet_atlas import grid_for
from server.app.ai_provider import GenerationCancelled  # noqa: F401
from server.app.package_builder import atlas_frame_threshold, generate_role_from_sheet

CELL = 96
BODY = (200, 130, 70, 255)


def _sheet_image(columns, rows, *, empty=(), opaque=False, body: float = 0.5):
    """Build a sheet where every cell holds one centered subject."""

    background = (255, 255, 255, 255) if opaque else (0, 0, 0, 0)
    sheet = Image.new("RGBA", (columns * CELL, rows * CELL), background)
    draw = ImageDraw.Draw(sheet)
    for row in range(rows):
        for column in range(columns):
            index = row * columns + column
            if index in empty:
                continue
            size = int(CELL * body)
            left = column * CELL + (CELL - size) // 2
            top = row * CELL + (CELL - size) // 2
            draw.ellipse((left, top, left + size - 1, top + size - 1), fill=BODY)
    return sheet


class FakeSheetProvider:
    """Writes a canned sheet and records the requests it received."""

    def __init__(self, sheet, *, usable_cutout: bool = True):
        self.sheet = sheet
        self.usable_cutout = usable_cutout
        self.sheet_calls = []
        self.cutout_calls = []
        self.settings = None

    async def generate_action_sheet(
        self,
        role,
        output_path,
        frame_count=8,
        columns=4,
        rows=2,
        identity_reference=None,
        references=None,
    ):
        self.sheet_calls.append(
            {
                "role": role,
                "frame_count": int(frame_count),
                "columns": int(columns),
                "rows": int(rows),
            }
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self.sheet.save(output_path)
        return output_path

    async def remove_background(self, source, output_path):
        self.cutout_calls.append((Path(source), Path(output_path)))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if self.usable_cutout:
            # Turn the opaque sheet transparent without moving the subject.
            image = Image.open(source).convert("RGBA")
            pixels = image.load()
            for y in range(image.height):
                for x in range(image.width):
                    if pixels[x, y][:3] == (255, 255, 255):
                        pixels[x, y] = (0, 0, 0, 0)
            image.save(output_path)
        else:
            Image.open(source).convert("RGBA").save(output_path)
        return output_path


def _run(coroutine):
    return asyncio.run(coroutine)


class AtlasThresholdTests(unittest.TestCase):
    def test_modes_pick_different_thresholds(self):
        self.assertEqual(atlas_frame_threshold("off"), 0)
        self.assertEqual(atlas_frame_threshold("on"), 2)
        self.assertEqual(atlas_frame_threshold("auto"), 4)
        self.assertEqual(atlas_frame_threshold("nonsense"), 4)


class SheetRoleGenerationTests(unittest.TestCase):
    def test_a_transparent_sheet_is_split_without_a_cutout_request(self):
        with TemporaryDirectory() as temporary:
            role_dir = Path(temporary)
            provider = FakeSheetProvider(_sheet_image(5, 2))

            frames = _run(
                generate_role_from_sheet(provider, "walk", role_dir, 10, None, None)
            )

            self.assertEqual(len(frames), 10)
            self.assertEqual(provider.sheet_calls[0]["columns"], 5)
            self.assertEqual(provider.sheet_calls[0]["rows"], 2)
            self.assertEqual(provider.cutout_calls, [])
            for frame in frames:
                with Image.open(frame) as image:
                    self.assertEqual(image.size, (CELL, CELL))
                    self.assertIsNotNone(image.getchannel("A").getbbox())

    def test_an_opaque_sheet_gets_one_cutout_request(self):
        with TemporaryDirectory() as temporary:
            role_dir = Path(temporary)
            provider = FakeSheetProvider(_sheet_image(3, 3, opaque=True))

            frames = _run(
                generate_role_from_sheet(provider, "sleep", role_dir, 9, None, None)
            )

            self.assertEqual(len(frames), 9)
            self.assertEqual(len(provider.cutout_calls), 1)

    def test_a_sheet_that_stays_opaque_is_rejected(self):
        with TemporaryDirectory() as temporary:
            role_dir = Path(temporary)
            provider = FakeSheetProvider(
                _sheet_image(3, 3, opaque=True), usable_cutout=False
            )

            frames = _run(
                generate_role_from_sheet(provider, "sleep", role_dir, 9, None, None)
            )

            self.assertEqual(frames, [])
            # The cutout pass retries once before the sheet is given up on.
            self.assertEqual(len(provider.cutout_calls), 2)

    def test_an_empty_cell_rejects_the_sheet(self):
        with TemporaryDirectory() as temporary:
            role_dir = Path(temporary)
            provider = FakeSheetProvider(_sheet_image(5, 2, empty=(4,)))

            frames = _run(
                generate_role_from_sheet(provider, "walk", role_dir, 10, None, None)
            )

            self.assertEqual(frames, [])
            # Nothing was written for the caller to pick up by accident.
            self.assertEqual(list(role_dir.glob("walk_[0-9]*.png")), [])

    def test_a_cut_through_subject_rejects_the_sheet(self):
        with TemporaryDirectory() as temporary:
            role_dir = Path(temporary)
            # A subject that fills its cell runs into the cell border, so the
            # split would cut it in half.
            sheet = _sheet_image(2, 1, body=1.0)
            provider = FakeSheetProvider(sheet)

            frames = _run(
                generate_role_from_sheet(provider, "react", role_dir, 2, None, None)
            )

            self.assertEqual(frames, [])

    def test_a_mismatched_cell_rejects_the_sheet(self):
        with TemporaryDirectory() as temporary:
            role_dir = Path(temporary)
            sheet = _sheet_image(3, 1)
            draw = ImageDraw.Draw(sheet)
            # A much larger subject in the last cell.
            draw.ellipse((2 * CELL + 1, 1, 2 * CELL + CELL - 2, CELL - 2), fill=BODY)
            provider = FakeSheetProvider(sheet)

            frames = _run(
                generate_role_from_sheet(provider, "react", role_dir, 3, None, None)
            )

            self.assertEqual(frames, [])

    def test_the_grid_matches_the_requested_frame_count(self):
        with TemporaryDirectory() as temporary:
            role_dir = Path(temporary)
            provider = FakeSheetProvider(_sheet_image(4, 2))

            frames = _run(
                generate_role_from_sheet(provider, "walk", role_dir, 8, None, None)
            )

            self.assertEqual(grid_for(8), (4, 2))
            self.assertEqual(len(frames), 8)


if __name__ == "__main__":
    unittest.main()
