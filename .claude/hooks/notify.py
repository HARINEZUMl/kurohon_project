"""Notification フック: Claude Code が注意を要する状態になったら通知する。

席を離れている間にループが止まっていた、という事態を避けるためのもの。

フックには制御端末がないため /dev/tty へ直接書けない（Windows には
そもそも存在しない）。terminalSequence フィールドに載せると、Claude Code が
自身の端末書き込み経路から出力する。

OSC 9 は Windows Terminal / WezTerm / ConEmu / iTerm2 が解釈する。

【注記】通知種別を示す入力フィールド名は版により異なりうるため、複数の候補を
順に見て、いずれも取れない場合は message にそのまま落とす。message は
公式のサンプルで使われているフィールドである。
"""

import json
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

TYPE_FIELDS = ("notification_type", "notificationType", "matcher", "type")

LABELS = {
    "permission_prompt": "許可を待っています",
    "idle_prompt": "入力を待っています",
    "agent_needs_input": "サブエージェントが入力を待っています",
    "agent_completed": "サブエージェントが完了しました",
}


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        payload = {}

    kind = ""
    for field in TYPE_FIELDS:
        value = payload.get(field)
        if isinstance(value, str) and value:
            kind = value
            break

    body = LABELS.get(kind) or payload.get("message") or "確認してください"

    # 制御文字が本文に混ざると表示が壊れるため除去する
    body = "".join(ch for ch in str(body) if ch.isprintable())[:120]

    sequence = f"\033]9;黒本プロジェクト: {body}\007"

    json.dump({"terminalSequence": sequence}, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
