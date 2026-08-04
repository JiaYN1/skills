"""Shared animation plans and the lightweight 2D skeleton interchange format.

The image provider uses the pose plans in this module when it asks an image
model for a sequence.  The generated package stores the same sequence as
``animation.json`` and, for ``skeleton``/``hybrid`` output, as ``skeleton.json``.

This is intentionally a small, engine-neutral format.  A sprite frame remains
the source of truth for photographic pets, while the bone timeline supplies a
stable fallback for runtimes that want to animate one base texture without
asking the model for every frame.
"""

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence


ANIMATION_ROLES = ("idle", "walk", "sleep", "react")
DEFAULT_ANIMATION_FPS = 12
MAX_FRAME_COUNT = 24
DEFAULT_FRAME_COUNTS = {
    "idle": 8,
    "walk": 16,
    "sleep": 12,
    "react": 6,
}
ANIMATION_MODES = ("png", "skeleton", "hybrid")


_POSE_PLANS = {
    "idle": (
        "neutral breathing",
        "breathing in",
        "breathing peak",
        "breathing out",
        "neutral breathing",
        "breathing in",
        "breathing peak",
        "breathing out",
    ),
    "walk": (
        "left front and right rear legs extended, contact pose",
        "left contact pose beginning to lift, body moving forward slightly",
        "body lowered, legs absorbing the step",
        "left legs passing under the body, right legs moving forward",
        "left legs passing forward, body centered over the feet",
        "body raised, opposite diagonal contact pose",
        "right front and left rear legs extended, contact pose",
        "right contact pose beginning to lift, body moving forward slightly",
        "body lowered, legs absorbing the step",
        "right legs passing under the body, left legs moving forward",
        "right legs passing forward, body centered over the feet",
        "body raised, opposite diagonal contact pose",
    ),
    "sleep": (
        "curled asleep, eyes closed, relaxed neutral pose",
        "gentle breathing in while curled asleep",
        "gentle breathing peak while curled asleep",
        "gentle breathing out while curled asleep",
        "tiny sleepy ear or whisker twitch, still curled asleep",
        "gentle breathing in while curled asleep",
        "gentle breathing peak while curled asleep",
        "gentle breathing out while curled asleep",
        "returning to curled neutral while asleep",
        "tiny sleepy ear or whisker twitch, still curled asleep",
    ),
    "react": (
        "neutral reaction anticipation",
        "small squash and stretch, happy reaction",
        "largest playful bounce",
        "settling from the bounce",
        "returning to neutral",
        "neutral reaction anticipation",
    ),
}


def normalize_animation_mode(value: Optional[str]) -> str:
    """Return a supported animation mode, defaulting to the hybrid format."""

    mode = str(value or "hybrid").strip().lower()
    return mode if mode in ANIMATION_MODES else "hybrid"


def frame_count_for_role(role: str, requested: Optional[int] = None) -> int:
    """Clamp a requested frame count while keeping useful defaults."""

    default = DEFAULT_FRAME_COUNTS.get(role, 6)
    if requested is None:
        return default
    try:
        count = int(requested)
    except (TypeError, ValueError):
        count = default
    return max(1, min(MAX_FRAME_COUNT, count))


def pose_plan_for(role: str, frame_count: Optional[int] = None) -> List[str]:
    """Return an ordered, cyclic pose plan for an action sequence."""

    count = frame_count_for_role(role, frame_count)
    plan = list(_POSE_PLANS.get(role, _POSE_PLANS["idle"]))
    if count == len(plan):
        return plan
    # Sample the authored cycle evenly instead of appending the first few
    # poses when a longer sequence is requested. This keeps the last pose
    # close to the cycle boundary and works better with sequential AI frames.
    return [plan[round(index * len(plan) / count) % len(plan)] for index in range(count)]


def _sequence_frame_paths(role: str, frame_paths: Sequence[str]) -> List[str]:
    """Expand a single source sprite into a complete procedural sequence."""

    paths = [str(item) for item in frame_paths]
    if len(paths) > 1:
        return paths
    if not paths:
        return []
    return [paths[0]] * frame_count_for_role(role)


def _bone(name: str, parent: Optional[str], length: float = 1.0) -> Dict[str, Any]:
    return {
        "name": name,
        "parent": parent,
        "length": length,
    }


def _root_transform(role: str, index: int, count: int) -> Dict[str, float]:
    phase = (index / max(1, count)) * math.pi * 2.0
    if role == "walk":
        return {
            "x": 0.0,
            "y": round(math.sin(phase * 2.0) * 2.0, 3),
            "rotation": round(math.sin(phase) * 1.5, 3),
            "scale_x": 1.0,
            "scale_y": 1.0,
        }
    if role == "sleep":
        return {
            "x": 0.0,
            "y": round(math.sin(phase) * 1.25, 3),
            "rotation": round(math.sin(phase) * 1.0, 3),
            "scale_x": 1.0,
            "scale_y": round(1.0 + math.sin(phase) * 0.012, 4),
        }
    if role == "react":
        squash = (0.0, 0.06, -0.04, 0.03, 0.0, 0.0)[index % 6]
        return {
            "x": 0.0,
            "y": round(-squash * 16.0, 3),
            "rotation": 0.0,
            "scale_x": round(1.0 + squash, 4),
            "scale_y": round(1.0 - squash, 4),
        }
    return {
        "x": 0.0,
        "y": round(math.sin(phase) * 1.25, 3),
        "rotation": 0.0,
        "scale_x": round(1.0 + math.sin(phase) * 0.008, 4),
        "scale_y": round(1.0 + math.sin(phase) * 0.012, 4),
    }


def _bone_transforms(role: str, index: int, count: int) -> Dict[str, Dict[str, float]]:
    phase = (index / max(1, count)) * math.pi * 2.0
    root = _root_transform(role, index, count)
    if role == "walk":
        leg = math.sin(phase) * 18.0
        opposite_leg = -leg
        return {
            "root": root,
            "body": {"x": 0.0, "y": 0.0, "rotation": round(math.sin(phase) * 2.0, 3), "scale_x": 1.0, "scale_y": 1.0},
            "head": {"x": 0.0, "y": 0.0, "rotation": round(-math.sin(phase) * 1.5, 3), "scale_x": 1.0, "scale_y": 1.0},
            "front_leg": {"x": 0.0, "y": 0.0, "rotation": round(leg, 3), "scale_x": 1.0, "scale_y": 1.0},
            "rear_leg": {"x": 0.0, "y": 0.0, "rotation": round(opposite_leg, 3), "scale_x": 1.0, "scale_y": 1.0},
            "tail": {"x": 0.0, "y": 0.0, "rotation": round(math.sin(phase + 0.8) * 8.0, 3), "scale_x": 1.0, "scale_y": 1.0},
        }
    if role == "sleep":
        return {
            "root": root,
            "body": {"x": 0.0, "y": 0.0, "rotation": round(-4.0 + math.sin(phase) * 1.2, 3), "scale_x": 1.0, "scale_y": 1.0},
            "head": {"x": 0.0, "y": 0.0, "rotation": round(-7.0 + math.sin(phase) * 0.8, 3), "scale_x": 1.0, "scale_y": 1.0},
            "front_leg": {"x": 0.0, "y": 0.0, "rotation": 8.0, "scale_x": 1.0, "scale_y": 1.0},
            "rear_leg": {"x": 0.0, "y": 0.0, "rotation": -8.0, "scale_x": 1.0, "scale_y": 1.0},
            "tail": {"x": 0.0, "y": 0.0, "rotation": round(-12.0 + math.sin(phase) * 1.0, 3), "scale_x": 1.0, "scale_y": 1.0},
        }
    return {
        "root": root,
        "body": {"x": 0.0, "y": 0.0, "rotation": 0.0, "scale_x": 1.0, "scale_y": 1.0},
        "head": {"x": 0.0, "y": 0.0, "rotation": 0.0, "scale_x": 1.0, "scale_y": 1.0},
        "front_leg": {"x": 0.0, "y": 0.0, "rotation": 0.0, "scale_x": 1.0, "scale_y": 1.0},
        "rear_leg": {"x": 0.0, "y": 0.0, "rotation": 0.0, "scale_x": 1.0, "scale_y": 1.0},
        "tail": {"x": 0.0, "y": 0.0, "rotation": 0.0, "scale_x": 1.0, "scale_y": 1.0},
    }


def build_skeleton_manifest(
    assets: Mapping[str, Sequence[str]],
    fps: int = DEFAULT_ANIMATION_FPS,
) -> Dict[str, Any]:
    """Build a portable, sprite-backed 2D skeleton animation manifest."""

    frame_rate = max(1, min(60, int(fps)))
    bones = [
        _bone("root", None),
        _bone("body", "root", 0.8),
        _bone("head", "body", 0.35),
        _bone("front_leg", "body", 0.45),
        _bone("rear_leg", "body", 0.45),
        _bone("tail", "body", 0.55),
    ]
    animations: Dict[str, Any] = {}
    for role in ANIMATION_ROLES:
        source_paths = [str(item) for item in assets.get(role, [])]
        frame_paths = _sequence_frame_paths(role, source_paths)
        if not frame_paths:
            continue
        count = len(frame_paths)
        animations[role] = {
            "loop": role not in {"react"},
            "fps": frame_rate,
            "poses": pose_plan_for(role, count),
            "source_frame_count": len(source_paths),
            "frames": [
                {
                    "index": index,
                    "sprite": frame_path,
                    "duration_ms": round(1000.0 / frame_rate),
                    "bones": _bone_transforms(role, index, count),
                }
                for index, frame_path in enumerate(frame_paths)
            ],
        }

    return {
        "format": "desktop-pet-skeleton",
        "version": 1,
        "render_mode": "sprite-per-frame",
        "notes": "Photo pets use a sprite-backed root rig; generated PNGs remain the visual source of truth.",
        "bones": bones,
        "slots": [{"name": "pet", "bone": "root", "attachment": "sprite"}],
        "animations": animations,
    }


def build_animation_manifest(
    assets: Mapping[str, Sequence[str]],
    mode: str = "hybrid",
    fps: int = DEFAULT_ANIMATION_FPS,
) -> Dict[str, Any]:
    """Return the package-level animation manifest shared by all runtimes."""

    normalized_mode = normalize_animation_mode(mode)
    frame_rate = max(1, min(60, int(fps)))
    sequences: Dict[str, Any] = {}
    for role in ANIMATION_ROLES:
        source_paths = [str(item) for item in assets.get(role, [])]
        frame_paths = _sequence_frame_paths(role, source_paths)
        if not frame_paths:
            continue
        sequences[role] = {
            "loop": role != "react",
            "fps": frame_rate,
            "frame_duration_ms": round(1000.0 / frame_rate),
            "frames": frame_paths,
            "pose_plan": pose_plan_for(role, len(frame_paths)),
            "source_frame_count": len(source_paths),
            "fallback_frame_count": frame_count_for_role(role) if len(source_paths) <= 1 else None,
        }
    manifest: Dict[str, Any] = {
        "format": "desktop-pet-animation",
        "version": 1,
        "mode": normalized_mode,
        "fps": frame_rate,
        "sequences": sequences,
    }
    if normalized_mode in {"skeleton", "hybrid"}:
        manifest["skeleton_path"] = "skeleton.json"
    return manifest


def write_animation_bundle(
    package_dir: Path,
    assets: Mapping[str, Sequence[str]],
    mode: str = "hybrid",
    fps: int = DEFAULT_ANIMATION_FPS,
) -> Dict[str, Any]:
    """Write ``animation.json`` and, when requested, ``skeleton.json``."""

    package_dir.mkdir(parents=True, exist_ok=True)
    manifest = build_animation_manifest(assets, mode=mode, fps=fps)
    (package_dir / "animation.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if "skeleton_path" in manifest:
        skeleton = build_skeleton_manifest(assets, fps=fps)
        (package_dir / "skeleton.json").write_text(
            json.dumps(skeleton, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return manifest
