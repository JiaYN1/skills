import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from PIL import Image

from pet_animation import write_animation_bundle
from server.app.storage import JobStore
import server.app.main as main


class ResourceFrameEditTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.original_store = main.store
        main.store = JobStore(Path(self.temporary.name))

    def tearDown(self):
        main.store = self.original_store
        self.temporary.cleanup()

    def test_remove_frame_reindexes_assets_and_manifests(self):
        record = main.store.new_job("pet", 1, False)
        job_dir = main.store.job_dir(record["id"])
        package_dir = job_dir / "package"
        assets_dir = package_dir / "assets"
        source_dir = job_dir / "ai" / "walk"
        assets_dir.mkdir(parents=True)
        source_dir.mkdir(parents=True)

        asset_paths = []
        metadata = []
        for index, color in enumerate(((180, 60, 40), (40, 160, 80), (50, 80, 190))):
            source = source_dir / f"walk_{index}.png"
            source_image = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
            for x in range(8, 32):
                for y in range(8, 32):
                    source_image.putpixel((x, y), color + (255,))
            source_image.save(source)
            asset = assets_dir / f"walk_{index}.png"
            Image.new("RGBA", (320, 320), color + (255,)).save(asset)
            asset_paths.append(f"assets/walk_{index}.png")
            metadata.append(
                {
                    "asset_path": f"package/assets/walk_{index}.png",
                    "source_path": f"ai/walk/walk_{index}.png",
                    "raw_source_path": f"ai/walk/walk_{index}.png",
                    "role": "walk",
                    "index": index,
                    "frame_count": 3,
                }
            )

        animation = write_animation_bundle(
            package_dir,
            {"walk": asset_paths},
            mode="hybrid",
            fps=10,
        )
        config_path = package_dir / "pet_config.json"
        config_path.write_text(
            json.dumps(
                {
                    "name": "pet",
                    "assets": {"walk": asset_paths},
                    "animation": animation,
                    "generation": {"frame_counts": {"walk": 3}},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        zip_path = job_dir / "pet.zip"
        main._rebuild_package_archive(package_dir, zip_path)
        main.store.update(
            record["id"],
            status="ready",
            package_path=str(zip_path),
            artifact_path=str(zip_path),
            artifact_kind="zip",
            resource_preview_paths=[item["asset_path"] for item in metadata],
            resource_frame_meta=metadata,
        )

        main._run_resource_frame_removal(record["id"], {"walk": [1]})

        updated = main.store.read(record["id"])
        self.assertEqual(updated["status"], "ready")
        self.assertEqual([item["index"] for item in updated["resource_frame_meta"]], [0, 1])
        self.assertEqual(
            updated["resource_frame_meta"][1]["source_path"],
            "ai/walk/walk_2.png",
        )
        saved_config = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(
            saved_config["animation"]["sequences"]["walk"]["frames"],
            ["assets/walk_0.png", "assets/walk_1.png"],
        )
        with zipfile.ZipFile(zip_path) as archive:
            self.assertIn("assets/walk_0.png", archive.namelist())
            self.assertIn("assets/walk_1.png", archive.namelist())
            self.assertNotIn("assets/walk_2.png", archive.namelist())


if __name__ == "__main__":
    unittest.main()
