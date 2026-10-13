"""Tests for the sprite-atlas grid helpers and the package CLI."""

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

import pet_atlas
from pet_atlas import main
from pet_animation import write_animation_bundle

CELL = 96
ROLES = ("idle", "walk", "sleep", "react")


def _sprite(width: int = CELL, height: int = CELL, scale: float = 0.5, offset=(0, 0)):
    """Return one transparent cell with a centered subject."""

    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    body = int(min(width, height) * scale)
    left = (width - body) // 2 + offset[0]
    top = (height - body) // 2 + offset[1]
    draw.ellipse((left, top, left + body - 1, top + body - 1), fill=(200, 130, 70, 255))
    return image


def _sheet(columns: int, rows: int, *, empty=(), sizes=None, touching=(), offset=(0, 0)):
    """Build a sheet from per-cell sprites."""

    sizes = sizes or {}
    sheet = Image.new("RGBA", (columns * CELL, rows * CELL), (0, 0, 0, 0))
    for row in range(rows):
        for column in range(columns):
            index = row * columns + column
            if index in empty:
                continue
            cell_offset = offset if index in touching else (0, 0)
            sheet.paste(
                _sprite(CELL, CELL, sizes.get(index, 0.5), cell_offset),
                (column * CELL, row * CELL),
            )
    return sheet


class GridLayoutTests(unittest.TestCase):
    def test_known_frame_counts_get_a_compact_grid(self):
        self.assertEqual(pet_atlas.grid_for(1), (1, 1))
        self.assertEqual(pet_atlas.grid_for(8), (4, 2))
        self.assertEqual(pet_atlas.grid_for(9), (3, 3))
        self.assertEqual(pet_atlas.grid_for(10), (5, 2))
        self.assertEqual(pet_atlas.grid_for(12), (4, 3))

    def test_unknown_frame_counts_still_fit_every_cell(self):
        for count in (13, 17, 19, 23, 27, 31):
            columns, rows = pet_atlas.grid_for(count)
            self.assertGreaterEqual(columns * rows, count)

    def test_cells_tile_the_sheet_without_gaps(self):
        image = Image.new("RGBA", (CELL * 5, CELL * 2), (0, 0, 0, 0))
        boxes = [
            pet_atlas.cell_bounds(image, 5, 2, column, row)
            for row in range(2)
            for column in range(5)
        ]

        self.assertEqual(boxes[0], (0, 0, CELL, CELL))
        self.assertEqual(boxes[-1], (CELL * 4, CELL, CELL * 5, CELL * 2))
        for column, box in enumerate(boxes[:5]):
            self.assertEqual(box[0], column * CELL)
            self.assertEqual(box[2], (column + 1) * CELL)

    def test_split_atlas_returns_reading_order(self):
        sheet = _sheet(5, 2, sizes={3: 0.7})
        frames = pet_atlas.split_atlas(sheet, 5, 2, 10)

        self.assertEqual(len(frames), 10)
        self.assertEqual(frames[0].size, (CELL, CELL))
        # Only the fourth cell was drawn larger.
        masses = [
            sum(frame.getchannel("A").getdata()) for frame in frames
        ]
        self.assertEqual(masses.index(max(masses)), 3)


class GridCheckTests(unittest.TestCase):
    def test_a_clean_sheet_passes(self):
        report = pet_atlas.grid_check(_sheet(5, 2), 5, 2, 10)

        self.assertTrue(report["ok"], report["problems"])
        self.assertEqual(report["cell_size"], [CELL, CELL])
        self.assertEqual(len(report["cells"]), 10)
        self.assertTrue(report["has_alpha"])

    def test_an_empty_cell_is_reported(self):
        report = pet_atlas.grid_check(_sheet(5, 2, empty=(6,)), 5, 2, 10)

        self.assertFalse(report["ok"])
        self.assertTrue(any("第 7 格" in problem for problem in report["problems"]))

    def test_content_crossing_a_cell_edge_is_reported(self):
        report = pet_atlas.grid_check(_sheet(3, 1, touching=(1,), offset=(CELL // 2, 0)), 3, 1, 3)

        self.assertFalse(report["ok"])
        self.assertTrue(any("边缘" in problem for problem in report["problems"]))

    def test_a_sheet_without_alpha_cannot_be_checked(self):
        sheet = Image.new("RGBA", (CELL * 2, CELL), (255, 255, 255, 255))
        report = pet_atlas.grid_check(sheet, 2, 1, 2)

        self.assertFalse(report["ok"])
        self.assertFalse(report["has_alpha"])
        self.assertTrue(any("透明" in problem for problem in report["problems"]))

    def test_a_mismatched_cell_is_reported(self):
        report = pet_atlas.grid_check(_sheet(3, 1, sizes={2: 0.95}), 3, 1, 3)

        self.assertFalse(report["ok"])
        self.assertTrue(any("大小" in problem for problem in report["problems"]))

    def test_a_grid_that_is_too_fine_is_reported(self):
        # 320 px shared out over 8x8 leaves 40 px cells, too coarse to draw into.
        report = pet_atlas.grid_check(Image.new("RGBA", (320, 320), (0, 0, 0, 0)), 8, 8, 64)

        self.assertFalse(report["ok"])
        self.assertTrue(any("格子太小" in problem for problem in report["problems"]))


class MirrorAndTrimTests(unittest.TestCase):
    def test_mirror_rows_flips_each_row_in_place(self):
        sheet = Image.new("RGBA", (CELL * 2, CELL * 2), (0, 0, 0, 0))
        draw = ImageDraw.Draw(sheet)
        # Unique markers: red at the top-left, green at the bottom-right.
        draw.rectangle((0, 0, 8, 8), fill=(255, 0, 0, 255))
        draw.rectangle((CELL + 88, CELL + 88, CELL * 2 - 1, CELL * 2 - 1), fill=(0, 255, 0, 255))

        mirrored = pet_atlas.mirror_rows(sheet, 2)

        self.assertEqual(mirrored.size, sheet.size)
        # Each row was flipped horizontally: x -> width - 1 - x.
        self.assertEqual(mirrored.getpixel((CELL * 2 - 1, 4))[:3], (255, 0, 0))
        self.assertEqual(mirrored.getpixel((0, CELL + 92))[:3], (0, 255, 0))
        # The row layout itself is untouched: the second row starts empty.
        self.assertEqual(mirrored.getpixel((0, CELL))[3], 0)

    def test_trim_cell_removes_transparent_border(self):
        cell = _sprite(CELL, CELL, 0.5)
        cell.paste(_sprite(CELL, CELL, 0.5), (0, 0))

        trimmed = pet_atlas.trim_cell(cell)

        self.assertLess(trimmed.width, cell.width)
        self.assertLess(trimmed.height, cell.height)


class CliTests(unittest.TestCase):
    def test_grid_check_exit_codes_follow_the_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            good = Path(temporary) / "good.png"
            bad = Path(temporary) / "bad.png"
            _sheet(2, 1).save(good)
            _sheet(2, 1, empty=(1,)).save(bad)

            self.assertEqual(
                main(["grid-check", "--image", str(good), "--frames", "2"]), 0
            )
            self.assertEqual(
                main(["grid-check", "--image", str(bad), "--frames", "2"]), 1
            )

    def test_split_writes_numbered_frames(self):
        with tempfile.TemporaryDirectory() as temporary:
            sheet_path = Path(temporary) / "walk.png"
            _sheet(5, 2).save(sheet_path)
            out = Path(temporary) / "frames"

            code = main(
                [
                    "split",
                    "--image",
                    str(sheet_path),
                    "--frames",
                    "10",
                    "--out",
                    str(out),
                    "--prefix",
                    "walk",
                ]
            )

            self.assertEqual(code, 0)
            written = sorted(path.name for path in out.glob("*.png"))
            self.assertEqual(len(written), 10)
            self.assertEqual(written[0], "walk_0.png")
            with Image.open(out / "walk_0.png") as frame:
                self.assertEqual(frame.size, (CELL, CELL))

    def test_split_refuses_a_broken_grid_unless_forced(self):
        with tempfile.TemporaryDirectory() as temporary:
            sheet_path = Path(temporary) / "broken.png"
            _sheet(2, 1, empty=(1,)).save(sheet_path)
            out = Path(temporary) / "frames"

            self.assertEqual(
                main(["split", "--image", str(sheet_path), "--frames", "2", "--out", str(out)]),
                1,
            )
            self.assertFalse(out.exists())

            self.assertEqual(
                main(
                    [
                        "split",
                        "--image",
                        str(sheet_path),
                        "--frames",
                        "2",
                        "--out",
                        str(out),
                        "--force",
                    ]
                ),
                0,
            )
            self.assertEqual(len(list(out.glob("*.png"))), 2)

    def test_flip_rows_writes_a_mirrored_sheet(self):
        with tempfile.TemporaryDirectory() as temporary:
            sheet_path = Path(temporary) / "walk.png"
            _sheet(2, 1).save(sheet_path)
            out = Path(temporary) / "walk_mirrored.png"

            code = main(
                ["flip-rows", "--image", str(sheet_path), "--rows", "1", "--out", str(out)]
            )

            self.assertEqual(code, 0)
            with Image.open(out) as image:
                self.assertEqual(image.size, (CELL * 2, CELL))

    def test_validate_reports_a_clean_package(self):
        with tempfile.TemporaryDirectory() as temporary:
            package_dir = Path(temporary) / "pet"
            assets_dir = package_dir / "assets"
            assets_dir.mkdir(parents=True)
            source_dir = Path(temporary) / "sources"
            source_dir.mkdir()

            assets = {}
            for role in ROLES:
                count = 4 if role in {"walk", "sleep"} else 1
                paths = []
                for index in range(count):
                    source = source_dir / f"{role}_{index}.png"
                    _sprite(320, 320, 0.5, offset=(index - 2, 0)).save(source)
                    asset = assets_dir / f"{role}_{index}.png"
                    _sprite(320, 320, 0.5, offset=(index - 2, 0)).save(asset)
                    paths.append(f"assets/{asset.name}")
                assets[role] = paths

            write_animation_bundle(package_dir, assets, mode="png", fps=12)
            (package_dir / "pet_config.json").write_text(
                json.dumps(
                    {
                        "name": "pet",
                        "assets": assets,
                        "generation": {"frame_counts": {role: len(items) for role, items in assets.items()}},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            self.assertEqual(main(["validate", "--package", str(package_dir)]), 0)
            self.assertEqual(
                main(["repack", "--package", str(package_dir)]), 0
            )
            # repack re-normalized the walk frames in place.
            with Image.open(assets_dir / "walk_0.png") as frame:
                self.assertEqual(frame.size, (320, 320))


if __name__ == "__main__":
    unittest.main()
