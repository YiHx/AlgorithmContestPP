// 算法竞赛++ 浏览器桥接 - popup
const statusEl = document.getElementById("status");
const detailEl = document.getElementById("detail");
const runtime = (globalThis.browser ?? globalThis.chrome).runtime;

function setStatus(text, ok) {
  statusEl.textContent = text;
  statusEl.className = ok ? "ok" : "bad";
}

async function refresh(force) {
  setStatus("检查 Engine…", false);
  let session = null;
  try {
    session = await runtime.sendMessage({ type: "get_session", force: !!force, includeCollector: true });
  } catch (e) {
    setStatus("无法联系扩展后台", false);
    return;
  }
  if (!session) {
    setStatus("没找到 Engine（127.0.0.1:8765~8774）", false);
    detailEl.textContent = "先启动引擎：\npython ojhC.py run --url <比赛URL> --handle <用户名>";
    return;
  }
  setStatus(`已连接 Engine · ${session.platform} / ${session.contest_id}`, true);
  const lastSuccess = session.collector?.lastSuccessAt;
  detailEl.textContent = [
    `handle: ${session.handle}`,
    `时长: ${Math.round((session.duration_s || 0) / 60)} 分钟`,
    `Engine: ${session.url || ""}`,
    lastSuccess ? `上次成功采集: ${new Date(lastSuccess).toLocaleTimeString()}`
      : "等待对应比赛页首次采集，请保持页面打开并登录。",
    session.collector?.error ? `最近一次采集失败: ${session.collector.error}` : ""
  ].filter(Boolean).join("\n");
}

document.getElementById("reconnect").addEventListener("click", () => refresh(true));
refresh(false);
setInterval(() => refresh(false), 5000);
