from __future__ import annotations

from dataclasses import dataclass, field
import re


HUNK_RE = re.compile(r"@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? \+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@")


@dataclass(slots=True)
class DiffLine:
    kind: str
    old_line: int | None
    new_line: int | None
    content: str


@dataclass(slots=True)
class DiffHunk:
    header: str
    lines: list[DiffLine] = field(default_factory=list)


@dataclass(slots=True)
class ChangedFile:
    old_path: str
    new_path: str
    patch: str
    hunks: list[DiffHunk] = field(default_factory=list)
    commentable_new_lines: set[int] = field(default_factory=set)
    added_new_lines: set[int] = field(default_factory=set)


def parse_unified_diff(diff_text: str) -> list[ChangedFile]:
    files: list[ChangedFile] = []
    current_lines: list[str] = []
    old_path = ""
    new_path = ""

    def flush() -> None:
        nonlocal current_lines, old_path, new_path
        if not current_lines:
            return
        patch = "\n".join(current_lines)
        path = new_path or old_path or "unknown"
        files.append(parse_patch(patch, path, old_path=old_path, new_path=new_path))
        current_lines = []
        old_path = ""
        new_path = ""

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            flush()
            old_path, new_path = _parse_diff_git_paths(line)
            current_lines.append(line)
            continue

        if line.startswith("--- "):
            old_path = _strip_diff_path(line[4:].strip())
        elif line.startswith("+++ "):
            new_path = _strip_diff_path(line[4:].strip())

        if current_lines or line.startswith("@@"):
            current_lines.append(line)

    flush()

    if not files and diff_text.strip():
        files.append(parse_patch(diff_text, "unknown"))

    return files


def parse_patch(patch: str, file_path: str, old_path: str | None = None, new_path: str | None = None) -> ChangedFile:
    old_path = old_path or file_path
    new_path = new_path or file_path
    changed_file = ChangedFile(old_path=old_path, new_path=new_path, patch=patch)

    current_hunk: DiffHunk | None = None
    old_line = 0
    new_line = 0

    for raw_line in patch.splitlines():
        if raw_line.startswith("--- "):
            changed_file.old_path = _strip_diff_path(raw_line[4:].strip()) or changed_file.old_path
            continue
        if raw_line.startswith("+++ "):
            changed_file.new_path = _strip_diff_path(raw_line[4:].strip()) or changed_file.new_path
            continue

        hunk_match = HUNK_RE.match(raw_line)
        if hunk_match:
            current_hunk = DiffHunk(header=raw_line)
            changed_file.hunks.append(current_hunk)
            old_line = int(hunk_match.group("old_start"))
            new_line = int(hunk_match.group("new_start"))
            continue

        if current_hunk is None:
            continue
        if raw_line.startswith("\\"):
            continue

        marker = raw_line[:1] or " "
        content = raw_line[1:] if marker in {" ", "+", "-"} else raw_line

        if marker == "+":
            current_hunk.lines.append(DiffLine(kind="add", old_line=None, new_line=new_line, content=content))
            changed_file.commentable_new_lines.add(new_line)
            changed_file.added_new_lines.add(new_line)
            new_line += 1
        elif marker == "-":
            current_hunk.lines.append(DiffLine(kind="delete", old_line=old_line, new_line=None, content=content))
            old_line += 1
        else:
            current_hunk.lines.append(DiffLine(kind="context", old_line=old_line, new_line=new_line, content=content))
            changed_file.commentable_new_lines.add(new_line)
            old_line += 1
            new_line += 1

    return changed_file


def render_annotated_diff(files: list[ChangedFile], max_chars: int) -> tuple[str, list[str]]:
    warnings: list[str] = []
    parts: list[str] = []

    for file in files:
        parts.append(f"文件: {file.new_path}")
        parts.append(f"可评论新行: {format_line_ranges(file.commentable_new_lines) or '无'}")

        if not file.hunks:
            parts.append("该文件没有可审查的文本 diff。")
            continue

        for hunk in file.hunks:
            parts.append(hunk.header)
            for line in hunk.lines:
                marker = {"add": "+", "delete": "-", "context": " "}.get(line.kind, " ")
                old_value = line.old_line if line.old_line is not None else "-"
                new_value = line.new_line if line.new_line is not None else "-"
                parts.append(f"{marker} [old:{old_value}] [new:{new_value}] {line.content}")

    text = "\n".join(parts)
    if len(text) > max_chars:
        warnings.append(f"diff 内容超过 {max_chars} 字符，已截断；review 会优先覆盖前面的变更。")
        text = text[:max_chars] + "\n[DIFF_TRUNCATED]"

    return text, warnings


def format_line_ranges(lines: set[int]) -> str:
    if not lines:
        return ""

    ordered = sorted(lines)
    ranges: list[str] = []
    start = ordered[0]
    previous = ordered[0]

    for line in ordered[1:]:
        if line == previous + 1:
            previous = line
            continue
        ranges.append(_format_range(start, previous))
        start = previous = line

    ranges.append(_format_range(start, previous))
    return ", ".join(ranges)


def _format_range(start: int, end: int) -> str:
    return str(start) if start == end else f"{start}-{end}"


def _parse_diff_git_paths(line: str) -> tuple[str, str]:
    match = re.match(r"diff --git a/(.+) b/(.+)$", line)
    if not match:
        return "", ""
    return match.group(1), match.group(2)


def _strip_diff_path(path: str) -> str:
    if path == "/dev/null":
        return ""
    if path.startswith("a/") or path.startswith("b/"):
        return path[2:]
    return path.strip('"')
