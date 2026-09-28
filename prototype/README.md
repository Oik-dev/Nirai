# Nirai v2 UI Prototype

インストールやbuild不要のHTML / CSS / JSモック。`index.html`をブラウザで開く。

## 役割

初期検討用の画面モック。2026-09-26以降の現行UIは`../v2/docs/ui-design.md`と`../v2/src/renderer/`を正本とする。このモックは現在の透過Glass、狭幅での一覧切替、native Holoの表示境界を再現しない。実行時の動作仕様は`../Nirai_v2_基本設計書.md`の§21・§22を参照し、`app.js`の模擬動作を移植しない。

## 現在の表示・操作モック

- Dashboardの展開 / 折り畳み、RUN / CHECK / PAUSE / COMPLETE表示
- Resident選択、＋でTask作成、最初のChat送信によるActivity表示
- Task選択に対応するChat
- Chat headerでのPause / 再開とResume ON / OFF
- Task footerの「完了扱い」と、完了Taskの72時間表示・「再開」・「閉じる」
- Task一覧の内部スクロール
- Resident状態・Limit表示
- 横画面の左右配置、縦画面はDashboardを下側に寄せて上側へWorld表示領域を残す

現在の「完了扱い」やChat送信に伴うCHECK解除は見た目を確認するための模擬動作であり、製品の完了・承認処理ではない。実データ接続は初期自走化計画M2で行う。

## 用語

TaskはMasterが依頼する仕事、ActivityはTask内の現在または最近の作業を見せる表示、RunはCapabilityを一回使った実行記録。モックはRunやTask状態の正本を実装しない。
