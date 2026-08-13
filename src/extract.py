"""PDF抽出、領域分割（表→囲み→図→波括弧）。

処理順序（CLAUDE.md 表 #1, #2）:
  1. テキスト・図形要素の抽出
  2. 領域分割。表→囲み→図→波括弧の順で確定し、確定済み領域は後続の検出から除外する
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# SPEC 2.11 のページラベル書式。座標は改正のたびに変わりうるため根拠にせず、
# 書式に一致するかどうかで判定する。
_ROMAN = "ⅠⅡⅢⅣⅤⅥⅦ"
PAGE_LABEL_THREE_PART = re.compile(f"^\\(([{_ROMAN}])\\)－(\\d+)－(\\d+)$")
PAGE_LABEL_TWO_PART = re.compile(f"^([{_ROMAN}])－(\\d+)$")

# ページ下部表記の候補として拾う、最下端からの許容誤差（同一行かどうかの判定用）。
# 本文の行組み立て（structure.py）とは別に、フッタ候補を1行分だけ集めるための値。
FOOTER_LINE_TOLERANCE = 2.0

# 隣接する矩形断片を1つの枠として束ねる許容誤差（CLAUDE.md「ラベルの矩形は分解されて取得される」）。
RECT_CLUSTER_TOLERANCE = 2.0

# 枠内の罫線位置をクラスタリングする際の許容誤差。
EDGE_CLUSTER_TOLERANCE = 1.0

# 罫線（表・囲みの枠線）とみなす太さの上限。実測した罫線は太くても1.6pt程度
# （角の重なり）であるのに対し、p199の図中に見られる塗りつぶし矩形は最小でも
# 9pt以上ある。この閾値で「線」と「塗りつぶされた図形」を区別する。
LINE_THICKNESS_MAX = 3.0


@dataclass
class Char:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float


@dataclass
class Region:
    kind: str  # "table" | "box" | "figure"
    x0: float
    x1: float
    top: float
    bottom: float
    rects: list = field(default_factory=list)
    text: str = ""  # "box" のみ設定。ラベル文字列（attributes.py がSECTIONに割り当てる）

    def contains_point(self, x: float, y: float, pad: float = 0.0) -> bool:
        return (self.x0 - pad <= x <= self.x1 + pad) and (self.top - pad <= y <= self.bottom + pad)


@dataclass
class PageExtraction:
    physical_page: int
    width: float
    height: float
    body_chars: list  # Char。本文候補（表・囲み・図・波括弧・フッタを除外済み）
    footer_text: str  # ページ下部表記の生テキスト（未解析）。空文字なら取得できず
    regions: list  # Region


def _to_char(c: dict) -> Char:
    return Char(text=c["text"], x0=c["x0"], x1=c["x1"], top=c["top"], bottom=c["bottom"])


def _cluster_rects(rects: list, tol: float = RECT_CLUSTER_TOLERANCE) -> list:
    """近接する矩形断片を1つの枠にまとめる（空間的な近接によるクラスタリング）。"""
    n = len(rects)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    def touches(r1: dict, r2: dict) -> bool:
        if r1["x1"] + tol < r2["x0"] or r2["x1"] + tol < r1["x0"]:
            return False
        if r1["bottom"] + tol < r2["top"] or r2["bottom"] + tol < r1["top"]:
            return False
        return True

    for i in range(n):
        for j in range(i + 1, n):
            if touches(rects[i], rects[j]):
                union(i, j)

    groups: dict = {}
    for i in range(n):
        root = find(i)
        groups.setdefault(root, []).append(rects[i])
    return list(groups.values())


def _cluster_edges(values: list, tol: float = EDGE_CLUSTER_TOLERANCE) -> list:
    """1次元の座標値を、近接するもの同士でグループ化する（罫線の本数を数えるため）。"""
    if not values:
        return []
    values = sorted(values)
    groups = [[values[0]]]
    for v in values[1:]:
        if v - groups[-1][-1] <= tol:
            groups[-1].append(v)
        else:
            groups.append([v])
    return groups


def _classify_cluster(members: list) -> str | None:
    """矩形クラスタを table / box に分類する。どちらとも判定できない場合は None。

    罫線が縦横とも2本(外枠のみ)なら囲み、3本以上あれば内部に分割線を持つ表とみなす。
    """
    x_edges = []
    y_edges = []
    for r in members:
        x_edges.extend([r["x0"], r["x1"]])
        y_edges.extend([r["top"], r["bottom"]])
    x_groups = _cluster_edges(x_edges)
    y_groups = _cluster_edges(y_edges)
    if len(x_groups) > 2 and len(y_groups) > 2:
        return "table"
    if len(x_groups) == 2 and len(y_groups) == 2:
        return "box"
    return None


def _is_thin(r: dict) -> bool:
    """罫線（表・囲みの枠線）らしい細さかどうか。"""
    return min(r["x1"] - r["x0"], r["bottom"] - r["top"]) <= LINE_THICKNESS_MAX


def detect_figure_regions(page, thick_rects: list) -> list:
    """図の領域を検出する（除外のみ。FIGUREノードの生成はfigures.pyの範囲）。

    ラスタ画像は page.images からそのまま矩形領域とする。ベクター図形は、
    curve と塗りつぶし矩形（罫線ではない太さの rect、引数 thick_rects）を
    まとめてクラスタリングする。p199の図は、輪郭をcurveで、塗りつぶし部分を
    rectで描いており、両者が空間的に重なっているため一体として扱う必要がある。

    波括弧（p158, SPEC 2.8）も curve で描画されるため、この判定ではまだ
    区別できていない。p199の図は本文から離れた孤立領域として出現するが、
    波括弧は行内に埋め込まれるため、単純なbboxクラスタリングでは誤って
    本文行を巻き込む可能性がある。p158着手時に、本文行との位置関係も含めて
    再検討すること（現時点では未検証の既知の限界）。
    """
    regions = []
    for im in page.images:
        regions.append(Region(kind="figure", x0=im["x0"], x1=im["x1"], top=im["top"], bottom=im["bottom"]))

    for members in _cluster_rects(list(page.curves) + thick_rects):
        x0 = min(c["x0"] for c in members)
        x1 = max(c["x1"] for c in members)
        top = min(c["top"] for c in members)
        bottom = max(c["bottom"] for c in members)
        regions.append(Region(kind="figure", x0=x0, x1=x1, top=top, bottom=bottom, rects=members))

    return regions


def _region_text(page, region: "Region") -> str:
    """矩形領域内の文字を、原本の表記のまま連結する（枠内テキストの取得用）。

    ラベルの本文（囲みの文字列）を得るために使う。表・図はテキストを
    必要としないため呼ばない。

    ソートキーのtopは8pt単位に丸めてからx0を比較する。半角英字と全角文字は
    ベースラインが微妙にずれる（1pt未満）ことがあり、topをそのまま比較すると
    同じ行内でも読み順が入れ替わりうるため（figures.pyで実測した同種の問題、
    「グループA機」が「グループ機A」になる不具合）、行の高さ（18pt程度）より
    十分小さい単位でまとめて吸収する。
    """
    inside = [
        c
        for c in page.chars
        if region.x0 <= (c["x0"] + c["x1"]) / 2 <= region.x1
        and region.top <= (c["top"] + c["bottom"]) / 2 <= region.bottom
    ]
    inside.sort(key=lambda c: (round(c["top"] / 8), c["x0"]))
    return "".join(c["text"] for c in inside).strip()


def detect_regions(page) -> tuple:
    """表→囲み→図の順に領域を確定する。波括弧はこのページでは未実装（下記参照）。

    表・囲みの候補は罫線（細い rect）に限る。塗りつぶされた rect（p199の図に
    含まれる矩形図形など）は罫線ではないため、curve と合わせて図の候補とする。
    これを分けないと、複数の塗りつぶし矩形がたまたま縦横とも3本以上の辺を
    持つ配置になった場合に、図の一部が表として誤検出される
    （p199で実際に発生した誤検出。SPEC上「表」ではないものを「表」と確定させて
    しまうため、CLAUDE.md原則3に反する）。

    戻り値: (regions, unclassified_clusters)
    unclassified_clusters は table/box いずれにも分類できなかった罫線クラスタで、
    確定させずに報告する（CLAUDE.md 原則5：確定できないものを確定させない）。
    """
    thin_rects = [r for r in page.rects if _is_thin(r)]
    thick_rects = [r for r in page.rects if not _is_thin(r)]

    clusters = _cluster_rects(thin_rects)

    regions = []
    unclassified = []
    for members in clusters:
        kind = _classify_cluster(members)
        if kind is None:
            unclassified.append(members)
            continue
        x0 = min(r["x0"] for r in members)
        x1 = max(r["x1"] for r in members)
        top = min(r["top"] for r in members)
        bottom = max(r["bottom"] for r in members)
        region = Region(kind=kind, x0=x0, x1=x1, top=top, bottom=bottom, rects=members)
        if kind == "box":
            region.text = _region_text(page, region)
        regions.append(region)

    regions.extend(detect_figure_regions(page, thick_rects))

    return regions, unclassified


def _is_page_label(text: str) -> bool:
    return bool(PAGE_LABEL_THREE_PART.match(text) or PAGE_LABEL_TWO_PART.match(text))


def detect_footer(chars: list) -> tuple:
    """ページ下部表記（SPEC 2.11）を検出する。

    座標の固定値ではなく書式（三成分/二成分）への一致で判定する。改正により
    総ページ数やレイアウトが変わっても、書式ルール自体は不変であるため
    （SPEC 2.11）、この判定は版をまたいで有効である。

    ページ最下端の1行分の文字だけを候補とする。書式に一致しなければ、
    そのページにはページ下部表記が無い（白紙ページ等）とみなし、除外は行わない
    （CLAUDE.md「白紙ページ」：異常として扱わず正常系として処理する）。

    戻り値: (footer_chars, footer_text)。検出できなければ ([], "")。
    """
    if not chars:
        return [], ""
    max_top = max(c.top for c in chars)
    candidates = sorted(
        (c for c in chars if max_top - c.top <= FOOTER_LINE_TOLERANCE),
        key=lambda c: c.x0,
    )
    text = "".join(c.text for c in candidates).strip()
    if _is_page_label(text):
        return candidates, text
    return [], ""


def extract_page(page) -> PageExtraction:
    """pdfplumber の Page オブジェクトから、本文文字列・除外領域を抽出する。"""
    regions, unclassified = detect_regions(page)

    all_chars = [_to_char(c) for c in page.chars]

    footer_chars, footer_text = detect_footer(all_chars)
    footer_char_ids = {id(c) for c in footer_chars}

    def excluded(c: Char) -> bool:
        if id(c) in footer_char_ids:
            return True
        cx = (c.x0 + c.x1) / 2
        cy = (c.top + c.bottom) / 2
        return any(r.contains_point(cx, cy) for r in regions)

    body_chars = [c for c in all_chars if not excluded(c)]

    extraction = PageExtraction(
        physical_page=page.page_number,
        width=page.width,
        height=page.height,
        body_chars=body_chars,
        footer_text=footer_text,
        regions=regions,
    )
    extraction.unclassified_clusters = unclassified  # type: ignore[attr-defined]
    return extraction
