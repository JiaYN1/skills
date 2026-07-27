import unittest

from app.diff_parser import parse_patch
from app.publisher import _gitcode_comment_position


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


if __name__ == "__main__":
    unittest.main()
