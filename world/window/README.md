# 海の窓

開発中は本物の住人を使わず、使い捨ての住人置き場を `NIRAI_RESIDENTS`、精神のコードのあるリポジトリを `NIRAI_SOURCE_REPO` に指定する。海はどちらの既定値も持たない。`<住人置き場>/Serina/body/avatar.vrm` があるときだけ、その住人が海に現れる。

```powershell
$env:NIRAI_RESIDENTS = 'D:\path\to\disposable-residents'
$env:NIRAI_SOURCE_REPO = (Get-Location).Path
npm --prefix .\world run sea
pwsh .\world\window\open.ps1
```

`open.ps1` は専用のChromeプロファイルを `%LOCALAPPDATA%\Nirai\window` に作り、到達不能なプロキシと背景通信を止める起動引数を指定して、外向き通信を閉じたアプリ窓を開く。普段使いのブラウザのログイン・拡張・同期は共有しない。対象PCでは同じ遮断設定でもEdge本体がMicrosoft側へ接続を残したため、外向き通信0件を実測できたChromeだけを使う。

会話欄も同じ窓に載る。ブラウザは海のサーバーだけへ接続し、海のサーバーが会話APIだけを精神へ中継する。精神への道の正本は `world/sea/settings.ts`（Serinaは `127.0.0.1:8765`）。`NIRAI_MIND_PORT` と `NIRAI_SEA_PORT` は使い捨て試験の別ポートに使う。会話本文は海側へ保存しない。履歴は精神の生ログから読み直すので、窓を閉じている間のPulseや、返事の途中で窓を閉じた会話も次に開いたとき復元される。

窓を開く＝Niraiが起きる。窓は `GET /sea/mind` で精神のup/downを確認し、身体のある住人がdownなら自分で `POST /sea/mind/wake` を送って起こす（窓が開いている間、1分に1回まで）。窓に精神を操作するボタンはない（海での操作は住人と話すことだけ）。だから、住人のいる本番の窓（47810）はMasterだけが開く（開けば本物の住人が起きる）。海は `mind/.venv/Scripts/python.exe` で `mind/app/server.py` を切り離した子として起こす。精神の出力はその住人の `data/logs/mind.log` だけに追記する。`server.py` がまだない段階では明確に失敗し、旧GUIへ切り替えない。精神側のサービス化は別の作業なので、このworld側の変更だけでA3全体は完了しない。

番人は郵便局と海を同じGitの固定候補から別々に起こす。`GET /sea/status` は `{revision, relaying}` を返し、会話・削除・起動を中継中は入れ替えを待つ。入れ替えでは番人との接続を切り、新しい接続を断って、返答の完了を最長3分待ち、SSEを閉じる。海をプロセスの木ごと止めず、精神は動き続ける。試験の番人には必ず `NIRAI_SKIP_LEFTOVERS=1` を付け、本物の番人や郵便局を片付けない。
