"""SessionStart フック: 現在のリポジトリ状態と直近の構築結果を文脈に入れる。

CLAUDE.md には変わらない規約を書く。ここに入れるのは、実行のたびに変わる
事実だけである（ブランチ、未コミットの変更、DBの状態、検証の件数）。

出力は事実の記述にとどめる。命令文にすると Claude のプロンプトインジェクション
防御が働き、文脈ではなく警告として扱われる。

【設計上の注意】
SPEC.md に定義のあるテーブル・カラムのみを参照する。「所属が確定不能な図表」
のように CLAUDE.md が指標として挙げていても、その記録先が SPEC.md に
定義されていないものは、こちらで記録方法を推測してはならない。
そうした指標は validation_issues の kind 別集計として自然に現れる。
"""

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def project_root(payload: dict) -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        try:
            return Path(env).resolve()
        except OSError:
            pass
    return Path(payload.get("cwd") or ".").resolve()


def git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def db_summary(db_path: Path) -> list[str]:
    if not db_path.is_file():
        return ["data/index.db は未生成である。"]

    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return ["data/index.db は存在するが読み取りに失敗した。"]

    lines: list[str] = []
    try:
        cur = con.cursor()

        def scalar(sql: str):
            try:
                row = cur.execute(sql).fetchone()
                return row[0] if row else None
            except sqlite3.Error:
                return None

        def rows(sql: str):
            try:
                return cur.execute(sql).fetchall()
            except sqlite3.Error:
                return []

        # SPEC 3.2 / 3.11 / 3.13 に定義のあるテーブルのみを参照する
        checks = (
            ("nodes の件数", "SELECT COUNT(*) FROM nodes"),
            (
                "解決先を持たない参照の件数",
                "SELECT COUNT(*) FROM refs r "
                "WHERE NOT EXISTS (SELECT 1 FROM ref_targets t WHERE t.ref_id = r.id)",
            ),
            (
                "引き継ぎで段を補完した参照の件数",
                "SELECT COUNT(*) FROM refs WHERE inherited_from IS NOT NULL",
            ),
            (
                "confidence が high でない注の件数",
                "SELECT COUNT(*) FROM notes WHERE confidence <> 'high'",
            ),
        )
        for label, sql in checks:
            value = scalar(sql)
            if value is not None:
                lines.append(f"{label}は {value} である。")

        # 検証結果は severity 別・kind 別に集計する。
        # どの kind が存在するかは実装が決めるため、こちらで列挙しない
        severities = rows(
            "SELECT severity, COUNT(*) FROM validation_issues GROUP BY severity"
        )
        if severities:
            summary = "、".join(f"{s}={n}" for s, n in severities)
            lines.append(f"validation_issues の内訳は {summary} である。")

        kinds = rows(
            "SELECT kind, COUNT(*) FROM validation_issues "
            "WHERE severity = 'error' GROUP BY kind ORDER BY COUNT(*) DESC LIMIT 8"
        )
        if kinds:
            summary = "、".join(f"{k}={n}" for k, n in kinds)
            lines.append(f"error の kind 別内訳は {summary} である。")

        # SPEC 3.14 に定義のあるキーのみ
        for key, label in (
            ("built_at", "最後の構築日時"),
            ("revision", "底本の改正次数"),
            ("parser_version", "パーサーのバージョン"),
            ("unresolved_ref_count", "build_info が記録する未解決参照の件数"),
            ("multipage_node_count", "ページをまたぐノードの件数"),
        ):
            value = scalar(f"SELECT value FROM build_info WHERE key = '{key}'")
            if value is not None and value != "":
                lines.append(f"{label}は {value} である。")
    finally:
        con.close()

    return lines or ["data/index.db は存在するが、想定するテーブルが見つからない。"]


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        payload = {}

    root = project_root(payload)
    lines: list[str] = []

    branch = git(root, "branch", "--show-current")
    if branch:
        lines.append(f"現在のブランチは {branch} である。")

    status = git(root, "status", "--short")
    if status:
        changed = status.splitlines()
        shown = changed[:15]
        lines.append("未コミットの変更がある: " + ", ".join(s.strip() for s in shown))
        if len(changed) > len(shown):
            lines.append(f"ほかに {len(changed) - len(shown)} 件の変更がある。")
    else:
        lines.append("作業ツリーはクリーンである。")

    last_commit = git(root, "log", "-1", "--format=%h %s")
    if last_commit:
        lines.append(f"直近のコミットは {last_commit} である。")

    lines.extend(db_summary(root / "data" / "index.db"))

    out_dir = root / "out"
    if (out_dir / "report.html").is_file():
        lines.append("out/report.html が存在し、前回の検証結果を確認できる。")
    if out_dir.is_dir():
        sheets = list(out_dir.glob("*contact*"))
        if sheets:
            lines.append(
                f"out/ にコンタクトシートが {len(sheets)} 件あり、"
                "図表検出の目視確認に使える。"
            )

    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": "\n".join(lines),
            }
        },
        sys.stdout,
        ensure_ascii=False,
    )


if __name__ == "__main__":
    main()
