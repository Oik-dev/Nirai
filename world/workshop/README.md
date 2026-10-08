# D0 活動の動きの道具

元データは `D:/Products/AI-Models/Motion/D0/` のみに置く。イデア、本番の窓、外部APIには触れない。

- `vrma.py`：人型ボーンの局所回転と腰移動をVRMAへ書く。
- `npz_to_vrma.py`：Kimodo SOMA77→VRMA。`--start`、`--end`、`--loop` で静止区間の輪を作る。肘の過屈曲を共通処理で補正する。
- `smpl_to_vrma.py`：SwimXYZ SMPL→VRMA。手首・つま先と関節、継ぎ目を補正する。
- `look.mjs` + `look.html`：ヘッドレスChromeでユメカVRMの姿勢・足裏を測り、フレーム記録と並べた絵を出す。`body/` のAnimationMixer、基準姿勢、地面補正を使用する。床は表示座標の `WATER_OPTICS.floorY`、関門の入力ではその位置を0mとする。
- `gate-run.mjs`：本体の `world/sea/gate.ts` を直接読み、動きごとにpass/issueを出す。動き別の閾値変更はない。
- `腰を下ろす.vrma`：Kimodo `sit_ground` の0〜19フレーム。0〜21では右足の滑りが終端20〜21フレームに残ったため、腰が下りた19フレームまでで切った。関門のしきい値は変更せず合格。砂地への到着から0.5倍速で1回だけ再生し、泳ぐ姿勢から0.6秒かけて混ぜ、入りの終わりから1秒で座る輪へ移る。重みと時刻は到着時刻から毎回計算する。

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
```

検査で`pass=false`の動きは本番へ入れない。眠りに入るまでの寝転びと伸びは後続工程とし、この段では作らない。
