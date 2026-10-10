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

test("ProjectのURLは名前の有無にかかわらず番号で同じProjectとみなす", () => {
  const id = "g-p-0123456789abcdef0123456789abcdef";
  const conversation = `https://chatgpt.com/g/${id}-nirai/c/33333333-3333-3333-3333-333333333333`;
  assert.equal(chatUrl.isProjectEntry(`https://chatgpt.com/g/${id}-nirai/project`, id), true);
  assert.equal(chatUrl.isProjectEntry(`https://chatgpt.com/g/${id}/project`, `${id}-nirai`), true);
  assert.equal(chatUrl.isProjectConversation(conversation, id), true);
  assert.equal(chatUrl.isProjectConversation(conversation, "g-p-fedcba9876543210fedcba9876543210"), false);
  assert.equal(chatUrl.isProjectConversation("https://chatgpt.com/c/33333333-3333-3333-3333-333333333333", id), false);
});
