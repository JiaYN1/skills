"""Shared constants and small helpers for the desktop pet MVP."""

import re
from pathlib import Path
from typing import Dict, Iterable, List


ROLES = ("idle", "walk", "sleep", "react")
ROLE_LABELS = {
    "idle": "待机",
    "walk": "走动",
    "sleep": "睡觉",
    "react": "点击反应",
}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}


def safe_filename(value: str, fallback: str = "my-pet") -> str:
    """Return a filename-safe value while keeping non-ASCII characters."""

    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value.strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned or fallback


def unique_directory(parent: Path, name: str) -> Path:
    """Choose a new directory without deleting an existing user package."""

    candidate = parent / safe_filename(name)
    index = 2
    while candidate.exists():
        candidate = parent / f"{safe_filename(name)}_{index}"
        index += 1
    return candidate


def assign_roles(photo_paths: Iterable[Path]) -> Dict[str, List[Path]]:
    """Map uploaded photos to the four MVP states.

    The first four photos are mapped to idle, walk, sleep and click reaction.
    Any additional photos become alternate walk frames. Missing states fall
    back to the first photo so that even a one-photo pet remains usable.
    """

    photos = list(photo_paths)
    if not photos:
        raise ValueError("至少需要一张宠物照片")

    roles: Dict[str, List[Path]] = {
        "idle": [photos[0]],
        "walk": [photos[1]] if len(photos) > 1 else [],
        "sleep": [photos[2]] if len(photos) > 2 else [],
        "react": [photos[3]] if len(photos) > 3 else [],
    }

    if len(photos) > 4:
        roles["walk"].extend(photos[4:])

    for role in ROLES:
        if not roles[role]:
            roles[role] = [photos[0]]

    return roles
