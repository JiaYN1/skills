from __future__ import annotations

from urllib.parse import quote

import httpx

from .providers import ProviderError, fetch_pull_request, parse_pr_url, resolve_token
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

    if ref.platform == "github":
        results = await _publish_github_comments(pr_url, publishable, resolved_token)
    elif ref.platform == "gitcode":
        raise PublishError("GitCode v5 行级评论发布接口尚未适配；当前只支持生成 review 结果。")
    else:
        results = await _publish_gitlab_comments(pr_url, publishable, resolved_token)

    return results + skipped


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
                "body": comment.body,
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
                "body": comment.body,
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


def _api_error(response: httpx.Response, prefix: str) -> str:
    return f"{prefix}: HTTP {response.status_code} {response.text[:400].replace(chr(10), ' ')}"
