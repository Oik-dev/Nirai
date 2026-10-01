import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const worldRulesPath = process.env.NIRAI_WORLD_RULES_PATH
  ?? fileURLToPath(new URL("../../../../WORLD_RULES.md", import.meta.url));
const worldRules = readFileSync(worldRulesPath, "utf8").trim();
if (!worldRules) throw new Error("WORLD_RULES.md is empty");
export const serverInstructions = `Nirai TaskではWORLD_RULESに従う。会話ContextはNiraiから届くMaster原文と現在のChatGPT会話とし、別Contextを再構成しない。Nirai MCPはTask制御やNirai固有能力が必要な場合に使う。依頼を達成した時は最終回答の前にCompleteTaskを呼び、成功後に通常のassistant本文で最終回答を書く。通常回答だけではTaskは完了しない。Masterの判断・回答が実際に必要な場合だけAwaitMasterReplyを呼び、質問を通常のassistant本文に書く。未完了のまま次Turnへ継続する場合は、AwaitMasterReplyを呼ばず通常回答でこのTurnを終える。自分の表情・衣装・アクセサリ・外見は利用可能なAvatar Capabilityの範囲で、自分のPersonaと会話・状況をもとに本人が選ぶ。瞬き・通常の視線追従等はAvatar Runtimeが担う。

${worldRules}`;

export const niraiCommandTool = {
  name: "nirai_command",
  description: "Nirai Turn control; pass turn_id. Index: InvokeCapability {capability_id,operation,input}=Capabilityを使う; GetRunResult {run_id,max_bytes}=Run結果を見る; AwaitMasterReply {}=Masterの判断・回答が必要な質問がある場合だけ呼び、質問は通常回答に書く。自動継続を待つためには使わない; CompleteTask {result_summary,completion?}=完了条件を固定し、続く最終回答が保存された時点でTask完了. local: read {path,max_bytes?}=読む; search {path,query,max_results?}=探す; apply_patch {changes:[{path,before_sha256,content}]}=修正; inspect {profile|run_id}=run_command前確認・Run状態確認; run_command {profile,source_fingerprint,timeout_ms?,output_bytes?}=登録済みbuild/test実行; cancel {run_id}=Run停止. avatar: inspect {}=自分の利用可能な表情・衣装・アクセサリ・外見、選択状態と表示確認を読む; set {model_id,expected_revision,appearance:{expression:{id,weight}|null,wardrobe:{item_id:boolean,...},choices?:{control_id:option_id,...}}}=自分の選択を保存。inspectのmodel_idとdesired.revisionを使う。wardrobeは全項目を指定。capabilities.controlsがあるモデルではchoicesに全controlのoption idを指定し、labelで意味を判断する。変更しない項目はdesired.appearanceから維持する。Mesh名やMorph名を指定しない。自己表現はResident本人が状況に応じて選ぶ。set成功は保存済みだけを示す。表示済みと伝える前にinspectのdisplay_appliedを確認する。Paths are workspace-relative. New command_id per request; reuse only for the same lost reply. result_summary is internal.",
  inputSchema: { type: "object", additionalProperties: false, required: ["turn_id", "envelope"], properties: {
    turn_id: { type: "string" },
    envelope: { type: "object", additionalProperties: false, required: ["command_id", "type", "payload"], properties: {
      command_id: { type: "string" }, type: { type: "string" }, payload: { type: "object" },
    } },
  } },
};

