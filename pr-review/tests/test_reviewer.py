import asyncio
import unittest

from app.diff_parser import parse_patch, render_annotated_diff
from app.providers import PullRequestData, PullRequestRef
from app.reviewer import _format_review_body, _normalize_comments, generate_review


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

    def test_normalize_comments_snaps_to_nearest_commentable_line(self):
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
                    "category": "逻辑",
                    "severity": "建议",
                    "message": "这里需要挂到新增调用附近。",
                    "suggestion": "把意见落到最近的可评论新行。",
                    "code_example": "do_something()",
                    "language": "python",
                }
            ],
            [changed_file],
        )

        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0].line, 13)
        self.assertTrue(comments[0].publishable)

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
