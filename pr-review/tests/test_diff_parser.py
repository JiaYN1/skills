import unittest

from app.diff_parser import format_line_ranges, parse_unified_diff, render_annotated_diff


class DiffParserTest(unittest.TestCase):
    def test_parse_added_and_context_lines(self):
        diff = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,4 +1,5 @@
 import os
 
-print("old")
+print("new")
+print("added")
 done()
"""
        files = parse_unified_diff(diff)

        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].new_path, "app.py")
        self.assertEqual(files[0].added_new_lines, {3, 4})
        self.assertEqual(files[0].commentable_new_lines, {1, 2, 3, 4, 5})

    def test_render_annotated_diff_adds_line_anchors(self):
        diff = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1000,3 +1000,4 @@
 keep
-old_call()
+new_call()
+extra_call()
 done
"""
        files = parse_unified_diff(diff)

        annotated, warnings = render_annotated_diff(files, max_chars=10000)

        self.assertEqual(warnings, [])
        self.assertIn("行号基准: [new:<数字>]", annotated)
        self.assertIn("+ [anchor:A000002] [old:-] [new:1001] new_call()", annotated)
        self.assertIn("+ [anchor:A000003] [old:-] [new:1002] extra_call()", annotated)
        self.assertEqual(files[0].line_anchors["A000002"], 1001)
        self.assertEqual(files[0].line_anchors["A000003"], 1002)

    def test_render_annotated_diff_prunes_truncated_line_anchors(self):
        diff = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1000,3 +1000,4 @@
 keep
-old_call()
+new_call()
+extra_call()
 done
"""
        files = parse_unified_diff(diff)
        full_text, _ = render_annotated_diff(files, max_chars=10000)

        truncated, warnings = render_annotated_diff(files, max_chars=full_text.index("[anchor:A000003]"))

        self.assertTrue(warnings)
        self.assertIn("[DIFF_TRUNCATED]", truncated)
        self.assertIn("A000002", files[0].line_anchors)
        self.assertNotIn("A000003", files[0].line_anchors)

    def test_render_annotated_diff_skips_test_files_before_truncating(self):
        diff = """diff --git a/src/app.py b/src/app.py
--- a/src/app.py
+++ b/src/app.py
@@ -1,2 +1,3 @@
 def run():
+    return compute()
     return None
diff --git a/tests/test_app.py b/tests/test_app.py
--- a/tests/test_app.py
+++ b/tests/test_app.py
@@ -1,2 +1,3 @@
 def test_run():
+    assert run() is not None
     assert True
"""
        files = parse_unified_diff(diff)
        full_text, _ = render_annotated_diff(files, max_chars=10000)
        max_chars = full_text.index("文件: tests/test_app.py") + 1

        annotated, warnings = render_annotated_diff(files, max_chars=max_chars)

        self.assertIn("文件: src/app.py", annotated)
        self.assertNotIn("文件: tests/test_app.py", annotated)
        self.assertNotIn("[DIFF_TRUNCATED]", annotated)
        self.assertTrue(any("已跳过 1 个测试文件" in warning for warning in warnings))
        self.assertTrue(files[0].line_anchors)
        self.assertEqual(files[1].line_anchors, {})

    def test_format_line_ranges(self):
        self.assertEqual(format_line_ranges({1, 2, 3, 5, 8, 9}), "1-3, 5, 8-9")


if __name__ == "__main__":
    unittest.main()

