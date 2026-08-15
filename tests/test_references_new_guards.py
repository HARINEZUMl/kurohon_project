"""text_norm への切り替えで新たに必要になったガードの回帰テスト
（DECISIONS.md D021追記2）。

全384ページの走査で見つかった2件の実際の誤検出を、直接ページを処理して
再現しないことを確認する。全数走査自体は開発時に一度行ったものであり
（`out/reference_span_scan5.txt`）、384ページ全体を対象とするテストは
CLAUDE.md の方針（PostToolUseフックの150秒制限）に反するため書かない。
該当する1ページのみを処理する。
"""

from __future__ import annotations

import unittest

from _helpers import build_pages, references


def _section_ref_texts(result) -> list:
    """resolve_references_for_node と同じ経路でsection参照候補の文字列を集める。"""
    out = []
    for node in result["all_nodes"]:
        text = node.text_norm
        if not text:
            continue
        figure_spans = references._find_figure_refs(text)
        table_spans = references._find_table_refs(text)
        mask = references._mask_spans(text, [(s, e) for s, e, _ in figure_spans] + list(table_spans))
        for s, e, chains, connectors, chain_spans in references._extract_section_spans(mask):
            out.append(text[s:e])
    return out


class NewGuardsRegressionTest(unittest.TestCase):
    def test_p80_700feet_not_misdetected_as_reference(self):
        # p80実測：「〔例〕...at 700feet, maximum thrust required.」の
        # 「700feet」が「700f」という見せかけの参照にならないこと。
        result = build_pages([80])
        candidates = _section_ref_texts(result)
        self.assertNotIn("700f", candidates)
        self.assertFalse(any("700feet" in c or c.startswith("700f") for c in candidates))

    def test_p88_meter_comma_number_not_misdetected_as_reference(self):
        # p88実測：「...メートルを超える場合はキロメートル、5,000メートル
        # 以下の場合は...」の「ル、5」が見せかけの参照にならないこと。
        result = build_pages([88])
        candidates = _section_ref_texts(result)
        self.assertNotIn("ル、5", candidates)

    def test_p75_atis_marker_still_detected(self):
        # p75実測：「(３)ATISａ(a)アからオに掲げる事項」の「ａ」（本物の
        # 階層記号）が、直前の大文字頭字語「ATIS」を理由に誤って除外
        # されないこと（大文字は_has_continuationの対象外にする回帰防止）。
        result = build_pages([75])
        candidates = _section_ref_texts(result)
        self.assertIn("a(a)アからオ", candidates)


if __name__ == "__main__":
    unittest.main()
