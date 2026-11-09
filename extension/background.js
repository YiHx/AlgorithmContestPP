// 算法竞赛++ 浏览器桥接 - background service worker
// 职责：发现本机 Engine（127.0.0.1:8765..8774）、缓存会话 token、代 content script 发 POST。
// 为什么由 background 发请求：HTTPS 页面里 fetch http://127.0.0.1 属于混合内容，
// 而扩展的 service worker 不受页面混合内容限制。

const PORTS = Array.from({ length: 10 }, (_, i) => 8765 + i);
let cached = null; // { base, session }
let collector = null;
let collectionStatus = null;

function claimCollector(page, session, tab) {
  if (!tab || page.platform !== session.platform || page.contestId !== String(session.contest_id)) {
    return false;
  }
  const now = Date.now();
  if (collector?.token === session.token && collector.tabId !== tab.id
      && collector.expiresAt > now && !(tab.active && !collector.active)) {
    return false;
  }
  collector = { token: session.token, tabId: tab.id, active: tab.active, expiresAt: now + 12000 };
  return true;
}

async function fetchJson(url, options) {
  const resp = await fetch(url, Object.assign({ cache: "no-store" }, options || {}));
  if (!resp.ok) throw new Error(`${url} -> ${resp.status}`);
  return resp.json();
}

async function discover() {
  for (const port of PORTS) {
    const base = `http://127.0.0.1:${port}`;
    try {
      await fetchJson(base + "/health", { method: "GET" });
      const session = await fetchJson(base + "/session");
      cached = { base, session };
      return cached;
    } catch (e) {
      // 该端口没有引擎，继续下一个
    }
  }
  cached = null;
  return null;
}

async function refreshSession() {
  if (!cached) return discover();
  try {
    cached.session = await fetchJson(cached.base + "/session");
    return cached;
  } catch (e) {
    cached = null;
    return discover();
  }
}

async function post(path, payload, token) {
  if (!cached || !cached.session) {
    if (!(await discover())) return { ok: false, error: "engine not found" };
  }
  if (token && token !== cached.session.token) return { ok: false, error: "session changed" };
  try {
    const resp = await fetch(cached.base + path, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-OJHC-Token": cached.session.token
      },
      body: JSON.stringify(payload)
    });
    const text = await resp.text();
    if (!resp.ok) return { ok: false, status: resp.status, error: text.slice(0, 200) };
    return { ok: true, data: text ? JSON.parse(text) : {} };
  } catch (e) {
    cached = null;
    return { ok: false, error: String(e) };
  }
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    try {
      if (msg && msg.type === "get_session") {
        const result = msg.force ? await discover() : await refreshSession();
        const session = result?.session;
        if (!session || (msg.collector && !claimCollector(msg.collector, session, sender.tab))) {
          sendResponse(null);
        } else if (msg.includeCollector) {
          const status = collectionStatus?.token === session.token ? collectionStatus : null;
          sendResponse({ ...session, collector: status });
        } else {
          sendResponse(session);
        }
      } else if (msg && msg.type === "collector_status") {
        if (collector?.token === msg.token && collector.tabId === sender.tab?.id) {
          const lastSuccessAt = collectionStatus?.token === msg.token ? collectionStatus.lastSuccessAt : null;
          collectionStatus = {
            token: msg.token,
            lastSuccessAt: msg.ok ? Date.now() : lastSuccessAt,
            error: msg.ok ? "" : String(msg.error || "采集失败").slice(0, 160)
          };
        }
        sendResponse({ ok: true });
      } else if (msg && msg.type === "post") {
        sendResponse(await post(msg.path, msg.payload, msg.token));
      } else {
        sendResponse({ ok: false, error: "unknown message" });
      }
    } catch (e) {
      sendResponse({ ok: false, error: String(e) });
    }
  })();
  return true; // 异步 sendResponse
});
