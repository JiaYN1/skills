from __future__ import annotations

import json
import logging
from urllib import error, request

from app.models import RepoDigestItem
from app.time_window import TimeWindow


LOGGER = logging.getLogger(__name__)
MAX_WECOM_MARKDOWN_CHARS = 3900


class NotifyError(RuntimeError):
    """Raised when a notification cannot be delivered."""


class Notifier:
    def send(self, title: str, content: str) -> None:
        raise NotImplementedError


class LogNotifier(Notifier):
    def send(self, title: str, content: str) -> None:
        LOGGER.info("[dry-run] %s\n%s", title, content)


class WeComWebhookNotifier(Notifier):
    def __init__(self, webhook_url: str, timeout: int) -> None:
        self.webhook_url = webhook_url
        self.timeout = timeout

    def send(self, title: str, content: str) -> None:
        del title
        payload = {
            "msgtype": "markdown",
            "markdown": {"content": content[:MAX_WECOM_MARKDOWN_CHARS]},
        }
        response = _post_json(self.webhook_url, payload, self.timeout)
        errcode = response.get("errcode")
        if errcode not in (0, None):
            raise NotifyError(f"WeCom webhook error: {response}")


def create_notifier(webhook_url: str | None, timeout: int, dry_run: bool) -> Notifier:
    if dry_run:
        return LogNotifier()
    if not webhook_url:
        raise NotifyError("WECOM_WEBHOOK_URL is required")
    return WeComWebhookNotifier(webhook_url, timeout)


def format_digest(
    window: TimeWindow,
    items: list[RepoDigestItem],
    estimated_bytes: int,
    downloaded_bytes: int,
    monthly_downloaded_after: int,
    monthly_budget: int,
) -> str:
    if not items:
        return (
            "**GitHub 昨日 Star 增长榜**\n"
            f">统计窗口：{_escape(window.label)}\n"
            ">结果：没有符合条件的项目"
        )

    lines = [
        f"**GitHub 昨日 Star 增长 Top {len(items)}**",
        f">统计窗口：{_escape(window.label)}",
        f">GH Archive 估算下载：{_format_bytes(estimated_bytes)}，实际下载：{_format_bytes(downloaded_bytes)}",
        f">本月下载预算：{_format_bytes(monthly_downloaded_after)} / {_format_bytes(monthly_budget)}",
        "",
    ]
    for index, item in enumerate(items, start=1):
        meta = [
            item.language or "Unknown",
            f"total {_format_int(item.total_stars)} stars",
            f"{_format_int(item.forks_count)} forks",
        ]
        lines.append(f"{index}. [{_escape(item.full_name)}]({item.html_url})  +{_format_int(item.unique_stargazers)}")
        lines.append(f"   {' | '.join(_escape(part) for part in meta)}")
        if item.description:
            lines.append(f"   {_escape(_compact(item.description, 110))}")
        lines.append("")
    return "\n".join(lines).strip()[:MAX_WECOM_MARKDOWN_CHARS]


def _post_json(url: str, payload: dict, timeout: int) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise NotifyError(f"HTTP {exc.code}: {detail}") from exc
    except error.URLError as exc:
        raise NotifyError(f"request failed: {exc}") from exc

    if not body:
        return {}
    try:
        decoded = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        detail = body[:300].decode("utf-8", errors="replace")
        raise NotifyError(f"invalid JSON response: {detail}") from exc
    if not isinstance(decoded, dict):
        raise NotifyError(f"unexpected response type: {type(decoded).__name__}")
    return decoded


def _escape(value: str) -> str:
    return value.replace("<", "&lt;").replace(">", "&gt;")


def _compact(value: str, max_chars: int) -> str:
    single_line = " ".join(value.split())
    if len(single_line) <= max_chars:
        return single_line
    return single_line[: max_chars - 3].rstrip() + "..."


def _format_int(value: int) -> str:
    return f"{value:,}"


def _format_bytes(value: int) -> str:
    gib = 1024**3
    tib = 1024**4
    if value >= tib:
        return f"{value / tib:.2f} TiB"
    return f"{value / gib:.2f} GiB"
