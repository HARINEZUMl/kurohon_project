"""p25〜p26 の回帰テスト（均等割り付け空白の除去、SPEC 2.10）。

CLAUDE.md が挙げる5ページ（p200/p158/p245/p302〜303/p254）のいずれも、
実際の本文中で均等割り付けの空白除去ルールを exercised しない
（全ページ走査で確認済み。DECISIONS.md D021追記2）。このページは、第1段
直下の短い見出し的な本文（「１　目的」「２　定義」）が実際に均等割り付け
で組まれている、本編中で確認できる唯一の実例である。
"""

from __future__ import annotations

import unittest

from _helpers import build_pages, find_node


class P26Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = build_pages([25, 26])

    def test_justified_heading_body_collapsed(self):
        node = find_node(self.result["roots"], marker="１")
        self.assertIsNotNone(node)
        self.assertEqual(node.text_raw, " 目 的 ")
        self.assertEqual(node.text_norm, " 目的 ")

    def test_justified_heading_body_collapsed_longer_body(self):
        # 「２ 定義」の直後に長い本文が続く場合でも、先頭の均等割り付け部分
        # だけが正しく畳まれ、後続の本文は変化しない。
        node = find_node(self.result["roots"], marker="２")
        self.assertIsNotNone(node)
        self.assertTrue(node.text_raw.startswith(" 定 義 この規程において"))
        self.assertTrue(node.text_norm.startswith(" 定義 この規程において"))

    def test_ordinary_body_text_unaffected(self):
        # 定義本文中の通常の語句（漢字・カタカナ混在）は変化しない。
        node = find_node(self.result["roots"], marker="２")
        self.assertIn("地表面に投影した航跡をいう", node.text_norm)
        self.assertIn("TACAN又はDMEから一定の距離", node.text_norm)


if __name__ == "__main__":
    unittest.main()
