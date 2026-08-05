import unittest
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory

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

    def test_single_image_retry_payloads_are_not_duplicated(self):
        attempts = OpenAICompatibleImageProvider._request_attempts(
            "gpt-image-2",
            {"model": "gpt-image-2", "prompt": "pet", "n": 1},
        )

        self.assertEqual(len(attempts), 2)
        self.assertNotEqual(attempts[0], attempts[1])

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

    def test_single_action_frame_prompt_requests_one_pose_only(self):
        prompt = OpenAICompatibleImageProvider._prompt_for(
            "walk",
            frame_count=12,
            pose_consistency=True,
            transparent_background=False,
            frame_index=3,
            pose="left legs passing under the body",
        )

        self.assertIn("exactly one separate PNG frame", prompt)
        self.assertIn("animation frame 4 of 12", prompt)
        self.assertIn("left legs passing under the body", prompt)
        self.assertIn("do not return a contact sheet", prompt)


class ActionFrameRequestFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_action_frames_use_one_image_request_per_pose(self):
        settings = SimpleNamespace(
            ai_enabled=True,
            ai_api_key="test-key",
            ai_image_model="gpt-image-2",
            ai_frame_count=3,
            ai_max_references=2,
            pose_consistency=True,
        )
        provider = OpenAICompatibleImageProvider(settings)
        requests = []

        async def fake_single_image(prompt, references, output_path):
            requests.append((prompt, references, output_path))
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"image")
            return output_path

        provider._generate_single_image = fake_single_image

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            identity = root / "identity.png"
            pose_reference = root / "pose.png"
            identity.write_bytes(b"identity")
            pose_reference.write_bytes(b"pose")

            output = await provider.generate_action_frames(
                [pose_reference],
                "walk",
                root / "frames",
                identity_reference=identity,
                frame_count=3,
            )

        self.assertEqual(len(output), 3)
        self.assertEqual(len(requests), 3)
        self.assertTrue(all("exactly one separate PNG frame" in item[0] for item in requests))
        self.assertEqual([item[2].name for item in requests], ["walk_0.png", "walk_1.png", "walk_2.png"])
        self.assertEqual([path.name for path in requests[1][1]], ["identity.png", "walk_0.png"])
        self.assertIn("immediately preceding animation frame", requests[1][0])

    async def test_inserted_frame_uses_only_the_previous_frame_as_continuity_reference(self):
        settings = SimpleNamespace(
            ai_enabled=True,
            ai_api_key="test-key",
            ai_image_model="gpt-image-2",
            ai_frame_count=3,
            ai_max_references=2,
            pose_consistency=True,
        )
        provider = OpenAICompatibleImageProvider(settings)
        requests = []

        async def fake_single_image(prompt, references, output_path):
            requests.append((prompt, references, output_path))
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"image")
            return output_path

        provider._generate_single_image = fake_single_image

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            identity = root / "identity.png"
            previous = root / "walk_0.png"
            identity.write_bytes(b"identity")
            previous.write_bytes(b"previous")

            await provider.generate_action_frame(
                [previous],
                "walk",
                root / "inserted.png",
                identity_reference=identity,
                frame_index=1,
                frame_count=4,
                continuity_reference=True,
            )

        self.assertEqual([path.name for path in requests[0][1]], ["identity.png", "walk_0.png"])
        self.assertIn("immediately preceding animation frame", requests[0][0])


if __name__ == "__main__":
    unittest.main()
