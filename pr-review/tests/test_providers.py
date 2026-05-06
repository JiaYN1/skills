import unittest

import httpx

from app.providers import ProviderError, _gitcode_file_to_changed_file, _response_json, parse_pr_url


class GitCodeProviderTest(unittest.TestCase):
    def test_parse_gitcode_pull_url_sets_owner_repo(self):
        ref = parse_pr_url("https://gitcode.com/Ascend/msmodeling/pull/156")

        self.assertEqual(ref.platform, "gitcode")
        self.assertEqual(ref.project_path, "Ascend/msmodeling")
        self.assertEqual(ref.owner, "Ascend")
        self.assertEqual(ref.repo, "msmodeling")
        self.assertEqual(ref.number, "156")

    def test_gitcode_file_patch_to_changed_file(self):
        changed_file = _gitcode_file_to_changed_file(
            {
                "filename": "cli/completion.py",
                "status": "added",
                "patch": {"diff": "@@ -0,0 +1,2 @@\n+print('x')\n+print('y')"},
            }
        )

        self.assertEqual(changed_file.new_path, "cli/completion.py")
        self.assertEqual(changed_file.added_new_lines, {1, 2})
        self.assertEqual(changed_file.commentable_new_lines, {1, 2})

    def test_non_json_response_raises_provider_error(self):
        response = httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=b"<html></html>",
            request=httpx.Request("GET", "https://gitcode.com/api/v4/test"),
        )

        with self.assertRaisesRegex(ProviderError, "非 JSON"):
            _response_json(response, "获取 GitCode PR 元数据失败")


if __name__ == "__main__":
    unittest.main()
