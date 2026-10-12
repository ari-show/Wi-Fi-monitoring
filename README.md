# Wi-Fi-monitoring

Linux / macOS 向けの回線監視ツール。ping と DNS 応答を計測し、層別の異常判定と CSV 記録を行います。

## セットアップ・実行

Python 3.14 と uv を使用します。実行時の Python 依存は標準ライブラリのみです。

```sh
uv sync --locked
uv run main.py --help
uv run main.py
```

## Linux: clone した端末でユーザーサービスとして使う

各端末で任意のディレクトリに clone し、そのリポジトリ内で以下を実行します。
自動デプロイは行いません。生成・登録・更新は端末ごとに手動で行います。

```sh
uv sync --locked
uv run python systemd/configure.py
```

`systemd/*.service` はテンプレートです。生成スクリプトは自身が置かれた clone 先の絶対パスを `WorkingDirectory` に、実行中の Python のパスを
`ExecStart` に設定し、`systemd/generated/` に2つのユニットを出力します。
別のカレントディレクトリから実行しても、その clone 先を使います。
生成ファイルには端末固有のパスが含まれるため Git 管理から除外しています。
CSV は clone 先に保存され、こちらも Git 管理から除外しています。

ポートを省略すると、その端末で現在空いている TCP ポートを OS に選ばせ、その番号をユニットに固定します。別の端末で同じ番号を使っていても競合しません。
空き確認後の予約は行わないため、起動までに他のプログラムが使用するとビューアは起動に失敗します。番号を固定したまま再生成する場合は、ビューアを停止してから `uv run python systemd/configure.py --port <番号>` を実行してください。省略して再生成すると、別の番号が選ばれることがあります。

毎回の起動時に空きポートを確保したい場合は、生成時に `--port 0` を指定できます。
この場合は再起動でポート番号が変わるため、起動ログで確認します。

生成した設定を確認し、Linux 上で登録します（`sudo systemctl` は使いません）。
テンプレートを直接コピーせず、`generated/` のファイルを使ってください。
既にシステムサービスとして登録している場合は、先に既存サービスを停止し、
二重起動を避けてください。

```sh
cat systemd/generated/*.service
systemd-analyze --user verify systemd/generated/*.service
mkdir -p ~/.config/systemd/user
cp systemd/generated/*.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now netwatch.service netwatch_view.service
systemctl --user status netwatch.service netwatch_view.service
```

ビューアは LAN から接続できるよう `0.0.0.0` で待ち受けます。
ブラウザでは `http://<監視端末のLAN IP>:<生成時に表示されたポート>/` を開きます。
LAN 内に限定するルーター・ファイアウォールの設定は端末側で管理してください。
ポート番号を変更した場合は、その設定も合わせます。実際に確保したポートは以下の起動ログでも確認できます。

```sh
journalctl --user -u netwatch_view.service -n 30 --no-pager
```

ログアウト後やログイン前も監視を続ける場合は、端末ごとに一度、`sudo loginctl enable-linger "$USER"` を実行します。

更新も手動です。`git pull` と `uv sync --locked` の後に、`systemctl --user restart netwatch.service netwatch_view.service` で再起動します。
ユニット設定を変更した場合は、再生成・再コピー・`daemon-reload` も行います。
clone 先を移動した場合や Python の配置が変わった場合も再生成が必要です。

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
