"""図表ノードの生成（SPEC 2.7, 3.5, 3.6）。

処理順序（CLAUDE.md 表 #7）:
  7. 図表ノードの生成

SPEC 2.7「構築の順序」に従い、この段階では所属（parent）を確定しない。
所属の確定は参照解決（stage8, references.py）の後（stage9）で行う。
生成したノードは parent=None のまま、フラットな列として返す。
"""

from __future__ import annotations

import os

from extract import Char, _cluster_edges
from structure import Node, _FULLWIDTH_TO_HALF, assemble_lines

IMAGE_DIR = "figures"

# 罫線とみなす太さの上限。extract.pyのLINE_THICKNESS_MAXと同じ考え方。
_LINE_THICKNESS_MAX = 3.0

# 罫線位置をクラスタリングする際の許容誤差。表の角では罫線の断片同士が
# 1.1〜1.6pt程度離れて取得されることがあり（p200実測）、1.0では別々の行
# 境界として誤認識し、内容の無い「幽霊行」が生じる。
_EDGE_TOL = 2.0


def _cell_text(page_chars, x0, x1, top, bottom) -> str:
    """セル内の文字を行として組み立て、複数行なら空白で連結する。

    単純にtopでソートすると、半角英字と全角文字でベースラインが微妙に
    ずれるため（p200実測：「Ａ」がtop=414.1、隣接する「機」がtop=413.3で
    0.8pt差）、読み順が入れ替わることがある（例:「グループA機」が
    「グループ機A」になる）。structure.assemble_lines と同じy方向の
    重なり判定で行を組み立ててから連結することで、この問題を避ける。
    """
    inside = [
        Char(text=c["text"], x0=c["x0"], x1=c["x1"], top=c["top"], bottom=c["bottom"])
        for c in page_chars
        if x0 <= (c["x0"] + c["x1"]) / 2 <= x1 and top <= (c["top"] + c["bottom"]) / 2 <= bottom
    ]
    lines = assemble_lines(inside)
    return " ".join(line.text.strip() for line in lines if line.text.strip())


def _grid_bounds(region) -> tuple:
    """表領域を構成する罫線（region.rects）から、行・列の境界位置を求める。"""
    x_edges = []
    y_edges = []
    for r in region.rects:
        x_edges.extend([r["x0"], r["x1"]])
        y_edges.extend([r["top"], r["bottom"]])
    col_bounds = sorted(sum(g) / len(g) for g in _cluster_edges(x_edges, _EDGE_TOL))
    row_bounds = sorted(sum(g) / len(g) for g in _cluster_edges(y_edges, _EDGE_TOL))
    return row_bounds, col_bounds


def _border_present(rects, axis: str, pos: float, span0: float, span1: float, tol: float = _EDGE_TOL) -> bool:
    """罫線の存在判定。

    axis="h": pos=y位置、[span0,span1]=x範囲を覆う水平罫線があるか。
    axis="v": pos=x位置、[span0,span1]=y範囲を覆う垂直罫線があるか。
    """
    for r in rects:
        if axis == "h":
            if abs(r["top"] - pos) <= tol and (r["bottom"] - r["top"]) <= _LINE_THICKNESS_MAX:
                if r["x0"] <= span0 + tol and r["x1"] >= span1 - tol:
                    return True
        else:
            if abs(r["x0"] - pos) <= tol and (r["x1"] - r["x0"]) <= _LINE_THICKNESS_MAX:
                if r["top"] <= span0 + tol and r["bottom"] >= span1 - tol:
                    return True
    return False


def _merge_cells(region, row_bounds: list, col_bounds: list) -> dict:
    """罫線の有無から結合セルを判定する（SPEC 3.6「結合セルは空文字で表現する」）。

    セル間の罫線が無い箇所を結合とみなし、Union-Findでグループ化する。
    pdfplumberのrectsは1つの罫線を複数断片で返すことがあるため（CLAUDE.md
    「ラベルの矩形は分解されて取得される」と同じ事情）、厳密な等値ではなく
    許容誤差付きで罫線の有無を判定する。

    戻り値: {(row, col): (代表row, 代表col)}
    """
    n_rows = len(row_bounds) - 1
    n_cols = len(col_bounds) - 1
    parent = {(i, j): (i, j) for i in range(n_rows) for j in range(n_cols)}

    def find(pos):
        while parent[pos] != pos:
            parent[pos] = parent[parent[pos]]
            pos = parent[pos]
        return pos

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            # 行→列の順で小さい方を代表にし、決定的にする。
            parent[rb if ra < rb else ra] = ra if ra < rb else rb

    for i in range(n_rows):
        for j in range(n_cols - 1):
            if not _border_present(region.rects, "v", col_bounds[j + 1], row_bounds[i], row_bounds[i + 1]):
                union((i, j), (i, j + 1))
    for j in range(n_cols):
        for i in range(n_rows - 1):
            if not _border_present(region.rects, "h", row_bounds[i + 1], col_bounds[j], col_bounds[j + 1]):
                union((i, j), (i + 1, j))

    return {pos: find(pos) for pos in parent}


def build_table_node(page, physical_page: int, region) -> Node:
    """表領域からTABLEノードを生成する（SPEC 3.6）。所属は未確定。"""
    row_bounds, col_bounds = _grid_bounds(region)
    n_rows = len(row_bounds) - 1
    n_cols = len(col_bounds) - 1
    primary_map = _merge_cells(region, row_bounds, col_bounds)

    group_bbox: dict = {}
    for (i, j), primary in primary_map.items():
        x0, x1 = col_bounds[j], col_bounds[j + 1]
        top, bottom = row_bounds[i], row_bounds[i + 1]
        gb = group_bbox.setdefault(primary, [x0, x1, top, bottom])
        gb[0] = min(gb[0], x0)
        gb[1] = max(gb[1], x1)
        gb[2] = min(gb[2], top)
        gb[3] = max(gb[3], bottom)

    group_text = {primary: _cell_text(page.chars, *bbox) for primary, bbox in group_bbox.items()}

    cells = [["" for _ in range(n_cols)] for _ in range(n_rows)]
    for pos, primary in primary_map.items():
        if pos == primary:
            i, j = pos
            cells[i][j] = group_text[primary]

    # 見出し行の検出: 先頭行が表全体を覆う1つの結合セルである場合に限り、
    # 構造的な根拠（内部の列区切りが一切無い）で見出し行1件と判定する。
    # 列ラベル行（先行機/後続機/最低基準 等）の判定は内容の意味解釈を要する
    # ため、ここでは行わない（CLAUDE.md原則4：推測で仕様の穴を埋めない）。
    header_rows = 0
    if n_rows > 0 and n_cols > 1:
        row0_primaries = {primary_map[(0, j)] for j in range(n_cols)}
        if len(row0_primaries) == 1:
            header_rows = 1

    return Node(
        node_type="TABLE",
        physical_page=physical_page,
        x0=region.x0,
        x1=region.x1,
        top=region.top,
        bottom=region.bottom,
        cells=cells,
        header_rows=header_rows,
    )


def _distance(region, top: float, x0: float) -> float:
    """図領域とキャプション位置の距離（隙間の合計）。重なっていれば0。"""
    dx = max(region.x0 - x0, x0 - region.x1, 0.0)
    dy = max(region.top - top, top - region.bottom, 0.0)
    return dx + dy


def group_figure_regions(figure_regions: list, captions: list) -> tuple:
    """図領域を、最近傍のキャプションでグループ化する。

    1つの図が複数のcurve/rectクラスタとして検出される実例（p199の「(２)－１」、
    輪郭curveと塗りつぶし矩形が離れて検出される）を1つの図にまとめるため、
    領域単位ではなくキャプション単位でグループ化する。

    座標のみによる判定であり、視覚的に確認していない図では誤りうる
    （CLAUDE.md「図表の検出は数値だけでは判断できない」）。呼び出し側で
    コンタクトシートを出力し、目視確認すること。

    戻り値: (groups, unmatched_regions)。groups は
    [(caption_tuple, [region, ...]), ...]。captions が空の場合、全領域が
    unmatched_regions に入る。
    """
    if not captions:
        return [], list(figure_regions)

    buckets: dict = {}
    for region in figure_regions:
        best_idx = min(
            range(len(captions)),
            key=lambda i: _distance(region, captions[i][1], captions[i][2]),
        )
        buckets.setdefault(best_idx, []).append(region)

    groups = [(captions[idx], regions) for idx, regions in buckets.items()]
    return groups, []


def _crop_and_save_image(page, physical_page: int, x0: float, top: float, x1: float, bottom: float) -> str:
    """図の領域をPNGとして切り出す。ファイル名は座標から決定的に生成する
    （SPEC 3.5「再構築のたびにファイル名が変わってはならない」）。
    """
    os.makedirs(IMAGE_DIR, exist_ok=True)
    filename = f"p{physical_page}_{round(x0)}_{round(top)}_{round(x1)}_{round(bottom)}.png"
    path = os.path.join(IMAGE_DIR, filename)
    cropped = page.crop((x0, top, x1, bottom))
    cropped.to_image(resolution=150).save(path)
    return path.replace(os.sep, "/")


def build_figure_nodes(page, physical_page: int, figure_regions: list, captions: list) -> tuple:
    """図領域からFIGUREノードを生成する（SPEC 3.5）。所属は未確定。

    戻り値: (nodes, unmatched_regions)。unmatched_regions は、このページに
    キャプションが1件も無く、図番号を確定できなかった領域（確定不能として
    報告する。CLAUDE.md原則5）。
    """
    groups, unmatched = group_figure_regions(figure_regions, captions)
    nodes = []
    for (physical_page_cap, cap_top, cap_x0, cap_text), regions in groups:
        x0 = min(r.x0 for r in regions)
        x1 = max(r.x1 for r in regions)
        rtop = min(r.top for r in regions)
        rbottom = max(r.bottom for r in regions)
        image_path = _crop_and_save_image(page, physical_page, x0, rtop, x1, rbottom)
        nodes.append(
            Node(
                node_type="FIGURE",
                physical_page=physical_page,
                x0=x0,
                x1=x1,
                top=rtop,
                bottom=rbottom,
                number=cap_text,
                number_norm=cap_text.translate(_FULLWIDTH_TO_HALF),
                image_path=image_path,
            )
        )
    return nodes, unmatched


def build_page_nodes(page, physical_page: int, regions: list, captions_on_page: list) -> tuple:
    """1ページ分の表・図領域からノードを生成する（所属未確定）。

    戻り値: (nodes, unmatched_figure_regions)。
    """
    nodes = []
    table_regions = [r for r in regions if r.kind == "table"]
    for region in table_regions:
        nodes.append(build_table_node(page, physical_page, region))

    figure_regions = [r for r in regions if r.kind == "figure"]
    figure_nodes, unmatched = build_figure_nodes(page, physical_page, figure_regions, captions_on_page)
    nodes.extend(figure_nodes)

    return nodes, unmatched


def save_contact_sheet(page, physical_page: int, nodes: list, out_path: str) -> None:
    """検出結果を目視確認するためのコンタクトシートを出力する。

    CLAUDE.md「図表の検出は数値だけでは判断できない...自分で見て確認する
    こと」に対応する。out/ は検証用の中間生成物置き場（gitignore対象）。
    """
    im = page.to_image(resolution=150)
    colors = {"FIGURE": (255, 0, 0), "TABLE": (0, 0, 255)}
    for node in nodes:
        color = colors.get(node.node_type)
        if color is None:
            continue
        im.draw_rect((node.x0, node.top, node.x1, node.bottom), stroke=color, stroke_width=2, fill=None)
    im.save(out_path)
