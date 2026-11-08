// 算法竞赛++ 浏览器桥接 - popup
const statusEl = document.getElementById("status");
const detailEl = document.getElementById("detail");

function setStatus(text, ok) {
  statusEl.textContent = text;
  statusEl.className = ok ? "ok" : "bad";
}

async function refresh(force) {
  setStatus("检查 Engine…", false);
  let session = null;
  try {
    session = await chrome.runtime.sendMessage({ type: "get_session", force: !!force });
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
  detailEl.textContent = [
    `handle: ${session.handle}`,
    `时长: ${Math.round((session.duration_s || 0) / 60)} 分钟`,
    `Engine: ${session.base || ""}`,
    "",
    "现在打开对应平台的比赛页面并保持登录，扩展会自动上报。"
  ].join("\n");
}

document.getElementById("reconnect").addEventListener("click", () => refresh(true));
refresh(false);
setInterval(() => refresh(false), 5000);
