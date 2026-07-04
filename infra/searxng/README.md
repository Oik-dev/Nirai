# SearXNG（セリナの検索の目）セットアップ

セリナが自前で WEB 検索するための、他人（検索API業者）を経由しないメタ検索エンジン。
Docker で立てて、ずっと動かしっぱなしにしておく。

## 通常運用（自動）

`Serina.bat` が起動時にこの検索エンジンを自動で立ち上げる（`docker compose up -d`）。
**前提**: Docker Desktop が起動していること。Windows 起動時に Docker Desktop が自動で立ち上がる
設定にしておくと、`Serina.bat` を押すだけで毎回そろう。Docker が動いていない時は Serina 本体は
そのまま起動し、その回だけ検索が使えない（`Serina.bat` が警告を1行出す）。

## 手動での立て方（初回・トラブル時）

1. Docker Desktop を起動しておく
2. このフォルダで次を実行:
   ```
   docker compose up -d
   ```
3. ブラウザで http://localhost:8888 を開き、検索できれば成功

## 動作確認（セリナが読めるJSONが出るか）

ブラウザで次を開き、真っ白でなくJSON（文字の羅列）が返れば正しく設定できている:

    http://localhost:8888/search?q=test&format=json

403エラーや空なら、`settings.yml` の `search.formats` に `json` が入っているか確認し、
`docker compose restart` する。

## セリナ側の接続先

既定は `http://localhost:8888`。別ポートにした場合は環境変数で上書きする:

    SERINA_SEARXNG_URL=http://localhost:9999

## 停止・更新

- 停止: `docker compose down`
- イメージ更新: `docker compose pull` → `docker compose up -d`

## メモ

- 秘匿情報なし（ローカル専用）。将来インターネット公開する場合は `secret_key` を変更し、
  `limiter` 等のアクセス制御を見直すこと。
