"""p245 の回帰テスト（CLAUDE.md開発時の起点。第8段、多重併記）。

同一行併記による第7段・第8段（kana→paren_kana）の木構築が壊れていない
ことと、SPEC 2.10 の全角→半角正規化が多段参照を含む本文でも正しく働く
ことを確認する。
"""

from __future__ import annotations

import unittest

from _helpers import build_pages, find_node


class P245Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = build_pages([245])

    def test_eighth_rank_co_occurrence_intact(self):
        # 「ア(ア)」「イ(ア)」の同一行併記（第7段kana→第8段paren_kana）。
        node_a = find_node(self.result["roots"], marker_type="kana", marker="ア")
        self.assertIsNotNone(node_a)
        child = next((c for c in node_a.children if c.marker_type == "paren_kana"), None)
        self.assertIsNotNone(child, "アの子に第8段(paren_kana)が見つからない")
        self.assertEqual(child.marker, "(ア)")

    def test_text_norm_halfwidth_conversion_in_multi_segment_reference(self):
        # 「７(１)及び(４)に規定する誘導」を含むノード。全角数字が半角化される。
        # 同じマーカー「ａ」を持つノードが複数あるため、該当テキストを含む
        # ものを木全体から探す。
        target = next(
            (n for n in _iter(self.result["roots"]) if n.text_raw and "７(１)及び(４)" in n.text_raw),
            None,
        )
        self.assertIsNotNone(target, "対象ノードが見つからない")
        self.assertIn("7(1)及び(4)", target.text_norm)

    def test_reference_with_number_type_in_own_path_is_not_descendant(self):
        # SPEC 3.11「ref_text は原本での参照表記」。ref.ref_text は
        # text_normではなくtext_rawから復元されるため、原本どおり全角。
        refs = self.result["refs"]
        candidates = [r for r in refs if r.ref_text == "７(１)"]
        self.assertTrue(candidates, "「７(１)」の参照が抽出されなかった")
        ref = candidates[0]
        # 参照元のパスに number 型（根の「４」）が含まれるため、子孫への
        # 参照ではない（SPEC 2.12）。ページ単独処理のため roman 段は
        # 参照元パスに存在せず補完不能になるが、is_descendant の判定
        # 自体はページ単独でも正しく行える。
        self.assertFalse(ref.is_descendant)


def _iter(nodes):
    for n in nodes:
        yield n
        yield from _iter(n.children)


if __name__ == "__main__":
    unittest.main()
