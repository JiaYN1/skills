from __future__ import annotations

import hmac
import json
import shutil
import sys
import uuid
import zipfile
from datetime import datetime
from urllib.parse import quote
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import (
    BackgroundTasks,
    Body,
    Cookie,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Response,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .ai_provider import GenerationCancelled, ImageGenerationError, OpenAICompatibleImageProvider
from .auth import AuthStoreError, SESSION_COOKIE, SESSION_TTL_DAYS, UserStore
from .config import settings
from .package_builder import BuildCancelled, ROLE_ANCHORS, ai_cutout_frame, build_pet_package
from .storage import JobStore


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pet_assets import normalize_pet_image, normalize_pet_sequence  # noqa: E402
from pet_animation import write_animation_bundle  # noqa: E402
from pet_common import ROLES, assign_roles, normalize_selected_actions  # noqa: E402

def _default_user_ai_config() -> Dict[str, Any]:
    values = settings.ai_snapshot()
    # A user's first account inherits harmless generation defaults, never the
    # administrator's private provider credential.
    values["ai_api_key"] = ""
    return values


store = JobStore(settings.data_dir)
provider = OpenAICompatibleImageProvider(settings)
user_store = UserStore(settings.data_dir, _default_user_ai_config)
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
    frame_repeat: Optional[int] = Field(default=None, ge=1, le=4)
    walk_frame_count: Optional[int] = Field(default=None, ge=1, le=24)
    sleep_frame_count: Optional[int] = Field(default=None, ge=1, le=24)


class AuthCredentials(BaseModel):
    username: str
    password: str


class GenerateFromPreviewRequest(BaseModel):
    name: Optional[str] = None
    build_exe: bool = False
    selected_actions: Optional[List[str]] = None


class RemoveResourceFramesRequest(BaseModel):
    frames: Dict[str, List[int]]


class InsertResourceFrameRequest(BaseModel):
    role: str
    after_index: int = Field(..., ge=0, le=23)

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["*"],
    )


@app.on_event("startup")
def recover_interrupted_jobs() -> None:
    # BackgroundTasks do not survive an API process restart. Turn those jobs
    # into explicit, user-visible resumable states before serving requests.
    store.recover_interrupted_jobs()


@app.post("/api/auth/register")
def register(credentials: AuthCredentials, response: Response) -> Dict[str, Any]:
    try:
        user = user_store.register(credentials.username, credentials.password)
        token = user_store.create_session(user["id"])
    except AuthStoreError as error:
        raise HTTPException(400, str(error))
    _set_session_cookie(response, token)
    return {"user": user}


@app.post("/api/auth/login")
def login(credentials: AuthCredentials, response: Response) -> Dict[str, Any]:
    try:
        user = user_store.authenticate(credentials.username, credentials.password)
        token = user_store.create_session(user["id"])
    except AuthStoreError as error:
        raise HTTPException(401, str(error))
    _set_session_cookie(response, token)
    return {"user": user}


@app.post("/api/auth/logout")
def logout(
    response: Response,
    session_id: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
) -> Dict[str, bool]:
    user_store.delete_session(session_id)
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@app.get("/api/auth/me")
def auth_me(
    response: Response,
    session_id: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
    x_admin_token: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    context = _request_context(session_id, x_admin_token)
    if context["role"] == "admin" and settings.admin_token:
        # The HttpOnly cookie lets browser image requests authenticate too;
        # an <img> element cannot attach X-Admin-Token itself.
        _set_session_cookie(response, settings.admin_token)
    return {"user": context}


@app.get("/healthz")
def healthz() -> Dict[str, Any]:
    return {
        "ok": True,
        "ai_configured": provider.available,
        "build_mode": settings.build_mode,
    }


@app.get("/api/settings/ai")
def get_ai_settings(
    session_id: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
    x_admin_token: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    context = _request_context(session_id, x_admin_token)
    if context["role"] == "admin":
        result = settings.ai_public()
        result["scope"] = "admin"
        return result
    user_settings = settings.with_ai_snapshot(user_store.get_ai_config(context["id"]))
    result = user_settings.ai_public()
    result["scope"] = "user"
    return result


@app.put("/api/settings/ai")
def update_ai_settings(
    payload: AISettingsUpdate,
    session_id: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
    x_admin_token: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    context = _request_context(session_id, x_admin_token)
    try:
        values = payload.model_dump(exclude_none=True)
        if context["role"] == "admin":
            result = settings.update_ai(values)
            result["scope"] = "admin"
            return result
        user_settings = settings.with_ai_snapshot(user_store.get_ai_config(context["id"]))
        result = user_settings.update_ai_values(values)
        user_store.save_ai_config(context["id"], user_settings.ai_snapshot())
        result["scope"] = "user"
        return result
    except (TypeError, ValueError) as error:
        raise HTTPException(400, str(error))


@app.post("/api/pets/prepare")
async def prepare_pet(
    background_tasks: BackgroundTasks,
    photos: List[UploadFile] = File(...),
    name: str = Form("我的宠物"),
    selected_actions: Optional[List[str]] = Form(default=None),
    session_id: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
    x_admin_token: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    """Create a job whose first stage only removes photo backgrounds."""

    context = _request_context(session_id, x_admin_token)

    if not photos or len(photos) > settings.max_files:
        raise HTTPException(400, f"请上传 1-{settings.max_files} 张图片")

    safe_name = _safe_name(name)
    selected_actions = _normalize_requested_actions(selected_actions)
    record = store.new_job(
        safe_name,
        len(photos),
        False,
        selected_actions,
        owner_id=context["id"],
        owner_name=context["username"],
        ai_config=_ai_snapshot_for_context(context),
    )
    job_id = record["id"]
    try:
        saved_paths = await _save_uploaded_photos(job_id, photos)
    except Exception:
        shutil.rmtree(store.job_dir(job_id), ignore_errors=True)
        raise

    store.update(
        job_id,
        status="preview_processing",
        progress=1,
        message="正在先为上传图片去背景",
    )
    background_tasks.add_task(_run_preparation, job_id, saved_paths)
    return _public_job(store.read(job_id))


@app.post("/api/pets/generate")
async def generate_pet(
    background_tasks: BackgroundTasks,
    photos: List[UploadFile] = File(...),
    name: str = Form("我的宠物"),
    build_exe: bool = Form(False),
    selected_actions: Optional[List[str]] = Form(default=None),
    session_id: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
    x_admin_token: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    context = _request_context(session_id, x_admin_token)
    if not photos or len(photos) > settings.max_files:
        raise HTTPException(400, f"请上传 1-{settings.max_files} 张图片")

    safe_name = _safe_name(name)
    selected_actions = _normalize_requested_actions(selected_actions)
    record = store.new_job(
        safe_name,
        len(photos),
        build_exe,
        selected_actions,
        owner_id=context["id"],
        owner_name=context["username"],
        ai_config=_ai_snapshot_for_context(context),
    )
    job_id = record["id"]
    try:
        saved_paths = await _save_uploaded_photos(job_id, photos)
    except Exception:
        shutil.rmtree(store.job_dir(job_id), ignore_errors=True)
        raise

    background_tasks.add_task(
        _run_generation,
        job_id,
        safe_name,
        saved_paths,
        build_exe,
        selected_actions,
    )
    return _public_job(store.read(job_id))


@app.post("/api/jobs/{job_id}/generate")
def generate_from_preview(
    job_id: str,
    payload: GenerateFromPreviewRequest,
    background_tasks: BackgroundTasks,
    session_id: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
    x_admin_token: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    """Start action generation after the user confirms cutout previews."""

    try:
        record = store.read(job_id)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "任务不存在")
    context = _request_context(session_id, x_admin_token, record)

    if record.get("status") != "preview_ready":
        raise HTTPException(409, "请先完成去背景预览")

    try:
        preview_paths = _prepared_preview_paths(job_id, record)
    except FileNotFoundError:
        raise HTTPException(409, "去背景预览文件不存在，请重新上传")

    name = _safe_name(payload.name or record.get("name", "我的宠物"))
    selected_actions = _normalize_requested_actions(
        payload.selected_actions
        if payload.selected_actions is not None
        else record.get("selected_actions")
    )
    build_exe = bool(payload.build_exe or record.get("build_exe", False))
    store.update(
        job_id,
        name=name,
        build_exe=build_exe,
        selected_actions=selected_actions,
        status="processing",
        progress=5,
        message="已确认去背景预览，正在生成动作资源",
        error="",
    )
    background_tasks.add_task(
        _run_generation,
        job_id,
        name,
        preview_paths,
        build_exe,
        selected_actions,
    )
    return _public_job(store.read(job_id))


@app.post("/api/jobs/{job_id}/resume")
def resume_failed_generation(
    job_id: str,
    payload: GenerateFromPreviewRequest,
    background_tasks: BackgroundTasks,
    session_id: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
    x_admin_token: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    """Resume a failed/cancelled/interrupted task from a persisted checkpoint."""

    try:
        record = store.read(job_id)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "任务不存在")
    context = _request_context(session_id, x_admin_token, record)

    if (
        record.get("status") in {"cancelled", "interrupted"}
        and record.get("package_path")
        and str((record.get("checkpoint") or {}).get("stage", "")) == "building"
    ):
        package = Path(str(record["package_path"]))
        if package.exists() and record.get("artifact_kind") in {"zip", "exe"}:
            if settings.build_mode != "worker":
                raise HTTPException(409, "当前部署未启用 Windows Worker")
            if not settings.worker_token:
                raise HTTPException(503, "Windows Worker 未配置 WORKER_TOKEN")
            resumed = store.update(
                job_id,
                status="ready_for_build",
                cancel_requested=False,
                message="已从打包断点恢复，等待 Windows Worker",
                resume_count=int(record.get("resume_count", 0)) + 1,
                error="",
            )
            return _public_job(resumed, include_owner=context["role"] == "admin")

    checkpoint = record.get("checkpoint") or {}
    if (
        record.get("status") in {"cancelled", "interrupted"}
        and record.get("package_path")
        and str(checkpoint.get("stage", "")) == "resource_edit"
    ):
        _ensure_editable_resource_package(record)
        operation = str(checkpoint.get("operation") or "")
        resume_count = int(record.get("resume_count", 0)) + 1
        common_changes = {
            "phase": "resource_edit",
            "progress": max(98, int(record.get("progress", 98))),
            "error": "",
            "cancel_requested": False,
            "resume_count": resume_count,
            "heartbeat_at": _utc_now(),
        }
        if operation == "regenerate":
            role = str(checkpoint.get("role") or "")
            try:
                index = int(checkpoint.get("index", -1))
            except (TypeError, ValueError):
                index = -1
            meta = _resource_frame_meta(record, role, index)
            if role not in ROLES or index < 0 or meta is None:
                raise HTTPException(409, "动作帧重新生成断点已失效")
            resumed = store.update(
                job_id,
                **common_changes,
                status="resource_regenerating",
                message=f"已从断点恢复，正在重新生成 {role}_{index}.png",
            )
            background_tasks.add_task(_run_resource_regeneration, job_id, meta)
            return _public_job(resumed, include_owner=context["role"] == "admin")

        if operation == "remove":
            raw_frames = checkpoint.get("frames")
            if not isinstance(raw_frames, dict) or not raw_frames:
                raise HTTPException(409, "动作帧删除断点已失效")
            requested: Dict[str, List[int]] = {}
            try:
                for role, raw_indices in raw_frames.items():
                    if role not in ROLES or not isinstance(raw_indices, list):
                        raise ValueError
                    indices = sorted(set(int(index) for index in raw_indices))
                    if not indices:
                        raise ValueError
                    requested[role] = indices
            except (TypeError, ValueError):
                raise HTTPException(409, "动作帧删除断点已失效")
            resumed = store.update(
                job_id,
                **common_changes,
                status="resource_editing",
                message="已从断点恢复，正在继续删除动作帧",
            )
            background_tasks.add_task(_run_resource_frame_removal, job_id, requested)
            return _public_job(resumed, include_owner=context["role"] == "admin")

        if operation == "insert":
            role = str(checkpoint.get("role") or "")
            try:
                after_index = int(checkpoint.get("after_index", -1))
            except (TypeError, ValueError):
                after_index = -1
            if role not in ROLES or after_index < 0:
                raise HTTPException(409, "动作帧插入断点已失效")
            resumed = store.update(
                job_id,
                **common_changes,
                status="resource_editing",
                message=f"已从断点恢复，正在根据 {role}_{after_index}.png 插入动作帧",
            )
            background_tasks.add_task(
                _run_resource_frame_insertion,
                job_id,
                role,
                after_index,
            )
            return _public_job(resumed, include_owner=context["role"] == "admin")

        raise HTTPException(409, "资源编辑断点已失效")

    if (
        record.get("status") in {"failed", "cancelled", "interrupted"}
        and not record.get("preview_paths")
        and str(checkpoint.get("stage", "")) in {"queued", "preparation"}
    ):
        input_paths = _input_photo_paths(job_id)
        if not input_paths:
            raise HTTPException(409, "没有找到可恢复的上传图片")
        resume_count = int(record.get("resume_count", 0)) + 1
        store.update(
            job_id,
            status="preview_processing",
            progress=int(record.get("progress", 1)),
            phase="preparation",
            message="已从去背景断点恢复",
            error="",
            cancel_requested=False,
            resume_count=resume_count,
        )
        background_tasks.add_task(_run_preparation, job_id, input_paths)
        return _public_job(store.read(job_id), include_owner=context["role"] == "admin")

    if not _can_resume_from_preview(record):
        raise HTTPException(409, "当前任务没有可复用的去背景预览或可恢复断点")
    try:
        preview_paths = _prepared_preview_paths(job_id, record)
    except FileNotFoundError:
        raise HTTPException(409, "去背景预览文件不存在，请重新上传")

    name = _safe_name(payload.name or record.get("name", "我的宠物"))
    selected_actions = _normalize_requested_actions(
        payload.selected_actions
        if payload.selected_actions is not None
        else record.get("selected_actions")
    )
    build_exe = bool(payload.build_exe or record.get("build_exe", False))
    resume_count = int(record.get("resume_count", 0)) + 1
    store.update(
        job_id,
        name=name,
        build_exe=build_exe,
        selected_actions=selected_actions,
        status="processing",
        progress=5,
        message="已复用去背景预览，正在继续生成动作资源",
        error="",
        resume_count=resume_count,
        cancel_requested=False,
    )
    background_tasks.add_task(
        _run_generation,
        job_id,
        name,
        preview_paths,
        build_exe,
        selected_actions,
    )
    return _public_job(store.read(job_id), include_owner=context["role"] == "admin")


@app.get("/api/jobs/{job_id}")
def get_job(
    job_id: str,
    session_id: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
    x_admin_token: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    store.recover_stale_jobs()
    try:
        record = store.read(job_id)
        context = _request_context(session_id, x_admin_token, record)
        return _public_job(record, include_owner=context["role"] == "admin")
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "任务不存在")


@app.get("/api/jobs")
def list_jobs(
    session_id: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
    x_admin_token: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    store.recover_stale_jobs()
    context = _request_context(session_id, x_admin_token)
    records = store.list_jobs(None if context["role"] == "admin" else context["id"])
    return {
        "jobs": [
            _public_job(record, include_events=False, include_owner=context["role"] == "admin")
            for record in records
        ],
        "admin": context["role"] == "admin",
    }


@app.get("/api/jobs/{job_id}/events")
def get_job_events(
    job_id: str,
    session_id: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
    x_admin_token: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    try:
        record = store.read(job_id)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "任务不存在")
    _request_context(session_id, x_admin_token, record)
    return {"job_id": job_id, "events": store.events(job_id)}


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(
    job_id: str,
    session_id: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
    x_admin_token: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    try:
        record = store.read(job_id)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "任务不存在")
    context = _request_context(session_id, x_admin_token, record)
    updated = store.request_cancel(job_id)
    return _public_job(updated, include_owner=context["role"] == "admin")


@app.get("/api/jobs/{job_id}/preview/{asset_path:path}")
def preview_job_asset(
    job_id: str,
    asset_path: str,
    session_id: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
    x_admin_token: Optional[str] = Header(default=None),
):
    try:
        record = store.read(job_id)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "任务不存在")
    _request_context(session_id, x_admin_token, record)

    relative = Path(asset_path)
    normalized = relative.as_posix()
    known_paths = {
        str(path).replace("\\", "/")
        for path in (
            list(record.get("preview_paths", []))
            + list(record.get("resource_preview_paths", []))
        )
    }
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or normalized not in known_paths
        or relative.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}
    ):
        raise HTTPException(404, "预览图片不存在")

    job_root = store.job_dir(job_id).resolve()
    target = (job_root / relative).resolve()
    if job_root not in target.parents or not target.is_file():
        raise HTTPException(404, "预览图片不存在")
    media_type = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }[relative.suffix.lower()]
    return FileResponse(str(target), media_type=media_type)


@app.post("/api/jobs/{job_id}/previews/cutout/{index}/regenerate")
def regenerate_cutout_preview(
    job_id: str,
    index: int,
    background_tasks: BackgroundTasks,
    session_id: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
    x_admin_token: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    try:
        record = store.read(job_id)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "任务不存在")
    _request_context(session_id, x_admin_token, record)

    if record.get("status") != "preview_ready":
        raise HTTPException(409, "当前任务不在可重新生成去背景预览的状态")
    if index < 0 or index >= int(record.get("photo_count", 0)):
        raise HTTPException(404, "预览图片不存在")

    preview_path = f"prepared/reference_{index}.png"
    if preview_path not in record.get("preview_paths", []):
        raise HTTPException(404, "预览图片不存在")
    source = _input_photo_path(job_id, index)
    if source is None:
        raise HTTPException(404, "原始图片不存在")

    store.update(
        job_id,
        status="preview_regenerating",
        progress=50,
        phase="preparation",
        message=f"正在重新生成第 {index + 1} 张去背景预览",
        checkpoint={"stage": "preparation", "operation": "cutout_regeneration", "index": index},
    )
    background_tasks.add_task(_run_cutout_regeneration, job_id, index, source)
    return _public_job(store.read(job_id))


@app.post("/api/jobs/{job_id}/previews/resource/{role}/{index}/regenerate")
def regenerate_resource_preview(
    job_id: str,
    role: str,
    index: int,
    background_tasks: BackgroundTasks,
    session_id: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
    x_admin_token: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    try:
        record = store.read(job_id)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "任务不存在")
    _request_context(session_id, x_admin_token, record)

    if record.get("status") != "ready":
        raise HTTPException(409, "请在动作资源生成完成后重新生成单帧")
    _ensure_editable_resource_package(record)
    if role not in ROLES or index < 0:
        raise HTTPException(404, "动作帧不存在")
    meta = _resource_frame_meta(record, role, index)
    if meta is None:
        raise HTTPException(404, "动作帧不存在或不支持重新生成")

    prepared_paths = [
        store.job_dir(job_id) / relative
        for relative in record.get("preview_paths", [])
    ]
    if not prepared_paths or not all(path.exists() for path in prepared_paths):
        raise HTTPException(409, "去背景参考图不存在，请重新上传")

    artifact_changes = _prepare_resource_edit(record)
    store.update(
        job_id,
        **artifact_changes,
        status="resource_regenerating",
        progress=98,
        phase="resource_edit",
        message=f"正在重新生成 {role}_{index}.png",
        checkpoint={"stage": "resource_edit", "operation": "regenerate", "role": role, "index": index},
    )
    background_tasks.add_task(_run_resource_regeneration, job_id, meta)
    return _public_job(store.read(job_id))


@app.post("/api/jobs/{job_id}/previews/resource/remove")
def remove_resource_frames(
    job_id: str,
    payload: RemoveResourceFramesRequest,
    background_tasks: BackgroundTasks,
    session_id: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
    x_admin_token: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    """Remove selected action frames and rebuild the downloadable package."""

    try:
        record = store.read(job_id)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "任务不存在")
    _request_context(session_id, x_admin_token, record)

    if record.get("status") != "ready":
        raise HTTPException(409, "请在动作资源生成完成后删除帧")
    _ensure_editable_resource_package(record)
    if not payload.frames:
        raise HTTPException(400, "至少选择一帧动作帧")

    metadata = [
        item
        for item in record.get("resource_frame_meta", [])
        if isinstance(item, dict)
    ]
    requested: Dict[str, List[int]] = {}
    for role, raw_indices in payload.frames.items():
        if role not in ROLES:
            raise HTTPException(400, f"不支持的动作：{role}")
        indices = sorted(set(int(index) for index in raw_indices))
        if not indices:
            raise HTTPException(400, f"{role} 至少选择一帧")
        role_indices = {
            int(item["index"])
            for item in metadata
            if item.get("role") == role and item.get("index") is not None
        }
        if not set(indices).issubset(role_indices):
            raise HTTPException(404, f"{role} 中存在不存在的动作帧")
        if len(role_indices) - len(indices) < 1:
            raise HTTPException(409, f"{role} 至少需要保留一帧")
        requested[role] = indices

    removed_count = sum(len(indices) for indices in requested.values())
    artifact_changes = _prepare_resource_edit(record)
    store.update(
        job_id,
        **artifact_changes,
        status="resource_editing",
        progress=99,
        phase="resource_edit",
        message=f"正在删除 {removed_count} 帧并更新资源包",
        checkpoint={"stage": "resource_edit", "operation": "remove", "frames": requested},
        error="",
    )
    background_tasks.add_task(_run_resource_frame_removal, job_id, requested)
    return _public_job(store.read(job_id))


@app.post("/api/jobs/{job_id}/previews/resource/insert")
def insert_resource_frame(
    job_id: str,
    payload: InsertResourceFrameRequest,
    background_tasks: BackgroundTasks,
    session_id: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
    x_admin_token: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    """Insert one AI-generated frame after an existing action frame."""

    try:
        record = store.read(job_id)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "任务不存在")
    _request_context(session_id, x_admin_token, record)

    if record.get("status") != "ready":
        raise HTTPException(409, "请在动作资源生成完成后插入帧")
    _ensure_editable_resource_package(record)
    if payload.role not in ROLES:
        raise HTTPException(400, f"不支持的动作：{payload.role}")

    role_meta = [
        dict(item)
        for item in record.get("resource_frame_meta", [])
        if isinstance(item, dict) and item.get("role") == payload.role
    ]
    role_meta.sort(key=lambda item: int(item.get("index", 0)))
    if len(role_meta) >= 24:
        raise HTTPException(409, "单个动作最多保留 24 帧")
    if payload.after_index >= len(role_meta) - 1:
        raise HTTPException(409, "只能在两帧之间插入新帧")
    if (
        payload.after_index < 0
        or int(role_meta[payload.after_index].get("index", -1)) != payload.after_index
    ):
        raise HTTPException(404, "参考动作帧不存在")

    artifact_changes = _prepare_resource_edit(record)
    store.update(
        job_id,
        **artifact_changes,
        status="resource_editing",
        progress=99,
        phase="resource_edit",
        message=f"正在根据 {payload.role}_{payload.after_index}.png 插入新帧",
        checkpoint={
            "stage": "resource_edit",
            "operation": "insert",
            "role": payload.role,
            "after_index": payload.after_index,
        },
        error="",
    )
    background_tasks.add_task(
        _run_resource_frame_insertion,
        job_id,
        payload.role,
        payload.after_index,
    )
    return _public_job(store.read(job_id))


@app.post("/api/jobs/{job_id}/build-exe")
def request_exe_build(
    job_id: str,
    session_id: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
    x_admin_token: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    """Queue the Windows build for an already generated resource package."""

    try:
        record = store.read(job_id)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "任务不存在")
    context = _request_context(session_id, x_admin_token, record)

    if settings.build_mode != "worker":
        raise HTTPException(409, "当前服务未启用 Windows Worker，请将 BUILD_MODE 设置为 worker")
    if not settings.worker_token:
        raise HTTPException(503, "Windows Worker 未配置 WORKER_TOKEN")

    package = record.get("package_path")
    if not package or not Path(package).exists():
        raise HTTPException(409, "资源包尚未生成完成")
    if record.get("artifact_kind") not in {"zip", "exe"}:
        raise HTTPException(409, "当前任务没有可重新打包的资源包")
    if record.get("status") not in {"ready", "failed"}:
        raise HTTPException(409, f"当前任务状态为 {record.get('status') or 'unknown'}，暂时不能打包 exe")

    artifact_changes = {}
    if record.get("artifact_kind") == "exe":
        # Rebuilding an unchanged package also invalidates the currently
        # advertised executable so a cancelled upload cannot destroy the
        # user's only recoverable copy.
        artifact_changes = _prepare_resource_edit(record)
    store.update(
        job_id,
        **artifact_changes,
        build_exe=True,
        status="ready_for_build",
        progress=90,
        phase="building",
        message="已提交 Windows exe 打包请求，等待 Worker",
        error="",
        cancel_requested=False,
        checkpoint={"stage": "building", "worker": "queued"},
    )
    return _public_job(store.read(job_id), include_owner=context["role"] == "admin")


@app.get("/api/jobs/{job_id}/download")
def download_job(
    job_id: str,
    session_id: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
    x_admin_token: Optional[str] = Header(default=None),
):
    try:
        record = store.read(job_id)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "任务不存在")
    _request_context(session_id, x_admin_token, record)
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
            "status_url": f"/api/worker/jobs/{record['id']}/status",
            "progress_url": f"/api/worker/jobs/{record['id']}/progress",
        }
    }


@app.get("/api/worker/healthz")
def worker_healthz(x_worker_token: Optional[str] = Header(default=None)) -> Dict[str, bool]:
    """Validate a Worker token without claiming a queued build job."""

    _verify_worker(x_worker_token)
    return {"ok": True}


@app.get("/api/worker/jobs/{job_id}/status")
def worker_job_status(job_id: str, x_worker_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _verify_worker(x_worker_token)
    try:
        record = store.read(job_id)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "任务不存在")
    if record.get("cancel_requested") or record.get("status") == "cancelling":
        record = store.update(
            job_id,
            status="cancelled",
            message="任务已停止，Worker 已结束",
            cancel_requested=True,
            heartbeat_at=_utc_now(),
        )
    return {
        "id": job_id,
        "status": record.get("status"),
        "cancel_requested": bool(record.get("cancel_requested")),
        "message": record.get("message", ""),
    }


@app.post("/api/worker/jobs/{job_id}/progress")
def worker_job_progress(
    job_id: str,
    payload: Optional[Dict[str, Any]] = Body(default=None),
    x_worker_token: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    _verify_worker(x_worker_token)
    try:
        record = store.read(job_id)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "任务不存在")
    if record.get("cancel_requested") or record.get("status") == "cancelling":
        updated = store.update(
            job_id,
            status="cancelled",
            message="任务已停止，Worker 已结束",
            cancel_requested=True,
            heartbeat_at=_utc_now(),
        )
        return {"cancelled": True, "status": updated.get("status")}
    values = payload or {}
    progress = values.get("progress")
    if progress is None:
        progress = record.get("progress", 90)
    try:
        progress = max(90, min(99, int(progress)))
    except (TypeError, ValueError):
        progress = int(record.get("progress", 90))
    message = str(values.get("message") or record.get("message") or "Windows Worker 正在打包 exe")[-4000:]
    updated = store.update(
        job_id,
        progress=progress,
        message=message,
        phase="building",
        heartbeat_at=_utc_now(),
        checkpoint={"stage": "building", "worker": "running", "progress": progress},
    )
    return {"cancelled": False, "status": updated.get("status"), "progress": progress}


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
        record = store.read(job_id)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "任务不存在")
    artifact_dir = store.job_dir(job_id) / "artifact"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    target = artifact_dir / f"{_safe_name(Path(artifact.filename or 'pet').stem)}.exe"
    await _save_upload(artifact, target, limit=250_000_000)
    if record.get("cancel_requested") or store.cancel_requested(job_id):
        try:
            target.unlink()
        except OSError:
            pass
        _mark_job_cancelled(job_id, "任务已停止，已忽略 Worker 生成结果")
        return {"ok": True, "cancelled": True}
    stale_artifact_path = record.get("stale_artifact_path")
    updated = store.update(
        job_id,
        status="ready",
        progress=100,
        message="Windows exe 已生成",
        artifact_path=str(target),
        artifact_kind="exe",
        stale_artifact_path="",
        phase="complete",
        checkpoint={"stage": "complete", "worker": "finished"},
        cancel_requested=False,
        heartbeat_at=_utc_now(),
    )
    _remove_stale_artifact(stale_artifact_path, target)
    return {"ok": True, "job": _public_job(updated)}


@app.post("/api/worker/jobs/{job_id}/error")
def worker_error(
    job_id: str,
    payload: Optional[Dict[str, str]] = Body(default=None),
    x_worker_token: Optional[str] = Header(default=None),
):
    _verify_worker(x_worker_token)
    try:
        store.read(job_id)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "任务不存在")
    message = (payload or {}).get("message", "Windows Worker 打包失败")[-4000:]
    if store.cancel_requested(job_id):
        _mark_job_cancelled(job_id, "任务已停止，Worker 已结束")
        return {"ok": True, "cancelled": True}
    store.update(
        job_id,
        status="failed",
        phase="building",
        message=message,
        error=message,
        checkpoint={"stage": "building", "worker": "failed"},
        heartbeat_at=_utc_now(),
    )
    return {"ok": True}


async def _run_preparation(job_id: str, paths: List[Path]) -> None:
    """Generate and persist transparent identity previews before animation."""

    prepared_dir = store.job_dir(job_id) / "prepared"
    prepared_dir.mkdir(parents=True, exist_ok=True)
    preview_paths: List[str] = []
    try:
        record = store.read(job_id)
        _check_job_cancelled(job_id)
        task_settings = _settings_for_job(record)
        task_provider = OpenAICompatibleImageProvider(task_settings)
        if not task_provider.available:
            raise ImageGenerationError("AI 图像服务未配置，无法执行去背景预处理")

        for index, source in enumerate(paths):
            _check_job_cancelled(job_id)
            progress = 5 + int(index * 80 / max(1, len(paths)))
            store.update(
                job_id,
                progress=progress,
                phase="preparation",
                message=f"正在为第 {index + 1}/{len(paths)} 张图片去背景",
                heartbeat_at=_utc_now(),
                checkpoint={
                    "stage": "preparation",
                    "completed_photo_indices": [
                        item
                        for item in range(index)
                        if (prepared_dir / f"reference_{item}.png").exists()
                    ],
                    "total_photos": len(paths),
                },
            )
            raw_path = prepared_dir / f"raw_{index}.png"
            preview_path = prepared_dir / f"reference_{index}.png"
            if not preview_path.exists():
                await task_provider.remove_background(source, raw_path)
                _normalize_ai_preview(raw_path, preview_path)
            preview_paths.append(f"prepared/{preview_path.name}")
            store.update(
                job_id,
                checkpoint={
                    "stage": "preparation",
                    "completed_photo_indices": list(range(index + 1)),
                    "total_photos": len(paths),
                },
                heartbeat_at=_utc_now(),
            )

        _check_job_cancelled(job_id)
        store.update(
            job_id,
            status="preview_ready",
            progress=100,
            phase="preparation",
            message="去背景预览已完成，请确认图片后生成动作资源",
            preview_paths=preview_paths,
            error="",
            cancel_requested=False,
            checkpoint={
                "stage": "preview_ready",
                "completed_photo_indices": list(range(len(paths))),
                "total_photos": len(paths),
            },
        )
    except (BuildCancelled, GenerationCancelled):
        _mark_job_cancelled(job_id, "任务已停止，已保存去背景进度")
    except Exception as error:
        if store.cancel_requested(job_id):
            _mark_job_cancelled(job_id, "任务已停止，已保存去背景进度")
            return
        store.update(
            job_id,
            status="failed",
            progress=0,
            phase="preparation",
            message=str(error)[-4000:],
            error=str(error)[-4000:],
        )


async def _run_cutout_regeneration(job_id: str, index: int, source: Path) -> None:
    try:
        _check_job_cancelled(job_id)
        task_provider = OpenAICompatibleImageProvider(_settings_for_job(store.read(job_id)))
        prepared_dir = store.job_dir(job_id) / "prepared"
        raw_path = prepared_dir / f"raw_{index}.png"
        preview_path = prepared_dir / f"reference_{index}.png"
        await task_provider.remove_background(source, raw_path)
        _normalize_ai_preview(raw_path, preview_path)
        _check_job_cancelled(job_id)
        record = store.read(job_id)
        revision = int(record.get("preview_revision", 0)) + 1
        store.update(
            job_id,
            status="preview_ready",
            progress=100,
            message=f"第 {index + 1} 张去背景预览已重新生成",
            preview_revision=revision,
            error="",
        )
    except (BuildCancelled, GenerationCancelled):
        _mark_job_cancelled(job_id, "任务已停止，已保存去背景预览")
    except Exception as error:
        if store.cancel_requested(job_id):
            _mark_job_cancelled(job_id, "任务已停止，已保存去背景预览")
            return
        store.update(
            job_id,
            status="preview_ready",
            progress=100,
            message=f"重新生成去背景预览失败，已保留原图片：{str(error)[-2000:]}",
            error=str(error)[-4000:],
        )


async def _run_resource_regeneration(job_id: str, target_meta: Dict[str, Any]) -> None:
    try:
        record = store.read(job_id)
        _check_job_cancelled(job_id)
        task_settings = _settings_for_job(record)
        task_provider = OpenAICompatibleImageProvider(task_settings)
        role = str(target_meta["role"])
        index = int(target_meta["index"])
        frame_count = int(target_meta.get("frame_count") or 1)
        job_dir = store.job_dir(job_id)
        prepared_paths = [
            job_dir / relative
            for relative in record.get("preview_paths", [])
        ]
        role_inputs = assign_roles(prepared_paths)
        identity_reference = prepared_paths[0]
        raw_path = job_dir / "ai" / role / f"{role}_{index}.png"
        await task_provider.generate_action_frame(
            role_inputs[role],
            role,
            raw_path,
            identity_reference=identity_reference,
            frame_index=index,
            frame_count=frame_count,
            pose_consistency=getattr(task_settings, "pose_consistency", True),
        )
        cleaned_path = job_dir / "ai" / role / f"{role}_{index}_cutout.png"
        try:
            replacement_source = await ai_cutout_frame(task_provider, raw_path, cleaned_path)
        except Exception:
            # The normalizer still has a simple connected-background fallback;
            # keep the existing resource usable if the extra AI pass fails.
            replacement_source = raw_path
        _check_job_cancelled(job_id)

        all_meta = [
            dict(item)
            for item in record.get("resource_frame_meta", [])
            if isinstance(item, dict)
        ]
        updated_meta: List[Dict[str, Any]] = []
        role_meta: List[Dict[str, Any]] = []
        for item in all_meta:
            if item.get("role") == role:
                if int(item.get("index", -1)) == index:
                    item["source_path"] = _relative_job_path(job_dir, replacement_source)
                    item["raw_source_path"] = _relative_job_path(job_dir, raw_path)
                role_meta.append(item)
            updated_meta.append(item)
        role_meta.sort(key=lambda item: int(item.get("index", 0)))
        if not role_meta:
            raise ValueError("动作帧元数据不存在")

        sources = [job_dir / str(item["source_path"]) for item in role_meta]
        destinations = [job_dir / str(item["asset_path"]) for item in role_meta]
        if not all(path.exists() for path in sources):
            raise ValueError("动作帧参考文件不存在")
        normalize_pet_sequence(
            sources,
            destinations,
            canvas_size=320,
            background_mode="simple",
            anchor=ROLE_ANCHORS.get(role, "center"),
            subject_scale=0.96,
        )
        _check_job_cancelled(job_id)

        package_dir = job_dir / "package"
        zip_path = Path(record["package_path"])
        _rebuild_package_archive(package_dir, zip_path)
        revision = int(record.get("resource_preview_revision", 0)) + 1
        store.update(
            job_id,
            status="ready",
            progress=100,
            message=f"{role}_{index}.png 已重新生成，资源包已更新",
            resource_frame_meta=updated_meta,
            resource_preview_revision=revision,
            artifact_path=str(zip_path),
            artifact_kind="zip",
            build_exe=True,
            error="",
        )
    except (BuildCancelled, GenerationCancelled):
        _mark_job_cancelled(job_id, "任务已停止，已保存当前资源断点")
    except Exception as error:
        if store.cancel_requested(job_id):
            _mark_job_cancelled(job_id, "任务已停止，已保存当前资源断点")
            return
        store.update(
            job_id,
            status="ready",
            progress=100,
            message=f"重新生成动作帧失败，已保留原资源：{str(error)[-2000:]}",
            error=str(error)[-4000:],
        )


def _run_resource_frame_removal(
    job_id: str,
    requested: Dict[str, List[int]],
) -> None:
    """Apply a batch frame edit and keep all package metadata in sync."""

    try:
        record = store.read(job_id)
        _check_job_cancelled(job_id)
        task_settings = _settings_for_job(record)
        job_dir = store.job_dir(job_id)
        package_dir = job_dir / "package"
        config_path = package_dir / "pet_config.json"
        if not config_path.is_file():
            raise ValueError("资源包配置文件不存在")

        all_meta = [
            dict(item)
            for item in record.get("resource_frame_meta", [])
            if isinstance(item, dict)
        ]
        updated_meta = all_meta
        config_data = json.loads(config_path.read_text(encoding="utf-8"))
        config_assets = config_data.get("assets")
        if not isinstance(config_assets, dict):
            config_assets = {}

        for role, removed_indices in requested.items():
            _check_job_cancelled(job_id)
            role_items = [
                item
                for item in all_meta
                if item.get("role") == role
            ]
            role_items.sort(key=lambda item: int(item.get("index", 0)))
            removed = set(removed_indices)
            remaining = [
                item for item in role_items
                if int(item.get("index", -1)) not in removed
            ]
            if not remaining:
                raise ValueError(f"{role} 至少需要保留一帧")
            remapped_role_meta, new_asset_paths = _rewrite_resource_role_assets(
                job_dir,
                package_dir,
                role,
                remaining,
            )
            updated_meta = _replace_resource_role_metadata(
                updated_meta,
                role,
                remapped_role_meta,
            )
            config_assets[role] = new_asset_paths

        animation_config = config_data.get("animation")
        if not isinstance(animation_config, dict):
            animation_config = {}
        animation_manifest = write_animation_bundle(
            package_dir,
            config_assets,
            mode=animation_config.get("mode", "hybrid"),
            fps=animation_config.get("fps", getattr(task_settings, "animation_fps", 12)),
            frame_repeat=animation_config.get("frame_repeat", getattr(task_settings, "frame_repeat", 1)),
        )
        config_data["assets"] = config_assets
        config_data["animation"] = animation_manifest
        generation = config_data.get("generation")
        if not isinstance(generation, dict):
            generation = {}
        frame_counts = generation.get("frame_counts")
        if not isinstance(frame_counts, dict):
            frame_counts = {}
        for role in requested:
            frame_counts[role] = sum(1 for item in updated_meta if item.get("role") == role)
        generation["frame_counts"] = frame_counts
        generation["removed_frame_count"] = int(generation.get("removed_frame_count", 0)) + sum(
            len(indices) for indices in requested.values()
        )
        config_data["generation"] = generation
        config_path.write_text(
            json.dumps(config_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        zip_path = Path(record["package_path"])
        _check_job_cancelled(job_id)
        _rebuild_package_archive(package_dir, zip_path)
        resource_preview_paths = [
            str(item["asset_path"])
            for item in updated_meta
            if item.get("asset_path")
        ]
        revision = int(record.get("resource_preview_revision", 0)) + 1
        removed_count = sum(len(indices) for indices in requested.values())
        store.update(
            job_id,
            status="ready",
            progress=100,
            message=f"已删除 {removed_count} 帧，资源包已更新",
            resource_preview_paths=resource_preview_paths,
            resource_frame_meta=updated_meta,
            resource_preview_revision=revision,
            frame_repeat=animation_manifest.get("frame_repeat", 1),
            artifact_path=str(zip_path),
            artifact_kind="zip",
            build_exe=True,
            error="",
        )
    except (BuildCancelled, GenerationCancelled):
        _mark_job_cancelled(job_id, "任务已停止，已保存当前资源断点")
    except Exception as error:
        if store.cancel_requested(job_id):
            _mark_job_cancelled(job_id, "任务已停止，已保存当前资源断点")
            return
        store.update(
            job_id,
            status="ready",
            progress=100,
            message=f"删除动作帧失败，已保留当前资源：{str(error)[-2000:]}",
            error=str(error)[-4000:],
        )


def _rewrite_resource_role_assets(
    job_dir: Path,
    package_dir: Path,
    role: str,
    role_items: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Normalize one edited role and return its remapped metadata/assets."""

    frame_count = len(role_items)
    remapped: List[Dict[str, Any]] = []
    sources: List[Path] = []
    destinations: List[Path] = []
    asset_paths: List[str] = []
    job_root = job_dir.resolve()
    for new_index, item in enumerate(role_items):
        updated = dict(item)
        asset_path = f"package/assets/{role}_{new_index}.png"
        updated.update(
            index=new_index,
            frame_count=frame_count,
            asset_path=asset_path,
        )
        source = (job_dir / str(item["source_path"])).resolve()
        if job_root not in source.parents or not source.is_file():
            raise ValueError(f"{role} 的参考文件不存在")
        remapped.append(updated)
        sources.append(source)
        destinations.append(package_dir / "assets" / f"{role}_{new_index}.png")
        asset_paths.append(f"assets/{role}_{new_index}.png")

    normalize_pet_sequence(
        sources,
        destinations,
        canvas_size=320,
        background_mode="simple",
        anchor=ROLE_ANCHORS.get(role, "center"),
        subject_scale=0.96,
    )
    assets_dir = package_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    desired = {path.resolve() for path in destinations}
    for stale in assets_dir.glob(f"{role}_*.png"):
        if stale.resolve() not in desired:
            stale.unlink()
    return remapped, asset_paths


def _replace_resource_role_metadata(
    all_meta: List[Dict[str, Any]],
    role: str,
    role_meta: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Replace one role's contiguous block while preserving other roles."""

    updated_meta: List[Dict[str, Any]] = []
    inserted = False
    for item in all_meta:
        if item.get("role") != role:
            updated_meta.append(item)
            continue
        if not inserted:
            updated_meta.extend(role_meta)
            inserted = True
    if not inserted:
        updated_meta.extend(role_meta)
    return updated_meta


async def _run_resource_frame_insertion(
    job_id: str,
    role: str,
    after_index: int,
) -> None:
    """Generate a frame from the previous frame and rebuild the package."""

    try:
        record = store.read(job_id)
        _check_job_cancelled(job_id)
        task_settings = _settings_for_job(record)
        task_provider = OpenAICompatibleImageProvider(task_settings)
        job_dir = store.job_dir(job_id)
        package_dir = job_dir / "package"
        config_path = package_dir / "pet_config.json"
        if not config_path.is_file():
            raise ValueError("资源包配置文件不存在")

        all_meta = [
            dict(item)
            for item in record.get("resource_frame_meta", [])
            if isinstance(item, dict)
        ]
        role_meta = [item for item in all_meta if item.get("role") == role]
        role_meta.sort(key=lambda item: int(item.get("index", 0)))
        if after_index < 0 or after_index >= len(role_meta) - 1:
            raise ValueError("只能在两帧之间插入新帧")
        previous = role_meta[after_index]
        previous_source = (job_dir / str(previous["source_path"])).resolve()
        if job_dir.resolve() not in previous_source.parents or not previous_source.is_file():
            raise ValueError("前一帧参考文件不存在")

        _prepared_preview_paths(job_id, record)
        # Insertion is deliberately a one-reference operation: the frame
        # immediately before the gap is the only continuity reference.
        identity_reference = None
        insertion_index = after_index + 1
        token = uuid.uuid4().hex
        raw_path = job_dir / "ai" / role / f"{role}_inserted_{token}.png"
        await task_provider.generate_action_frame(
            [previous_source],
            role,
            raw_path,
            identity_reference=identity_reference,
            frame_index=insertion_index,
            frame_count=len(role_meta) + 1,
            pose_consistency=getattr(task_settings, "pose_consistency", True),
            continuity_reference=True,
        )
        cleaned_path = raw_path.with_name(f"{raw_path.stem}_cutout.png")
        try:
            replacement_source = await ai_cutout_frame(task_provider, raw_path, cleaned_path)
        except Exception:
            replacement_source = raw_path
        _check_job_cancelled(job_id)

        inserted = {
            "asset_path": "",
            "source_path": _relative_job_path(job_dir, replacement_source),
            "raw_source_path": _relative_job_path(job_dir, raw_path),
            "role": role,
            "index": insertion_index,
            "frame_count": len(role_meta) + 1,
            "inserted": True,
        }
        desired_role_items = role_meta[:insertion_index] + [inserted] + role_meta[insertion_index:]
        remapped_role_meta, role_asset_paths = _rewrite_resource_role_assets(
            job_dir,
            package_dir,
            role,
            desired_role_items,
        )
        _check_job_cancelled(job_id)
        updated_meta = _replace_resource_role_metadata(
            all_meta,
            role,
            remapped_role_meta,
        )

        config_data = json.loads(config_path.read_text(encoding="utf-8"))
        config_assets = config_data.get("assets")
        if not isinstance(config_assets, dict):
            config_assets = {}
        config_assets[role] = role_asset_paths
        animation_config = config_data.get("animation")
        if not isinstance(animation_config, dict):
            animation_config = {}
        animation_manifest = write_animation_bundle(
            package_dir,
            config_assets,
            mode=animation_config.get("mode", "hybrid"),
            fps=animation_config.get("fps", getattr(task_settings, "animation_fps", 12)),
            frame_repeat=animation_config.get("frame_repeat", getattr(task_settings, "frame_repeat", 1)),
        )
        config_data["assets"] = config_assets
        config_data["animation"] = animation_manifest
        generation = config_data.get("generation")
        if not isinstance(generation, dict):
            generation = {}
        frame_counts = generation.get("frame_counts")
        if not isinstance(frame_counts, dict):
            frame_counts = {}
        frame_counts[role] = len(remapped_role_meta)
        generation["frame_counts"] = frame_counts
        generation["inserted_frame_count"] = int(generation.get("inserted_frame_count", 0)) + 1
        config_data["generation"] = generation
        config_path.write_text(
            json.dumps(config_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        zip_path = Path(record["package_path"])
        _rebuild_package_archive(package_dir, zip_path)
        resource_preview_paths = [
            str(item["asset_path"])
            for item in updated_meta
            if item.get("asset_path")
        ]
        revision = int(record.get("resource_preview_revision", 0)) + 1
        store.update(
            job_id,
            status="ready",
            progress=100,
            message=f"已在 {role}_{after_index}.png 后插入一帧，资源包已更新",
            resource_preview_paths=resource_preview_paths,
            resource_frame_meta=updated_meta,
            resource_preview_revision=revision,
            frame_repeat=animation_manifest.get("frame_repeat", 1),
            artifact_path=str(zip_path),
            artifact_kind="zip",
            build_exe=True,
            error="",
        )
    except (BuildCancelled, GenerationCancelled):
        _mark_job_cancelled(job_id, "任务已停止，已保存当前资源断点")
    except Exception as error:
        if store.cancel_requested(job_id):
            _mark_job_cancelled(job_id, "任务已停止，已保存当前资源断点")
            return
        store.update(
            job_id,
            status="ready",
            progress=100,
            message=f"插入动作帧失败，已保留当前资源：{str(error)[-2000:]}",
            error=str(error)[-4000:],
        )


async def _run_generation(
    job_id: str,
    name: str,
    paths: List[Path],
    build_exe: bool,
    selected_actions: List[str],
) -> None:
    record = store.read(job_id)
    if record.get("cancel_requested"):
        _mark_job_cancelled(job_id, "任务已停止，未开始新的处理")
        return
    task_settings = _settings_for_job(record)
    task_provider = OpenAICompatibleImageProvider(task_settings)
    resume_checkpoint = dict(record.get("checkpoint") or {})
    store.update(
        job_id,
        status="processing",
        progress=max(5, int(record.get("progress", 0))),
        phase="actions",
        message="正在准备照片",
        heartbeat_at=_utc_now(),
    )

    def progress(value: int, message: str) -> None:
        _check_job_cancelled(job_id)
        store.update(job_id, progress=value, message=message)

    def write_checkpoint(value: Dict[str, object]) -> None:
        _check_job_cancelled(job_id)
        checkpoint_message = _checkpoint_message(value)
        store.update(
            job_id,
            checkpoint=dict(value),
            phase=str(value.get("stage", "actions")),
            message=checkpoint_message,
            heartbeat_at=_utc_now(),
        )

    try:
        result = await build_pet_package(
            store.job_dir(job_id),
            name,
            paths,
            task_provider,
            task_settings,
            progress,
            selected_actions=selected_actions,
            should_cancel=lambda: store.cancel_requested(job_id),
            checkpoint=write_checkpoint,
            resume_checkpoint=resume_checkpoint,
        )
        _check_job_cancelled(job_id)
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
            animation_mode=result.get("animation_mode", task_settings.animation_mode),
            frame_repeat=result.get("frame_repeat", task_settings.frame_repeat),
            animation_fps=result.get("animation_fps", task_settings.animation_fps),
            selected_actions=result.get("selected_actions", selected_actions),
            resource_preview_paths=result.get("resource_preview_paths", []),
            resource_frame_meta=result.get("resource_frame_meta", []),
            phase="building" if next_status == "ready_for_build" else "complete",
            checkpoint={
                "stage": "building" if next_status == "ready_for_build" else "complete",
                "completed_roles": list(ROLES),
            },
            cancel_requested=False,
            heartbeat_at=_utc_now(),
        )
    except (BuildCancelled, GenerationCancelled):
        _mark_job_cancelled(job_id, "任务已停止，已保存当前断点")
    except Exception as error:
        if store.cancel_requested(job_id):
            _mark_job_cancelled(job_id, "任务已停止，已保存当前断点")
            return
        store.update(
            job_id,
            status="failed",
            phase="actions",
            message=str(error)[-4000:],
            error=str(error)[-4000:],
            heartbeat_at=_utc_now(),
        )


async def _save_uploaded_photos(job_id: str, photos: List[UploadFile]) -> List[Path]:
    input_dir = store.job_dir(job_id) / "input"
    saved_paths: List[Path] = []
    for index, upload in enumerate(photos):
        suffix = Path(upload.filename or "photo.png").suffix.lower()
        if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}:
            raise HTTPException(400, f"不支持的图片格式：{suffix or 'unknown'}")
        target = input_dir / f"photo_{index}{suffix}"
        await _save_upload(upload, target)
        saved_paths.append(target)
    return saved_paths


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


def _normalize_ai_preview(source: Path, destination: Path) -> None:
    from PIL import Image

    with Image.open(source) as image:
        alpha = image.convert("RGBA").getchannel("A")
        background_mode = "none" if alpha.getextrema() != (255, 255) else "simple"

    normalize_pet_image(
        source,
        destination,
        canvas_size=512,
        background_mode=background_mode,
        anchor="center",
        subject_scale=0.96,
    )


def _input_photo_path(job_id: str, index: int) -> Optional[Path]:
    input_dir = store.job_dir(job_id) / "input"
    candidates = sorted(input_dir.glob(f"photo_{index}.*"))
    return candidates[0] if len(candidates) == 1 else None


def _input_photo_paths(job_id: str) -> List[Path]:
    input_dir = store.job_dir(job_id) / "input"
    return sorted(
        path
        for path in input_dir.glob("photo_*")
        if path.is_file() and path.suffix.lower() in {
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
            ".bmp",
            ".gif",
            ".tif",
            ".tiff",
        }
    )


def _prepared_preview_paths(job_id: str, record: Dict[str, Any]) -> List[Path]:
    """Resolve stored cutout previews and reject missing or escaped files."""

    relative_paths = list(record.get("preview_paths", []))
    if not relative_paths:
        raise FileNotFoundError("preview paths missing")

    job_root = store.job_dir(job_id).resolve()
    paths: List[Path] = []
    for relative in relative_paths:
        target = (job_root / str(relative)).resolve()
        if job_root not in target.parents or not target.is_file():
            raise FileNotFoundError(str(target))
        paths.append(target)
    return paths


def _can_resume_from_preview(record: Dict[str, Any]) -> bool:
    """Whether the task has enough persisted data for a user-visible resume."""

    return (
        record.get("status") in {"failed", "cancelled", "interrupted"}
        and not record.get("package_path")
        and (
            bool(record.get("preview_paths"))
            or str((record.get("checkpoint") or {}).get("stage", ""))
            in {"queued", "preparation", "actions", "packaging"}
        )
    )


def _resource_frame_meta(
    record: Dict[str, Any],
    role: str,
    index: int,
) -> Optional[Dict[str, Any]]:
    for item in record.get("resource_frame_meta", []):
        if not isinstance(item, dict):
            continue
        try:
            item_index = int(item.get("index", -1))
        except (TypeError, ValueError):
            continue
        if item.get("role") == role and item_index == index:
            return dict(item)
    return None


def _ensure_editable_resource_package(record: Dict[str, Any]) -> Path:
    """Validate that the persisted resource package can still be edited.

    A completed EXE does not make the source package immutable.  The package
    remains the source of truth for future edits and Worker rebuilds.
    """

    if record.get("artifact_kind") not in {"zip", "exe"}:
        raise HTTPException(409, "当前任务没有可编辑的资源包")
    package_value = record.get("package_path")
    package = Path(str(package_value)) if package_value else None
    if package is None or not package.is_file():
        raise HTTPException(409, "资源包不存在，无法编辑动作帧")
    return package


def _prepare_resource_edit(record: Dict[str, Any]) -> Dict[str, Any]:
    """Invalidate a downloaded EXE while retaining it as a rollback artifact.

    Once an asset changes, the old EXE must not be advertised as the current
    result.  Move it aside instead of deleting it, so a failed edit/build can
    still be recovered from the server's job directory.
    """

    package = _ensure_editable_resource_package(record)
    changes: Dict[str, Any] = {
        "artifact_path": str(package),
        "artifact_kind": "zip",
    }
    existing_stale = record.get("stale_artifact_path")
    if existing_stale:
        changes["stale_artifact_path"] = str(existing_stale)

    if record.get("artifact_kind") != "exe":
        return changes

    artifact_value = record.get("artifact_path")
    artifact = Path(str(artifact_value)) if artifact_value else None
    if artifact is None or not artifact.is_file():
        return changes
    stale = artifact.with_name(
        f"{artifact.stem}.stale-{uuid.uuid4().hex[:12]}{artifact.suffix}"
    )
    try:
        artifact.replace(stale)
    except OSError:
        # The old file is not part of the new package state.  Leave it in
        # place if the filesystem refuses the move; the next Worker upload
        # will safely overwrite the canonical artifact path.
        return changes
    changes["stale_artifact_path"] = str(stale)
    return changes


def _remove_stale_artifact(value: Any, current: Path) -> None:
    """Best-effort cleanup after a replacement EXE has uploaded."""

    if not value:
        return
    try:
        stale = Path(str(value)).resolve()
        if stale != current.resolve() and stale.is_file():
            stale.unlink()
    except (OSError, RuntimeError, ValueError):
        pass


def _relative_job_path(job_dir: Path, source: Path) -> str:
    try:
        return source.resolve().relative_to(job_dir.resolve()).as_posix()
    except ValueError:
        return source.name


def _rebuild_package_archive(package_dir: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in package_dir.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(package_dir).as_posix())


def _artifact_path(record: Dict[str, Any]) -> Optional[Path]:
    path = record.get("artifact_path")
    return Path(path) if path else None


def _public_job(
    record: Dict[str, Any],
    include_events: bool = True,
    include_owner: bool = False,
) -> Dict[str, Any]:
    result = dict(record)
    preview_paths = list(result.pop("preview_paths", []))
    resource_preview_paths = list(result.pop("resource_preview_paths", []))
    resource_frame_meta = [
        item
        for item in result.pop("resource_frame_meta", [])
        if isinstance(item, dict)
    ]
    result.pop("ai_config", None)
    owner_id = result.pop("owner_id", None)
    owner_name = result.pop("owner_name", None)
    if include_owner and owner_id:
        result["owner"] = {"id": owner_id, "username": owner_name or ""}
    if not include_events:
        result.pop("events", None)
    else:
        result["events"] = [
            dict(item)
            for item in result.get("events", [])
            if isinstance(item, dict)
        ]
    result.pop("artifact_path", None)
    result.pop("package_path", None)
    result.pop("stale_artifact_path", None)
    result["preview_images"] = _preview_entries(
        record["id"],
        preview_paths,
        kind="cutout",
        revision=int(record.get("preview_revision", 0)),
    )
    result["resource_preview_images"] = _resource_preview_entries(
        record["id"],
        resource_preview_paths,
        resource_frame_meta,
        revision=int(record.get("resource_preview_revision", 0)),
    )
    if record.get("artifact_path"):
        result["download_url"] = f"/api/jobs/{record['id']}/download"
    if _can_resume_from_preview(record) or (
        record.get("status") in {"cancelled", "interrupted"}
        and record.get("package_path")
        and str((record.get("checkpoint") or {}).get("stage", "")) == "building"
    ) or (
        record.get("status") in {"cancelled", "interrupted"}
        and record.get("package_path")
        and str((record.get("checkpoint") or {}).get("stage", "")) == "resource_edit"
    ):
        result["resume_url"] = f"/api/jobs/{record['id']}/resume"
    if record.get("status") in {
        "queued",
        "preview_processing",
        "preview_regenerating",
        "processing",
        "resource_regenerating",
        "resource_editing",
        "ready_for_build",
        "building",
        "cancelling",
    }:
        result["cancel_url"] = f"/api/jobs/{record['id']}/cancel"
    return result


def _preview_entries(
    job_id: str,
    paths: List[str],
    kind: str,
    revision: int = 0,
) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for index, path in enumerate(paths):
        url = f"/api/jobs/{job_id}/preview/{quote(path, safe='/')}"
        if revision:
            url += f"?v={revision}"
        entries.append(
            {
                "name": Path(path).name,
                "url": url,
                "kind": kind,
                "index": index,
                "regenerate_url": f"/api/jobs/{job_id}/previews/cutout/{index}/regenerate",
            }
        )
    return entries


def _resource_preview_entries(
    job_id: str,
    paths: List[str],
    metadata: List[Dict[str, Any]],
    revision: int = 0,
) -> List[Dict[str, Any]]:
    metadata_by_path = {
        str(item.get("asset_path")): item
        for item in metadata
        if item.get("asset_path")
    }
    entries: List[Dict[str, Any]] = []
    for path in paths:
        url = f"/api/jobs/{job_id}/preview/{quote(path, safe='/')}"
        if revision:
            url += f"?v={revision}"
        item = metadata_by_path.get(path, {})
        entry: Dict[str, Any] = {
            "name": Path(path).name,
            "url": url,
            "kind": "resource",
        }
        if item.get("role") is not None and item.get("index") is not None:
            role = str(item["role"])
            index = int(item["index"])
            entry.update(
                role=role,
                index=index,
                regenerate_url=(
                    f"/api/jobs/{job_id}/previews/resource/"
                    f"{quote(role, safe='')}/{index}/regenerate"
                ),
            )
        entries.append(entry)
    return entries


def _safe_name(value: str) -> str:
    from pet_common import safe_filename

    return safe_filename(value[:80], "my-pet")


def _normalize_requested_actions(values: Optional[List[str]]) -> List[str]:
    try:
        return normalize_selected_actions(values)
    except ValueError as error:
        raise HTTPException(400, str(error))


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=SESSION_TTL_DAYS * 24 * 60 * 60,
        httponly=True,
        samesite="lax",
        secure=settings.auth_cookie_secure,
    )


def _request_context(
    session_id: Optional[str],
    admin_token: Optional[str],
    record: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Authenticate a browser request or the legacy unowned test job.

    Existing installations may contain jobs created before accounts existed.
    Those records have no owner and remain readable only through their exact
    ID for backwards compatibility; every newly created job always has an
    owner and requires a real account or the administrator token.
    """

    if admin_token:
        _verify_admin(admin_token)
        return {"id": "admin", "username": "管理员", "role": "admin"}
    if session_id and settings.admin_token:
        try:
            if hmac.compare_digest(session_id, settings.admin_token):
                return {"id": "admin", "username": "管理员", "role": "admin"}
        except (TypeError, ValueError):
            pass
    user = user_store.get_session_user(session_id)
    if user:
        if record is not None and record.get("owner_id") != user.get("id"):
            # Do not reveal whether another user's job ID exists.
            raise HTTPException(404, "任务不存在")
        return user
    if record is not None and not record.get("owner_id") and not session_id:
        return {"id": "legacy", "username": "历史任务", "role": "legacy"}
    raise HTTPException(401, "请先登录")


def _ai_snapshot_for_context(context: Dict[str, Any]) -> Dict[str, Any]:
    if context.get("role") == "admin":
        return settings.ai_snapshot()
    if context.get("role") == "legacy":
        return settings.ai_snapshot()
    return user_store.get_ai_config(str(context["id"]))


def _settings_for_job(record: Dict[str, Any]):
    snapshot = record.get("ai_config")
    return settings.with_ai_snapshot(snapshot if isinstance(snapshot, dict) else {})


def _checkpoint_message(value: Dict[str, object]) -> str:
    stage = str(value.get("stage", ""))
    if stage == "actions" and value.get("role"):
        labels = {"idle": "待机", "walk": "走动", "sleep": "睡觉", "react": "点击反应"}
        role = labels.get(str(value.get("role")), str(value.get("role")))
        return f"{role}动作已处理第 {int(value.get('completed_frame_count', 0))} 帧"
    if stage == "packaging":
        return "动作帧已完成，正在写入资源包"
    if stage == "preparation":
        completed = value.get("completed_photo_indices") or []
        total = value.get("total_photos") or "?"
        return f"去背景已完成 {len(completed)}/{total} 张图片"
    return "任务正在保存断点"


def _check_job_cancelled(job_id: str) -> None:
    if store.cancel_requested(job_id):
        raise BuildCancelled("任务已停止")


def _mark_job_cancelled(job_id: str, message: str) -> None:
    try:
        record = store.read(job_id)
    except (FileNotFoundError, ValueError):
        return
    if record.get("phase") == "resource_edit":
        store.update(
            job_id,
            status="cancelled",
            message="已停止资源编辑，当前断点已保存，可继续恢复",
            error="",
            cancel_requested=True,
            progress=int(record.get("progress", 0)),
            heartbeat_at=_utc_now(),
        )
        return
    store.update(
        job_id,
        status="cancelled",
        message=message,
        error="",
        cancel_requested=True,
        progress=int(record.get("progress", 0)),
        heartbeat_at=_utc_now(),
    )


def _utc_now() -> str:
    return datetime.utcnow().isoformat() + "Z"


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
