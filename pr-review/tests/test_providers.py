import unittest
from unittest.mock import patch

import httpx

from app.providers import (
    ProviderError,
    _gitcode_file_to_changed_file,
    _response_json,
    fetch_existing_comments,
    parse_pr_url,
)


def client_factory(handler):
    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    return factory


class GitCodeProviderTest(unittest.TestCase):
    def test_parse_gitcode_pull_url_sets_owner_repo(self):
        ref = parse_pr_url("https://gitcode.com/Ascend/msmodeling/pull/156")

        self.assertEqual(ref.platform, "gitcode")
        self.assertEqual(ref.project_path, "Ascend/msmodeling")
        self.assertEqual(ref.owner, "Ascend")
        self.assertEqual(ref.repo, "msmodeling")
        self.assertEqual(ref.number, "156")

    def test_gitcode_file_patch_to_changed_file(self):
        changed_file = _gitcode_file_to_changed_file(
            {
                "filename": "cli/completion.py",
                "status": "added",
                "patch": {"diff": "@@ -0,0 +1,2 @@\n+print('x')\n+print('y')"},
            }
        )

        self.assertEqual(changed_file.new_path, "cli/completion.py")
        self.assertEqual(changed_file.added_new_lines, {1, 2})
        self.assertEqual(changed_file.commentable_new_lines, {1, 2})

    def test_non_json_response_raises_provider_error(self):
        response = httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=b"<html></html>",
            request=httpx.Request("GET", "https://gitcode.com/api/v4/test"),
        )

        with self.assertRaisesRegex(ProviderError, "非 JSON"):
            _response_json(response, "获取 GitCode PR 元数据失败")


class ExistingCommentsTest(unittest.IsolatedAsyncioTestCase):
    async def test_github_review_comments_are_parsed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/repos/org/repo/pulls/7/comments")
            self.assertEqual(request.headers.get("authorization"), "Bearer t")
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 11,
                        "path": "app/main.py",
                        "line": 12,
                        "body": "【review】【逻辑】 `app/main.py` 第 12 行",
                        "html_url": "https://github.com/org/repo/pull/7#discussion_r11",
                        "user": {"login": "reviewer"},
                    },
                    {"id": 12, "path": "app/main.py", "original_line": 30, "body": "无行号字段"},
                    "not a dict",
                ],
            )

        ref = parse_pr_url("https://github.com/org/repo/pull/7")
        with patch("app.providers.httpx.AsyncClient", new=client_factory(handler)):
            comments = await fetch_existing_comments(ref, token="t")

        self.assertEqual(len(comments), 2)
        self.assertEqual(comments[0].file_path, "app/main.py")
        self.assertEqual(comments[0].line, 12)
        self.assertEqual(comments[0].author, "reviewer")
        self.assertEqual(comments[0].url, "https://github.com/org/repo/pull/7#discussion_r11")
        self.assertEqual(comments[1].line, 30)

    async def test_gitlab_discussions_skip_system_notes(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertIn("merge_requests/3/discussions", str(request.url))
            self.assertEqual(request.headers.get("private-token"), "t")
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "discussion-1",
                        "notes": [
                            {
                                "id": 1,
                                "body": "【review】【逻辑】 `a.py` 第 3 行",
                                "position": {"new_path": "a.py", "new_line": 3},
                                "author": {"username": "bot"},
                            },
                            {"id": 2, "body": "added 1 commit", "system": True},
                            {"id": 3, "body": "普通评论，没有位置信息"},
                        ],
                    }
                ],
            )

        ref = parse_pr_url("https://gitlab.example.com/group/repo/-/merge_requests/3")
        with patch("app.providers.httpx.AsyncClient", new=client_factory(handler)):
            comments = await fetch_existing_comments(ref, token="t")

        self.assertEqual([comment.id for comment in comments], ["1", "3"])
        self.assertEqual(comments[0].file_path, "a.py")
        self.assertEqual(comments[0].line, 3)
        self.assertEqual(comments[0].author, "bot")
        self.assertIsNone(comments[1].line)

    async def test_gitcode_comments_use_token_param(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/api/v5/repos/owner/repo/pulls/9/comments")
            self.assertEqual(request.url.params.get("access_token"), "t")
            # 真实响应：行级评论的行号在 diff_position 里，列表接口不返回文件路径
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 5,
                        "discussion_id": "abc",
                        "body": "【review】【逻辑缺陷】问题描述。",
                        "user": {"login": "u"},
                        "comment_type": "diff_comment",
                        "resolved": True,
                        "diff_position": {"start_new_line": 8, "end_new_line": 8, "position_type": "text"},
                    },
                    {
                        "id": 6,
                        "body": "ascend docs pipeline is running...",
                        "user": {"login": "robot"},
                        "comment_type": "pr_comment",
                    },
                ],
            )

        ref = parse_pr_url("https://gitcode.com/owner/repo/pull/9")
        with patch("app.providers.httpx.AsyncClient", new=client_factory(handler)):
            comments = await fetch_existing_comments(ref, token="t")

        self.assertEqual([comment.id for comment in comments], ["5", "6"])
        self.assertEqual(comments[0].file_path, "")
        self.assertEqual(comments[0].line, 8)
        self.assertEqual(comments[0].author, "u")
        self.assertEqual(comments[0].body, "【review】【逻辑缺陷】问题描述。")
        self.assertIsNone(comments[1].line)

    async def test_gitcode_accepts_a_flat_position_shape_too(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=[{"id": 5, "body": "x", "path": "cli/x.py", "position": 8, "user": {"login": "u"}}],
            )

        ref = parse_pr_url("https://gitcode.com/owner/repo/pull/9")
        with patch("app.providers.httpx.AsyncClient", new=client_factory(handler)):
            comments = await fetch_existing_comments(ref, token="t")

        self.assertEqual(comments[0].file_path, "cli/x.py")
        self.assertEqual(comments[0].line, 8)

    async def test_platform_error_is_raised_for_the_caller_to_ignore(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"message": "forbidden"})

        ref = parse_pr_url("https://github.com/org/repo/pull/7")
        with patch("app.providers.httpx.AsyncClient", new=client_factory(handler)):
            with self.assertRaises(ProviderError):
                await fetch_existing_comments(ref, token="t")


if __name__ == "__main__":
    unittest.main()
