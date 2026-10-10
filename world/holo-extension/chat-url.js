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

  // ProjectのURLは「g-p-<番号>」の後ろに名前が付くときと付かないときがあるので、番号だけで比べる。
  function sameProject(left, right) {
    const key = id => /^g-p-[0-9a-f]+/i.exec(id)?.[0].toLowerCase() ?? id;
    return left !== undefined && right !== undefined && key(left) === key(right);
  }

  const api = {
    parse: parsed,
    sameConversation(left, right) {
      const a = parsed(left);
      const b = parsed(right);
      return Boolean(a && b && a.id === b.id);
    },
    isProjectConversation(raw, projectId) {
      return sameProject(parsed(raw)?.projectId, projectId);
    },
    isProjectEntry(raw, projectId) {
      return sameProject(entryProjectId(raw), projectId);
    },
    projectIdFromEntry: entryProjectId,
  };

  globalThis.NiraiChatUrl = api;
})();
