# Holo Fresh統合監査 — 2026-09-14

判定: **SAFE（下記の隔離コピー・監査範囲）**。今回再現したP2 2件を修正後、確認範囲の残存P1/P2はなし。DNAは対象外。commit/pushは実施していない。

## 範囲と制約

Holoのattach期限・永続化・再起動復元、WorldのDive通知とAuto Resume、Workflow所有IDと取消経路、Local Client、World build完了判定、関連テストを現在のソースから確認した。過去のSAFE/NEEDS_FIX判定を現在の証拠として流用していない。

隔離コピーには独立したGit履歴がない。冒頭のGit確認は親Repositoryの変更パスを返したため、その後は隔離コピーのファイルだけを読み、対象16ファイルのSHA-256を末尾に記録した。HEADからの全hunkとの対応、削除やコピー漏れ、実Workspaceとの完全一致を保証する監査ではない。

Worldのnode_modulesが存在せず、ネットワークも利用できないため、World Vitest・TypeScript型検査・production build・実Electron/ChatGPT/MCP連携は未実施。下記の構文確認は型検査や製品動作確認の代替ではない。

## 今回のP2と修正

1. **中断された所有情報の書込みがロックを恒久的に塞ぐ。**
   owner.jsonを直接書き込む途中で終了すると、以降のreadOwnerがJSON構文例外を出し、stale期限後も回収できなかった。Workflow更新とWorld buildが失敗し続ける。途中JSONを置いた回帰は修正前にSyntaxErrorで失敗した。
   修正後は一意のownerファイルを一時ディレクトリ内で完成させ、非空ディレクトリをrenameで公開する。旧形式の破損JSONは猶予期間後に回収し、直近の不完全な所有情報には猶予を維持する。

2. **古いロック回収が新しい生存所有者を奪う。**
   dead PIDを確認してから共有lockPathをrenameするまでに、別プロセスが回収・再取得すると、新しい所有者まで移動・削除できた。同一leaseへの並行書込みや競合buildを許してしまう。dead PID確認直後に、実際の取得APIで準備した生存ロックへ置き換える回帰では、修正前の取得が成功して排他違反を再現した。
   修正後は観測した一意のownerファイルだけをunlinkし、空ディレクトリだけをrmdirする。新しい所有者は別名のファイルを持つため削除されない。解放途中の停止による空ディレクトリも直ちに回収できる。

今回の実装修正はtools/file-lock.mjs、回帰追加はtools/file-lock.test.mjs。外部依存は追加していない。

## 統合確認

- attachは元の絶対期限を維持し、同じDiveの再通知でpending/attachedを更新し直さない。binding保存に失敗した場合はpendingが維持され、再起動時は同じDiveのbindingが古いwindowより優先される。Coreの認証境界とLocal Client往復も既存回帰で確認した。
- Workflow完了はexact workflow_idを必須とし、古い完了要求で後続leaseを完了しない。World取消は同じLocal Client writerへ委譲され、Workflow書込みのロックを共有する。コピー内のMCP登録ソースとbootstrap/Auto ResumeのID受渡しも静的に照合した。
- World buildは入力をbuild前後で比較し、失敗・途中変更・競合時に成功stampを発行しない。内容が同じに戻った編集、mtimeを保った編集、リンクによる入力範囲脱出も既存回帰で確認した。実builderの代わりに注入したfixtureによる検証である。
- 既存のHolo_attach_workflow_review_2026-09-14.mdは以前のコードに対する履歴として保持した。その未解決3件に対応するID必須化、writer統合、build前後の入力検証は現在のコピーに存在する。本記録の制約を越えて過去判定を上書きしない。

## 今回の実測

| 検証 | 結果 |
| --- | --- |
| python -m pytest core/tests/test_holo.py -q -p no:cacheprovider | 49 passed |
| node --test tools/file-lock.test.mjs tools/world-build-state.test.mjs tools/holo-local-client.test.mjs | 25 passed |
| node --check: file-lock / world-build-state / holo-local-client | 3ファイル成功 |
| node --experimental-transform-types --check: HoloWebHost.ts / holoWeb.ts | 2ファイル成功 |

Node回帰には実子プロセス終了後のロック回収、4プロセスによる20回のread-modify-write、旧形式互換、timeout、破損所有情報の猶予、live owner置換競合を含む。Core suiteはロック修正前に実行した。ロック修正後は、それを利用するLocal Client/buildのNode回帰を再実行した。テスト用TEMP/TMPとpytest basetempは隔離コピー内に配置した。

## 対象内容のSHA-256

この表は実WorkspaceのGit fingerprintではなく、監査した隔離コピーの内容を識別する。監査記録自身は含めない。

| Path | SHA-256 |
| --- | --- |
| core/holo/auth.py | 8a7cd43d6d73d8b37889abb29dee32a5bcac568825bfed2be9fcb409de75f431 |
| core/server.py | d7e4e0599292f5222697ad5c8ee06095594cad7e0c6fdca558c5385c85f7f122 |
| core/tests/test_holo.py | d66fd3d5236b76c5e032509dacf71115848b100055b4581f0f321c289e48bf62 |
| tools/holo-local-client.mjs | 15ff13f0c58febdcdaaed153a229d6f1c03b27432a95c097cd8c87a8de3059b9 |
| tools/file-lock.mjs | c87f2ef4c0532de75f37367ae7f9b372c00065b34a2e50048489d7c09acab216 |
| tools/file-lock.test.mjs | 155364d0921247b3f1d2d55b1e44d07ef4fe65df3ae1a5b01d0b8c1c82e7f991 |
| tools/holo-local-client.test.mjs | 6be384e0b066630e45aa5286e2a1a3f77ae5fe3b6cb0a0236e7b81ca7ea2f05c |
| tools/world-build-state.mjs | d0537cf82cbb50e022e030c76a1f461980418716ffa028c957429d7e23cf13ed |
| tools/world-build-state.test.mjs | 037a6a746661de776df5a38472e0afdd3f10824d5acbfe59981a1f6d43b330ad |
| world/package.json | 1847f849c771a47cf9d351d6cd30735f00e79a2669d73216e3c49c3884bd3e5b |
| world/src/main/holo/HoloWebHost.ts | 60d1c86bf76498c58cd80f4a6f0bc60a8c3463cf7b952acadbdc9916d8df9c27 |
| world/src/main/holo/holoWeb.ts | 2333674d488fad439098470bac9b3ae8d5e4ef388ddac7195f0d6e5f755c9751 |
| world/tests/unit/HoloAddonHost.test.ts | b297a95bf7591b151bda46980f01422687022a0d79715c1e8af5d47caf66143c |
| world/tests/unit/HoloWeb.test.ts | be1307e7dd927ea2720fffd1527bcc5113751d125e8e36ec35ad51a68769e93f |
| Docs/詳細設計/12_HoloAddonとChatGPTDive.md | 4f78694264f6f029130d3ff75b8b855c250ba297683cae53acee3ff0322d5c13 |
| Docs/evidence/reports/Holo_attach_workflow_review_2026-09-14.md | f3ea33e6f60640421ed859de197cd3f29cd83d51369368af51f7a81504bb6d06 |

