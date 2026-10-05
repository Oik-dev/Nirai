import { test } from "node:test";
import assert from "node:assert/strict";
import "../holo-extension/room-url.js";

const roomUrl = (globalThis as typeof globalThis & { NiraiRoomUrl: any }).NiraiRoomUrl;
const PROJECT = "g-p-abc-nirai";

test("ChatGPTの通常会話とProject会話を同じ会話IDで比べられる", () => {
  const plain = "https://chatgpt.com/c/11111111-1111-1111-1111-111111111111";
  const project = `https://chatgpt.com/g/${PROJECT}/c/11111111-1111-1111-1111-111111111111`;
  assert.equal(roomUrl.parse(plain).id, "11111111-1111-1111-1111-111111111111");
  assert.equal(roomUrl.parse(project).projectId, PROJECT);
  assert.equal(roomUrl.sameConversation(plain, project), true);
});

test("Project入口とProject会話を別物として確かめる", () => {
  const entry = `https://chatgpt.com/g/${PROJECT}/project`;
  const room = `https://chatgpt.com/g/${PROJECT}/c/22222222-2222-2222-2222-222222222222`;
  assert.equal(roomUrl.projectIdFromEntry(entry), PROJECT);
  assert.equal(roomUrl.isProjectEntry(entry, PROJECT), true);
  assert.equal(roomUrl.isProjectConversation(room, PROJECT), true);
  assert.equal(roomUrl.isProjectConversation(room, "other"), false);
});
