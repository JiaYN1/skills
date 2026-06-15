from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any

import httpx

from .diff_parser import ChangedFile, render_annotated_diff
from .providers import PullRequestData
from .schemas import ReviewComment, ReviewSummary


CATEGORIES = {"性能", "设计", "安全", "可维护性", "错误处理", "测试", "规范", "逻辑"}
SEVERITIES = {"严重", "建议", "规范"}


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
3. 必须选择带 `[anchor:<值>]` 的可评论新行；如果问题发生在删除行或整段逻辑上，挂到最近的相关 anchor 行。
4. line_anchor 必须原样复制该行的 anchor 值；line 必须填写同一行 `new:<数字>` 的数字，不能填写 hunk 序号、old 行号或相对偏移。
5. message 写中文，包含具体风险或行为后果，不要写泛泛建议。
6. suggestion 写中文，说明具体修改方向。
7. code_example 默认可以写简短伪代码；只有当修正代码不超过 10 行时，才给出可直接参考的完整修正代码。
8. 没有实际问题时返回空 comments。
9. 只输出符合 JSON schema 的 JSON，不要输出 Markdown。"""


async def generate_review(data: PullRequestData, model: str | None = None) -> tuple[list[ReviewComment], ReviewSummary, list[str]]:
    max_diff_chars = int(os.getenv("MAX_DIFF_CHARS", "120000"))
    annotated_diff, warnings = render_annotated_diff(data.files, max_chars=max_diff_chars)
    if not annotated_diff.strip():
        summary = ReviewSummary(total=0, severe=0, suggestion=0, style=0, text="总结：共发现 0 个问题（严重 0 个，建议 0 个，规范 0 个）")
        return [], summary, ["没有可审查的文本 diff。"]

    raw_result = await _call_llm(data, annotated_diff, model=model)
    comments = _normalize_comments(raw_result.get("comments", []), data.files)
    summary = _build_summary(comments)
    return comments, summary, warnings


async def _call_llm(data: PullRequestData, annotated_diff: str, model: str | None) -> dict[str, Any]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ReviewError("未配置 OPENAI_API_KEY，无法生成自动 review。")

    selected_model = model or os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    url = f"{base_url}/chat/completions"
    user_prompt = f"""请审查以下 PR diff，并返回结构化 JSON。

PR: {data.ref.web_url}
平台: {data.ref.platform}
仓库: {data.ref.project_path}
标题: {data.title or ""}

说明：diff 中只有带 `[anchor:<值>]` 的行可以发布行级评论。每条 comment 必须同时填写 `line_anchor` 和对应的 `line`。

{annotated_diff}
"""

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


def _normalize_comments(raw_comments: list[dict[str, Any]], files: list[ChangedFile]) -> list[ReviewComment]:
    path_map = {file.new_path: file for file in files}
    comments: list[ReviewComment] = []

    for raw in raw_comments:
        if not isinstance(raw, dict):
            continue

        anchor_target = _resolve_line_anchor(_clean_text(raw.get("line_anchor")), files)
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
        line = _normalize_comment_line(requested_line, changed_file)
        publishable = line in changed_file.commentable_new_lines
        publish_warning = None
        if not publishable:
            publish_warning = "该行不在 PR diff 的可评论新行中，只能展示，不能自动发布。"

        body = _format_review_body(
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


def _format_review_body(
    *,
    file_path: str,
    line: int,
    category: str,
    message: str,
    suggestion: str,
    language: str,
    code_example: str,
) -> str:
    return (
        f"【review】【{category}】 `{file_path}` 第 {line} 行\n\n"
        f"问题：{message}\n\n"
        f"修改建议：{suggestion}\n\n"
        f"```{language}\n{code_example}\n```"
    )


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


def _normalize_comment_line(line: int, changed_file: ChangedFile) -> int:
    if line in changed_file.commentable_new_lines or not changed_file.commentable_new_lines:
        return line

    ordered_lines = sorted(changed_file.commentable_new_lines)
    return min(ordered_lines, key=lambda candidate: (abs(candidate - line), candidate))


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
