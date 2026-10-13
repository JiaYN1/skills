"""Deterministic, Pillow-only frame quality checks for action sequences.

The image model generates every frame on its own, so a sequence can contain a
frame whose body size, colour, or pose no longer matches its neighbours. Those
frames are what a user ends up deleting by hand. This module turns the
measurements that sequence normalization already produces into explicit,
explainable flags, so the server and the preview can point at the frame that
broke instead of relying on the user's eye.

Nothing here calls the network, writes files, or needs FastAPI: the server
imports it, and so do the tests.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

HISTOGRAM_BINS = 4
#: How far the body size may drift from the sequence median (linear, not area).
SCALE_TOLERANCE = 0.28
#: How much worse than typical a frame may register before it looks foreign.
#: All three terms must be exceeded: a ratio to the median, a robust deviation
#: (median absolute deviation, which tolerates a swinging gait cycle), and an
#: absolute floor so an almost perfect sequence stays quiet.
REGISTRATION_RATIO = 1.8
REGISTRATION_MAD = 4.5
REGISTRATION_FLOOR = 8.0
#: Colour/histogram distance at which two frames no longer look like one pet.
IDENTITY_TOLERANCE = 0.34
IDENTITY_MAD = 4.5
#: A consensus needs a few frames; shorter sequences are never judged.
MIN_FRAMES_FOR_CONSENSUS = 3

REASON_LABELS = {
    "unreadable": "读取失败",
    "body-scale": "身体大小与同组不一致",
    "registration": "姿态与同组配准不上",
    "identity": "毛色/花纹与同组不一致",
    "clipped": "肢端越出画布被裁切",
}

#: How a flag is described back to the image model when a frame is retried.
PROMPT_HINTS = {
    "unreadable": "the frame was empty or unreadable",
    "body-scale": "the body came out at the wrong size",
    "registration": "the pose did not line up with the neighbouring frames",
    "identity": "the colours or markings did not match the reference",
    "clipped": "a limb ran off the canvas",
}


def prompt_hint(reasons: Sequence[str]) -> str:
    """Return one sentence fragment describing why a frame was rejected."""

    hints = [PROMPT_HINTS[reason] for reason in reasons if reason in PROMPT_HINTS]
    if not hints:
        return ""
    if len(hints) == 1:
        return hints[0]
    return ", ".join(hints[:-1]) + " and " + hints[-1]


def _median(values: Sequence[float]) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _quantize(channel, bins: int = HISTOGRAM_BINS) -> List[float]:
    """Return a normalized coarse histogram of one 8-bit channel."""

    counts = channel.histogram()
    step = 256.0 / float(max(1, bins))
    grouped = [0.0] * bins
    for value, count in enumerate(counts):
        grouped[min(bins - 1, int(value // step))] += float(count)
    total = sum(grouped)
    if total <= 0:
        return grouped
    return [value / total for value in grouped]


def subject_signature(image, box=None) -> Dict[str, Any]:
    """Return the colour, mass, and body-box signature of one prepared frame.

    Only the middle of the subject is sampled: limbs, cast edges, and
    antialiased borders would otherwise dominate the colour histogram, so a
    raised paw would read as a colour change.
    """

    from PIL import Image, ImageStat

    alpha = image.getchannel("A")
    bounds = box or alpha.getbbox() or (0, 0, image.width, image.height)
    width = max(1, bounds[2] - bounds[0])
    height = max(1, bounds[3] - bounds[1])
    core = (
        bounds[0] + int(width * 0.2),
        bounds[1] + int(height * 0.2),
        max(bounds[0] + int(width * 0.2) + 1, bounds[0] + int(width * 0.8)),
        max(bounds[1] + int(height * 0.2) + 1, bounds[1] + int(height * 0.8)),
    )
    core_rgb = image.convert("RGB").crop(core)
    core_alpha = alpha.crop(core).point(lambda value: 255 if value > 128 else 0)
    # Composite over one neutral tone so transparent corners are comparable
    # across frames instead of injecting random background colour.
    neutral = Image.new("RGB", core_rgb.size, (128, 128, 128))
    masked = Image.composite(core_rgb, neutral, core_alpha)

    histogram: List[float] = []
    for channel in masked.split():
        histogram.extend(_quantize(channel))
    return {
        "mass": float(ImageStat.Stat(alpha).sum[0]),
        "box": list(bounds),
        "mean": [round(value / 255.0, 4) for value in ImageStat.Stat(masked).mean[:3]],
        "histogram": [round(value, 5) for value in histogram],
    }


def subject_signatures(
    source_paths: Sequence[Path],
    background_mode: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return one signature per source frame, never raising on a bad frame.

    A frame that cannot even be read is reported as an ``error`` signature so
    the judge can flag it instead of failing the whole edit.
    """

    from pet_assets import _prepare_subject

    signatures: List[Dict[str, Any]] = []
    for path in source_paths:
        try:
            image, box, _size = _prepare_subject(path, background_mode, False)
        except Exception as error:  # noqa: BLE001 - any failure is a quality flag
            signatures.append({"error": f"{type(error).__name__}: {error}"})
            continue
        signature = subject_signature(image, box)
        signature["error"] = ""
        signatures.append(signature)
    return signatures


def _consensus(signatures: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return the median signature of the readable frames in a sequence."""

    usable = [item for item in signatures if item and not item.get("error")]
    if len(usable) < MIN_FRAMES_FOR_CONSENSUS:
        return None
    return {
        "mass": _median([float(item.get("mass", 0.0)) for item in usable]),
        "mean": [
            _median([float(item.get("mean", [0, 0, 0])[channel]) for item in usable])
            for channel in range(3)
        ],
        "histogram": [
            _median([float(item.get("histogram", [])[bin_index]) for item in usable])
            for bin_index in range(len(usable[0].get("histogram", [])))
        ],
    }


def signature_distance(signature: Dict[str, Any], reference: Dict[str, Any]) -> float:
    """Return a 0..1 colour/marking distance between two frame signatures."""

    histogram_a = signature.get("histogram") or []
    histogram_b = reference.get("histogram") or []
    if not histogram_a or len(histogram_a) != len(histogram_b):
        return 1.0
    histogram = sum(
        abs(float(a) - float(b)) for a, b in zip(histogram_a, histogram_b)
    ) / 3.0
    mean_a = signature.get("mean") or [0.0, 0.0, 0.0]
    mean_b = reference.get("mean") or [0.0, 0.0, 0.0]
    colour = sum(abs(float(a) - float(b)) for a, b in zip(mean_a, mean_b)) / 3.0
    return round(max(0.0, min(1.0, 0.5 * histogram + 0.5 * colour)), 4)


def _outlier_threshold(
    population: Sequence[float],
    ratio: float,
    mad_multiplier: float,
    floor: float,
    absolute: float = 0.0,
) -> float:
    """Return the robust "this frame does not belong" threshold for a metric.

    A fixed multiple of the median is too blunt here: a real walk cycle already
    spreads its registration cost across the gait phase, and every sequence has
    its own amount of legitimate variation. The median absolute deviation scales
    with that variation, so a frame only stands out when it is far outside what
    the rest of the sequence does.
    """

    median = _median(population)
    deviations = [abs(float(value) - median) for value in population]
    mad = _median(deviations)
    return max(median * ratio, median + mad_multiplier * mad, floor, absolute)


def judge_sequence(
    report: Dict[str, Any],
    signatures: Optional[Sequence[Dict[str, Any]]] = None,
    scale_tolerance: float = SCALE_TOLERANCE,
    registration_ratio: float = REGISTRATION_RATIO,
    registration_mad: float = REGISTRATION_MAD,
    registration_floor: float = REGISTRATION_FLOOR,
    identity_tolerance: float = IDENTITY_TOLERANCE,
    identity_mad: float = IDENTITY_MAD,
) -> List[Dict[str, Any]]:
    """Return one per-frame verdict for a normalized sequence.

    Every entry carries the measured metrics plus the reasons the frame looks
    wrong; ``reasons`` is empty for a frame that fits its sequence. Sequences
    with fewer than ``MIN_FRAMES_FOR_CONSENSUS`` frames are never judged,
    because a consensus needs something to compare against.
    """

    frames = list(report.get("frames") or [])
    if len(frames) < MIN_FRAMES_FOR_CONSENSUS:
        return []

    masses = [max(1.0, float(frame.get("mass") or 0.0)) for frame in frames]
    median_mass = _median(masses)
    costs = [max(0.0, float(frame.get("alignment_cost") or 0.0)) for frame in frames]
    cost_threshold = _outlier_threshold(
        costs, registration_ratio, registration_mad, registration_floor
    )
    consensus = _consensus(list(signatures or []))
    identity_distances: List[float] = []
    if consensus is not None:
        identity_distances = [
            signature_distance(item, consensus)
            if item and not item.get("error")
            else 0.0
            for item in (signatures or [])
        ]
        identity_threshold = _outlier_threshold(
            identity_distances,
            ratio=1.0,
            mad_multiplier=identity_mad,
            floor=0.0,
            absolute=identity_tolerance,
        )
    else:
        identity_threshold = identity_tolerance

    verdicts: List[Dict[str, Any]] = []
    for frame in frames:
        index = int(frame.get("index", 0))
        reasons: List[str] = []
        signature = (
            signatures[index]
            if signatures is not None and index < len(signatures)
            else None
        )
        mass = max(1.0, float(frame.get("mass") or 0.0))
        body_scale = (mass / median_mass) ** 0.5 if median_mass > 0 else 1.0
        cost = max(0.0, float(frame.get("alignment_cost") or 0.0))
        identity = (
            identity_distances[index] if index < len(identity_distances) else None
        )

        if signature is not None and signature.get("error"):
            reasons.append("unreadable")
        else:
            if abs(body_scale - 1.0) > scale_tolerance:
                reasons.append("body-scale")
            if cost > cost_threshold:
                reasons.append("registration")
            if identity is not None and identity > identity_threshold:
                reasons.append("identity")
        if frame.get("clipped"):
            reasons.append("clipped")
        verdicts.append(
            {
                "index": index,
                "reasons": reasons,
                "metrics": {
                    "body_scale": round(body_scale, 4),
                    "registration_cost": round(cost, 4),
                    "identity_distance": identity,
                    "clipped": bool(frame.get("clipped")),
                },
            }
        )
    return verdicts


def flagged_frames(verdicts: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return only the verdicts that carry at least one reason."""

    return [dict(item) for item in verdicts if item.get("reasons")]


def frame_flags(verdicts: Sequence[Dict[str, Any]], index: int) -> List[str]:
    """Return the reasons recorded for one frame index."""

    for item in verdicts:
        if int(item.get("index", -1)) == int(index):
            return list(item.get("reasons") or [])
    return []


def describe(verdicts: Sequence[Dict[str, Any]], limit: int = 6) -> str:
    """Return a short Chinese summary of the flagged frames, if any."""

    flagged = flagged_frames(verdicts)
    if not flagged:
        return ""
    grouped: Dict[str, List[int]] = {}
    for item in flagged:
        for reason in item["reasons"]:
            grouped.setdefault(reason, []).append(int(item["index"]) + 1)
    parts = [
        f"{REASON_LABELS.get(reason, reason)}：第 "
        + "、".join(str(index) for index in sorted(indices)[:limit])
        + " 帧"
        for reason, indices in sorted(grouped.items())
    ]
    return "；".join(parts)
