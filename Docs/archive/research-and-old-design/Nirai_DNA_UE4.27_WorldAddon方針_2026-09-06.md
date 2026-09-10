# Nirai DNA / UE4.27 World Addon 方針

Status: **Future Candidate / Post-Foundation Decision**

本書は、Niraiの基盤完成後に実施するWorld置換候補の方針を記録する。現時点ではUE4.27 WorldをActive実装対象へ昇格させず、まずFeasibility Spikeで成立性を確認する。

## 1. Master決定

Niraiの今後の大きな進行順は次とする。

1. **Nirai基盤完成**
2. **DNA → UE4.27 World置換のFeasibility確認。成立するならそのまま移植へ進む**
3. **M3暮らしContract / World連携を完成**
4. **Core / Protocol v1を固定**
5. **移植後World上で生活表現・M5+を拡張**

旧Three.js海中Worldを先に磨き切ってからUE4.27へ移る順にはしない。

## 2. 基本方針

Niraiの人格・Memory・Brain・Conversation・Task / Agent RuntimeはCore側へ保持し、World実装と分離する。

Worldは交換可能なPresentation / Embodiment層とし、現在のElectron + Three.js WorldをNirai唯一のWorld RuntimeというInvariantにはしない。

基盤完成後、DNA由来のScene / AssetをUE4.27へ持ち込み、Nirai World Protocolへ接続できるかを実機で確認する。

成立性が十分なら、Three.js Worldの追加磨き込みを挟まず、UE4.27 World Addonの実装へ進む。

## 3. DNA由来Worldの配布境界

DNA由来のAsset / Scene等は**Masterの個人利用に限定する前提**で扱う。

したがってDNA WorldはNirai本体へ同梱しない。

```text
Nirai distributable package
  Core
  World Protocol
  Distribution-safe standard World
  public/re-distributable assets only

Private World Addon
  DNA / UE4.27 World
  Master local environment only
  not bundled
  not redistributed
```

Nirai本体の配布Package、公開Repository、配布用Installer、Sample AssetへDNA由来Dataを混入させない。

DNA World Addonが存在しなくてもNirai本体は起動・利用できることを必須とする。

実際の抽出・変換・利用へ着手する時点で、対象Asset / Game / Toolの現行License / Termsと技術的成立性をReference-First Gateで再確認する。

## 4. 現行Three.js海中Worldの扱い

現行Electron + Three.js海中WorldはM0〜M2を成立させた有効な実装であり、削除しない。

ただし当面は次の扱いとする。

- M0〜M2 Evidenceとして保持
- Core / World ProtocolのRegression確認用として保持
- 配布可能な標準World候補として保持
- DNA / UE4.27 Feasibility確認前に追加Graphic工数を大量投入しない
- 配布Productを整える段階で、必要なら改めて整理・完成させる

つまり**退役ではなく凍結 / 退避**とする。

## 5. Feasibility Spikeで確認すること

基盤完成直後に、実装本番へ入る前の小さいSpikeを行う。

最低限確認する。

### Asset / Scene

- DNAから実際に何を取得できるか
- Mesh
- Texture
- Material
- Animation
- Scene placement
- Lighting
- Environment / VFX
- Shader
- Collision / Navigation情報
- Camera / post-process等

「全部抜ける」前提を置かず、実データで確認する。

### UE4.27

- UE4.27へ持ち込める形式
- Material / Shader再現性
- Scene再構築Cost
- Animation / Skeleton / Avatar接続
- Runtime負荷
- Package / local deployment

### Nirai接続

最低限、UE WorldからNirai Coreへ次を接続できること。

- Resident spawn / despawn
- semantic action command
- movement / pose / face / expression
- speech / TTS / lip sync event
- Master focus / interaction
- World Observation snapshot
- action_done / failure

Core側へUE固有Object / Actor / Blueprint /座標仕様を漏らさない。

## 6. Go / No-Go

### Go

以下が成立するならUE4.27 World Addonへ進む。

- DNA Scene再利用が手作業再制作より十分に有利
- UE4.27上で見た目の価値が高い
- Nirai World Protocolと無理なく接続できる
- Core / Memory / Conversation / Agent Runtimeを作り直さない
- 個人利用Addonとして配布境界を守れる
- 維持不能な独自変換Pipelineを大量に抱えない

### No-Go / Hold

次ならThree.js Worldを維持し、World置換は保留する。

- Scene抽出が実用にならない
- Material / Shader再現の手直しがほぼ全面再制作になる
- UE4.27接続のためCoreを強くUE依存化する必要がある
- 維持Costが見た目の利益を上回る
- 利用条件上、安全な個人利用境界を作れない

## 7. 設計原則

- DNA Worldは**Addon**でありNirai本体ではない
- DNA AssetはNirai Repository /配布物へ入れない
- Core / World Protocolは配布可能な汎用仕様を維持する
- Addon不在でNirai本体が縮退動作できる
- 現行Three.js Worldを破壊してから移植しない
- Feasibility確認前に大規模なUE固有設計を確定しない
- UE4.27 / DNA固有制約をNirai全体のInvariantへ昇格させない
