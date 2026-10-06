# 海の窓

開発中は本物の住人を使わず、使い捨てイデアの絶対パスを `NIRAI_IDEA` に指定する。

```powershell
$env:NIRAI_IDEA = 'D:\path\to\disposable-idea'
npm --prefix .\world run sea
pwsh .\world\window\open.ps1
```

`open.ps1` は専用のChromeプロファイルを `%LOCALAPPDATA%\Nirai\window` に作り、到達不能なプロキシと背景通信を止める起動引数を指定して、外向き通信を閉じたアプリ窓を開く。普段使いのブラウザのログイン・拡張・同期は共有しない。対象PCでは同じ遮断設定でもEdge本体がMicrosoft側へ接続を残したため、外向き通信0件を実測できたChromeだけを使う。

A2では会話欄も同じ窓に載る。ブラウザは海のサーバーだけへ接続し、海のサーバーが会話APIだけを `127.0.0.1:8765` の精神へ中継する。会話本文は海側へ保存しない。履歴は精神の生ログから読み直すので、窓を閉じている間のPulseや、返事の途中で窓を閉じた会話も次に開いたとき復元される。
