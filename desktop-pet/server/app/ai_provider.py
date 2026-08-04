from __future__ import annotations

import base64
import mimetypes
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from .config import Settings


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pet_animation import frame_count_for_role, pose_plan_for  # noqa: E402


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
        keep_consistency = (
            getattr(self.settings, "pose_consistency", True)
            if pose_consistency is None
            else bool(pose_consistency)
        )
        reference_paths = self._reference_paths(references, identity_reference)
        reference_paths = reference_paths[: self.settings.ai_max_references]

        output_paths: List[Path] = []
        errors: List[str] = []
        continuity_reference: Optional[Path] = None
        # Match the stable cutout workflow: one pose, one edit request, one
        # returned image. Avoid n=count because compatible gateways often
        # reject it or turn the response into a contact sheet.
        for index in range(count):
            target = output_dir / f"{role}_{index}.png"
            request_references = [continuity_reference] if continuity_reference else reference_paths
            try:
                await self.generate_action_frame(
                    request_references,
                    role,
                    target,
                    identity_reference=identity_reference,
                    frame_index=index,
                    frame_count=count,
                    pose_consistency=keep_consistency,
                    continuity_reference=continuity_reference is not None,
                )
                continuity_reference = target
            except Exception as error:
                errors.append(f"{role}_{index}: {error}")
                # Keep the cycle length stable when one provider request fails.
                # Repeating the previous frame is less disruptive than dropping
                # a pose and shifting all later frames left.
                fallback = continuity_reference or (reference_paths[-1] if reference_paths else None)
                if fallback and fallback.exists():
                    shutil.copy2(fallback, target)
                    continuity_reference = target
                else:
                    continue
            output_paths.append(target)

        if not output_paths and errors:
            raise ImageGenerationError("动作帧生成失败：" + "；".join(errors[-3:]))
        return output_paths

    async def generate_action_frame(
        self,
        references: List[Path],
        role: str,
        output_path: Path,
        identity_reference: Optional[Path] = None,
        frame_index: int = 0,
        frame_count: Optional[int] = None,
        pose_consistency: Optional[bool] = None,
        continuity_reference: bool = False,
    ) -> Path:
        """Generate exactly one transparent action frame."""

        if not self.available:
            raise ImageGenerationError("AI 图像服务未配置，无法生成动作帧")

        count = frame_count_for_role(role, frame_count)
        index = max(0, min(count - 1, int(frame_index)))
        pose = pose_plan_for(role, count)[index]
        keep_consistency = (
            getattr(self.settings, "pose_consistency", True)
            if pose_consistency is None
            else bool(pose_consistency)
        )
        transparent_background = self._transparent_background_support(
            self.settings.ai_image_model
        )
        prompt = self._prompt_for(
            role,
            frame_count=count,
            pose_consistency=keep_consistency,
            transparent_background=transparent_background,
            frame_index=index,
            pose=pose,
            continuity_reference=continuity_reference,
        )
        reference_paths = self._reference_paths(references, identity_reference)
        reference_paths = reference_paths[: self.settings.ai_max_references]
        return await self._generate_single_image(prompt, reference_paths, output_path)

    async def remove_background(self, source: Path, output_path: Path) -> Path:
        """Ask the image model for one transparent cutout preview.

        This is deliberately a separate, single-image step from animation
        generation. The user can inspect the identity reference before the
        more expensive pose-generation stage starts.
        """

        if not self.available:
            raise ImageGenerationError("AI 图像服务未配置，无法执行去背景预处理")

        transparent_background = self._transparent_background_support(
            self.settings.ai_image_model
        )
        prompt = self._background_removal_prompt(transparent_background)
        return await self._generate_single_image(prompt, [source], output_path)

    async def _generate_single_image(
        self,
        prompt: str,
        references: List[Path],
        output_path: Path,
    ) -> Path:
        """Run one image edit and persist its first returned image."""

        response = await self._request(prompt, references, frame_count=1)
        payload = response.json()
        if not isinstance(payload, dict):
            raise ImageGenerationError("图像服务返回了无法识别的数据格式")
        items = payload.get("data") or payload.get("images") or []
        if not isinstance(items, list):
            raise ImageGenerationError("图像服务返回了无法识别的数据格式")

        async with httpx.AsyncClient(timeout=self.settings.ai_timeout_seconds) as client:
            for item in items:
                image_bytes = await self._decode_item(client, item)
                if image_bytes:
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    output_path.write_bytes(image_bytes)
                    return output_path
        raise ImageGenerationError("图像服务没有返回图片")

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
            candidates = [
                dict(base_data, output_format="png"),
                dict(base_data, n=1, output_format="png"),
                dict(base_data, n=1),
            ]
        else:
            candidates = [
                dict(base_data, background="transparent", output_format="png"),
                dict(base_data, background="transparent"),
                dict(base_data, n=1),
            ]

        unique: List[Dict[str, Any]] = []
        for candidate in candidates:
            if candidate not in unique:
                unique.append(candidate)
        return unique

    async def _request(
        self,
        prompt: str,
        references: List[Path],
        frame_count: Optional[int] = None,
    ) -> httpx.Response:
        endpoint = "/images/edits" if references else "/images/generations"
        url = f"{self.settings.ai_api_base_url}{endpoint}"
        headers = {"Authorization": f"Bearer {self.settings.ai_api_key}"}
        count = max(1, min(24, int(frame_count or self.settings.ai_frame_count)))
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
        frame_index: Optional[int] = None,
        pose: Optional[str] = None,
        continuity_reference: bool = False,
    ) -> str:
        action = ROLE_PROMPTS.get(role, ROLE_PROMPTS["idle"])
        count = frame_count_for_role(role, frame_count)
        plan = pose_plan_for(role, count)
        if frame_index is None:
            sequence = "; ".join(
                f"frame {index + 1}: {frame_pose}"
                for index, frame_pose in enumerate(plan)
            )
            frame_request = (
                f"Generate exactly {count} separate PNG frames, in order, not a contact sheet. "
                f"The ordered pose plan is: {sequence}."
            )
            consistency = (
                "Lock identity and proportions to the first identity reference before changing only the pose. "
                "Keep the same face, markings, fur or feather pattern, ear shape, tail shape, camera angle, "
                "lighting direction, crop, scale, and ground contact across every frame."
                if pose_consistency
                else "Keep the animal recognizable and full-body in every frame."
            )
        else:
            selected_pose = pose or plan[frame_index % len(plan)]
            frame_request = (
                f"Generate exactly one separate PNG frame for animation frame {frame_index + 1} of {count}; "
                "do not return a contact sheet or multiple variations. "
                f"The target pose for this frame is: {selected_pose}."
            )
            consistency = (
                "Lock identity and proportions to the first identity reference and change only the pose. "
                "Keep the same face, markings, fur or feather pattern, ear shape, tail shape, camera angle, "
                "lighting direction, crop, scale, and ground contact so this frame matches the other frames."
                if pose_consistency
                else "Keep the animal recognizable, full-body, and consistent with the reference images."
            )
        continuity = (
            " The last input image is the immediately preceding animation frame. Treat it as the temporal "
            "anchor: make only the smallest pose change needed for this frame, preserve the same silhouette "
            "and landmarks, and never redesign the pet."
            if continuity_reference
            else ""
        )
        background = (
            "Output settings: transparent background enabled. Prefer the image model's native "
            "transparent-background mode and return a PNG with a fully transparent background, "
            "a real RGBA color model, and a clean alpha channel around the pet. Isolate only the "
            "pet; do not place it in a scene and do not draw a room, floor, white/green backdrop, "
            "checkerboard pattern, chroma-key green, or cast shadow. If the input or model draws a fake "
            "checkerboard pattern, treat it as a real background to remove, never as transparency. If this "
            "endpoint cannot encode alpha, use one "
            "perfectly uniform white background (#FFFFFF) with no texture or shadow as a compatibility "
            "fallback; never invent a detailed background."
        )
        if transparent_background is False:
            background += (
                " The API request may omit the background parameter for model compatibility, but "
                "the transparent-background output setting still applies to the prompt."
            )
        return (
            "Use case: identity-preserve. Asset type: a frame-by-frame 2D desktop-pet animation. "
            "Input images: Image 1 is the identity reference; later images are pose references only. "
            "Create exactly the same pet shown in the reference images. "
            f"{consistency}{continuity} "
            "Show the pet full body, centered, in a clean game-sprite style. "
            "Keep the subject at the same pixel scale in every frame: the body occupies the same "
            "approximate area, the horizontal center stays fixed, and the feet stay on the same "
            "baseline near the lower edge. Do not zoom, pan, change the camera, or change lighting. "
            f"The action is {action}. {frame_request} "
            f"{background} Keep clean anti-aliased edges, no text, no frame, no room, no people, "
            "no watermark, no shadow, and no extra animals."
        )

    @staticmethod
    def _background_removal_prompt(
        transparent_background: Optional[bool] = None,
    ) -> str:
        compatibility = ""
        if transparent_background is False:
            compatibility = (
                " The API request may omit the background parameter for model compatibility, "
                "but the transparent-background output setting still applies."
            )
        return (
            "Image editing task: remove the entire background from this exact image before it is "
            "used as a desktop-pet asset. Preserve the pet's identity, species, fur or feather "
            "colors, markings, face, body shape, pose, proportions, crop, camera angle, and "
            "lighting. Do not redraw, stylize, rotate, retouch, or add details. "
            "Output settings: transparent background enabled. Return exactly one PNG with a real "
            "RGBA color model and a clean alpha channel containing only the pet. Remove the room, "
            "floor, furniture, people, leash, text, watermark, and all shadows. Do not use a white, "
            "green, or checkerboard background. If the input contains a fake checkerboard or chroma-key "
            "matte, remove those pixels instead of preserving them. If native alpha encoding is unavailable, use one "
            "perfectly uniform white background (#FFFFFF) with no texture or shadow as a compatibility "
            "fallback; never invent a scene."
            + compatibility
        )
