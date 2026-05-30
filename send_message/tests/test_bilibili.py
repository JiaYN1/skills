from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from app.bilibili import parse_dynamic_item


class ParseDynamicItemTest(unittest.TestCase):
    def test_parse_archive_dynamic(self) -> None:
        item = parse_dynamic_item(
            "123",
            {
                "id_str": "987654321",
                "type": "DYNAMIC_TYPE_AV",
                "basic": {"jump_url": "//www.bilibili.com/video/BV1xx411c7mD"},
                "modules": {
                    "module_author": {"name": "test_up", "pub_ts": 1_700_000_000},
                    "module_dynamic": {
                        "desc": {"text": "新视频来了"},
                        "major": {
                            "type": "MAJOR_TYPE_ARCHIVE",
                            "archive": {
                                "title": "视频标题",
                                "desc": "视频简介",
                                "jump_url": "//www.bilibili.com/video/BV1xx411c7mD",
                            },
                        },
                    },
                },
            },
        )

        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.dynamic_id, "987654321")
        self.assertEqual(item.author_name, "test_up")
        self.assertEqual(item.title, "视频标题")
        self.assertIn("新视频来了", item.summary)
        self.assertIn("视频简介", item.summary)
        self.assertEqual(item.url, "https://www.bilibili.com/video/BV1xx411c7mD")
        self.assertEqual(
            item.published_at,
            datetime.fromtimestamp(1_700_000_000, tz=timezone(timedelta(hours=8))),
        )

    def test_parse_word_dynamic_from_rich_text_nodes(self) -> None:
        item = parse_dynamic_item(
            "123",
            {
                "id": 111,
                "type": "DYNAMIC_TYPE_WORD",
                "modules": {
                    "module_author": {"name": "test_up"},
                    "module_dynamic": {
                        "desc": {
                            "rich_text_nodes": [
                                {"text": "第一段"},
                                {"text": "第二段"},
                            ]
                        }
                    },
                },
            },
        )

        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.dynamic_id, "111")
        self.assertEqual(item.title, "第一段第二段")
        self.assertEqual(item.summary, "第一段第二段")
        self.assertEqual(item.url, "https://t.bilibili.com/111")


if __name__ == "__main__":
    unittest.main()
