from __future__ import annotations

import hmac
import shutil
import sys
import zipfile
from urllib.parse import quote
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import BackgroundTasks, Body, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .ai_provider import ImageGenerationError, OpenAICompatibleImageProvider
from .config import settings
from .package_builder import ROLE_ANCHORS, build_pet_package
from .storage import JobStore


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pet_assets import normalize_pet_image, normalize_pet_sequence  # noqa: E402
from pet_common import ROLES, assign_roles  # noqa: E402

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


class GenerateFromPreviewRequest(BaseModel):
    name: Optional[str] = None
    build_exe: bool = False

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


@app.post("/api/pets/prepare")
async def prepare_pet(
    background_tasks: BackgroundTasks,
    photos: List[UploadFile] = File(...),
    name: str = Form("我的宠物"),
) -> Dict[str, Any]:
    """Create a job whose first stage only removes photo backgrounds."""

    if not photos or len(photos) > settings.max_files:
        raise HTTPException(400, f"请上传 1-{settings.max_files} 张图片")

    safe_name = _safe_name(name)
    record = store.new_job(safe_name, len(photos), False)
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
) -> Dict[str, Any]:
    if not photos or len(photos) > settings.max_files:
        raise HTTPException(400, f"请上传 1-{settings.max_files} 张图片")

    safe_name = _safe_name(name)
    record = store.new_job(safe_name, len(photos), build_exe)
    job_id = record["id"]
    try:
        saved_paths = await _save_uploaded_photos(job_id, photos)
    except Exception:
        shutil.rmtree(store.job_dir(job_id), ignore_errors=True)
        raise

    background_tasks.add_task(_run_generation, job_id, safe_name, saved_paths, build_exe)
    return _public_job(store.read(job_id))


@app.post("/api/jobs/{job_id}/generate")
def generate_from_preview(
    job_id: str,
    payload: GenerateFromPreviewRequest,
    background_tasks: BackgroundTasks,
) -> Dict[str, Any]:
    """Start action generation after the user confirms cutout previews."""

    try:
        record = store.read(job_id)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "任务不存在")

    if record.get("status") != "preview_ready":
        raise HTTPException(409, "请先完成去背景预览")

    preview_paths = [
        store.job_dir(job_id) / relative
        for relative in record.get("preview_paths", [])
    ]
    if not preview_paths or not all(path.exists() for path in preview_paths):
        raise HTTPException(409, "去背景预览文件不存在，请重新上传")

    name = _safe_name(payload.name or record.get("name", "我的宠物"))
    build_exe = bool(payload.build_exe)
    store.update(
        job_id,
        name=name,
        build_exe=build_exe,
        status="processing",
        progress=5,
        message="已确认去背景预览，正在生成动作资源",
        error="",
    )
    background_tasks.add_task(_run_generation, job_id, name, preview_paths, build_exe)
    return _public_job(store.read(job_id))


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> Dict[str, Any]:
    try:
        return _public_job(store.read(job_id))
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "任务不存在")


@app.get("/api/jobs/{job_id}/preview/{asset_path:path}")
def preview_job_asset(job_id: str, asset_path: str):
    try:
        record = store.read(job_id)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "任务不存在")

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
) -> Dict[str, Any]:
    try:
        record = store.read(job_id)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "任务不存在")

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
        message=f"正在重新生成第 {index + 1} 张去背景预览",
    )
    background_tasks.add_task(_run_cutout_regeneration, job_id, index, source)
    return _public_job(store.read(job_id))


@app.post("/api/jobs/{job_id}/previews/resource/{role}/{index}/regenerate")
def regenerate_resource_preview(
    job_id: str,
    role: str,
    index: int,
    background_tasks: BackgroundTasks,
) -> Dict[str, Any]:
    try:
        record = store.read(job_id)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "任务不存在")

    if record.get("status") != "ready":
        raise HTTPException(409, "请在动作资源生成完成后重新生成单帧")
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

    store.update(
        job_id,
        status="resource_regenerating",
        progress=98,
        message=f"正在重新生成 {role}_{index}.png",
    )
    background_tasks.add_task(_run_resource_regeneration, job_id, meta)
    return _public_job(store.read(job_id))


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


async def _run_preparation(job_id: str, paths: List[Path]) -> None:
    """Generate and persist transparent identity previews before animation."""

    prepared_dir = store.job_dir(job_id) / "prepared"
    prepared_dir.mkdir(parents=True, exist_ok=True)
    preview_paths: List[str] = []
    try:
        if not provider.available:
            raise ImageGenerationError("AI 图像服务未配置，无法执行去背景预处理")

        for index, source in enumerate(paths):
            progress = 5 + int(index * 80 / max(1, len(paths)))
            store.update(
                job_id,
                progress=progress,
                message=f"正在为第 {index + 1}/{len(paths)} 张图片去背景",
            )
            raw_path = prepared_dir / f"raw_{index}.png"
            preview_path = prepared_dir / f"reference_{index}.png"
            await provider.remove_background(source, raw_path)
            _normalize_ai_preview(raw_path, preview_path)
            preview_paths.append(f"prepared/{preview_path.name}")

        store.update(
            job_id,
            status="preview_ready",
            progress=100,
            message="去背景预览已完成，请确认图片后生成动作资源",
            preview_paths=preview_paths,
            error="",
        )
    except Exception as error:
        store.update(
            job_id,
            status="failed",
            progress=0,
            message=str(error)[-4000:],
            error=str(error)[-4000:],
        )


async def _run_cutout_regeneration(job_id: str, index: int, source: Path) -> None:
    try:
        prepared_dir = store.job_dir(job_id) / "prepared"
        raw_path = prepared_dir / f"raw_{index}.png"
        preview_path = prepared_dir / f"reference_{index}.png"
        await provider.remove_background(source, raw_path)
        _normalize_ai_preview(raw_path, preview_path)
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
    except Exception as error:
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
        await provider.generate_action_frame(
            role_inputs[role],
            role,
            raw_path,
            identity_reference=identity_reference,
            frame_index=index,
            frame_count=frame_count,
            pose_consistency=getattr(settings, "pose_consistency", True),
        )

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
                    item["source_path"] = _relative_job_path(job_dir, raw_path)
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
            error="",
        )
    except Exception as error:
        store.update(
            job_id,
            status="ready",
            progress=100,
            message=f"重新生成动作帧失败，已保留原资源：{str(error)[-2000:]}",
            error=str(error)[-4000:],
        )


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
            resource_preview_paths=result.get("resource_preview_paths", []),
            resource_frame_meta=result.get("resource_frame_meta", []),
        )
    except Exception as error:
        store.update(job_id, status="failed", progress=0, message=str(error)[-4000:], error=str(error)[-4000:])


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


def _public_job(record: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(record)
    preview_paths = list(result.pop("preview_paths", []))
    resource_preview_paths = list(result.pop("resource_preview_paths", []))
    resource_frame_meta = [
        item
        for item in result.pop("resource_frame_meta", [])
        if isinstance(item, dict)
    ]
    result.pop("artifact_path", None)
    result.pop("package_path", None)
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
