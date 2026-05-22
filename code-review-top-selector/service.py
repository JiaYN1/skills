#!/usr/bin/env python3
"""HTTP service for selecting top code review comments from CSV files."""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from email import policy
from email.parser import BytesParser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterable
from urllib.parse import parse_qs, urlparse


REVIEW_MARKER = "review"
DEFAULT_LIMIT = 75
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_BASE_URL = "https://api.openai.com/v1"
AI_BATCH_SIZE = 20
OUTPUT_COLUMNS = ["检视详情", "创建时间", "检视者", "质量等级", "检视类型"]
GRADE_RANK = {"d": 4, "c": 3, "b": 2, "a": 1}

ALIASES = {
    "content": [
        "检视详情",
        "review content",
        "review_content",
        "review",
        "comment",
        "comments",
        "content",
        "内容",
        "评论内容",
        "检视意见",
        "评审意见",
        "代码检视意见",
    ],
    "created_at": [
        "创建时间",
        "review time",
        "review_time",
        "created_at",
        "create_time",
        "time",
        "date",
        "提交时间",
        "评论时间",
    ],
    "reviewer": [
        "检视者",
        "reviewer",
        "author",
        "user",
        "name",
        "创建人",
        "评论人",
        "评审人",
    ],
}

TYPE_KEYWORDS = {
    "安全": [
        "安全",
        "权限",
        "认证",
        "鉴权",
        "授权",
        "密码",
        "密钥",
        "token",
        "secret",
        "sql注入",
        "xss",
        "csrf",
        "sanitize",
        "escape",
        "encrypt",
        "vulnerability",
    ],
    "性能": [
        "性能",
        "耗时",
        "复杂度",
        "缓存",
        "批量",
        "索引",
        "查询",
        "循环",
        "内存",
        "n+1",
        "o(",
        "latency",
        "throughput",
        "cache",
        "memory",
    ],
    "设计": [
        "设计",
        "架构",
        "职责",
        "抽象",
        "耦合",
        "解耦",
        "扩展",
        "复用",
        "接口",
        "边界",
        "可维护",
        "design",
        "architecture",
        "abstraction",
    ],
    "可靠性": [
        "异常",
        "错误处理",
        "重试",
        "超时",
        "并发",
        "竞态",
        "空指针",
        "空值",
        "边界条件",
        "幂等",
        "rollback",
        "timeout",
        "retry",
        "race",
        "null",
    ],
    "测试": [
        "测试",
        "单测",
        "用例",
        "覆盖率",
        "mock",
        "fixture",
        "test",
        "coverage",
    ],
    "规范": [
        "规范",
        "命名",
        "格式",
        "缩进",
        "换行",
        "注释",
        "lint",
        "style",
        "naming",
    ],
}

PROBLEM_TERMS = [
    "问题",
    "风险",
    "缺陷",
    "错误",
    "bug",
    "不合理",
    "不正确",
    "会导致",
    "可能导致",
    "容易",
    "当前",
    "这里",
    "this causes",
    "risk",
    "issue",
    "bug",
]

ACTION_TERMS = [
    "建议",
    "请",
    "应该",
    "需要",
    "改为",
    "改成",
    "使用",
    "移除",
    "增加",
    "补充",
    "避免",
    "封装",
    "提取",
    "拆分",
    "should",
    "please",
    "recommend",
    "use",
    "avoid",
    "replace",
    "add",
    "remove",
]

REASONING_TERMS = [
    "因为",
    "否则",
    "原因",
    "导致",
    "以便",
    "这样",
    "从而",
    "否则会",
    "可维护",
    "可扩展",
    "复杂度",
    "职责",
    "边界",
    "why",
    "because",
    "since",
    "therefore",
    "so that",
    "maintain",
    "complexity",
]

DETAIL_TERMS = [
    "例如",
    "比如",
    "可以这样",
    "方案",
    "步骤",
    "伪代码",
    "示例",
    "example",
    "for example",
    "snippet",
    "plan",
]


@dataclass(frozen=True)
class ReviewResult:
    content: str
    created_at: str
    reviewer: str
    grade: str
    review_type: str
    score: int
    index: int

    def as_output_row(self) -> dict[str, str]:
        return {
            "检视详情": self.content,
            "创建时间": self.created_at,
            "检视者": self.reviewer,
            "质量等级": self.grade,
            "检视类型": self.review_type,
        }


@dataclass(frozen=True)
class AIConfig:
    api_key: str
    base_url: str
    model: str
    timeout: int = 90


def normalize_name(value: str) -> str:
    return re.sub(r"[\s_\-]+", "", value.strip().lower())


def clean_cell(value: object) -> str:
    if value is None:
        return ""
    return str(value).lstrip("\ufeff").strip()


def is_review_content(content: str) -> bool:
    return REVIEW_MARKER in content.lower()


def contains_any(text: str, terms: Iterable[str]) -> bool:
    return any(term in text for term in terms)


def count_terms(text: str, terms: Iterable[str]) -> int:
    return sum(1 for term in terms if term in text)


def decode_csv(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def load_csv(data: bytes) -> tuple[list[str], list[dict[str, str]]]:
    text = decode_csv(data)
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
    except csv.Error:
        first_line = text.splitlines()[0] if text.splitlines() else ""
        dialect = csv.excel_tab if "\t" in first_line else csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        raise ValueError("CSV must contain a header row.")
    fieldnames = [field for field in reader.fieldnames if field]
    rows = list(reader)
    if not fieldnames:
        raise ValueError("CSV header row is empty.")
    return fieldnames, rows


def pick_column(
    fieldnames: list[str],
    rows: list[dict[str, str]],
    kind: str,
) -> str:
    fields_by_normalized = {normalize_name(field): field for field in fieldnames}
    for alias in ALIASES[kind]:
        match = fields_by_normalized.get(normalize_name(alias))
        if match:
            return match

    if kind != "content":
        return ""

    scored_fields = []
    for field in fieldnames:
        valid_count = sum(
            1
            for row in rows
            if is_review_content(clean_cell(row.get(field, "")))
        )
        scored_fields.append((valid_count, field))

    valid_count, field = max(scored_fields, key=lambda item: item[0])
    return field if valid_count > 0 else fieldnames[0]


def extract_leading_tags(content: str) -> list[str]:
    tags = []
    cursor = 0
    while content.startswith("【", cursor):
        end = content.find("】", cursor + 1)
        if end < 0:
            break
        tag = content[cursor + 1 : end].strip()
        if not tag:
            break
        tags.append(tag)
        cursor = end + 1
    return tags


def strip_leading_tags(content: str) -> str:
    return re.sub(r"^(?:【[^】]+】)+", "", content).strip()


def normalize_explicit_type(tag: str) -> str:
    lowered = tag.lower()
    if lowered == "review":
        return ""
    for review_type, keywords in TYPE_KEYWORDS.items():
        if review_type in tag or lowered in keywords:
            return review_type
    return tag[:20]


def classify_type(content: str, body: str) -> str:
    explicit_types = []
    for tag in extract_leading_tags(content):
        review_type = normalize_explicit_type(tag)
        if review_type and review_type not in explicit_types:
            explicit_types.append(review_type)
    if explicit_types:
        return "/".join(explicit_types)

    lowered = body.lower()
    scores = {
        review_type: count_terms(lowered, keywords)
        for review_type, keywords in TYPE_KEYWORDS.items()
    }
    review_type, score = max(scores.items(), key=lambda item: item[1])
    return review_type if score > 0 else "综合"


def classify_grade(body: str) -> tuple[str, int]:
    lowered = body.lower()
    length = len(body)
    has_problem = contains_any(lowered, PROBLEM_TERMS)
    has_action = contains_any(lowered, ACTION_TERMS)
    has_reasoning = contains_any(lowered, REASONING_TERMS)
    has_detail = contains_any(lowered, DETAIL_TERMS)
    has_code = "`" in body or re.search(r"\b(if|for|while|return|class|def|func)\b", lowered)
    has_structure = "\n" in body or "：" in body or ":" in body or "；" in body or ";" in body
    question_count = body.count("?") + body.count("？")

    score = 0
    score += min(length // 12, 25)
    score += 18 if has_reasoning else 0
    score += 15 if has_detail or has_code else 0
    score += 12 if has_problem else 0
    score += 12 if has_action else 0
    score += 8 if has_structure else 0

    question_only = question_count > 0 and not has_action and not has_reasoning and length < 120
    standard_only = (
        contains_any(lowered, TYPE_KEYWORDS["规范"])
        and not has_problem
        and not has_reasoning
        and length < 100
    )

    if question_only or standard_only:
        return "a", score

    if has_reasoning and has_action and (has_detail or has_code or has_structure or length >= 160):
        return "d", score

    if (
        has_reasoning
        and (has_problem or has_action or length >= 120)
    ) or (has_problem and has_action and length >= 120):
        return "c", score

    if has_problem or has_action:
        return "b", score

    return "a", score


def analyze_comment(
    content: str,
    created_at: str,
    reviewer: str,
    index: int,
) -> ReviewResult:
    body = strip_leading_tags(content)
    grade, score = classify_grade(body)
    review_type = classify_type(content, body)
    return ReviewResult(
        content=content,
        created_at=created_at,
        reviewer=reviewer,
        grade=grade,
        review_type=review_type,
        score=score,
        index=index,
    )


def review_text_length(result: ReviewResult) -> int:
    return len(strip_leading_tags(result.content))


def normalize_grade(value: object) -> str:
    grade = str(value or "").strip().lower()
    if grade.startswith("grade "):
        grade = grade.replace("grade ", "", 1).strip()
    return grade if grade in GRADE_RANK else ""


def normalize_ai_type(value: object, content: str) -> str:
    review_type = str(value or "").strip()
    if not review_type:
        return classify_type(content, strip_leading_tags(content))
    return review_type[:40]


def normalize_base_url(base_url: str) -> str:
    value = base_url.strip().rstrip("/")
    if not value:
        raise ValueError("base_url is required when AI scoring is enabled.")
    if value.endswith("/chat/completions"):
        return value
    return value + "/chat/completions"


def build_ai_prompt(batch: list[ReviewResult]) -> list[dict[str, str]]:
    reviews = [
        {
            "index": result.index,
            "content": result.content,
        }
        for result in batch
    ]
    system_prompt = (
        "You classify code review comment quality. "
        "Use exactly one grade: a, b, c, or d. "
        "Grade a: only a question or pure restatement of a coding standard. "
        "Grade b: identifies the issue and says what to do. "
        "Grade c: explains design thinking or reasoning. "
        "Grade d: includes c-level reasoning plus why/how to improve, concrete plan, or examples. "
        "Also assign a short Chinese review type such as 安全, 性能, 设计, 可靠性, 测试, 规范, or 综合. "
        "Return only valid JSON."
    )
    user_prompt = (
        "Score these review comments and return this exact JSON shape: "
        '{"results":[{"index":0,"grade":"d","type":"设计"}]}\n'
        + json.dumps({"reviews": reviews}, ensure_ascii=False)
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def call_chat_completion(config: AIConfig, messages: list[dict[str, str]]) -> str:
    payload = {
        "model": config.model,
        "messages": messages,
        "temperature": 0,
    }
    request = urllib.request.Request(
        normalize_base_url(config.base_url),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=config.timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")[:500]
        raise ValueError(f"AI scoring request failed with HTTP {exc.code}: {error_body}") from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"AI scoring request failed: {exc.reason}") from exc

    data = json.loads(raw.decode("utf-8"))
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("AI scoring response did not contain choices[0].message.content.") from exc


def extract_json_payload(text: str) -> Any:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        starts = [pos for pos in (cleaned.find("{"), cleaned.find("[")) if pos >= 0]
        ends = [pos for pos in (cleaned.rfind("}"), cleaned.rfind("]")) if pos >= 0]
        if not starts or not ends:
            raise ValueError("AI scoring response was not valid JSON.")
        return json.loads(cleaned[min(starts) : max(ends) + 1])


def parse_ai_scores(text: str) -> dict[int, tuple[str, str]]:
    payload = extract_json_payload(text)
    if isinstance(payload, dict):
        items = payload.get("results", payload.get("reviews", []))
    else:
        items = payload
    if not isinstance(items, list):
        raise ValueError("AI scoring JSON must include a results array.")

    scores: dict[int, tuple[str, str]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        grade = normalize_grade(item.get("grade"))
        if not grade:
            continue
        review_type = str(item.get("type") or item.get("review_type") or "").strip()
        scores[index] = (grade, review_type)
    return scores


def score_reviews_with_ai(
    results: list[ReviewResult],
    config: AIConfig,
) -> list[ReviewResult]:
    if not config.api_key.strip():
        raise ValueError("open_ai_key is required when AI scoring is enabled.")

    scored: dict[int, tuple[str, str]] = {}
    for offset in range(0, len(results), AI_BATCH_SIZE):
        batch = results[offset : offset + AI_BATCH_SIZE]
        response_text = call_chat_completion(config, build_ai_prompt(batch))
        scored.update(parse_ai_scores(response_text))

    missing = [result.index for result in results if result.index not in scored]
    if missing:
        preview = ", ".join(str(index) for index in missing[:10])
        raise ValueError(f"AI scoring response missed review index: {preview}")

    return [
        replace(
            result,
            grade=scored[result.index][0],
            review_type=normalize_ai_type(scored[result.index][1], result.content),
        )
        for result in results
    ]


def select_reviews(
    data: bytes,
    limit: int = DEFAULT_LIMIT,
    include_lower: bool = False,
    ai_config: AIConfig | None = None,
) -> tuple[bytes, dict[str, object], list[ReviewResult]]:
    fieldnames, rows = load_csv(data)
    content_column = pick_column(fieldnames, rows, "content")
    created_at_column = pick_column(fieldnames, rows, "created_at")
    reviewer_column = pick_column(fieldnames, rows, "reviewer")

    seen = set()
    analyzed = []
    valid_count = 0
    duplicate_count = 0

    for index, row in enumerate(rows):
        content = clean_cell(row.get(content_column, ""))
        if not is_review_content(content):
            continue
        valid_count += 1
        dedupe_key = re.sub(r"\s+", " ", content)
        if dedupe_key in seen:
            duplicate_count += 1
            continue
        seen.add(dedupe_key)
        analyzed.append(
            analyze_comment(
                content=content,
                created_at=clean_cell(row.get(created_at_column, "")),
                reviewer=clean_cell(row.get(reviewer_column, "")),
                index=index,
            )
        )

    if ai_config and analyzed:
        analyzed = score_reviews_with_ai(analyzed, ai_config)

    candidates = analyzed
    selected = sorted(
        candidates,
        key=lambda result: (
            GRADE_RANK[result.grade],
            review_text_length(result),
            result.score,
            -result.index,
        ),
        reverse=True,
    )[:limit]

    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=OUTPUT_COLUMNS)
    writer.writeheader()
    for result in selected:
        writer.writerow(result.as_output_row())

    metadata = {
        "input_rows": len(rows),
        "valid_rows": valid_count,
        "duplicate_rows": duplicate_count,
        "unique_valid_rows": len(analyzed),
        "selected_rows": len(selected),
        "content_column": content_column,
        "created_at_column": created_at_column,
        "reviewer_column": reviewer_column,
        "limit": limit,
        "include_lower": include_lower,
        "scoring_mode": "ai" if ai_config else "heuristic",
        "candidate_filter": "all",
        "model": ai_config.model if ai_config else "",
    }
    return output.getvalue().encode("utf-8-sig"), metadata, selected


def parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def parse_limit(value: object) -> int:
    if value in (None, ""):
        return DEFAULT_LIMIT
    try:
        limit = int(str(value))
    except ValueError as exc:
        raise ValueError("limit must be an integer.") from exc
    if limit < 1 or limit > 500:
        raise ValueError("limit must be between 1 and 500.")
    return limit


def get_field(fields: dict[str, str], *names: str) -> str:
    for name in names:
        value = fields.get(name)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def build_ai_config(fields: dict[str, str]) -> AIConfig | None:
    use_ai_score = parse_bool(
        get_field(fields, "use_ai_score", "ai_score", "use_ai")
    )
    if not use_ai_score:
        return None

    api_key = get_field(fields, "open_ai_key", "openai_key", "api_key") or os.getenv(
        "OPENAI_API_KEY", ""
    )
    base_url = get_field(fields, "base_url", "openai_base_url") or os.getenv(
        "OPENAI_BASE_URL", DEFAULT_BASE_URL
    )
    model = get_field(fields, "model", "openai_model") or os.getenv(
        "OPENAI_MODEL", DEFAULT_MODEL
    )
    if not api_key:
        raise ValueError("open_ai_key is required when AI scoring is enabled.")
    return AIConfig(api_key=api_key, base_url=base_url, model=model)


def parse_multipart(content_type: str, body: bytes) -> tuple[bytes, dict[str, str]]:
    message = BytesParser(policy=policy.default).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode()
        + body
    )
    if not message.is_multipart():
        raise ValueError("multipart request is invalid.")

    fields: dict[str, str] = {}
    file_content = b""
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        payload = part.get_payload(decode=True) or b""
        filename = part.get_filename()
        if filename or name == "file":
            file_content = payload
            continue
        charset = part.get_content_charset() or "utf-8"
        fields[name] = payload.decode(charset, errors="replace")

    if not file_content:
        raise ValueError("multipart request must include a file field.")
    return file_content, fields


def build_html() -> bytes:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Code Review Top Selector</title>
  <style>
    :root { color-scheme: light; font-family: Arial, "Microsoft YaHei", sans-serif; }
    body { margin: 0; background: #f4f6f8; color: #17202a; }
    main { max-width: 1100px; margin: 40px auto; padding: 0 24px; }
    h1 { font-size: 28px; margin: 0 0 24px; }
    form { background: #fff; border: 1px solid #d9e0e7; border-radius: 8px; padding: 24px; display: grid; gap: 18px; }
    label { display: grid; gap: 8px; font-size: 14px; font-weight: 700; }
    input[type="file"], input[type="number"], input[type="password"], input[type="url"], input[type="text"] { box-sizing: border-box; width: 100%; padding: 10px 12px; border: 1px solid #b9c4cf; border-radius: 6px; font-size: 14px; background: #fff; }
    .inline { display: flex; gap: 10px; align-items: center; font-weight: 400; }
    .inline input { width: 16px; height: 16px; }
    .ai-fields { display: grid; gap: 14px; padding: 16px; border: 1px solid #d9e0e7; border-radius: 8px; background: #f8fafc; }
    .ai-fields[hidden] { display: none; }
    .actions { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; }
    button { width: fit-content; border: 0; border-radius: 6px; padding: 10px 16px; background: #1f6feb; color: #fff; font-size: 14px; font-weight: 700; cursor: pointer; }
    button.secondary { background: #2f3a45; }
    button:hover { background: #195bc2; }
    button.secondary:hover { background: #222b33; }
    button:disabled { background: #a8b3bf; cursor: not-allowed; }
    code { background: #e9eef3; padding: 2px 5px; border-radius: 4px; }
    .api { margin-top: 16px; color: #46515c; font-size: 13px; }
    .status { min-height: 20px; color: #46515c; font-size: 14px; }
    .status.error { color: #b42318; }
    .preview { margin-top: 24px; background: #fff; border: 1px solid #d9e0e7; border-radius: 8px; overflow: hidden; }
    .preview[hidden] { display: none; }
    .preview-header { display: flex; justify-content: space-between; gap: 16px; align-items: center; padding: 16px 18px; border-bottom: 1px solid #d9e0e7; }
    .preview-header h2 { margin: 0; font-size: 18px; }
    .summary { color: #46515c; font-size: 13px; }
    .table-wrap { overflow: auto; max-height: 560px; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { border-bottom: 1px solid #e5ebf1; padding: 10px 12px; text-align: left; vertical-align: top; }
    th { position: sticky; top: 0; background: #f8fafc; z-index: 1; font-weight: 700; }
    td.content { min-width: 420px; white-space: pre-wrap; line-height: 1.5; }
    td.nowrap { white-space: nowrap; }
  </style>
</head>
<body>
  <main>
    <h1>Code Review Top Selector</h1>
    <form id="selector-form" method="post" action="/api/select" enctype="multipart/form-data" onsubmit="return false">
      <label>
        CSV 文件
        <input type="file" name="file" accept=".csv,.tsv,text/csv,text/tab-separated-values" required>
      </label>
      <label>
        输出 Top N
        <input type="number" name="limit" value="75" min="1" max="500" step="1">
      </label>
      <label class="inline">
        <input type="checkbox" id="use-ai-score" name="use_ai_score" value="true">
        使用 AI 评分
      </label>
      <div class="ai-fields" id="ai-fields" hidden>
        <label>
          OpenAI Key
          <input type="password" id="open-ai-key" name="open_ai_key" autocomplete="off">
        </label>
        <label>
          Base URL
          <input type="url" name="base_url" value="https://api.openai.com/v1">
        </label>
        <label>
          模型
          <input type="text" name="model" value="gpt-4o-mini">
        </label>
      </div>
      <div class="actions">
        <button type="button" id="preview-button">预览结果</button>
        <button type="button" id="download-button" class="secondary" disabled>下载 CSV</button>
        <span class="status" id="status"></span>
      </div>
    </form>
    <p class="api">API: <code>POST /api/select</code>, form-data 字段名 <code>file</code>，可传 <code>limit</code>、<code>use_ai_score</code>、<code>open_ai_key</code>、<code>base_url</code>、<code>model</code>。</p>
    <section class="preview" id="preview" hidden>
      <div class="preview-header">
        <h2>结果预览</h2>
        <div class="summary" id="summary"></div>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>序号</th>
              <th>质量等级</th>
              <th>检视类型</th>
              <th>检视者</th>
              <th>创建时间</th>
              <th>检视详情</th>
            </tr>
          </thead>
          <tbody id="preview-body"></tbody>
        </table>
      </div>
    </section>
  </main>
  <script>
    const outputColumns = ["检视详情", "创建时间", "检视者", "质量等级", "检视类型"];
    const useAi = document.getElementById("use-ai-score");
    const aiFields = document.getElementById("ai-fields");
    const keyInput = document.getElementById("open-ai-key");
    const form = document.getElementById("selector-form");
    const statusEl = document.getElementById("status");
    const previewEl = document.getElementById("preview");
    const summaryEl = document.getElementById("summary");
    const previewBody = document.getElementById("preview-body");
    const previewButton = document.getElementById("preview-button");
    const downloadButton = document.getElementById("download-button");
    let previewRows = [];

    function syncAiFields() {
      aiFields.hidden = !useAi.checked;
      keyInput.required = useAi.checked;
    }

    function setStatus(message, isError = false) {
      statusEl.textContent = message;
      statusEl.classList.toggle("error", isError);
    }

    function escapeHtml(value) {
      return String(value == null ? "" : value)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
    }

    function renderPreview(rows, metadata) {
      previewRows = rows;
      previewBody.innerHTML = rows.map((row, index) => `
        <tr>
          <td class="nowrap">${index + 1}</td>
          <td class="nowrap">${escapeHtml(row["质量等级"])}</td>
          <td class="nowrap">${escapeHtml(row["检视类型"])}</td>
          <td class="nowrap">${escapeHtml(row["检视者"])}</td>
          <td class="nowrap">${escapeHtml(row["创建时间"])}</td>
          <td class="content">${escapeHtml(row["检视详情"])}</td>
        </tr>
      `).join("");
      summaryEl.textContent = `输入 ${metadata.input_rows} 行，含 review ${metadata.valid_rows} 行，去重 ${metadata.duplicate_rows} 行，输出 ${metadata.selected_rows} 行`;
      previewEl.hidden = false;
      downloadButton.disabled = rows.length === 0;
    }

    function escapeCsv(value) {
      const text = String(value == null ? "" : value);
      return /[",\\r\\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
    }

    function downloadCsv() {
      if (!previewRows.length) return;
      const lines = [
        outputColumns.map(escapeCsv).join(","),
        ...previewRows.map(row => outputColumns.map(column => escapeCsv(row[column])).join(",")),
      ];
      const blob = new Blob(["\\ufeff" + lines.join("\\r\\n")], { type: "text/csv;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "top_reviews.csv";
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    }

    function markPreviewStale() {
      if (!previewRows.length) return;
      previewRows = [];
      downloadButton.disabled = true;
      setStatus("参数已变更，请重新预览");
    }

    async function previewResults() {
      previewRows = [];
      downloadButton.disabled = true;
      previewButton.disabled = true;
      setStatus("处理中...");
      try {
        const formData = new FormData(form);
        formData.set("format", "json");
        const response = await fetch("/api/select", { method: "POST", body: formData });
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.error || `HTTP ${response.status}`);
        }
        renderPreview(data.rows || [], data.metadata || {});
        setStatus("已生成预览");
      } catch (error) {
        previewEl.hidden = true;
        setStatus(error.message || "处理失败", true);
      } finally {
        previewButton.disabled = false;
      }
    }

    useAi.addEventListener("change", syncAiFields);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
    });
    previewButton.addEventListener("click", previewResults);
    form.addEventListener("input", markPreviewStale);
    form.addEventListener("change", markPreviewStale);
    downloadButton.addEventListener("click", downloadCsv);
    syncAiFields();
  </script>
</body>
</html>
""".encode("utf-8")


class ReviewServiceHandler(BaseHTTPRequestHandler):
    server_version = "CodeReviewTopSelector/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        print(
            "%s - - [%s] %s"
            % (self.address_string(), self.log_date_time_string(), fmt % args),
            flush=True,
        )

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.send_bytes(HTTPStatus.OK, build_html(), "text/html; charset=utf-8")
            return
        if parsed.path == "/healthz":
            self.send_json(HTTPStatus.OK, {"status": "ok", "service": "code-review-top-selector"})
            return
        self.send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/select":
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return

        try:
            query = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
            body = self.read_request_body()
            csv_content, fields = self.extract_csv_content(body)
            merged = {**query, **fields}
            limit = parse_limit(merged.get("limit"))
            include_lower = parse_bool(merged.get("include_lower"))
            ai_config = build_ai_config(merged)

            csv_output, metadata, selected = select_reviews(
                csv_content,
                limit=limit,
                include_lower=include_lower,
                ai_config=ai_config,
            )

            if merged.get("format") == "json":
                self.send_json(
                    HTTPStatus.OK,
                    {
                        "metadata": metadata,
                        "rows": [result.as_output_row() for result in selected],
                    },
                )
                return

            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="top_reviews.csv"')
            self.send_header("X-Selected-Count", str(metadata["selected_rows"]))
            self.send_header("Content-Length", str(len(csv_output)))
            self.end_headers()
            self.wfile.write(csv_output)
        except ValueError as exc:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - keep service response stable.
            self.log_error("Unhandled error: %r", exc)
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "internal_server_error"})

    def read_request_body(self) -> bytes:
        content_length = self.headers.get("Content-Length")
        if not content_length:
            raise ValueError("Content-Length header is required.")
        try:
            length = int(content_length)
        except ValueError as exc:
            raise ValueError("Content-Length header is invalid.") from exc
        max_upload_bytes = getattr(self.server, "max_upload_bytes", 20 * 1024 * 1024)
        if length <= 0:
            raise ValueError("request body is empty.")
        if length > max_upload_bytes:
            raise ValueError("request body exceeds upload limit.")
        return self.rfile.read(length)

    def extract_csv_content(self, body: bytes) -> tuple[bytes, dict[str, str]]:
        content_type = self.headers.get("Content-Type", "")
        lowered = content_type.lower()
        if lowered.startswith("multipart/form-data"):
            return parse_multipart(content_type, body)
        if lowered.startswith("text/csv") or lowered.startswith("application/octet-stream"):
            fields = {
                "open_ai_key": self.headers.get("X-OpenAI-Key", ""),
                "base_url": self.headers.get("X-OpenAI-Base-URL", ""),
                "model": self.headers.get("X-OpenAI-Model", ""),
                "use_ai_score": self.headers.get("X-Use-AI-Score", ""),
            }
            return body, {key: value for key, value in fields.items() if value}
        raise ValueError("Content-Type must be multipart/form-data or text/csv.")

    def send_json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_bytes(status, body, "application/json; charset=utf-8")

    def send_bytes(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the code review top selector service.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=18898)
    parser.add_argument("--max-upload-mb", type=int, default=20)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), ReviewServiceHandler)
    server.max_upload_bytes = args.max_upload_mb * 1024 * 1024
    display_host = "127.0.0.1" if args.host == "0.0.0.0" else args.host
    print(f"Listening on http://{display_host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Shutting down", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
