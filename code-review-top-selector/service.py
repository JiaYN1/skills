#!/usr/bin/env python3
"""HTTP service for selecting top code review comments from CSV files."""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Iterable
from urllib.parse import parse_qs, urlparse


REVIEW_PREFIX = "【review】"
DEFAULT_LIMIT = 75
OUTPUT_COLUMNS = ["检视详情", "创建时间", "检视者", "质量等级", "检视类型"]

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


def normalize_name(value: str) -> str:
    return re.sub(r"[\s_\-]+", "", value.strip().lower())


def clean_cell(value: object) -> str:
    if value is None:
        return ""
    return str(value).lstrip("\ufeff").strip()


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
    reader = csv.DictReader(io.StringIO(text))
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
            if clean_cell(row.get(field, "")).startswith(REVIEW_PREFIX)
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


def select_reviews(
    data: bytes,
    limit: int = DEFAULT_LIMIT,
    include_lower: bool = False,
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
        if not content.startswith(REVIEW_PREFIX):
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

    candidates = analyzed if include_lower else [
        result for result in analyzed if result.grade in {"d", "c"}
    ]
    grade_rank = {"d": 4, "c": 3, "b": 2, "a": 1}
    selected = sorted(
        candidates,
        key=lambda result: (
            grade_rank[result.grade],
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
    main { max-width: 760px; margin: 56px auto; padding: 0 24px; }
    h1 { font-size: 28px; margin: 0 0 24px; }
    form { background: #fff; border: 1px solid #d9e0e7; border-radius: 8px; padding: 24px; display: grid; gap: 18px; }
    label { display: grid; gap: 8px; font-size: 14px; font-weight: 700; }
    input[type="file"], input[type="number"] { box-sizing: border-box; width: 100%; padding: 10px 12px; border: 1px solid #b9c4cf; border-radius: 6px; font-size: 14px; background: #fff; }
    .inline { display: flex; gap: 10px; align-items: center; font-weight: 400; }
    .inline input { width: 16px; height: 16px; }
    button { width: fit-content; border: 0; border-radius: 6px; padding: 10px 16px; background: #1f6feb; color: #fff; font-size: 14px; font-weight: 700; cursor: pointer; }
    button:hover { background: #195bc2; }
    code { background: #e9eef3; padding: 2px 5px; border-radius: 4px; }
    .api { margin-top: 16px; color: #46515c; font-size: 13px; }
  </style>
</head>
<body>
  <main>
    <h1>Code Review Top Selector</h1>
    <form method="post" action="/api/select" enctype="multipart/form-data">
      <label>
        CSV 文件
        <input type="file" name="file" accept=".csv,text/csv" required>
      </label>
      <label>
        输出 Top N
        <input type="number" name="limit" value="75" min="1" max="500" step="1">
      </label>
      <label class="inline">
        <input type="checkbox" name="include_lower" value="true">
        包含 a/b 等级
      </label>
      <button type="submit">生成 CSV</button>
    </form>
    <p class="api">API: <code>POST /api/select</code>, form-data 字段名 <code>file</code>，可传 <code>limit</code> 控制输出 Top N。</p>
  </main>
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

            csv_output, metadata, selected = select_reviews(
                csv_content,
                limit=limit,
                include_lower=include_lower,
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
            return body, {}
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
