"""End-to-end tests for the quality-aware single-frame edit flows."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

import server.app.main as main
from pet_animation import write_animation_bundle
from server.app.storage import JobStore

BODY = (200, 130, 70, 255)


def _draw_frame(path: Path, *, body_scale: float = 1.0, leg_shift: int = 0) -> Path:
    """Draw one source frame on the shared 400x400 canvas."""

    image = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    size = int(240 * body_scale)
    left = 80 + (240 - size) // 2
    top = 80 + (240 - size) // 2
    draw.ellipse((left, top, left + size - 1, top + size - 1), fill=BODY)
    draw.rectangle((150 + leg_shift, 320, 190 + leg_shift, 390), fill=(120, 80, 40, 255))
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return path


class FakeProvider:
    """A stand-in image provider that records how it was asked to generate."""

    calls = []
    body_scale = 1.0
    recover_on_retry = True

    def __init__(self, settings):
        self.settings = settings

    async def generate_action_frame(
        self,
        references,
        role,
        output_path,
        identity_reference=None,
        frame_index=0,
        frame_count=None,
        pose_consistency=True,
        continuity_reference=False,
        next_reference=False,
        quality_hint="",
    ):
        FakeProvider.calls.append(
            {
                "references": [Path(path) for path in references],
                "role": role,
                "identity_reference": (
                    Path(identity_reference) if identity_reference is not None else None
                ),
                "frame_index": int(frame_index),
                "continuity_reference": bool(continuity_reference),
                "next_reference": bool(next_reference),
                "quality_hint": str(quality_hint),
            }
        )
        scale = FakeProvider.body_scale
        if quality_hint and FakeProvider.recover_on_retry:
            # A turned-down retry is expected to do better.
            scale = 1.0
        return _draw_frame(Path(output_path), body_scale=scale)


async def _passthrough_cutout(provider, source, output_path):
    return Path(source)


class ResourceFrameQualityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.original_store = main.store
        self.original_provider = main.OpenAICompatibleImageProvider
        self.original_cutout = main.ai_cutout_frame
        main.store = JobStore(Path(self.temporary.name))
        main.OpenAICompatibleImageProvider = FakeProvider
        main.ai_cutout_frame = _passthrough_cutout
        FakeProvider.calls = []
        FakeProvider.body_scale = 1.0
        FakeProvider.recover_on_retry = True

    def tearDown(self):
        main.store = self.original_store
        main.OpenAICompatibleImageProvider = self.original_provider
        main.ai_cutout_frame = self.original_cutout
        self.temporary.cleanup()

    def _build_job(self, frame_count: int = 4):
        record = main.store.new_job("pet", 1, False)
        job_id = record["id"]
        job_dir = main.store.job_dir(job_id)
        package_dir = job_dir / "package"
        assets_dir = package_dir / "assets"
        source_dir = job_dir / "ai" / "walk"
        assets_dir.mkdir(parents=True)
        source_dir.mkdir(parents=True)

        preview = _draw_frame(job_dir / "prepared" / "prepared_0.png")
        asset_paths = []
        metadata = []
        contracts = {}
        for index in range(frame_count):
            source = _draw_frame(source_dir / f"walk_{index}.png", leg_shift=index * 4 - 6)
            asset = assets_dir / f"walk_{index}.png"
            Image.new("RGBA", (320, 320), BODY).save(asset)
            asset_paths.append(f"assets/walk_{index}.png")
            metadata.append(
                {
                    "asset_path": f"package/assets/walk_{index}.png",
                    "source_path": f"ai/walk/walk_{index}.png",
                    "raw_source_path": f"ai/walk/walk_{index}.png",
                    "role": "walk",
                    "index": index,
                    "frame_count": frame_count,
                }
            )

        # Normalize once so the recorded contract is a real one and the stored
        # assets are the normalized frames a real job would have shipped.
        report: dict = {}
        main.normalize_pet_sequence(
            [job_dir / item["source_path"] for item in metadata],
            [job_dir / item["asset_path"] for item in metadata],
            canvas_size=320,
            background_mode="simple",
            anchor="bottom",
            subject_scale=0.96,
            report=report,
        )
        contracts["walk"] = {
            "canvas_size": report.get("canvas_size"),
            "render_scale": report.get("render_scale"),
            "reference_index": report.get("reference_index"),
            "placements": report.get("placements"),
        }
        animation = write_animation_bundle(package_dir, {"walk": asset_paths}, mode="hybrid", fps=12)
        (package_dir / "pet_config.json").write_text(
            json.dumps(
                {
                    "name": "pet",
                    "assets": {"walk": asset_paths},
                    "animation": animation,
                    "generation": {"frame_counts": {"walk": frame_count}},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        zip_path = job_dir / "pet.zip"
        main._rebuild_package_archive(package_dir, zip_path)
        main.store.update(
            job_id,
            status="ready",
            package_path=str(zip_path),
            artifact_path=str(zip_path),
            artifact_kind="zip",
            preview_paths=[main._relative_job_path(job_dir, preview)],
            resource_preview_paths=[item["asset_path"] for item in metadata],
            resource_frame_meta=metadata,
            sequence_contract=contracts,
        )
        return job_id, job_dir, package_dir, metadata

    def test_regeneration_feeds_identity_and_both_neighbours(self):
        job_id, job_dir, _package_dir, metadata = self._build_job()

        asyncio.run(
            main._run_resource_regeneration(job_id, {"role": "walk", "index": 1})
        )

        self.assertEqual(len(FakeProvider.calls), 1)
        call = FakeProvider.calls[0]
        self.assertEqual(
            call["references"],
            [
                (job_dir / "ai" / "walk" / "walk_0.png").resolve(),
                (job_dir / "ai" / "walk" / "walk_2.png").resolve(),
            ],
        )
        self.assertEqual(
            call["identity_reference"],
            (job_dir / "prepared" / "prepared_0.png").resolve(),
        )
        self.assertTrue(call["continuity_reference"])
        self.assertTrue(call["next_reference"])

        updated = main.store.read(job_id)
        self.assertEqual(updated["status"], "ready")
        self.assertEqual(
            [item["index"] for item in updated["resource_frame_meta"]], [0, 1, 2, 3]
        )
        self.assertIn("walk", updated["sequence_contract"])
        self.assertEqual(
            updated["resource_frame_meta"][1]["role"], "walk"
        )
        del metadata

    def test_pinned_edit_keeps_the_other_frames_byte_identical(self):
        job_id, _job_dir, package_dir, _metadata = self._build_job()
        before = {
            index: (package_dir / "assets" / f"walk_{index}.png").read_bytes()
            for index in (0, 2, 3)
        }
        # A replacement frame much larger than the rest would, without the
        # contract, shrink and shift the whole action.
        FakeProvider.body_scale = 1.7

        asyncio.run(
            main._run_resource_regeneration(job_id, {"role": "walk", "index": 1})
        )

        # The oversized frame failed the quality check, so it was retried once,
        # and the turned-down retry passed.
        self.assertEqual(len(FakeProvider.calls), 2)
        self.assertNotEqual(FakeProvider.calls[1]["quality_hint"], "")
        after = {
            index: (package_dir / "assets" / f"walk_{index}.png").read_bytes()
            for index in (0, 2, 3)
        }
        self.assertEqual(before, after)

        updated = main.store.read(job_id)
        edited = [item for item in updated["resource_frame_meta"] if item["index"] == 1][0]
        self.assertEqual(edited["quality_flags"], [])
        self.assertIn("重新生成", updated["message"])

    def test_flagging_survives_a_retry_that_still_fails(self):
        job_id, _job_dir, _package_dir, _metadata = self._build_job()
        FakeProvider.body_scale = 1.7
        FakeProvider.recover_on_retry = False

        asyncio.run(
            main._run_resource_regeneration(job_id, {"role": "walk", "index": 2})
        )

        updated = main.store.read(job_id)
        edited = [item for item in updated["resource_frame_meta"] if item["index"] == 2][0]
        self.assertTrue(edited["quality_flags"])
        self.assertIn("body-scale", edited["quality_flags"])
        self.assertIn("仍有标记", updated["message"])

        entries = main._resource_preview_entries(
            job_id,
            updated["resource_preview_paths"],
            updated["resource_frame_meta"],
        )
        flagged = [entry for entry in entries if entry.get("quality_flags")]
        self.assertEqual([entry["index"] for entry in flagged], [2])
        self.assertTrue(flagged[0]["quality_notes"])

    def test_insertion_uses_both_neighbours_and_the_contract(self):
        job_id, job_dir, package_dir, _metadata = self._build_job()
        before = {
            index: (package_dir / "assets" / f"walk_{index}.png").read_bytes()
            for index in range(4)
        }

        asyncio.run(main._run_resource_frame_insertion(job_id, "walk", 1))

        self.assertEqual(len(FakeProvider.calls), 1)
        call = FakeProvider.calls[0]
        self.assertEqual(
            call["references"],
            [
                (job_dir / "ai" / "walk" / "walk_1.png").resolve(),
                (job_dir / "ai" / "walk" / "walk_2.png").resolve(),
            ],
        )
        self.assertEqual(
            call["identity_reference"],
            (job_dir / "prepared" / "prepared_0.png").resolve(),
        )
        self.assertTrue(call["continuity_reference"])
        self.assertTrue(call["next_reference"])

        updated = main.store.read(job_id)
        self.assertEqual(len(updated["resource_frame_meta"]), 5)
        self.assertEqual(
            [item["index"] for item in updated["resource_frame_meta"]],
            [0, 1, 2, 3, 4],
        )
        # Frames 0 and 1 kept their index, so they keep their pixels.
        self.assertEqual(
            before[0], (package_dir / "assets" / "walk_0.png").read_bytes()
        )
        self.assertEqual(
            before[1], (package_dir / "assets" / "walk_1.png").read_bytes()
        )
        saved = json.loads((package_dir / "pet_config.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["generation"]["frame_counts"]["walk"], 5)


if __name__ == "__main__":
    unittest.main()
