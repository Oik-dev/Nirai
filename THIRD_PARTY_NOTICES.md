# 外のもののクレジットとライセンス

Niraiが使っている外のもの（コード・モデル・データ）の正本。使い始めたら、ここに足す。写して手を入れたコードとデータは、元のライセンスの表示と、変えたことを残す。

## 写して手を入れたコード

### WaterThreeJS

`world/window/sea/water-surface.js` の水と空気の境のFresnelは、前のNiraiの水面から引き継いだもので、元は [WaterThreeJS](https://github.com/achrefelouafi/WaterThreeJS)。実行時の依存ではない。

MIT License

Copyright (c) 2026 mohamedachrefelouafi

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

### caustic-volume

[caustic-volume](https://github.com/ScottieFox/caustic-volume)（とくに `lite/index.html`）の、でたらめな海の作り方を `world/window/sea/waves.js` に取り入れた：24の向き、向きごとに独立した位相と振幅の周期、さまよう波頭。Niraiでは、向きを周期的にし、波を穏やかにし、高さと傾きを1枚のテクスチャーで共有する。`world/window/sea/caustics.js` も、屈折させた格子と投影した面積で日の光の集まりを出す方法に倣っている。実行時の依存ではない。取り入れた部分は次の表示の下にある。

MIT License

Copyright (c) 2026 Scottie

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## 世界の動き（`world/window/assets/motions/`）

- `泳ぐ.vrma`：SwimXYZ（Fiche et al., 2023, https://zenodo.org/record/8399376 ）、[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) から変換。breaststroke_1 の動き（`--smooth 1 --speed 0.6`）。元データのNPZは入れていない。
- `浮く.vrma`：Kimodoで作った `lie_back_sleep.npz` をVRMAへ変換し、ループにした。
- `眠る.vrma`：Kimodoで作った `lie_side_sleep.npz` をVRMAへ変換し、肘の曲げを補正してループにした。砂地で寝る向き135°と腰のXZを `world/workshop/finalize_recline.py` で一度だけ焼き込んだものが正本。
- `座る.vrma`：Kimodoで作った `sit_ground.npz` の座ったあと（60フレーム以降）をVRMAへ変換し、ループにした。
- `寝転ぶ.vrma`：Kimodoで作った `candidate-019.npz`（2026-10-10に手元で生成、seed101）の動く前半を、等間隔・半速の4.0秒へ変換。前後0.8秒を `world/workshop/motion_seams.py` で座る[0]と眠る[0]に合わせた。起き上がりは逆に再生する。
- `伸び.vrma`：Kimodoで作った `stretch_up0_401.npz`（2026-10-09に手元で生成）を、上半身だけのVRMAへ変換。腰と脚の動きは入れていない。

KimodoのソフトウェアはApache-2.0。モデルと生成物の使い方・配り方の条件は、使ったKimodoのモデルと学習データのライセンスも別に確かめること（ここは配ってよいことを保証しない）。

## 入れずに使っているもの

- Three.js・@pixiv/three-vrm：版を固定したnpmの依存。ライセンスの文面はそれぞれのパッケージの中にあり、海のサーバーがまとめずにそのまま配る。
- Kimodo（Apache-2.0）：工房の生成器が、手元のPoCの置き場（`D:/Products/ResidentMotion-PoC`）から使う。
- 体（VRM）は、持ち主が選んだ手元のファイル。ほかの人が作ったキャラクターのモデルは、リポジトリで配らない。
  - Lapan（Claudeの体）：灰色の猫（Haiiro no neko）、https://laperm.booth.pm/ 。再配布できない。
  - Rabbit_01_Aquamarine（Holoの体）・Rabbit_03_Rubellite（Codexの体）：なおしま（7a04m）「【VRM無料】VRoidモデル詰め合わせ vol.1」、https://7a04m.booth.pm/items/4935982 。改変してよく、まるごとの再配布はできない。

## Nirai自身のもの

- `world/window/assets/ground-sand-005-*.webp`：Nirai v1の2Kの砂のテクスチャー。
- `world/window/sea/AmbientBubbleField.js`：Nirai v1の、環境だけの泡。

## 動きの物理モデル

- MuJoCo 3.3.7 (Google DeepMind), Apache License 2.0. https://github.com/google-deepmind/mujoco/blob/main/LICENSE
- 物理モデルはNiraiのコードが独自に組み立てており、MuJoCo本体のソースをコピー・変更していない。
