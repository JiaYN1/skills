import unittest

from app.diff_parser import format_line_ranges, parse_unified_diff


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

    def test_format_line_ranges(self):
        self.assertEqual(format_line_ranges({1, 2, 3, 5, 8, 9}), "1-3, 5, 8-9")


if __name__ == "__main__":
    unittest.main()

