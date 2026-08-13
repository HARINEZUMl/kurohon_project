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


def process_page(pdf: "pdfplumber.PDF", physical_page: int):
    page = pdf.pages[physical_page - 1]
    extraction = extract.extract_page(page)
    lines = structure.assemble_lines(extraction.body_chars)
    roots, orphan_lines = structure.build_forest(lines)
    return extraction, roots, orphan_lines


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--page", type=int, help="単一ページのみ処理する（開発用）")
    parser.add_argument("--stage", type=int, help="指定段階まで実行する（未実装。現状は常に structure.py までを実行）")
    args = parser.parse_args()

    if args.page is None:
        print("現時点では --page <物理ページ番号> のみ対応している（全体構築は未実装）。", file=sys.stderr)
        sys.exit(1)

    with pdfplumber.open(PDF_PATH) as pdf:
        extraction, roots, orphan_lines = process_page(pdf, args.page)

    print(f"=== page {args.page} ===")
    print(f"footer_text: {extraction.footer_text!r}")

    print(f"\n--- regions ({len(extraction.regions)}) ---")
    for r in extraction.regions:
        print(f"{r.kind}: x0={r.x0:.1f} x1={r.x1:.1f} top={r.top:.1f} bottom={r.bottom:.1f}")

    unclassified = getattr(extraction, "unclassified_clusters", [])
    if unclassified:
        print(f"\n--- unclassified rect clusters ({len(unclassified)}) ---")
        for members in unclassified:
            x0 = min(r["x0"] for r in members)
            x1 = max(r["x1"] for r in members)
            top = min(r["top"] for r in members)
            bottom = max(r["bottom"] for r in members)
            print(f"確定不能: x0={x0:.1f} x1={x1:.1f} top={top:.1f} bottom={bottom:.1f} (rects={len(members)})")

    if orphan_lines:
        print(f"\n--- orphan lines ({len(orphan_lines)}) ---")
        print("このページ単独では親を確定できないテキスト（前ページからの継続の可能性）:")
        for text in orphan_lines:
            print(f"  {text!r}")

    print("\n--- tree ---")
    print(structure.format_tree(roots))


if __name__ == "__main__":
    main()
