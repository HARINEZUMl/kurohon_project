"""構築の入口。CLAUDE.md の処理順序に従い、各モジュールを順に呼ぶ。

現時点では extract.py / structure.py（処理順序 1〜5）までを実装している。
attributes.py 以降（見出し・ラベル付与、図表所属確定、参照解決、検索、検証）は未着手。
"""

from __future__ import annotations

import argparse
import sys

import pdfplumber

# Windows のコンソールは既定でUTF-8でないため、日本語出力が文字化けする。
sys.stdout.reconfigure(encoding="utf-8")

import extract
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


def process_pages(pdf: "pdfplumber.PDF", physical_pages: list) -> tuple:
    """複数ページを指定順に処理し、行を連結してから木を構築する。

    行の組み立て（y方向の重なり判定）はページごとに独立して行う必要がある
    （物理座標top はページごとにリセットされるため、複数ページの文字をまとめて
    クラスタリングすると、別ページの偶然近い top 値を持つ行が誤って同一行と
    みなされる）。行として組み立てたあとのテキスト列にはページ固有の座標情報が
    不要になるため、そこから先はページをまたいで連結してよい。
    """
    extractions = []
    all_lines = []
    for physical_page in physical_pages:
        page = pdf.pages[physical_page - 1]
        extraction = extract.extract_page(page)
        lines = structure.assemble_lines(extraction.body_chars)
        extractions.append(extraction)
        all_lines.extend(lines)

    confirmed_roots, unconfirmed_roots, orphan_lines = structure.build_forest(all_lines)
    return extractions, confirmed_roots, unconfirmed_roots, orphan_lines


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--page", type=int, help="単一ページのみ処理する（開発用）")
    parser.add_argument("--pages", type=str, help="複数ページを連結して処理する（例: 199-200）")
    parser.add_argument("--stage", type=int, help="指定段階まで実行する（未実装。現状は常に structure.py までを実行）")
    args = parser.parse_args()

    if args.pages is not None:
        physical_pages = parse_page_spec(args.pages)
    elif args.page is not None:
        physical_pages = [args.page]
    else:
        print("--page <物理ページ番号> または --pages <開始-終了> を指定すること。", file=sys.stderr)
        sys.exit(1)

    with pdfplumber.open(PDF_PATH) as pdf:
        extractions, confirmed_roots, unconfirmed_roots, orphan_lines = process_pages(pdf, physical_pages)

    print(f"=== pages {physical_pages[0]}-{physical_pages[-1]} ===")

    for extraction in extractions:
        print(f"\n--- page {extraction.physical_page} ---")
        print(f"footer_text: {extraction.footer_text!r}")
        print(f"regions ({len(extraction.regions)}):")
        for r in extraction.regions:
            print(f"  {r.kind}: x0={r.x0:.1f} x1={r.x1:.1f} top={r.top:.1f} bottom={r.bottom:.1f}")
        unclassified = getattr(extraction, "unclassified_clusters", [])
        if unclassified:
            print(f"unclassified rect clusters ({len(unclassified)}):")
            for members in unclassified:
                x0 = min(r["x0"] for r in members)
                x1 = max(r["x1"] for r in members)
                top = min(r["top"] for r in members)
                bottom = max(r["bottom"] for r in members)
                print(f"  確定不能: x0={x0:.1f} x1={x1:.1f} top={top:.1f} bottom={bottom:.1f} (rects={len(members)})")

    if orphan_lines:
        print(f"\n--- orphan lines ({len(orphan_lines)}) ---")
        print("指定範囲の先頭より前に続きがあるテキスト（さらに前のページからの継続の可能性）:")
        for text in orphan_lines:
            print(f"  {text!r}")

    print(f"\n--- confirmed roots ({len(confirmed_roots)}) ---")
    print("SPEC 2.1 の第1段（roman）に一致する、真の根であることが確定したノード:")
    print(structure.format_tree(confirmed_roots))

    if unconfirmed_roots:
        print(f"\n--- unconfirmed roots ({len(unconfirmed_roots)}) ---")
        print(
            "marker_type が roman ではない根。指定範囲の先頭では親が見つからなかった"
            "だけであり、真の根であると確定したものではない（CLAUDE.md 原則5）:"
        )
        print(structure.format_tree(unconfirmed_roots))


if __name__ == "__main__":
    main()
