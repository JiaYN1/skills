from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any, Dict, List

import httpx

from .config import Settings


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
    ) -> List[Path]:
        if not self.available:
            return []

        output_dir.mkdir(parents=True, exist_ok=True)
        prompt = self._prompt_for(role)
        reference_paths = references[: self.settings.ai_max_references]
        response = await self._request(prompt, reference_paths)
        payload = response.json()
        items = payload.get("data") or payload.get("images") or []
        if not isinstance(items, list):
            raise ImageGenerationError("图像服务返回了无法识别的数据格式")

        output_paths: List[Path] = []
        async with httpx.AsyncClient(timeout=self.settings.ai_timeout_seconds) as client:
            for index, item in enumerate(items):
                image_bytes = await self._decode_item(client, item)
                if not image_bytes:
                    continue
                target = output_dir / f"{role}_{index}.png"
                target.write_bytes(image_bytes)
                output_paths.append(target)
        return output_paths

    async def _request(self, prompt: str, references: List[Path]) -> httpx.Response:
        endpoint = "/images/edits" if references else "/images/generations"
        url = f"{self.settings.ai_api_base_url}{endpoint}"
        headers = {"Authorization": f"Bearer {self.settings.ai_api_key}"}
        base_data = {
            "model": self.settings.ai_image_model,
            "prompt": prompt,
            "n": str(self.settings.ai_frame_count),
            "size": "1024x1024",
        }

        # Optional fields are accepted by current image APIs but not by every
        # OpenAI-compatible gateway. Retry with the minimal shape if rejected.
        attempts = [
            dict(base_data, background="transparent", output_format="png"),
            dict(base_data, n="1"),
        ]
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
    def _prompt_for(role: str) -> str:
        action = ROLE_PROMPTS.get(role, ROLE_PROMPTS["idle"])
        return (
            "Create a consistent 2D desktop-pet sprite of the exact same pet shown in the reference image. "
            "Preserve the species, fur or feather colors, markings, face, ears, tail, body proportions, "
            "and overall identity. Show the pet full body, centered, in a clean game-sprite style, "
            f"with the action: {action}. "
            "Use a fully transparent background, clean anti-aliased edges, no text, no frame, no room, "
            "no people, no watermark, and no extra animals."
        )

