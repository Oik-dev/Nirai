// Explicit local verification capability used only by smoke/tests.
import { randomUUID } from "node:crypto";
import { fingerprint } from "../shared/stable.js";
import { CapabilityRegistry, type Capability } from "../hub/capability.js";
import type { HoloDriver } from "../hub/engine.js";
import type { HubRuntime } from "../hub/runtime.js";
import type { HubCommandEnvelope, HoloTurnRecord } from "../shared/types.js";

export function verificationRegistry(): CapabilityRegistry {
  const registry = new CapabilityRegistry();
  const capability: Capability = {
    id: "verification",
    operations: new Map([
      ["verify", {
        side_effects: "none",
        resources: ["verification-result"],
        approval: "検証用の結果を作成・検査します。外部ファイルは変更しません。",
      }],
    ]),
    availability: () => ({ state: "ready" }),
    cancel: async () => ({ state: "Cancelled", effects: "none", cleanup_state: "clear" }),
    async invoke(operation, _input, context) {
      if (operation !== "verify") throw new Error("unsupported verification operation");
      const ref = `verification://${context.task_id}/result`;
      const hash = fingerprint("verified");
      return {
        state: "Completed",
        effects: "none",
        cleanup_state: "clear",
        result: { summary: "検証用の成果物と検査結果" },
        artifacts: [{ ref, fingerprint: hash, ownership: "temporary" }],
        verification: { kind: "verification", artifact_ref: ref, fingerprint: hash, passed: true },
      };
    },
  };
  registry.register(capability);
  return registry;
}


const command = (type: string, payload: Record<string, unknown> = {}): HubCommandEnvelope => ({
  protocol_version: 1,
  command_id: randomUUID(),
  issued_at: new Date().toISOString(),
  target: null,
  type,
  payload,
});

export function verificationHoloDriver(runtime: HubRuntime): HoloDriver {
  return {
    availability: () => ({ state: "ready" }),
    start(turn: HoloTurnRecord) {
      setImmediate(() => {
        try {
          const context = runtime.store.getTaskContext(turn.id) as {
            task: { objective: string | null };
            requests: Array<{ kind: string; state: string }>;
            actions: Array<{ operation: string; state: string }>;
          };
          if (context.task.objective?.includes("hold:")) return;

          const inputRequest = context.requests.find(item => item.kind === "input");
          if (!inputRequest) {
            runtime.service.handleTurnCommand(turn.id, command("RequestMasterInput", {
              prompt: "検証用の結果に付ける説明を入力してください。",
            }));
            runtime.store.finishHoloTurn(turn.id, "検証: Master入力待ち");
          } else {
            const verification = context.actions.find(run => run.operation === "verify");
            if (!verification) {
              runtime.service.handleTurnCommand(turn.id, command("InvokeCapability", {
                capability_id: "verification",
                operation: "verify",
                input: { target: "検証用の成果物" },
              }));
              runtime.store.finishHoloTurn(turn.id, "検証: 承認待ち");
            } else if (verification.state === "Completed") {
              const summary = "検証完了: Masterの回答とAction結果を確認しました。";
              runtime.service.handleTurnCommand(turn.id, command("CompleteTask", {
                result_summary: summary,
              }));
              runtime.store.finishHoloTurn(turn.id, summary);
            } else {
              runtime.store.finishHoloTurn(turn.id, "検証: Action完了待ち");
            }
          }
        } finally {
          runtime.engine.schedule();
          runtime.engine.onChanged();
        }
      });
    },
  };
}
