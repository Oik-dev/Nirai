// ChatGPTの会話とProject入口のURLを1か所で読む。backgroundではmodule import、contentでは先にscriptとして読む。
(() => {
  function parsed(raw) {
    try {
      const url = new URL(raw);
      if (url.protocol !== "https:" || url.hostname !== "chatgpt.com") return undefined;
      const plain = /^\/c\/([0-9a-f-]+)\/?$/i.exec(url.pathname);
      if (plain) return { url: `${url.origin}/c/${plain[1]}`, id: plain[1] };
      const project = /^\/g\/([^/]+)\/c\/([0-9a-f-]+)\/?$/i.exec(url.pathname);
      if (project) return { url: `${url.origin}/g/${project[1]}/c/${project[2]}`, id: project[2], projectId: project[1] };
    } catch {
      // URLでなければ会話ではない。
    }
  }

  function entryProjectId(raw) {
    try {
      const url = new URL(raw);
      if (url.protocol !== "https:" || url.hostname !== "chatgpt.com") return undefined;
      return /^\/g\/([^/]+)\/project\/?$/i.exec(url.pathname)?.[1];
    } catch {
      return undefined;
    }
  }

  const api = {
    parse: parsed,
    sameConversation(left, right) {
      const a = parsed(left);
      const b = parsed(right);
      return Boolean(a && b && a.id === b.id);
    },
    isProjectConversation(raw, projectId) {
      return parsed(raw)?.projectId === projectId;
    },
    isProjectEntry(raw, projectId) {
      return entryProjectId(raw) === projectId;
    },
    projectIdFromEntry: entryProjectId,
  };

  globalThis.NiraiChatUrl = api;
})();
