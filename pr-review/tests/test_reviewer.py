import asyncio
import unittest
from unittest.mock import patch

from app.dedupe import comment_fingerprint, fingerprint_marker
from app.diff_parser import parse_patch, parse_unified_diff, render_annotated_diff
from app.providers import ExistingComment, PullRequestData, PullRequestRef
from app.reviewer import (
    SYSTEM_PROMPT,
    ReviewError,
    _build_user_prompt,
    _normalize_comments,
    format_review_body,
    generate_review,
)
from app.strategy import build_review_plan


class ReviewerTest(unittest.TestCase):
    def test_generate_review_handles_empty_diff(self):
        ref = PullRequestRef(
            platform="gitcode",
            scheme="https",
            host="gitcode.com",
            project_path="Ascend/msmodeling",
            number="156",
            web_url="https://gitcode.com/Ascend/msmodeling/pull/156",
            owner="Ascend",
            repo="msmodeling",
        )
        data = PullRequestData(ref=ref, title=None, files=[], diff="")

        comments, summary, warnings = asyncio.run(generate_review(data))

        self.assertEqual(comments, [])
        self.assertEqual(summary.total, 0)
        self.assertIn("没有可审查的文本 diff。", warnings)

    def test_format_review_body_avoids_punctuation_collisions(self):
        body = format_review_body(
            file_path="app.py",
            line=12,
            category="逻辑",
            message="这里会重复处理同一条记录。",
            suggestion="在循环前去重，避免重复写入。",
            language="python",
            code_example="seen = set()",
        )

        self.assertNotIn("。；", body)
        self.assertNotIn("。，", body)
        self.assertIn("问题：这里会重复处理同一条记录。", body)
        self.assertIn("修改建议：在循环前去重，避免重复写入。", body)
        self.assertIn("修改建议：在循环前去重，避免重复写入。\n\n```python\nseen = set()\n```", body)

    def test_normalize_comments_without_valid_anchor_is_not_publishable_and_not_snapped(self):
        changed_file = parse_patch(
            """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -10,3 +10,4 @@
 value = 1
-old_call()
+new_call()
+extra_call()
 return value
""",
            "app.py",
        )

        comments = _normalize_comments(
            [
                {
                    "file_path": "app.py",
                    "line": 15,
                    "line_anchor": "A999999",
                    "category": "逻辑",
                    "severity": "建议",
                    "message": "这里需要挂到新增调用附近。",
                    "suggestion": "缺少有效锚点时不要自动推送。",
                    "code_example": "do_something()",
                    "language": "python",
                }
            ],
            [changed_file],
        )

        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0].line, 15)
        self.assertFalse(comments[0].publishable)
        self.assertIn("缺少有效 line_anchor", comments[0].publish_warning or "")

    def test_prompt_defines_new_line_basis(self):
        ref = PullRequestRef(
            platform="github",
            scheme="https",
            host="github.com",
            project_path="org/repo",
            number="1",
            web_url="https://github.com/org/repo/pull/1",
            owner="org",
            repo="repo",
        )
        data = PullRequestData(ref=ref, title="line test", files=[], diff="")
        prompt = _build_user_prompt(data, "+ [anchor:A000001] [old:-] [new:42] call()")

        self.assertIn("[new:<数字>]", SYSTEM_PROMPT)
        self.assertIn("当前磁盘文件行号", SYSTEM_PROMPT)
        self.assertIn("line 必须以同一行的 `[new:<数字>]` 为准", prompt)

    def test_normalize_comments_uses_line_anchor_before_line_number(self):
        changed_file = parse_patch(
            """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1000,3 +1000,4 @@
 keep
-old_call()
+new_call()
+extra_call()
 done
""",
            "app.py",
        )
        render_annotated_diff([changed_file], max_chars=10000)
        anchor = next(anchor for anchor, line in changed_file.line_anchors.items() if line == 1002)

        comments = _normalize_comments(
            [
                {
                    "file_path": "app.py",
                    "line": 12,
                    "line_anchor": anchor,
                    "category": "逻辑",
                    "severity": "建议",
                    "message": "这里需要挂到新增调用的真实大行号。",
                    "suggestion": "优先使用锚点映射回真实新文件行号。",
                    "code_example": "extra_call()",
                    "language": "python",
                }
            ],
            [changed_file],
        )

        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0].line, 1002)
        self.assertTrue(comments[0].publishable)


def make_patch(path: str, added: int, context: int = 1) -> str:
    header = [
        f"diff --git a/{path} b/{path}",
        f"--- a/{path}",
        f"+++ b/{path}",
        f"@@ -1,{context} +1,{context + added} @@",
    ]
    body = [" context"] * context + [f"+added {index}" for index in range(added)]
    return "\n".join(header + body) + "\n"


def make_ref() -> PullRequestRef:
    return PullRequestRef(
        platform="github",
        scheme="https",
        host="github.com",
        project_path="org/repo",
        number="1",
        web_url="https://github.com/org/repo/pull/1",
        owner="org",
        repo="repo",
    )


def llm_comment(file_path: str, line: int, anchor: str) -> dict:
    return {
        "file_path": file_path,
        "line": line,
        "line_anchor": anchor,
        "category": "逻辑",
        "severity": "建议",
        "message": "这里会重复处理同一条记录。",
        "suggestion": "在循环前先去重。",
        "code_example": "seen = set()",
        "language": "python",
    }


class ReviewBodyTest(unittest.TestCase):
    def test_body_carries_fingerprint_marker(self):
        body = format_review_body(
            file_path="app/service.py",
            line=10,
            category="逻辑",
            message="这里会重复处理同一条记录。",
            suggestion="在循环前先去重。",
            language="python",
            code_example="seen = set()",
        )

        fingerprint = comment_fingerprint("app/service.py", "逻辑", "这里会重复处理同一条记录。")
        self.assertIn(fingerprint_marker(fingerprint), body)


class LargePrReviewTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        diff = "".join(
            [
                make_patch("app/api/router.py", 200),
                make_patch("config/prod.yaml", 150),
                make_patch("src/core.py", 260),
                make_patch("src/utils.py", 120),
                make_patch("src/legacy.py", 80),
                make_patch("src/extra.py", 60),
                make_patch("tests/test_core.py", 300),
            ]
        )
        self.files = parse_unified_diff(diff)
        self.data = PullRequestData(ref=make_ref(), title="large", files=self.files, diff=diff)

    def _llm_reply(self, focus_path: str) -> dict:
        target = next(file for file in self.files if file.new_path == focus_path)
        anchor, line = next(iter(target.line_anchors.items()))
        return {"comments": [llm_comment(focus_path, line, anchor)]}

    async def test_selected_files_are_reviewed_one_call_each(self):
        calls: list[tuple[str | None, str]] = []

        async def fake_call(data, annotated_diff, model=None, focus_path=None):
            calls.append((focus_path, annotated_diff))
            return self._llm_reply(focus_path)

        with patch("app.reviewer._call_llm", new=fake_call):
            comments, summary, warnings = await generate_review(self.data)

        plan = build_review_plan(self.files)
        self.assertEqual([path for path, _ in calls], plan.selected_paths)
        self.assertEqual(len(calls), plan.file_limit)
        self.assertEqual(len(comments), plan.file_limit)
        self.assertTrue(all(comment.publishable for comment in comments))
        self.assertEqual(summary.total, plan.file_limit)

        # 每次调用只带一个文件的 diff，测试文件不占用名额
        for focus_path, annotated in calls:
            self.assertEqual(annotated.count("文件: "), 1)
            self.assertIn(f"文件: {focus_path}", annotated)
        self.assertNotIn("tests/test_core.py", plan.selected_paths)
        self.assertTrue(any("检测到大 PR" in warning for warning in warnings))

    async def test_anchors_stay_unique_across_per_file_rendering(self):
        async def fake_call(data, annotated_diff, model=None, focus_path=None):
            return self._llm_reply(focus_path)

        with patch("app.reviewer._call_llm", new=fake_call):
            await generate_review(self.data)

        anchors = [anchor for file in self.files for anchor in file.line_anchors]
        self.assertEqual(len(anchors), len(set(anchors)))

    async def test_one_failed_file_does_not_break_the_others(self):
        async def fake_call(data, annotated_diff, model=None, focus_path=None):
            if focus_path == "src/core.py":
                raise ReviewError("模拟上游失败")
            return self._llm_reply(focus_path)

        with patch("app.reviewer._call_llm", new=fake_call):
            comments, _, warnings = await generate_review(self.data)

        self.assertNotIn("src/core.py", [comment.file_path for comment in comments])
        self.assertEqual(len(comments), 4)
        self.assertTrue(any("src/core.py 审查失败" in warning for warning in warnings))

    async def test_repeated_findings_in_one_run_are_removed(self):
        async def fake_call(data, annotated_diff, model=None, focus_path=None):
            reply = self._llm_reply(focus_path)
            return {"comments": [*reply["comments"], *reply["comments"]]}

        with patch("app.reviewer._call_llm", new=fake_call):
            comments, _, _ = await generate_review(self.data)

        self.assertEqual(len(comments), 5)
        self.assertEqual(len({comment.file_path for comment in comments}), 5)

    async def test_already_posted_comments_are_flagged(self):
        async def fake_call(data, annotated_diff, model=None, focus_path=None):
            return self._llm_reply(focus_path)

        existing = [
            ExistingComment(
                id="1",
                file_path="src/core.py",
                line=1,
                body="【review】【逻辑】 `src/core.py` 第 1 行\n\n问题：这里会重复处理同一条记录。",
                url="https://example.com/comment/1",
            )
        ]

        with patch("app.reviewer._call_llm", new=fake_call):
            comments, _, warnings = await generate_review(self.data, existing=existing)

        flagged = [comment for comment in comments if comment.already_posted]
        self.assertEqual([comment.file_path for comment in flagged], ["src/core.py"])
        self.assertFalse(flagged[0].publishable)
        self.assertEqual(flagged[0].existing_url, "https://example.com/comment/1")
        self.assertTrue(any("已存在于该 PR/MR" in warning for warning in warnings))

    async def test_similar_existing_comments_are_marked_for_confirmation(self):
        async def fake_call(data, annotated_diff, model=None, focus_path=None):
            return self._llm_reply(focus_path)

        existing = [
            ExistingComment(
                id="1",
                file_path="",
                line=417,
                body="【review】【逻辑缺陷】【高】这里会重复处理同一条记录。；修改建议：在循环前先去重，参考代码如下：",
            )
        ]

        with patch("app.reviewer._call_llm", new=fake_call):
            comments, _, warnings = await generate_review(self.data, existing=existing)

        suspects = [comment for comment in comments if comment.duplicate_suspect]
        self.assertEqual(len(suspects), len(comments))
        self.assertTrue(all(comment.publishable for comment in suspects))
        self.assertTrue(all(not comment.already_posted for comment in suspects))
        self.assertTrue(any("待确认" in warning for warning in warnings))

    def test_user_prompt_scopes_large_pr_call_to_one_file(self):
        prompt = _build_user_prompt(self.data, "+ [anchor:A000001] [old:-] [new:1] call()", focus_path="src/core.py")

        self.assertIn("本次只审查文件: src/core.py", prompt)
        self.assertIn("只报告 `src/core.py` 内的问题", prompt)


if __name__ == "__main__":
    unittest.main()
