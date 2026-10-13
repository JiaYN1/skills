"""Shared pytest configuration for the desktop-pet test suite.

The web API lives in ``server.app.main``, which needs FastAPI - a server-side
dependency, not a runtime one. On a machine that only has the Pillow/httpx
runtime install, the API-driven modules are skipped rather than failing at
collection, so ``python -m pytest tests -q`` still covers everything it can.
"""



FASTAPI_MODULES = [
    "test_preview_flow.py",
    "test_resource_frame_edit.py",
    "test_resource_frame_quality.py",
    "test_resume_api.py",
    "test_worker_api.py",
]

try:  # pragma: no cover - environment dependent
    import fastapi  # noqa: F401
except ImportError:  # pragma: no cover - environment dependent
    collect_ignore = list(FASTAPI_MODULES)
