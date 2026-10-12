#!/usr/bin/env python3
"""clone 先に合わせたユーザーユニットを生成する。登録・起動は行わない。"""

import argparse
import re
import socket
import sys
from pathlib import Path


def unit_path(value, *, command=False):
    """WorkingDirectory と ExecStart の異なるパス構文に合わせる。"""
    value = str(value)
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("パスに制御文字は使用できません")
    value = value.replace("%", "%%")
    if command:
        value = value.replace("\\", "\\\\").replace('"', '\\"')
        # : は ExecStart の環境変数展開を無効化する接頭辞。
        return f'":{value}"'
    # WorkingDirectory は引用符を除去しない。末尾の空白や \ にも対応する。
    return value.rstrip("/") + "/"


def choose_port(port=None):
    """ビューアと同じ IPv4 全インターフェースで空きを確認する。"""
    if port == 0:
        return 0  # 起動時に OS が確保する動的ポート
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("0.0.0.0", port if port is not None else 0))
        return probe.getsockname()[1]


def generate_units(root, python, port):
    values = {
        "WORKING_DIRECTORY": unit_path(root),
        "PYTHON": unit_path(python, command=True),
        "PORT": str(port),
    }
    rendered = {}
    for name in ("netwatch.service", "netwatch_view.service"):
        template = (root / "systemd" / name).read_text(encoding="utf-8")
        rendered[name] = re.sub(
            r"@(WORKING_DIRECTORY|PYTHON|PORT)@",
            lambda match: values[match[1]],
            template,
        )
    output = root / "systemd" / "generated"
    output.mkdir(exist_ok=True)
    for name, content in rendered.items():
        (output / name).write_text(content, encoding="utf-8")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--port",
        type=int,
        help="固定ポート（省略時は空きを選択、0ならサービス起動ごとに自動割り当て）",
    )
    args = parser.parse_args()
    if args.port is not None and not 0 <= args.port <= 65535:
        parser.error("--port は 0〜65535 で指定してください")
    root = Path(__file__).resolve().parent.parent
    try:
        port = choose_port(args.port)
        # resolve() すると venv 内のシンボリックリンクを辿るため、そのまま使う。
        output = generate_units(root, Path(sys.executable).absolute(), port)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"設定を生成できませんでした: {exc}\n")
    print(f"生成先: {output}")
    print(f"ビューアのポート: {port if port else '起動時に自動割り当て'}")
    print("登録・起動は README の手順で実行してください。")


if __name__ == "__main__":
    main()
