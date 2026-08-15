"""structure.normalize_text の単体テスト（SPEC 2.10）。

PDFを読まないため高速。均等割り付け空白の除去ルール、ハイフン類の半角化
（DECISIONS.md D022）、および text_norm→text_raw の位置対応
（offsets。SPEC 3.11 対応）を検証する。
"""

from __future__ import annotations

import unittest

from _helpers import structure


def norm(text: str) -> str:
    return structure.normalize_text(text)[0]


class NormalizeTextTest(unittest.TestCase):
    def test_fullwidth_alnum_to_halfwidth(self):
        self.assertEqual(norm("１２３ａｂｃ"), "123abc")

    def test_roman_and_kana_untouched(self):
        # ローマ数字・カタカナは半角へ変換しない（SPEC 2.10）
        self.assertEqual(norm("Ⅲアイウ"), "Ⅲアイウ")

    def test_justified_spacing_two_char(self):
        # 実例：p26相当（２ 定 義 → ２定義。ここでは記号を除いた本文部分のみ）
        self.assertEqual(norm(" 定 義"), " 定義")
        self.assertEqual(norm(" 目 的"), " 目的")

    def test_justified_spacing_three_char(self):
        # 実例：p92「海 兵 隊」
        self.assertEqual(norm("海 兵 隊"), "海兵隊")

    def test_marker_body_leading_space_preserved(self):
        # SPEC 2.10「階層記号と本文の間の空白を除去しない」。
        # 先頭の空白は単独文字の連鎖ではないため対象にならない。
        self.assertEqual(norm(" 総 則"), " 総則")

    def test_negative_ascii_slash_phraseology_untouched(self):
        # 実測の誤爆候補（p116等）。ASCII文字は対象外。
        self.assertEqual(norm("CLIMB / DESCEND"), "CLIMB / DESCEND")
        self.assertEqual(norm("E / B"), "E / B")

    def test_negative_bracket_notation_untouched(self):
        # 実測の誤爆候補（p46）。〔 〕はカタカナ・漢字のUnicode範囲に含まれない。
        text = "〔 〕 ：括弧内に該当する数値、名称等を入れることを示す。"
        self.assertEqual(norm(text), text)

    def test_negative_two_char_words_not_merged(self):
        # 実測：p262「進入復行経路」と「出発経路」のような2文字語同士は
        # トークン長が1ではないため対象にならない。
        text = "進入復行経路 出発経路"
        self.assertEqual(norm(text), text)

    def test_hyphen_fullwidth_to_half(self):
        # DECISIONS.md D022。図参照区切りの全角ハイフン(U+FF0D)を半角化する。
        self.assertEqual(norm("(２)－４"), "(2)-4")

    def test_hyphen_u2010_to_half(self):
        # p212実測。本物のハイフン(U+2010)も対象に含める。
        self.assertEqual(norm("(４)‐４"), "(4)-4")

    def test_chōonpu_untouched(self):
        # U+30FC（カタカナ長音符）はハイフンではないため変換しない。
        self.assertEqual(norm("レーダー"), "レーダー")

    def test_fullwidth_space_justified(self):
        # 全角スペース(U+3000)も均等割り付けの区切りとして扱う。
        self.assertEqual(norm("総　則"), "総則")


class OffsetsTest(unittest.TestCase):
    """SPEC 3.11対応：text_normの各文字が text_raw のどの位置に由来するか。"""

    def test_no_change_offsets_are_identity(self):
        text = "CLIMB / DESCEND"
        result, offsets = structure.normalize_text(text)
        self.assertEqual(result, text)
        self.assertEqual(offsets, list(range(len(text))))

    def test_width_conversion_preserves_position(self):
        # 全角→半角は1文字1文字の置換であり、文字数も位置も変わらない。
        text = "１２３"
        result, offsets = structure.normalize_text(text)
        self.assertEqual(result, "123")
        self.assertEqual(offsets, [0, 1, 2])

    def test_justified_spacing_offsets_skip_removed_spaces(self):
        # "海 兵 隊" (raw, 5文字: 海=0, ' '=1, 兵=2, ' '=3, 隊=4)
        # -> "海兵隊" (norm)。除去された空白の位置(1,3)はoffsetsに現れない。
        text = "海 兵 隊"
        result, offsets = structure.normalize_text(text)
        self.assertEqual(result, "海兵隊")
        self.assertEqual(offsets, [0, 2, 4])

    def test_map_norm_span_to_raw_recovers_original_substring(self):
        # 「ａ(c)」（先頭が全角、括弧内は半角）をnormalize_textにかけた後、
        # norm側のspanから raw側の元の表記（全角を含む）を復元できること。
        text_raw = "ａ(c)"
        text_norm, offsets = structure.normalize_text(text_raw)
        self.assertEqual(text_norm, "a(c)")
        raw_start, raw_end = structure.map_norm_span_to_raw(offsets, 0, len(text_norm))
        self.assertEqual(text_raw[raw_start:raw_end], "ａ(c)")

    def test_map_norm_span_to_raw_partial_span(self):
        # 実例（p26相当）どおり、均等割り付けの最後の文字と後続本文の間には
        # 実在の空白がある（"隊"と"です"の間の空白はここでは除去されない。
        # "です"が2文字のトークンであり、単独文字同士の結合条件を満たさない
        # ため）。
        text_raw = "海 兵 隊 です"
        text_norm, offsets = structure.normalize_text(text_raw)
        self.assertEqual(text_norm, "海兵隊 です")
        # norm側の "海兵隊"（0:3）は raw側の "海 兵 隊"（0:5）に対応する。
        raw_start, raw_end = structure.map_norm_span_to_raw(offsets, 0, 3)
        self.assertEqual(text_raw[raw_start:raw_end], "海 兵 隊")


if __name__ == "__main__":
    unittest.main()
