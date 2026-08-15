"""p199〜p201（p200中心）の回帰テスト（CLAUDE.md開発時の起点）。

階層の併記・四角囲みラベル・図表の所属・段の省略を含む。本テストでは
構造の基本的な健全性と、SPEC 2.10 実装後の text_norm 経由での図参照解決
（本文中の図参照が figures レコードに解決されること）を確認する。

図(２)－４・(２)－５はp200の本文から参照されるが、キャプション自体は
p201にある（figures.pyが検出するFIGUREノードもp201側）。p200単独では
参照は未解決のままになるため、p199〜p201の3ページを連結して処理する。
"""

from __future__ import annotations

import unittest

from _helpers import build_pages, find_node


class P200Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = build_pages([199, 200, 201])

    def test_text_norm_populated(self):
        node = find_node(self.result["roots"], marker="ｂ")
        self.assertIsNotNone(node)
        self.assertTrue(node.text_norm)
        # 全角丸括弧内の全角数字が半角化されている（D022）。
        aro_node = next(c for c in node.children[0].children if c.marker == "ア")
        self.assertIn("(2)-4", aro_node.text_norm)

    def test_figure_reference_resolves_to_figure_node(self):
        # 本文「((２)－４図)」が FIGURE (2)-4 ノードへ解決されることを確認する
        # （ユーザー指示：図参照がfiguresレコードに解決されるテストを1本追加）。
        figure_refs = [r for r in self.result["refs"] if r.ref_type == "figure"]
        self.assertTrue(figure_refs, "figure参照が1件も抽出されなかった")
        resolved_numbers = {r.targets[0].number_norm for r in figure_refs if r.targets}
        self.assertIn("(2)-4", resolved_numbers)
        target = next(r.targets[0] for r in figure_refs if r.targets and r.targets[0].number_norm == "(2)-4")
        self.assertEqual(target.node_type, "FIGURE")

    def test_multiple_figure_references_resolve_through_single_comparison_function(self):
        # 比較が figures.normalize_figure_number の1関数に限定されていること
        # （DECISIONS.md D022）を、複数の図参照が一貫して解決できることで
        # 確認する。
        figure_refs = [r for r in self.result["refs"] if r.ref_type == "figure"]
        resolved_numbers = {r.targets[0].number_norm for r in figure_refs if r.targets}
        self.assertEqual(resolved_numbers, {"(2)-1", "(2)-4", "(2)-5"})


if __name__ == "__main__":
    unittest.main()
