import json
import tempfile
import unittest
from pathlib import Path

from pet_animation import (
    build_animation_manifest,
    build_skeleton_manifest,
    pose_plan_for,
    write_animation_bundle,
)


class PetAnimationTests(unittest.TestCase):
    def test_walk_and_sleep_have_ordered_pose_plans(self):
        walk = pose_plan_for("walk", 8)
        sleep = pose_plan_for("sleep", 8)

        self.assertEqual(len(walk), 8)
        self.assertEqual(len(sleep), 8)
        self.assertIn("contact", walk[0])
        self.assertIn("breathing", sleep[1])

    def test_hybrid_manifest_contains_sequences_and_skeleton_path(self):
        assets = {
            "idle": ["assets/idle_0.png"],
            "walk": ["assets/walk_%d.png" % index for index in range(8)],
            "sleep": ["assets/sleep_%d.png" % index for index in range(8)],
            "react": ["assets/react_0.png"],
        }

        manifest = build_animation_manifest(assets, mode="hybrid", fps=12)
        skeleton = build_skeleton_manifest(assets, fps=12)

        self.assertEqual(manifest["mode"], "hybrid")
        self.assertEqual(manifest["fps"], 12)
        self.assertEqual(len(manifest["sequences"]["walk"]["frames"]), 8)
        self.assertEqual(manifest["skeleton_path"], "skeleton.json")
        self.assertEqual(skeleton["format"], "desktop-pet-skeleton")
        self.assertEqual(skeleton["animations"]["sleep"]["fps"], 12)
        self.assertEqual(len(skeleton["animations"]["walk"]["frames"]), 8)

    def test_write_bundle_persists_json_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary)
            assets = {"walk": ["assets/walk_0.png", "assets/walk_1.png"]}
            manifest = write_animation_bundle(package, assets, mode="hybrid")

            self.assertTrue((package / "animation.json").is_file())
            self.assertTrue((package / "skeleton.json").is_file())
            saved = json.loads((package / "animation.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["skeleton_path"], manifest["skeleton_path"])


if __name__ == "__main__":
    unittest.main()
