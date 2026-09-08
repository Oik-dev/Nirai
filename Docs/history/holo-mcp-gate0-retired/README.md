# Nirai Holo MCP

Holo Addon Gate 0で検証した旧MCP AdapterのSpike資料。

> **Retired:** `holo-mcp/`は現在のNirai製品経路では使用しない。ここに残る実装・コマンド・認証方式はGate 0時点の検証記録であり、現在の実行手順ではない。
>
> 現行のChatGPT Dive / Local Bridgeの入口はNiraiルートの`tools/holo-local-client.mjs`である。具体的な実行手順は現行の設計正本を参照し、このREADMEを運用手順として使用しない。

## 退役時点の構成

以下はGate 0で検証した旧構成の記録である。

```text
MCP Client
  ↓ Streamable HTTP
Holo MCP Adapter
  ↓ authenticated local WebSocket
Nirai Core
  ↓
Snapshot / World Say / Event Queue
```

公開Toolは次の4つだけ。

- `holo_attach`
- `holo_get_snapshot`
- `holo_say`
- `holo_wait_events`

Approval / Decision Toolは持たない。

## 認証境界

### MCP Client → Holo MCP Adapter

Gate 0では静的Bearerを検証してMCP SDKの`authInfo`へ変換する。

これは本番Authorization方式ではない。本番ではChatGPT / Secure MCP Tunnel側で利用可能な検証済みAuthorization方式へ置換する。

モデルがTool引数として`client_id`やScopeを指定する経路は作らない。

### Holo MCP Adapter → Nirai Core

Core起動時に生成・注入する内部SecretでAdapterを認証する。

このSecretはChatGPT会話、MCP Tool引数、Tool結果、World履歴へ出さない。

## Dive Binding

1. MasterがNirai UIで`Dive`を直接押す
2. Niraiが`current_dive_session_id`を生成
3. Coreが60秒・一回利用のAttach Windowを開く
4. 検証済みMCP Identityによる`holo_attach`が成功するとBinding確立
5. 新しいDive開始時は旧Bindingを失効

Holo Scope allowlist:

- `read_snapshot`
- `world_action`
- `task_control`

Approval Decision Scopeは存在しない。

## Local MCP Bridge（退役済み）

Gate 0では`holo-mcp/scripts/local-bridge-client.mjs`を使うDesktop fallbackを検証したが、この経路は製品経路から退役済みである。

旧Clientが使用していた`%LOCALAPPDATA%\Nirai\holo-mcp-bridge.json`、HTTP MCP endpoint、Bearer認証は現行CoreのLocal Bridgeと互換性がない。旧Clientを現在のNiraiへ接続する手順として使用してはならない。

現行ClientはCoreが生成するLocal Bridge descriptorを内部で扱う。descriptorや内部Secretを直接読み取ったり、ChatGPT会話・Tool引数・Tool結果へ露出させたりしない。

## Gate 0環境変数

- `NIRAI_CORE_URL`
- `NIRAI_HOLO_ADAPTER_SECRET`
- `NIRAI_HOLO_MCP_PORT`
- `NIRAI_HOLO_MCP_GATE0_BEARER`
- `NIRAI_HOLO_MCP_GATE0_CLIENT_ID`
- `NIRAI_HOLO_MCP_GATE0_SCOPES`
- `NIRAI_HOLO_MCP_GATE0_EXPIRES_AT`（任意）

## 依存

正式依存は`package.json`を正とする。

- `@modelcontextprotocol/sdk` 1.30.0
- `zod` 4.4.3

現在のLocal MCP安全Policyでは`npm install`が拒否されるため、Gate 0自動検証時だけ`D:\Products\Elpis\node_modules`に既存の同依存をread-only fallbackとして利用する。

通常の依存installが可能になった場合はNirai自身の`node_modules`を使用し、fallbackを本番依存にしない。

## Gate 0で成立済み

実MCP SDK Clientを使って同一Session内で以下を完走済み。

```text
listTools
→ holo_attach
→ holo_get_snapshot
→ holo_say
→ holo_wait_events(success)
→ holo_wait_events(timeout)
→ holo_wait_events(cancel)
→ holo_get_snapshot
```

誤BearerはHTTP 401、誤Core Adapter SecretはWebSocket 4003で拒否する。

## Process Lifecycle（Gate 0時点の記録）

Gate 0では、Coreが起動ごとに内部Adapter Secretを生成し、`holo-mcp/src/server.mjs`を子Processとして起動して同じSecretを環境変数で直接注入する方式を検証した。Secretをruntime、Log、Tool結果へ保存しない設計としていた。

Gate 0 Bearerも未指定時は起動ごとに生成して子Processへだけ渡し、手動QA時のみ起動環境から固定値を上書きする構成だった。

これらは退役済みSpikeの記録であり、現在のNirai製品Lifecycleを記述するものではない。

## 退役時点で未実施だった項目

以下はこのSpikeを退役した時点で残っていた検証候補であり、現在の製品TODOではない。

- Secure MCP Tunnelを利用するRemote MCP経路の実証
- Remote MCP採用時のGate 0静的Bearerから検証可能なAuthorizationへの置換
