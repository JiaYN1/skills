from __future__ import annotations

import json
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Callable, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pet_assets import normalize_pet_image
from pet_common import ROLES, assign_roles, safe_filename

from .ai_provider import OpenAICompatibleImageProvider
from .config import Settings


ProgressCallback = Callable[[int, str], None]


async def build_pet_package(
    job_dir: Path,
    name: str,
    input_paths: List[Path],
    provider: OpenAICompatibleImageProvider,
    settings: Settings,
    progress: ProgressCallback,
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
        target = references_dir / f"reference_{index}.png"
        normalize_pet_image(source, target, canvas_size=512, use_rembg=settings.remove_background)
        normalized_inputs.append(target)

    role_inputs = assign_roles(normalized_inputs)
    config_assets: Dict[str, List[str]] = {}
    ai_frame_total = 0
    ai_error_count = 0

    for role_index, role in enumerate(ROLES):
        progress(10 + role_index * 18, f"正在生成{_role_label(role)}动作")
        generated: List[Path] = []
        try:
            generated = await provider.generate_action_frames(
                role_inputs[role],
                role,
                ai_dir / role,
            )
        except Exception as error:
            # Keep the job usable if a provider is temporarily unavailable.
            ai_error_count += 1
            progress(10 + role_index * 18, f"AI 生成失败，{_role_label(role)}使用照片动画：{error}")

        sources = generated or [role_inputs[role][0]]
        ai_frame_total += len(generated)
        config_assets[role] = []
        for frame_index, source in enumerate(sources[: settings.ai_frame_count]):
            filename = f"{role}_{frame_index}.png"
            destination = assets_dir / filename
            normalize_pet_image(source, destination, canvas_size=320, use_rembg=settings.remove_background)
            config_assets[role].append(f"assets/{filename}")

    config = {
        "name": name,
        "scale": 1.0,
        "speed": 2.2,
        "always_on_top": True,
        "sleep_after_seconds": 60,
        "assets": config_assets,
        "generation": {
            "provider": "openai-compatible" if ai_frame_total else "photo-fallback",
            "ai_frame_count": ai_frame_total,
            "ai_error_count": ai_error_count,
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
        "如果由 Windows Worker 打包，运行生成的 exe 即可。\n",
        encoding="utf-8",
    )

    zip_path = job_dir / f"{safe_filename(name, 'my-pet')}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in package_dir.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(package_dir).as_posix())

    progress(88, "资源包已生成")
    return {
        "package_dir": package_dir,
        "zip_path": zip_path,
        "ai_frame_total": ai_frame_total,
        "ai_error_count": ai_error_count,
    }


def _role_label(role: str) -> str:
    return {
        "idle": "待机",
        "walk": "走动",
        "sleep": "睡觉",
        "react": "点击反应",
    }.get(role, role)
