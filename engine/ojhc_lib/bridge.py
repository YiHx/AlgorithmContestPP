"""本地桥接：Chrome 扩展在用户已登录的页面里取数 → POST 到 Engine。

为什么需要它（DESIGN.md §4.2）：AtCoder / 洛谷 / 牛客没有可用的公开匿名接口，
由扩展在已登录的页面中取数，再转发给本机引擎。支持本人最终判题快照的平台
可以在显式开启设备输出后参与联动。

协议（只监听 127.0.0.1）：
    GET  /health                → {"ok": true, "version": "..."}
    GET  /session               → 当前会话 + token（扩展据此知道往哪 POST）
    POST /ingest/meta           → {"name"?, "duration_s"?, "problems"?}
    POST /ingest/standings      → {"rank"?, "solved"?, "n_official"?, "frozen"?, "rel_s"?}
    POST /ingest/submissions    → [{sid|id, verdict, problem, rel_s?, points?}, ...]
    POST /ingest/cf-page        → CF /my 页面快照，首帧建立会话基线
    POST 需要 Header: X-OJHC-Token: <token>

扩展在页面里 fetch 平台自己的接口（AtCoder standings/json、洛谷 scoreboard 等），
拿到数据后 POST 上来；引擎完全不接触用户 Cookie。
"""
from __future__ import annotations

import json
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .platforms.base import (
    Capabilities, ContestMeta, NotSupported, PlatformAdapter, Problem, Standings,
    Submission, normalize_verdict,
    PENDING_VERDICTS, VERDICT_UNKNOWN,
)

DEFAULT_BRIDGE_PORT = 8765
BRIDGE_TOKEN_HEADER = "X-OJHC-Token"
SUBMISSION_SNAPSHOT_PLATFORMS = frozenset({"atcoder", "nowcoder"})
PLATFORM_NAMES = {"atcoder": "AtCoder", "nowcoder": "牛客"}


class _BridgeHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = not hasattr(socket, "SO_EXCLUSIVEADDRUSE")

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            # Windows 的 SO_REUSEADDR 会让两个引擎同时监听同一端口。
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class BridgeHub:
    """会话数据的中转站：HTTP 线程写、asyncio 主循环读。"""

    def __init__(self, platform: str, contest_id: str, handle: str,
                 duration_s: float = 7200.0, port: int = DEFAULT_BRIDGE_PORT,
                 started_at: float | None = None) -> None:
        self.platform = platform
        self.contest_id = contest_id
        self.handle = handle
        self.duration_s = float(duration_s)
        self.started_at = started_at or time.time()
        self.token = secrets.token_urlsafe(12)
        self.name = f"{platform} contest {contest_id}"
        self.problems: list[Problem] = []
        self._lock = threading.Lock()
        self._inbox: list[Submission] = []
        self._seen: set[str] = set()
        self._page_baseline: int | None = None
        self._standings = Standings()
        self.has_standings = False
        self.handshakes = 0
        self.last_ingest = 0.0
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.port = port

    # -------------------------------------------------- 生命周期
    def start(self) -> str:
        handler = _make_handler(self)
        self._server = _BridgeHTTPServer(("127.0.0.1", self.port), handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    # -------------------------------------------------- 写入（HTTP 线程调用）
    def ingest_meta(self, payload: dict) -> None:
        with self._lock:
            if payload.get("name"):
                self.name = str(payload["name"])[:160]
            if payload.get("duration_s"):
                self.duration_s = float(payload["duration_s"])
            problems = payload.get("problems")
            if isinstance(problems, list):
                self.problems = [
                    Problem(index=str(p.get("index", p.get("name", "?"))),
                            rating=p.get("rating"))
                    for p in problems if isinstance(p, dict)]

    def ingest_standings(self, payload: dict) -> None:
        with self._lock:
            rel_s = float(payload.get("rel_s") or (time.time() - self.started_at))
            self._standings = Standings(
                rel_s=rel_s,
                rank=_opt_int(payload.get("rank")),
                solved=_opt_int(payload.get("solved")),
                n_official=_opt_int(payload.get("n_official")),
                frozen=bool(payload.get("frozen", False)))
            self.has_standings = True
            self.handshakes += 1
            self.last_ingest = time.time()

    def ingest_submissions(self, items: list[dict], *, cf_page: bool = False) -> int:
        received = 0
        platform_name = PLATFORM_NAMES.get(self.platform)
        page_snapshot = cf_page or self.platform in SUBMISSION_SNAPSHOT_PLATFORMS
        with self._lock:
            if page_snapshot and self._page_baseline is None:
                self._page_baseline = max((_num(item.get("sid") or item.get("id")) for item in items
                                           if isinstance(item, dict)), default=0)
                self.handshakes += 1
                self.last_ingest = time.time()
                if platform_name:
                    print(f"[bridge] {platform_name} 采集已建立基线（{len(items)} 条历史提交）", flush=True)
                return 0
            for item in items:
                if not isinstance(item, dict):
                    continue
                sid = str(item.get("sid") or item.get("id") or "")
                if not sid or sid in self._seen:
                    continue
                if page_snapshot and _num(sid) <= self._page_baseline:
                    continue
                if self.platform == "atcoder":
                    handle = str(item.get("handle") or "").strip().casefold()
                    timestamp = item.get("time")
                    if (handle != self.handle.strip().casefold()
                            or not isinstance(timestamp, (int, float))
                            or timestamp < self.started_at * 1000):
                        continue
                verdict = normalize_verdict(item.get("verdict") or item.get("status"))
                if (page_snapshot or self.platform == "codeforces") and (
                    verdict in PENDING_VERDICTS or verdict == VERDICT_UNKNOWN
                ):
                    continue
                rel_s = item.get("rel_s")
                if rel_s is None:
                    ts = item.get("time") or item.get("timestamp")
                    if isinstance(ts, (int, float)) and ts > 1e12:      # 毫秒
                        ts = ts / 1000.0
                    rel_s = (float(ts) - self.started_at) if isinstance(ts, (int, float)) \
                        else (time.time() - self.started_at)
                sub = Submission(
                    sid=sid, rel_s=max(0.0, float(rel_s)), verdict=verdict,
                    problem=str(item.get("problem") or item.get("task") or "?"),
                    points=float(item.get("points") or 0.0), raw=item)
                self._seen.add(sid)
                self._inbox.append(sub)
                received += 1
                if platform_name:
                    print(f"[bridge] {platform_name} 收到判题 #{sid} {verdict}", flush=True)
            if received:
                self.handshakes += 1
                self.last_ingest = time.time()
        return received

    # -------------------------------------------------- 读取（asyncio 主循环调用）
    def drain(self, after: str | None = None) -> list[Submission]:
        with self._lock:
            items = self._inbox
            self._inbox = []
        if after is not None:
            items = [s for s in items if _num(s.sid) > _num(after)]
        items.sort(key=lambda s: _num(s.sid))
        return items

    def snapshot(self) -> Standings:
        with self._lock:
            return self._standings

    def session_info(self) -> dict:
        return {
            "active": True,
            "platform": self.platform,
            "contest_id": self.contest_id,
            "handle": self.handle,
            "duration_s": self.duration_s,
            "started_at": self.started_at,
            "token": self.token,
            "url": self.url,
        }


class BridgeAdapter:
    """把 BridgeHub 包装成标准 PlatformAdapter（C 级数据源）。"""

    capabilities = Capabilities(
        realtime_verdict=True, live_standings=True, freeze_aware=False,
        requires_auth=True, source_level="C")

    def __init__(self, hub: BridgeHub) -> None:
        self.hub = hub
        self.platform = hub.platform
        self.has_submission_baseline = self.platform in SUBMISSION_SNAPSHOT_PLATFORMS
        self.poll_sleep_s = 0.4 if self.has_submission_baseline else 2.5

    async def load_meta(self) -> ContestMeta:
        return ContestMeta(
            platform=self.hub.platform,
            contest_id=self.hub.contest_id,
            name=self.hub.name,
            duration_s=self.hub.duration_s,
            problems=list(self.hub.problems),
            start_ts=self.hub.started_at,
            frozen_from_s=None,
            url="",
            extras={"source": "browser-extension"},
        )

    async def poll(self, after: str | None) -> list[Submission]:
        return self.hub.drain(None if self.has_submission_baseline else after)

    async def standings(self, elapsed_s: float) -> Standings:
        if not self.hub.has_standings:
            raise NotSupported("扩展还没有上报榜单（页面未打开或未登录）")
        return self.hub.snapshot()


def _opt_int(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _num(value: str):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------- HTTP 层
def _make_handler(hub: BridgeHub):
    class Handler(BaseHTTPRequestHandler):
        server_version = "OJHCBridge/0.1"

        def log_message(self, fmt, *args):  # 静音默认日志
            pass

        def _cors(self) -> None:
            origin = self.headers.get("Origin", "")
            if origin.startswith("chrome-extension://") or origin.startswith("moz-extension://"):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Headers",
                                 f"Content-Type, {BRIDGE_TOKEN_HEADER}")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

        def _json(self, code: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self._cors()
            self.end_headers()
            self.wfile.write(body)

        def _check_token(self) -> bool:
            return self.headers.get(BRIDGE_TOKEN_HEADER, "") == hub.token

        def _read_json(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 4 * 1024 * 1024:
                return None
            try:
                return json.loads(self.rfile.read(length).decode("utf-8", "replace"))
            except json.JSONDecodeError:
                return None

        def do_OPTIONS(self):  # noqa: N802
            self.send_response(204)
            self._cors()
            self.end_headers()

        def do_GET(self):  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path == "/health":
                self._json(200, {"ok": True, "version": "0.1",
                                 "session_active": True, "platform": hub.platform})
            elif path == "/session":
                self._json(200, hub.session_info())
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802
            if not self._check_token():
                self._json(401, {"error": "bad token"})
                return
            payload = self._read_json()
            if payload is None:
                self._json(400, {"error": "bad json"})
                return
            path = self.path.split("?", 1)[0]
            if path == "/ingest/meta":
                hub.ingest_meta(payload)
                self._json(200, {"ok": True})
            elif path == "/ingest/standings":
                hub.ingest_standings(payload)
                self._json(200, {"ok": True})
            elif path == "/ingest/cf-page" and hub.platform == "codeforces":
                if not isinstance(payload, list):
                    self._json(400, {"error": "expected a list"})
                    return
                count = hub.ingest_submissions(payload, cf_page=True)
                self._json(200, {"ok": True, "received": count})
            elif path == "/ingest/submissions":
                items = payload if isinstance(payload, list) else payload.get("submissions", [])
                count = hub.ingest_submissions(items)
                self._json(200, {"ok": True, "received": count})
            else:
                self._json(404, {"error": "not found"})

    return Handler
