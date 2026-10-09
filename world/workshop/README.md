# D0 活動の動きの道具

元データは `D:/Products/AI-Models/Motion/D0/` のみに置く。イデア、本番の窓、外部APIには触れない。

- `vrma.py`：人型ボーンの局所回転と腰移動をVRMAへ書く。
- `npz_to_vrma.py`：Kimodo SOMA77→VRMA。`--start`、`--end`、`--loop` で静止区間の輪を作る。肘の過屈曲は角度が±180°をまたいでも補正が途切れないようにする。`--upper-body` は身振り用に上半身の回転だけを書き、腰の移動・腰と脚の回転を入れない。
- `smpl_to_vrma.py`：SwimXYZ SMPL→VRMA。手首・つま先と関節、継ぎ目を補正する。
- `look.mjs` + `look.html`：ヘッドレスChromeでユメカVRMの姿勢・足裏を測り、フレーム記録と並べた絵を出す。`body/` のAnimationMixer、基準姿勢、地面補正を使用する。床は表示座標の `WATER_OPTICS.floorY`、関門の入力ではその位置を0mとする。
- `伸び.vrma` を `look.mjs` へ渡すと、通常の関門用記録に加え、立位・座位・泳ぎと重ねた `伸び-活動別.png` も描く。
- `gate-run.mjs`：本体の `world/sea/gate.ts` を直接読み、動きごとにpass/issueを出す。動き別の閾値変更はない。
- `腰を下ろす.vrma`：Kimodo `sit_ground` の0〜19フレーム。0〜21では右足の滑りが終端20〜21フレームに残ったため、腰が下りた19フレームまでで切った。関門のしきい値は変更せず合格。砂地への到着から0.5倍速で1回だけ再生し、泳ぐ姿勢から0.6秒かけて混ぜ、入りの終わりから1秒で座る輪へ移る。重みと時刻は到着時刻から毎回計算する。

## 手元の動き生成 HTTP（D0）

`generator.py` は `127.0.0.1` だけで待つ生成器。モデルを読み込む前に、固定版のローカルファイル一式と空きRAM 16GiB以上・空きGPU VRAM 3072MiB以上を検査し、満たさなければ終了する（閾値はPoCの実測に安全余裕を足したもの）。実モデルは手元のKimodo SOMA77とCPUのLLM2Vecを使い、外部通信・モデルの自動取得は拒否する。文と埋め込みはファイルやログに保存しない。

- `GET /health`：読み込み前・読み込み中は503、利用可能になったら200。
- `POST /motion`：JSONの `text`（1〜200文字）、`seconds`（1〜10秒）、`seed`（整数、省略可）。成功で `model/gltf-binary` のVRMAと `X-Motion-Seed` を返す。入力不正は400、未準備・別件の生成中は503、生成失敗は500。
- 生成は同時に1件だけ。生成結果は `npz_to_vrma.convert_arrays()` からメモリ上でVRMAへ変換する。関節制約は内部生成用で、HTTPからは受け取らない。
- 内部では `KimodoBackend.generate_arrays(text, seconds, seed, constraints=[...])` が制約付きの配列を返す。`constraints.py` は制約辞書の形・フレーム番号を検査し、`load_constraints_lst` でKimodo自身の骨格変換を使う。最初の全身制約の腰XZを原点へ移し、`first_heading_angle` を計算してから生成し、出力配列の位置を元の座標へ戻す。外部テキスト・埋め込みは保存しない。
- 制約辞書の `type` は `fullbody` / `end-effector` / `left-hand` / `right-hand`。整数の `frame_indices`、SOMA77の軸角 `local_joints_rot [N,77,3]`、`root_positions [N,3]`、任意の `smooth_root_2d [N,2]` を指定する。`end-effector` は `joint_names` も必要。実Kimodoへの適合と座る・寝るの接合は、実生成後の検証が必要。
- `reference_constraints.py` は、既存のKimodo NPZと骨格JSONから全身制約の姿勢・腰XZを抽出するCPU専用補助。9秒/30fpsなら `reclining_anchors(folder, 270)` で先頭に `sit_ground` の60フレーム目、末尾に `lie_side_sleep` の0フレーム目を置く。末尾を使わない新しい睡眠ループを試す場合は `end_as_sleep=False`。実生成には `KimodoBackend.generate_arrays(..., constraints=rows)` を使い、HTTPには渡さない。つなぎ目の整合性・自然さは生成後の関門と絵で別途検査する。
- D0のCPU試行専用 `text_features.py` は、承認済みの抽出実行で自作の文を4096次元の特徴へ変換し、`D:/Products/AI-Models/Motion/D0/text-features/` に文のSHA256先頭16桁を名前として保存できる。以後は読み手なしの `TextFeatures(folder)` が同じ文を読み出し、未保存の文は拒否する。本番HTTPには接続せず、Serinaの言葉はここへ保存しない。抽出実行とCPUの制約付き生成は別途実装・検証を要する。
- `roll_metrics.py` は、生成したSOMA77の腰回転の総角度・始終点の正味角度・迂回比率・最大角速度と、指定した終点からの角度差を数えるCPU専用の純関数。**しきい値を持たず**、候補の比較にだけ用いる。VRMの共通関門や実際の絵の確認は省略しない。
- 寝転びの終点は加工前のNPZではなく、正本の `眠る.vrma` の最終5フレームを使う。`vrma.read_tracks` でVRM22骨の局所回転と腰を読み、`npz_to_vrma.position_animation` で窓の睡眠向き（現在135°）と座る腰XZを焼き込み、`animation_to_soma` でSOMA77の制約に戻す。`reference_constraints.sleep_vrma_end_anchors(..., frames)` が、生成する寝転びの最後5フレームの全身制約（腰も含む）を返す。中間骨は単位局所回転。変換は既存の `眠る.vrma` を変更せず、描画・共通関門は別途通す。
- `recline_trials.prepare_recline_trials` は本物のモデルを読み込まない事前準備。承認済みの文4種類が全てローカル特徴キャッシュに存在することと、座る姿勢の骨格順が生成先SOMA77と一致することを検査し、4秒/6秒×seed3種類の計24候補の先頭座位・末尾quiet睡眠5フレームの制約を組む。文章は結果の識別子に含めない。**候補の実生成・数値選抜・共通関門・絵の確認は未実装/未実施**。特徴抽出やKimodoモデル起動はMaster承認後に限る。
- `trial_outputs.generate_trial` は組立済みの候補1件と外部から渡された生成器から、モデル結果の配列形・有限性を検査し、`candidate-001.npz` と `candidate-001.vrma` のような番号のみの候補ファイルを作る。腰の回転の測定結果も番号・seed・秒数・数値のみで返す。上書きは拒否し、変換失敗なら候補を残さない。入力文と文章特徴は結果に書かない。**生成器を実際にCPUで起動する台本と、VRM共通関門の自動採否は未結線**。候補ファイルを置いたことは採用を意味しない。
- `KimodoBackend.load_cached(checkpoints, features=TextFeatures(...), poc=..., hf_home=...)` はD0実験用のCPUモデル入口。外部通信を閉じ、ローカルKimodoの存在と空きRAM 16GiBを先に検査してから、キャッシュ済み特徴の読み手を渡してKimodo本体を**CPUのみ**で読み込む。8B文章モデルを読み込まずCUDAを使わない。事前に `prepare_recline_trials` で文4件の特徴がそろうことを確認する。**CPU実生成・速度・メモリ使用量は未検証**。Serinaの精神停止や実モデル起動は新たなMaster承認まで行わない。
- ポートは `--port` または `NIRAI_GENERATOR_PORT` で指定（既定47820）。

本物の生成器を再度実行し、Serinaの精神を一時停止する際は、Masterの約30分の事前了承を得る。2026-10-09の了承済みKimodo実行では19候補を作り、6候補をユメカで描画・共通関門で検査した。寝転ぶ3候補はすべて不合格で未採用。両腕を伸ばす3候補は合格し、`stretch_up0_401` を上半身専用に変換した `伸び.vrma` も関門合格・画像確認済み。本番のSerinaではまだ体験していない。

偽モデルのみのテスト（外部接続・モデル読み込み・本番の住人への接触はしない）：

```powershell
& 'D:/Products/ResidentMotion-PoC/.venv/Scripts/python.exe' -m unittest -v test_generator.py test_reference_constraints.py test_upper_body.py test_elbow_rotation.py test_text_features.py test_roll_metrics.py test_vrma_inverse.py test_recline_trials.py test_trial_outputs.py test_cached_backend.py
```

コマンド例（`world/` をカレントにする）：

```powershell
$py = 'D:/Products/ResidentMotion-PoC/.venv/Scripts/python.exe'
& $py workshop/npz_to_vrma.py 'D:/Products/AI-Models/Motion/D0/kimodo/sit_ground.npz' 'D:/Products/AI-Models/Motion/D0/kimodo/sit_ground.json' window/assets/motions/座る.vrma --start 60 --loop
node workshop/look.mjs 'D:/Products/Work/stage4-d0-motions/checks' '泳ぐ.vrma' '浮く.vrma' '座る.vrma' '眠る.vrma'
node workshop/gate-run.mjs 'D:/Products/Work/stage4-d0-motions/checks'

# 入りと座る輪の単体関門と、実際のBodyでの横からのつなぎ（着地から座る.png）
& $py workshop/npz_to_vrma.py 'D:/Products/AI-Models/Motion/D0/kimodo/sit_ground.npz' 'D:/Products/AI-Models/Motion/D0/kimodo/sit_ground.json' window/assets/motions/腰を下ろす.vrma --start 0 --end 20
node workshop/look.mjs 'D:/Products/Work/stage4-d0-sit-entry/checks' '腰を下ろす.vrma' '座る.vrma'
node workshop/gate-run.mjs 'D:/Products/Work/stage4-d0-sit-entry/checks'

# 承認済みの2026-10-09 Kimodo生成素材から、上半身のみの「伸び」を作る
& $py workshop/npz_to_vrma.py 'D:/Products/AI-Models/Motion/D0/kimodo-run-20261009/stretch_up0_401.npz' 'D:/Products/AI-Models/Motion/D0/kimodo-run-20261009/stretch_up0_401.json' window/assets/motions/伸び.vrma --upper-body
node workshop/look.mjs 'D:/Products/Work/stage4-d0-remaining/checks-stretch' '伸び.vrma'
node workshop/gate-run.mjs 'D:/Products/Work/stage4-d0-remaining/checks-stretch'
```

検査で`pass=false`の動きは本番へ入れない。寝転びは生成方法の改善と再検証が必要。変換器の肘補正を連続化して再比較しても、`recline_side_start_103` には元データ由来の逆関節・足滑りが残り不合格。これは採用しない。
