# Wi-Fi-monitoring

Linux / macOS 向けの回線監視ツール。ping と DNS 応答を計測し、層別の異常判定と CSV 記録を行います。

## セットアップ

### 共通の準備（Linux / macOS）

1. **Python 3.14 と uv を用意し、任意の場所に clone する。**

   以降のコマンドは、clone したリポジトリのディレクトリ内で実行します。
   複数端末で使う場合は、端末ごとに作業してください。

2. **依存関係をインストールし、コマンドが動くことを確認する。**

   ```sh
   uv sync --locked
   uv run main.py --help
   ```

   **確認：** エラーなくヘルプが表示されること。実行時の Python 依存は標準ライブラリのみです。

3. **手動で監視画面を確認する。**

   ```sh
   uv run main.py
   ```

   **確認：** 監視画面に測定結果が表示されること。確認後は `Ctrl+C` で終了します。
   Linux で常駐させる場合は、次の手順に進みます。

### Linux：clone した端末でユーザーサービスを設定する

自動デプロイは行いません。以下の作業を各端末で手動で実施します。
サービス操作には `systemctl --user` を使います。
既にシステムサービスとして運用している場合は、既存サービスを停止・自動起動解除してから進めてください。

1. **端末に合わせた設定ファイルを生成する。**

   ```sh
   uv run python systemd/configure.py
   ```

   **確認：** 生成先 `systemd/generated/` と、ビューアのポート番号が表示されること。
   ポートは、その端末で現在空いている番号が選ばれ、設定に固定されます。

2. **clone 先と Python のパスを確認する。**

   ```sh
   cat systemd/generated/*.service
   ```

   **確認：** `WorkingDirectory` が clone 先の絶対パスになっていること。
   `ExecStart` が使用する Python を指し、監視側は `main.py`、表示側は `netwatch_view.py` を起動すること。

3. **systemd の設定として読み込めるか検証する。**

   ```sh
   systemd-analyze --user verify systemd/generated/*.service
   ```

   **確認：** エラーが出ないこと。エラーがあれば、登録前にパスや設定を修正します。

4. **ユーザーサービスの配置先へコピーし、設定を読み込む。**

   ```sh
   mkdir -p ~/.config/systemd/user
   cp systemd/generated/*.service ~/.config/systemd/user/
   systemctl --user daemon-reload
   ```

   **確認：** コマンドがエラーなく完了すること。
   `systemd/*.service` はテンプレートなので、コピーするのは必ず `generated/` 内のファイルです。

5. **自動起動を登録し、サービスを起動する。**

   ```sh
   systemctl --user enable --now netwatch.service netwatch_view.service
   systemctl --user is-enabled netwatch.service netwatch_view.service
   systemctl --user status netwatch.service netwatch_view.service
   ```

   **確認：** 両方が `enabled`（自動起動が有効）、`active (running)`（現在動作中）になっていること。

6. **CSV の記録とビューアの待受ポートを確認する。**

   ```sh
   tail -n 5 netwatch.csv
   journalctl --user -u netwatch_view.service -n 30 --no-pager
   ```

   **確認：** CSV に最近の時刻の測定値が記録され、起動ログにポート番号が表示されていること。
   起動直後は経路検出などに時間がかかるため、CSV がまだなければ少し待って確認します。

7. **同じ LAN のブラウザから画面を開く。**

   `http://<監視端末のLAN IP>:<起動ログのポート>/` を開きます。

   **確認：** グラフが表示され、更新時刻が進むこと。
   ビューアは `0.0.0.0` で待ち受けます。LAN 内に限定するルーター・ファイアウォールの設定は端末側で管理し、選んだポートに合わせてください。

8. **ログアウト後やログイン前も監視する場合は、linger を有効にする。**

   ```sh
   sudo loginctl enable-linger "$USER"
   loginctl show-user "$USER" -p Linger
   ```

   **確認：** `Linger=yes` と表示されること。この設定は端末ごとに一度行います。

### ポートと生成ファイルについて

- 別の端末で同じポート番号を使っていても競合しません。
- 空き確認後のポート予約は行いません。起動までに他のプログラムが使用した場合は、別の番号で設定を生成し直してください。
- 同じ番号で再生成する場合は、ビューアを停止してから `uv run python systemd/configure.py --port <番号>` を実行します。番号を省略して再生成すると、別の番号になることがあります。
- 起動するたびに空きポートを確保したい場合は、生成時に `--port 0` を指定します。再起動で番号が変わるため、起動ログで確認してください。
- 生成スクリプトは自身が置かれた clone 先を使います。別のカレントディレクトリから実行しても設定対象は変わりません。
- `systemd/generated/` の端末固有の設定と、clone 先に保存される CSV は Git 管理から除外しています。

## 更新（Linux のユーザーサービス）

1. **clone 先でコードと依存関係を更新する。**

   ```sh
   git pull
   uv sync --locked
   ```

   **確認：** 両方がエラーなく完了すること。

2. **ユニット設定や配置先が変わった場合は、設定を反映する。**

   セットアップ手順の生成・検証・コピー・`daemon-reload` をやり直します。
   clone 先の移動や Python の配置変更があった場合も必要です。
   ポートを維持する場合は、上の「ポートと生成ファイルについて」を参照してください。

3. **再起動し、更新後の動作を確認する。**

   ```sh
   systemctl --user restart netwatch.service netwatch_view.service
   systemctl --user status netwatch.service netwatch_view.service
   ```

   **確認：** 両方が `active (running)` で、ブラウザの更新時刻も進むこと。

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
