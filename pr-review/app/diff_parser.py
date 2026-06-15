from __future__ import annotations

from dataclasses import dataclass, field
import re


HUNK_RE = re.compile(r"@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? \+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@")
ANCHOR_RE = re.compile(r"\[anchor:(?P<anchor>A\d{6})\]")
TEST_PATH_PARTS = {"__tests__", "spec", "specs", "test", "tests"}


@dataclass(slots=True)
class DiffLine:
    kind: str
    old_line: int | None
    new_line: int | None
    content: str
    anchor: str | None = None


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
    line_anchors: dict[str, int] = field(default_factory=dict)


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
    assign_line_anchors(files)

    text = _render_annotated_files(files)
    if len(text) > max_chars:
        code_files = [file for file in files if not is_test_file(file.new_path)]
        skipped_test_count = len(files) - len(code_files)
        if code_files and skipped_test_count:
            text = _render_annotated_files(code_files)
            warnings.append(f"diff 内容超过 {max_chars} 字符，已跳过 {skipped_test_count} 个测试文件，优先审查业务代码。")

    if len(text) > max_chars:
        warnings.append(f"diff 内容超过 {max_chars} 字符，已截断；review 会优先覆盖前面的变更。")
        text = _truncate_annotated_diff(text, max_chars)

    prune_line_anchors(files, text)
    return text, warnings


def _render_annotated_files(files: list[ChangedFile]) -> str:
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
                anchor = f" [anchor:{line.anchor}]" if line.anchor else ""
                parts.append(f"{marker}{anchor} [old:{old_value}] [new:{new_value}] {line.content}")

    return "\n".join(parts)


def assign_line_anchors(files: list[ChangedFile]) -> None:
    counter = 1

    for file in files:
        file.line_anchors.clear()
        for hunk in file.hunks:
            for line in hunk.lines:
                if line.kind in {"add", "context"} and line.new_line is not None:
                    line.anchor = f"A{counter:06d}"
                    file.line_anchors[line.anchor] = line.new_line
                    counter += 1
                else:
                    line.anchor = None


def is_test_file(path: str) -> bool:
    normalized = path.replace("\\", "/").lower().strip()
    parts = [part for part in normalized.split("/") if part]
    if not parts:
        return False

    if any(part in TEST_PATH_PARTS for part in parts[:-1]):
        return True

    filename = parts[-1]
    stem = filename.rsplit(".", 1)[0]
    return (
        stem.startswith("test_")
        or stem.endswith("_test")
        or stem.endswith("_spec")
        or ".test." in filename
        or ".spec." in filename
        or "-test." in filename
        or "-spec." in filename
    )


def prune_line_anchors(files: list[ChangedFile], rendered_text: str) -> None:
    visible_anchors = set(ANCHOR_RE.findall(rendered_text))

    for file in files:
        file.line_anchors = {anchor: line for anchor, line in file.line_anchors.items() if anchor in visible_anchors}
        for hunk in file.hunks:
            for line in hunk.lines:
                if line.anchor and line.anchor not in visible_anchors:
                    line.anchor = None


def _truncate_annotated_diff(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return "[DIFF_TRUNCATED]"

    truncated = text[:max_chars]
    newline_index = truncated.rfind("\n")
    if newline_index > 0:
        truncated = truncated[:newline_index]

    return f"{truncated}\n[DIFF_TRUNCATED]" if truncated else "[DIFF_TRUNCATED]"


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
