"""End-to-end tests for the image-model picker endpoints.

These drive the real FastAPI app with a temporary job store, so they cover the
form field, the JSON field, and the model-listing endpoint together.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import httpx

import server.app.main as main
from server.app.main import app, settings, store
from server.app.storage import JobStore

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


class _FakeResponse:
    status_code = 200

    @staticmethod
    def json():
        return {"data": [{"id": "gateway-cheap"}, {"id": "gateway-good"}]}


class _FakeClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get(self, url, headers=None):
        return _FakeResponse()


class ModelPickerApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        import server.app.main as main

        self.main = main
        self.original_store = main.store
        self.original_model = settings.ai_image_model
        self.original_key = settings.ai_api_key
        self.original_enabled = settings.ai_enabled
        self.original_admin_token = settings.admin_token
        main.store = JobStore(Path(self.temporary.name))
        settings.ai_image_model = "account-model"
        settings.ai_api_key = "test-key"
        settings.ai_enabled = True
        # The administrator token authenticates every request here, so no user
        # account (and no real data directory) is touched.
        settings.admin_token = "model-picker-test-token"
        # No photo work should actually run; only the record matters here.
        self._background = mock.patch.object(main, "_run_preparation", lambda *a, **k: None)
        self._background.start()
        self.client = TestClient(app)
        self.headers = {"X-Admin-Token": settings.admin_token}

    def tearDown(self) -> None:
        self._background.stop()
        self.main.store = self.original_store
        settings.ai_image_model = self.original_model
        settings.ai_api_key = self.original_key
        settings.ai_enabled = self.original_enabled
        settings.admin_token = self.original_admin_token
        self.temporary.cleanup()

    def _prepare(self, data=None):
        return self.client.post(
            "/api/pets/prepare",
            files={"photos": ("pet.png", PNG_BYTES, "image/png")},
            data={"name": "pet", **(data or {})},
            headers=self.headers,
        )

    def test_models_endpoint_returns_the_gateway_list(self):
        with mock.patch.object(httpx, "AsyncClient", _FakeClient):
            response = self.client.get("/api/ai/models", headers=self.headers)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["source"], "gateway")
        # The account model stays first even though the gateway omits it.
        self.assertEqual(
            payload["models"], ["account-model", "gateway-cheap", "gateway-good"]
        )

    def test_models_endpoint_survives_an_unreachable_gateway(self):
        with mock.patch.object(
            httpx, "AsyncClient", side_effect=httpx.ConnectError("boom")
        ):
            response = self.client.get("/api/ai/models", headers=self.headers)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["source"], "builtin")
        self.assertTrue(payload["models"])

    def test_a_job_can_pick_its_own_model(self):
        response = self._prepare({"image_model": "task-model"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["image_model"], "task-model")
        record = self.main.store.read(payload["id"])
        self.assertEqual(record["ai_config"]["ai_image_model"], "task-model")

    def test_a_job_without_a_choice_follows_the_account(self):
        response = self._prepare()

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["image_model"], "account-model")

    def test_an_empty_choice_also_follows_the_account(self):
        response = self._prepare({"image_model": ""})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["image_model"], "account-model")

    def test_a_malformed_model_name_is_rejected(self):
        response = self._prepare({"image_model": "bad\nname"})

        self.assertEqual(response.status_code, 400)

    def test_the_model_can_still_be_changed_after_the_preview(self):
        prepared = self._prepare().json()
        job_id = prepared["id"]
        # The preview step hands its cutouts to the generation step.
        prepared_dir = self.main.store.job_dir(job_id) / "prepared"
        prepared_dir.mkdir(parents=True, exist_ok=True)
        (prepared_dir / "reference_0.png").write_bytes(PNG_BYTES)
        self.main.store.update(
            job_id,
            status="preview_ready",
            preview_paths=["prepared/reference_0.png"],
        )

        with mock.patch.object(self.main, "_run_generation", lambda *a, **k: None):
            response = self.client.post(
                f"/api/jobs/{job_id}/generate",
                json={"name": "pet", "image_model": "second-model"},
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["image_model"], "second-model")
        self.assertEqual(
            self.main.store.read(job_id)["ai_config"]["ai_image_model"], "second-model"
        )

    def test_the_effective_model_reaches_the_provider(self):
        job_id = self._prepare({"image_model": "task-model"}).json()["id"]

        task_settings = self.main._settings_for_job(self.main.store.read(job_id))

        self.assertEqual(task_settings.ai_image_model, "task-model")

    def test_atlas_mode_round_trips_through_the_settings_endpoint(self):
        response = self.client.put(
            "/api/settings/ai", json={"atlas_mode": "off"}, headers=self.headers
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["atlas_mode"], "off")
        settings.atlas_mode = "auto"


class ModelSnapshotTests(unittest.TestCase):
    """The per-task snapshot, without an HTTP round trip."""

    def test_the_snapshot_override_wins_over_the_account(self):
        context = {"id": "u1", "username": "u", "role": "admin"}

        snapshot = main._ai_snapshot_for_context(context, "task-model")

        self.assertEqual(snapshot["ai_image_model"], "task-model")
        # The account setting itself was not modified.
        self.assertEqual(
            settings.ai_snapshot()["ai_image_model"], settings.ai_image_model
        )

    def test_no_override_leaves_the_snapshot_alone(self):
        context = {"id": "u1", "username": "u", "role": "admin"}

        snapshot = main._ai_snapshot_for_context(context)

        self.assertEqual(snapshot["ai_image_model"], settings.ai_image_model)

    def test_a_job_built_with_an_override_uses_that_model(self):
        record = {"ai_config": {"ai_image_model": "task-model", "ai_api_key": "k"}}

        task_settings = main._settings_for_job(record)

        self.assertEqual(task_settings.ai_image_model, "task-model")
        # Non-AI process settings still come from the live process.
        self.assertEqual(task_settings.data_dir, settings.data_dir)

    def test_the_public_job_exposes_the_effective_model(self):
        record = {
            "id": "job1",
            "ai_config": {"ai_image_model": "task-model"},
            "events": [],
            "preview_paths": [],
            "resource_preview_paths": [],
            "resource_frame_meta": [],
        }

        public = main._public_job(record, include_events=False)

        self.assertEqual(public["image_model"], "task-model")
        self.assertNotIn("ai_config", public)

    def test_a_job_without_a_model_override_has_no_model_field(self):
        record = {
            "id": "job2",
            "ai_config": {},
            "events": [],
            "preview_paths": [],
            "resource_preview_paths": [],
            "resource_frame_meta": [],
        }

        public = main._public_job(record, include_events=False)

        self.assertNotIn("image_model", public)

    def test_a_malformed_name_becomes_a_400_not_a_crash(self):
        with self.assertRaises(main.HTTPException) as caught:
            main._normalize_model_name("bad\nname")

        self.assertEqual(caught.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
