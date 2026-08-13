"""PDF抽出、領域分割（表→囲み→図→波括弧）。

処理順序（CLAUDE.md 表 #1, #2）:
  1. テキスト・図形要素の抽出
  2. 領域分割。表→囲み→図→波括弧の順で確定し、確定済み領域は後続の検出から除外する
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ページ下部のラベル（SPEC 2.11）は本文と無関係な組版要素であり、木構築の対象外。
# 複数ページで実測した位置（top≈780.2〜780.3）に基づく固定の足切り値。
FOOTER_TOP_THRESHOLD = 770.0

# 隣接する矩形断片を1つの枠として束ねる許容誤差（CLAUDE.md「ラベルの矩形は分解されて取得される」）。
RECT_CLUSTER_TOLERANCE = 2.0

# 枠内の罫線位置をクラスタリングする際の許容誤差。
EDGE_CLUSTER_TOLERANCE = 1.0


@dataclass
class Char:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float


@dataclass
class Region:
    kind: str  # "table" | "box"
    x0: float
    x1: float
    top: float
    bottom: float
    rects: list = field(default_factory=list)

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


def detect_regions(page) -> tuple:
    """表→囲みの順に領域を確定する。図・波括弧はこのページでは未実装（下記参照）。

    戻り値: (regions, unclassified_clusters)
    unclassified_clusters は table/box いずれにも分類できなかった矩形クラスタで、
    確定させずに報告する（CLAUDE.md 原則5：確定できないものを確定させない）。
    """
    rects = page.rects
    clusters = _cluster_rects(rects)

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
        regions.append(Region(kind=kind, x0=x0, x1=x1, top=top, bottom=bottom, rects=members))

    # 図・波括弧の検出は未実装。curves/images を含むページに遭遇した場合、
    # 誤って本文として処理するより先に止めて報告する（CLAUDE.md 原則4・5）。
    if page.curves:
        raise NotImplementedError(
            f"page {page.page_number}: {len(page.curves)} 個の curve を検出したが、"
            "図・波括弧の検出は未実装（p302/p158 着手時に実装する）。"
        )
    if page.images:
        raise NotImplementedError(
            f"page {page.page_number}: {len(page.images)} 個の image を検出したが、"
            "図の検出は未実装（p302 着手時に実装する）。"
        )

    return regions, unclassified


def extract_page(page) -> PageExtraction:
    """pdfplumber の Page オブジェクトから、本文文字列・除外領域を抽出する。"""
    regions, unclassified = detect_regions(page)

    all_chars = [_to_char(c) for c in page.chars]

    footer_chars = [c for c in all_chars if c.top > FOOTER_TOP_THRESHOLD]
    footer_chars.sort(key=lambda c: c.x0)
    footer_text = "".join(c.text for c in footer_chars).strip()

    def excluded(c: Char) -> bool:
        if c.top > FOOTER_TOP_THRESHOLD:
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
