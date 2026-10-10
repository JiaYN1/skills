from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from typing import Any

import httpx

from .dedupe import (
    comment_fingerprint,
    deduplicate_within_batch,
    mark_already_reported,
    with_fingerprint_marker,
)
from .diff_parser import (
    ChangedFile,
    assign_line_anchors,
    render_annotated_diff,
    render_file_annotated_diff,
)
from .providers import ExistingComment, PullRequestData
from .schemas import ReviewComment, ReviewSummary
from .strategy import (
    ReviewPlan,
    build_review_plan,
    large_pr_concurrency,
    per_file_max_chars,
    skip_summary,
)


CATEGORIES = {"性能", "设计", "安全", "可维护性", "错误处理", "测试", "规范", "逻辑"}
SEVERITIES = {"严重", "建议", "规范"}
_NO_DIFF_MESSAGE = "没有可审查的文本 diff。"


class ReviewError(RuntimeError):
    pass


REVIEW_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "comments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "line": {"type": "integer"},
                    "line_anchor": {"type": "string"},
                    "category": {"type": "string", "enum": sorted(CATEGORIES)},
                    "severity": {"type": "string", "enum": sorted(SEVERITIES)},
                    "message": {"type": "string"},
                    "suggestion": {"type": "string"},
                    "code_example": {"type": "string"},
                    "language": {"type": "string"},
                },
                "required": [
                    "file_path",
                    "line",
                    "line_anchor",
                    "category",
                    "severity",
                    "message",
                    "suggestion",
                    "code_example",
                    "language",
                ],
                "additionalProperties": False,
            },
        },
        "summary": {
            "type": "object",
            "properties": {
                "total": {"type": "integer"},
                "severe": {"type": "integer"},
                "suggestion": {"type": "integer"},
                "style": {"type": "integer"},
                "text": {"type": "string"},
            },
            "required": ["total", "severe", "suggestion", "style", "text"],
            "additionalProperties": False,
        },
    },
    "required": ["comments", "summary"],
    "additionalProperties": False,
}


SYSTEM_PROMPT = """你是一个严谨的 PR 代码审查助手。你只审查给出的 diff，不臆造问题。

审查类别只能使用：性能 / 设计 / 安全 / 可维护性 / 错误处理 / 测试 / 规范 / 逻辑。

规则：
1. 每条意见必须是真实、具体、可执行的问题。
2. file_path 必须严格使用 diff 中出现的文件路径。
3. 行号基准只能使用 diff 中同一行的 `[new:<数字>]`；它是 PR 变更后文件的绝对行号，也是发布用行号。不要使用 `[old:<数字>]`、`@@` hunk 起始行、相对偏移、工具展示行号或当前磁盘文件行号。
4. 必须选择带 `[anchor:<值>]` 的可评论新行；如果问题发生在删除行或整段逻辑上，挂到最近的相关 anchor 行。找不到可映射的 anchor 时不要输出该 comment。
5. line_anchor 必须原样复制该行的 anchor 值；line 必须填写同一行 `[new:<数字>]` 的数字，不能填写 hunk 序号、old 行号或相对偏移。
6. message 写中文，包含具体风险或行为后果，不要写泛泛建议。
7. suggestion 写中文，说明具体修改方向。
8. code_example 默认可以写简短伪代码；只有当修正代码不超过 10 行时，才给出可直接参考的完整修正代码。
9. 没有实际问题时返回空 comments。
10. 只输出符合 JSON schema 的 JSON，不要输出 Markdown。"""


async def generate_review(
    data: PullRequestData,
    model: str | None = None,
    existing: list[ExistingComment] | None = None,
) -> tuple[list[ReviewComment], ReviewSummary, list[str]]:
    """Generate review comments for a PR.

    Containers whose diff exceeds ``LARGE_PR_DIFF_LINES`` are reviewed one file
    at a time, most important files first. Comments that already exist on the
    PR are returned flagged: exact matches as ``already_posted`` (never
    published twice), look-alikes as ``duplicate_suspect`` (the page lets the
    user decide whether to send them).
    """

    plan = build_review_plan(data.files)
    if not plan.selected:
        return [], _empty_summary(), [_NO_DIFF_MESSAGE]

    if plan.large:
        comments, warnings = await _generate_large_review(data, plan, model=model)
    else:
        comments, warnings = await _generate_single_review(data, plan, model=model)

    comments = deduplicate_within_batch(comments)
    comments, duplicates, suspects = mark_already_reported(comments, existing or [])
    if duplicates:
        warnings.append(
            f"其中 {len(duplicates)} 条意见已存在于该 PR/MR，已标记为不重复发布。"
        )
    if suspects:
        warnings.append(
            f"其中 {len(suspects)} 条意见可能与已有意见重复，已标记待确认（默认不勾选，可手动勾选后发布）。"
        )

    return comments, _build_summary(comments), warnings


async def _generate_single_review(
    data: PullRequestData,
    plan: ReviewPlan,
    model: str | None,
) -> tuple[list[ReviewComment], list[str]]:
    annotated_diff, warnings = render_annotated_diff(plan.selected, max_chars=_max_diff_chars())
    if not annotated_diff.strip():
        return [], [*warnings, _NO_DIFF_MESSAGE]

    raw_result = await _call_llm(data, annotated_diff, model=model)
    return _normalize_comments(raw_result.get("comments", []), plan.selected), warnings


async def _generate_large_review(
    data: PullRequestData,
    plan: ReviewPlan,
    model: str | None,
) -> tuple[list[ReviewComment], list[str]]:
    warnings = [_large_pr_notice(plan)]
    skipped_notice = skip_summary(plan)
    if skipped_notice:
        warnings.append(skipped_notice.lstrip("；"))

    assign_line_anchors(plan.selected)
    budget = per_file_max_chars()
    semaphore = asyncio.Semaphore(large_pr_concurrency())

    async def review_one(file: ChangedFile) -> tuple[list[ReviewComment], list[str], str | None]:
        async with semaphore:
            annotated_diff, file_warnings = render_file_annotated_diff(file, budget)
            if not annotated_diff.strip():
                return [], file_warnings, None
            try:
                raw_result = await _call_llm(data, annotated_diff, model=model, focus_path=file.new_path)
            except ReviewError as exc:
                return [], file_warnings, f"{file.new_path} 审查失败，已跳过该文件：{exc}"
            return _normalize_comments(raw_result.get("comments", []), [file]), file_warnings, None

    results = await asyncio.gather(*(review_one(file) for file in plan.selected))

    comments: list[ReviewComment] = []
    for file_comments, file_warnings, error in results:
        comments.extend(file_comments)
        warnings.extend(file_warnings)
        if error:
            warnings.append(error)
    return comments, warnings


def _large_pr_notice(plan: ReviewPlan) -> str:
    details = "、".join(
        f"{path}（{plan.selected_tiers.get(path, '业务代码')}）" for path in plan.selected_paths
    )
    return (
        f"检测到大 PR：本次变更 {plan.changed_lines} 行（阈值 {plan.threshold} 行），"
        f"已改为逐文件检视最关键的 {len(plan.selected)} 个文件：{details}。"
    )


def _empty_summary() -> ReviewSummary:
    return ReviewSummary(
        total=0,
        severe=0,
        suggestion=0,
        style=0,
        text="总结：共发现 0 个问题（严重 0 个，建议 0 个，规范 0 个）",
    )


def _max_diff_chars() -> int:
    raw = os.getenv("MAX_DIFF_CHARS", "").strip()
    if not raw:
        return 120000
    try:
        return max(int(raw), 1)
    except ValueError:
        return 120000


async def _call_llm(
    data: PullRequestData,
    annotated_diff: str,
    model: str | None,
    focus_path: str | None = None,
) -> dict[str, Any]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ReviewError("未配置 OPENAI_API_KEY，无法生成自动 review。")

    selected_model = model or os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    url = f"{base_url}/chat/completions"
    user_prompt = _build_user_prompt(data, annotated_diff, focus_path=focus_path)

    payload: dict[str, Any] = {
        "model": selected_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "pr_review_result",
                "schema": REVIEW_JSON_SCHEMA,
                "strict": True,
            },
        },
    }

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
        response = await client.post(url, headers=headers, json=payload)
        if response.status_code == 400:
            payload["response_format"] = {"type": "json_object"}
            response = await client.post(url, headers=headers, json=payload)
        if response.status_code >= 400:
            body = response.text[:400].replace("\n", " ")
            raise ReviewError(f"LLM 调用失败: HTTP {response.status_code} {body}")

    try:
        content = response.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ReviewError("LLM 响应格式不符合 chat completions 结构。") from exc

    return _parse_json_content(content)


def _build_user_prompt(data: PullRequestData, annotated_diff: str, focus_path: str | None = None) -> str:
    scope = f"本次只审查文件: {focus_path}\n\n" if focus_path else ""
    return f"""请审查以下 PR diff，并返回结构化 JSON。

PR: {data.ref.web_url}
平台: {data.ref.platform}
仓库: {data.ref.project_path}
标题: {data.title or ""}

{scope}说明：
- diff 中只有带 `[anchor:<值>]` 的行可以发布行级评论。
- line 必须以同一行的 `[new:<数字>]` 为准；不要使用 hunk 序号、old 行号、当前磁盘文件行号或相对偏移。
- 每条 comment 必须同时填写 `line_anchor` 和对应的 `line`。
{_focus_hint(focus_path)}
{annotated_diff}
"""


def _focus_hint(focus_path: str | None) -> str:
    if not focus_path:
        return ""
    return f"- 只报告 `{focus_path}` 内的问题，不要评论其他文件。\n"


def _normalize_comments(raw_comments: list[dict[str, Any]], files: list[ChangedFile]) -> list[ReviewComment]:
    path_map = {file.new_path: file for file in files}
    comments: list[ReviewComment] = []

    for raw in raw_comments:
        if not isinstance(raw, dict):
            continue

        anchor_target = _resolve_line_anchor(_clean_text(raw.get("line_anchor")), files)
        has_valid_anchor = anchor_target is not None
        if anchor_target:
            file_path, requested_line = anchor_target
        else:
            file_path = _resolve_file_path(str(raw.get("file_path", "")), path_map)
            if not file_path:
                continue

            try:
                requested_line = int(raw.get("line"))
            except (TypeError, ValueError):
                continue

        if requested_line < 1:
            continue

        category = str(raw.get("category", "可维护性"))
        if category not in CATEGORIES:
            category = "可维护性"

        severity = str(raw.get("severity", "建议"))
        if severity not in SEVERITIES:
            severity = "建议"

        message = _clean_text(raw.get("message"))
        suggestion = _clean_text(raw.get("suggestion"))
        code_example = _strip_code_fence(str(raw.get("code_example", "")).strip())
        language = _clean_language(raw.get("language"), file_path)

        if not message or not suggestion or not code_example:
            continue

        changed_file = path_map[file_path]
        line = requested_line
        publishable = has_valid_anchor and line in changed_file.commentable_new_lines
        publish_warning = None
        if not has_valid_anchor:
            publish_warning = "缺少有效 line_anchor，为避免推送到错误行，只展示，不能自动发布。"
        elif not publishable:
            publish_warning = "该行不在 PR diff 的可评论新行中，只能展示，不能自动发布。"

        body = format_review_body(
            file_path=file_path,
            line=line,
            category=category,
            message=message,
            suggestion=suggestion,
            language=language,
            code_example=code_example,
        )
        comment_id = _comment_id(file_path, line, category, message)
        comments.append(
            ReviewComment(
                id=comment_id,
                file_path=file_path,
                line=line,
                category=category,
                severity=severity,
                message=message,
                suggestion=suggestion,
                code_example=code_example,
                language=language,
                body=body,
                publishable=publishable,
                publish_warning=publish_warning,
            )
        )

    return comments


def _build_summary(comments: list[ReviewComment]) -> ReviewSummary:
    total = len(comments)
    severe = sum(1 for comment in comments if comment.severity == "严重")
    style = sum(1 for comment in comments if comment.severity == "规范" or comment.category == "规范")
    suggestion = max(total - severe - style, 0)
    text = f"总结：共发现 {total} 个问题（严重 {severe} 个，建议 {suggestion} 个，规范 {style} 个）"
    return ReviewSummary(total=total, severe=severe, suggestion=suggestion, style=style, text=text)


def format_review_body(
    *,
    file_path: str,
    line: int,
    category: str,
    message: str,
    suggestion: str,
    language: str,
    code_example: str,
) -> str:
    body = (
        f"【review】【{category}】 `{file_path}` 第 {line} 行\n\n"
        f"问题：{message}\n\n"
        f"修改建议：{suggestion}\n\n"
        f"```{language}\n{code_example}\n```"
    )
    return with_fingerprint_marker(body, comment_fingerprint(file_path, category, message))


def _parse_json_content(content: str) -> dict[str, Any]:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, flags=re.DOTALL)
        if not match:
            raise ReviewError("LLM 未返回 JSON。")
        parsed = json.loads(match.group(0))

    if not isinstance(parsed, dict):
        raise ReviewError("LLM 返回的 JSON 顶层必须是对象。")
    parsed.setdefault("comments", [])
    parsed.setdefault("summary", {})
    return parsed


def _resolve_file_path(raw_path: str, path_map: dict[str, ChangedFile]) -> str:
    if raw_path in path_map:
        return raw_path
    matches = [path for path in path_map if path.endswith(raw_path) or raw_path.endswith(path)]
    return matches[0] if len(matches) == 1 else ""


def _resolve_line_anchor(raw_anchor: str, files: list[ChangedFile]) -> tuple[str, int] | None:
    if not raw_anchor:
        return None

    matches: list[tuple[str, int]] = []
    for file in files:
        line = file.line_anchors.get(raw_anchor)
        if line is not None:
            matches.append((file.new_path, line))

    return matches[0] if len(matches) == 1 else None


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _strip_code_fence(value: str) -> str:
    match = re.match(r"^```[\w+-]*\n(?P<code>.*)\n```$", value, flags=re.DOTALL)
    return match.group("code").strip() if match else value


def _clean_language(value: Any, file_path: str) -> str:
    language = str(value or "").strip().lower()
    if language:
        return language
    suffix = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
    return {
        "py": "python",
        "js": "javascript",
        "ts": "typescript",
        "tsx": "tsx",
        "jsx": "jsx",
        "go": "go",
        "java": "java",
        "kt": "kotlin",
        "rs": "rust",
        "cpp": "cpp",
        "cc": "cpp",
        "c": "c",
        "h": "c",
        "hpp": "cpp",
        "cs": "csharp",
        "rb": "ruby",
        "php": "php",
        "sh": "bash",
        "sql": "sql",
    }.get(suffix, "")


def _comment_id(file_path: str, line: int, category: str, message: str) -> str:
    digest = hashlib.sha1(f"{file_path}:{line}:{category}:{message}".encode("utf-8")).hexdigest()
    return digest[:12]
