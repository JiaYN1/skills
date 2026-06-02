from __future__ import annotations

from datetime import date, datetime, timezone
import logging

from app.config import Config
from app.gharchive_source import GHArchiveSource
from app.github_client import GitHubClient
from app.notifier import create_notifier, format_digest
from app.state import StateStore
from app.time_window import build_window, yesterday_window


LOGGER = logging.getLogger(__name__)


class DigestError(RuntimeError):
    """Raised when a digest run cannot complete."""


def run_digest(config: Config, target_date: date | None = None, force: bool = False) -> bool:
    state = StateStore(config.state_file)
    state.load()

    window = build_window(target_date, config.timezone) if target_date else yesterday_window(
        datetime.now(timezone.utc),
        config.timezone,
    )
    if state.has_sent(window.target_date_iso) and not force:
        LOGGER.info("Digest for %s was already sent; skipping", window.target_date_iso)
        return False

    source = GHArchiveSource(
        base_url=config.archive_base_url,
        timeout=config.request_timeout,
        max_download_bytes=config.archive_max_download_bytes,
    )
    LOGGER.info(
        "Estimating GH Archive download for %s, suffix=%s..%s",
        window.target_date_iso,
        window.start_suffix,
        window.end_suffix,
    )
    estimated_bytes = source.estimate_bytes(window)
    if estimated_bytes > config.archive_max_download_bytes:
        raise DigestError(
            f"GH Archive files estimate {_format_bytes(estimated_bytes)}, "
            f"above ARCHIVE_MAX_DOWNLOAD_BYTES={_format_bytes(config.archive_max_download_bytes)}"
        )

    state.assert_month_budget(window.month_key, estimated_bytes, config.archive_monthly_download_budget_bytes)
    LOGGER.info("Fetching candidates from GH Archive; estimated_download=%s", _format_bytes(estimated_bytes))
    candidates, downloaded_bytes = source.fetch_candidates(
        window,
        candidate_limit=config.candidate_limit,
        min_unique_stargazers=config.min_unique_stargazers,
    )
    state.record_download(window.month_key, estimated_bytes=estimated_bytes, downloaded_bytes=downloaded_bytes)
    usage_after = state.month_usage(window.month_key)

    LOGGER.info("Fetched %s candidate repo(s); enriching via GitHub API", len(candidates))
    github = GitHubClient(config.github_token, timeout=config.request_timeout)
    items = github.enrich(
        candidates,
        limit=config.top_limit,
        exclude_forks=config.exclude_forks,
        exclude_archived=config.exclude_archived,
    )

    title = f"GitHub Star Digest {window.target_date_iso}"
    content = format_digest(
        window=window,
        items=items,
        estimated_bytes=estimated_bytes,
        downloaded_bytes=downloaded_bytes,
        monthly_downloaded_after=usage_after.downloaded_bytes,
        monthly_budget=config.archive_monthly_download_budget_bytes,
    )
    notifier = create_notifier(config.wecom_webhook_url, config.request_timeout, config.notify_dry_run)
    notifier.send(title, content)
    state.mark_sent(window.target_date_iso)
    LOGGER.info("Digest sent for %s with %s repo(s)", window.target_date_iso, len(items))
    return True


def estimate_digest(config: Config, target_date: date | None = None) -> tuple[str, int, int, int]:
    state = StateStore(config.state_file)
    state.load()
    window = build_window(target_date, config.timezone) if target_date else yesterday_window(
        datetime.now(timezone.utc),
        config.timezone,
    )
    source = GHArchiveSource(
        base_url=config.archive_base_url,
        timeout=config.request_timeout,
        max_download_bytes=config.archive_max_download_bytes,
    )
    estimated_bytes = source.estimate_bytes(window)
    usage = state.month_usage(window.month_key)
    return (
        window.target_date_iso,
        estimated_bytes,
        usage.downloaded_bytes,
        config.archive_monthly_download_budget_bytes,
    )


def _format_bytes(value: int) -> str:
    gib = 1024**3
    tib = 1024**4
    if value >= tib:
        return f"{value / tib:.2f} TiB"
    return f"{value / gib:.2f} GiB"
