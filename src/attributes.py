"""属性の付与（見出し、ラベル）（SPEC 2.5, 2.6）。

処理順序（CLAUDE.md 表 #6）:
  6. 属性の付与（見出し、ラベル、ページラベル）

見出し・ラベルはノードではなく属性である（SPEC 2.2「種別に含めないもの」）。
本モジュールは、structure.py が木構築時に脇へ避けておいた見出し行・
extract.py が検出した囲みラベルを、正しいノードへ割り当てる。

割り当ての根拠は座標（ページ・y位置）である。SPEC 2.5 は「見出し→ラベル→
階層記号」の配置順序を明記しており、これは印刷上の位置関係そのものである
（図表の所属判定のように紙面位置を「所属」の根拠とすることを禁じているのとは
別の話：ここで使うのは、見出し・ラベルが「どの記号列の直前に印刷されているか」
という、SPEC自身が定めた配置規則である）。
"""

from __future__ import annotations

import re

HEADING_INNER_PATTERN = re.compile(r"^【([^】]*)】$")

# ページラベルの正規化（SPEC 2.10「均等割り付けの空白を除去する」）。
# 観測した見出しはいずれも短い連続した語句であり、【】内に空白があれば
# 均等割り付けによるものと判断できる（例：【適 用】→【適用】）。語間の実質的な
# 空白を含む見出しは今のところ確認していない。見つかった場合はこの前提を
# 見直すこと。
_JUSTIFICATION_SPACE = re.compile(r"\s+")


def normalize_heading(text: str) -> str:
    m = HEADING_INNER_PATTERN.match(text)
    if not m:
        return text
    inner = _JUSTIFICATION_SPACE.sub("", m.group(1))
    return f"【{inner}】"


def _find_next_node(all_nodes: list, physical_page: int, top: float, node_type: str | None = None):
    """(physical_page, top) の直後に生成されたノードを、出現順の列から探す。

    見出し・ラベルは、対象となる階層記号の直前に印刷されている
    （SPEC 2.5「見出し→ラベル→階層記号」）。structure.py が記録した
    ノード生成順（＝文書上の出現順）の中から、同じページでtopが最小の
    ノードを探すことで「直後の記号列」を特定する。

    node_type を指定した場合、その種別のノードに限って候補とする。
    ラベルの対象はSECTIONに限られる（SPEC 2.6）ため、直後がNOTE/PHRASE
    だった場合にそれらへ誤って付与しないようにする。
    """
    candidates = [n for n in all_nodes if n.physical_page == physical_page and n.top > top]
    if node_type is not None:
        candidates = [n for n in candidates if n.node_type == node_type]
    if not candidates:
        return None
    return min(candidates, key=lambda n: n.top)


def assign_headings(all_nodes: list, pending_headings: list) -> list:
    """見出し（SPEC 2.5）を直後の第4段（paren_number）ノードへ割り当てる。

    戻り値: 割り当てられなかった見出しのリスト（確定不能として報告する）。
    """
    unresolved = []
    for physical_page, top, text in pending_headings:
        target = _find_next_node(all_nodes, physical_page, top, node_type="SECTION")
        if target is None or target.marker_type != "paren_number":
            unresolved.append((physical_page, top, text, target))
            continue
        target.heading = text
        target.heading_norm = normalize_heading(text)
    return unresolved


def assign_labels(all_nodes: list, box_regions: list) -> list:
    """囲みラベル（SPEC 2.6）を直後のSECTIONノードへ割り当てる。段は問わない。

    box_regions は (physical_page, region) のタプルの列（region は
    extract.Region、kind=="box" のもの）。1ノードに複数のラベルがありうる。

    戻り値: 割り当てられなかったラベルのリスト（確定不能として報告する）。
    """
    unresolved = []
    for physical_page, region in box_regions:
        if not region.text:
            unresolved.append((physical_page, region, "枠内にテキストが無い"))
            continue
        target = _find_next_node(all_nodes, physical_page, region.bottom, node_type="SECTION")
        if target is None:
            unresolved.append((physical_page, region, "直後のノードが見つからない"))
            continue
        target.labels.append(region.text)
    return unresolved


def resolve_heading(node) -> str:
    """祖先を辿って直近の第4段の見出しを返す（SPEC 2.5「祖先による解決」）。

    直近の第4段の祖先が存在しない場合、または見出しを持たない場合は空文字。
    """
    n = node
    while n is not None:
        if n.marker_type == "paren_number":
            return n.heading
        n = n.parent
    return ""


def resolve_labels(node) -> list:
    """自身を含む祖先すべてのラベルを集める（SPEC 2.6「部分木全体に継承する」）。

    継承関係は問い合わせ時に木を辿って解決し、テーブルとして展開しない
    （SPEC 3.4 の注記）。
    """
    labels: list = []
    n = node
    while n is not None:
        labels.extend(n.labels)
        n = n.parent
    return labels
