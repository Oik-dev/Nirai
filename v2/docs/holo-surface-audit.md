# Holo native surface 表示監査

2026-09-26。対象はNirai v2の表示層。現在のデザイン基準は[ui-design.md](ui-design.md)、共有色は`src/renderer/theme.css`。

## 実画面から確認した問題

通常の製品起動で実ChatGPTのDOM、computed style、native viewの寸法、ウィンドウ全体の画像を照合した。会話本文だけでなく、composer、page header、body直下のsidebar、固定・追従要素、疑似要素、view transitionを対象にした。

- 開始時のSkinは未知のProvider要素まで背景・影・filterを消す方式で、会話・ツール・メニューの意味ある面も巻き込んでいた。実画面ではsidebar開閉時に会話部分の濃い矩形を確認した。
- RendererにはDashboard、内側の面、選択カード等の複数の背景ぼかしがあり、同じ画素を複数層で加工していた。描画の責務を一層に整理した。
- 実sidebarの新しいrootは`#browser-sidebar-popover`と`#app-shell-sidebar`。navだけを指定するとheader/history/accountが別の面になった。旧`#stage-popover-sidebar` / `#stage-slideover-sidebar`も限定して対応する。inertな`#stage-sidebar-tiny-bar`は実sidebarではない。
- Composerは外側formではなく角丸の内面が塗りの所有者。寸法やラベルからmessage actionの祖先を推測すると、`display:contents`を通じて会話全体へ処理が広がり得る。
- `.translucent-surface`は実測で黒い半透明背景と追加ぼかしを持つ補助面だった。ここだけ共有色の静的な面へ対応付ける。

ユーザーのv1との比較に従い、v1のWebContentsView配置、最小Skin、外枠Glassだけを限定参照した。v1のTaskや送信処理は採用していない。両版のElectronは同じ41.10.6であり、Electronの版差だけでは説明できない。CSSと面の重なりを改善対象とし、低水準の描画エンジン不具合を単独原因と断定していない。

## 現在の構造

- Holo・通常会話とも透過Glass。Holo主会話面を不透明にして症状を隠さない。
- DashboardだけがWorldをぼかす。外枠・余白は薄く、文字のある場所に読みやすい下地を付ける。
- Holoのhtml/bodyと監査済み会話surface tokenを透明にし、補助メニュー・sidebar・composerへ共有色を対応付ける。
- 全子孫、全portal、全role、全疑似要素をresetしない。未知のProvider背景・影・filterを保つ。推奨や免責表示も幾何学的な推測で隠さない。
- Decoratorはcomposer内面、sidebar実rootと上下端、Chat/Work切替だけを識別する。監視したDOMが変われば不要な識別属性を外す。
- Native viewを取り付ける前に寸法を設定する。Renderer側は表示枠そのものの寸法変更を監視する。通知・設定・Task一覧切替はnative会話を覆わない。

## 検証の範囲

| 確認 | 結果と意味 |
| --- | --- |
| Unit | 40 / 40通過。処理層の既存契約 |
| Main smoke | 通過。MainとHubの起動・往復 |
| UI smoke | 通過。5サイズ、通常会話の下書き分離・読書位置、承認/却下のフォーカス、設定のキーボード操作、既存のTask操作 |
| Holo fixture | 通過。透明canvas、未知面の保持、限定Skin、送信・Stop・scroll等の既存契約 |
| 実ChatGPTの外観 | 通常の製品ウィンドウで短文・長文、sidebar、思考量メニュー、履歴移動、縦長へのサイズ変更、一覧切替を確認。確認範囲では以前の余分な矩形・帯を再現しなかった |

UI検証の画像保存はテストウィンドウを前面表示して行う。背面に隠したままの画像保存が停止する環境要因と、製品の表示不具合を区別する。模擬Providerの座標クリックも表示・描画完了後に行う。

ローカルの調査画像・寸法記録・検証ログは`.tmp-ui-redesign/`。Providerの会話本文やアカウントを恒久fixtureへ複製していない。将来のChatGPTのDOM変更すべてを保証するものではなく、変更時は同じ実DOM監査を行う。

実ChatGPTへ新規メッセージを送り、Nirai-MCPとToolを往復させる処理の再E2Eは今回の表示確認に含めない。VRM・動く3D背景は未接続であり、その背景での確認も未実施。UI更新をM4〜M7やWorld実装の完了とは扱わない。

## 変更境界

開始時のファイル指紋と照合し、`src/hub/`、`src/shared/`、`src/bridge/`、処理の`tests/`、Main入口、preloadは変更していない。`holo-dom.mjs`は表示用decoratorだけを変更し、入力送信・Stop・scroll guardは同一。`holo-view.mjs`は表示寸法を取り付け前に設定する順序だけを変更した。作業開始前から存在した他の差分を今回の変更と混同しない。
