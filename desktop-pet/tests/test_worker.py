from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKER_DIR = PROJECT_ROOT / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.insert(0, str(WORKER_DIR))

import worker


class WorkerConnectionTests(unittest.TestCase):
    @patch("worker.requests.get")
    def test_check_connection_does_not_claim_a_build_job(self, get: Mock) -> None:
        health_response = Mock()
        health_response.json.return_value = {"ok": True}
        worker_response = Mock()
        get.side_effect = [health_response, worker_response]

        worker.check_connection()

        self.assertEqual(get.call_count, 2)
        self.assertEqual(get.call_args_list[1].args[0], f"{worker.SERVER_URL}/api/worker/healthz")
        self.assertEqual(get.call_args_list[1].kwargs["headers"], worker.headers())


class WorkerBuildCommandTests(unittest.TestCase):
    @patch("worker.subprocess.run")
    def test_build_collects_pillow_native_extension(self, run: Mock) -> None:
        run.return_value.returncode = 0
        run.return_value.stdout = ""
        run.return_value.stderr = ""
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary) / "package"
            executable = package / "dist" / "Build_Test.exe"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"test executable")

            result = worker.build_exe(package, "Build Test")

            self.assertEqual(result, executable)
            command = run.call_args_list[0].args[0]
            collect_index = command.index("--collect-binaries")
            self.assertEqual(command[collect_index + 1], "PIL")
            hidden_imports = [
                command[index + 1]
                for index, value in enumerate(command)
                if value == "--hidden-import"
            ]
            self.assertIn("PIL._imaging", hidden_imports)
            self.assertIn("PIL.ImageTk", hidden_imports)
            self.assertNotIn("--clean", command)
            self.assertEqual(run.call_args_list[1].args[0][-1], "--self-test")


if __name__ == "__main__":
    unittest.main()
