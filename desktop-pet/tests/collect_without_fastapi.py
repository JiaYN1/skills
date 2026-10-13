"""Collect the test suite as if FastAPI were not installed.

Verifies that a Pillow/httpx-only machine still gets a clean pytest run instead
of a collection error. Run from the desktop-pet directory.
"""

import importlib.machinery
import os
import sys

# ``python -m pytest`` puts the working directory on the path; a plain script
# puts the script directory there instead, so add the project root back.
sys.path.insert(0, os.getcwd())


class BlockFastApi(importlib.machinery.PathFinder):
    """Make ``import fastapi`` fail, as it would on a runtime-only install."""

    BLOCKED = {"fastapi", "starlette", "multipart", "python_multipart"}

    @classmethod
    def find_spec(cls, fullname, path=None, target=None):
        if fullname.split(".")[0] in cls.BLOCKED:
            raise ModuleNotFoundError(f"No module named {fullname!r} (simulated)")
        return super().find_spec(fullname, path, target)


sys.meta_path.insert(0, BlockFastApi)

import pytest  # noqa: E402

raise SystemExit(pytest.main(["tests", "-q", "-p", "no:warnings"]))
