from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from server.app.main import app, store
from server.app.storage import JobStore


class ResumeGenerationEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.original_store = store
        import server.app.main as main

        self.main = main
        self.main.store = JobStore(Path(self.temporary.name))
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.main.store = self.original_store
        self.temporary.cleanup()

    def _failed_job(self, *, package_path: str | None = None):
        record = self.main.store.new_job("旧名称", 1, False)
        preview = self.main.store.job_dir(record["id"]) / "prepared" / "reference_0.png"
        preview.parent.mkdir(parents=True, exist_ok=True)
        preview.write_bytes(b"prepared preview")
        changes = {
            "status": "failed",
            "preview_paths": ["prepared/reference_0.png"],
            "error": "action generation failed",
        }
        if package_path is not None:
            changes["package_path"] = package_path
        self.main.store.update(record["id"], **changes)
        return record

    def test_resume_reuses_prepared_preview_without_reuploading(self) -> None:
        record = self._failed_job()
        captured = []

        async def fake_run_generation(job_id, name, paths, build_exe, selected_actions):
            captured.append((job_id, name, paths, build_exe, selected_actions))

        original_runner = self.main._run_generation
        self.main._run_generation = fake_run_generation
        try:
            response = self.client.post(
                f"/api/jobs/{record['id']}/resume",
                json={"name": "继续生成的宠物", "selected_actions": ["walk"]},
            )
        finally:
            self.main._run_generation = original_runner

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "processing")
        self.assertEqual(len(captured), 1)
        job_id, name, paths, build_exe, selected_actions = captured[0]
        self.assertEqual(job_id, record["id"])
        self.assertEqual(name, "继续生成的宠物")
        self.assertEqual(paths[0].name, "reference_0.png")
        self.assertEqual(paths[0].parent.name, "prepared")
        self.assertFalse(build_exe)
        self.assertEqual(selected_actions, ["walk"])
        self.assertEqual(self.main.store.read(record["id"])["resume_count"], 1)

    def test_resume_is_rejected_when_the_job_already_has_a_package(self) -> None:
        package = Path(self.temporary.name) / "pet.zip"
        package.write_bytes(b"resource package")
        record = self._failed_job(package_path=str(package))

        response = self.client.post(f"/api/jobs/{record['id']}/resume", json={})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.main.store.read(record["id"])["status"], "failed")

    def test_cancelled_resource_edit_can_resume_from_its_checkpoint(self) -> None:
        package = Path(self.temporary.name) / "pet.zip"
        package.write_bytes(b"resource package")
        record = self.main.store.new_job("pet", 1, True)
        self.main.store.update(
            record["id"],
            status="cancelled",
            phase="resource_edit",
            package_path=str(package),
            artifact_path=str(package),
            artifact_kind="zip",
            cancel_requested=True,
            checkpoint={
                "stage": "resource_edit",
                "operation": "remove",
                "frames": {"walk": [1]},
            },
        )
        captured = []

        def fake_remove(job_id, requested):
            captured.append((job_id, requested))

        original_runner = self.main._run_resource_frame_removal
        self.main._run_resource_frame_removal = fake_remove
        try:
            response = self.client.post(f"/api/jobs/{record['id']}/resume", json={})
        finally:
            self.main._run_resource_frame_removal = original_runner

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "resource_editing")
        self.assertEqual(captured, [(record["id"], {"walk": [1]})])
        self.assertFalse(self.main.store.read(record["id"])["cancel_requested"])


if __name__ == "__main__":
    unittest.main()
