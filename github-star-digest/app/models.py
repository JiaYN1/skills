from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class StarCandidate:
    full_name: str
    star_events: int
    unique_stargazers: int
    first_star_at: datetime | None = None
    last_star_at: datetime | None = None


@dataclass(frozen=True)
class RepoDigestItem:
    full_name: str
    unique_stargazers: int
    star_events: int
    total_stars: int
    language: str | None
    description: str | None
    html_url: str
    forks_count: int
    pushed_at: str | None
    fork: bool
    archived: bool
