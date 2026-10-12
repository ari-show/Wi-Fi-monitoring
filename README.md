# Wi-Fi-monitoring

Linux / macOS 向けの回線監視ツール。ping と DNS 応答を計測し、層別の異常判定と CSV 記録を行います。

## セットアップ・実行

Python 3.14 と uv を使用します。実行時の Python 依存は標準ライブラリのみです。

```sh
uv sync --locked
uv run main.py --help
uv run main.py
```

## 開発

pytest と Ruff は dev 依存として管理し、`uv sync --locked` でインストールします。

```sh
# 単体テスト（実際の通信や待機は行わない）
uv run pytest

# コード整形
uv run ruff format .

# import 順などの静的チェック（自動修正には --fix を追加）
uv run ruff check .

# 整形済みかの確認（ファイルを書き換えない）
uv run ruff format --check .
```

テストではコマンド実行・ソケット・時刻をモックし、CSV は pytest の一時ディレクトリに書き込みます。
集計、判定の境界値、ICMP 非応答、DNS の失敗、CSV 世代管理、イベントの開始・終了を検証します。
実機での疎通や OS コマンドの利用可否は単体テストの対象外です。

mypy は未導入です。導入する場合は、集計結果などの辞書の型と関数の型注釈を整え、段階的にチェックを追加する方針です。
