from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any
from urllib.parse import quote, unquote, urlparse

import httpx

from .diff_parser import ChangedFile, parse_patch, parse_unified_diff


class ProviderError(RuntimeError):
    pass


@dataclass(slots=True)
class PullRequestRef:
    platform: str
    scheme: str
    host: str
    project_path: str
    number: str
    web_url: str
    owner: str = ""
    repo: str = ""


@dataclass(slots=True)
class PullRequestData:
    ref: PullRequestRef
    title: str | None
    files: list[ChangedFile]
    diff: str
    head_sha: str | None = None
    base_sha: str | None = None
    start_sha: str | None = None


def parse_pr_url(pr_url: str) -> PullRequestRef:
    parsed = urlparse(pr_url)
    if not parsed.scheme or not parsed.netloc:
        raise ProviderError("PR 链接格式不正确。")

    host = parsed.netloc.lower()
    parts = [unquote(part) for part in parsed.path.strip("/").split("/") if part]

    if host == "github.com" and len(parts) >= 4 and parts[2] == "pull":
        owner, repo, _, number = parts[:4]
        return PullRequestRef(
            platform="github",
            scheme=parsed.scheme,
            host=host,
            project_path=f"{owner}/{repo}",
            owner=owner,
            repo=repo,
            number=number,
            web_url=_without_query(pr_url),
        )

    if host in {"gitcode.com", "git.code.tencent.com"}:
        ref = _parse_gitcode_url(parsed.scheme, host, parts, pr_url)
        if ref:
            return ref

    gitlab_ref = _parse_gitlab_url(parsed.scheme, host, parts, pr_url)
    if gitlab_ref:
        return gitlab_ref

    raise ProviderError("暂不支持该 PR 链接。支持 GitHub pull、GitLab merge request 和 GitCode PR/MR。")


async def fetch_pull_request(pr_url: str, token: str | None = None) -> PullRequestData:
    ref = parse_pr_url(pr_url)
    if ref.platform == "github":
        return await _fetch_github_pull(ref, token)
    if ref.platform == "gitcode":
        return await _fetch_gitcode_pull(ref, token)
    return await _fetch_gitlab_merge_request(ref, token)


def resolve_token(platform: str, explicit_token: str | None = None) -> str | None:
    if explicit_token:
        return explicit_token
    if platform == "github":
        return os.getenv("GITHUB_TOKEN")
    if platform == "gitcode":
        return os.getenv("GITCODE_TOKEN") or os.getenv("GITLAB_TOKEN")
    return os.getenv("GITLAB_TOKEN")


def _parse_gitlab_url(scheme: str, host: str, parts: list[str], pr_url: str) -> PullRequestRef | None:
    if "-" not in parts:
        return None
    marker_index = parts.index("-")
    if len(parts) <= marker_index + 2 or parts[marker_index + 1] != "merge_requests":
        return None

    project_path = "/".join(parts[:marker_index])
    number = parts[marker_index + 2]
    if not project_path or not number:
        return None

    return PullRequestRef(
        platform="gitlab",
        scheme=scheme,
        host=host,
        project_path=project_path,
        number=number,
        web_url=_without_query(pr_url),
    )


def _parse_gitcode_url(scheme: str, host: str, parts: list[str], pr_url: str) -> PullRequestRef | None:
    for marker in ("pull", "merge_requests"):
        if marker not in parts:
            continue
        marker_index = parts.index(marker)
        if len(parts) <= marker_index + 1:
            continue
        project_path = "/".join(parts[:marker_index])
        number = parts[marker_index + 1]
        if project_path and number:
            owner = parts[0] if len(parts[:marker_index]) == 2 else ""
            repo = parts[1] if len(parts[:marker_index]) == 2 else ""
            return PullRequestRef(
                platform="gitcode",
                scheme=scheme,
                host=host,
                project_path=project_path,
                owner=owner,
                repo=repo,
                number=number,
                web_url=_without_query(pr_url),
            )
    return None


async def _fetch_github_pull(ref: PullRequestRef, token: str | None) -> PullRequestData:
    api_url = f"https://api.github.com/repos/{ref.owner}/{ref.repo}/pulls/{ref.number}"
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    resolved_token = resolve_token(ref.platform, token)
    if resolved_token:
        headers["Authorization"] = f"Bearer {resolved_token}"

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        response = await client.get(api_url, headers=headers)
        if response.status_code >= 400:
            if response.status_code in {401, 403, 404} and not resolved_token:
                return await _fetch_github_raw_diff(ref, client)
            _raise_provider_error(response, "获取 GitHub PR 元数据失败")

        pull = response.json()
        files = await _fetch_github_files(client, api_url, headers)
        diff = "\n".join(_github_file_to_diff(file) for file in pull_files_with_patch(files))

    return PullRequestData(
        ref=ref,
        title=pull.get("title"),
        files=[_github_file_to_changed_file(file) for file in files if file.get("patch")],
        diff=diff,
        head_sha=(pull.get("head") or {}).get("sha"),
        base_sha=(pull.get("base") or {}).get("sha"),
    )


async def _fetch_github_raw_diff(ref: PullRequestRef, client: httpx.AsyncClient) -> PullRequestData:
    response = await client.get(f"{ref.web_url}.diff", headers={"Accept": "text/plain"})
    if response.status_code >= 400:
        _raise_provider_error(response, "获取 GitHub PR diff 失败")
    diff = response.text
    return PullRequestData(ref=ref, title=None, files=parse_unified_diff(diff), diff=diff)


async def _fetch_github_files(client: httpx.AsyncClient, api_url: str, headers: dict[str, str]) -> list[dict]:
    files: list[dict] = []
    page = 1
    while True:
        response = await client.get(f"{api_url}/files", params={"per_page": 100, "page": page}, headers=headers)
        if response.status_code >= 400:
            _raise_provider_error(response, "获取 GitHub PR 文件列表失败")
        page_items = response.json()
        files.extend(page_items)
        if len(page_items) < 100:
            break
        page += 1
    return files


async def _fetch_gitlab_merge_request(ref: PullRequestRef, token: str | None) -> PullRequestData:
    api_base = f"{ref.scheme}://{ref.host}/api/v4"
    project = quote(ref.project_path, safe="")
    mr_url = f"{api_base}/projects/{project}/merge_requests/{ref.number}"
    headers: dict[str, str] = {}
    resolved_token = resolve_token(ref.platform, token)
    if resolved_token:
        headers["PRIVATE-TOKEN"] = resolved_token

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        mr_response = await client.get(mr_url, headers=headers)
        if mr_response.status_code >= 400:
            _raise_provider_error(mr_response, "获取 GitLab/GitCode MR 元数据失败")
        mr = _response_json(mr_response, "获取 GitLab/GitCode MR 元数据失败")

        changes_response = await client.get(f"{mr_url}/changes", headers=headers)
        if changes_response.status_code >= 400:
            _raise_provider_error(changes_response, "获取 GitLab/GitCode MR diff 失败")

    changes_payload = _response_json(changes_response, "获取 GitLab/GitCode MR diff 失败")
    changes = changes_payload.get("changes", []) if isinstance(changes_payload, dict) else []
    files = [_gitlab_change_to_changed_file(change) for change in changes if change.get("diff")]
    diff = "\n".join(_gitlab_change_to_diff(change) for change in changes if change.get("diff"))
    diff_refs = mr.get("diff_refs") or {}

    return PullRequestData(
        ref=ref,
        title=mr.get("title"),
        files=files,
        diff=diff,
        head_sha=mr.get("sha") or diff_refs.get("head_sha"),
        base_sha=diff_refs.get("base_sha"),
        start_sha=diff_refs.get("start_sha"),
    )


async def _fetch_gitcode_pull(ref: PullRequestRef, token: str | None) -> PullRequestData:
    if not ref.owner or not ref.repo:
        raise ProviderError("GitCode PR 链接需要是 https://gitcode.com/{owner}/{repo}/pull/{number} 格式。")

    owner = quote(ref.owner, safe="")
    repo = quote(ref.repo, safe="")
    number = quote(ref.number, safe="")
    api_url = f"https://api.gitcode.com/api/v5/repos/{owner}/{repo}/pulls/{number}"
    headers = {"Accept": "application/json"}
    params = _gitcode_params(token)

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        pull_response = await client.get(api_url, headers=headers, params=params)
        if pull_response.status_code >= 400:
            _raise_provider_error(pull_response, "获取 GitCode PR 元数据失败")
        pull = _response_json(pull_response, "获取 GitCode PR 元数据失败")
        if not isinstance(pull, dict):
            raise ProviderError("获取 GitCode PR 元数据失败: API 响应不是 JSON 对象。")

        files_response = await client.get(f"{api_url}/files", headers=headers, params=params)
        if files_response.status_code >= 400:
            _raise_provider_error(files_response, "获取 GitCode PR 文件列表失败")
        file_items = _response_json(files_response, "获取 GitCode PR 文件列表失败")
        if not isinstance(file_items, list):
            raise ProviderError("获取 GitCode PR 文件列表失败: API 响应不是 JSON 数组。")

    files = [_gitcode_file_to_changed_file(file) for file in file_items if _gitcode_file_patch(file)]
    diff = "\n".join(file.patch for file in files)
    head = pull.get("head") if isinstance(pull.get("head"), dict) else {}
    base = pull.get("base") if isinstance(pull.get("base"), dict) else {}

    return PullRequestData(
        ref=ref,
        title=pull.get("title"),
        files=files,
        diff=diff,
        head_sha=head.get("sha"),
        base_sha=base.get("sha"),
    )


def _github_file_to_changed_file(file: dict) -> ChangedFile:
    filename = file.get("filename", "")
    old_path = file.get("previous_filename") or filename
    patch = _github_file_to_diff(file)
    return parse_patch(patch, filename, old_path=old_path, new_path=filename)


def _github_file_to_diff(file: dict) -> str:
    filename = file.get("filename", "")
    old_path = file.get("previous_filename") or filename
    patch = file.get("patch") or ""
    return f"diff --git a/{old_path} b/{filename}\n--- a/{old_path}\n+++ b/{filename}\n{patch}"


def _gitlab_change_to_changed_file(change: dict) -> ChangedFile:
    old_path = change.get("old_path") or change.get("new_path") or ""
    new_path = change.get("new_path") or old_path
    patch = _gitlab_change_to_diff(change)
    return parse_patch(patch, new_path, old_path=old_path, new_path=new_path)


def _gitlab_change_to_diff(change: dict) -> str:
    old_path = change.get("old_path") or change.get("new_path") or ""
    new_path = change.get("new_path") or old_path
    diff = change.get("diff") or ""
    return f"diff --git a/{old_path} b/{new_path}\n--- a/{old_path}\n+++ b/{new_path}\n{diff}"


def _gitcode_params(token: str | None = None) -> dict[str, str]:
    resolved_token = resolve_token("gitcode", token)
    return {"access_token": resolved_token} if resolved_token else {}


def _gitcode_file_to_changed_file(file: dict) -> ChangedFile:
    filename = file.get("filename") or ""
    patch = _gitcode_file_to_diff(file)
    old_path = _gitcode_old_path(file)
    new_path = _gitcode_new_path(file)
    return parse_patch(patch, filename, old_path=old_path, new_path=new_path)


def _gitcode_file_to_diff(file: dict) -> str:
    old_path = _gitcode_old_path(file)
    new_path = _gitcode_new_path(file)
    old_marker = "/dev/null" if _gitcode_patch_bool(file, "new_file") or file.get("status") == "added" else f"a/{old_path}"
    new_marker = "/dev/null" if _gitcode_patch_bool(file, "deleted_file") or file.get("status") == "removed" else f"b/{new_path}"
    return f"diff --git a/{old_path} b/{new_path}\n--- {old_marker}\n+++ {new_marker}\n{_gitcode_file_patch(file)}"


def _gitcode_old_path(file: dict) -> str:
    patch = file.get("patch") if isinstance(file.get("patch"), dict) else {}
    return patch.get("old_path") or file.get("previous_filename") or file.get("filename") or ""


def _gitcode_new_path(file: dict) -> str:
    patch = file.get("patch") if isinstance(file.get("patch"), dict) else {}
    return patch.get("new_path") or file.get("filename") or _gitcode_old_path(file)


def _gitcode_file_patch(file: dict) -> str:
    patch = file.get("patch")
    if isinstance(patch, dict):
        return str(patch.get("diff") or "")
    if isinstance(patch, str):
        return patch
    return ""


def _gitcode_patch_bool(file: dict, key: str) -> bool:
    patch = file.get("patch")
    return bool(patch.get(key)) if isinstance(patch, dict) else False


def pull_files_with_patch(files: list[dict]) -> list[dict]:
    return [file for file in files if file.get("patch")]


def _without_query(url: str) -> str:
    parsed = urlparse(url)
    return parsed._replace(query="", fragment="").geturl()


def _response_json(response: httpx.Response, prefix: str) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        content_type = response.headers.get("content-type", "unknown")
        body = response.text[:400].replace("\n", " ")
        raise ProviderError(f"{prefix}: HTTP {response.status_code} 返回非 JSON 响应 ({content_type}) {body}") from exc


def _raise_provider_error(response: httpx.Response, prefix: str) -> None:
    body = response.text[:400].replace("\n", " ")
    raise ProviderError(f"{prefix}: HTTP {response.status_code} {body}")
