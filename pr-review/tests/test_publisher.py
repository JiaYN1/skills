import unittest
from unittest.mock import AsyncMock, patch

from app.dedupe import comment_fingerprint, fingerprint_marker
from app.diff_parser import parse_patch
from app.providers import ExistingComment, ProviderError
from app.publisher import _gitcode_comment_position, _publish_body, publish_comments
from app.schemas import PublishItemResult, ReviewComment


def make_comment(**overrides) -> ReviewComment:
    data = {
        "id": "c1",
        "file_path": "app/service.py",
        "line": 10,
        "category": "逻辑",
        "message": "这里会重复处理同一条记录。",
        "suggestion": "在循环前先去重。",
        "code_example": "seen = set()",
        "language": "python",
        "body": "",
    }
    data.update(overrides)
    return ReviewComment(**data)


def existing_body(message: str = "这里会重复处理同一条记录。") -> str:
    fingerprint = comment_fingerprint("app/service.py", "逻辑", message)
    return f"【review】【逻辑】 `app/service.py` 第 10 行\n\n问题：{message}\n\n{fingerprint_marker(fingerprint)}"


class GitCodePublisherTest(unittest.TestCase):
    def test_gitcode_comment_position_uses_absolute_new_line(self):
        changed_file = parse_patch(
            """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,3 +1,4 @@
 import os
-print("old")
+print("new")
+print("added")
 done()
""",
            "app.py",
        )

        self.assertEqual(_gitcode_comment_position(changed_file, 1), 1)
        self.assertEqual(_gitcode_comment_position(changed_file, 2), 2)
        self.assertEqual(_gitcode_comment_position(changed_file, 3), 3)
        self.assertEqual(_gitcode_comment_position(changed_file, 4), 4)

    def test_gitcode_comment_position_does_not_use_hunk_offsets(self):
        changed_file = parse_patch(
            """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,2 +1,2 @@
 one
-two
+two changed
@@ -10,2 +10,3 @@
 ten
+eleven
 twelve
""",
            "app.py",
        )

        self.assertEqual(_gitcode_comment_position(changed_file, 1), 1)
        self.assertEqual(_gitcode_comment_position(changed_file, 2), 2)
        self.assertEqual(_gitcode_comment_position(changed_file, 10), 10)
        self.assertEqual(_gitcode_comment_position(changed_file, 11), 11)
        self.assertEqual(_gitcode_comment_position(changed_file, 12), 12)

    def test_gitcode_comment_position_rejects_deleted_lines(self):
        changed_file = parse_patch(
            """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,2 +1,1 @@
 one
-two
""",
            "app.py",
        )

        self.assertIsNone(_gitcode_comment_position(changed_file, 2))


class PublishBodyTest(unittest.TestCase):
    def test_body_is_rebuilt_and_marked_when_missing(self):
        body = _publish_body(make_comment())

        self.assertIn("【review】【逻辑】", body)
        self.assertIn("pr-review-id:", body)

    def test_marking_is_idempotent(self):
        first = _publish_body(make_comment())
        second = _publish_body(make_comment(body=first))

        self.assertEqual(first, second)
        self.assertEqual(second.count("pr-review-id"), 1)


class PublishDedupTest(unittest.IsolatedAsyncioTestCase):
    async def test_already_reported_comment_is_not_posted(self):
        comment = make_comment()
        existing = [ExistingComment(id="1", file_path="app/service.py", line=10, body=existing_body())]

        with patch("app.publisher.fetch_existing_comments", new=AsyncMock(return_value=existing)), patch(
            "app.publisher._publish_github_comments", new=AsyncMock(return_value=[])
        ) as publish:
            results = await publish_comments("https://github.com/org/repo/pull/1", [comment], token="t")

        publish.assert_not_awaited()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "skipped")
        self.assertIn("已存在相同意见", results[0].error or "")

    async def test_only_new_comments_reach_the_platform(self):
        duplicate = make_comment(id="dup")
        fresh = make_comment(id="new", line=22, message="另一个问题。")
        existing = [ExistingComment(id="1", file_path="app/service.py", line=10, body=existing_body())]
        posted: list[ReviewComment] = []

        async def fake_publish(pr_url, comments, token):
            posted.extend(comments)
            return [
                PublishItemResult(id=comment.id, file_path=comment.file_path, line=comment.line, status="published")
                for comment in comments
            ]

        with patch("app.publisher.fetch_existing_comments", new=AsyncMock(return_value=existing)), patch(
            "app.publisher._publish_github_comments", new=fake_publish
        ):
            results = await publish_comments(
                "https://github.com/org/repo/pull/1", [duplicate, fresh], token="t"
            )

        self.assertEqual([comment.id for comment in posted], ["new"])
        statuses = {result.id: result.status for result in results}
        self.assertEqual(statuses, {"new": "published", "dup": "skipped"})

    async def test_suspected_duplicate_still_reaches_the_platform(self):
        """疑似重复只是提示，用户勾选后必须真的发出去。"""

        message = "AFD 分解发生在 maybe_reuse_layers 之后，启用默认层复用时后续遍历只能看到代表层。"
        comment = make_comment(message=message)
        existing = [
            ExistingComment(
                id="1",
                file_path="",
                line=417,
                body=f"【review】【逻辑缺陷】【高】{message}；修改建议：把分解放到复用之前。",
            )
        ]
        posted: list[ReviewComment] = []

        async def fake_publish(pr_url, comments, token):
            posted.extend(comments)
            return [
                PublishItemResult(id=item.id, file_path=item.file_path, line=item.line, status="published")
                for item in comments
            ]

        with patch("app.publisher.fetch_existing_comments", new=AsyncMock(return_value=existing)), patch(
            "app.publisher._publish_github_comments", new=fake_publish
        ):
            results = await publish_comments("https://github.com/org/repo/pull/1", [comment], token="t")

        self.assertEqual([item.id for item in posted], ["c1"])
        self.assertEqual([result.status for result in results], ["published"])

    async def test_lookup_failure_does_not_block_publishing(self):
        comment = make_comment()

        async def fake_publish(pr_url, comments, token):
            return [
                PublishItemResult(id=item.id, file_path=item.file_path, line=item.line, status="published")
                for item in comments
            ]

        with patch(
            "app.publisher.fetch_existing_comments", new=AsyncMock(side_effect=ProviderError("boom"))
        ), patch("app.publisher._publish_github_comments", new=fake_publish):
            results = await publish_comments("https://github.com/org/repo/pull/1", [comment], token="t")

        self.assertEqual([result.status for result in results], ["published"])


if __name__ == "__main__":
    unittest.main()
