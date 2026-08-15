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

# references.py が正規化テキスト（text_norm）を走査する際に使う階層記号パターン
# （SPEC 2.12「参照は正規化テキストから抽出する」）。MARKER_PATTERNS は原本表記
# （text_raw）に対する定義であり、number・paren_number・alpha は全角のまま
# 一致させる。text_norm では SPEC 2.10 により数字・英字が半角化されているため、
# 同じ段でも一致させるべき文字クラスが異なる。roman・paren_roman・kana・
# paren_kana・paren_alpha は正規化で変化しない（ローマ数字・カタカナは全角の
# まま、alphaの括弧付き表記はもともと半角）ため元の定義を再利用する。
MARKER_PATTERNS_NORM = {
    "roman": MARKER_PATTERNS["roman"],
    "paren_roman": MARKER_PATTERNS["paren_roman"],
    "number": re.compile(r"[0-9]+"),
    "paren_number": re.compile(r"\([0-9]+\)"),
    "alpha": re.compile(r"[a-z]"),
    "paren_alpha": MARKER_PATTERNS["paren_alpha"],
    "kana": MARKER_PATTERNS["kana"],
    "paren_kana": MARKER_PATTERNS["paren_kana"],
}

# SPEC 2.10 の正規化規則では「全角英数字を半角に統一する」としか定められて
# おらず、ハイフンは英数字ではないため本文中では対象外に読める。しかし図参照
# 表記 ((２)－４図) の区切りに使われるハイフンを正規化しないと、検索や参照
# 抽出のたびに個別の変換が必要になる（DECISIONS.md D022）。対象は次の2文字：
#   U+FF0D（全角ハイフンマイナス、原本の図番号キャプションで使われる表記）
#   U+2010（ハイフン。p212の本文参照1件でのみ使用。キャプション側は常に
#           U+FF0D であり、この1件は原本の表記揺れと判断した）
# 変換しないもの：
#   U+30FC（カタカナ長音符「ー」）。ハイフンではなく、変換すると「レーダー」
#   等の通常の語を破壊する
_HYPHEN_LIKE_TO_HALF = {ord("－"): "-", ord("‐"): "-"}

# SPEC 2.10「均等割り付けの空白を除去する」。対象は漢字・全角カタカナの単独
# 文字が空白（半角/全角）で区切られている場合のみ（例：'総 則'→'総則'、
# '海 兵 隊'→'海兵隊'）。次のいずれかに該当する場合は対象としない：
#   - 隣接するトークンの少なくとも一方が2文字以上（例：'進入復行経路'と
#     '出発経路' のような通常の2語区切りを誤って結合しない）
#   - 隣接するトークンが漢字・全角カタカナ以外（英数字、記号、括弧類）
#     具体例（誤爆として確認済み・回帰テストの負例）：
#       'CLIMB / DESCEND'（ASCII文字と'/'）
#       'E / B'（ASCII文字と'/'）
#       '〔 〕 ：括弧内に…'（括弧・記号。〔〕：はいずれも漢字/カタカナの
#       Unicode範囲に含まれないため、このルールでは自動的に除外される）
# ひらがなは対象外とする（実例が見つかっていないため。CLAUDE.md原則4）。
_JUSTIFY_ELIGIBLE = re.compile(f"^[一-鿿{_KANA}]$")
_JUSTIFY_SPACE_RUN = re.compile(r"[ 　]+")


def _is_justify_eligible_singleton(token: str) -> bool:
    return len(token) == 1 and bool(_JUSTIFY_ELIGIBLE.match(token))


def _collapse_justified_spacing(text: str) -> str:
    """漢字・全角カタカナの単独文字が空白で区切られた列を詰める。

    空白の連続でテキストをトークン化し、隣接する2つの「原本のトークン」が
    両方とも「漢字/全角カタカナの1文字」である場合に限り、間の空白を除去
    する。トークン化により、2文字以上の語同士がたまたま空白1つで隣接する
    場合（通常この原本には存在しない）を誤って結合しない。

    直前トークンが単独文字かどうかは、常に「分割直後の元のトークン」で
    判定する（結合後の累積文字列の長さでは判定しない）。そうしないと
    「海 兵 隊」のような3つ以上の連鎖で、2文字目を結合した時点で累積が
    2文字になり、3文字目との結合を誤って拒否してしまう。
    """
    parts = _JUSTIFY_SPACE_RUN.split(text)
    seps = _JUSTIFY_SPACE_RUN.findall(text)
    if not seps:
        return text
    result = parts[0]
    prev_singleton = _is_justify_eligible_singleton(parts[0])
    for sep, nxt in zip(seps, parts[1:]):
        nxt_singleton = _is_justify_eligible_singleton(nxt)
        if prev_singleton and nxt_singleton:
            result += nxt  # 間の空白を落として結合する
        else:
            result += sep + nxt
        prev_singleton = nxt_singleton
    return result


def normalize_text(text: str) -> str:
    """本文の正規化テキスト（SPEC 2.10）を生成する。

    適用する規則：
      - 全角英数字を半角に統一する（ローマ数字・カタカナは対象外）
      - 図番号等の区切りに使われる全角ハイフン等（_HYPHEN_LIKE_TO_HALF）を
        半角に統一する（DECISIONS.md D022。SPEC本文には明記がない拡張）
      - 均等割り付けの空白を除去する（_collapse_justified_spacing）

    適用しない規則とその理由：
      - 「行の折り返しによる改行を除去し一続きの文にする」：text_raw は
        structure.build_forest の行組み立て時点で既に折り返しの改行を
        持たない一続きの文字列であるため、ここで行うことは無い（no-op）
      - 「段落の区切りとしての改行を保持する」：底本384ページ全体を走査した
        結果、ノード本文内部に段落区切りの実例が見つからなかったため、
        no-opとして実装する（DECISIONS.md D023）
      - 「階層記号と本文の間の空白を除去しない」：_collapse_justified_spacing
        はトークン単位で判定するため、先頭の空白（記号と本文の区切り）は
        対象にならない
    """
    result = text.translate(_FULLWIDTH_TO_HALF)
    result = result.translate(_HYPHEN_LIKE_TO_HALF)
    result = _collapse_justified_spacing(result)
    return result

# 【】見出し単独の行（SPEC 2.5）。行全体がこの形なら見出し行とみなし、
# 本文への連結対象から外す（属性としての分離はattributes.pyが行う）。
HEADING_LINE_PATTERN = re.compile(r"^【[^】]*】$")

# NOTE（SPEC 2.2, 3.10）。「注」に番号（全角数字）が続く場合がある。
# 階層記号と同様、実測データでは本文との間に実在の空白を1つ挟む。
NOTE_PATTERN = re.compile(r"注([０-９]+)?")

# PHRASE（SPEC 2.2, 2.8）。「★」に邦文が直接続く（間に空白を挟まない）。
# ｂ199実測: '★' と直後の文字の間に空白文字は無い（x座標が隙間なく連続）。
PHRASE_MARKER = "★"

# 図番号のキャプション（例: "(２)－１"）単独の行。図の本体（curve/rect）とは
# 別に、キャプション文字は本文の文字列として現れる。行全体がこの形なら
# キャプション行とみなし、本文への連結対象から外す（figures.pyが図ノードに
# 割り当てる）。本文中の参照表記「((２)－４図)」とは末尾の「図)」の有無で
# 区別され、混同しない。
CAPTION_LINE_PATTERN = re.compile(r"^\([０-９]+\)－[０-９]+$")


@dataclass
class Line:
    top: float
    bottom: float
    text: str
    physical_page: int = 0
    x0: float = 0.0


@dataclass
class Node:
    node_type: str = "SECTION"  # "SECTION" | "NOTE" | "PHRASE"
    marker_type: str = ""
    marker: str = ""
    marker_norm: str = ""
    text_raw: str = ""
    text_norm: str = ""  # SPEC 2.10。build_forest の末尾で text_raw から生成する
    parent: "Node | None" = None
    children: list = field(default_factory=list)
    seq: int = 0
    depth: int = 0
    # parent is None のノードにのみ意味を持つ。SPEC 2.1「第1段（roman）のみが根」
    # に一致するかどうか。False は「このページ単独処理では親を確定できなかった」
    # ことを意味し、真の根であると確定したわけではない（複数ページを跨いで処理
    # すれば親が見つかる可能性がある）。
    is_confirmed_root: bool = False
    # 木構築時の位置（この記号列の行の座標）。attributes.py が見出し・ラベルを
    # 「直後に現れるノード」として割り当てる際の手がかりに使う。
    physical_page: int = 0
    top: float = 0.0
    # 以下はstructure.py段階では常に空。attributes.pyが付与する（SPEC 2.5, 2.6）。
    heading: str = ""
    heading_norm: str = ""
    labels: list = field(default_factory=list)
    # NOTE専用（SPEC 3.10）。係り先の判定規則は未決（SPEC 2.16 U1）のため、
    # 現状はすべて confidence="unresolved", attached_to=None として記録する。
    confidence: str = ""
    indent_x: float = 0.0
    attached_to: "Node | None" = None
    # PHRASE専用（SPEC 2.8, 3.7）。邦文と英文の対。
    ja_raw: str = ""
    en_raw: str = ""
    # FIGURE専用（SPEC 3.5）。所属（parent）はfigures.py段階では未確定のまま
    # （SPEC 2.7「構築の順序」。所属確定は参照解決の後＝stage9）。
    number: str = ""
    number_norm: str = ""
    image_path: str = ""
    # TABLE専用（SPEC 3.6）。cells は行の配列の配列（JSON化前のPython値）。
    cells: list = field(default_factory=list)
    header_rows: int = 0
    # FIGURE/TABLE共通のページ内座標（bbox）。topは既存フィールドを流用する。
    x0: float = 0.0
    x1: float = 0.0
    bottom: float = 0.0


# 同じy位置にあっても、これ以上x方向に離れていれば別々の行（別の印刷要素）
# とみなす。実測した通常の字間・記号と本文の間隔は最大でも11pt程度であるのに
# 対し、p199の図キャプション2つ（同じtopでx=277.8と457.3）は179pt離れている。
LINE_SPLIT_GAP = 30.0


def assemble_lines(chars: list, physical_page: int = 0) -> list:
    """文字要素をy方向の重なりでグループ化して行を組み立てる。

    厳密な等値ではなく重なり判定を使うのは、1pt程度ずれて取得される行が
    存在するため（CLAUDE.md「〔例〕の1文目は同じ行にある」）。

    y方向でグループ化したのち、x方向に大きく離れた文字群はさらに別の行へ
    分割する。図のキャプションのように、同じy位置に印字されているが
    水平方向には無関係な複数の要素が存在するため（p199実測）。
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
        segment = [ordered[0]]
        for c in ordered[1:]:
            if c.x0 - segment[-1].x1 > LINE_SPLIT_GAP:
                text = "".join(ch.text for ch in segment)
                lines.append(
                    Line(top=b["top"], bottom=b["bottom"], text=text, physical_page=physical_page, x0=segment[0].x0)
                )
                segment = []
            segment.append(c)
        text = "".join(ch.text for ch in segment)
        lines.append(
            Line(top=b["top"], bottom=b["bottom"], text=text, physical_page=physical_page, x0=segment[0].x0)
        )
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

    SPEC 2.1「第1段（roman）のみが根」に対応するため、marker_type が
    roman である根と、それ以外の根を区別して返す。後者は、このページ単独
    処理では親が見つからなかっただけであり、真の根であると確定したわけでは
    ない（前ページに続きがある可能性がある）。両者を区別せずに扱うと、
    複数ページを統合した際に誤ったノードが根として混入する
    （CLAUDE.md 原則5：確定できないものを確定させない）。

    戻り値: (confirmed_roots, unconfirmed_roots, orphan_lines, pending_headings,
    pending_captions, all_nodes)。

    - orphan_lines: 最初の階層記号が出現する前に存在した行（このページ単独
      では所属先を確定できないテキスト）。確定できないため木には含めず、
      そのまま報告する。
    - pending_headings: 【】見出し単独の行（(physical_page, top, text) の
      タプル）。見出しはノードではなく属性のため（SPEC 2.2「種別に含めない
      もの」）、ここでは本文に連結せず脇に置くだけにとどめる。直後のノードへの
      割り当てと祖先解決はattributes.pyの役割（SPEC 2.5, CLAUDE.md属性の付与）。
    - pending_captions: 図番号キャプション単独の行（(physical_page, top, x0,
      text) のタプル）。図に対応付ける処理はfigures.pyの役割（stage7）。
    - all_nodes: 生成順（＝文書上の出現順）に並んだ全ノードのフラットな列。
      attributes.pyが見出し・ラベルを「直後に現れるノード」へ割り当てる際、
      木をたどらずに出現順で参照するために使う。

    NOTE（注・注N）とPHRASE（★）は階層記号を持たず、`stack`（SECTIONの
    段管理）には参加しない。現在開いている最も深いSECTION（stack[-1]）の
    子として、seqを共有しながら追加する（SPEC 2.4「兄弟内順序は紙面上の
    出現順」。図表の例（ア=1,イ=2,表=3,表=4）と同じ扱い）。

    PHRASEは邦文行（★で始まる）と、直後に続く英文行の対からなる（SPEC 2.8）。
    邦文はその行だけから取り、以降の非マーカー行は英文として連結する
    （英文が複数行に折り返す場合を扱うため）。邦文自体が複数行に折り返す
    実例は未確認であり、その場合は正しく扱えない（既知の限界）。
    """
    confirmed_roots: list = []
    unconfirmed_roots: list = []
    all_nodes: list = []
    stack: list = []
    orphan_lines: list = []
    pending_headings: list = []
    pending_captions: list = []
    current_leaf: Node | None = None
    current_field = "text_raw"  # current_leaf のどの属性に継続行を連結するか

    def append_child(node: Node) -> None:
        parent = stack[-1] if stack else None
        node.parent = parent
        node.depth = parent.depth + 1 if parent else 0
        node.seq = len(parent.children) + 1
        parent.children.append(node)
        all_nodes.append(node)

    for line in lines:
        if not line.text.strip():
            # 空白のみの行（枠外にはみ出した文字の空白等）は内容を持たないため無視する。
            continue

        heading_match = HEADING_LINE_PATTERN.match(line.text.strip())
        if heading_match:
            pending_headings.append((line.physical_page, line.top, line.text.strip()))
            continue

        if CAPTION_LINE_PATTERN.match(line.text.strip()):
            pending_captions.append((line.physical_page, line.top, line.x0, line.text.strip()))
            continue

        note_match = NOTE_PATTERN.match(line.text)
        note_remainder = line.text[note_match.end():] if note_match else None
        if note_match and (not note_remainder or note_remainder[0].isspace()):
            if not stack:
                # 所属先のSECTIONがこのページ範囲内に無い。確定できないため
                # 本文と同じ扱いで報告するにとどめる（CLAUDE.md 原則5）。
                orphan_lines.append(line.text)
                continue
            raw = note_match.group(0)
            node = Node(
                node_type="NOTE",
                marker_type="note",
                marker=raw,
                marker_norm=raw.translate(_FULLWIDTH_TO_HALF),
                physical_page=line.physical_page,
                top=line.top,
                indent_x=line.x0,
                # 係り先の判定規則は未決（SPEC 2.16 U1）。現状は判定を行わず、
                # 常にunresolvedとして記録する（SPEC 3.10の制約：attached_toが
                # 空のときconfidenceはunresolvedでなければならない）。
                confidence="unresolved",
            )
            append_child(node)
            node.text_raw += note_remainder
            current_leaf = node
            current_field = "text_raw"
            continue

        if line.text.startswith(PHRASE_MARKER):
            if not stack:
                orphan_lines.append(line.text)
                continue
            node = Node(node_type="PHRASE", physical_page=line.physical_page, top=line.top)
            node.ja_raw = line.text[len(PHRASE_MARKER):]
            append_child(node)
            current_leaf = node
            current_field = "en_raw"
            continue

        markers, remainder = parse_marker_chain(line.text)

        if not markers:
            if current_leaf is None:
                orphan_lines.append(line.text)
            elif current_field == "en_raw":
                current_leaf.en_raw += line.text
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
                physical_page=line.physical_page,
                top=line.top,
            )
            if parent is not None:
                node.seq = len(parent.children) + 1
                parent.children.append(node)
            else:
                node.is_confirmed_root = mtype == "roman"
                target = confirmed_roots if node.is_confirmed_root else unconfirmed_roots
                node.seq = len(target) + 1
                target.append(node)
            stack.append(node)
            current_leaf = node
            current_field = "text_raw"
            all_nodes.append(node)

        # 記号列の直後（最後の記号の子ノード）に本文が続く。
        # 階層記号と本文の間の空白は除去しない（SPEC 2.10）。
        current_leaf.text_raw += remainder

    for node in all_nodes:
        if node.text_raw:
            node.text_norm = normalize_text(node.text_raw)

    return confirmed_roots, unconfirmed_roots, orphan_lines, pending_headings, pending_captions, all_nodes


def format_tree(nodes: list, indent: int = 0) -> str:
    out = []
    for node in nodes:
        prefix = "  " * indent
        if node.node_type == "NOTE":
            out.append(f"{prefix}[NOTE] {node.marker} (seq={node.seq}, depth={node.depth})")
            out.append(f"{prefix}    confidence: {node.confidence!r} indent_x: {node.indent_x:.1f}")
            if node.text_raw:
                out.append(f"{prefix}    text_raw: {node.text_raw!r}")
                out.append(f"{prefix}    text_norm: {node.text_norm!r}")
        elif node.node_type == "PHRASE":
            out.append(f"{prefix}[PHRASE] (seq={node.seq}, depth={node.depth})")
            out.append(f"{prefix}    ja_raw: {node.ja_raw!r}")
            out.append(f"{prefix}    en_raw: {node.en_raw!r}")
        elif node.node_type == "FIGURE":
            out.append(f"{prefix}[FIGURE] {node.number} (seq={node.seq})")
            out.append(f"{prefix}    number_norm: {node.number_norm!r} image_path: {node.image_path!r}")
            out.append(
                f"{prefix}    page={node.physical_page} bbox=({node.x0:.1f},{node.top:.1f},{node.x1:.1f},{node.bottom:.1f})"
            )
        elif node.node_type == "TABLE":
            out.append(f"{prefix}[TABLE] (seq={node.seq}) header_rows={node.header_rows}")
            out.append(
                f"{prefix}    page={node.physical_page} bbox=({node.x0:.1f},{node.top:.1f},{node.x1:.1f},{node.bottom:.1f})"
            )
            for row in node.cells:
                out.append(f"{prefix}    {row!r}")
        else:
            out.append(f"{prefix}[{node.marker_type}] {node.marker} (seq={node.seq}, depth={node.depth})")
            if node.heading:
                out.append(f"{prefix}    heading: {node.heading!r} heading_norm: {node.heading_norm!r}")
            if node.labels:
                out.append(f"{prefix}    labels: {node.labels!r}")
            if node.text_raw:
                out.append(f"{prefix}    text_raw: {node.text_raw!r}")
                out.append(f"{prefix}    text_norm: {node.text_norm!r}")
        out.append(format_tree(node.children, indent + 1))
    return "\n".join(x for x in out if x)
