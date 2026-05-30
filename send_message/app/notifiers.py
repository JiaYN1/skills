from __future__ import annotations

import json
import logging
from typing import Protocol
from urllib import error, parse, request

from app.bilibili import DynamicItem
from app.config import Config


LOGGER = logging.getLogger(__name__)


class NotifyError(RuntimeError):
    """Raised when a notification cannot be delivered."""


class Notifier(Protocol):
    def send(self, item: DynamicItem) -> None:
        ...


class CompositeNotifier:
    def __init__(self, notifiers: list[Notifier]) -> None:
        self.notifiers = notifiers

    def send(self, item: DynamicItem) -> None:
        failures: list[Exception] = []
        for notifier in self.notifiers:
            try:
                notifier.send(item)
            except Exception as exc:  # noqa: BLE001 - collect channel failures.
                failures.append(exc)
                LOGGER.warning("Notification channel failed: %s", exc)

        if failures and len(failures) == len(self.notifiers):
            raise NotifyError(str(failures[-1]))


class LogNotifier:
    def send(self, item: DynamicItem) -> None:
        LOGGER.info("[dry-run] %s", _format_plain_message(item))


class WeComWebhookNotifier:
    def __init__(self, webhook_url: str, timeout: int) -> None:
        self.webhook_url = webhook_url
        self.timeout = timeout

    def send(self, item: DynamicItem) -> None:
        payload = {
            "msgtype": "markdown",
            "markdown": {
                "content": _format_wecom_markdown(item),
            },
        }
        response = _post_json(self.webhook_url, payload, self.timeout)
        errcode = response.get("errcode")
        if errcode not in (0, None):
            raise NotifyError(f"WeCom webhook error: {response}")


class ServerChanNotifier:
    def __init__(self, sendkey: str, timeout: int) -> None:
        self.url = f"https://sctapi.ftqq.com/{sendkey}.send"
        self.timeout = timeout

    def send(self, item: DynamicItem) -> None:
        title = f"{item.author_name} 发布新动态"
        data = parse.urlencode(
            {
                "title": title,
                "desp": _format_serverchan_markdown(item),
            }
        ).encode("utf-8")
        req = request.Request(
            self.url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"},
            method="POST",
        )
        decoded = _open_json(req, self.timeout)
        code = decoded.get("code")
        if code not in (0, None):
            raise NotifyError(f"ServerChan error: {decoded}")


def create_notifier(config: Config) -> Notifier:
    notifiers: list[Notifier] = []
    if config.notify_dry_run:
        return CompositeNotifier([LogNotifier()])
    if config.wechat_webhook_url:
        notifiers.append(WeComWebhookNotifier(config.wechat_webhook_url, config.request_timeout))
    if config.serverchan_sendkey:
        notifiers.append(ServerChanNotifier(config.serverchan_sendkey, config.request_timeout))
    return CompositeNotifier(notifiers)


def _format_wecom_markdown(item: DynamicItem) -> str:
    summary = _quote_for_markdown(item.summary)
    return (
        f"**{_escape_markdown(item.author_name)} 发布新动态**\n"
        f">类型：{_escape_markdown(item.dynamic_type)}\n"
        f">时间：{_escape_markdown(item.published_text)}\n"
        f">标题：{_escape_markdown(item.title)}\n"
        f"{summary}\n"
        f"[查看动态]({item.url})"
    )[:3900]


def _format_serverchan_markdown(item: DynamicItem) -> str:
    return (
        f"### {item.author_name} 发布新动态\n\n"
        f"- 类型：{item.dynamic_type}\n"
        f"- 时间：{item.published_text}\n"
        f"- 标题：{item.title}\n\n"
        f"{item.summary}\n\n"
        f"[查看动态]({item.url})"
    )


def _format_plain_message(item: DynamicItem) -> str:
    return f"{item.author_name} {item.published_text} {item.title} {item.url}"


def _quote_for_markdown(value: str) -> str:
    if not value:
        return ""
    lines = [_escape_markdown(line) for line in value.splitlines()]
    return "\n".join(f">{line}" for line in lines)


def _escape_markdown(value: str) -> str:
    return value.replace("<", "&lt;").replace(">", "&gt;")


def _post_json(url: str, payload: dict, timeout: int) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    return _open_json(req, timeout)


def _open_json(req: request.Request, timeout: int) -> dict:
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
