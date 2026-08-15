"""PreToolUse フック: 編集してはならないファイルへの書き込みを拒否する。

CLAUDE.md の絶対ルール1（SPEC.md を編集しない）を、助言ではなく機械的な
強制として実装する。底本PDFの保護も兼ねる。

settings.json の permissions.deny でも同じ対象を拒否している。
確実な拒否は権限側が担い、本フックは拒否の理由を Claude に伝えて
次の行動（実装を直す／報告して止まる）を誘導する役割を持つ。

Windows のファイルシステムは大文字小文字を区別しないため、`spec.md` と
`SPEC.md` は同一のファイルを指す。照合も大文字小文字を無視して行う。
"""

import json
import os
import sys
from pathlib import Path

# 編集を禁止するファイル（プロジェクトルートからの相対パス、小文字で記述）
PROTECTED_FILES = {
    "spec.md",
}

# 編集を禁止するディレクトリ（小文字で記述）
PROTECTED_DIRS = {
    "pdfs",
}


def project_root(payload: dict) -> Path:
    """プロジェクトルートを得る。

    payload["cwd"] は「フック起動時のカレントディレクトリ」であり、
    Claude が cd した後だとプロジェクトルートと一致しない。
    Claude Code が注入する CLAUDE_PROJECT_DIR を優先する。
    """
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        try:
            return Path(env).resolve()
        except OSError:
            pass
    return Path(payload.get("cwd") or ".").resolve()


def deny(reason: str) -> None:
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        },
        sys.stdout,
        ensure_ascii=False,
    )
    sys.exit(0)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)  # 入力が読めない場合は判断しない

    tool_input = payload.get("tool_input") or {}
    raw_path = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not raw_path:
        sys.exit(0)

    root = project_root(payload)

    try:
        rel = Path(raw_path).resolve().relative_to(root)
    except (ValueError, OSError):
        # プロジェクト外、または解決不能。ファイル名のみで判定する（安全側）
        rel = Path(Path(raw_path).name)

    rel_lower = rel.as_posix().lower()
    first_part = rel.parts[0].lower() if rel.parts else ""

    if rel_lower in PROTECTED_FILES:
        deny(
            f"{rel.as_posix()} は編集禁止である（CLAUDE.md 絶対ルール1）。"
            "実装と仕様に相違がある場合は実装を修正すること。"
            "仕様側に誤りがあると考えるときは、変更せず報告して指示を待つこと。"
        )

    if first_part in PROTECTED_DIRS:
        deny(
            f"{rel.parts[0]}/ は底本の格納場所であり書き込み禁止である。"
            "抽出結果は out/ または data/ に出力すること。"
        )

    sys.exit(0)


if __name__ == "__main__":
    main()
