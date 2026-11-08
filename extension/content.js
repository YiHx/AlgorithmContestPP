// 算法竞赛++ 浏览器桥接 - content script
// 在已登录的 AtCoder / 洛谷 / 牛客 页面上下文里 fetch 平台数据，交给 background 转发给本机 Engine。
// 设计约束（research/platform-endpoints-v2.md）：
//   * 每平台 3s 节流，失败指数退避；数据缺失就显式降级，不猜
//   * 只读页面数据，不接管账号密码；POST 只发往 127.0.0.1 的 Engine

(() => {
  "use strict";

  const PLATFORM = detectPlatform();
  if (!PLATFORM) return;
  const CONTEST_ID = contestIdFromUrl();

  const POLL_MS = 3000;
  const FAST_POLL_MS = 1000;   // Codeforces：扩展盯页面，绕过官方 API 的 2s 限速
  const KNOWN_VERDICTS = [
    "AC", "WA", "TLE", "MLE", "RE", "CE", "WJ", "QLE", "OLE", "IE",
    "WRONG_ANSWER", "TIME_LIMIT_EXCEEDED", "RUNTIME_ERROR", "COMPILATION_ERROR",
    "答案正确", "答案错误", "运行超时", "内存超限", "运行错误", "编译错误"
  ];

  let backoff = PLATFORM === "codeforces" ? FAST_POLL_MS : POLL_MS;
  let lastError = "";

  function detectPlatform() {
    const host = location.hostname;
    if (host.endsWith("atcoder.jp")) return "atcoder";
    if (host.endsWith("luogu.com.cn")) return "luogu";
    if (host.endsWith("nowcoder.com")) return "nowcoder";
    if (host.endsWith("codeforces.com")) return "codeforces";
    return null;
  }

  function contestIdFromUrl() {
    const path = location.pathname;
    let m;
    if (PLATFORM === "atcoder" && (m = path.match(/\/contests\/([A-Za-z0-9_-]+)/))) return m[1];
    if (PLATFORM === "luogu" && (m = path.match(/\/contest\/(\d+)/))) return m[1];
    if (PLATFORM === "nowcoder" && (m = path.match(/\/acm\/contest\/(\d+)/))) return m[1];
    if (PLATFORM === "codeforces" && (m = path.match(/(?:contest|gym)\/(\d+)/))) return m[1];
    return null;
  }

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const send = (payload) => chrome.runtime.sendMessage(payload);

  async function post(path, payload) {
    const result = await send({ type: "post", path, payload });
    if (!result || result.ok === false) {
      lastError = (result && result.error) || "post failed";
      return false;
    }
    lastError = "";
    return true;
  }

  function hhmmssToSeconds(text) {
    const parts = text.split(":").map((x) => parseInt(x, 10));
    if (parts.length === 2) return parts[0] * 60 + parts[1];
    if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
    return null;
  }

  function pickVerdict(texts) {
    for (let i = texts.length - 1; i >= 0; i--) {
      const raw = (texts[i] || "").trim();
      if (!raw) continue;
      const upper = raw.toUpperCase();
      for (const token of KNOWN_VERDICTS) {
        if (upper === token.toUpperCase() || raw === token) return token;
      }
    }
    return null;
  }

  // ------------------------------------------------------------ AtCoder
  async function atcoderMeta() {
    const meta = { name: document.title.replace(/\s*-\s*AtCoder\s*$/, "").trim() };
    const text = document.body ? document.body.innerText : "";
    const duration = text.match(/(?:Duration|所要時間)[:：]\s*(\d+)\s*(?:min|分)/i);
    if (duration) meta.duration_s = parseInt(duration[1], 10) * 60;
    return meta;
  }

  async function atcoderStandings(handle) {
    const resp = await fetch(`/contests/${CONTEST_ID}/standings/json`, {
      credentials: "same-origin",
      headers: { "Accept": "application/json" }
    });
    if (!resp.ok || resp.redirected) return null;
    const data = await resp.json();
    const raw = data.StandingsData || [];
    const rows = Array.isArray(raw) ? raw : Object.values(raw);
    const mine = rows.find((row) => {
      const names = [row.UserScreenName, row.UserName].filter(Boolean).map((x) => String(x).toLowerCase());
      return names.includes(handle.toLowerCase());
    });
    if (!mine) return null;
    let solved = 0;
    let frozen = false;
    const taskResults = mine.TaskResults || {};
    const tasks = Array.isArray(taskResults) ? taskResults : Object.values(taskResults);
    for (const task of tasks) {
      const score = Number(task.Score || 0);
      if (score > 0) solved += 1;
      if (task.Frozen) frozen = true;
    }
    if (mine.TotalResult && mine.TotalResult.Frozen) frozen = true;
    return { rank: mine.Rank != null ? Number(mine.Rank) : null, solved, n_official: rows.length, frozen };
  }

  async function atcoderSubmissions(handle) {
    const resp = await fetch(`/contests/${CONTEST_ID}/submissions/me`, { credentials: "same-origin" });
    if (!resp.ok || resp.redirected) return [];
    const html = await resp.text();
    const doc = new DOMParser().parseFromString(html, "text/html");
    const out = [];
    for (const row of doc.querySelectorAll("table tbody tr")) {
      const link = row.querySelector('a[href*="/submissions/"]');
      if (!link) continue;
      const m = (link.getAttribute("href") || "").match(/\/submissions\/(\d+)/);
      if (!m) continue;
      const texts = [...row.querySelectorAll("td")].map((td) => td.textContent.replace(/\s+/g, " ").trim());
      const verdict = pickVerdict(texts) || "UNKNOWN";
      const timeText = texts.find((t) => /^\d{1,2}:\d{2}(:\d{2})?$/.test(t));
      const taskLink = row.querySelector('a[href*="/tasks/"]');
      const problem = taskLink ? (taskLink.getAttribute("href").split("/tasks/")[1] || "?") : "?";
      out.push({
        sid: m[1],
        rel_s: timeText ? hhmmssToSeconds(timeText) : null,
        verdict,
        problem
      });
    }
    return out;
  }

  async function collectAtCoder(session) {
    const payload = { meta: await atcoderMeta() };
    const standings = await atcoderStandings(session.handle);
    if (standings) payload.standings = standings;
    const submissions = await atcoderSubmissions(session.handle);
    if (submissions.length) payload.submissions = submissions;
    return payload;
  }

  // ------------------------------------------------------------ 洛谷
  async function collectLuogu(session) {
    const payload = { meta: { name: document.title.replace(/\s*-\s*洛谷\s*$/, "").trim() } };
    const handle = session.handle.toLowerCase();
    for (let page = 1; page <= 5; page++) {
      let resp;
      try {
        resp = await fetch(`/fe/api/contest/scoreboard/${CONTEST_ID}?page=${page}&_contentOnly=1`, {
          credentials: "same-origin",
          headers: { "x-luogu-type": "content-only" }
        });
      } catch (e) {
        lastError = "scoreboard fetch failed";
        return payload;
      }
      if (!resp.ok) { lastError = `scoreboard HTTP ${resp.status}`; return payload; }
      let data;
      try { data = await resp.json(); } catch (e) { lastError = "scoreboard not json"; return payload; }
      const board = data.scoreboard || (data.currentData && data.currentData.scoreboard) || [];
      if (!Array.isArray(board) || board.length === 0) return payload;
      const idx = board.findIndex((item) => {
        const user = item.user || {};
        return String(user.name || "").toLowerCase() === handle;
      });
      if (idx >= 0) {
        const item = board[idx];
        const details = item.details || {};
        let solved = 0;
        for (const key of Object.keys(details)) {
          const detail = details[key];
          const score = typeof detail === "number" ? detail
            : detail && typeof detail === "object" ? Number(detail.score || 0) : 0;
          if (score > 0) solved += 1;
        }
        const perPage = Number(data.perPage || data.pageSize || board.length || 50);
        payload.standings = {
          rank: (page - 1) * perPage + idx + 1,
          solved,
          n_official: Number(data.totalCount || data.count || 0) || null,
          frozen: false
        };
        return payload;
      }
      if (board.length < 2) return payload;
    }
    lastError = "我还没出现在成绩表里";
    return payload;
  }

  // ------------------------------------------------------------ 牛客
  async function collectNowcoder(session) {
    const payload = { meta: { name: document.title.replace(/\s*-\s*牛客网\s*$/, "").trim() } };
    try {
      const resp = await fetch(
        `/acm/contest/status-list?token=&id=${CONTEST_ID}&page=1&pageSize=50`
        + `&searchUserName=${encodeURIComponent(session.handle)}`,
        { credentials: "same-origin", headers: { "X-Requested-With": "XMLHttpRequest" } });
      if (!resp.ok) { lastError = `status-list HTTP ${resp.status}`; return payload; }
      const data = await resp.json();
      const items = (data.data && data.data.data) || [];
      const submissions = items
        .filter((it) => it && it.submissionId)
        .map((it) => ({
          sid: String(it.submissionId),
          verdict: it.statusMessage || "UNKNOWN",
          problem: it.index || "?",
          time: it.submitTime
        }));
      if (submissions.length) payload.submissions = submissions;
      // 牛客没有可用的实时榜单接口（research: rank/rank-list/scoreboard 全 404）
    } catch (e) {
      lastError = "status-list failed";
    }
    return payload;
  }

  // ------------------------------------------------------------ Codeforces（快速通道）
  function cfVerdict(texts) {
    for (const raw of texts) {
      const t = (raw || "").trim();
      if (!t) continue;
      if (/^accepted$/i.test(t)) return "AC";
      if (/wrong answer/i.test(t)) return "WRONG_ANSWER";
      if (/time limit/i.test(t)) return "TIME_LIMIT_EXCEEDED";
      if (/memory limit/i.test(t)) return "MEMORY_LIMIT_EXCEEDED";
      if (/runtime error/i.test(t)) return "RUNTIME_ERROR";
      if (/compilation error/i.test(t)) return "COMPILATION_ERROR";
      if (/presentation error/i.test(t)) return "PRESENTATION_ERROR";
      if (/idleness limit/i.test(t)) return "IDLENESS_LIMIT_EXCEEDED";
      if (/judgement failed|judgment failed/i.test(t)) return "JUDGEMENT_FAILED";
      if (/in queue|running on test|testing/i.test(t)) return "TESTING";
    }
    return "UNKNOWN";
  }

  async function collectCodeforces(session) {
    const payload = {};
    let resp;
    try {
      resp = await fetch(`/contest/${CONTEST_ID}/my`, { credentials: "same-origin" });
    } catch (e) {
      return payload;
    }
    if (!resp.ok || resp.redirected) return payload;
    const html = await resp.text();
    const doc = new DOMParser().parseFromString(html, "text/html");
    const items = [];
    for (const row of doc.querySelectorAll("table tbody tr")) {
      const subLink = row.querySelector('a[href*="/submission/"]');
      if (!subLink) continue;
      const m = (subLink.getAttribute("href") || "").match(/\/submission\/(\d+)/);
      if (!m) continue;
      const sid = parseInt(m[1], 10);
      if (!Number.isFinite(sid)) continue;
      const texts = [...row.querySelectorAll("td")].map(
        (td) => td.textContent.replace(/\s+/g, " ").trim());
      const problemLink = row.querySelector('a[href*="/problem/"]');
      const pm = problemLink
        ? (problemLink.getAttribute("href") || "").match(/\/problem\/([A-Za-z0-9]+)/) : null;
      const when = texts.find((t) => /^\d{1,2}:\d{2}:\d{2}$/.test(t));
      const rel = when
        ? when.split(":").reduce((acc, x) => acc * 60 + parseInt(x, 10), 0) : null;
      items.push({
        sid: String(sid),
        verdict: cfVerdict(texts),
        problem: pm ? pm[1] : "?",
        rel_s: rel,
      });
    }
    if (!items.length) return payload;

    // 用 chrome.storage 记录已上报的最大 submission id：
    // 首次只建立基线（旧提交不补电），之后只推新增的
    const key = `ojhc_cf_${CONTEST_ID}_maxsid`;
    const maxSid = Math.max(...items.map((i) => parseInt(i.sid, 10)));
    const stored = await new Promise((resolve) => {
      chrome.storage.local.get([key], (data) => resolve(data[key]));
    });
    if (stored === undefined || stored === null) {
      await new Promise((resolve) => chrome.storage.local.set({ [key]: maxSid }, resolve));
      return payload;
    }
    const fresh = items
      .filter((i) => parseInt(i.sid, 10) > Number(stored))
      .sort((a, b) => parseInt(a.sid, 10) - parseInt(b.sid, 10));
    if (fresh.length) {
      await new Promise((resolve) => chrome.storage.local.set({ [key]: maxSid }, resolve));
      payload.submissions = fresh;
    }
    return payload;
  }

  const COLLECTORS = { atcoder: collectAtCoder, luogu: collectLuogu,
                       nowcoder: collectNowcoder, codeforces: collectCodeforces };

  async function loop() {
    if (!CONTEST_ID) return;
    for (;;) {
      let session = null;
      try {
        session = await send({ type: "get_session", force: false });
      } catch (e) {
        lastError = "engine not reachable";
      }
      if (!session || session.platform !== PLATFORM || String(session.contest_id) !== String(CONTEST_ID)) {
        await sleep(POLL_MS);
        continue;
      }
      try {
        const payload = await COLLECTORS[PLATFORM](session);
        let ok = true;
        if (payload.meta) ok = (await post("/ingest/meta", payload.meta)) && ok;
        if (payload.standings) ok = (await post("/ingest/standings", payload.standings)) && ok;
        if (payload.submissions && payload.submissions.length) {
          ok = (await post("/ingest/submissions", payload.submissions)) && ok;
        }
        backoff = ok ? (PLATFORM === "codeforces" ? FAST_POLL_MS : POLL_MS)
                     : Math.min(backoff * 2, 30000);
      } catch (e) {
        lastError = String(e);
        backoff = Math.min(backoff * 2, 30000);
      }
      await sleep(backoff);
    }
  }

  loop();
})();
