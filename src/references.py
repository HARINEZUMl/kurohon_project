"""参照の解決（SPEC 2.12）。

処理順序（CLAUDE.md 表 #8, #9）:
  8. 参照の解決  ← 本モジュールが扱う
  9. 図表の所属確定  ← 未実装。参照解決の結果を使って別途行う

SPEC 2.12 は本プロジェクトで最も複雑な規則である。DECISIONS.md D010 とその
追記（引き継ぎ、子孫判定、範囲指定）、および D021・D022・D023（正規化テキスト
からの抽出への切り替えに伴う判断）、D024（ref_text をtext_rawから復元する
対応）を読んでから変更すること。

**text_norm から抽出する（SPEC 2.12 の要請どおり）**

参照は `node.text_norm`（SPEC 2.10）から抽出する。以前は `text_raw` から
直接抽出していた（text_norm 未実装だったため）が、SPEC 2.10 の正規化に伴い
数字・英字が半角化されるため、階層記号を走査する正規表現も
`structure.MARKER_PATTERNS`（原本表記＝text_raw用）ではなく
`structure.MARKER_PATTERNS_NORM`（正規化後の表記用）を使う必要がある。

この切り替えにより、D021（誤検出防止規則）が引き続き必要かどうかを実データで
再検証した。結論と新たに見つかった誤検出パターンは D021 の追記を参照。

**ref_text は text_raw から復元する（SPEC 3.11。D024）**

`refs.ref_text` は「原本での参照表記」（必須）と定義されており、正規化済み
表記であってはならない。抽出・境界判定は text_norm 上で行うが、
`Ref.ref_text` を構築する最後の一歩では、抽出した span を
`structure.map_norm_span_to_raw`（node.text_norm_offsets を使う）で
text_raw 上の span に変換し、そこから切り出す。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from structure import MARKER_PATTERNS_NORM, MARKER_TYPES, RANK, map_norm_span_to_raw, normalize_marker
from figures import normalize_figure_number

# 素のkana型（paren_kanaではない単独の全角カタカナ1文字）の直後に、さらに
# 全角カタカナが続く場合、それは階層記号ではなく通常のカタカナ語の一部で
# ある可能性が高い（実測：「３スキャン」の「ス」がkana型と誤って一致する）。
# 本物のkana記号は単独の1文字であり、単語の一部として現れることはない。
#
# 直前の文字についても同じ判定を行う（D021追記2）。「メートル、5,000メートル」
# の末尾「ル」は直後が「、」のため上記の前方チェックをすり抜けるが、直前が
# 「ト」（カタカナ）であり、単語の末尾文字にすぎないことが分かる。text_norm
# 化により直後の「5」が半角数字として一致するようになったことで、この
# 「ル、5」という見せかけの参照が実際に出現した（p88実測）。
_KANA_CONTINUATION = re.compile(r"[ァ-ヺー]")

# 素のalpha型（paren_alphaではない単独の半角英字1文字）の前後に、さらに
# 半角の小文字英字が続く場合、それは階層記号ではなく英単語の一部である
# （実測：p80「700feet」の「f」がalpha型と誤って一致し、直前の数字「700」と
# number→alpha の段順で連結され「700f」という見せかけの参照になる）。
# text_raw の時点ではalpha型は全角のみに一致していたため英文中の半角英字と
# 衝突しなかったが、text_norm では英数字が半角に統一されるためこの区別が
# 失われる（D021追記2）。kana型と同じ理由で前方・後方の両方を確認する。
#
# 大文字は対象に含めない。p75実測：「(３)ATISａ(a)アからオ」のａ（本物の
# 階層記号）の直前は大文字の頭字語「ATIS」の末尾「S」であり、大文字→小文字の
# 切り替わり自体が語の境界を示す（同じ単語の続きではない）。大文字も対象に
# 含めると、この本物の参照の先頭セグメントを誤って除外してしまう。
_ALPHA_CONTINUATION = re.compile(r"[a-z]")


def _has_continuation(text: str, mtype: str, start: int, end: int) -> bool:
    """matched span [start, end) の前後に、同じ文字種の続きがあるかを見る。

    kana・alpha のみが対象（本文中の通常の語の一部として現れうる種別）。
    それ以外（roman・数字の連続・かっこ付き記号）はそもそも regex 自体が
    連続分をまとめて取り込むか、かっこで区切られているため対象外。
    """
    if mtype == "kana":
        pattern = _KANA_CONTINUATION
    elif mtype == "alpha":
        pattern = _ALPHA_CONTINUATION
    else:
        return False
    if pattern.match(text, end):
        return True
    if start > 0 and pattern.match(text, start - 1):
        return True
    return False

# 第7段の階層記号（ア、イ、ウ…）はいろは・五十音順の通常サイズの文字のみで
# あり、拗音・促音・単位の助数詞に使う小書きカタカナ（「ヵ月」「１ヶ所」等）
# が段記号として使われることはない。structure.MARKER_PATTERNS の kana は
# 範囲指定（ァ-ヺ）で小書き文字も含めてしまうため、参照抽出ではここで除外する
# （実測：「１ヵ月」の「ヵ」がkana型と誤って一致する）。
_SMALL_KANA = set("ァィゥェォッャュョヮヵヶ")

# 参照の境界となる接続表現（SPEC 2.12）。「から」は境界ではなく範囲指定。
BOUNDARY_WORDS = ["及び", "又は", "若しくは", "並びに", "、"]
RANGE_WORD = "から"
_CONNECTIVES = BOUNDARY_WORDS + [RANGE_WORD]

# 図参照（例：((２)－４図)）。内側の "(２)－４" が図番号（figures.Node.number）
# と同じ表記。捕捉したうえで figures.normalize_figure_number で正規化し、
# FIGUREノードの number_norm と突き合わせる。
_FIGURE_REF_PATTERN_INNER = "図)"


def _match_marker_at(text: str, pos: int):
    """posの位置から階層記号1つを取り出す。一致しなければNone。

    structure.MARKER_PATTERNS_NORM を使う。text は node.text_norm（SPEC 2.10
    適用済み）であり、数字・英字は半角化されている。戻り値:
    (marker_type, raw, end) | None。
    """
    for mtype in MARKER_TYPES:
        m = MARKER_PATTERNS_NORM[mtype].match(text, pos)
        if m:
            return mtype, m.group(0), m.end()
    return None


def _match_chain_at(text: str, pos: int):
    """posから始まる、隙間なく連続する階層記号列を貪欲に取り出す。

    structure.parse_marker_chain と異なり、行頭であることや記号直後の空白を
    要求しない（本文中のどの位置にも現れうるため）。そのかわり、SPEC 2.3.2の
    同一行併記が常に「親の段→子の段」という段の昇順で並ぶという性質を
    利用し、段の順位（RANK）が狭義単調増加でなくなった時点でチェーンを
    打ち切る。

    この制約が無いと、カタカナが連続する通常の語（「ポイントアウト」
    「アプローチ」等）が、1文字ずつ独立に kana 型として一致し続け、
    見せかけの多段チェーンとして誤検出される（実測で確認）。段の昇順制約は
    本物の同一行併記（例："ｂ(a)ア"）を排除しない。

    戻り値: (segments, end) | (None, pos)。segments は [(marker_type, marker_norm), ...]。
    """
    segments = []
    p = pos
    prev_rank = -1
    while True:
        matched = _match_marker_at(text, p)
        if matched is None:
            break
        mtype, raw, end = matched
        if mtype == "kana" and raw in _SMALL_KANA:
            break
        if _has_continuation(text, mtype, p, end):
            break
        if RANK[mtype] <= prev_rank:
            break
        segments.append((mtype, normalize_marker(mtype, raw)))
        prev_rank = RANK[mtype]
        p = end
    if not segments:
        return None, pos
    return segments, p


def _find_figure_refs(text: str):
    """図参照 ((2)-4図) を検出する。戻り値: [(start, end, number_norm), ...]。

    text は node.text_norm。SPEC 2.10 の半角化に加え、区切りのハイフンも
    text_norm 生成時点で半角化済み（DECISIONS.md D022）であるため、原本の
    "((２)－４図)" は "((2)-4図)" という形で現れる。
    """
    results = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] != "(":
            i += 1
            continue
        # "(" の直後がさらに "(" で始まる図番号でなければ図参照ではない。
        if not text.startswith("((", i):
            i += 1
            continue
        # 図番号本体 "(2)-4" を _match_chain_at 相当ではなく専用に走査する。
        # 図番号は paren_number の直後に半角ハイフンと算用数字が続く形。
        j = i + 1
        paren_num = MARKER_PATTERNS_NORM["paren_number"].match(text, j)
        if not paren_num:
            i += 1
            continue
        j = paren_num.end()
        if not text.startswith("-", j):
            i += 1
            continue
        j += 1
        k = j
        while k < n and "0" <= text[k] <= "9":
            k += 1
        if k == j:
            i += 1
            continue
        digits_end = k
        if not text.startswith(_FIGURE_REF_PATTERN_INNER, digits_end):
            i += 1
            continue
        end = digits_end + len(_FIGURE_REF_PATTERN_INNER)
        raw_number = text[i + 1:digits_end]  # "(2)-4"
        # 比較は必ず figures.normalize_figure_number 経由で行う（両側を同じ
        # 関数に通す。DECISIONS.md D022）。text_norm は既に半角化済みのため
        # ここでは実質的に恒等変換だが、将来どちらかの正規化規則が変わっても
        # 突き合わせが分散しないようにするため呼び出しを残す。
        results.append((i, end, normalize_figure_number(raw_number)))
        i = end
    return results


def _find_table_refs(text: str):
    """表参照「次表」を検出する。戻り値: [(start, end), ...]。

    「次表」は識別番号を持たないため、どのTABLEノードを指すかは紙面上の
    位置関係（次に印刷される表）に依存する。その判定は図表の所属確定
    （stage9）の領域であり、本モジュールでは解決先を持たない参照として
    記録するにとどめる（CLAUDE.md原則5：確定できないものを確定させない）。
    """
    results = []
    start = 0
    while True:
        idx = text.find("次表", start)
        if idx == -1:
            break
        results.append((idx, idx + 2))
        start = idx + 2
    return results


def _mask_spans(text: str, spans: list) -> str:
    """spans（[(start, end), ...]）の範囲を空白で置き換える。

    後続の条項参照走査が図表参照の内部を誤って階層記号列として拾わないよう
    にするための前処理。位置がずれないよう、置換後も同じ長さを保つ。
    """
    chars = list(text)
    for s, e in spans:
        for i in range(s, e):
            chars[i] = " "
    return "".join(chars)


def _extract_section_spans(text: str):
    """条項参照の候補となる範囲を走査する。

    戻り値: [(start, end, chains, connectors, chain_spans), ...]。
    chains は [[(marker_type, marker_norm), ...], ...]、connectors は
    chains の間を埋める接続表現の列（len(chains)-1件）。chain_spans は
    chains と対になる [(chain_start, chain_end), ...]（text上の位置。
    _split_section_refs が個々の参照のspanを組み立てる際に使う。SPEC 3.11
    対応でref_textをtext_rawから復元するために必要）。

    採用条件（SPEC 2.12「参照の境界」「範囲指定」の実例から導いた最小限の
    判定基準）:
    - 接続表現（及び/又は/若しくは/並びに/、/から）で2つ以上の記号列が
      連結されている（例："(a)及び(b)"、"(a)から(c)"）
    - または、単独の記号列であっても2つ以上の段を含む
      （例："(Ⅰ)２(５)"、"５(２)ｃ(b)"）

    単独・1段のみの記号列（例：本文中に単発で現れる "ａ"）は、階層記号の
    偶然の一致である可能性を排除できないため参照として採用しない。SPEC
    自身が示す実例（CLAUDE.md・DECISIONS.md所載）はいずれもこの2条件の
    どちらかを満たす。判定基準を緩めて単独1段も参照とみなすかどうかは、
    実例が見つかった時点で改めて判断する（CLAUDE.md原則4）。
    """
    spans = []
    i = 0
    n = len(text)
    while i < n:
        segments, end = _match_chain_at(text, i)
        if segments is None:
            i += 1
            continue
        chains = [segments]
        connectors = []
        chain_spans = [(i, end)]
        pos = end
        while True:
            matched_conn = None
            for w in _CONNECTIVES:
                if text.startswith(w, pos):
                    matched_conn = w
                    break
            if matched_conn is None:
                break
            next_start = pos + len(matched_conn)
            next_segments, next_end = _match_chain_at(text, next_start)
            if next_segments is None:
                break
            if matched_conn == RANGE_WORD and chains[-1][-1][0] != next_segments[-1][0]:
                # SPEC 2.12「からの前後は同じ段の記号でなければならない」。
                # 型が一致しない場合はこの「から」を境界として認めない
                # （「サイトから１海里」のように、「から」の前後にたまたま
                # 記号らしき文字が来る通常の文を誤検出しないため）。
                break
            connectors.append(matched_conn)
            chains.append(next_segments)
            chain_spans.append((next_start, next_end))
            pos = next_end
        if len(chains) >= 2 or len(chains[0]) >= 2:
            spans.append((i, pos, chains, connectors, chain_spans))
            i = pos
        else:
            i += 1
    return spans


def _split_section_refs(chains: list, connectors: list, chain_spans: list) -> list:
    """接続表現で区切られた記号列群を、個々の参照に分割する（SPEC 2.12）。

    「から」は直前・直後の記号列を1つの範囲参照にまとめる。それ以外の
    接続表現（及び/又は/若しくは/並びに/、）は境界であり、記号列ごとに
    独立した参照とする。

    戻り値: [{"segments": [...], "range_to": [...] | None, "span": (start, end)}, ...]。
    span は text_norm（masked）上の、この参照1件分の位置。範囲参照
    （from...から...to）では from の開始から to の終了までを覆う。
    ref_text（SPEC 3.11）を text_raw から復元する際に使う。
    """
    refs = []
    pending = chains[0]
    pending_span = chain_spans[0]
    for connector, nxt, nxt_span in zip(connectors, chains[1:], chain_spans[1:]):
        if connector == RANGE_WORD:
            refs.append({"segments": pending, "range_to": nxt, "span": (pending_span[0], nxt_span[1])})
            pending = None
            pending_span = None
        else:
            if pending is not None:
                refs.append({"segments": pending, "range_to": None, "span": pending_span})
            pending = nxt
            pending_span = nxt_span
    if pending is not None:
        refs.append({"segments": pending, "range_to": None, "span": pending_span})
    return refs


def _node_path_segments(node) -> list:
    """ノード自身を含む祖先の階層記号列を、根から自身の順で返す。

    SPEC 2.4の論理パスと同じ考え方だが、ここでは表示用の文字列ではなく
    (marker_type, marker_norm) の列として保持する（セグメント種別ごとの
    照合に使うため）。marker_type が8種のいずれでもないノード（NOTE等）は
    自身の分をスキップし、祖先はそのまま辿る。
    """
    chain = []
    n = node
    while n is not None:
        if getattr(n, "marker_type", None) in RANK:
            chain.append((n.marker_type, n.marker_norm))
        n = n.parent
    chain.reverse()
    return chain


def _walk(base_node, roots: list, segments: list):
    """base_node（Noneならrootsの中）から、segmentsの列をたどって子孫を探す。

    各段で marker_type と marker_norm が一致する子ノードを探す。木の実際の
    親子関係（段の省略・併記を経て構築済み）をそのまま使うため、段が省略
    されていても正しくたどれる。

    戻り値: (到達ノード | None, 到達できたセグメント数)。
    """
    current = base_node
    for i, (mtype, norm) in enumerate(segments):
        pool = roots if current is None else current.children
        match = next((c for c in pool if getattr(c, "marker_type", None) == mtype and c.marker_norm == norm), None)
        if match is None:
            return None, i
        current = match
    return current, len(segments)


def _resolve_range(parent, marker_type: str, from_norm: str, to_norm: str) -> list:
    """parentの子のうち marker_type が一致するものの中から、from_normからto_normまでの範囲を返す。

    範囲は親ノードの実際の子の並び（seq順）に基づいて展開する（SPEC 2.12
    「範囲の展開には親ノードの子の一覧が必要」）。アルファベット等の機械的な
    連番生成ではなく、木に実在する子を照会する。
    """
    if parent is None:
        return []
    siblings = [c for c in parent.children if getattr(c, "marker_type", None) == marker_type]
    try:
        i0 = next(idx for idx, c in enumerate(siblings) if c.marker_norm == from_norm)
        i1 = next(idx for idx, c in enumerate(siblings) if c.marker_norm == to_norm)
    except StopIteration:
        return []
    if i0 > i1:
        return []
    return siblings[i0:i1 + 1]


def _segment_text(segments: list) -> str:
    return "".join(norm for _, norm in segments)


@dataclass
class Ref:
    """本文中の1件の参照（SPEC 2.12、3.11）。"""

    from_node: object
    seq_in_node: int
    ref_type: str  # "figure" | "table" | "section"
    ref_text: str
    is_range: bool = False
    is_descendant: bool = False
    inherited_from: "Ref | None" = None
    resolution_note: str = ""
    own_segments: list = field(default_factory=list)  # このrefが明示するセグメント（範囲ならfromの側）
    range_to_segments: list = field(default_factory=list)  # 範囲のto側。範囲でなければ空
    targets: list = field(default_factory=list)  # 解決できたノード。未解決なら空


def _resolve_section_ref(node, prior_refs: list, subref: dict, roots: list, ref_text: str) -> Ref:
    own_segments = subref["segments"]
    range_to = subref["range_to"]
    is_range = range_to is not None
    s_type = own_segments[0][0]

    self_path = _node_path_segments(node)
    self_types = {t for t, _ in self_path}
    is_descendant = s_type not in self_types

    notes = []
    inherited_from = None

    if is_descendant:
        base_node = node
        walk_path = own_segments
        notes.append("子孫への参照のため補完しない")
    else:
        base_node = None
        filled = []
        ok = True
        for mtype in MARKER_TYPES:
            if RANK[mtype] >= RANK[s_type]:
                break
            value = None
            source_ref = None
            for prior in reversed(prior_refs):
                seg = next((n for t, n in prior.own_segments if t == mtype), None)
                if seg is not None:
                    value = seg
                    source_ref = prior
                    break
            if value is None:
                self_seg = next((n for t, n in self_path if t == mtype), None)
                if self_seg is not None:
                    value = self_seg
            if value is None:
                notes.append(f"{mtype}=補完不能（先行参照にも参照元パスにも明示なし）")
                ok = False
                continue
            filled.append((mtype, value))
            if source_ref is not None:
                notes.append(f"{mtype}={value}(参照#{source_ref.seq_in_node}より引き継ぎ)")
                inherited_from = source_ref
            else:
                notes.append(f"{mtype}={value}(参照元より補完)")
        walk_path = (filled + own_segments) if ok else None

    targets = []
    if walk_path is not None:
        if is_range:
            parent_path = walk_path[:-1]
            if parent_path:
                parent, reached = _walk(base_node, roots, parent_path)
                if parent is None:
                    notes.append(f"範囲の親を辿る途中（{reached + 1}段目）で一致するノードが見つからない")
            else:
                parent = base_node
            range_type = own_segments[-1][0]
            if range_to[-1][0] != range_type:
                notes.append("範囲の前後で段の種類が一致しない")
            elif parent is not None:
                targets = _resolve_range(parent, range_type, own_segments[-1][1], range_to[-1][1])
                if not targets:
                    notes.append("範囲内に一致する子ノードが見つからない")
        else:
            result, reached = _walk(base_node, roots, walk_path)
            if result is not None:
                targets = [result]
            else:
                notes.append(f"{reached + 1}段目で一致するノードが見つからない")

    return Ref(
        from_node=node,
        seq_in_node=len(prior_refs) + 1,
        ref_type="section",
        ref_text=ref_text,
        is_range=is_range,
        is_descendant=is_descendant,
        inherited_from=inherited_from,
        resolution_note="; ".join(notes),
        own_segments=own_segments,
        range_to_segments=range_to or [],
        targets=targets,
    )


def resolve_references_for_node(node, roots: list, figure_table_nodes: list) -> list:
    """1ノードの本文（text_norm）から参照を抽出・解決する（SPEC 2.12）。

    参照は出現順に処理し、条項参照の段の補完は同一ノード内で先行する参照を
    逆順に走査して行う（引き継ぎ状態はノードをまたいでリセットされる。
    呼び出し側でノードごとに本関数を呼ぶことでこれを満たす）。
    """
    text = getattr(node, "text_norm", "") or ""
    if not text.strip():
        return []

    offsets = getattr(node, "text_norm_offsets", None) or []

    def raw_slice(start: int, end: int) -> str:
        # SPEC 3.11「ref_text は原本での参照表記」。抽出・境界判定は
        # text_norm 上で行うが、記録する ref_text は text_raw から
        # 復元する（DECISIONS.md D024）。
        raw_start, raw_end = map_norm_span_to_raw(offsets, start, end)
        return node.text_raw[raw_start:raw_end]

    figure_spans = _find_figure_refs(text)
    table_spans = _find_table_refs(text)
    mask = _mask_spans(text, [(s, e) for s, e, _ in figure_spans] + list(table_spans))

    events = []  # (start, kind, payload)
    for s, e, number_norm in figure_spans:
        events.append((s, e, "figure", number_norm))
    for s, e in table_spans:
        events.append((s, e, "table", None))
    for s, e, chains, connectors, chain_spans in _extract_section_spans(mask):
        events.append((s, e, "section", (chains, connectors, chain_spans)))
    events.sort(key=lambda ev: ev[0])

    refs: list = []
    for start, end, kind, payload in events:
        if kind == "figure":
            ref_text = raw_slice(start, end)
            target = next(
                (
                    n
                    for n in figure_table_nodes
                    if n.node_type == "FIGURE" and n.number_norm == payload
                ),
                None,
            )
            note = "" if target is not None else "同じ番号のFIGUREノードが見つからない"
            refs.append(
                Ref(
                    from_node=node,
                    seq_in_node=len(refs) + 1,
                    ref_type="figure",
                    ref_text=ref_text,
                    resolution_note=note,
                    targets=[target] if target is not None else [],
                )
            )
        elif kind == "table":
            ref_text = raw_slice(start, end)
            refs.append(
                Ref(
                    from_node=node,
                    seq_in_node=len(refs) + 1,
                    ref_type="table",
                    ref_text=ref_text,
                    resolution_note="「次表」は識別番号を持たないため未解決（所属確定=stage9の領域）",
                    targets=[],
                )
            )
        else:
            chains, connectors, chain_spans = payload
            for subref in _split_section_refs(chains, connectors, chain_spans):
                sub_text = raw_slice(*subref["span"])
                refs.append(_resolve_section_ref(node, refs, subref, roots, sub_text))

    return refs


def resolve_all(all_nodes: list, roots: list, figure_table_nodes: list) -> list:
    """処理対象の全ノードについて参照を解決する。

    all_nodes は structure.build_forest が返す、生成順（文書上の出現順）に
    並んだノードの列。ノードごとに独立して呼ぶことで、引き継ぎの範囲を
    「同一ノードの本文内」に閉じる（SPEC 2.12「引き継ぎの範囲」）。
    """
    refs = []
    for node in all_nodes:
        refs.extend(resolve_references_for_node(node, roots, figure_table_nodes))
    return refs


def format_refs(refs: list) -> str:
    out = []
    for ref in refs:
        path = getattr(ref.from_node, "marker", "") or ref.from_node.node_type
        target_desc = ", ".join(_describe_target(t) for t in ref.targets) if ref.targets else "未解決"
        range_desc = f"（範囲, from={_segment_text(ref.own_segments)} to={_segment_text(ref.range_to_segments)}）" if ref.is_range else ""
        out.append(
            f"[{path}] #{ref.seq_in_node} {ref.ref_type}:{ref.ref_text!r}{range_desc} "
            f"is_descendant={ref.is_descendant} -> {target_desc}"
        )
        if ref.resolution_note:
            out.append(f"    note: {ref.resolution_note}")
    return "\n".join(out)


def _describe_target(node) -> str:
    if node is None:
        return "?"
    if node.node_type == "FIGURE":
        return f"FIGURE {node.number}"
    if node.node_type == "TABLE":
        return "TABLE"
    return f"{node.marker_type}:{node.marker}"
