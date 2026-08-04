import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from server.app.package_builder import ai_cutout_frame, build_pet_package


class FakeFrameProvider:
    available = True

    def __init__(self):
        self.cutout_calls = []

    async def generate_action_frames(self, references, role, output_dir, **_kwargs):
        output_dir.mkdir(parents=True, exist_ok=True)
        target = output_dir / f"{role}_0.png"
        shutil.copy2(references[0], target)
        return [target]

    async def remove_background(self, source, output_path):
        self.cutout_calls.append((source, output_path))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, output_path)
        return output_path


class RetryCutoutProvider:
    available = True

    def __init__(self):
        self.calls = 0

    async def remove_background(self, _source, output_path):
        self.calls += 1
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if self.calls == 1:
            Image.new("RGB", (32, 32), (255, 255, 255)).save(output_path)
        else:
            image = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
            image.putpixel((16, 16), (180, 80, 60, 255))
            image.save(output_path)
        return output_path


class PackageBuilderTests(unittest.IsolatedAsyncioTestCase):
    async def test_cutout_pass_retries_when_alpha_is_missing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.png"
            Image.new("RGBA", (32, 32), (0, 0, 0, 0)).save(source)
            provider = RetryCutoutProvider()

            result = await ai_cutout_frame(provider, source, root / "cutout.png")

            self.assertEqual(provider.calls, 2)
            self.assertEqual(result.name, "cutout_retry.png")

    async def test_ai_action_frames_get_a_second_cutout_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "pet.png"
            image = Image.new("RGBA", (80, 80), (0, 0, 0, 0))
            for x in range(20, 60):
                for y in range(15, 65):
                    image.putpixel((x, y), (180, 80, 60, 255))
            image.save(source)

            settings = SimpleNamespace(
                ai_frame_count=1,
                animation_mode="hybrid",
                animation_fps=12,
                pose_consistency=True,
                frame_count_for_role=lambda _role: 1,
            )
            provider = FakeFrameProvider()
            result = await build_pet_package(
                root / "job",
                "Test Pet",
                [source],
                provider,
                settings,
                lambda _value, _message: None,
            )

            self.assertEqual(len(provider.cutout_calls), 4)
            self.assertTrue(all(path.name.endswith("_cutout.png") for _, path in provider.cutout_calls))
            self.assertTrue(Path(result["zip_path"]).is_file())
            self.assertTrue(all("raw_source_path" in item for item in result["resource_frame_meta"]))


if __name__ == "__main__":
    unittest.main()
