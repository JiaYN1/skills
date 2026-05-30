from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import logging
from typing import Any
from urllib import error, parse, request


LOGGER = logging.getLogger(__name__)


DEFAULT_API_URL = "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space"


class BilibiliError(RuntimeError):
    """Raised when Bilibili returns an unusable response."""


@dataclass(frozen=True)
class DynamicItem:
    uid: str
    dynamic_id: str
    author_name: str
    published_at: datetime | None
    dynamic_type: str
    title: str
    summary: str
    url: str

    @property
    def published_text(self) -> str:
        if self.published_at is None:
            return "unknown time"
        return self.published_at.strftime("%Y-%m-%d %H:%M:%S")


class BilibiliClient:
    def __init__(
        self,
        timeout: int,
        cookie: str | None = None,
        api_url: str = DEFAULT_API_URL,
    ) -> None:
        self.timeout = timeout
        self.cookie = cookie
        self.api_url = api_url

    def fetch_latest(self, uid: str) -> list[DynamicItem]:
        query = parse.urlencode(
            {
                "host_mid": uid,
                "timezone_offset": "-480",
                "features": "itemOpusStyle,listOnlyfans,opusBigCover,onlyfansVote,decorationCard,forwardListHidden,ugcDelete,onlyfansQaCard",
            }
        )
        payload = self._get_json(f"{self.api_url}?{query}", uid)
        code = payload.get("code")
        if code != 0:
            message = payload.get("message") or payload.get("msg") or "unknown error"
            raise BilibiliError(f"Bilibili API error for uid {uid}: code={code}, message={message}")

        items = (((payload.get("data") or {}).get("items")) or [])
        parsed_items: list[DynamicItem] = []
        for raw in items:
            if not isinstance(raw, dict):
                continue
            item = parse_dynamic_item(uid, raw)
            if item is not None:
                parsed_items.append(item)
        return parsed_items

    def _get_json(self, url: str, uid: str) -> dict[str, Any]:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": f"https://space.bilibili.com/{uid}/dynamic",
            "Origin": "https://space.bilibili.com",
        }
        if self.cookie:
            headers["Cookie"] = self.cookie

        req = request.Request(url, headers=headers, method="GET")
        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read()
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise BilibiliError(f"HTTP {exc.code} from Bilibili for uid {uid}: {detail}") from exc
        except error.URLError as exc:
            raise BilibiliError(f"Unable to request Bilibili for uid {uid}: {exc}") from exc

        try:
            decoded = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            snippet = body[:300].decode("utf-8", errors="replace")
            raise BilibiliError(f"Invalid JSON from Bilibili for uid {uid}: {snippet}") from exc

        if not isinstance(decoded, dict):
            raise BilibiliError(f"Unexpected Bilibili response type for uid {uid}: {type(decoded).__name__}")
        return decoded


def parse_dynamic_item(uid: str, raw: dict[str, Any]) -> DynamicItem | None:
    dynamic_id = str(raw.get("id_str") or raw.get("id") or "").strip()
    if not dynamic_id:
        return None

    modules = _dict(raw.get("modules"))
    author_module = _dict(modules.get("module_author"))
    dynamic_module = _dict(modules.get("module_dynamic"))
    major = _dict(dynamic_module.get("major"))

    author_name = str(author_module.get("name") or uid)
    published_at = _parse_timestamp(author_module.get("pub_ts"))
    dynamic_type = str(raw.get("type") or major.get("type") or "dynamic")

    desc_text = _extract_desc_text(dynamic_module.get("desc"))
    title, summary, preferred_url = _extract_major_text(major)

    if not summary:
        summary = desc_text
    elif desc_text and desc_text not in summary:
        summary = f"{desc_text}\n{summary}"

    if not title:
        title = _first_line(summary) or _type_label(dynamic_type)

    url = _normalize_url(
        preferred_url
        or _dict(raw.get("basic")).get("jump_url")
        or f"https://t.bilibili.com/{dynamic_id}"
    )

    return DynamicItem(
        uid=uid,
        dynamic_id=dynamic_id,
        author_name=author_name,
        published_at=published_at,
        dynamic_type=dynamic_type,
        title=_truncate(_clean_text(title), 80),
        summary=_truncate(_clean_text(summary or title), 800),
        url=url,
    )


def _extract_major_text(major: dict[str, Any]) -> tuple[str, str, str]:
    if not major:
        return "", "", ""

    major_type = str(major.get("type") or "")
    archive = _dict(major.get("archive"))
    if archive:
        return (
            str(archive.get("title") or ""),
            str(archive.get("desc") or archive.get("title") or ""),
            str(archive.get("jump_url") or ""),
        )

    article = _dict(major.get("article"))
    if article:
        return (
            str(article.get("title") or ""),
            str(article.get("desc") or article.get("summary") or ""),
            str(article.get("jump_url") or ""),
        )

    opus = _dict(major.get("opus"))
    if opus:
        summary = _dict(opus.get("summary"))
        title = str(opus.get("title") or "")
        text = str(summary.get("text") or opus.get("desc") or "")
        return (title or _first_line(text), text, str(opus.get("jump_url") or ""))

    common = _dict(major.get("common"))
    if common:
        return (
            str(common.get("title") or ""),
            str(common.get("desc") or common.get("title") or ""),
            str(common.get("jump_url") or ""),
        )

    pgc = _dict(major.get("pgc"))
    if pgc:
        return (
            str(pgc.get("title") or ""),
            str(pgc.get("desc") or pgc.get("subtitle") or ""),
            str(pgc.get("jump_url") or ""),
        )

    live_rcmd = _dict(major.get("live_rcmd"))
    if live_rcmd:
        title, summary, url = _extract_live_rcmd(live_rcmd)
        return title, summary, url

    draw = _dict(major.get("draw"))
    if draw:
        count = len(draw.get("items") or [])
        summary = f"发布了 {count} 张图片" if count else "发布了图片动态"
        return "", summary, ""

    LOGGER.debug("Unhandled Bilibili major type: %s", major_type)
    return "", "", ""


def _extract_live_rcmd(live_rcmd: dict[str, Any]) -> tuple[str, str, str]:
    content = live_rcmd.get("content")
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except json.JSONDecodeError:
            content = {}
    content_dict = _dict(content)
    live_info = _dict(content_dict.get("live_play_info"))
    title = str(live_info.get("title") or content_dict.get("title") or "直播动态")
    watched = str(_dict(live_info.get("watched_show")).get("text") or "")
    area = str(live_info.get("area_name") or "")
    summary = " ".join(part for part in [area, watched] if part)
    url = str(live_info.get("link") or live_info.get("jump_url") or content_dict.get("jump_url") or "")
    return title, summary, url


def _extract_desc_text(desc: Any) -> str:
    desc_dict = _dict(desc)
    text = str(desc_dict.get("text") or "")
    if text:
        return text

    nodes = desc_dict.get("rich_text_nodes") or []
    if isinstance(nodes, list):
        return "".join(str(_dict(node).get("text") or "") for node in nodes)
    return ""


def _parse_timestamp(value: Any) -> datetime | None:
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone(timedelta(hours=8)))


def _normalize_url(url: Any) -> str:
    value = str(url or "").strip()
    if value.startswith("//"):
        return f"https:{value}"
    if value.startswith("/"):
        return f"https://www.bilibili.com{value}"
    return value


def _type_label(dynamic_type: str) -> str:
    labels = {
        "DYNAMIC_TYPE_AV": "投稿了视频",
        "DYNAMIC_TYPE_DRAW": "发布了图片动态",
        "DYNAMIC_TYPE_FORWARD": "转发了动态",
        "DYNAMIC_TYPE_WORD": "发布了文字动态",
        "DYNAMIC_TYPE_ARTICLE": "发布了专栏",
        "DYNAMIC_TYPE_LIVE_RCMD": "发布了直播动态",
    }
    return labels.get(dynamic_type, "发布了新动态")


def _clean_text(value: str) -> str:
    return "\n".join(line.strip() for line in value.replace("\r", "\n").split("\n") if line.strip())


def _first_line(value: str) -> str:
    cleaned = _clean_text(value)
    if not cleaned:
        return ""
    return cleaned.split("\n", 1)[0]


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "..."


def _dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}
