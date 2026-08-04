from __future__ import annotations

import hmac
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import BackgroundTasks, Body, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .ai_provider import OpenAICompatibleImageProvider
from .config import settings
from .package_builder import build_pet_package
from .storage import JobStore


PROJECT_ROOT = Path(__file__).resolve().parents[2]
store = JobStore(settings.data_dir)
provider = OpenAICompatibleImageProvider(settings)
app = FastAPI(title="AI Desktop Pet Generator", version="0.2.0")


class AISettingsUpdate(BaseModel):
    enabled: Optional[bool] = None
    api_base_url: Optional[str] = None
    image_model: Optional[str] = None
    api_key: Optional[str] = None
    clear_api_key: bool = False
    frame_count: Optional[int] = Field(default=None, ge=1, le=8)
    max_references: Optional[int] = Field(default=None, ge=1, le=4)
    timeout_seconds: Optional[int] = Field(default=None, ge=30, le=900)
    pose_consistency: Optional[bool] = None
    animation_mode: Optional[str] = None
    animation_fps: Optional[int] = Field(default=None, ge=1, le=60)
    walk_frame_count: Optional[int] = Field(default=None, ge=1, le=12)
    sleep_frame_count: Optional[int] = Field(default=None, ge=1, le=12)

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["*"],
    )


@app.get("/healthz")
def healthz() -> Dict[str, Any]:
    return {
        "ok": True,
        "ai_configured": provider.available,
        "build_mode": settings.build_mode,
    }


@app.get("/api/settings/ai")
def get_ai_settings(x_admin_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _verify_admin(x_admin_token)
    return settings.ai_public()


@app.put("/api/settings/ai")
def update_ai_settings(
    payload: AISettingsUpdate,
    x_admin_token: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    _verify_admin(x_admin_token)
    try:
        values = payload.model_dump(exclude_none=True)
        return settings.update_ai(values)
    except (TypeError, ValueError) as error:
        raise HTTPException(400, str(error))


@app.post("/api/pets/generate")
async def generate_pet(
    background_tasks: BackgroundTasks,
    photos: List[UploadFile] = File(...),
    name: str = Form("我的宠物"),
    build_exe: bool = Form(False),
) -> Dict[str, Any]:
    if not photos or len(photos) > settings.max_files:
        raise HTTPException(400, f"请上传 1-{settings.max_files} 张图片")

    safe_name = _safe_name(name)
    record = store.new_job(safe_name, len(photos), build_exe)
    job_id = record["id"]
    input_dir = store.job_dir(job_id) / "input"
    saved_paths: List[Path] = []
    try:
        for index, upload in enumerate(photos):
            suffix = Path(upload.filename or "photo.png").suffix.lower()
            if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}:
                raise HTTPException(400, f"不支持的图片格式：{suffix or 'unknown'}")
            target = input_dir / f"photo_{index}{suffix}"
            await _save_upload(upload, target)
            saved_paths.append(target)
    except Exception:
        shutil.rmtree(store.job_dir(job_id), ignore_errors=True)
        raise

    background_tasks.add_task(_run_generation, job_id, safe_name, saved_paths, build_exe)
    return _public_job(store.read(job_id))


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> Dict[str, Any]:
    try:
        return _public_job(store.read(job_id))
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "任务不存在")


@app.post("/api/jobs/{job_id}/build-exe")
def request_exe_build(job_id: str) -> Dict[str, Any]:
    """Queue the Windows build for an already generated resource package."""

    try:
        record = store.read(job_id)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "任务不存在")

    if settings.build_mode != "worker":
        raise HTTPException(409, "当前服务未启用 Windows Worker，请将 BUILD_MODE 设置为 worker")
    if not settings.worker_token:
        raise HTTPException(503, "Windows Worker 未配置 WORKER_TOKEN")

    package = record.get("package_path")
    if not package or not Path(package).exists():
        raise HTTPException(409, "资源包尚未生成完成")
    if record.get("artifact_kind") == "exe":
        raise HTTPException(409, "该任务已经生成 Windows exe")
    if record.get("status") not in {"ready", "failed"}:
        raise HTTPException(409, f"当前任务状态为 {record.get('status') or 'unknown'}，暂时不能打包 exe")

    store.update(
        job_id,
        build_exe=True,
        status="ready_for_build",
        progress=90,
        message="已提交 Windows exe 打包请求，等待 Worker",
        error="",
    )
    return _public_job(store.read(job_id))


@app.get("/api/jobs/{job_id}/download")
def download_job(job_id: str):
    try:
        record = store.read(job_id)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "任务不存在")
    artifact = _artifact_path(record)
    if not artifact or not artifact.exists():
        raise HTTPException(409, "任务尚未生成可下载文件")
    filename = artifact.name
    return FileResponse(str(artifact), filename=filename, media_type="application/octet-stream")


@app.get("/api/worker/jobs/next")
def worker_next(x_worker_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _verify_worker(x_worker_token)
    record = store.claim_next_build_job()
    if not record:
        return {"job": None}
    return {
        "job": {
            "id": record["id"],
            "name": record["name"],
            "source_url": f"/api/worker/jobs/{record['id']}/source",
            "artifact_url": f"/api/worker/jobs/{record['id']}/artifact",
            "error_url": f"/api/worker/jobs/{record['id']}/error",
        }
    }


@app.get("/api/worker/healthz")
def worker_healthz(x_worker_token: Optional[str] = Header(default=None)) -> Dict[str, bool]:
    """Validate a Worker token without claiming a queued build job."""

    _verify_worker(x_worker_token)
    return {"ok": True}


@app.get("/api/worker/jobs/{job_id}/source")
def worker_source(job_id: str, x_worker_token: Optional[str] = Header(default=None)):
    _verify_worker(x_worker_token)
    try:
        record = store.read(job_id)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "任务不存在")
    package = record.get("package_path")
    if not package or not Path(package).exists():
        raise HTTPException(409, "资源包尚未准备好")
    return FileResponse(package, filename=Path(package).name, media_type="application/zip")


@app.post("/api/worker/jobs/{job_id}/artifact")
async def worker_artifact(
    job_id: str,
    artifact: UploadFile = File(...),
    x_worker_token: Optional[str] = Header(default=None),
):
    _verify_worker(x_worker_token)
    try:
        store.read(job_id)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "任务不存在")
    artifact_dir = store.job_dir(job_id) / "artifact"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    target = artifact_dir / f"{_safe_name(Path(artifact.filename or 'pet').stem)}.exe"
    await _save_upload(artifact, target, limit=250_000_000)
    store.update(
        job_id,
        status="ready",
        progress=100,
        message="Windows exe 已生成",
        artifact_path=str(target),
        artifact_kind="exe",
    )
    return {"ok": True, "job": _public_job(store.read(job_id))}


@app.post("/api/worker/jobs/{job_id}/error")
def worker_error(
    job_id: str,
    payload: Optional[Dict[str, str]] = Body(default=None),
    x_worker_token: Optional[str] = Header(default=None),
):
    _verify_worker(x_worker_token)
    message = (payload or {}).get("message", "Windows Worker 打包失败")[-4000:]
    store.update(job_id, status="failed", message=message, error=message)
    return {"ok": True}


async def _run_generation(job_id: str, name: str, paths: List[Path], build_exe: bool) -> None:
    store.update(job_id, status="processing", progress=5, message="正在准备照片")

    def progress(value: int, message: str) -> None:
        store.update(job_id, progress=value, message=message)

    try:
        result = await build_pet_package(
            store.job_dir(job_id),
            name,
            paths,
            provider,
            settings,
            progress,
        )
        zip_path = Path(result["zip_path"])
        next_status = "ready_for_build" if build_exe and settings.build_mode == "worker" else "ready"
        ai_frame_total = int(result["ai_frame_total"])
        if ai_frame_total:
            message = f"AI 已生成 {ai_frame_total} 张动作帧"
        else:
            message = "未生成 AI 动作帧，已使用照片动画兜底"
        if next_status == "ready_for_build":
            message += "；等待 Windows Worker 打包 exe"
        if build_exe and settings.build_mode == "archive":
            message += "；Docker Linux 模式需接入 Windows Worker 才能生成 exe"
        store.update(
            job_id,
            status=next_status,
            progress=100 if next_status == "ready" else 90,
            message=message,
            package_path=str(zip_path),
            artifact_path=str(zip_path),
            artifact_kind="zip",
            ai_frame_total=ai_frame_total,
            ai_error_count=result["ai_error_count"],
            animation_mode=result.get("animation_mode", settings.animation_mode),
        )
    except Exception as error:
        store.update(job_id, status="failed", progress=0, message=str(error)[-4000:], error=str(error)[-4000:])


async def _save_upload(upload: UploadFile, target: Path, limit: Optional[int] = None) -> None:
    maximum = limit or settings.max_upload_bytes
    total = 0
    with target.open("wb") as output:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > maximum:
                raise HTTPException(413, f"文件过大，单张图片不能超过 {maximum // 1_000_000} MB")
            output.write(chunk)


def _artifact_path(record: Dict[str, Any]) -> Optional[Path]:
    path = record.get("artifact_path")
    return Path(path) if path else None


def _public_job(record: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(record)
    result.pop("artifact_path", None)
    result.pop("package_path", None)
    if record.get("artifact_path"):
        result["download_url"] = f"/api/jobs/{record['id']}/download"
    return result


def _safe_name(value: str) -> str:
    from pet_common import safe_filename

    return safe_filename(value[:80], "my-pet")


def _verify_worker(token: Optional[str]) -> None:
    if not settings.worker_token:
        raise HTTPException(503, "Windows Worker 未配置 WORKER_TOKEN")
    if not token or not hmac.compare_digest(token, settings.worker_token):
        raise HTTPException(401, "Worker token 无效")


def _verify_admin(token: Optional[str]) -> None:
    if not settings.admin_token:
        raise HTTPException(503, "前端 AI 配置未启用，请先配置 ADMIN_TOKEN")
    if not token or not hmac.compare_digest(token, settings.admin_token):
        raise HTTPException(401, "管理令牌无效")


static_dir = PROJECT_ROOT / "server" / "static"
app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
