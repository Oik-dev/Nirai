import { test } from "node:test";
import assert from "node:assert/strict";
import "../holo-extension/chat-url.js";

const chatUrl = (globalThis as typeof globalThis & { NiraiChatUrl: any }).NiraiChatUrl;
const PROJECT = "g-p-abc-nirai";

test("ChatGPTの通常会話とProject会話を同じ会話IDで比べられる", () => {
  const plain = "https://chatgpt.com/c/11111111-1111-1111-1111-111111111111";
  const project = `https://chatgpt.com/g/${PROJECT}/c/11111111-1111-1111-1111-111111111111`;
  assert.equal(chatUrl.parse(plain).id, "11111111-1111-1111-1111-111111111111");
  assert.equal(chatUrl.parse(project).projectId, PROJECT);
  assert.equal(chatUrl.sameConversation(plain, project), true);
});

test("Project入口とProject会話を別物として確かめる", () => {
  const entry = `https://chatgpt.com/g/${PROJECT}/project`;
  const conversation = `https://chatgpt.com/g/${PROJECT}/c/22222222-2222-2222-2222-222222222222`;
  assert.equal(chatUrl.projectIdFromEntry(entry), PROJECT);
  assert.equal(chatUrl.isProjectEntry(entry, PROJECT), true);
  assert.equal(chatUrl.isProjectConversation(conversation, PROJECT), true);
  assert.equal(chatUrl.isProjectConversation(conversation, "other"), false);
});
