"""structure.normalize_text の単体テスト（SPEC 2.10）。

PDFを読まないため高速。均等割り付け空白の除去ルールと、ハイフン類の半角化
（DECISIONS.md D022）を主に検証する。
"""

from __future__ import annotations

import unittest

from _helpers import structure


class NormalizeTextTest(unittest.TestCase):
    def test_fullwidth_alnum_to_halfwidth(self):
        self.assertEqual(structure.normalize_text("１２３ａｂｃ"), "123abc")

    def test_roman_and_kana_untouched(self):
        # ローマ数字・カタカナは半角へ変換しない（SPEC 2.10）
        self.assertEqual(structure.normalize_text("Ⅲアイウ"), "Ⅲアイウ")

    def test_justified_spacing_two_char(self):
        # 実例：p26相当（２ 定 義 → ２定義。ここでは記号を除いた本文部分のみ）
        self.assertEqual(structure.normalize_text(" 定 義"), " 定義")
        self.assertEqual(structure.normalize_text(" 目 的"), " 目的")

    def test_justified_spacing_three_char(self):
        # 実例：p92「海 兵 隊」
        self.assertEqual(structure.normalize_text("海 兵 隊"), "海兵隊")

    def test_marker_body_leading_space_preserved(self):
        # SPEC 2.10「階層記号と本文の間の空白を除去しない」。
        # 先頭の空白は単独文字の連鎖ではないため対象にならない。
        self.assertEqual(structure.normalize_text(" 総 則"), " 総則")

    def test_negative_ascii_slash_phraseology_untouched(self):
        # 実測の誤爆候補（p116等）。ASCII文字は対象外。
        self.assertEqual(structure.normalize_text("CLIMB / DESCEND"), "CLIMB / DESCEND")
        self.assertEqual(structure.normalize_text("E / B"), "E / B")

    def test_negative_bracket_notation_untouched(self):
        # 実測の誤爆候補（p46）。〔 〕はカタカナ・漢字のUnicode範囲に含まれない。
        text = "〔 〕 ：括弧内に該当する数値、名称等を入れることを示す。"
        self.assertEqual(structure.normalize_text(text), text)

    def test_negative_two_char_words_not_merged(self):
        # 実測：p262「進入復行経路」と「出発経路」のような2文字語同士は
        # トークン長が1ではないため対象にならない。
        text = "進入復行経路 出発経路"
        self.assertEqual(structure.normalize_text(text), text)

    def test_hyphen_fullwidth_to_half(self):
        # DECISIONS.md D022。図参照区切りの全角ハイフン(U+FF0D)を半角化する。
        self.assertEqual(structure.normalize_text("(２)－４"), "(2)-4")

    def test_hyphen_u2010_to_half(self):
        # p212実測。本物のハイフン(U+2010)も対象に含める。
        self.assertEqual(structure.normalize_text("(４)‐４"), "(4)-4")

    def test_chōonpu_untouched(self):
        # U+30FC（カタカナ長音符）はハイフンではないため変換しない。
        self.assertEqual(structure.normalize_text("レーダー"), "レーダー")

    def test_fullwidth_space_justified(self):
        # 全角スペース(U+3000)も均等割り付けの区切りとして扱う。
        self.assertEqual(structure.normalize_text("総　則"), "総則")


if __name__ == "__main__":
    unittest.main()
