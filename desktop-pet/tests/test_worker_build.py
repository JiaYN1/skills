from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKER_DIR = PROJECT_ROOT / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.insert(0, str(WORKER_DIR))

import worker


def _has_pyinstaller() -> bool:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        return False
    return True


def _has_functional_tkinter() -> bool:
    try:
        import tkinter

        tkinter.Tcl()
    except Exception:
        return False
    return True


@unittest.skipUnless(os.name == "nt", "Windows-specific PyInstaller build")
@unittest.skipUnless(shutil.which("pyinstaller") or _has_pyinstaller(), "PyInstaller is required")
@unittest.skipUnless(_has_functional_tkinter(), "functional Tkinter is required")
class WorkerBuildTests(unittest.TestCase):
    def test_build_exe_bundles_runtime_and_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary) / "package"
            assets = package / "assets"
            assets.mkdir(parents=True)
            (package / "pet_runtime.py").write_text(
                (PROJECT_ROOT / "pet_runtime.py").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            (package / "pet_config.json").write_text(
                json.dumps({"name": "Build Test", "assets": {}}),
                encoding="utf-8",
            )

            executable = worker.build_exe(package, "Build Test")

            self.assertTrue(executable.is_file())
            self.assertGreater(executable.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
