from __future__ import annotations

import os
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _animation_mode(value: Any, default: str = "hybrid") -> str:
    mode = str(value or default).strip().lower()
    return mode if mode in {"png", "skeleton", "hybrid"} else default


@dataclass
class Settings:
    data_dir: Path
    max_files: int
    max_upload_bytes: int
    ai_enabled: bool
    ai_api_key: str
    ai_api_base_url: str
    ai_image_model: str
    ai_timeout_seconds: int
    ai_frame_count: int
    ai_max_references: int
    build_mode: str
    worker_token: str
    admin_token: str
    cors_origins: str
    pose_consistency: bool = True
    animation_mode: str = "hybrid"
    animation_fps: int = 12
    walk_frame_count: int = 16
    sleep_frame_count: int = 12
    animation_defaults_version: int = 2

    @classmethod
    def from_env(cls) -> "Settings":
        base_url = os.getenv(
            "AI_API_BASE_URL",
            os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        ).rstrip("/")
        build_mode = os.getenv("BUILD_MODE", "worker").strip().lower()
        if build_mode not in {"worker", "archive"}:
            build_mode = "worker"
        data_dir = Path(os.getenv("DATA_DIR", "/data"))
        data_dir.mkdir(parents=True, exist_ok=True)
        result = cls(
            data_dir=data_dir,
            max_files=max(1, min(8, _env_int("MAX_FILES", 8))),
            max_upload_bytes=max(1_000_000, _env_int("MAX_UPLOAD_BYTES", 30_000_000)),
            ai_enabled=_env_bool("AI_ENABLED", True),
            ai_api_key=os.getenv("AI_API_KEY", os.getenv("OPENAI_API_KEY", "")).strip(),
            ai_api_base_url=base_url,
            ai_image_model=os.getenv("AI_IMAGE_MODEL", "gpt-image-1").strip(),
            ai_timeout_seconds=max(30, _env_int("AI_TIMEOUT_SECONDS", 180)),
            ai_frame_count=max(1, min(8, _env_int("AI_FRAME_COUNT", 4))),
            ai_max_references=max(1, min(4, _env_int("AI_MAX_REFERENCES", 2))),
            build_mode=build_mode,
            worker_token=os.getenv("WORKER_TOKEN", "").strip(),
            admin_token=os.getenv("ADMIN_TOKEN", "").strip(),
            cors_origins=os.getenv("CORS_ORIGINS", "").strip(),
            pose_consistency=_env_bool("POSE_CONSISTENCY", True),
            animation_mode=_animation_mode(os.getenv("ANIMATION_MODE", "hybrid")),
            animation_fps=max(1, min(60, _env_int("ANIMATION_FPS", 12))),
            walk_frame_count=max(1, min(24, _env_int("WALK_FRAME_COUNT", 16))),
            sleep_frame_count=max(1, min(24, _env_int("SLEEP_FRAME_COUNT", 12))),
        )
        result._load_runtime_overrides()
        return result

    @property
    def runtime_file(self) -> Path:
        return self.data_dir / "settings.json"

    def _load_runtime_overrides(self) -> None:
        if not self.runtime_file.exists():
            return
        try:
            values = json.loads(self.runtime_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(values, dict):
            return
        legacy_frame_defaults = (
            values.get("animation_defaults_version") != self.animation_defaults_version
            and values.get("walk_frame_count") == 12
            and values.get("sleep_frame_count") == 10
        )
        self._apply_runtime_values(values)
        # The previous release persisted 12/10 as its defaults. Migrate only
        # that exact unversioned pair so an explicit newer configuration is
        # never overwritten.
        if legacy_frame_defaults:
            self.walk_frame_count = 16
            self.sleep_frame_count = 12

    def _apply_runtime_values(self, values: Dict[str, Any]) -> None:
        if isinstance(values.get("ai_enabled"), bool):
            self.ai_enabled = values["ai_enabled"]
        if isinstance(values.get("ai_api_key"), str):
            self.ai_api_key = values["ai_api_key"].strip()
        if isinstance(values.get("ai_api_base_url"), str) and values["ai_api_base_url"].strip():
            self.ai_api_base_url = values["ai_api_base_url"].strip().rstrip("/")
        if isinstance(values.get("ai_image_model"), str) and values["ai_image_model"].strip():
            self.ai_image_model = values["ai_image_model"].strip()
        if isinstance(values.get("ai_timeout_seconds"), int):
            self.ai_timeout_seconds = max(30, min(900, values["ai_timeout_seconds"]))
        if isinstance(values.get("ai_frame_count"), int):
            self.ai_frame_count = max(1, min(8, values["ai_frame_count"]))
        if isinstance(values.get("ai_max_references"), int):
            self.ai_max_references = max(1, min(4, values["ai_max_references"]))
        if isinstance(values.get("pose_consistency"), bool):
            self.pose_consistency = values["pose_consistency"]
        if isinstance(values.get("animation_mode"), str):
            self.animation_mode = _animation_mode(values["animation_mode"])
        if isinstance(values.get("animation_fps"), int):
            self.animation_fps = max(1, min(60, values["animation_fps"]))
        if isinstance(values.get("walk_frame_count"), int):
            self.walk_frame_count = max(1, min(24, values["walk_frame_count"]))
        if isinstance(values.get("sleep_frame_count"), int):
            self.sleep_frame_count = max(1, min(24, values["sleep_frame_count"]))

    def _runtime_values(self) -> Dict[str, Any]:
        return {
            "ai_enabled": self.ai_enabled,
            "ai_api_key": self.ai_api_key,
            "ai_api_base_url": self.ai_api_base_url,
            "ai_image_model": self.ai_image_model,
            "ai_timeout_seconds": self.ai_timeout_seconds,
            "ai_frame_count": self.ai_frame_count,
            "ai_max_references": self.ai_max_references,
            "pose_consistency": self.pose_consistency,
            "animation_mode": self.animation_mode,
            "animation_fps": self.animation_fps,
            "walk_frame_count": self.walk_frame_count,
            "sleep_frame_count": self.sleep_frame_count,
            "animation_defaults_version": self.animation_defaults_version,
        }

    def _persist_runtime_values(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(self.data_dir),
            prefix="settings-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(self._runtime_values(), handle, ensure_ascii=False, indent=2)
            temporary = Path(handle.name)
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        temporary.replace(self.runtime_file)

    def update_ai(self, values: Dict[str, Any]) -> Dict[str, Any]:
        if "enabled" in values and values["enabled"] is not None:
            self.ai_enabled = bool(values["enabled"])

        base_url = values.get("api_base_url")
        if base_url is not None:
            base_url = str(base_url).strip().rstrip("/")
            if not base_url.startswith(("http://", "https://")):
                raise ValueError("AI API 地址必须以 http:// 或 https:// 开头")
            self.ai_api_base_url = base_url

        model = values.get("image_model")
        if model is not None:
            model = str(model).strip()
            if not model or len(model) > 120:
                raise ValueError("AI 模型名称无效")
            self.ai_image_model = model

        api_key = values.get("api_key")
        if values.get("clear_api_key"):
            self.ai_api_key = ""
        if api_key is not None and str(api_key).strip():
            self.ai_api_key = str(api_key).strip()

        if values.get("frame_count") is not None:
            self.ai_frame_count = max(1, min(8, int(values["frame_count"])))
        if values.get("max_references") is not None:
            self.ai_max_references = max(1, min(4, int(values["max_references"])))
        if values.get("timeout_seconds") is not None:
            self.ai_timeout_seconds = max(30, min(900, int(values["timeout_seconds"])))
        if values.get("pose_consistency") is not None:
            self.pose_consistency = bool(values["pose_consistency"])
        if values.get("animation_mode") is not None:
            self.animation_mode = _animation_mode(values["animation_mode"])
        if values.get("animation_fps") is not None:
            self.animation_fps = max(1, min(60, int(values["animation_fps"])))
        if values.get("walk_frame_count") is not None:
            self.walk_frame_count = max(1, min(24, int(values["walk_frame_count"])))
        if values.get("sleep_frame_count") is not None:
            self.sleep_frame_count = max(1, min(24, int(values["sleep_frame_count"])))

        self._persist_runtime_values()
        return self.ai_public()

    def ai_public(self) -> Dict[str, Any]:
        masked_key = ""
        if self.ai_api_key:
            masked_key = "****" if len(self.ai_api_key) <= 4 else "****" + self.ai_api_key[-4:]
        return {
            "enabled": self.ai_enabled,
            "configured": bool(self.ai_api_key),
            "api_key_mask": masked_key,
            "api_base_url": self.ai_api_base_url,
            "image_model": self.ai_image_model,
            "timeout_seconds": self.ai_timeout_seconds,
            "frame_count": self.ai_frame_count,
            "max_references": self.ai_max_references,
            "pose_consistency": self.pose_consistency,
            "animation_mode": self.animation_mode,
            "animation_fps": self.animation_fps,
            "walk_frame_count": self.walk_frame_count,
            "sleep_frame_count": self.sleep_frame_count,
        }

    def frame_count_for_role(self, role: str) -> int:
        """Return the configured count for a role without coupling callers to UI names."""

        if role == "walk":
            return self.walk_frame_count
        if role == "sleep":
            return self.sleep_frame_count
        return self.ai_frame_count


settings = Settings.from_env()
