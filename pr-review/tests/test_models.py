import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from app.main import app


class ModelListTest(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "ACCESS_PASSWORD": "",
            "OPENAI_API_KEY": "test-key",
            "OPENAI_MODEL": "custom-default",
            "OPENAI_BASE_URL": "https://models.example/v1/",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.client = TestClient(app)

    def test_models_are_deduplicated_and_include_default(self):
        response = httpx.Response(200, request=httpx.Request("GET", "https://models.example/v1/models"), json={
            "data": [{"id": "model-b"}, {"id": "model-a"}, {"id": "model-b"},
                     {"id": "custom-default"}, {"id": None}, {}],
        })
        with patch("app.main.httpx.AsyncClient.get", new_callable=AsyncMock, return_value=response) as get:
            result = self.client.get("/api/models")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json(), {
            "default_model": "custom-default",
            "models": ["custom-default", "model-a", "model-b"],
        })
        get.assert_awaited_once_with("https://models.example/v1/models", headers={"Authorization": "Bearer test-key"})

    def test_provider_failure_keeps_default_without_exposing_error(self):
        with patch("app.main.httpx.AsyncClient.get", new_callable=AsyncMock,
                   side_effect=httpx.ConnectError("private upstream details")):
            result = self.client.get("/api/models")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["models"], ["custom-default"])
        self.assertIn("warning", result.json())
        self.assertNotIn("private upstream details", result.text)

    def test_missing_key_does_not_request_provider(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}), patch("app.main.httpx.AsyncClient.get", new_callable=AsyncMock) as get:
            result = self.client.get("/api/models")
        get.assert_not_awaited()
        self.assertEqual(result.json()["models"], ["custom-default"])

    def test_model_endpoint_requires_login_when_password_is_enabled(self):
        with patch.dict(os.environ, {"ACCESS_PASSWORD": "secret"}):
            self.assertEqual(self.client.get("/api/models").status_code, 401)


if __name__ == "__main__":
    unittest.main()
