import unittest
from unittest.mock import patch

from app.dedupe import (
    DUPLICATE_WARNING,
    SIMILAR_WARNING,
    body_fingerprints,
    body_subject,
    comment_fingerprint,
    comment_fingerprint_of,
    deduplicate_within_batch,
    existing_fingerprints,
    fingerprint_marker,
    find_similar_comment,
    mark_already_reported,
    subject_similarity,
    with_fingerprint_marker,
)
from app.providers import ExistingComment
from app.schemas import ReviewComment


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


def posted_body(comment: ReviewComment, fingerprint: str | None = None) -> str:
    marker = fingerprint_marker(fingerprint or comment_fingerprint_of(comment))
    return f"【review】【{comment.category}】 `{comment.file_path}` 第 {comment.line} 行\n\n问题：{comment.message}\n\n{marker}"


class FingerprintTest(unittest.TestCase):
    def test_fingerprint_ignores_surrounding_and_duplicated_whitespace(self):
        first = comment_fingerprint("app/service.py", "逻辑", "这里会重复处理同一条记录。")
        second = comment_fingerprint("  app/service.py  ", " 逻辑 ", "  这里会重复处理同一条记录。  ")

        self.assertEqual(first, second)

    def test_fingerprint_ignores_line_wrapping(self):
        wrapped = "这里会重复处理\n同一条记录。"
        flat = "这里会重复处理 同一条记录。"

        self.assertEqual(
            comment_fingerprint("app/service.py", "逻辑", wrapped),
            comment_fingerprint("app/service.py", "逻辑", flat),
        )

    def test_fingerprint_depends_on_message(self):
        self.assertNotEqual(
            comment_fingerprint("a.py", "逻辑", "问题一"),
            comment_fingerprint("a.py", "逻辑", "问题二"),
        )

    def test_marker_is_idempotent_and_replaces_stale_marker(self):
        body = with_fingerprint_marker("【review】正文", "abc123")
        self.assertIn(fingerprint_marker("abc123"), body)
        self.assertEqual(with_fingerprint_marker(body, "abc123"), body)

        updated = with_fingerprint_marker(body, "def456")
        self.assertNotIn(fingerprint_marker("abc123"), updated)
        self.assertIn(fingerprint_marker("def456"), updated)
        self.assertEqual(updated.count("pr-review-id"), 1)


class BodyParsingTest(unittest.TestCase):
    def test_marker_is_preferred(self):
        comment = make_comment()

        self.assertEqual(body_fingerprints(posted_body(comment), comment.file_path), {comment_fingerprint_of(comment)})

    def test_legacy_body_without_marker_is_parsed(self):
        comment = make_comment()
        legacy = f"【review】【{comment.category}】 `{comment.file_path}` 第 42 行\n\n问题：{comment.message}"

        self.assertEqual(body_fingerprints(legacy, comment.file_path), {comment_fingerprint_of(comment)})

    def test_legacy_body_of_another_finding_is_not_a_match(self):
        comment = make_comment()
        other_message = "完全不同的另一个问题。"
        other = f"【review】【逻辑】 `{comment.file_path}` 第 42 行\n\n问题：{other_message}"

        parsed = body_fingerprints(other, comment.file_path)

        self.assertEqual(parsed, {comment_fingerprint(comment.file_path, "逻辑", other_message)})
        self.assertNotIn(comment_fingerprint_of(comment), parsed)

    def test_foreign_comment_body_is_ignored(self):
        self.assertEqual(body_fingerprints("LGTM", "app/service.py"), set())


class MarkAlreadyReportedTest(unittest.TestCase):
    def test_marker_match_flags_the_comment(self):
        comment = make_comment()
        existing = [
            ExistingComment(
                id="1",
                file_path="app/service.py",
                line=999,
                body=posted_body(comment),
                url="https://example/pr#discussion_r1",
            )
        ]

        updated, duplicates, _ = mark_already_reported([comment], existing)

        self.assertEqual(len(duplicates), 1)
        self.assertFalse(updated[0].publishable)
        self.assertTrue(updated[0].already_posted)
        self.assertEqual(updated[0].publish_warning, DUPLICATE_WARNING)
        self.assertEqual(updated[0].existing_url, "https://example/pr#discussion_r1")

    def test_legacy_body_match_flags_the_comment(self):
        comment = make_comment()
        legacy = f"【review】【{comment.category}】 `{comment.file_path}` 第 10 行\n\n问题：{comment.message}"
        existing = [ExistingComment(id="1", file_path="app/service.py", line=10, body=legacy)]

        _, duplicates, suspects = mark_already_reported([comment], existing)

        self.assertEqual(len(duplicates), 1)

    def test_same_line_but_different_finding_is_kept(self):
        comment = make_comment()
        other = "【review】【逻辑】 `app/service.py` 第 10 行\n\n问题：同一行上的另一个问题。"
        existing = [ExistingComment(id="1", file_path="app/service.py", line=10, body=other)]

        updated, duplicates, _ = mark_already_reported([comment], existing)

        self.assertEqual(duplicates, [])
        self.assertTrue(updated[0].publishable)
        self.assertFalse(updated[0].already_posted)

    def test_no_existing_comments_keeps_everything(self):
        updated, duplicates, suspects = mark_already_reported([make_comment()], [])

        self.assertEqual(duplicates, [])
        self.assertEqual(suspects, [])
        self.assertEqual(len(updated), 1)

    def test_exact_match_is_not_reported_as_a_suspect(self):
        comment = make_comment()
        existing = [ExistingComment(id="1", file_path="app/service.py", line=10, body=posted_body(comment))]

        _, duplicates, suspects = mark_already_reported([comment], existing)

        self.assertEqual(len(duplicates), 1)
        self.assertEqual(suspects, [])
        self.assertFalse(duplicates[0].duplicate_suspect)

    def test_existing_index_uses_first_occurrence(self):
        comment = make_comment()
        existing = [
            ExistingComment(id="1", file_path="app/service.py", line=10, body=posted_body(comment)),
            ExistingComment(id="2", file_path="app/service.py", line=11, body=posted_body(comment)),
        ]

        index = existing_fingerprints(existing)

        self.assertEqual(index[comment_fingerprint_of(comment)].id, "1")


class SubjectTest(unittest.TestCase):
    def test_our_own_body_subject_is_the_problem_text(self):
        comment = make_comment()

        self.assertEqual(body_subject(posted_body(comment)), comment.message)

    def test_skill_body_subject_drops_category_and_suggestion(self):
        body = (
            "【review】【逻辑缺陷】【高】AFD 分解发生在 `maybe_reuse_layers` 之后。"
            "；修改建议：把分解放到复用之前，参考代码如下：\n```python\nx\n```"
        )

        self.assertEqual(body_subject(body), "AFD 分解发生在 `maybe_reuse_layers` 之后。")

    def test_foreign_body_is_used_as_is(self):
        self.assertEqual(body_subject("🟠 **High Priority** 变更行：a.py:247 崩溃"), "🟠 **High Priority** 变更行：a.py:247 崩溃")

    def test_empty_body_has_no_subject(self):
        self.assertEqual(body_subject("   "), "")

    def test_containment_counts_as_a_full_match(self):
        self.assertEqual(subject_similarity("min(args.tpot_limits) 会抛 TypeError", "提示：min(args.tpot_limits) 会抛 TypeError，需改成标量"), 1.0)

    def test_unrelated_text_is_far_below_the_threshold(self):
        self.assertLess(subject_similarity("这里会重复处理同一条记录。", "ascend docs pipeline is running..."), 0.5)


class SimilarSuspectTest(unittest.TestCase):
    def test_skill_format_comment_becomes_a_suspect_but_stays_publishable(self):
        message = "AFD 分解发生在 maybe_reuse_layers 之后，启用默认层复用时后续遍历只能看到代表层。"
        comment = make_comment(message=message)
        existing = [
            ExistingComment(
                id="1",
                file_path="",  # GitCode 列表接口不返回文件路径
                line=417,
                body=f"【review】【逻辑缺陷】【高】{message}；修改建议：把分解放到复用之前，参考代码如下：\n```python\nx\n```",
                url=None,
            )
        ]

        updated, duplicates, suspects = mark_already_reported([comment], existing)

        self.assertEqual(duplicates, [])
        self.assertEqual(len(suspects), 1)
        marked = updated[0]
        self.assertTrue(marked.publishable)
        self.assertTrue(marked.duplicate_suspect)
        self.assertFalse(marked.already_posted)
        self.assertEqual(marked.publish_warning, SIMILAR_WARNING)
        self.assertGreaterEqual(marked.duplicate_score or 0, 0.7)

    def test_include_similar_false_leaves_the_comment_alone(self):
        message = "AFD 分解发生在 maybe_reuse_layers 之后，启用默认层复用时后续遍历只能看到代表层。"
        comment = make_comment(message=message)
        existing = [ExistingComment(id="1", file_path="", line=None, body=f"【review】【逻辑缺陷】{message}")]

        updated, duplicates, suspects = mark_already_reported([comment], existing, include_similar=False)

        self.assertEqual((duplicates, suspects), ([], []))
        self.assertFalse(updated[0].duplicate_suspect)

    def test_different_known_file_is_not_a_suspect(self):
        message = "这里会重复处理同一条记录，批量导入时会写入重复数据。"
        comment = make_comment(file_path="app/service.py", message=message)
        existing = [ExistingComment(id="1", file_path="other/service.py", line=5, body=f"【review】{message}")]

        updated, _, suspects = mark_already_reported([comment], existing)

        self.assertEqual(suspects, [])
        self.assertFalse(updated[0].duplicate_suspect)

    def test_short_noise_comments_are_not_suspects(self):
        comment = make_comment(message="compile")

        updated, _, suspects = mark_already_reported(
            [comment], [ExistingComment(id="1", file_path="", line=None, body="compile")]
        )

        self.assertEqual(suspects, [])
        self.assertFalse(updated[0].duplicate_suspect)

    def test_low_similarity_is_not_a_suspect(self):
        comment = make_comment(message="这里会重复处理同一条记录，批量导入时会写入重复数据。")
        existing = [ExistingComment(id="1", file_path="", line=None, body="【review】【性能】建议给这个循环加缓存。")]

        _, _, suspects = mark_already_reported([comment], existing)

        self.assertEqual(suspects, [])

    def test_threshold_is_configurable(self):
        message = "这里会重复处理同一条记录，批量导入时会写入重复数据。"
        comment = make_comment(message=message)
        existing = [ExistingComment(id="1", file_path="", line=None, body=f"【review】这里会重复处理同一条记录，导入时可能写入重复的数据。")]
        similar, score = find_similar_comment(comment, existing)

        self.assertIsNotNone(similar)
        self.assertLess(score, 1.0)

        with patch.dict("os.environ", {"DUPLICATE_SIMILARITY_THRESHOLD": "0.99"}):
            strict, _ = find_similar_comment(comment, existing)
        with patch.dict("os.environ", {"DUPLICATE_SIMILARITY_THRESHOLD": "0.2"}):
            loose, _ = find_similar_comment(comment, existing)

        self.assertIsNone(strict)
        self.assertIsNotNone(loose)

    def test_invalid_threshold_falls_back_to_default(self):
        message = "AFD 分解发生在 maybe_reuse_layers 之后，启用默认层复用时后续遍历只能看到代表层。"
        comment = make_comment(message=message)
        existing = [ExistingComment(id="1", file_path="", line=None, body=f"【review】【逻辑缺陷】{message}")]

        with patch.dict("os.environ", {"DUPLICATE_SIMILARITY_THRESHOLD": "not-a-number"}):
            _, _, suspects = mark_already_reported([comment], existing)

        self.assertEqual(len(suspects), 1)


class WithinBatchDedupTest(unittest.TestCase):
    def test_repeated_findings_are_removed(self):
        first = make_comment(id="c1")
        second = make_comment(id="c2", line=20)

        unique = deduplicate_within_batch([first, second])

        self.assertEqual([comment.id for comment in unique], ["c1"])

    def test_different_findings_are_kept(self):
        first = make_comment(id="c1")
        second = make_comment(id="c2", message="另一个问题。")

        self.assertEqual(len(deduplicate_within_batch([first, second])), 2)


if __name__ == "__main__":
    unittest.main()
