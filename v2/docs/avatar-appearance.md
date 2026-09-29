# Avatarの衣装・外見

Residentは`avatar.inspect`にある意味付きの選択肢から衣装・アクセサリ・体形を選ぶ。Mesh名・Morph名の判断はConverterの変換入力へ閉じ込める。VRM内のMetadataがRuntimeの正本であり、Niraiは元モデルや隣接JSONを読まない。

## AIからの選択

`capabilities.controls`は`{id,label,category,default_option,options:[{id,label}]}`の一覧。`avatar.set`の`appearance.choices`へ**全項目**の`control id: option id`を指定する。変更しない項目は`desired.appearance`を維持する。

例: inspectの`desired.appearance`を複製して`choices.outfit = "jacket"`とし、同じinspectの`model_id`と`desired.revision`を使って`set`する。IDはモデルの公開一覧から取得し、固定のIDをすべてのモデルへ仮定しない。

表情の`expression`、従来の`wardrobe`も完全な状態として渡す。従来モデルは`choices`を省略できる。選択を保存しただけでは表示完了ではない。`inspect.display_applied`を確認する。Task / Turn、世代、保存済みRun、再起動復元の契約は基本設計§16.3と共通。

## VRM内の形式

`extras.nirai.capabilities.appearance`:

```json
{
  "schemaVersion": 1,
  "controls": [{
    "id": "outfit", "label": "衣装", "category": "outfit", "defaultOption": "normal",
    "options": [
      {"id":"normal","label":"通常","visibility":[{"node":12,"nodeName":"Coat","value":false}],"morphs":[{"node":10,"nodeName":"Body","index":2,"morphName":"Fit","weight":0}]},
      {"id":"coat","label":"コート","visibility":[{"node":12,"nodeName":"Coat","value":true}],"morphs":[{"node":10,"nodeName":"Body","index":2,"morphName":"Fit","weight":1}]}
    ]
  }]
}
```

- 一つの項目が複数Nodeの表示と複数Morph値をまとめて変更できる。表示対象Nodeは独立したMesh単位。glTFの複数primitiveは一緒に扱う。
- 各項目のすべての選択肢は、同じ操作対象を完全に指定する。各表示フラグとMorphは一つの項目だけが所有する。適用順の優先度や前回値への上書きは設けない。
- Node番号と成果物内Node名、Morph番号と`mesh.extras.targetNames`の名前を照合する。Loaderで名前が変わってもNode番号で実体を得る。参照欠落、別Nodeへの親子影響、表情・瞬き・視線のMorphとの競合、従来Wardrobeとの対象重複は拒否する。
- Morph値は有限の`-1..1`。負数は、既に頂点へ焼き込まれた基準変形を差し引くために使える。AIにはこの値を公開しない。
- 最大64項目、各2〜32選択肢、合計4096操作。ID・label等は160文字まで。現行は選択式。連続スライダー、任意のMaterial変更、別モデルからの衣装取付けは含まない。
- 不正Metadataはその外見選択だけを無効化し、理由を表示する。選択全体を検証してから適用する。実Nodeの表示とMorph値を読み返し、受け付けた選択と一致した描画だけを反映確認へ進める。
- 従来の`extras.nirai.capabilities.wardrobe` schemaVersion 1 / visibilityは継続対応。旧モデルのAPI形や保存結果は変換しない。

`three` r167のLoaderは一部Morphだけに存在する属性の欠落を基準属性で補うため、負のWeightで法線が0になり肌が黒くなる。Loader境界の`morph-deltas.js`で欠落をゼロ差分へ補完する。元VRMとMaterialは書き換えない。[glTFのMorph定義](https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html#morph-targets)に従う処理で、モデル名による分岐はない。

## 実モデル検証

通常の`smoke:world`に次を追加すると、全衣装の往復、各衣装と他項目の全選択肢、同時アクセサリ変更、実表示確認、非初期衣装のReload・Hub再起動復元を検証する。

```powershell
$env:NIRAI_V2_WORLD_SMOKE_AVATAR='D:\Products\Model Converter\output\Yumeka_v1.0.4-appearance.vrm'
$env:NIRAI_V2_WORLD_SMOKE_REFERENCE='D:\Products\Model Converter\output\Yumeka_v1.0.4-face.vrm'
$env:NIRAI_V2_WORLD_SMOKE_APPEARANCE='1'
$env:NIRAI_V2_UI_CAPTURE_DIR='D:\Products\Model Converter\validation\yumeka-appearance'
npm run smoke:world
```

正面・背面・顔の画像と`appearance-evidence.json`を出す。実ChatGPTの思考・選択判断や実ProviderからのMCP呼出の証拠ではない。
