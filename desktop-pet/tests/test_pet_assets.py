import tempfile
import unittest
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    Image = None

from pet_assets import normalize_pet_image


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


if __name__ == "__main__":
    unittest.main()
