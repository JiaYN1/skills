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
        import server.app.main as main

        self.main = main
        self.main.store = JobStore(Path(self.temporary.name))
        settings.worker_token = "worker-test-token"
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.main.store = self.original_store
        settings.worker_token = self.original_token
        self.temporary.cleanup()

    def test_health_check_validates_token_without_claiming_job(self) -> None:
        record = self.main.store.new_job("pet", 1, True)
        self.main.store.update(record["id"], status="ready_for_build")

        response = self.client.get("/api/worker/healthz", headers={"X-Worker-Token": "worker-test-token"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})
        self.assertEqual(self.main.store.read(record["id"])["status"], "ready_for_build")


if __name__ == "__main__":
    unittest.main()
