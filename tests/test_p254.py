"""p252〜p255（p254中心）の回帰テスト（CLAUDE.md開発時の起点）。

参照の子孫判定（SPEC 2.12）と範囲指定（`から`）を検証する。同じ記号
「(a)」「(b)」でも、参照元の段の位置によって子孫への参照か、祖先の
兄弟への参照かが変わる、SPEC 2.12 本文の実例に対応するページ。

このページ範囲だけでは第1・2段（roman・paren_roman）の祖先が別ページに
あるため、祖先の兄弟を指す参照（is_descendant=False）は「未解決」のまま
になる。本テストが検証するのは判定（is_descendant）の正しさであり、
解決の成否ではない（解決には文書全体の処理が必要でありCLAUDE.mdの方針
「384ページ全体を対象とするテストを書かない」に反する）。

また、SPEC 3.11「ref_text は原本での参照表記」（DECISIONS.md D024）の
回帰テストとして、この範囲内の参照の ref_text が正規化前の原本表記
（全角数字を含む）と一致することも検証する。
"""

from __future__ import annotations

import unittest

from _helpers import build_pages


class P254Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = build_pages([252, 253, 254, 255])
        cls.refs = cls.result["refs"]

    def test_range_reference_is_descendant_and_resolves(self):
        # 「(a)から(c)」。参照元 ｂ の子である (a)(b)(c) への範囲参照
        # （SPEC 2.12 表の1行目の実例）。
        range_refs = [r for r in self.refs if r.from_node.marker == "ｂ" and r.is_range]
        self.assertTrue(range_refs, "「ｂ」からの範囲参照が見つからない")
        ref = range_refs[0]
        self.assertTrue(ref.is_descendant)
        self.assertEqual(len(ref.targets), 3)
        self.assertEqual(
            [t.marker_norm for t in ref.targets],
            ["(a)", "(b)", "(c)"],
        )

    def test_ancestor_sibling_reference_is_not_descendant(self):
        # 「(a)若しくは(b)」。参照元 イ の祖先の兄弟を指す参照
        # （SPEC 2.12 表の2行目の実例）。このページ範囲だけでは祖先の
        # 一部（roman）が欠けるため解決はできないが、is_descendant の
        # 判定は参照元のパスとの照合のみで決まるため正しく False になる。
        candidates = [
            r
            for r in self.refs
            if r.from_node.marker == "イ"
            and r.ref_text in ("(a)", "(b)")
            and "ハンドオフ" in (r.from_node.text_raw or "")
        ]
        self.assertEqual(len(candidates), 2, "「(a)若しくは(b)」の分割が2件になっていない")
        for ref in candidates:
            self.assertFalse(ref.is_descendant)
            self.assertIsNone(ref.inherited_from)

    def test_ref_text_recovers_original_fullwidth_notation(self):
        # SPEC 3.11「ref_text は原本での参照表記」（DECISIONS.md D024）。
        # 「ｄ」ノードの参照は原本で「３(２)」、「ｇ」ノードの参照は
        # 原本で「(Ⅰ)２(５)」と、いずれも全角数字を含む表記で印字されて
        # いる。抽出・境界判定は正規化テキスト（半角数字）上で行うが、
        # 記録される ref_text は正規化前の原本表記と一致しなければならない。
        d_ref = next(r for r in self.refs if r.from_node.marker == "ｄ" and r.ref_type == "section")
        self.assertEqual(d_ref.ref_text, "３(２)")

        g_ref = next(r for r in self.refs if r.from_node.marker == "ｇ" and r.ref_type == "section")
        self.assertEqual(g_ref.ref_text, "(Ⅰ)２(５)")

    def test_ref_text_range_reference_matches_raw_with_connector(self):
        # 範囲参照（from...から...to）の ref_text も、区切り文字「から」を
        # 含めて text_raw から復元される。
        range_refs = [r for r in self.refs if r.from_node.marker == "ｂ" and r.is_range]
        self.assertTrue(range_refs)
        self.assertEqual(range_refs[0].ref_text, "(a)から(c)")


if __name__ == "__main__":
    unittest.main()
