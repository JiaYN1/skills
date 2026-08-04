from __future__ import annotations

import base64
import mimetypes
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from .config import Settings


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pet_animation import frame_count_for_role, pose_plan_for  # noqa: E402
from pet_assets import split_contact_sheet_bytes  # noqa: E402


ROLE_PROMPTS = {
    "idle": "standing or sitting calmly with a subtle breathing pose",
    "walk": "walking in a clear side-facing step pose, with the legs in a natural walking position",
    "sleep": "curled up asleep with closed eyes and a relaxed posture",
    "react": "a playful happy reaction pose as if the pet was just clicked",
}


class ImageGenerationError(RuntimeError):
    pass


class OpenAICompatibleImageProvider:
    """Image edit/generation adapter for OpenAI-compatible services.

    The provider accepts both base64 and URL image responses. The endpoint and
    model are configurable so a self-hosted compatible gateway can be used.
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def available(self) -> bool:
        return self.settings.ai_enabled and bool(self.settings.ai_api_key)

    async def generate_action_frames(
        self,
        references: List[Path],
        role: str,
        output_dir: Path,
        identity_reference: Optional[Path] = None,
        frame_count: Optional[int] = None,
        pose_consistency: Optional[bool] = None,
    ) -> List[Path]:
        if not self.available:
            return []

        output_dir.mkdir(parents=True, exist_ok=True)
        configured_count = frame_count
        if configured_count is None:
            role_count = getattr(self.settings, "frame_count_for_role", None)
            configured_count = role_count(role) if callable(role_count) else self.settings.ai_frame_count
        count = frame_count_for_role(role, configured_count)
        transparent_background = self._transparent_background_support(self.settings.ai_image_model)
        prompt = self._prompt_for(
            role,
            frame_count=count,
            pose_consistency=(
                getattr(self.settings, "pose_consistency", True)
                if pose_consistency is None
                else bool(pose_consistency)
            ),
            transparent_background=transparent_background,
        )
        reference_paths = self._reference_paths(references, identity_reference)
        reference_paths = reference_paths[: self.settings.ai_max_references]
        response = await self._request(prompt, reference_paths, frame_count=count)
        payload = response.json()
        if not isinstance(payload, dict):
            raise ImageGenerationError("图像服务返回了无法识别的数据格式")
        items = payload.get("data") or payload.get("images") or []
        if not isinstance(items, list):
            raise ImageGenerationError("图像服务返回了无法识别的数据格式")

        output_paths: List[Path] = []
        async with httpx.AsyncClient(timeout=self.settings.ai_timeout_seconds) as client:
            for index, item in enumerate(items):
                image_bytes = await self._decode_item(client, item)
                if not image_bytes:
                    continue
                frame_bytes = (
                    split_contact_sheet_bytes(image_bytes, count)
                    if len(items) == 1
                    else [image_bytes]
                )
                for frame in frame_bytes:
                    target = output_dir / f"{role}_{len(output_paths)}.png"
                    target.write_bytes(frame)
                    output_paths.append(target)
                    if len(output_paths) >= count:
                        break
                if len(output_paths) >= count:
                    break
        return output_paths

    @staticmethod
    def _reference_paths(references: List[Path], identity_reference: Optional[Path]) -> List[Path]:
        """Put the identity anchor first and remove duplicate reference files."""

        result: List[Path] = []
        for path in ([identity_reference] if identity_reference else []) + list(references):
            if path is not None and path not in result:
                result.append(path)
        return result

    @staticmethod
    def _transparent_background_support(model: str) -> Optional[bool]:
        """Return known transparent-background support for an image model.

        ``gpt-image-2`` currently rejects ``background=transparent``.  Keep
        unknown compatible model names undecided so the request-level fallback
        can still discover what the gateway accepts.
        """

        normalized = str(model or "").strip().lower()
        if normalized == "gpt-image-2" or normalized.startswith("gpt-image-2-"):
            return False
        if normalized in {"gpt-image-1", "gpt-image-1-mini", "gpt-image-1.5"}:
            return True
        if normalized.startswith("gpt-image-1-") or normalized.startswith("gpt-image-1.5-"):
            return True
        return None

    @classmethod
    def _request_attempts(cls, model: str, base_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Build model-aware request payloads from richest to minimal."""

        transparent_support = cls._transparent_background_support(model)
        if transparent_support is False:
            return [
                dict(base_data, output_format="png"),
                dict(base_data, n=1, output_format="png"),
                dict(base_data, n=1),
            ]
        return [
            dict(base_data, background="transparent", output_format="png"),
            dict(base_data, background="transparent"),
            dict(base_data, n=1),
        ]

    async def _request(
        self,
        prompt: str,
        references: List[Path],
        frame_count: Optional[int] = None,
    ) -> httpx.Response:
        endpoint = "/images/edits" if references else "/images/generations"
        url = f"{self.settings.ai_api_base_url}{endpoint}"
        headers = {"Authorization": f"Bearer {self.settings.ai_api_key}"}
        count = max(1, min(12, int(frame_count or self.settings.ai_frame_count)))
        base_data = {
            "model": self.settings.ai_image_model,
            "prompt": prompt,
            "n": count,
            "size": "1024x1024",
        }

        # Optional fields are accepted by some image APIs but not by every
        # model or OpenAI-compatible gateway. In particular, gpt-image-2
        # currently rejects background=transparent, so do not send it at all.
        attempts = self._request_attempts(self.settings.ai_image_model, base_data)
        last_error = "未知错误"
        async with httpx.AsyncClient(timeout=self.settings.ai_timeout_seconds) as client:
            for data in attempts:
                if references:
                    files = []
                    for reference in references:
                        mime = mimetypes.guess_type(reference.name)[0] or "image/png"
                        files.append(("image", (reference.name, reference.read_bytes(), mime)))
                    response = await client.post(url, headers=headers, data=data, files=files)
                else:
                    response = await client.post(url, headers=headers, json=data)
                if response.status_code < 400:
                    return response
                last_error = response.text[-2000:]
                if response.status_code not in {400, 404, 422}:
                    break
        raise ImageGenerationError(f"图像服务请求失败（{response.status_code}）：{last_error}")

    async def _decode_item(self, client: httpx.AsyncClient, item: Any) -> bytes:
        if not isinstance(item, dict):
            return b""
        encoded = item.get("b64_json") or item.get("b64")
        if encoded:
            return base64.b64decode(encoded)
        url = item.get("url")
        nested = item.get("image_url")
        if isinstance(nested, dict):
            url = url or nested.get("url")
        if url:
            response = await client.get(str(url))
            response.raise_for_status()
            return response.content
        return b""

    @staticmethod
    def _prompt_for(
        role: str,
        frame_count: int = 8,
        pose_consistency: bool = True,
        transparent_background: Optional[bool] = None,
    ) -> str:
        action = ROLE_PROMPTS.get(role, ROLE_PROMPTS["idle"])
        count = frame_count_for_role(role, frame_count)
        plan = pose_plan_for(role, count)
        sequence = "; ".join(
            f"frame {index + 1}: {pose}"
            for index, pose in enumerate(plan)
        )
        consistency = (
            "Lock identity and proportions to the first identity reference before changing only the pose. "
            "Keep the same face, markings, fur or feather pattern, ear shape, tail shape, camera angle, "
            "lighting direction, crop, scale, and ground contact across every frame."
            if pose_consistency
            else "Keep the animal recognizable and full-body in every frame."
        )
        if transparent_background is True:
            background = (
                "Use a fully transparent background or a perfectly flat removable green background "
                "if the service cannot return alpha."
            )
        elif transparent_background is False:
            background = (
                "Use a perfectly flat white or green background with no shadows; the server will remove "
                "this background after generation because this model does not support transparent output."
            )
        else:
            background = (
                "Use a fully transparent background when supported; otherwise use a perfectly flat "
                "white or green background that can be removed after generation."
            )
        return (
            "Use case: identity-preserve. Asset type: a frame-by-frame 2D desktop-pet animation. "
            "Input images: Image 1 is the identity reference; later images are pose references only. "
            "Create exactly the same pet shown in the reference images. "
            f"{consistency} "
            "Show the pet full body, centered, in a clean game-sprite style. "
            "Keep the subject at the same pixel scale in every frame: the body occupies the same "
            "approximate area, the horizontal center stays fixed, and the feet stay on the same "
            "baseline near the lower edge. Do not zoom, pan, change the camera, or change lighting. "
            f"The action is {action}. Generate exactly {count} separate PNG frames, in order, not a contact sheet. "
            f"The ordered pose plan is: {sequence}. "
            f"{background} Keep clean anti-aliased edges, no text, no frame, no room, no people, "
            "no watermark, no shadow, and no extra animals."
        )
