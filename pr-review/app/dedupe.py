"""Detect review comments that were already posted on the PR/MR.

Two levels of matching are used:

* **exact** — the fingerprint marker embedded in the body
  (``<!-- pr-review-id:... -->``), which survives line shifts because it does
  not depend on the line number, or, for comments posted before the marker
  existed, the category and problem description parsed back out of our own
  body format. Exact matches are never published twice.
* **similar** — the subject text of an existing comment is close to the one we
  just generated (same wording, different format, or posted by another
  reviewer / the agent skill). Those are only marked as suspected duplicates:
  they stay publishable and the page lets the user decide whether to send them.

The similarity pass is intentionally allowed to over-match; it flags rather
than blocks, so a false positive costs the user one glance, not a lost finding.
"""

from __future__ import annotations

import difflib
import hashlib
import os
import re

from .providers import ExistingComment
from .schemas import ReviewComment


MARKER_PREFIX = "pr-review-id"
MARKER_RE = re.compile(r"<!--\s*pr-review-id:([0-9a-fA-F]{6,40})\s*-->")
REVIEW_BODY_PREFIX = "【review】"
BODY_CATEGORY_RE = re.compile(r"【review】\s*【(?P<category>[^】]+)】")
BODY_MESSAGE_RE = re.compile(r"问题：(?P<message>.*?)(?:\n\s*\n|$)", re.DOTALL)
SKILL_TAIL_RE = re.compile(r"[；;，,]?\s*(?:修改建议|建议)\s*[:：]")
REFERENCE_TAIL_RE = re.compile(r"[；;，,]?\s*参考代码如下\s*[:：]?\s*$")
DUPLICATE_WARNING = "PR 上已存在相同意见，不再重复发布。"
SIMILAR_WARNING = "PR 上可能存在相同意见，请确认是否仍需发布。"

SIMILARITY_THRESHOLD_ENV = "DUPLICATE_SIMILARITY_THRESHOLD"
DEFAULT_SIMILARITY_THRESHOLD = 0.7
MIN_SUBJECT_CHARS = 12
MAX_SUBJECT_CHARS = 4000



def comment_fingerprint(file_path: str, category: str, message: str) -> str:
    """Stable identity of a finding, independent of the line number."""

    normalized = "\n".join((_normalize(file_path), _normalize(category), _normalize(message)))
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]


def comment_fingerprint_of(comment: ReviewComment) -> str:
    return comment_fingerprint(comment.file_path, comment.category, comment.message)


def fingerprint_marker(fingerprint: str) -> str:
    return f"<!-- {MARKER_PREFIX}:{fingerprint} -->"


def with_fingerprint_marker(body: str, fingerprint: str) -> str:
    """Return ``body`` carrying exactly one fingerprint marker."""

    text = (body or "").strip()
    marker = fingerprint_marker(fingerprint)
    if marker in text:
        return text
    text = MARKER_RE.sub("", text).strip()
    return f"{text}\n\n{marker}" if text else marker


def body_fingerprints(body: str, file_path: str = "") -> set[str]:
    """Fingerprints of a posted comment: marker first, body format second."""

    markers = {match.lower() for match in MARKER_RE.findall(body or "")}
    if markers:
        return markers

    text = body or ""
    if REVIEW_BODY_PREFIX not in text:
        return set()

    category_match = BODY_CATEGORY_RE.search(text)
    message_match = BODY_MESSAGE_RE.search(text)
    if not category_match or not message_match:
        return set()

    message = message_match.group("message").strip()
    if not message:
        return set()
    return {comment_fingerprint(file_path, category_match.group("category"), message)}


def existing_fingerprints(existing: list[ExistingComment]) -> dict[str, ExistingComment]:
    """Map every fingerprint we can recover from existing comments to its comment."""

    found: dict[str, ExistingComment] = {}
    for comment in existing:
        for fingerprint in body_fingerprints(comment.body, comment.file_path):
            found.setdefault(fingerprint, comment)
    return found


def body_subject(body: str) -> str:
    """The issue text of a posted comment, whatever format it uses."""

    text = body or ""
    if not text.strip():
        return ""

    if REVIEW_BODY_PREFIX in text:
        message_match = BODY_MESSAGE_RE.search(text)
        if message_match and message_match.group("message").strip():
            return _trim_subject(message_match.group("message"))

    # agent skill 格式：【review】【类别】【严重度】<问题>；修改建议：...
    subject = text
    if REVIEW_BODY_PREFIX in subject:
        subject = subject.split(REVIEW_BODY_PREFIX, 1)[1]
        for _ in range(2):
            stripped = subject.lstrip()
            if not stripped.startswith("【"):
                break
            _, _, subject = stripped.partition("】")
    subject = SKILL_TAIL_RE.split(subject)[0]
    subject = REFERENCE_TAIL_RE.sub("", subject)
    return _trim_subject(subject)


def subject_similarity(left: str, right: str) -> float:
    """How close two issue texts are; containment counts as a full match."""

    first = _normalize(left).lower()[:MAX_SUBJECT_CHARS]
    second = _normalize(right).lower()[:MAX_SUBJECT_CHARS]
    if not first or not second:
        return 0.0
    if first in second or second in first:
        return 1.0
    return difflib.SequenceMatcher(None, first, second, autojunk=False).ratio()


def similarity_threshold() -> float:
    raw = (os.getenv(SIMILARITY_THRESHOLD_ENV) or "").strip()
    if not raw:
        return DEFAULT_SIMILARITY_THRESHOLD
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_SIMILARITY_THRESHOLD
    return value if 0.0 < value <= 1.0 else DEFAULT_SIMILARITY_THRESHOLD


def find_similar_comment(
    comment: ReviewComment, existing: list[ExistingComment], threshold: float | None = None
) -> tuple[ExistingComment | None, float]:
    """Best existing comment that looks like the same finding, if any."""

    limit = similarity_threshold() if threshold is None else threshold
    subject = _normalize(comment.message)
    if len(subject) < MIN_SUBJECT_CHARS:
        return None, 0.0

    best: ExistingComment | None = None
    best_score = 0.0
    for source in existing:
        source_subject = body_subject(source.body)
        if len(source_subject) < MIN_SUBJECT_CHARS:
            continue
        if source.file_path and comment.file_path and source.file_path != comment.file_path:
            continue
        score = subject_similarity(subject, source_subject)
        if score > best_score:
            best, best_score = source, score

    if best is not None and best_score >= limit:
        return best, best_score
    return None, best_score


def mark_already_reported(
    comments: list[ReviewComment],
    existing: list[ExistingComment],
    *,
    include_similar: bool = True,
) -> tuple[list[ReviewComment], list[ReviewComment], list[ReviewComment]]:
    """Flag comments that already exist on the PR.

    Returns ``(updated, duplicates, suspects)``. ``updated`` preserves the input
    order and contains the same comments. Exact duplicates are marked
    ``already_posted`` and taken out of the publishable set; suspected ones are
    only marked ``duplicate_suspect`` so the page can let the user decide.
    """

    if not existing:
        return list(comments), [], []

    known = existing_fingerprints(existing)
    threshold = similarity_threshold()
    updated: list[ReviewComment] = []
    duplicates: list[ReviewComment] = []
    suspects: list[ReviewComment] = []

    for comment in comments:
        source = known.get(comment_fingerprint_of(comment))
        if source is not None:
            marked = comment.model_copy(
                update={
                    "publishable": False,
                    "already_posted": True,
                    "existing_url": source.url,
                    "publish_warning": DUPLICATE_WARNING,
                }
            )
            updated.append(marked)
            duplicates.append(marked)
            continue

        if include_similar:
            similar, score = find_similar_comment(comment, existing, threshold)
            if similar is not None:
                marked = comment.model_copy(
                    update={
                        "duplicate_suspect": True,
                        "existing_url": similar.url,
                        "publish_warning": SIMILAR_WARNING,
                        "duplicate_score": round(score, 3),
                    }
                )
                updated.append(marked)
                suspects.append(marked)
                continue

        updated.append(comment)

    return updated, duplicates, suspects



def deduplicate_within_batch(comments: list[ReviewComment]) -> list[ReviewComment]:
    """Drop repeated findings produced inside one review run."""

    seen: set[str] = set()
    unique: list[ReviewComment] = []
    for comment in comments:
        fingerprint = comment_fingerprint_of(comment)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        unique.append(comment)
    return unique


def _normalize(value: object) -> str:
    return " ".join(str(value or "").split())


def _trim_subject(value: str) -> str:
    return _normalize(value)[:MAX_SUBJECT_CHARS]
