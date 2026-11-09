// 算法竞赛++ 浏览器桥接 - content script
// 在已登录的 AtCoder / 洛谷 / 牛客 页面上下文里 fetch 平台数据，交给 background 转发给本机 Engine。
// 设计约束（research/platform-endpoints-v2.md）：
//   * CF / 牛客在有新提交待判时加速，其余平台保持 3s；失败指数退避
//   * 只读页面数据，不接管账号密码；POST 只发往 127.0.0.1 的 Engine

(() => {
  "use strict";

  const PLATFORM = detectPlatform();
  function pollBase() {
    if (FAST_PLATFORM) {
      return Date.now() < fastUntil ? BURST_POLL_MS : FAST_POLL_MS;
    }
    return POLL_MS;
  }
  if (!PLATFORM) return;
  const CONTEST_ID = contestIdFromUrl();
  const FAST_PLATFORM = PLATFORM === "codeforces" || PLATFORM === "nowcoder";

  const POLL_MS = 3000;
  const FAST_POLL_MS = 1000;
  const BURST_POLL_MS = 300;
  let fastUntil = /\/submit\/?$/.test(location.pathname) ? Date.now() + 30000
    : /\/my\/?$/.test(location.pathname) ? Date.now() + 20000 : 0;
  let pendingIds = new Set();
  let lastToken = null;
  const lastPosted = new Map();
  const KNOWN_VERDICTS = [
    "AC", "WA", "TLE", "MLE", "RE", "CE", "WJ", "QLE", "OLE", "IE",
    "WRONG_ANSWER", "TIME_LIMIT_EXCEEDED", "RUNTIME_ERROR", "COMPILATION_ERROR",
    "答案正确", "答案错误", "运行超时", "内存超限", "运行错误", "编译错误"
  ];

  let backoff = pollBase();
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
  const runtime = (globalThis.browser ?? globalThis.chrome).runtime;
  const send = (payload) => runtime.sendMessage(payload);

  async function post(path, payload, token) {
    const result = await send({ type: "post", path, payload, token });
    if (!result || result.ok === false) {
      lastError = (result && result.error) || "post failed";
      return false;
    }
    return true;
  }

  async function postChanged(path, payload, token) {
    const snapshot = JSON.stringify(payload);
    if (lastPosted.get(path) === snapshot) return true;
    if (!await post(path, payload, token)) return false;
    lastPosted.set(path, snapshot);
    return true;
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
    const path = `/contests/${CONTEST_ID}/submissions`;
    const resp = await fetch(`${path}/me`, { credentials: "same-origin", cache: "no-store" });
    if (!resp.ok) return null;
    const resultUrl = new URL(resp.url);
    if (resultUrl.origin !== location.origin || ![path, `${path}/me`].includes(resultUrl.pathname)) return null;
    const html = await resp.text();
    const doc = new DOMParser().parseFromString(html, "text/html");
    const out = [];
    for (const row of doc.querySelectorAll("table tbody tr")) {
      const user = row.querySelector('a[href^="/users/"]')?.textContent.trim();
      if (user?.toLowerCase() !== handle.trim().toLowerCase()) continue;
      const link = row.querySelector(`a[href^="${path}/"]`);
      if (!link) continue;
      const m = (link.getAttribute("href") || "").match(/\/submissions\/(\d+)/);
      if (!m) continue;
      const header = [...(row.closest("table")?.querySelectorAll("thead th") || [])];
      const statusColumn = header.findIndex((th) => /^(status|result|結果|判定|ステータス)$/i.test(th.textContent.trim()));
      const statusCell = row.querySelector(".judge-status") || row.cells[statusColumn];
      const status = statusCell?.textContent.trim().toUpperCase();
      const verdict = KNOWN_VERDICTS.includes(status) ? status : "UNKNOWN";
      const timeTag = row.querySelector("time");
      const submittedAt = Date.parse(timeTag?.getAttribute("datetime") || timeTag?.textContent || "");
      if (!Number.isFinite(submittedAt)) continue;
      const taskLink = row.querySelector('a[href*="/tasks/"]');
      const problem = taskLink ? (taskLink.getAttribute("href").split("/tasks/")[1] || "?") : "?";
      out.push({
        sid: m[1],
        handle: user,
        time: submittedAt,
        verdict,
        problem
      });
    }
    return out;
  }

  async function collectAtCoder(session) {
    const payload = { meta: await atcoderMeta() };
    const standings = await atcoderStandings(session.handle).catch(() => null);
    if (standings) payload.standings = standings;
    const submissions = await atcoderSubmissions(session.handle);
    if (submissions !== null) payload.submissions = submissions;
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
    const resp = await fetch(
      `/acm/contest/status-list?token=&id=${CONTEST_ID}&page=1&pageSize=50`
      + `&searchUserName=${encodeURIComponent(session.handle)}`,
      { credentials: "same-origin", cache: "no-store", signal: AbortSignal.timeout(8000),
        headers: { "X-Requested-With": "XMLHttpRequest" } });
    if (!resp.ok) throw new Error(`牛客提交列表 HTTP ${resp.status}`);
    const data = await resp.json();
    const items = data.data?.data;
    if (data.code !== 0 || !Array.isArray(items)) {
      throw new Error("牛客没有返回提交列表，请检查登录状态");
    }
    const handle = session.handle.trim().toLowerCase();
    payload.submissions = items
      .filter((it) => it?.submissionId && String(it.userName).trim().toLowerCase() === handle)
      .map((it) => ({
        sid: String(it.submissionId),
        verdict: it.statusMessage || "UNKNOWN",
        problem: it.index || "?",
        time: it.submitTime
      }));
    return payload;
  }

  // ------------------------------------------------------------ Codeforces（快速通道）
  function cfVerdict(texts) {
    for (const raw of texts) {
      const t = (raw || "").trim();
      if (!t) continue;
      if (/^(accepted|pretests passed)$/i.test(t)) return "AC";
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

  async function collectCodeforces() {
    const contestPath = location.pathname.startsWith("/gym/") ? "gym" : "contest";
    const resp = await fetch(`/${contestPath}/${CONTEST_ID}/my`, {
      credentials: "same-origin", cache: "no-store", signal: AbortSignal.timeout(8000)
    });
    if (!resp.ok || resp.redirected) throw new Error(`CF /my: HTTP ${resp.status}`);
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
    if (!items.length && !doc.querySelector("table.status-frame-datatable")) {
      throw new Error("CF /my: submission table not found");
    }
    return { cfPage: items };
  }

  const COLLECTORS = { atcoder: collectAtCoder, luogu: collectLuogu,
                       nowcoder: collectNowcoder, codeforces: collectCodeforces };

  async function loop() {
    if (!CONTEST_ID) return;
    for (;;) {
      let session = null;
      try {
        session = await send({
          type: "get_session", collector: { platform: PLATFORM, contestId: CONTEST_ID }
        });
      } catch (e) {
        lastError = "engine not reachable";
      }
      if (!session || session.platform !== PLATFORM || String(session.contest_id) !== String(CONTEST_ID)) {
        await sleep(POLL_MS);
        continue;
      }
      if (session.token !== lastToken) {
        lastToken = session.token;
        lastPosted.clear();
        pendingIds.clear();
      }
      lastError = "";
      try {
        const payload = await COLLECTORS[PLATFORM](session);
        const submissions = payload.cfPage || payload.submissions;
        if (FAST_PLATFORM && submissions) {
          const pending = submissions.filter(item =>
            /^(TESTING|PENDING)$|等待|判题中|评测中|排队|运行中|编译中/i.test(item.verdict));
          if (pending.some(item => !pendingIds.has(item.sid))) fastUntil = Date.now() + 30000;
          pendingIds = new Set(pending.map(item => item.sid));
        }
        if (payload.cfPage) {
          await postChanged("/ingest/cf-page", payload.cfPage, session.token);
        }
        if (payload.meta) await postChanged("/ingest/meta", payload.meta, session.token);
        if (payload.submissions) {
          await postChanged("/ingest/submissions", payload.submissions, session.token);
        }
        if (payload.standings) await postChanged("/ingest/standings", payload.standings, session.token);
        if (!submissions && !payload.standings && !lastError) lastError = "比赛页没有返回可用数据";
      } catch (e) {
        lastError = String(e);
      }
      backoff = lastError ? Math.min(Math.max(POLL_MS, backoff * 2), 30000) : pollBase();
      try {
        await send({ type: "collector_status", token: session.token, ok: !lastError, error: lastError });
      } catch (e) {
        // 扩展重载后由新脚本恢复采集。
      }
      await sleep(backoff);
    }
  }

  loop();
})();
