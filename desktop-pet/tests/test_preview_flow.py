import unittest

from server.app.main import _public_job


class PreviewFlowTests(unittest.TestCase):
    def test_public_job_exposes_preview_urls_without_filesystem_paths(self):
        record = {
            "id": "a" * 32,
            "status": "preview_ready",
            "preview_paths": ["prepared/reference_0.png"],
            "resource_preview_paths": ["package/assets/walk_0.png"],
        }

        public = _public_job(record)

        self.assertNotIn("preview_paths", public)
        self.assertNotIn("resource_preview_paths", public)
        self.assertEqual(
            public["preview_images"][0]["url"],
            "/api/jobs/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/preview/prepared/reference_0.png",
        )
        self.assertEqual(
            public["resource_preview_images"][0]["name"],
            "walk_0.png",
        )


if __name__ == "__main__":
    unittest.main()
