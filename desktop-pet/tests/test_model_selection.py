"""Tests for choosing the image model per account and per task."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

import httpx

from server.app.ai_provider import KNOWN_IMAGE_MODELS, OpenAICompatibleImageProvider
from server.app.config import normalize_model_name


def _settings(**overrides):
    values = {
        "ai_enabled": True,
        "ai_api_key": "test-key",
        "ai_api_base_url": "https://gateway.test/v1",
        "ai_image_model": "account-model",
        "ai_timeout_seconds": 60,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self._payload


class _FakeClient:
    """Stands in for httpx.AsyncClient and records the request it received."""

    calls = []
    response = _FakeResponse(payload={"data": [{"id": "cheap-model"}, {"id": "good-model"}]})
    error = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get(self, url, headers=None):
        if _FakeClient.error is not None:
            raise _FakeClient.error
        _FakeClient.calls.append({"url": url, "headers": headers})
        return _FakeClient.response


class ListModelsTests(unittest.TestCase):
    def setUp(self):
        _FakeClient.calls = []
        _FakeClient.response = _FakeResponse(
            payload={"data": [{"id": "cheap-model"}, {"id": "good-model"}]}
        )
        _FakeClient.error = None
        self._patch = mock.patch.object(httpx, "AsyncClient", _FakeClient)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_gateway_models_are_returned_and_authenticated(self):
        result = asyncio.run(
            OpenAICompatibleImageProvider(_settings(ai_image_model="good-model")).list_models()
        )

        self.assertEqual(result["source"], "gateway")
        self.assertEqual(result["models"], ["cheap-model", "good-model"])
        self.assertEqual(result["error"], "")
        self.assertEqual(_FakeClient.calls[0]["url"], "https://gateway.test/v1/models")
        self.assertEqual(
            _FakeClient.calls[0]["headers"]["Authorization"], "Bearer test-key"
        )

    def test_the_account_model_stays_selectable_when_the_gateway_omits_it(self):
        result = asyncio.run(OpenAICompatibleImageProvider(_settings()).list_models())

        self.assertEqual(result["models"], ["account-model", "cheap-model", "good-model"])
        self.assertEqual(result["current"], "account-model")

    def test_a_gateway_error_falls_back_to_the_known_names(self):
        _FakeClient.response = _FakeResponse(status_code=404, text="not found")

        result = asyncio.run(OpenAICompatibleImageProvider(_settings()).list_models())

        self.assertEqual(result["source"], "builtin")
        self.assertEqual(list(result["models"]), list(KNOWN_IMAGE_MODELS))
        self.assertIn("404", result["error"])

    def test_an_unreachable_gateway_falls_back_without_raising(self):
        _FakeClient.error = httpx.ConnectError("boom")

        result = asyncio.run(OpenAICompatibleImageProvider(_settings()).list_models())

        self.assertEqual(result["source"], "builtin")
        self.assertTrue(result["error"])

    def test_an_empty_gateway_list_falls_back(self):
        _FakeClient.response = _FakeResponse(payload={"data": []})

        result = asyncio.run(OpenAICompatibleImageProvider(_settings()).list_models())

        self.assertEqual(result["source"], "builtin")
        self.assertIn("没有返回任何模型", result["error"])

    def test_a_missing_api_key_does_not_call_the_gateway(self):
        result = asyncio.run(
            OpenAICompatibleImageProvider(_settings(ai_api_key="")).list_models()
        )

        self.assertEqual(result["source"], "builtin")
        self.assertEqual(_FakeClient.calls, [])


class ModelNameValidationTests(unittest.TestCase):
    """Validation of a per-task model name (no FastAPI needed)."""

    def test_blank_means_use_the_account_setting(self):
        self.assertIsNone(normalize_model_name(None))
        self.assertIsNone(normalize_model_name(""))
        self.assertIsNone(normalize_model_name("   "))

    def test_a_name_is_trimmed_and_kept(self):
        self.assertEqual(normalize_model_name("  flux-pro  "), "flux-pro")

    def test_an_absurd_name_is_rejected(self):
        for value in ("x" * 121, "bad\nname", "bad\tname", "bad\rname"):
            with self.assertRaises(ValueError):
                normalize_model_name(value)


if __name__ == "__main__":
    unittest.main()
