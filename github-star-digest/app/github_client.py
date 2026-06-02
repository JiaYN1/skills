from __future__ import annotations

import json
import logging
from urllib import error, request

from app.models import RepoDigestItem, StarCandidate


LOGGER = logging.getLogger(__name__)
DEFAULT_API_URL = "https://api.github.com"


class GitHubError(RuntimeError):
    """Raised when GitHub repository metadata cannot be fetched."""


class GitHubRateLimitError(GitHubError):
    """Raised when GitHub rejects metadata requests due to rate limits."""


class GitHubClient:
    def __init__(
        self,
        token: str | None,
        timeout: int,
        api_url: str = DEFAULT_API_URL,
    ) -> None:
        self.token = token
        self.timeout = timeout
        self.api_url = api_url.rstrip("/")

    def enrich(
        self,
        candidates: list[StarCandidate],
        limit: int,
        exclude_forks: bool = True,
        exclude_archived: bool = True,
    ) -> list[RepoDigestItem]:
        items: list[RepoDigestItem] = []
        for candidate in candidates:
            try:
                item = self.fetch_repo(candidate)
            except GitHubRateLimitError:
                raise
            except GitHubError as exc:
                LOGGER.warning("Skipping %s: %s", candidate.full_name, exc)
                continue
            if exclude_forks and item_is_fork(item):
                LOGGER.debug("Skipping fork repo: %s", item.full_name)
                continue
            if exclude_archived and item_is_archived(item):
                LOGGER.debug("Skipping archived repo: %s", item.full_name)
                continue
            items.append(item)
            if len(items) >= limit:
                break
        return items

    def fetch_repo(self, candidate: StarCandidate) -> RepoDigestItem:
        if "/" not in candidate.full_name:
            raise GitHubError("invalid repo name")
        data = self._get_json(f"/repos/{candidate.full_name}")
        if not isinstance(data, dict):
            raise GitHubError("unexpected GitHub response")
        if data.get("disabled"):
            raise GitHubError("repository is disabled")
        if data.get("private"):
            raise GitHubError("repository is private")
        return RepoDigestItem(
            full_name=str(data.get("full_name") or candidate.full_name),
            unique_stargazers=candidate.unique_stargazers,
            star_events=candidate.star_events,
            total_stars=int(data.get("stargazers_count") or 0),
            language=data.get("language"),
            description=data.get("description"),
            html_url=str(data.get("html_url") or f"https://github.com/{candidate.full_name}"),
            forks_count=int(data.get("forks_count") or 0),
            pushed_at=data.get("pushed_at"),
            fork=bool(data.get("fork")),
            archived=bool(data.get("archived")),
        )

    def _get_json(self, path: str) -> object:
        req = request.Request(
            f"{self.api_url}{path}",
            headers=self._headers(),
            method="GET",
        )
        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read()
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            if exc.code == 404:
                raise GitHubError("repository not found or unavailable") from exc
            if exc.code == 403:
                raise GitHubRateLimitError(f"rate limited or forbidden: {detail}") from exc
            raise GitHubError(f"HTTP {exc.code}: {detail}") from exc
        except error.URLError as exc:
            raise GitHubError(f"request failed: {exc}") from exc

        try:
            return json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise GitHubError("invalid JSON response") from exc

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "github-star-digest",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers


def item_is_fork(item: RepoDigestItem) -> bool:
    return item.fork


def item_is_archived(item: RepoDigestItem) -> bool:
    return item.archived
