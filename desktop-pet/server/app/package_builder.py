from __future__ import annotations

import json
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pet_assets import has_usable_transparency, normalize_pet_image, normalize_pet_sequence
from pet_animation import write_animation_bundle
from pet_common import ROLES, assign_roles, normalize_selected_actions, safe_filename

from .ai_provider import GenerationCancelled, OpenAICompatibleImageProvider
from .config import Settings


ProgressCallback = Callable[[int, str], None]
CheckpointCallback = Callable[[Dict[str, object]], None]
CancelCheck = Callable[[], bool]


class BuildCancelled(GenerationCancelled):
    """Raised when a job's owner requests a cooperative stop."""


ROLE_ANCHORS = {
    "idle": "bottom",
    "walk": "bottom",
    "sleep": "center",
    "react": "bottom",
}


async def ai_cutout_frame(provider: OpenAICompatibleImageProvider, source: Path, output_path: Path) -> Path:
    """Run the AI cutout pass and retry once when alpha is still missing."""

    await provider.remove_background(source, output_path)
    if has_usable_transparency(output_path):
        return output_path

    retry_path = output_path.with_name(f"{output_path.stem}_retry{output_path.suffix}")
    await provider.remove_background(output_path, retry_path)
    if has_usable_transparency(retry_path):
        return retry_path
    return output_path


async def build_pet_package(
    job_dir: Path,
    name: str,
    input_paths: List[Path],
    provider: OpenAICompatibleImageProvider,
    settings: Settings,
    progress: ProgressCallback,
    selected_actions: Optional[Sequence[str]] = None,
    should_cancel: Optional[CancelCheck] = None,
    checkpoint: Optional[CheckpointCallback] = None,
    resume_checkpoint: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    package_dir = job_dir / "package"
    assets_dir = package_dir / "assets"
    references_dir = job_dir / "references"
    ai_dir = job_dir / "ai"
    assets_dir.mkdir(parents=True, exist_ok=True)
    references_dir.mkdir(parents=True, exist_ok=True)
    ai_dir.mkdir(parents=True, exist_ok=True)

    normalized_inputs: List[Path] = []
    for index, source in enumerate(input_paths):
        _check_cancelled(should_cancel)
        target = references_dir / f"reference_{index}.png"
        normalize_pet_image(
            source,
            target,
            canvas_size=512,
            background_mode="none",
            anchor="center",
            subject_scale=0.96,
        )
        normalized_inputs.append(target)

    role_inputs = assign_roles(normalized_inputs)
    config_assets: Dict[str, List[str]] = {}
    resource_frame_meta: List[Dict[str, object]] = []
    ai_frame_total = 0
    ai_error_count = 0
    selected_action_list = normalize_selected_actions(selected_actions)
    selected_action_set = set(selected_action_list)
    stored_roles = (resume_checkpoint or {}).get("completed_roles", [])
    completed_roles_state = list(stored_roles) if isinstance(stored_roles, list) else []

    for role_index, role in enumerate(ROLES):
        _check_cancelled(should_cancel)
        progress(10 + role_index * 18, f"正在生成{_role_label(role)}动作")
        generated: List[Path] = []
        role_frame_count = _frame_count_for(settings, role)
        role_resume = _role_resume_checkpoint(resume_checkpoint, role)
        start_index = int(role_resume.get("completed_frame_count", 0))
        if start_index < 0 or start_index >= role_frame_count:
            start_index = 0
        _write_checkpoint(
            checkpoint,
            {
                "stage": "actions",
                "role": role,
                "role_index": role_index,
                "completed_frame_count": start_index,
                "completed_roles": list(completed_roles_state),
            },
        )
        if role in selected_action_set:
            try:
                generated = await provider.generate_action_frames(
                    role_inputs[role],
                    role,
                    ai_dir / role,
                    identity_reference=normalized_inputs[0],
                    frame_count=role_frame_count,
                    pose_consistency=getattr(settings, "pose_consistency", True),
                    start_index=start_index,
                    should_cancel=should_cancel,
                    on_frame=lambda frame_index, current_role=role: _write_checkpoint(
                        checkpoint,
                        {
                            "stage": "actions",
                            "role": current_role,
                            "role_index": role_index,
                            "completed_frame_count": int(frame_index) + 1,
                            "completed_roles": list(completed_roles_state),
                        },
                    ),
                )
            except Exception as error:
                if isinstance(error, GenerationCancelled):
                    raise BuildCancelled(str(error))
                # Keep the job usable if a provider is temporarily unavailable.
                ai_error_count += 1
                progress(10 + role_index * 18, f"AI 生成失败，{_role_label(role)}使用照片动画：{error}")
        else:
            progress(10 + role_index * 18, f"未选择生成{_role_label(role)}，使用照片动画兜底")

        if generated:
            # Run every AI action frame through the same AI cutout workflow as
            # the upload preview. This is intentionally a second pass for
            # models/gateways that return an RGB checkerboard or chroma-key
            # matte even when the prompt requests alpha.
            cleaned_sources: List[Path] = []
            for frame_index, source in enumerate(generated):
                _check_cancelled(should_cancel)
                cleaned_path = ai_dir / role / f"{role}_{frame_index}_cutout.png"
                if (
                    frame_index < start_index
                    and cleaned_path.exists()
                    and has_usable_transparency(cleaned_path)
                ):
                    cleaned_sources.append(cleaned_path)
                else:
                    try:
                        cleaned_sources.append(await ai_cutout_frame(provider, source, cleaned_path))
                    except GenerationCancelled:
                        raise BuildCancelled("任务已停止")
                    except Exception as error:
                        ai_error_count += 1
                        progress(
                            10 + role_index * 18,
                            f"{_role_label(role)}第 {frame_index + 1} 帧去背景失败，保留原帧：{error}",
                        )
                        cleaned_sources.append(source)
                _write_checkpoint(
                    checkpoint,
                    {
                        "stage": "actions",
                        "role": role,
                        "role_index": role_index,
                        "completed_frame_count": frame_index + 1,
                        "completed_roles": list(completed_roles_state),
                    },
                )
            sources = cleaned_sources
        else:
            sources = role_inputs[role] or [normalized_inputs[0]]
        ai_frame_total += len(generated)
        selected_sources = sources[: role_frame_count]
        destinations = [assets_dir / f"{role}_{index}.png" for index in range(len(selected_sources))]
        normalize_pet_sequence(
            selected_sources,
            destinations,
            canvas_size=320,
            # Prefer the alpha channel requested in the AI prompt. Some
            # compatible gateways still return an opaque PNG; the simple
            # Pillow fallback removes only a connected flat border so one
            # imperfect frame does not fail the whole job. It does not use
            # rembg or another local segmentation model.
            background_mode="simple",
            anchor=ROLE_ANCHORS.get(role, "center"),
            subject_scale=0.96,
        )
        if role not in completed_roles_state:
            completed_roles_state.append(role)
        _write_checkpoint(
            checkpoint,
            {
                "stage": "actions",
                "role": role,
                "role_index": role_index,
                "completed_frame_count": len(selected_sources),
                "completed_roles": list(completed_roles_state),
            },
        )
        config_assets[role] = [
            f"assets/{destination.name}" for destination in destinations
        ]
        for index, (source, destination) in enumerate(zip(selected_sources, destinations)):
            resource_frame_meta.append(
                {
                    "asset_path": f"package/{config_assets[role][index]}",
                    "source_path": _relative_job_path(job_dir, source),
                    "raw_source_path": (
                        _relative_job_path(job_dir, generated[index])
                        if generated and index < len(generated)
                        else _relative_job_path(job_dir, source)
                    ),
                    "role": role,
                    "index": index,
                    "frame_count": role_frame_count,
                }
            )

    animation_mode = getattr(settings, "animation_mode", "hybrid")
    animation_fps = getattr(settings, "animation_fps", 10)
    frame_repeat = getattr(settings, "frame_repeat", 1)
    _check_cancelled(should_cancel)
    _write_checkpoint(
        checkpoint,
        {
            "stage": "packaging",
            "completed_roles": list(ROLES),
            "completed_frame_count": 0,
        },
    )
    animation_manifest = write_animation_bundle(
        package_dir,
        config_assets,
        mode=animation_mode,
        fps=animation_fps,
        frame_repeat=frame_repeat,
    )

    config = {
        "name": name,
        "scale": 1.0,
        "speed": 2.2,
        "always_on_top": True,
        "sleep_after_seconds": 60,
        "assets": config_assets,
        "animation": animation_manifest,
        "generation": {
            "provider": "openai-compatible" if ai_frame_total else "photo-fallback",
            "ai_frame_count": ai_frame_total,
            "ai_error_count": ai_error_count,
            "selected_actions": selected_action_list,
            "fallback_actions": [role for role in ROLES if role not in selected_action_set],
            "frame_counts": {role: len(config_assets.get(role, [])) for role in ROLES},
            "background_removal": "ai-prompt+simple-fallback",
            "pose_consistency": bool(getattr(settings, "pose_consistency", True)),
            "frame_repeat": animation_manifest.get("frame_repeat", 1),
        },
    }
    (package_dir / "pet_config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    shutil.copy2(PROJECT_ROOT / "pet_runtime.py", package_dir / "pet_runtime.py")
    (package_dir / "README.txt").write_text(
        "AI 桌面宠物资源包\n"
        "预览：python pet_runtime.py --config pet_config.json\n"
        "如果由 Windows Worker 打包，运行生成的 exe 即可。\n"
        "animation.json 保存 PNG 序列；hybrid/skeleton 模式还包含 skeleton.json。\n",
        encoding="utf-8",
    )

    zip_path = job_dir / f"{safe_filename(name, 'my-pet')}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in package_dir.rglob("*"):
            _check_cancelled(should_cancel)
            if path.is_file():
                archive.write(path, path.relative_to(package_dir).as_posix())

    progress(88, "资源包已生成")
    resource_preview_paths = [item["asset_path"] for item in resource_frame_meta]
    return {
        "package_dir": package_dir,
        "zip_path": zip_path,
        "ai_frame_total": ai_frame_total,
        "ai_error_count": ai_error_count,
        "animation_mode": animation_manifest["mode"],
        "frame_repeat": animation_manifest.get("frame_repeat", 1),
        "animation_fps": animation_manifest.get("fps", animation_fps),
        "resource_preview_paths": resource_preview_paths,
        "resource_frame_meta": resource_frame_meta,
        "selected_actions": selected_action_list,
    }


def _check_cancelled(should_cancel: Optional[CancelCheck]) -> None:
    if should_cancel is not None and should_cancel():
        raise BuildCancelled("任务已停止")


def _write_checkpoint(
    callback: Optional[CheckpointCallback],
    value: Dict[str, object],
) -> None:
    if callback is not None:
        callback(value)


def _role_resume_checkpoint(
    checkpoint: Optional[Dict[str, object]],
    role: str,
) -> Dict[str, object]:
    if not isinstance(checkpoint, dict) or checkpoint.get("role") != role:
        return {}
    return checkpoint


def _role_label(role: str) -> str:
    return {
        "idle": "待机",
        "walk": "走动",
        "sleep": "睡觉",
        "react": "点击反应",
    }.get(role, role)


def _frame_count_for(settings: Settings, role: str) -> int:
    configured = getattr(settings, "frame_count_for_role", None)
    if callable(configured):
        return max(1, min(24, int(configured(role))))
    return max(1, min(24, int(getattr(settings, "ai_frame_count", 8))))


def _relative_job_path(job_dir: Path, source: Path) -> str:
    try:
        return source.resolve().relative_to(job_dir.resolve()).as_posix()
    except ValueError:
        return source.name
