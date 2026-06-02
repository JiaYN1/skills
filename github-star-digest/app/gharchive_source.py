from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import gzip
import io
import json
import logging
from urllib import error, request

from app.models import StarCandidate
from app.time_window import TimeWindow


LOGGER = logging.getLogger(__name__)


class GHArchiveError(RuntimeError):
    """Raised when GH Archive data cannot be downloaded or parsed."""


class DownloadLimitExceeded(GHArchiveError):
    """Raised when archive downloads exceed the configured byte limit."""


@dataclass
class _RepoStats:
    star_events: int = 0
    actor_ids: set[str] = field(default_factory=set)
    first_star_at: datetime | None = None
    last_star_at: datetime | None = None


class GHArchiveSource:
    def __init__(self, base_url: str, timeout: int, max_download_bytes: int) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_download_bytes = max_download_bytes

    def estimate_bytes(self, window: TimeWindow) -> int:
        total = 0
        for hour in _utc_hours(window):
            url = self._hour_url(hour)
            total += self._content_length(url)
        return total

    def fetch_candidates(
        self,
        window: TimeWindow,
        candidate_limit: int,
        min_unique_stargazers: int,
    ) -> tuple[list[StarCandidate], int]:
        stats: dict[str, _RepoStats] = {}
        downloaded_bytes = 0
        for hour in _utc_hours(window):
            remaining = self.max_download_bytes - downloaded_bytes
            if remaining <= 0:
                raise DownloadLimitExceeded(
                    f"archive download limit exceeded: max={self.max_download_bytes} bytes"
                )
            url = self._hour_url(hour)
            LOGGER.info("Downloading %s", url)
            bytes_read = self._download_hour(url, window, stats, remaining)
            downloaded_bytes += bytes_read
        return _rank_stats(stats, candidate_limit, min_unique_stargazers), downloaded_bytes

    def _hour_url(self, hour: datetime) -> str:
        return f"{self.base_url}/{hour:%Y-%m-%d}-{hour.hour}.json.gz"

    def _content_length(self, url: str) -> int:
        req = request.Request(url, method="HEAD", headers={"User-Agent": "github-star-digest"})
        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
                raw_length = resp.headers.get("Content-Length")
        except error.HTTPError as exc:
            raise GHArchiveError(f"HEAD {url} failed with HTTP {exc.code}") from exc
        except error.URLError as exc:
            raise GHArchiveError(f"HEAD {url} failed: {exc}") from exc
        if not raw_length:
            raise GHArchiveError(f"HEAD {url} did not return Content-Length")
        try:
            return int(raw_length)
        except ValueError as exc:
            raise GHArchiveError(f"HEAD {url} returned invalid Content-Length: {raw_length}") from exc

    def _download_hour(
        self,
        url: str,
        window: TimeWindow,
        stats: dict[str, _RepoStats],
        max_bytes: int,
    ) -> int:
        req = request.Request(url, headers={"User-Agent": "github-star-digest"}, method="GET")
        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
                counting = _CountingReader(resp, max_bytes)
                with gzip.GzipFile(fileobj=counting) as gz:
                    for line in io.TextIOWrapper(gz, encoding="utf-8"):
                        _record_line(stats, line, window)
                return counting.bytes_read
        except DownloadLimitExceeded:
            raise
        except error.HTTPError as exc:
            raise GHArchiveError(f"GET {url} failed with HTTP {exc.code}") from exc
        except error.URLError as exc:
            raise GHArchiveError(f"GET {url} failed: {exc}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise GHArchiveError(f"failed to parse {url}: {exc}") from exc


class _CountingReader:
    def __init__(self, raw, max_bytes: int) -> None:
        self.raw = raw
        self.max_bytes = max_bytes
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        data = self.raw.read(size)
        self.bytes_read += len(data)
        if self.bytes_read > self.max_bytes:
            raise DownloadLimitExceeded(
                f"archive download limit exceeded: read={self.bytes_read}, max={self.max_bytes}"
            )
        return data


def _utc_hours(window: TimeWindow) -> list[datetime]:
    hours: list[datetime] = []
    current = window.start_utc.replace(minute=0, second=0, microsecond=0)
    while current < window.end_utc:
        hours.append(current)
        current += timedelta(hours=1)
    return hours


def _record_line(stats: dict[str, _RepoStats], line: str, window: TimeWindow) -> None:
    if not line.strip():
        return
    event = json.loads(line)
    _record_event(stats, event, window)


def _record_event(stats: dict[str, _RepoStats], event: dict, window: TimeWindow) -> None:
    if event.get("type") != "WatchEvent":
        return
    created_at = _parse_created_at(event.get("created_at"))
    if created_at is None or created_at < window.start_utc or created_at >= window.end_utc:
        return
    repo = event.get("repo") or {}
    actor = event.get("actor") or {}
    full_name = repo.get("name")
    actor_id = actor.get("id")
    if not full_name or actor_id is None:
        return

    stat = stats.setdefault(str(full_name), _RepoStats())
    stat.star_events += 1
    stat.actor_ids.add(str(actor_id))
    if stat.first_star_at is None or created_at < stat.first_star_at:
        stat.first_star_at = created_at
    if stat.last_star_at is None or created_at > stat.last_star_at:
        stat.last_star_at = created_at


def _rank_stats(
    stats: dict[str, _RepoStats],
    candidate_limit: int,
    min_unique_stargazers: int,
) -> list[StarCandidate]:
    candidates = [
        StarCandidate(
            full_name=full_name,
            star_events=stat.star_events,
            unique_stargazers=len(stat.actor_ids),
            first_star_at=stat.first_star_at,
            last_star_at=stat.last_star_at,
        )
        for full_name, stat in stats.items()
        if len(stat.actor_ids) >= min_unique_stargazers
    ]
    candidates.sort(key=lambda item: (item.unique_stargazers, item.star_events), reverse=True)
    return candidates[:candidate_limit]


def _parse_created_at(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def aggregate_events(
    events: Iterable[dict],
    window: TimeWindow,
    candidate_limit: int,
    min_unique_stargazers: int,
) -> list[StarCandidate]:
    stats: dict[str, _RepoStats] = {}
    for event in events:
        _record_event(stats, event, window)
    return _rank_stats(stats, candidate_limit, min_unique_stargazers)
