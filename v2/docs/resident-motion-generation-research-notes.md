# Resident Motion Generation 調査メモ

> Status: Research memo / NOT FIXED  
> Date: 2026-10-01  
> Purpose: 後続AIがResidentの自律身体制御を再検討するための叩き台。設計決定書ではない。  
> Target: Nirai v2 / VRM Resident / 主対象PC NVIDIA RTX 2080 Super 8GB

## 0. 今回の話の要約

目標は、Residentが「登録済みモーションを番号で選ぶ」のではなく、その場の会話・感情・状況に応じて自分の身体表現を決められること。

現時点の有力イメージ:

```text
Resident LLM
  ↓ 何をしたいか（Body Intent）
Motion Generator / Procedural Motion
  ↓ 数秒分の人体モーション
Retarget / IK /補間
  ↓
Nirai VRM Humanoid
  ↓
World内で再生
```

重要: LLMに毎フレームBone角度を直接決めさせる方式ではない。
それをやると遅延・ノイズ・カクつきが出やすい。

「Residentが動作意図を決める」ことと、「60fpsで滑らかな身体運動を作る」ことは分離する。

---

## 1. Motion生成モデルとは何か

言語モデル = GPT / Qwen / Claude  
画像生成モデル = Stable Diffusion / FLUX  
モーション生成モデル = MotionLCM / T2M-GPT / MotionGPT系

という位置づけ。

入力例:

```text
slowly swims forward while looking to the right
```

出力イメージ:

```text
frame 0: pelvis / spine / shoulder / elbow / knee ... の姿勢
frame 1: ...
frame 2: ...
...
```

つまり、既存モーションファイルを単純に検索して再生するだけではなく、
大量の「人間の動き + 言語説明」を学習し、新しい時系列モーションを生成する系統が既に存在する。

---

## 2. NiraiのVRMへ入るか

原理上は入る。

ただし、Motionモデルの出力をそのままVRMへ渡せるとは限らない。
ここがNirai側の主要実装ポイント。

MotionLCMの例では、生成結果として概ね以下のようなjoint列を扱う。

```text
(nframes, njoints, 3)
= 各フレーム × 各関節 × XYZ
```

一方、NiraiのVRMはVRM Humanoid Boneを回転・位置として動かす。

したがって間に、

```text
Generated Human Skeleton
  ↓
Retarget / IK / joint→bone rotation conversion
  ↓
VRM Humanoid Bones
```

が必要。

ここはPoCで最優先確認対象。
「Text-to-Motionが動く」より「生成結果をNiraiの任意VRMへ自然にRetargetできる」が重要。

---

## 3. カクカク問題

毎フレームLLMが、

```text
右腕 12度
次のframe 14度
次のframe 9度
```

のように指示する構成は避けたい。

有力なのは、AIが2〜10秒程度の動きをまとめて生成し、
Nirai Runtimeが60fps等で補間して再生する方式。

追加で、

- Quaternion interpolation
- Joint limit
- IK
- Foot lock / contact
- Root motion補正
- Motion blending
- SpringBone

などをRuntime側で扱う。

「AIが考える周期」と「描画frame」は分離する。

---

## 4. 泳ぐことは可能か

HumanML3Dには swimming を含むスポーツ系モーションが明記されている。
よってText-to-Motionモデルが「泳ぐ身体運動」を生成する方向には学習上の土台がある。

ただし、

- 手足を泳ぐように動かす
- World内で実際に前進する
- 浮力を表現する
- 水面・海底・障害物との位置関係を守る

は別問題。

イメージ:

```text
Motion AI     = 泳ぐ身体運動
Nirai World   = 移動、方向、速度、衝突、浮力、水中制約
```

海中Worldとの統合ではこの責務分離が必要。

---

## 5. Resident LLMとの関係

Motion生成モデルは「Qwenをもう1個入れる」という意味ではない。

例:

```text
Serina / Resident LLM
  ↓
"ゆっくり近づきながら手を振る"
  ↓
Motion Generator
  ↓
数秒分の骨格運動
  ↓
Nirai VRM
```

Resident側LLMは「何をしたいか」を決めればよい。

Body Intentを構造化する案:

```json
{
  "action": "approach_and_wave",
  "style": "gentle",
  "target": "master",
  "duration": 3.0
}
```

この契約を間に置けば、Motion backendを後から差し替えやすい。

Local Qwen / Cloud LLM / JEV等をResidentの判断側に使うことは可能。
ただし普通のLLM/CLIがMotion生成モデルの代わりになるわけではない。
Motion部分は専用モデルまたは専用サービスが必要。

---

## 6. 2080 Super 8GB前提の省エネ方針（有力案、FIXではない）

このPCではSerinaのLLMもGPUを使用する想定。
今後Vision / Voice / Motion等を足すたび、全モデル常駐方式はVRAM的に厳しくなる。

そのため、Nirai全体として以下の思想が有力。

> Resident = 常駐する人格/判断系  
> AI能力 = 必要時に借りるモジュール

候補:

1. Motionモデルを常駐させない
   - 必要時Load
   - 数秒分生成
   - キャッシュ
   - 不要ならVRAMからUnload

2. Resident間でMotionモデルを共有
   - Residentごとに1モデル持たない
   - 共通Motion ServiceへQueueする

3. 軽い動作はAI生成しない
   - 呼吸
   - Idle
   - LookAt
   - 首傾げ
   - 小さいGesture
   はProcedural Runtimeで十分な可能性が高い。

4. 重い/自由度の高い動作だけMotion AI
   - 泳ぐ
   - 踊る
   - 寝転ぶ
   - 複雑な連続動作
   - 文脈依存の長い身体表現

5. 生成中と再生中を分離
   - 再生中はMotionモデルの推論不要
   - 次のMotionを先読み可能

6. CPU offload / 量子化 / unloadは実測
   - 2080S 8GBで何が現実的かはPoCが必要
   - 「常駐できるはず」は前提にしない

現時点では、
**Procedural Motion + 必要時だけMotion生成AI**
のHybridが、省エネ・自然さ・実装難度のバランスが良さそう。

これは決定ではない。

---

## 7. 調査したPJ / 参考リンク

### MotionLCM
GitHub:
https://github.com/Dai-Wenxun/MotionLCM

Hugging Face Blog:
https://huggingface.co/blog/EvanTHU/motionlcm

- Text-to-Motion
- Motion control
- one/few-step inferenceを狙った高速系
- 生成結果にjoint時系列を持つ
- PoC候補として分かりやすい
- 公式Repo記載では商用利用不可の独自LICENSE。Niraiへの最終採用前に必ず再確認
- Blogには約200 framesを約30msで生成した旨の記載があるが、使用GPU・条件差があるため2080S性能とは見なさない

### HumanML3D
GitHub:
https://github.com/EricGuoICT/HumanML3D

- 3D human motion-language dataset
- 14,616 motions / 44,970 descriptions と記載
- walking / jumping / swimming / golf / cartwheel / dancing等を含む
- Clipは2〜10秒、20fps
- 「泳ぐ」のような動作をText-to-Motionで扱える根拠の一つ
- Dataset/元データ/SMPL関連Licenseは実利用前に別途確認

### MotionGPT3
GitHub:
https://github.com/OpenMotionLab/MotionGPT3

- Motionを「第二のモダリティ」としてLanguage modelと統合する方向
- Motion VAE + motion branch + diffusion head
- Motion理解と生成を同一frameworkへ寄せる研究
- Niraiの思想的参考には強い
- 2080S実装候補として軽いかは未評価

### AvatarGPT
GitHub:
https://github.com/zixiangzhou916/AvatarGPT

- Motion understanding / planning / generationをLanguage interfaceで繋ぐ
- Motionをdiscrete token化しLLM vocabularyへ組み込む
- 長いMotion planningの思想がResident設計の参考になる
- FLAN-T5-large等を使う構成。Niraiへの直接採用より設計参考寄り

### Language of Motion
GitHub:
https://github.com/Juzezhang/language_of_motion

v2 docs:
https://github.com/Juzezhang/language_of_motion/blob/main/docs/v2.md

- Face / Hand / Upper / Lowerなど身体部位を分けて扱う
- Text-to-Motion / Audio-to-Motion / co-speech gesture
- v2ではQwen3-0.6Bを使う構成例あり
- 会話しながら身振りを生成するResidentには特に参考になりそう
- 「MotionモデルとLLMが完全に別」という構成だけでなく、LLMへMotion tokenを統合する系統も存在することが分かる

### T2M-GPT
GitHub:
https://github.com/Mael-zys/T2M-GPT

- Text description → human motion
- VQ-VAEでMotionを離散表現化し、GPT系Transformerで生成
- 古典寄りだが仕組みを理解しやすい
- GitHub表示ではApache-2.0
- Training要件とInference要件は別。2080SでのInference実測が必要

### fano-vrm-controller
GitHub:
https://github.com/Fano1/fano-vrm-controller

- @pixiv/three-vrm上のVRM controller
- LookAt / face / lip-sync / Mixamo retarget
- procedural animation systemあり
- idle / look-at / gesturesをRuntime生成可能と記載
- Motion AIではない
- Niraiの「軽い仕草を生成AIなしで作る」層の参考候補
- 小規模PJに見えるため、品質・保守性・License・そのまま採用できるかは要監査

### three-vrm
GitHub:
https://github.com/pixiv/three-vrm

NiraiがVRMをThree.js上で扱う場合の基盤候補/参考。
現在のNirai実装との重複やversionはコード確認してから判断すること。

---

## 8. 現時点の有力アーキテクチャ案

```text
[Resident LLM]
      |
      | Body Intent
      v
[Body / Motion Orchestrator]
      |
      +---- 軽い動作 ----> [Procedural Animator]
      |
      +---- 複雑動作 ----> [Motion Model (on demand)]
                               |
                               v
                        [Motion Cache]
      |
      v
[Retarget + IK + Blend]
      |
      v
[VRM Humanoid Runtime]
      |
      v
[Nirai World]
```

Body / Motion Orchestratorがあると、
将来Motionモデルを差し替えてもResident側APIを壊しにくい。

---

## 9. 次にPoCするなら

最小PoC候補:

1. 任意テキストからMotionLCM等で1 clip生成
2. 出力joint/SMPL形式を確認
3. Niraiの1体のVRMへRetarget
4. 「歩く」「手を振る」「泳ぐ」の3種を見る
5. 2080Sで以下を計測
   - model load時間
   - VRAM常駐量
   - 生成時間
   - unload後のVRAM解放
   - Serina LLM同居時の余裕
6. カクつき/足滑り/関節破綻を見る

このPoCが成功してから、ResidentのBody Intent契約をFIXした方がよい。

---

## 10. 未確定 / 後続AIが再調査すること

- MotionLCM以外に、より軽量・商用可・最近のモデルがないか
- 2080S 8GBでの実VRAM/latency
- VRM HumanoidへのRetarget品質
- HumanML3D由来Motionの泳ぎ品質
- Motion生成とRoot Motion/World移動の境界
- 水中姿勢・浮力の扱い
- Resident会話中のGestureはProceduralで足りるか
- Motion cacheの単位と再利用戦略
- 複数Resident同時動作時のQueue/priority
- Local Motion modelとCloud Motion serviceを同じinterfaceで差し替え可能にするか
- License（コードだけでなくcheckpoint / dataset / SMPL等を含む）
- fano-vrm-controllerを依存採用するのか、設計だけ参考にしてNirai側で薄く実装するのか

---

## 11. 一言で引き継ぐなら

**Resident自身に毎frame骨を操作させない。Residentは「何をしたいか」を決め、NiraiがMotionを生成・Retarget・補間して身体にする。2080S 8GBと今後のAI機能追加を考えると、軽い仕草はProcedural、複雑動作だけMotion AIをオンデマンド起動する省エネHybridが現時点では有力。ただしまだFIXしない。**
