import { app } from "electron/main";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

async function waitFor(predicate, label, timeout = 8000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    try { if (await predicate()) return; } catch { /* A reconnect may still be pending. */ }
    await new Promise(resolve => setTimeout(resolve, 40));
  }
  throw new Error(`UI smoke timed out: ${label}`);
}

export async function runUiSmoke(window, { request, userData, interruptNextReply, finish }) {
  if (process.env.NIRAI_V2_UI_CAPTURE_DIR) window.showInactive();
  const js = source => window.webContents.executeJavaScript(source);
  const click = id => js(`document.getElementById(${JSON.stringify(id)}).click()`);
  const snapshot = () => request("snapshot");
  const idle = () => waitFor(() => js("document.getElementById('dashboard').getAttribute('aria-busy') !== 'true'"), "UI idle");
  const chat = async text => {
    await idle();
    await js(`(()=>{const input=document.getElementById('chatInput'); input.focus(); input.value=${JSON.stringify(text)}; input.dispatchEvent(new Event('input',{bubbles:true})); input.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true}))})()`);
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
  await waitFor(async () => (await snapshot()).pending_requests[0]?.kind === "input", "question from Capability");
  await chat("CHECK中の通常Chatです。質問への回答とは別に保存してください。");
  if ((await snapshot()).pending_requests.length !== 1) throw new Error("ordinary Chat consumed CHECK");
  await waitFor(() => js("Boolean(document.querySelector('[data-request-answer]'))"), "question field");
  window.webContents.reload();
  await waitFor(() => js("Boolean(document.querySelector('[data-request-answer]')) && document.getElementById('chatMessages').textContent.includes('CHECK中')"), "Reload persisted question and Chat");
  await click("pauseButton");
  await waitFor(async () => (await snapshot()).tasks[0].state === "Paused", "Pause");
  await idle();
  await js("document.querySelector('[data-request-answer]').value='保存した結果を検証'; document.querySelector('[data-request-action=answer]').click()");
  await waitFor(async () => (await snapshot()).pending_requests.length === 0, "answer while Paused");
  if ((await snapshot()).runs.length !== 0) throw new Error("Paused answer executed an Action");
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
  await waitFor(() => js(`(()=>{const dashboard=document.getElementById('dashboard').getBoundingClientRect(); const task=document.getElementById('taskPane').getBoundingClientRect(); const chat=document.getElementById('chatPane').getBoundingClientRect(); return dashboard.top >= innerHeight * .3 && Math.abs(task.top-chat.top) < 4 && task.right <= chat.left + 1})()`), "portrait lower dashboard layout");
  await capture("portrait-check.png");
  await idle();
  await js("document.querySelector('[data-request-action=approve]').click(); document.querySelector('[data-request-action=approve]').click()");
  await waitFor(async () => (await snapshot()).tasks[0].state === "Completed", "verified Task completion");
  if ((await snapshot()).runs.length !== 1) throw new Error("duplicate or missing Action Run");
  if ((await snapshot()).holo_turns.length !== 3) throw new Error("duplicate or missing Holo Turn");
  const completedTaskId = (await snapshot()).tasks[0].id;
  await waitFor(() => js("document.getElementById('taskAccordion').textContent.includes('Completed') && document.getElementById('chatMessages').textContent.includes('検証完了') && Boolean(document.querySelector('[data-task-action=restart]')) && Boolean(document.querySelector('[data-task-action=close]'))"), "integrated completed Task and result Message");
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
  await chat("受付結果が不明になっても、この指示を二重に保存しない");
  await waitFor(() => js("!localStorage.getItem('nirai:v2:uncertain-command-id') && document.getElementById('connectionStatus').textContent === '検証構成'"), "receipt reconciliation after connection loss");
  const recovered = await snapshot();
  if (recovered.tasks[1].state !== "Paused") throw new Error("connection loss did not pause before DB close");
  if (recovered.messages.filter(message => message.content.startsWith("受付結果が不明")).length !== 1) throw new Error("uncertain command duplicated");
  await idle();
  await waitFor(() => js("document.activeElement?.id === 'chatInput'"), "chat focus restored after reconnect");
  await js("window.confirm=()=>true; document.querySelector('[data-task-action=cancel]').click()");
  await waitFor(async () => (await snapshot()).tasks[1].state === "Cancelled", "Cancel");
  const cancelledTaskId = (await snapshot()).tasks[1].id;
  await waitFor(() => js("document.getElementById('taskAccordion').textContent.includes('Cancelled') && document.getElementById('taskAccordion').textContent.includes('Completed')"), "terminal Tasks stay in unified list");

  await js(`document.querySelector('[data-task-id="${completedTaskId}"] [data-task-toggle]').click()`);
  await waitFor(() => js(`Boolean(document.querySelector('[data-task-id="${completedTaskId}"] [data-task-action=restart]'))`), "completed Task restart control");
  await js(`document.querySelector('[data-task-id="${completedTaskId}"] [data-task-action=restart]').click()`);
  await waitFor(async () => {
    const state = await snapshot();
    return state.tasks.length === 3 && state.tasks.some(task => task.id !== completedTaskId && task.id !== cancelledTaskId && task.initial_message_id);
  }, "restart creates a new Task with a continuation instruction");
  const restartedTaskId = (await snapshot()).tasks.find(task => task.id !== completedTaskId && task.id !== cancelledTaskId).id;
  await idle();
  await js("window.confirm=()=>true; document.querySelector('[data-task-action=cancel]').click()");
  await waitFor(async () => (await snapshot()).tasks.find(task => task.id === restartedTaskId)?.state === "Cancelled", "restarted Task can be cancelled independently");

  await js(`document.querySelector('[data-task-id="${completedTaskId}"] [data-task-toggle]').click()`);
  await waitFor(() => js(`Boolean(document.querySelector('[data-task-id="${completedTaskId}"] [data-task-action=close]'))`), "completed Task close control");
  await js(`document.querySelector('[data-task-id="${completedTaskId}"] [data-task-action=close]').click()`);
  await waitFor(() => js(`!document.querySelector('[data-task-id="${completedTaskId}"]')`), "completed Task closed from Task list");
  await capture("landscape-terminal.png");
  console.log("M2 UI: Enter send, Shift+Enter newline allowance, double click, question, approval, verified completion, Chat/Reload, Pause/Resume, ON/OFF, unified terminal list with restart/close, portrait lower layout, disconnect and receipt reconciliation passed");
  await finish();
}
