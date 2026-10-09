# 体の外見（VRMの形式）

住人の体（イデアの `body/` にあるVRM）は、Model Converterで作る。外見の選択肢は、VRMの中の `extras.nirai.capabilities.appearance` に書く（Model Converterとの約束）。窓は元のモデルや隣のJSONを読まず、VRMの中に書かれたものだけを使う。読むのは `world/window/body/appearance.js`。

## 形式

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

- 項目（control）ごとに選択肢（option）が2つ以上あり、既定（`defaultOption`）はそのどれか。
- 1つの選択肢は、いくつかのNodeの表示（`visibility`）と、いくつかのMorphの値（`morphs`）をまとめて変える。表示を切り替えるNodeは、それぞれ独立したMesh（子Nodeに描画対象を持たない）。glTFの複数のprimitiveは一緒に扱う。
- 1つの項目の選択肢は、どれも同じ対象を漏れなく指定する。1つの表示や1つのMorphを持つ項目は1つだけで、当てる順番や上書きはない。
- Node番号とNode名、Morph番号と `mesh.extras.targetNames` の名前を突き合わせる。Loaderで名前が変わっても、番号で実体を引く。
- Morphの値は −1〜1 の有限の数。負の値は、頂点に焼き込んだ基準の変形を差し引くのに使う。
- 表情・瞬き・視線に結び付いたMorph（VRMの表情の結び付け）は使えない。
- 操作は全部の項目で合わせて4096まで。
- 形式に合わないVRMは読み込まず、理由を出す。全体を確かめてから当てる。

## 今の窓がすること

読み込んだときに、各項目の既定を当てる。本人が外見を選ぶ道は、まだない。

## Loaderの補い

three r167 のLoaderは、一部のMorphにだけある属性の欠けを基準の属性で埋める。そのため負の値で法線が0になり、肌が黒くなる。読み込みの入口（`world/window/body/morph-deltas.js`）で、欠けをゼロの差分として補う。glTFのMorphの定義どおりの処理で、元のVRMとMaterialは書き換えず、モデル名で分けることもしない。
