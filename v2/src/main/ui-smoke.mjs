import { app } from "electron/main";
import { randomUUID } from "node:crypto";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { checkPresentation } from "./ui-presentation-checks.mjs";

async function waitFor(predicate, label, timeout = 8000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    try { if (await predicate()) return; } catch { /* A reconnect may still be pending. */ }
    await new Promise(resolve => setTimeout(resolve, 40));
  }
  throw new Error(`UI smoke timed out: ${label}`);
}

export async function runUiSmoke(window, { request, userData, interruptNextReply, finish }) {
  if (process.env.NIRAI_V2_WORLD_SMOKE === '1') {
    const { runWorldSmoke } = await import('./world-smoke.mjs');
    return runWorldSmoke(window, { request, interruptNextReply, finish });
  }
  // The isolated test window may be covered by the real application. Keep
  // animation-frame based layout checks running without stealing user focus.
  window.webContents.setBackgroundThrottling(false);
  if (process.env.NIRAI_V2_UI_CAPTURE_DIR) window.show();
  const js = source => window.webContents.executeJavaScript(source);
  const click = id => js(`document.getElementById(${JSON.stringify(id)}).click()`);
  const snapshot = () => request("snapshot");
  const idle = () => waitFor(() => js("document.getElementById('dashboard').getAttribute('aria-busy') !== 'true'"), "UI idle");
  const chat = async text => {
    await idle();
    const state = await snapshot();
    const selectedTaskId = await js("sessionStorage.getItem('nirai:v2:selected-task')");
    const task = state.tasks.find(item => item.id === selectedTaskId) ?? state.tasks.at(-1);
    if (!task) throw new Error("UI smoke has no selected Task");
    await request("command", {
      envelope: {
        protocol_version: 1,
        command_id: randomUUID(),
        issued_at: new Date().toISOString(),
        type: "SendConversationMessage",
        target: task.id,
        expected_revision: task.revision,
        payload: { task_id: task.id, sender: "master", content: text },
      },
    });
    await waitFor(async () => (await snapshot()).messages.some(message =>
      message.conversation_id === task.conversation_id && message.sender === "master" && message.content === text
    ), "Master message saved");
    await idle();
  };
  const capture = async name => {
    const root = process.env.NIRAI_V2_UI_CAPTURE_DIR;
    if (!root) return;
    mkdirSync(root, { recursive: true });
    await js("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))");
    await new Promise(resolve => setTimeout(resolve, 150));
    console.log(`Capturing ${name}`);
    writeFileSync(join(root, name), (await window.webContents.capturePage(undefined, { stayAwake: true })).toPNG());
  };
  await waitFor(() => js("Boolean(window.niraiDashboard) && document.getElementById('chatTaskMeta').textContent.includes('新しいTaskを作成できます')"), "initial Snapshot");
  if (!(await js("document.getElementById('chatInput').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',shiftKey:true,bubbles:true,cancelable:true}))"))) throw new Error("Shift+Enter was prevented");
  if (app.getPath("userData") !== userData) throw new Error("Electron Data Root is not isolated");
  await waitFor(() => js("Array.from(document.images).every(image => image.complete && image.naturalWidth > 0)"), "images");
  await click("settingsButton");
  if (await js("Boolean(document.querySelector('[data-holo-open]'))")) throw new Error("manual Holo display control returned");
  await js("document.getElementById('holoAppName').value='nirai-v2-fixture'; document.querySelector('[data-holo-save]').click()");
  await waitFor(async () => (await snapshot()).settings.value.holo_app_name === "nirai-v2-fixture", "Holo setting saved through Hub");
  await idle();
  await capture("holo-settings.png");
  await click("residentSettingsClose");
  await js("document.getElementById('addTaskButton').click(); document.getElementById('addTaskButton').click()");
  await waitFor(async () => (await snapshot()).tasks.length === 1, "single draft");
  await idle();
  if (await js("!document.getElementById('pauseButton').hidden")) throw new Error("Draft exposes Resume");
  await chat("M2 UI smoke");
  if (await js("Boolean(document.querySelector('[data-task-action=rebind], [data-task-rebind-url]'))")) {
    throw new Error("manual Holo conversation recovery UI must not exist");
  }
  await waitFor(async () => {
    const state = await snapshot();
    return state.holo_turns.some(turn => turn.await_master && turn.end_reason === "assistant")
      && state.messages.some(message => message.content === "検証用の結果に付ける説明を入力してください。");
  }, "question in normal Chat with Master handoff");
  if ((await snapshot()).pending_requests.length !== 0) throw new Error("conversation question created a Master Request");
  if (await js("Boolean(document.querySelector('[data-request-answer]'))")) throw new Error("dedicated question UI returned");
  window.webContents.reload();
  await waitFor(() => js("document.getElementById('chatPane').classList.contains('is-holo') && !document.getElementById('holoSurface').hidden && !document.querySelector('[data-request-answer]')"), "Reload restored Holo native surface shell");
  await click("pauseButton");
  await waitFor(async () => (await snapshot()).tasks[0].state === "Paused", "Pause");
  await idle();
  await chat("保存した結果を検証");
  if ((await snapshot()).runs.length !== 0) throw new Error("Paused Chat reply executed an Action");
  await idle();
  await click("pauseButton");
  await waitFor(async () => (await snapshot()).pending_requests[0]?.kind === "approval", "concrete approval");
  await idle();
  await click("resumeButton");
  await waitFor(async () => (await snapshot()).tasks[0].resume_enabled, "Resume ON");
  await idle();
  await click("resumeButton");
  await waitFor(async () => !(await snapshot()).tasks[0].resume_enabled, "Resume OFF");
  await idle();
  await click("resumeButton");
  await waitFor(async () => (await snapshot()).tasks[0].resume_enabled, "Resume ON for continuation");
  await idle();
  await js("document.querySelector('[data-task-action=complete]').click()");
  await waitFor(() => js("document.getElementById('hubNotice')?.textContent.includes('開始待ちの作業')"), "premature completion rejected");
  await idle();
  await capture("landscape-check.png");
  window.setSize(620, 980);
  await waitFor(() => js(`(()=>{const dashboard=document.getElementById('dashboard').getBoundingClientRect(); const chat=document.getElementById('chatPane').getBoundingClientRect(); return dashboard.top >= innerHeight * .3 && chat.width > innerWidth * .85 && getComputedStyle(document.getElementById('taskPane')).display === 'none'})()`), "portrait keeps World above a readable conversation");
  await capture("portrait-check.png");
  await click("showTasksButton");
  await waitFor(() => js("document.getElementById('taskPane').getBoundingClientRect().width > innerWidth * .85 && getComputedStyle(document.getElementById('chatPane')).display === 'none'"), "portrait task switch keeps controls outside the native view");
  await capture("portrait-tasks.png");
  await idle();
  await js("document.querySelector('[data-request-action=approve]').click(); document.querySelector('[data-request-action=approve]').click()");
  await waitFor(async () => (await snapshot()).tasks[0].state === "Completed", "verified Task completion");
  if ((await snapshot()).runs.length !== 1) throw new Error("duplicate or missing Action Run");
  if ((await snapshot()).holo_turns.length !== 3) throw new Error("duplicate or missing Holo Turn");
  const completedState = await snapshot();
  const completedTask = completedState.tasks[0];
  const completedTaskId = completedTask.id;
  const finalHoloMessage = [...completedState.messages].reverse().find(message =>
    message.conversation_id === completedTask.conversation_id && !['master', 'control', 'system'].includes(message.sender)
  );
  if (!finalHoloMessage) throw new Error("completed Task has no Holo Chat message");
  await waitFor(() => js(`document.getElementById('taskAccordion').textContent.includes('完了')
    && document.getElementById('chatPane').classList.contains('is-holo')
    && !document.getElementById('holoSurface').hidden
    && Boolean(document.querySelector('[data-task-action=restart]'))
    && Boolean(document.querySelector('[data-task-action=close]'))`), "integrated completed Task and Holo native surface shell");
  if (completedTask.result_summary) {
    const summary = JSON.stringify(completedTask.result_summary);
    if (await js(`document.querySelector('[data-task-id="${completedTaskId}"]')?.textContent.includes(${summary})`)) {
      throw new Error("internal result_summary leaked into the Task UI");
    }
    if (await js(`document.getElementById('holoSurface').textContent.includes(${summary})`)) {
      throw new Error("internal result_summary leaked into Holo surface shell");
    }
  }
  if (await js("Boolean(document.getElementById('archiveTab'))")) throw new Error("ARCHIVE tab returned");
  await capture("portrait-completed.png");
  window.setSize(1500, 930);
  await idle();
  await click("addTaskButton");
  await waitFor(async () => (await snapshot()).tasks.length === 2, "second Task");
  await chat("hold: 停止・接続復旧の検証");
  await waitFor(async () => {
    const state = await snapshot()
    return state.holo_turns.some(turn => turn.task_id === state.tasks[1].id && !turn.ended_at)
  }, "active Holo Turn");
  await idle();
  await js("document.querySelector('[data-task-action=complete]').click()");
  await idle();
  if ((await snapshot()).tasks[1].state !== "Running") throw new Error("active Holo Turn completed from UI");
  interruptNextReply();
  await click("pauseButton");
  await waitFor(() => js("!localStorage.getItem('nirai:v2:uncertain-command-id') && document.getElementById('connectionStatus').textContent === '検証構成'"), "receipt reconciliation after connection loss");
  const recovered = await snapshot();
  if (recovered.tasks[1].state !== "Paused") throw new Error("accepted Pause was not recovered after connection loss");
  await idle();
  await waitFor(() => js("document.getElementById('chatPane').classList.contains('is-holo') && !document.getElementById('holoSurface').hidden"), "Holo surface shell restored after reconnect");
  await js("window.confirm=()=>true; document.querySelector('[data-task-action=cancel]').click()");
  await waitFor(async () => (await snapshot()).tasks[1].state === "Cancelled", "Cancel");
  const cancelledTaskId = (await snapshot()).tasks[1].id;
  await waitFor(() => js("document.getElementById('taskAccordion').textContent.includes('取消済み') && document.getElementById('taskAccordion').textContent.includes('完了')"), "terminal Tasks stay in unified list");

  await js(`document.querySelector('[data-task-id="${completedTaskId}"] [data-task-toggle]').click()`);
  await waitFor(() => js(`Boolean(document.querySelector('[data-task-id="${completedTaskId}"] [data-task-action=restart]'))`), "completed Task restart control");
  await js(`document.querySelector('[data-task-id="${completedTaskId}"] [data-task-action=restart]').click()`);
  await waitFor(async () => {
    const state = await snapshot();
    return state.tasks.length === 3 && state.tasks.some(task =>
      task.id !== completedTaskId && task.id !== cancelledTaskId && task.initial_message_id === null
    );
  }, "Holo restart creates a fresh draft Task without a synthetic continuation message");
  const restartedTaskId = (await snapshot()).tasks.find(task => task.id !== completedTaskId && task.id !== cancelledTaskId).id;
  await idle();
  await js("window.confirm=()=>true; document.querySelector('[data-task-action=cancel]').click()");
  await waitFor(async () => (await snapshot()).tasks.find(task => task.id === restartedTaskId)?.state === "Cancelled", "restarted Task can be cancelled independently");
  await idle();

  await js(`document.querySelector('[data-task-id="${completedTaskId}"] [data-task-toggle]').click()`);
  await waitFor(() => js(`Boolean(document.querySelector('[data-task-id="${completedTaskId}"] [data-task-action=close]'))`), "completed Task close control");
  await js(`document.querySelector('[data-task-id="${completedTaskId}"] [data-task-action=close]').click()`);
  await waitFor(() => js(`!document.querySelector('[data-task-id="${completedTaskId}"]')`), "completed Task closed from Task list");
  await capture("landscape-terminal.png");
  await click("noticeDismiss");
  await checkPresentation(window, js, capture);
  console.log("M2 UI: Holo native surface shell, Shift+Enter allowance, double click, Master handoff, approval, verified completion, Reload, Pause/Resume, ON/OFF, fresh-draft restart, portrait layout, disconnect and receipt reconciliation passed");
  await finish();
}
