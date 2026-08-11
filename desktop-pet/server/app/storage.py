from __future__ import annotations

import json
import re
import tempfile
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from pet_common import ROLES


JOB_ID_PATTERN = re.compile(r"^[a-f0-9]{16,64}$")


class JobStore:
    """Small filesystem-backed job store for the single-container MVP."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.jobs_dir = data_dir / "jobs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def new_job(
        self,
        name: str,
        photo_count: int,
        build_exe: bool,
        selected_actions: Optional[List[str]] = None,
        owner_id: Optional[str] = None,
        owner_name: Optional[str] = None,
        ai_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        job_id = uuid.uuid4().hex
        job_dir = self.job_dir(job_id)
        (job_dir / "input").mkdir(parents=True, exist_ok=False)
        record = {
            "id": job_id,
            "name": name,
            "photo_count": photo_count,
            "build_exe": build_exe,
            "selected_actions": list(selected_actions) if selected_actions is not None else list(ROLES),
            "status": "queued",
            "progress": 0,
            "message": "任务已创建",
            "created_at": datetime.utcnow().isoformat() + "Z",
            "owner_id": owner_id,
            "owner_name": owner_name or "",
            # This is intentionally private and is removed by _public_job.
            # A task must continue using the configuration it was created
            # with even if the user edits their account settings later.
            "ai_config": dict(ai_config or {}),
            "checkpoint": {
                "stage": "queued",
                "completed_photo_indices": [],
                "completed_roles": [],
            },
            "events": [],
            "event_seq": 0,
        }
        self._append_event(record, force=True)
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
            previous = {
                key: record.get(key)
                for key in ("status", "progress", "message", "phase", "checkpoint")
            }
            record.update(changes)
            if any(record.get(key) != value for key, value in previous.items()):
                self._append_event(record)
            self._write(record)
            return record

    def list_jobs(self, owner_id: Optional[str] = None) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        with self._lock:
            for job_dir in self.jobs_dir.iterdir():
                if not job_dir.is_dir():
                    continue
                record_path = job_dir / "job.json"
                if not record_path.exists():
                    continue
                try:
                    record = json.loads(record_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if owner_id is not None and record.get("owner_id") != owner_id:
                    continue
                records.append(record)
        records.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
        return records

    def events(self, job_id: str) -> List[Dict[str, Any]]:
        record = self.read(job_id)
        values = record.get("events", [])
        return [dict(item) for item in values if isinstance(item, dict)]

    def request_cancel(self, job_id: str) -> Dict[str, Any]:
        with self._lock:
            record = self.read(job_id)
            status = str(record.get("status") or "")
            if status in {"ready", "preview_ready", "failed", "cancelled"}:
                return record
            if status == "ready_for_build":
                record.update(
                    status="cancelled",
                    cancel_requested=True,
                    message="任务已停止，Windows Worker 尚未开始打包",
                    heartbeat_at=_utc_now(),
                )
            else:
                record.update(
                    status="cancelling",
                    cancel_requested=True,
                    message="已请求停止，正在保存当前断点",
                    heartbeat_at=_utc_now(),
                )
            self._append_event(record)
            self._write(record)
            return record

    def cancel_requested(self, job_id: str) -> bool:
        try:
            return bool(self.read(job_id).get("cancel_requested"))
        except (FileNotFoundError, ValueError):
            return True

    def recover_interrupted_jobs(self) -> int:
        """Make in-flight jobs resumable after an API process restart."""

        recovered = 0
        active = {
            "queued",
            "preview_processing",
            "preview_regenerating",
            "processing",
            "resource_regenerating",
            "resource_editing",
            "cancelling",
            "building",
        }
        for record in self.list_jobs():
            if record.get("status") not in active:
                continue
            if record.get("status") == "cancelling":
                self.update(
                    record["id"],
                    status="cancelled",
                    message="服务重启时任务已停止，可从已保存断点恢复",
                    cancel_requested=True,
                )
                recovered += 1
                continue
            if record.get("phase") == "resource_edit" and record.get("package_path"):
                self.update(
                    record["id"],
                    status="interrupted",
                    message="服务重启时资源编辑已中断，可从断点恢复",
                    cancel_requested=False,
                )
                recovered += 1
                continue
            if record.get("status") == "building" and record.get("package_path"):
                self.update(
                    record["id"],
                    status="ready_for_build",
                    message="服务已重启，Windows Worker 可重新领取此任务",
                    cancel_requested=False,
                    checkpoint=dict(record.get("checkpoint") or {}, stage="building"),
                )
            else:
                self.update(
                    record["id"],
                    status="interrupted",
                    message="服务重启，任务已保存到断点，可点击恢复",
                    cancel_requested=False,
                )
            recovered += 1
        return recovered

    def recover_stale_jobs(self, max_age_seconds: int = 6 * 60 * 60) -> int:
        """Recover jobs whose worker/API heartbeat stopped without a restart."""

        now = datetime.utcnow()
        recovered = 0
        active = {
            "preview_processing",
            "preview_regenerating",
            "processing",
            "resource_regenerating",
            "resource_editing",
            "cancelling",
            "building",
        }
        for record in self.list_jobs():
            if record.get("status") not in active or not record.get("heartbeat_at"):
                continue
            try:
                raw_heartbeat = str(record["heartbeat_at"]).rstrip("Z")
                try:
                    heartbeat = datetime.fromisoformat(raw_heartbeat)
                except AttributeError:
                    heartbeat = datetime.strptime(
                        raw_heartbeat.split(".", 1)[0],
                        "%Y-%m-%dT%H:%M:%S",
                    )
            except (AttributeError, TypeError, ValueError):
                continue
            if (now - heartbeat).total_seconds() <= max_age_seconds:
                continue
            if record.get("phase") == "resource_edit" and record.get("package_path"):
                changes = {
                    "status": "interrupted",
                    "message": "资源编辑心跳超时，可从断点恢复",
                    "cancel_requested": False,
                }
            elif record.get("status") == "building" and record.get("package_path"):
                changes = {
                    "status": "ready_for_build",
                    "message": "Windows Worker 心跳超时，可重新领取打包任务",
                    "cancel_requested": False,
                    "checkpoint": dict(record.get("checkpoint") or {}, stage="building"),
                }
            else:
                changes = {
                    "status": "interrupted",
                    "message": "任务心跳超时，已保存断点，可点击恢复",
                    "cancel_requested": False,
                }
            self.update(record["id"], **changes)
            recovered += 1
        return recovered

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
                if record.get("cancel_requested"):
                    record.update(status="cancelled", message="任务已停止，Worker 未开始打包")
                    self._append_event(record)
                    self._write(record)
                    continue
                record.update(
                    status="building",
                    message="Windows Worker 正在打包 exe",
                    heartbeat_at=_utc_now(),
                    checkpoint=dict(record.get("checkpoint") or {}, stage="building"),
                )
                self._append_event(record)
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
        try:
            target.chmod(0o600)
        except OSError:
            pass

    @staticmethod
    def _append_event(record: Dict[str, Any], force: bool = False) -> None:
        events = record.get("events")
        if not isinstance(events, list):
            events = []
        if not force and events:
            latest = events[-1]
            if (
                latest.get("status") == record.get("status")
                and latest.get("progress") == record.get("progress")
                and latest.get("message") == record.get("message")
                and latest.get("checkpoint") == record.get("checkpoint")
            ):
                record["events"] = events
                return
        sequence = int(record.get("event_seq", 0)) + 1
        event = {
            "id": sequence,
            "time": _utc_now(),
            "status": record.get("status", ""),
            "progress": record.get("progress", 0),
            "message": record.get("message", ""),
            "phase": record.get("phase", ""),
            "checkpoint": dict(record.get("checkpoint") or {}),
        }
        events.append(event)
        record["event_seq"] = sequence
        record["events"] = events[-500:]


def _utc_now() -> str:
    return datetime.utcnow().isoformat() + "Z"
