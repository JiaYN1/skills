from __future__ import annotations

import sys
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


if __name__ == "__main__":
    unittest.main()
