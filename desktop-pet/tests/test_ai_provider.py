import unittest

from server.app.ai_provider import OpenAICompatibleImageProvider


class ImageProviderModelTests(unittest.TestCase):
    def test_transparent_background_capability_is_model_aware(self):
        self.assertTrue(
            OpenAICompatibleImageProvider._transparent_background_support("gpt-image-1")
        )
        self.assertTrue(
            OpenAICompatibleImageProvider._transparent_background_support("gpt-image-1.5")
        )
        self.assertFalse(
            OpenAICompatibleImageProvider._transparent_background_support("gpt-image-2")
        )
        self.assertIsNone(
            OpenAICompatibleImageProvider._transparent_background_support("custom-image-model")
        )

    def test_gpt_image_2_prompt_enables_transparent_background(self):
        prompt = OpenAICompatibleImageProvider._prompt_for(
            "walk",
            transparent_background=False,
        )

        self.assertIn("transparent background enabled", prompt)
        self.assertIn("fully transparent background", prompt)
        self.assertIn("real RGBA", prompt)
        self.assertIn("clean alpha channel", prompt)
        self.assertIn("uniform white background", prompt)
        self.assertIn("may omit the background parameter", prompt)

    def test_gpt_image_2_request_never_sends_transparent_background(self):
        attempts = OpenAICompatibleImageProvider._request_attempts(
            "gpt-image-2",
            {"model": "gpt-image-2", "prompt": "pet", "n": 8},
        )

        self.assertTrue(all("background" not in data for data in attempts))
        self.assertEqual(attempts[0]["output_format"], "png")
        self.assertEqual(attempts[1]["n"], 1)

    def test_transparent_model_prompt_requests_alpha(self):
        prompt = OpenAICompatibleImageProvider._prompt_for(
            "walk",
            transparent_background=True,
        )

        self.assertIn("transparent background enabled", prompt)
        self.assertIn("fully transparent background", prompt)
        self.assertIn("real RGBA", prompt)

    def test_background_preview_prompt_does_not_change_pet(self):
        prompt = OpenAICompatibleImageProvider._background_removal_prompt(True)

        self.assertIn("remove the entire background", prompt)
        self.assertIn("transparent background enabled", prompt)
        self.assertIn("exactly one PNG", prompt)
        self.assertIn("Do not redraw", prompt)
        self.assertIn("never invent a scene", prompt)


if __name__ == "__main__":
    unittest.main()
