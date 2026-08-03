from __future__ import annotations

import json
import re
import tempfile
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


JOB_ID_PATTERN = re.compile(r"^[a-f0-9]{16,64}$")


class JobStore:
    """Small filesystem-backed job store for the single-container MVP."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.jobs_dir = data_dir / "jobs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def new_job(self, name: str, photo_count: int, build_exe: bool) -> Dict[str, Any]:
        job_id = uuid.uuid4().hex
        job_dir = self.job_dir(job_id)
        (job_dir / "input").mkdir(parents=True, exist_ok=False)
        record = {
            "id": job_id,
            "name": name,
            "photo_count": photo_count,
            "build_exe": build_exe,
            "status": "queued",
            "progress": 0,
            "message": "任务已创建",
            "created_at": datetime.utcnow().isoformat() + "Z",
        }
        self._write(record)
        return record

    def job_dir(self, job_id: str) -> Path:
        if not JOB_ID_PATTERN.fullmatch(job_id):
            raise ValueError("非法任务 ID")
        return self.jobs_dir / job_id

    def read(self, job_id: str) -> Dict[str, Any]:
        path = self.job_dir(job_id) / "job.json"
        if not path.exists():
            raise FileNotFoundError(job_id)
        return json.loads(path.read_text(encoding="utf-8"))

    def update(self, job_id: str, **changes: Any) -> Dict[str, Any]:
        with self._lock:
            record = self.read(job_id)
            record.update(changes)
            self._write(record)
            return record

    def claim_next_build_job(self) -> Optional[Dict[str, Any]]:
        """Claim one job; deployments should normally run one worker."""

        with self._lock:
            for job_dir in sorted(self.jobs_dir.iterdir()):
                if not job_dir.is_dir():
                    continue
                record_path = job_dir / "job.json"
                if not record_path.exists():
                    continue
                record = json.loads(record_path.read_text(encoding="utf-8"))
                if record.get("status") != "ready_for_build":
                    continue
                record.update(status="building", message="Windows Worker 正在打包 exe")
                self._write(record)
                return record
        return None

    def _write(self, record: Dict[str, Any]) -> None:
        target = self.job_dir(record["id"]) / "job.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(target.parent),
            prefix="job-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(record, handle, ensure_ascii=False, indent=2)
            temporary = Path(handle.name)
        temporary.replace(target)
