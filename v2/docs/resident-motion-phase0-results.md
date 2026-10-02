# Resident Motion Phase 0 結果

> Date: 2026-10-02
> Status: Phase 0 completed / Nirai本体未統合
> Purpose: Kimodo → SOMA77 → VRM(Yumeka) がRTX 2080 SUPER 8GB環境で成立するかの隔離PoC結果。

## 結論

技術経路は成立した。

```text
Text
→ Llama 3 8B + LLM2Vec (CPU)
→ Kimodo-SOMA-RP-v1.1 (RTX 2080 SUPER)
→ BVH / NPZ
→ SOMA77 → VRM Humanoid Retarget
→ Yumeka VRM
```

「手を振る」「ゆっくり歩く」「ゆっくり平泳ぎ」の3本を実生成し、YumekaへRetargetして描画できた。
Viewerのconsole error / clip errorは0。静止画確認では大きな骨折れ・姿勢破綻は見られなかった。
泳ぎの最終的な自然さ・連続再生品質はHuman Visual Gateを残す。

## 実測

Run:
`D:\Products\ResidentMotion-PoC\results\run-20261002T073410Z`

### Text Encoder

- Meta-Llama-3-8B-Instruct + LLM2Vec
- CPU / BF16
- Model load: 約235.8秒
- Text stage total: 約421.8秒（モデルのfingerprint計算を含む）
- Peak RSS: 約15.4GB
- wave encode: 6.05秒
- walk encode: 2.82秒
- swim encode: 3.42秒

重要:
Cold loadが非常に重い。
毎回Motion要求ごとにText Encoderを起動・終了する構成は実用的ではない。
共有常駐、Embedding cache、別service、より軽量なText Encoder等を再検討する価値が高い。

### Kimodo Motion

- Kimodo-SOMA-RP-v1.1
- CUDA FP32
- 5秒 / 150 frames / 30fps / 100 denoising steps
- Model load: 約29.1秒
- Kimodo stage total: 約58.1秒
- PyTorch VRAM peak: 約1.22GB
- GPU全体の計測peak: 約2.82GB（Desktop等を含む）

生成:
- wave: 9.44秒
- walk: 5.38秒
- swim: 5.36秒

2080 SUPER 8GBでKimodo本体は十分動作した。
現状の最大のボトルネックはGPU容量よりText Encoderのcold load / RAM。

## VRM Retarget

隔離Viewer:
`D:\Products\ResidentMotion-PoC\viewer`

生成BVH:
`D:\Products\ResidentMotion-PoC\results\clips`

Yumeka:
`D:\Products\Model Converter\output\Yumeka_v1.0.4-appearance.vrm`

SOMA77 BVHをVRM Humanoid semantic boneへRetarget。
wave / walk / swimの3clipを実描画。
Viewer smoke status:
`three_clips_rendered_pending_visual_review`

gross visual check:
- wave: 意図に沿った右手の挙上・手振り姿勢を確認
- walk: 歩行姿勢を確認
- swim: 水平姿勢と前方へ腕を出す泳動作を確認
- 明確な骨折れ・180度反転等は静止画では未確認

ただし滑らかさ、足滑り、平泳ぎとしての質は実時間再生で最終確認する。

## Phase 0中に判明した実装上の注意

- WindowsでHugging Face cacheのsymlink警告が出るが、取得自体は成立。
- LLM2Vec MNTP adapterの `base_model_name_or_path` はHugging Face repo名を指す。
  完全offline PoCではローカルLlama snapshotへ向けたlocal viewを作成した。
- Kimodo公式が説明しているLLM2Vec load時のMISSING / UNEXPECTED警告は発生した。
- Text Encoder終了後にRAMが解放されてからKimodo GPU stageへ進む分離構成は成立した。
- Nirai本体にはまだ変更を入れていない。

## 現時点の判断材料

Phase 0としては成功。

有力な方向:
- 軽い仕草: Procedural
- 複雑な自由動作: Kimodo等のMotion model
- Motion model: 必要時起動 / Resident間共有
- Embedding: cacheを強く活用

未FIX:
- Llama 3 Text Encoderを常駐させるか
- 量子化できるか
- Remote / Cloud Text Encoderを許容するか
- Kimodoを最終採用するか、ARDY等と比較するか
- Motion生成step数の削減と品質の関係
- World移動 / root motion / IK / foot contact
