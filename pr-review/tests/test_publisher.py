import unittest

from app.diff_parser import parse_patch
from app.publisher import _gitcode_diff_position


class GitCodePublisherTest(unittest.TestCase):
    def test_gitcode_diff_position_maps_new_lines(self):
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

        self.assertEqual(_gitcode_diff_position(changed_file, 1), 1)
        self.assertEqual(_gitcode_diff_position(changed_file, 2), 3)
        self.assertEqual(_gitcode_diff_position(changed_file, 3), 4)
        self.assertEqual(_gitcode_diff_position(changed_file, 4), 5)

    def test_gitcode_diff_position_accounts_for_additional_hunk_header(self):
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

        self.assertEqual(_gitcode_diff_position(changed_file, 1), 1)
        self.assertEqual(_gitcode_diff_position(changed_file, 2), 3)
        self.assertEqual(_gitcode_diff_position(changed_file, 10), 5)
        self.assertEqual(_gitcode_diff_position(changed_file, 11), 6)
        self.assertEqual(_gitcode_diff_position(changed_file, 12), 7)

    def test_gitcode_diff_position_skips_deleted_lines(self):
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

        self.assertIsNone(_gitcode_diff_position(changed_file, 2))


if __name__ == "__main__":
    unittest.main()
