# 世界が持つ活動モーション

- `泳ぐ.vrma`：SwimXYZ (Fiche et al., 2023), https://zenodo.org/record/8399376, CC BY 4.0 から変換。breaststroke_1 の動き（`--smooth 1 --speed 0.6`）。元データのNPZは収録しない。
- `浮く.vrma`：Kimodoによる `lie_back_sleep.npz` をVRMAへ変換し、ループ化。
- `眠る.vrma`：Kimodoによる `lie_side_sleep.npz` をVRMAへ変換し、肘の曲げを補正してループ化。
- `座る.vrma`：Kimodoによる `sit_ground.npz` の着座後（60フレーム以降）をVRMAへ変換し、ループ化。
- `伸び.vrma`：Kimodoによる `stretch_up0_401.npz`（2026-10-09ローカル生成）を上半身のみのVRMAへ変換。腰と脚の動きは含めない。

KimodoのソフトウェアはApache-2.0。モデルと生成物の利用・再配布条件は使用したKimodoのモデル・学習データのライセンスも別途確認すること（この文書は再配布許諾を保証しない）。
