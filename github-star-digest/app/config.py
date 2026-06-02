from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


MIB = 1024**2
GIB = 1024**3
TIB = 1024**4


class ConfigError(RuntimeError):
    """Raised when configuration is missing or invalid."""


@dataclass(frozen=True)
class Config:
    archive_base_url: str
    archive_max_download_bytes: int
    archive_monthly_download_budget_bytes: int
    state_file: Path
    timezone_name: str
    run_time: str
    top_limit: int
    candidate_limit: int
    min_unique_stargazers: int
    request_timeout: int
    github_token: str | None
    wecom_webhook_url: str | None
    notify_dry_run: bool
    run_on_start: bool
    exclude_forks: bool
    exclude_archived: bool
    log_level: str

    @property
    def timezone(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ConfigError(f"TIMEZONE is invalid: {self.timezone_name}") from exc

    @classmethod
    def from_env(cls, require_notifier: bool = True) -> "Config":
        top_limit = _int_env("TOP_LIMIT", 10, minimum=1, maximum=50)
        candidate_limit = _int_env("CANDIDATE_LIMIT", max(50, top_limit * 5), minimum=top_limit, maximum=200)

        webhook_url = _empty_to_none(_env("WECOM_WEBHOOK_URL") or _env("WECHAT_WEBHOOK_URL"))
        notify_dry_run = _bool_env("NOTIFY_DRY_RUN", False)
        if require_notifier and not notify_dry_run and not webhook_url:
            raise ConfigError("WECOM_WEBHOOK_URL is required unless NOTIFY_DRY_RUN=true")
        if webhook_url and not webhook_url.startswith(("http://", "https://")):
            raise ConfigError("WECOM_WEBHOOK_URL must be a full http(s) URL")

        run_time = _env("RUN_TIME") or "09:00"
        _parse_run_time(run_time)

        timezone_name = _env("TIMEZONE") or "Asia/Shanghai"
        try:
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ConfigError(f"TIMEZONE is invalid: {timezone_name}") from exc

        return cls(
            archive_base_url=(_env("ARCHIVE_BASE_URL") or "https://data.gharchive.org").rstrip("/"),
            archive_max_download_bytes=_bytes_env("ARCHIVE_MAX_DOWNLOAD_BYTES", 6 * GIB, minimum=100 * MIB),
            archive_monthly_download_budget_bytes=_bytes_env(
                "ARCHIVE_MONTHLY_DOWNLOAD_BUDGET_BYTES",
                180 * GIB,
                minimum=1 * GIB,
            ),
            state_file=Path(_env("STATE_FILE") or "/data/state.json"),
            timezone_name=timezone_name,
            run_time=run_time,
            top_limit=top_limit,
            candidate_limit=candidate_limit,
            min_unique_stargazers=_int_env("MIN_UNIQUE_STARGAZERS", 5, minimum=1, maximum=10000),
            request_timeout=_int_env("REQUEST_TIMEOUT", 15, minimum=3, maximum=120),
            github_token=_empty_to_none(_env("GITHUB_TOKEN")),
            wecom_webhook_url=webhook_url,
            notify_dry_run=notify_dry_run,
            run_on_start=_bool_env("RUN_ON_START", False),
            exclude_forks=_bool_env("EXCLUDE_FORKS", True),
            exclude_archived=_bool_env("EXCLUDE_ARCHIVED", True),
            log_level=(_env("LOG_LEVEL") or "INFO").upper(),
        )


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _empty_to_none(value: str) -> str | None:
    return value or None


def _int_env(name: str, default: int, minimum: int, maximum: int | None = None) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise ConfigError(f"{name} must be <= {maximum}")
    return value


def _bytes_env(name: str, default: int, minimum: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    value = _parse_bytes(raw)
    if value < minimum:
        raise ConfigError(f"{name} must be >= {minimum} bytes")
    return value


def _parse_bytes(raw: str) -> int:
    normalized = raw.strip().lower().replace(" ", "")
    units = {
        "tib": TIB,
        "tb": 1000**4,
        "gib": GIB,
        "gb": 1000**3,
        "mib": 1024**2,
        "mb": 1000**2,
        "b": 1,
    }
    for suffix, multiplier in units.items():
        if normalized.endswith(suffix):
            number = normalized[: -len(suffix)]
            break
    else:
        number = normalized
        multiplier = 1

    try:
        return int(float(number) * multiplier)
    except ValueError as exc:
        raise ConfigError(f"invalid byte value: {raw}") from exc


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


def _parse_run_time(value: str) -> tuple[int, int]:
    parts = value.split(":")
    if len(parts) != 2:
        raise ConfigError("RUN_TIME must use HH:MM format")
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError as exc:
        raise ConfigError("RUN_TIME must use HH:MM format") from exc
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ConfigError("RUN_TIME must be a valid time")
    return hour, minute
