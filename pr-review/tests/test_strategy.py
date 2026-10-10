import unittest
from unittest.mock import patch

from app.diff_parser import parse_unified_diff
from app.strategy import (
    REASON_DOC,
    REASON_GENERATED,
    REASON_LIMIT,
    REASON_LOCK,
    REASON_TEST,
    TIER_BUSINESS,
    TIER_CONFIG,
    TIER_INTERFACE,
    build_review_plan,
    changed_line_count,
    count_changed_lines,
)


def make_patch(path: str, added: int, context: int = 1) -> str:
    header = [
        f"diff --git a/{path} b/{path}",
        f"--- a/{path}",
        f"+++ b/{path}",
        f"@@ -1,{context} +1,{context + added} @@",
    ]
    body = [" context"] * context + [f"+added {index}" for index in range(added)]
    return "\n".join(header + body) + "\n"


class SmallPrTest(unittest.TestCase):
    def test_small_pr_keeps_every_file(self):
        files = parse_unified_diff(make_patch("src/app.py", 20) + make_patch("tests/test_app.py", 30))

        plan = build_review_plan(files)

        self.assertFalse(plan.large)
        self.assertEqual(len(plan.selected), 2)
        self.assertEqual(plan.changed_lines, 50)

    def test_changed_lines_count_additions_and_deletions(self):
        diff = (
            "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n"
            "@@ -1,3 +1,3 @@\n keep\n-old line\n+new line\n"
        )
        files = parse_unified_diff(diff)

        self.assertEqual(changed_line_count(files[0]), 2)
        self.assertEqual(count_changed_lines(files), 2)

    def test_file_without_hunks_is_not_selected(self):
        files = parse_unified_diff("diff --git a/logo.png b/logo.png\nBinary files differ\n")

        plan = build_review_plan(files)

        self.assertEqual(plan.selected, [])
        self.assertEqual(plan.skipped, [("logo.png", "没有文本 diff")])


class LargePrTest(unittest.TestCase):
    def build_plan(self):
        diff = "".join(
            [
                make_patch("include/service.h", 60),
                make_patch("app/api/router.py", 80),
                make_patch("config/prod.yaml", 120),
                make_patch("deploy/k8s/deployment.yaml", 40),
                make_patch("src/core.py", 300),
                make_patch("src/utils.py", 200),
                make_patch("src/legacy.py", 90),
                make_patch("tests/test_core.py", 400),
                make_patch("docs/guide.md", 50),
                make_patch("poetry.lock", 500),
                make_patch("vendor/lib/mod.py", 300),
            ]
        )
        return build_review_plan(parse_unified_diff(diff))

    def test_large_pr_is_detected(self):
        plan = self.build_plan()

        self.assertTrue(plan.large)
        self.assertGreater(plan.changed_lines, plan.threshold)

    def test_only_the_five_most_important_files_are_selected(self):
        plan = self.build_plan()

        self.assertEqual(
            plan.selected_paths,
            [
                "app/api/router.py",
                "include/service.h",
                "config/prod.yaml",
                "deploy/k8s/deployment.yaml",
                "src/core.py",
            ],
        )

    def test_selected_files_are_labelled_by_tier(self):
        plan = self.build_plan()

        self.assertEqual(
            [plan.selected_tiers[path] for path in plan.selected_paths],
            ["接口定义", "接口定义", "配置/依赖", "配置/依赖", "业务代码"],
        )
        self.assertEqual((TIER_INTERFACE, TIER_CONFIG, TIER_BUSINESS), (3, 2, 1))

    def test_non_core_files_are_skipped_with_reasons(self):
        plan = self.build_plan()
        reasons = dict(plan.skipped)

        self.assertEqual(reasons["tests/test_core.py"], REASON_TEST)
        self.assertEqual(reasons["docs/guide.md"], REASON_DOC)
        self.assertEqual(reasons["poetry.lock"], REASON_LOCK)
        self.assertEqual(reasons["vendor/lib/mod.py"], REASON_GENERATED)
        self.assertEqual(reasons["src/utils.py"], REASON_LIMIT)
        self.assertNotIn("src/core.py", reasons)

    def test_limit_is_configurable(self):
        with patch.dict("os.environ", {"LARGE_PR_MAX_FILES": "2"}):
            plan = self.build_plan()

        self.assertEqual(plan.selected_paths, ["app/api/router.py", "include/service.h"])

    def test_threshold_is_configurable(self):
        with patch.dict("os.environ", {"LARGE_PR_DIFF_LINES": "5000"}):
            plan = self.build_plan()

        # 小 PR 走原来的单次检视路径，所有有文本 diff 的文件都保留。
        self.assertFalse(plan.large)
        self.assertEqual(len(plan.selected), 11)

    def test_invalid_configuration_falls_back_to_defaults(self):
        with patch.dict("os.environ", {"LARGE_PR_MAX_FILES": "abc", "LARGE_PR_DIFF_LINES": ""}):
            plan = self.build_plan()

        self.assertTrue(plan.large)
        self.assertEqual(len(plan.selected), 5)


if __name__ == "__main__":
    unittest.main()
