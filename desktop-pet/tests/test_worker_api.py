from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from server.app.main import app, settings, store
from server.app.storage import JobStore


class WorkerHealthEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.original_store = store
        self.original_token = settings.worker_token
        self.original_build_mode = settings.build_mode
        import server.app.main as main

        self.main = main
        self.main.store = JobStore(Path(self.temporary.name))
        settings.worker_token = "worker-test-token"
        settings.build_mode = "worker"
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.main.store = self.original_store
        settings.worker_token = self.original_token
        settings.build_mode = self.original_build_mode
        self.temporary.cleanup()

    def test_health_check_validates_token_without_claiming_job(self) -> None:
        record = self.main.store.new_job("pet", 1, True)
        self.main.store.update(record["id"], status="ready_for_build")

        response = self.client.get("/api/worker/healthz", headers={"X-Worker-Token": "worker-test-token"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})
        self.assertEqual(self.main.store.read(record["id"])["status"], "ready_for_build")

    def test_resource_job_can_be_queued_for_exe_as_a_second_step(self) -> None:
        package = Path(self.temporary.name) / "pet.zip"
        package.write_bytes(b"resource package")
        record = self.main.store.new_job("pet", 1, False)
        self.main.store.update(
            record["id"],
            status="ready",
            package_path=str(package),
            artifact_path=str(package),
            artifact_kind="zip",
        )

        response = self.client.post(f"/api/jobs/{record['id']}/build-exe")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ready_for_build")
        updated = self.main.store.read(record["id"])
        self.assertTrue(updated["build_exe"])
        self.assertEqual(updated["package_path"], str(package))


if __name__ == "__main__":
    unittest.main()
