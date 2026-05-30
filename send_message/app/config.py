from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from app.bilibili import DEFAULT_API_URL


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class Config:
    uids: list[str]
    poll_interval: int
    request_timeout: int
    state_file: Path
    bili_cookie: str | None
    wechat_webhook_url: str | None
    serverchan_sendkey: str | None
    notify_existing_on_first_run: bool
    max_seen_per_uid: int
    notify_dry_run: bool
    log_level: str
    bili_api_url: str

    @classmethod
    def from_env(cls) -> "Config":
        uids = _split_csv(_env("BILI_UIDS") or _env("BILI_UID"))
        if not uids:
            raise ConfigError("BILI_UIDS is required, for example: BILI_UIDS=123456,987654")

        poll_interval = _int_env("POLL_INTERVAL", 30, minimum=10)
        request_timeout = _int_env("REQUEST_TIMEOUT", 12, minimum=3)
        max_seen_per_uid = _int_env("MAX_SEEN_PER_UID", 500, minimum=50)
        state_file = Path(_env("STATE_FILE") or "/data/state.json")

        wechat_webhook_url = _empty_to_none(_env("WECHAT_WEBHOOK_URL"))
        serverchan_sendkey = _empty_to_none(_env("SERVERCHAN_SENDKEY"))
        notify_dry_run = _bool_env("NOTIFY_DRY_RUN", False)

        if not notify_dry_run and not wechat_webhook_url and not serverchan_sendkey:
            raise ConfigError(
                "Configure at least one notification channel: WECHAT_WEBHOOK_URL or SERVERCHAN_SENDKEY"
            )

        if wechat_webhook_url and not wechat_webhook_url.startswith(("http://", "https://")):
            raise ConfigError("WECHAT_WEBHOOK_URL must be a full http(s) URL")

        return cls(
            uids=uids,
            poll_interval=poll_interval,
            request_timeout=request_timeout,
            state_file=state_file,
            bili_cookie=_empty_to_none(_env("BILI_COOKIE")),
            wechat_webhook_url=wechat_webhook_url,
            serverchan_sendkey=serverchan_sendkey,
            notify_existing_on_first_run=_bool_env("NOTIFY_EXISTING_ON_FIRST_RUN", False),
            max_seen_per_uid=max_seen_per_uid,
            notify_dry_run=notify_dry_run,
            log_level=(_env("LOG_LEVEL") or "INFO").upper(),
            bili_api_url=_env("BILI_API_URL") or DEFAULT_API_URL,
        )


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _empty_to_none(value: str) -> str | None:
    return value or None


def _split_csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _int_env(name: str, default: int, minimum: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}")
    return value


def _bool_env(name: str, default: bool) -> bool:
    raw = _env(name)
    if not raw:
        return default
    normalized = raw.lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be a boolean value")

