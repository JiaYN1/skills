import unittest
from pathlib import Path

from pet_common import assign_roles, safe_filename, unique_directory


class PetCommonTests(unittest.TestCase):
    def test_safe_filename_keeps_unicode_and_replaces_reserved_chars(self):
        self.assertEqual(safe_filename('猫: "Mimi"'), "猫_ _Mimi_")
        self.assertEqual(safe_filename("   "), "my-pet")

    def test_assign_roles_falls_back_to_first_photo(self):
        photos = [Path("one.jpg")]
        roles = assign_roles(photos)
        for role in ("idle", "walk", "sleep", "react"):
            self.assertEqual(roles[role], photos)

    def test_extra_photos_become_walk_frames(self):
        photos = [Path(f"{index}.png") for index in range(6)]
        roles = assign_roles(photos)
        self.assertEqual(roles["idle"], [photos[0]])
        self.assertEqual(roles["sleep"], [photos[2]])
        self.assertEqual(roles["react"], [photos[3]])
        self.assertEqual(roles["walk"], [photos[1], photos[4], photos[5]])

    def test_unique_directory_does_not_overwrite(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            (parent / "pet").mkdir()
            result = unique_directory(parent, "pet")
            self.assertEqual(result.name, "pet_2")


if __name__ == "__main__":
    unittest.main()

