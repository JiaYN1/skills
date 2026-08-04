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


if __name__ == "__main__":
    unittest.main()
