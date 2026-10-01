import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { HubRuntime } from '../out/src/hub/runtime.js';

// Login/catalog inspection is the default. Live inference requires an explicit opt-in.
const live = process.env.NIRAI_V2_CODEX_LIVE === '1';
const taskOnly = process.argv.includes('--task-only');
assert.ok(process.argv.slice(2).every(argument => argument === '--task-only'), 'Unknown Codex check option');
const root = await mkdtemp(join(tmpdir(), 'nirai-v2-codex-live-'));
let runtime;
let workspace;
const envelope = (type, payload, expectedRevision) => ({ protocol_version: 1, command_id: randomUUID(),
  issued_at: new Date().toISOString(), target: null, type, payload,
  ...(expectedRevision === undefined ? {} : { expected_revision: expectedRevision }) });
const waitFor = async (predicate, description) => {
  const deadline = Date.now() + 100_000;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error(`Codex live check timed out: ${description}`);
};
try {
  runtime = await HubRuntime.start(root);
  await runtime.conversation.providers.refresh('codex');
  const connection = runtime.conversation.providers.list().find(item => item.id === 'codex');
  assert.equal(connection.availability.state, 'ready', connection.availability.reason);
  assert.ok(connection.models.length);
  console.log(`Codex CLI: ChatGPT login and ${connection.models.length} models confirmed; inference=${live}`);
  if (live) {
    // Keep a failed Task from starting additional billed turns during this check.
    const provider = runtime.conversation.providers.get('codex');
    const run = provider.run.bind(provider);
    let generations = 0;
    provider.run = (...arguments_) => {
      if (generations >= (taskOnly ? 1 : 3)) return Promise.reject(new Error('Live generation limit reached'));
      generations++;
      return run(...arguments_);
    };
    const handleTurnCommand = runtime.service.handleTurnCommand.bind(runtime.service);
    runtime.service.handleTurnCommand = (turnId, command) => {
      try {
        const result = handleTurnCommand(turnId, command);
        console.log('Live Task Tool:', JSON.stringify({ type: command.type, run_id: result.run_id,
          completion_pending: result.completion_pending, state: result.state }));
        return result;
      } catch (error) {
        console.log('Live Task Tool rejected:', JSON.stringify({ type: command.type, reason: error.message.slice(0, 256) }));
        throw error;
      }
    };
    const send = (type, payload, expectedRevision) => runtime.service.handleMasterCommand(envelope(type, payload, expectedRevision));
    send('CreateResident', { id: 'codex-live', display_name: 'Codex接続検証', capability_id: 'codex' });
    const originalTaskCount = runtime.store.listTasks().length;
    const originalRunCount = runtime.store.listRuns().length;
    for (const channel of taskOnly ? [] : ['whisper', 'say']) {
      const expected = `Codex ${channel}接続OK`;
      const input = send('SendChatMessage', { channel, ...(channel === 'whisper' ? { resident_id: 'codex-live' } : {}),
        content: `接続確認です。返答は「${expected}」の文字だけにしてください。` });
      await runtime.conversation.idle();
      const response = runtime.store.getChatResponse(input.message_id, 'codex-live');
      assert.equal(response.state, 'completed', response.error);
      const context = runtime.store.getChatContext(input.conversation_id, 'codex-live');
      assert.equal(context.messages.find(message => message.reply_to_message_id === input.message_id && message.sender === 'codex-live')?.content, expected);
    }
    assert.equal(runtime.store.listTasks().length, originalTaskCount);
    assert.equal(runtime.store.listRuns().length, originalRunCount);
    if (!taskOnly) console.log('Live Say/Whisper: original destination, exact final replies, no Task/Action grants passed');

    workspace = await mkdtemp(join(tmpdir(), 'nirai-v2-codex-workspace-'));
    await writeFile(join(workspace, 'fixture.txt'), 'Codex Task接続OK', 'utf8');
    const settings = runtime.store.getSettings();
    runtime.store.updateSettings({ workspace_scope: workspace }, settings.revision);
    const task = runtime.store.createTask('codex-live');
    send('SendConversationMessage', { task_id: task.id, sender: 'master',
      content: 'これはTask接続確認です。Niraiのlocal.readでfixture.txtを一度だけ読み、Run結果を確認してください。ファイルを変更しないでください。内容を確認したらCompleteTaskを呼び、最終返答はファイル内容の文字だけにしてください。' }, task.revision);
    await waitFor(() => runtime.store.getTask(task.id)?.state === 'Completed'
      || runtime.store.listHoloTurns(task.id).some(turn => turn.ended_at), 'Task final reply');
    const messages = runtime.store.snapshot().messages.filter(message => message.conversation_id === task.conversation_id);
    console.log('Live Task observation:', JSON.stringify({ generations, state: runtime.store.getTask(task.id).state,
      turns: runtime.store.listHoloTurns(task.id).map(turn => ({ end_reason: turn.end_reason })),
      runs: runtime.store.listRuns(task.id).map(run => ({ capability_id: run.capability_id, operation: run.operation,
        state: run.state, error_json: run.error_json })),
      replies: messages.filter(message => message.sender === 'codex-live').map(message => message.content) }));
    assert.equal(runtime.store.getTask(task.id).state, 'Completed', runtime.store.listHoloTurns(task.id).at(-1)?.end_reason);
    assert.equal(messages.filter(message => message.sender === 'codex-live').length, 1);
    assert.equal(messages.find(message => message.sender === 'codex-live')?.content, 'Codex Task接続OK');
    const reads = runtime.store.listRuns(task.id).filter(run => run.capability_id === 'local' && run.operation === 'read');
    assert.equal(reads.length, 1);
    assert.equal(reads[0].state, 'Completed');
    assert.equal(reads[0].effects, 'none');
    assert.equal(runtime.store.getTask(task.id).resume_enabled, false);
    console.log('Live Task: one Hub local.read, saved Run result, CompleteTask plus exact assistant reply passed');
  }
} finally {
  await runtime?.close();
  if (workspace) await rm(workspace, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  await rm(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
}
