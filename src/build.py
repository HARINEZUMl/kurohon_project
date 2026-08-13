"""構築の入口。CLAUDE.md の処理順序に従い、各モジュールを順に呼ぶ。

現時点では extract.py / structure.py / attributes.py / figures.py（処理順序
1〜7）までを実装している。参照解決・図表の所属確定（stage8-9）、検索、
検証は未着手。
"""

from __future__ import annotations

import argparse
import sys

import pdfplumber

# Windows のコンソールは既定でUTF-8でないため、日本語出力が文字化けする。
sys.stdout.reconfigure(encoding="utf-8")

import attributes
import extract
import figures
import structure

PDF_PATH = "pdfs/kurohon_148.pdf"


def parse_page_spec(spec: str) -> list:
    """"199" や "199-200" のようなページ指定を物理ページ番号のリストに変換する。"""
    if "-" in spec:
        start, end = spec.split("-", 1)
        start, end = int(start), int(end)
        if start > end:
            raise ValueError(f"開始ページ({start})が終了ページ({end})より後になっている。")
        return list(range(start, end + 1))
    return [int(spec)]


def process_pages(pdf: "pdfplumber.PDF", physical_pages: list) -> dict:
    """複数ページを指定順に処理し、行を連結してから木を構築し、属性・図表ノードを生成する。

    行の組み立て（y方向の重なり判定）はページごとに独立して行う必要がある
    （物理座標top はページごとにリセットされるため、複数ページの文字をまとめて
    クラスタリングすると、別ページの偶然近い top 値を持つ行が誤って同一行と
    みなされる）。行として組み立てたあとのテキスト列にはページ固有の座標情報が
    不要になるため、そこから先はページをまたいで連結してよい。
    """
    extractions = []
    pages_by_number = {}
    all_lines = []
    box_regions = []  # (physical_page, Region)
    for physical_page in physical_pages:
        page = pdf.pages[physical_page - 1]
        pages_by_number[physical_page] = page
        extraction = extract.extract_page(page)
        lines = structure.assemble_lines(extraction.body_chars, physical_page=physical_page)
        extractions.append(extraction)
        all_lines.extend(lines)
        box_regions.extend((physical_page, r) for r in extraction.regions if r.kind == "box")

    (
        confirmed_roots,
        unconfirmed_roots,
        orphan_lines,
        pending_headings,
        pending_captions,
        all_nodes,
    ) = structure.build_forest(all_lines)

    unresolved_headings = attributes.assign_headings(all_nodes, pending_headings)
    unresolved_labels = attributes.assign_labels(all_nodes, box_regions)

    figure_table_nodes = []
    unmatched_figure_regions = []
    contact_sheets = []
    for extraction in extractions:
        physical_page = extraction.physical_page
        captions_on_page = [c for c in pending_captions if c[0] == physical_page]
        nodes, unmatched = figures.build_page_nodes(
            pages_by_number[physical_page], physical_page, extraction.regions, captions_on_page
        )
        figure_table_nodes.extend(nodes)
        unmatched_figure_regions.extend((physical_page, r) for r in unmatched)
        if nodes:
            out_path = f"out/contact_p{physical_page}.png"
            figures.save_contact_sheet(pages_by_number[physical_page], physical_page, nodes, out_path)
            contact_sheets.append(out_path)

    unmatched_captions = [
        c for c in pending_captions if not any(n.number == c[3] and n.physical_page == c[0] for n in figure_table_nodes)
    ]

    return {
        "extractions": extractions,
        "confirmed_roots": confirmed_roots,
        "unconfirmed_roots": unconfirmed_roots,
        "orphan_lines": orphan_lines,
        "all_nodes": all_nodes,
        "unresolved_headings": unresolved_headings,
        "unresolved_labels": unresolved_labels,
        "figure_table_nodes": figure_table_nodes,
        "unmatched_figure_regions": unmatched_figure_regions,
        "unmatched_captions": unmatched_captions,
        "contact_sheets": contact_sheets,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--page", type=int, help="単一ページのみ処理する（開発用）")
    parser.add_argument("--pages", type=str, help="複数ページを連結して処理する（例: 199-200）")
    parser.add_argument("--stage", type=int, help="指定段階まで実行する（未実装。現状は常に figures.py までを実行）")
    args = parser.parse_args()

    if args.pages is not None:
        physical_pages = parse_page_spec(args.pages)
    elif args.page is not None:
        physical_pages = [args.page]
    else:
        print("--page <物理ページ番号> または --pages <開始-終了> を指定すること。", file=sys.stderr)
        sys.exit(1)

    with pdfplumber.open(PDF_PATH) as pdf:
        result = process_pages(pdf, physical_pages)

    print(f"=== pages {physical_pages[0]}-{physical_pages[-1]} ===")

    for extraction in result["extractions"]:
        print(f"\n--- page {extraction.physical_page} ---")
        print(f"footer_text: {extraction.footer_text!r}")
        print(f"regions ({len(extraction.regions)}):")
        for r in extraction.regions:
            extra = f" text={r.text!r}" if r.text else ""
            print(f"  {r.kind}: x0={r.x0:.1f} x1={r.x1:.1f} top={r.top:.1f} bottom={r.bottom:.1f}{extra}")
        unclassified = getattr(extraction, "unclassified_clusters", [])
        if unclassified:
            print(f"unclassified rect clusters ({len(unclassified)}):")
            for members in unclassified:
                x0 = min(r["x0"] for r in members)
                x1 = max(r["x1"] for r in members)
                top = min(r["top"] for r in members)
                bottom = max(r["bottom"] for r in members)
                print(f"  確定不能: x0={x0:.1f} x1={x1:.1f} top={top:.1f} bottom={bottom:.1f} (rects={len(members)})")

    if result["orphan_lines"]:
        print(f"\n--- orphan lines ({len(result['orphan_lines'])}) ---")
        print("指定範囲の先頭より前に続きがあるテキスト（さらに前のページからの継続の可能性）:")
        for text in result["orphan_lines"]:
            print(f"  {text!r}")

    if result["unresolved_headings"]:
        print(f"\n--- unresolved headings ({len(result['unresolved_headings'])}) ---")
        print("直後に第4段のノードが見つからなかった見出し（確定不能）:")
        for physical_page, top, text, target in result["unresolved_headings"]:
            target_desc = f"marker_type={target.marker_type} {target.marker!r}" if target else "直後のノードなし"
            print(f"  page {physical_page} top={top:.1f} {text!r} -> {target_desc}")

    if result["unresolved_labels"]:
        print(f"\n--- unresolved labels ({len(result['unresolved_labels'])}) ---")
        print("直後のノードが見つからなかった、またはテキストが取得できなかったラベル（確定不能）:")
        for physical_page, region, reason in result["unresolved_labels"]:
            print(f"  page {physical_page} region=({region.x0:.1f},{region.top:.1f}) {reason}")

    print(f"\n--- figure/table nodes ({len(result['figure_table_nodes'])}) ---")
    print("所属未確定（stage9=図表の所属確定は未実装。参照解決の後に行う）:")
    print(structure.format_tree(result["figure_table_nodes"]))

    if result["unmatched_figure_regions"]:
        print(f"\n--- unmatched figure regions ({len(result['unmatched_figure_regions'])}) ---")
        print("同じページにキャプションが無く、図番号を確定できなかった領域（確定不能）:")
        for physical_page, region in result["unmatched_figure_regions"]:
            print(f"  page {physical_page} bbox=({region.x0:.1f},{region.top:.1f},{region.x1:.1f},{region.bottom:.1f})")

    if result["unmatched_captions"]:
        print(f"\n--- unmatched captions ({len(result['unmatched_captions'])}) ---")
        print("図領域に対応付けられなかったキャプション（確定不能）:")
        for physical_page, top, x0, text in result["unmatched_captions"]:
            print(f"  page {physical_page} top={top:.1f} x0={x0:.1f} {text!r}")

    if result["contact_sheets"]:
        print(f"\n--- contact sheets ---")
        for path in result["contact_sheets"]:
            print(f"  {path}")

    print(f"\n--- confirmed roots ({len(result['confirmed_roots'])}) ---")
    print("SPEC 2.1 の第1段（roman）に一致する、真の根であることが確定したノード:")
    print(structure.format_tree(result["confirmed_roots"]))

    if result["unconfirmed_roots"]:
        print(f"\n--- unconfirmed roots ({len(result['unconfirmed_roots'])}) ---")
        print(
            "marker_type が roman ではない根。指定範囲の先頭では親が見つからなかった"
            "だけであり、真の根であると確定したものではない（CLAUDE.md 原則5）:"
        )
        print(structure.format_tree(result["unconfirmed_roots"]))


if __name__ == "__main__":
    main()
