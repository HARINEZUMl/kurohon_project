"""PostToolUse フック: src/ または tests/ の .py が編集されたら pytest を走らせる。

失敗した場合は exit 2 で終了する。PostToolUse における exit 2 はツール実行
そのものは止めないが、stderr の内容が Claude に渡る。これにより Claude は
自分の変更が回帰を起こしたことを即座に知り、人間の指摘を待たずに修正へ入れる。

【重要1】このフックは `py -3.12` で起動されるため、sys.executable は
システムの Python であって .venv の Python ではない。pytest は .venv に
入っているため、明示的に .venv の Python を探して使う必要がある。

【重要2】pytest が導入されていない環境で失敗を報告してはならない。
存在しないバグを Claude に追わせることになり、実装を無用に書き換えさせる。
"""

import json
import os
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

WATCHED_DIRS = ("src", "tests")
PYTEST_ARGS = ["-x", "-q", "--no-header", "--tb=short"]
MAX_OUTPUT_CHARS = 4000
PYTEST_TIMEOUT = 150

# pytest 自体が使えないことを示す出力。これらは実装の失敗ではない
NOT_INSTALLED_MARKERS = (
    "No module named pytest",
    "No module named `pytest`",
)


def project_root(payload: dict) -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        try:
            return Path(env).resolve()
        except OSError:
            pass
    return Path(payload.get("cwd") or ".").resolve()


def find_python(root: Path) -> str:
    """pytest が入っている Python を探す。.venv を優先する。"""
    candidates = (
        root / ".venv" / "Scripts" / "python.exe",  # Windows
        root / ".venv" / "bin" / "python",          # POSIX
    )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return sys.executable


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    tool_input = payload.get("tool_input") or {}
    raw_path = tool_input.get("file_path")
    if not raw_path or not raw_path.endswith(".py"):
        sys.exit(0)

    root = project_root(payload)

    try:
        rel = Path(raw_path).resolve().relative_to(root)
    except (ValueError, OSError):
        sys.exit(0)

    if not rel.parts or rel.parts[0] not in WATCHED_DIRS:
        sys.exit(0)

    tests_dir = root / "tests"
    if not tests_dir.is_dir() or not any(tests_dir.rglob("test_*.py")):
        sys.exit(0)  # 回帰テストがまだ無い段階。黙って通す

    python = find_python(root)

    try:
        result = subprocess.run(
            [python, "-m", "pytest", *PYTEST_ARGS],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=PYTEST_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        print(
            f"pytest が {PYTEST_TIMEOUT} 秒でタイムアウトした。"
            "無限ループ、または全ページ処理を含むテストの可能性がある。"
            "テストの対象を p200 のような単一ページに絞ること。",
            file=sys.stderr,
        )
        sys.exit(2)
    except OSError as exc:
        print(f"pytest を起動できなかった（{python}）: {exc}", file=sys.stderr)
        sys.exit(2)

    if result.returncode == 0:
        sys.exit(0)

    if result.returncode == 5:
        sys.exit(0)  # テストを1件も収集できなかった。失敗扱いにしない

    output = (result.stdout or "") + (result.stderr or "")

    # pytest 未導入は環境の問題であり、実装の失敗ではない。
    # ここで exit 2 を返すと、存在しないバグを Claude に追わせることになる。
    if any(marker in output for marker in NOT_INSTALLED_MARKERS):
        json.dump(
            {
                "systemMessage": (
                    f"pytest が {python} に導入されていないため、"
                    "自動テストを実行できませんでした。"
                    "pip install pytest で導入してください。"
                )
            },
            sys.stdout,
            ensure_ascii=False,
        )
        sys.exit(0)

    if len(output) > MAX_OUTPUT_CHARS:
        output = output[:MAX_OUTPUT_CHARS] + "\n…（以下省略）"

    print(
        f"{rel.as_posix()} の編集後、pytest が失敗した。\n"
        "検証を通すためにテスト側の判定を緩めてはならない"
        "（CLAUDE.md 絶対ルール3）。不一致の原因は実装にある。\n\n"
        f"{output}",
        file=sys.stderr,
    )
    sys.exit(2)


if __name__ == "__main__":
    main()
