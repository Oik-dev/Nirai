// Explicit verification composition only. No provider impersonation or external IO.
import { randomUUID } from "node:crypto";
import { fingerprint } from "../shared/stable.js";
import type { CompletionEvidence, RunRecord, TaskRecord } from "../shared/types.js";
import { CapabilityRegistry, type Capability } from "../hub/capability.js";

export function verificationRegistry(): CapabilityRegistry {
  const registry = new CapabilityRegistry();
  const capability: Capability = {
    id: "verification",
    operations: new Map([
      ["respond", { side_effects: "none", resources: ["verification-response"], delivery: true }],
      ["verify", { side_effects: "none", resources: ["verification-result"], approval: "検証用の結果を作成・検査します。外部ファイルは変更しません。" }],
    ]),
    availability: () => ({ state: "ready" }),
    cancel: async () => ({ state: "Cancelled", effects: "none", cleanup_state: "clear" }),
    async invoke(operation, _input, context) {
      if (operation === "verify") return { state: "Completed", effects: "none", cleanup_state: "clear",
        result: { summary: "検証用の成果物と検査結果" },
        artifacts: [{ ref: `verification://${context.task_id}/result`, fingerprint: fingerprint("verified"), ownership: "temporary" }],
        verification: { kind: "verification", artifact_ref: `verification://${context.task_id}/result`, fingerprint: fingerprint("verified"), passed: true } };
      context.observeDelivery!("started");
      context.observeDelivery!("acknowledged");
      const call = (type: string, payload: Record<string, unknown> = {}) => context.command!({ protocol_version: 1,
        command_id: randomUUID(), issued_at: new Date().toISOString(), type, target: context.task_id,
        payload: { task_id: context.task_id, ...payload } });
      call("AcceptResponse");
      const current = call("GetTaskContext") as { task: TaskRecord; instruction_seq: number; wake_seq: number; requests: Array<{ id: string; kind: string; state: string }>; runs: RunRecord[] };
      if (current.task.objective?.includes("hold")) return { accepted: true };
      const criteria = [{ id: "verified-result", text: "検証用の成果物と検査結果が一致する", required: true, verification_kind: "verification" }];
      call("RefineTaskDefinition", { title: "M1 / M2 検証Task", completion_criteria: criteria });
      const finish = (waitFor: string) => call("FinishResponse", { disposition: "wait", summary: "検証用Capability: Masterの回答を待っています。",
        instruction_seq: current.instruction_seq, wake_seq: current.wake_seq, wait_for: [waitFor] });
      if (!current.requests.some(item => item.kind === "input")) {
        const requested = call("RequestMasterInput", { prompt: "検証用の結果に付ける説明を入力してください。" });
        finish(String(requested.request_id));
      } else {
        const action = current.runs.find(run => run.operation === "verify" && run.state === "Completed");
        if (!action) {
          const child = call("InvokeCapability", { capability_id: "verification", operation: "verify", input: { target: "検証用の成果物", expected: fingerprint("verified") } });
          finish(String(child.run_id));
        } else {
          const completion: CompletionEvidence[] = [{ criterion_id: "verified-result", artifact_ref: `verification://${context.task_id}/result`,
            fingerprint: fingerprint("verified"), verification_run_id: action.id }];
          call("CompleteTask", { result_summary: "検証完了: 質問への回答、具体的な承認、子Runの実結果と成果物の照合を確認しました。",
            instruction_seq: current.instruction_seq, wake_seq: current.wake_seq, completion });
        }
      }
      return { accepted: true };
    },
  };
  registry.register(capability);
  registry.bindResident("holo", capability.id);
  return registry;
}
