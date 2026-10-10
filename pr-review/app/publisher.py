from __future__ import annotations

from urllib.parse import quote

import httpx

from .dedupe import comment_fingerprint_of, mark_already_reported, with_fingerprint_marker
from .diff_parser import ChangedFile
from .providers import (
    ProviderError,
    PullRequestRef,
    fetch_existing_comments,
    fetch_pull_request,
    parse_pr_url,
    resolve_token,
)
from .reviewer import format_review_body
from .schemas import PublishItemResult, ReviewComment


class PublishError(RuntimeError):
    pass


async def publish_comments(pr_url: str, comments: list[ReviewComment], token: str | None = None) -> list[PublishItemResult]:
    ref = parse_pr_url(pr_url)
    publishable = [comment for comment in comments if comment.publishable]
    skipped = [
        PublishItemResult(
            id=comment.id,
            file_path=comment.file_path,
            line=comment.line,
            status="skipped",
            error=comment.publish_warning or "该意见不可自动发布。",
        )
        for comment in comments
        if not comment.publishable
    ]

    if not publishable:
        return skipped

    resolved_token = resolve_token(ref.platform, token)
    if not resolved_token:
        raise PublishError("发布评论需要配置对应平台 token，或在请求中提供 scm_token。")

    publishable, duplicates = await _drop_already_published(ref, publishable, resolved_token)
    duplicate_results = [
        PublishItemResult(
            id=comment.id,
            file_path=comment.file_path,
            line=comment.line,
            status="skipped",
            error=comment.publish_warning or "PR 上已存在相同意见，不再重复发布。",
        )
        for comment in duplicates
    ]
    if not publishable:
        return duplicate_results + skipped

    if ref.platform == "github":
        results = await _publish_github_comments(pr_url, publishable, resolved_token)
    elif ref.platform == "gitcode":
        results = await _publish_gitcode_comments(pr_url, publishable, resolved_token)
    else:
        results = await _publish_gitlab_comments(pr_url, publishable, resolved_token)

    return results + duplicate_results + skipped


async def _drop_already_published(
    ref: PullRequestRef, comments: list[ReviewComment], token: str
) -> tuple[list[ReviewComment], list[ReviewComment]]:
    """Re-check the PR right before publishing, so a stale page cannot repost.

    Only exact matches are dropped here: comments the page marked as suspected
    duplicates stay, because selecting them is an explicit user decision.
    """

    try:
        existing = await fetch_existing_comments(ref, token=token)
    except (ProviderError, httpx.HTTPError):
        return list(comments), []

    updated, duplicates, _ = mark_already_reported(comments, existing, include_similar=False)
    fresh = [comment for comment in updated if not comment.already_posted]
    return fresh, duplicates


def _publish_body(comment: ReviewComment) -> str:
    """Body posted to the platform, always carrying the fingerprint marker."""

    body = (comment.body or "").strip()
    if not body:
        body = format_review_body(
            file_path=comment.file_path,
            line=comment.line,
            category=comment.category,
            message=comment.message,
            suggestion=comment.suggestion,
            language=comment.language,
            code_example=comment.code_example,
        )
    return with_fingerprint_marker(body, comment_fingerprint_of(comment))


async def _publish_github_comments(pr_url: str, comments: list[ReviewComment], token: str) -> list[PublishItemResult]:
    data = await fetch_pull_request(pr_url, token=token)
    if not data.head_sha:
        raise PublishError("无法识别 PR head commit，不能发布 GitHub review comment。")

    ref = data.ref
    url = f"https://api.github.com/repos/{ref.owner}/{ref.repo}/pulls/{ref.number}/reviews"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    payload = {
        "commit_id": data.head_sha,
        "event": "COMMENT",
        "comments": [
            {
                "path": comment.file_path,
                "line": comment.line,
                "side": comment.side,
                "body": _publish_body(comment),
            }
            for comment in comments
        ],
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        response = await client.post(url, headers=headers, json=payload)

    if response.status_code >= 400:
        error = _api_error(response, "GitHub 发布 review 失败")
        return [
            PublishItemResult(id=comment.id, file_path=comment.file_path, line=comment.line, status="error", error=error)
            for comment in comments
        ]

    body = response.json()
    review_url = body.get("html_url")
    return [
        PublishItemResult(
            id=comment.id,
            file_path=comment.file_path,
            line=comment.line,
            status="published",
            url=review_url,
        )
        for comment in comments
    ]


async def _publish_gitcode_comments(pr_url: str, comments: list[ReviewComment], token: str) -> list[PublishItemResult]:
    data = await fetch_pull_request(pr_url, token=token)
    ref = data.ref
    if not ref.owner or not ref.repo:
        raise PublishError("GitCode PR 链接需要是 https://gitcode.com/{owner}/{repo}/pull/{number} 格式。")

    owner = quote(ref.owner, safe="")
    repo = quote(ref.repo, safe="")
    number = quote(ref.number, safe="")
    url = f"https://api.gitcode.com/api/v5/repos/{owner}/{repo}/pulls/{number}/comments"
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    params = {"access_token": token}
    file_map = {file.new_path: file for file in data.files}

    results: list[PublishItemResult] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        for comment in comments:
            changed_file = file_map.get(comment.file_path)
            if not changed_file:
                results.append(
                    PublishItemResult(
                        id=comment.id,
                        file_path=comment.file_path,
                        line=comment.line,
                        status="error",
                        error="无法在当前 GitCode PR diff 中找到该文件，不能发布行级评论。",
                    )
                )
                continue

            position = _gitcode_comment_position(changed_file, comment.line)
            if position is None:
                results.append(
                    PublishItemResult(
                        id=comment.id,
                        file_path=comment.file_path,
                        line=comment.line,
                        status="error",
                        error="无法将该新行号映射到 GitCode diff position，不能发布行级评论。",
                    )
                )
                continue

            payload = {
                "body": _publish_body(comment),
                "path": comment.file_path,
                "position": position,
                "need_to_resolve": False,
            }
            response = await client.post(url, headers=headers, params=params, json=payload)
            if response.status_code >= 400:
                results.append(
                    PublishItemResult(
                        id=comment.id,
                        file_path=comment.file_path,
                        line=comment.line,
                        status="error",
                        error=_api_error(response, "GitCode 发布行级评论失败"),
                    )
                )
                continue

            body = _safe_json(response)
            results.append(
                PublishItemResult(
                    id=comment.id,
                    file_path=comment.file_path,
                    line=comment.line,
                    status="published",
                    url=body.get("html_url") or body.get("url"),
                )
            )

    return results


async def _publish_gitlab_comments(pr_url: str, comments: list[ReviewComment], token: str) -> list[PublishItemResult]:
    data = await fetch_pull_request(pr_url, token=token)
    ref = data.ref
    api_base = f"{ref.scheme}://{ref.host}/api/v4"
    project = quote(ref.project_path, safe="")
    mr_url = f"{api_base}/projects/{project}/merge_requests/{ref.number}"
    headers = {"PRIVATE-TOKEN": token}
    version = await _latest_gitlab_version(mr_url, headers, data)

    results: list[PublishItemResult] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        for comment in comments:
            payload = {
                "body": _publish_body(comment),
                "position": {
                    "position_type": "text",
                    "base_sha": version["base_sha"],
                    "start_sha": version["start_sha"],
                    "head_sha": version["head_sha"],
                    "old_path": comment.file_path,
                    "new_path": comment.file_path,
                    "new_line": comment.line,
                },
            }
            response = await client.post(f"{mr_url}/discussions", headers=headers, json=payload)
            if response.status_code >= 400:
                results.append(
                    PublishItemResult(
                        id=comment.id,
                        file_path=comment.file_path,
                        line=comment.line,
                        status="error",
                        error=_api_error(response, "GitLab/GitCode 发布讨论失败"),
                    )
                )
                continue

            body = response.json()
            results.append(
                PublishItemResult(
                    id=comment.id,
                    file_path=comment.file_path,
                    line=comment.line,
                    status="published",
                    url=body.get("web_url"),
                )
            )

    return results


async def _latest_gitlab_version(mr_url: str, headers: dict[str, str], data) -> dict[str, str]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        response = await client.get(f"{mr_url}/versions", headers=headers)

    version: dict | None = None
    if response.status_code < 400:
        versions = response.json()
        if isinstance(versions, list) and versions:
            version = versions[0]

    base_sha = (version or {}).get("base_commit_sha") or data.base_sha
    start_sha = (version or {}).get("start_commit_sha") or data.start_sha or base_sha
    head_sha = (version or {}).get("head_commit_sha") or data.head_sha

    if not base_sha or not start_sha or not head_sha:
        raise PublishError("无法识别 GitLab/GitCode diff_refs，不能发布行级评论。")

    return {"base_sha": base_sha, "start_sha": start_sha, "head_sha": head_sha}


def _gitcode_comment_position(file: ChangedFile, new_line: int) -> int | None:
    """Return GitCode's position: the absolute line number in the new file.

    GitCode's API calls this field ``position``, but it expects the new-file
    line number rather than a line offset within the unified diff. Mapping it
    to the diff offset makes comments drift upward in large or multi-hunk
    changes.
    """
    return new_line if new_line in file.commentable_new_lines else None


def _safe_json(response: httpx.Response) -> dict:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _api_error(response: httpx.Response, prefix: str) -> str:
    return f"{prefix}: HTTP {response.status_code} {response.text[:400].replace(chr(10), ' ')}"
