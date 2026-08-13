"""行の組み立て、階層記号の抽出、木の構築（SPEC 2.3）。

処理順序（CLAUDE.md 表 #3, #4, #5）:
  3. 行の組み立て
  4. 階層記号の抽出
  5. 木の構築
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# 段の順序（SPEC 2.3）。この順序でのみ親子関係を判定する。x座標は根拠にしない。
MARKER_TYPES = [
    "roman",
    "paren_roman",
    "number",
    "paren_number",
    "alpha",
    "paren_alpha",
    "kana",
    "paren_kana",
]
RANK = {t: i for i, t in enumerate(MARKER_TYPES)}

_ROMAN = "ⅠⅡⅢⅣⅤⅥⅦ"
_KANA = "ァ-ヺ"  # 全角カタカナ

MARKER_PATTERNS = {
    "roman": re.compile(f"[{_ROMAN}]"),
    "paren_roman": re.compile(f"\\([{_ROMAN}]\\)"),
    "number": re.compile(r"[０-９]+"),
    "paren_number": re.compile(r"\([０-９]+\)"),
    "alpha": re.compile(r"[ａ-ｚ]"),
    "paren_alpha": re.compile(r"\([a-z]\)"),
    "kana": re.compile(f"[{_KANA}]"),
    "paren_kana": re.compile(f"\\([{_KANA}]\\)"),
}

_FULLWIDTH_TO_HALF = {}
for _i in range(10):
    _FULLWIDTH_TO_HALF[ord("０") + _i] = ord("0") + _i
for _i in range(26):
    _FULLWIDTH_TO_HALF[ord("ａ") + _i] = ord("a") + _i
    _FULLWIDTH_TO_HALF[ord("Ａ") + _i] = ord("A") + _i


@dataclass
class Line:
    top: float
    bottom: float
    text: str


@dataclass
class Node:
    marker_type: str
    marker: str
    marker_norm: str
    text_raw: str = ""
    parent: "Node | None" = None
    children: list = field(default_factory=list)
    seq: int = 0
    depth: int = 0


def assemble_lines(chars: list) -> list:
    """文字要素をy方向の重なりでグループ化して行を組み立てる。

    厳密な等値ではなく重なり判定を使うのは、1pt程度ずれて取得される行が
    存在するため（CLAUDE.md「〔例〕の1文目は同じ行にある」）。
    x座標による領域分割（波括弧対応）はp200では不要なため未実装。
    """
    buckets: list = []  # each: {"top", "bottom", "chars": [...]}
    for c in sorted(chars, key=lambda c: (c.top, c.x0)):
        target = None
        for b in buckets:
            height = min(c.bottom - c.top, b["bottom"] - b["top"])
            overlap = min(c.bottom, b["bottom"]) - max(c.top, b["top"])
            if height > 0 and overlap / height >= 0.5:
                target = b
                break
        if target is None:
            buckets.append({"top": c.top, "bottom": c.bottom, "chars": [c]})
        else:
            target["chars"].append(c)
            target["top"] = min(target["top"], c.top)
            target["bottom"] = max(target["bottom"], c.bottom)

    lines = []
    for b in sorted(buckets, key=lambda b: b["top"]):
        ordered = sorted(b["chars"], key=lambda c: c.x0)
        text = "".join(c.text for c in ordered)
        lines.append(Line(top=b["top"], bottom=b["bottom"], text=text))
    return lines


def normalize_marker(marker_type: str, raw: str) -> str:
    """marker_norm を得る（SPEC 2.10 / 2.4）。ローマ数字・カタカナは全角のまま、英数字は半角。"""
    if marker_type == "alpha" or marker_type == "number":
        return raw.translate(_FULLWIDTH_TO_HALF)
    if marker_type == "paren_number":
        return "(" + raw[1:-1].translate(_FULLWIDTH_TO_HALF) + ")"
    return raw


def parse_marker_chain(text: str) -> tuple:
    """行頭から連続する階層記号をすべて取り出す（SPEC 2.3.2）。

    行頭から記号を1つだけ取る処理をしてはならない、という制約に対応する。
    戻り値: (markers, remainder)。markers は (marker_type, raw) のリスト。

    最後の記号の直後が空白（または行末）であることを条件とする。実測データでは
    本物の階層記号は必ず本文との間に空白を1つ挟む（SPEC 2.10「階層記号と本文の
    間の空白は除去しない」）。この条件がないと、行をまたいだ参照の折り返し
    （例：p200 の「((２)－５図)」が「((２)－」「５図)」に分割される）で、
    継続行の先頭が偶然「５」のような数字段の記号と一致し、誤って新しい
    ノードとして検出されてしまう。
    """
    pos = 0
    markers = []
    while True:
        matched = None
        for mtype in MARKER_TYPES:
            m = MARKER_PATTERNS[mtype].match(text, pos)
            if m:
                matched = (mtype, m.group(0), m.end())
                break
        if matched is None:
            break
        mtype, raw, end = matched
        markers.append((mtype, raw))
        pos = end

    if not markers:
        return [], text

    remainder = text[pos:]
    if remainder and not remainder[0].isspace():
        # 記号の直後に空白がない = 本物の階層記号ではない可能性が高い。
        return [], text

    return markers, remainder


def build_forest(lines: list) -> tuple:
    """行の列から木を構築する（SPEC 2.3.1 段の順序、2.3.2 同一行併記）。

    段の省略・非隣接段の併記があっても、段の順序のみで親子を判定する
    （x座標・記号の個数や隣接性を前提としない）。

    戻り値: (roots, orphan_lines)。orphan_lines は、最初の階層記号が
    出現する前に存在した行（このページ単独では所属先を確定できないテキスト）。
    確定できないため木には含めず、そのまま報告する（CLAUDE.md 原則5）。
    """
    roots: list = []
    stack: list = []
    orphan_lines: list = []
    current_leaf: Node | None = None

    for line in lines:
        if not line.text.strip():
            # 空白のみの行（枠外にはみ出した文字の空白等）は内容を持たないため無視する。
            continue

        markers, remainder = parse_marker_chain(line.text)

        if not markers:
            if current_leaf is None:
                orphan_lines.append(line.text)
            else:
                # 行の折り返しによる改行は除去し、一続きの文にする（SPEC 2.10）。
                current_leaf.text_raw += line.text
            continue

        for mtype, raw in markers:
            rank = RANK[mtype]
            while stack and RANK[stack[-1].marker_type] >= rank:
                stack.pop()
            parent = stack[-1] if stack else None
            node = Node(
                marker_type=mtype,
                marker=raw,
                marker_norm=normalize_marker(mtype, raw),
                parent=parent,
                depth=(parent.depth + 1 if parent else 0),
            )
            if parent is not None:
                node.seq = len(parent.children) + 1
                parent.children.append(node)
            else:
                node.seq = len(roots) + 1
                roots.append(node)
            stack.append(node)
            current_leaf = node

        # 記号列の直後（最後の記号の子ノード）に本文が続く。
        # 階層記号と本文の間の空白は除去しない（SPEC 2.10）。
        current_leaf.text_raw += remainder

    return roots, orphan_lines


def format_tree(nodes: list, indent: int = 0) -> str:
    out = []
    for node in nodes:
        prefix = "  " * indent
        out.append(f"{prefix}[{node.marker_type}] {node.marker} (seq={node.seq}, depth={node.depth})")
        if node.text_raw:
            out.append(f"{prefix}    text_raw: {node.text_raw!r}")
        out.append(format_tree(node.children, indent + 1))
    return "\n".join(x for x in out if x)
