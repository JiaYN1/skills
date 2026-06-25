import asyncio
import unittest

from app.diff_parser import parse_patch, render_annotated_diff
from app.providers import PullRequestData, PullRequestRef
from app.reviewer import SYSTEM_PROMPT, _build_user_prompt, _format_review_body, _normalize_comments, generate_review


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
        body = _format_review_body(
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


if __name__ == "__main__":
    unittest.main()
