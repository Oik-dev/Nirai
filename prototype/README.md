# Nirai v2 UI Prototype

インストールやbuild不要のHTML / CSS / JSモック。`index.html`をブラウザで開く。

## 役割

画面構造・見た目・配置の基準。実行時の動作仕様は`../Nirai_v2_基本設計書.md`の§21・§22を参照する。`app.js`のTask更新・模擬応答は実Hubへの接続時に置き換える。

## 現在の表示・操作モック

- Dashboardの展開 / 折り畳み、RUN / CHECK / PAUSE / COMPLETE表示
- Resident選択、＋でTask作成、最初のChat送信によるActivity表示
- Task選択に対応するChat
- Chat headerでのPause / 再開とResume ON / OFF
- Task footerの「完了扱い」とArchive表示
- TASK / ARCHIVE切替、内部スクロール
- Resident状態・Limit表示
- 横画面の左右配置、縦画面の上下配置

現在の「完了扱い」やChat送信に伴うCHECK解除は見た目を確認するための模擬動作であり、製品の完了・承認処理ではない。実データ接続は初期自走化計画M2で行う。

## 用語

TaskはMasterが依頼する仕事、ActivityはTask内の現在または最近の作業を見せる表示、RunはCapabilityを一回使った実行記録。モックはRunやTask状態の正本を実装しない。
