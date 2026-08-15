"""回帰テスト用の共通ヘルパー。pytest等の追加パッケージを要求しないよう、
標準ライブラリの unittest のみで完結させる。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pdfplumber  # noqa: E402

import attributes  # noqa: E402
import extract  # noqa: E402
import figures  # noqa: E402
import references  # noqa: E402
import structure  # noqa: E402

PDF_PATH = os.path.join(os.path.dirname(__file__), "..", "pdfs", "kurohon_148.pdf")


def build_pages(physical_pages: list):
    """指定した物理ページ群を連結して処理する（src/build.py の process_pages を
    簡略化したテスト用版）。戻り値は build.process_pages と同じ形の dict。
    """
    with pdfplumber.open(PDF_PATH) as pdf:
        extractions = []
        pages_by_number = {}
        all_lines = []
        box_regions = []
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

        attributes.assign_headings(all_nodes, pending_headings)
        attributes.assign_labels(all_nodes, box_regions)

        figure_table_nodes = []
        for extraction in extractions:
            physical_page = extraction.physical_page
            captions_on_page = [c for c in pending_captions if c[0] == physical_page]
            nodes, _unmatched = figures.build_page_nodes(
                pages_by_number[physical_page], physical_page, extraction.regions, captions_on_page
            )
            figure_table_nodes.extend(nodes)

        roots = confirmed_roots + unconfirmed_roots
        refs = references.resolve_all(all_nodes, roots, figure_table_nodes)

        return {
            "roots": roots,
            "all_nodes": all_nodes,
            "figure_table_nodes": figure_table_nodes,
            "refs": refs,
        }


def find_node(nodes: list, **attrs):
    """条件に一致する最初のノードを木全体（子孫含む）から探す。"""
    for node in nodes:
        if all(getattr(node, k, None) == v for k, v in attrs.items()):
            return node
        found = find_node(node.children, **attrs)
        if found is not None:
            return found
    return None
