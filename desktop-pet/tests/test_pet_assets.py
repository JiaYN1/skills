import tempfile
import unittest
from io import BytesIO
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    Image = None

from pet_assets import normalize_pet_image, normalize_pet_sequence, split_contact_sheet_bytes


@unittest.skipIf(Image is None, "Pillow 未安装")
class PetAssetTests(unittest.TestCase):
    def test_normalize_creates_square_transparent_png(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.png"
            destination = root / "assets" / "pet.png"

            image = Image.new("RGBA", (160, 100), (255, 255, 255, 255))
            for x in range(40, 120):
                for y in range(15, 85):
                    image.putpixel((x, y), (200, 80, 60, 255))
            image.save(source)

            normalize_pet_image(source, destination, canvas_size=128)
            with Image.open(destination) as normalized:
                self.assertEqual(normalized.size, (128, 128))
                self.assertEqual(normalized.mode, "RGBA")
                self.assertEqual(normalized.getpixel((0, 0))[3], 0)
                self.assertGreater(normalized.getbbox()[2], 60)

    def test_normalize_sequence_uses_one_canvas_and_cleans_transparent_rgb(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sources = []
            destinations = []
            for index in range(2):
                source = root / f"source-{index}.png"
                image = Image.new("RGBA", (160, 100), (255, 255, 255, 255))
                for x in range(40 + index * 4, 120 + index * 4):
                    for y in range(15, 85):
                        image.putpixel((x, y), (200, 80, 60, 255))
                image.save(source)
                sources.append(source)
                destinations.append(root / "assets" / f"frame-{index}.png")

            normalize_pet_sequence(
                sources,
                destinations,
                canvas_size=128,
                background_mode="simple",
                anchor="bottom",
            )

            for destination in destinations:
                with Image.open(destination) as frame:
                    self.assertEqual(frame.size, (128, 128))
                    self.assertEqual(frame.mode, "RGBA")
                    self.assertEqual(frame.getpixel((0, 0)), (0, 0, 0, 0))

    def test_normalize_sequence_rejects_opaque_ai_frame(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "opaque-ai-frame.png"
            destination = root / "assets" / "frame.png"
            Image.new("RGB", (80, 80), (255, 255, 255)).save(source)

            with self.assertRaisesRegex(ValueError, "没有透明 alpha 通道"):
                normalize_pet_sequence(
                    [source],
                    [destination],
                    canvas_size=64,
                    background_mode="none",
                    require_transparency=True,
                )

    def test_normalize_opaque_ai_frame_uses_simple_fallback(self):
        from PIL import ImageDraw

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "opaque-ai-frame.png"
            destination = root / "assets" / "frame.png"
            image = Image.new("RGB", (80, 80), (255, 255, 255))
            ImageDraw.Draw(image).ellipse((20, 12, 60, 68), fill=(180, 80, 60))
            image.save(source)

            normalize_pet_sequence(
                [source],
                [destination],
                canvas_size=64,
                background_mode="simple",
            )

            with Image.open(destination) as normalized:
                self.assertEqual(normalized.mode, "RGBA")
                self.assertEqual(normalized.getpixel((0, 0))[3], 0)

    def test_normalize_clears_green_chroma_edge_next_to_alpha(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "green-matte.png"
            destination = root / "assets" / "frame.png"
            image = Image.new("RGBA", (80, 80), (0, 0, 0, 0))
            for x in range(25, 55):
                for y in range(20, 60):
                    image.putpixel((x, y), (190, 80, 60, 255))
            image.putpixel((24, 40), (10, 230, 40, 255))
            image.save(source)

            normalize_pet_sequence(
                [source],
                [destination],
                canvas_size=64,
                background_mode="none",
            )

            with Image.open(destination) as normalized:
                green_pixels = [
                    pixel
                    for pixel in normalized.convert("RGBA").getdata()
                    if pixel[3] > 0 and pixel[1] > pixel[0] + 30 and pixel[1] > pixel[2] + 20
                ]
                self.assertEqual(green_pixels, [])

    def test_split_contact_sheet_returns_individual_frames(self):
        from PIL import ImageDraw

        sheet = Image.new("RGBA", (480, 240), (255, 255, 255, 255))
        draw = ImageDraw.Draw(sheet)
        for index in range(8):
            row, column = divmod(index, 4)
            left = column * 120
            top = row * 120
            color = ((index * 31) % 256, (index * 67) % 256, (index * 97) % 256, 255)
            draw.rectangle((left, top, left + 119, top + 119), fill=color)
            draw.ellipse((left + 24, top + 24, left + 96, top + 96), fill=(255, 255, 255, 255))

        encoded = BytesIO()
        sheet.save(encoded, format="PNG")
        frames = split_contact_sheet_bytes(encoded.getvalue(), 8)

        self.assertEqual(len(frames), 8)
        with Image.open(BytesIO(frames[0])) as frame:
            self.assertEqual(frame.size, (120, 120))

    def test_split_flat_background_contact_sheet(self):
        from PIL import ImageDraw

        sheet = Image.new("RGBA", (480, 240), (255, 255, 255, 255))
        draw = ImageDraw.Draw(sheet)
        for index in range(8):
            row, column = divmod(index, 4)
            left = column * 120
            top = row * 120
            draw.ellipse(
                (left + 30, top + 24, left + 90, top + 96),
                fill=((index * 31) % 256, 100, 180, 255),
            )

        encoded = BytesIO()
        sheet.save(encoded, format="PNG")
        frames = split_contact_sheet_bytes(encoded.getvalue(), 8)

        self.assertEqual(len(frames), 8)

    def test_split_vertical_transparent_contact_sheet(self):
        from PIL import ImageDraw

        sheet = Image.new("RGBA", (240, 480), (0, 0, 0, 0))
        draw = ImageDraw.Draw(sheet)
        for index in range(8):
            row, column = divmod(index, 2)
            left = column * 120
            top = row * 120
            draw.ellipse(
                (left + 28, top + 28, left + 92, top + 92),
                fill=((index * 29) % 256, 120, 210, 255),
            )

        encoded = BytesIO()
        sheet.save(encoded, format="PNG")
        frames = split_contact_sheet_bytes(encoded.getvalue(), 8)

        self.assertEqual(len(frames), 8)
        with Image.open(BytesIO(frames[-1])) as frame:
            self.assertEqual(frame.size, (120, 120))

    def test_single_frame_is_not_split_as_a_contact_sheet(self):
        from PIL import ImageDraw

        image = Image.new("RGBA", (480, 480), (255, 255, 255, 255))
        ImageDraw.Draw(image).ellipse((120, 70, 360, 410), fill=(190, 90, 70, 255))
        encoded = BytesIO()
        image.save(encoded, format="PNG")

        frames = split_contact_sheet_bytes(encoded.getvalue(), 8)

        self.assertEqual(len(frames), 1)


BODY = (200, 120, 60, 255)
LEG = (120, 80, 40, 255)
MARKER = (0, 0, 255, 255)

TORSO_WIDTH = 280
TORSO_HEIGHT = 320
MARKER_OFFSET = 90
MARKER_SIZE = 100


def _pose_frame(path, width, height, leg=None, origin=20):
    """Build a frame whose torso and blue marker never move between poses.

    Only ``leg`` and the torso ``origin`` change, which is exactly what a walk
    cycle does: the subject's bounding box grows and shifts while the body
    itself keeps the same size, shape, and position.
    """

    from PIL import ImageDraw

    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((origin, 0, origin + TORSO_WIDTH - 1, TORSO_HEIGHT - 1), fill=BODY)
    draw.rectangle(
        (origin + MARKER_OFFSET, 100, origin + MARKER_OFFSET + MARKER_SIZE - 1, 199),
        fill=MARKER,
    )
    if leg:
        draw.rectangle(leg, fill=LEG)
    image.save(path)
    return path


# Four poses of one cycle, all on the same source canvas the image model is
# asked for, so the sequence really is registrable. Every limb is thin enough
# that it must not be mistaken for the body, every source is larger than the
# target extent so a per-frame fit would visibly rescale the pet, and the last
# pose shifts the whole subject inside its canvas so alignment has to recover a
# translation as well as a growth.
STABILITY_POSES = [
    ((400, 400), (301, 230, 399, 319), 20),  # limb reaching right
    ((400, 400), None, 20),  # compact body
    ((400, 400), (100, 320, 140, 379), 20),  # paw stepping below the body
    ((400, 400), (20, 230, 99, 319), 100),  # limb reaching left, subject shifted
]


def _marker_box(path):
    """Return the canvas box of the opaque blue body marker.

    The alpha channel is part of the test on purpose: resampling leaves stray
    ``(0, 0, 255, 1)`` pixels along the marker edge, and those must not be
    mistaken for the marker itself.
    """

    from PIL import ImageChops

    with Image.open(path) as frame:
        red, _green, blue, alpha = frame.convert("RGBA").split()
        marker = ImageChops.multiply(
            ImageChops.multiply(
                blue.point(lambda value: 255 if value > 200 else 0),
                red.point(lambda value: 255 if value < 60 else 0),
            ),
            alpha.point(lambda value: 255 if value > 200 else 0),
        )
        return marker.getbbox()


@unittest.skipIf(Image is None, "Pillow 未安装")
class SequenceStabilityTests(unittest.TestCase):
    """Frame-to-frame stability is the whole point of sequence normalization."""

    def _normalize(self, root, poses=STABILITY_POSES):
        root.mkdir(parents=True, exist_ok=True)
        sources = [
            _pose_frame(root / f"pose-{index}.png", *size, leg=leg, origin=origin)
            for index, (size, leg, origin) in enumerate(poses)
        ]
        destinations = [root / f"out-{index}.png" for index in range(len(sources))]
        normalize_pet_sequence(
            sources,
            destinations,
            canvas_size=320,
            background_mode="none",
            anchor="bottom",
            subject_scale=0.96,
        )
        return destinations

    def test_stretched_limb_does_not_rescale_the_body(self):
        with tempfile.TemporaryDirectory() as temporary:
            destinations = self._normalize(Path(temporary))
            boxes = [_marker_box(path) for path in destinations]

            widths = {box[2] - box[0] for box in boxes}
            heights = {box[3] - box[1] for box in boxes}

            self.assertLessEqual(max(widths) - min(widths), 1, f"body width jittered: {boxes}")
            self.assertLessEqual(max(heights) - min(heights), 1, f"body height jittered: {boxes}")

    def test_stretched_limb_does_not_slide_or_bob_the_body(self):
        with tempfile.TemporaryDirectory() as temporary:
            destinations = self._normalize(Path(temporary))
            boxes = [_marker_box(path) for path in destinations]

            centers_x = {(box[0] + box[2]) / 2 for box in boxes}
            centers_y = {(box[1] + box[3]) / 2 for box in boxes}

            self.assertLessEqual(max(centers_x) - min(centers_x), 1, f"body slid: {boxes}")
            self.assertLessEqual(max(centers_y) - min(centers_y), 1, f"body bobbed: {boxes}")

    def test_body_keeps_a_legible_share_of_the_canvas(self):
        with tempfile.TemporaryDirectory() as temporary:
            destinations = self._normalize(Path(temporary))

            for path in destinations:
                box = _marker_box(path)
                with Image.open(path) as frame:
                    self.assertGreater(box[2] - box[0], frame.width * 0.15)

    def test_sequence_output_never_leaves_the_canvas(self):
        with tempfile.TemporaryDirectory() as temporary:
            destinations = self._normalize(Path(temporary))

            for path in destinations:
                with Image.open(path) as frame:
                    self.assertEqual(frame.size, (320, 320))
                    bbox = frame.convert("RGBA").getchannel("A").getbbox()
                    self.assertGreaterEqual(bbox[0], 0)
                    self.assertGreaterEqual(bbox[1], 0)
                    self.assertLessEqual(bbox[2], 320)
                    self.assertLessEqual(bbox[3], 320)

    def test_mixed_source_sizes_fall_back_to_per_frame_anchoring(self):
        """Frames that share no source canvas still share one render scale.

        Loose still photos of different sizes cannot be registered against each
        other, so each frame must be anchored by its own alpha box instead of by
        a translation computed from unrelated images.
        """

        poses = [
            ((400, 320), (301, 230, 399, 319), 20),  # limb reaching right
            ((500, 500), None, 20),  # a photo with a different canvas size
        ]
        with tempfile.TemporaryDirectory() as temporary:
            destinations = self._normalize(Path(temporary), poses=poses)
            boxes = [_marker_box(path) for path in destinations]

            widths = {box[2] - box[0] for box in boxes}
            centers_x = {(box[0] + box[2]) / 2 for box in boxes}
            centers_y = {(box[1] + box[3]) / 2 for box in boxes}

            self.assertLessEqual(max(widths) - min(widths), 1, f"body width jittered: {boxes}")
            self.assertLessEqual(
                max(centers_x) - min(centers_x), 1, f"body slid: {boxes}"
            )
            self.assertLessEqual(
                max(centers_y) - min(centers_y), 1, f"body bobbed: {boxes}"
            )

    def test_homogeneous_sequence_does_not_depend_on_the_frame_count(self):
        """Repeating one pose must not change its scale or its placement.

        This is the contract behind "one scale for the whole sequence": adding
        more frames of the same size may not resize or move the pet, otherwise
        every extra frame would reintroduce the jitter this module removes.
        """

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            one = self._normalize(root / "one", poses=[STABILITY_POSES[1]])
            three = self._normalize(root / "three", poses=[STABILITY_POSES[1]] * 3)

            single = _marker_box(one[0])
            repeated = [_marker_box(path) for path in three]

            self.assertEqual(
                {abs(box[2] - box[0]) for box in repeated} | {single[2] - single[0]},
                {single[2] - single[0]},
                f"repeating a pose changed its size: {single} {repeated}",
            )
            for box in repeated:
                self.assertLessEqual(
                    abs(box[0] - single[0]) + abs(box[1] - single[1]),
                    1,
                    f"repeating a pose moved it: {single} {repeated}",
                )


if __name__ == "__main__":
    unittest.main()
