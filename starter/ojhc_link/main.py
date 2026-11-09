"""算法竞赛++ 插件（M4）：DGHub 与 Engine 之间的设备执行端。

安全模型（DESIGN.md §7.6）：
  * Engine 每 2s 发一次 lease（ttl 5s）；本插件每次输出前校验 lease_id + 一次性 nonce
  * 租约过期 / Engine 断开 / DGHub 要求退出 / 急停 → 立刻把设备归零并停止接受输出
  * 只用 rollback 脉冲（自动回正），绝不用 permanent
  * 强度/时长在插件内再钳一次：≤ 配置上限、≤ 60%、0.3~3s

Engine 发现方式：读取 Engine 写的链路文件（默认 ~/.ojhc/device_link.json），
里面是 {host, port, token}。也可以用配置项 link_file 指定路径。

手动调试：
    python main.py --selftest           # 只校验参数/文件逻辑，不联网
    python main.py --engine-port 8768 --engine-token <token>   # 直连 Engine 调试
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

try:
    import websockets
    WS_IMPORT_ERROR: Exception | None = None
except ImportError as exc:  # pragma: no cover
    websockets = None  # type: ignore[assignment]
    WS_IMPORT_ERROR = exc

PLUGIN_ID = "ojhc_link"
PLUGIN_VERSION = "0.1.0"
HARD_CAP_PCT = 100           # 代码硬上限（配置改不动；App 上限本身是最终边界）

TUNING_DEFAULTS = {
    "mode": "simple",       # simple=单次失败也电 + 没 AC 递增；pressure=压力算法
    "no_ac_minutes": 20,    # 简单模式间隔
    "no_ac_escalation": 5,  # 每次没 AC 电击递增
    "base_intensity": 74,   # App 上限的百分比（s=2 档）
    "intensity_step": 4,    # 每升一档加多少
    "max_s": 4,             # 最高档位
    "cooldown_s": 10,       # 两次电击最小间隔
    "hourly_cap": 30,       # 每小时最多几次
    "allow_level_up": False,  # 等级上升是否也电
    "preset": "呼吸",        # 波形预设
}


def normalize_tuning(cfg: dict) -> dict:
    def num(key, lo, hi, default):
        try:
            value = int(round(float(cfg.get(key, default))))
        except (TypeError, ValueError):
            value = default
        return max(lo, min(hi, value))

    if "use_pressure_mode" in cfg:
        mode = "pressure" if cfg.get("use_pressure_mode") else "simple"
    else:
        mode = str(cfg.get("mode") or "simple")
        if mode not in ("simple", "pressure"):
            mode = "simple"
    t = {
        "mode": mode,
        "no_ac_minutes": num("no_ac_minutes", 5, 120, TUNING_DEFAULTS["no_ac_minutes"]),
        "no_ac_escalation": num("no_ac_escalation", 0, 20, TUNING_DEFAULTS["no_ac_escalation"]),
        "base_intensity": num("base_intensity", 40, 90, TUNING_DEFAULTS["base_intensity"]),
        "intensity_step": num("intensity_step", 0, 10, TUNING_DEFAULTS["intensity_step"]),
        "max_s": num("max_s", 2, 5, TUNING_DEFAULTS["max_s"]),
        "cooldown_s": num("cooldown_s", 10, 300, TUNING_DEFAULTS["cooldown_s"]),
        "hourly_cap": num("hourly_cap", 5, 60, TUNING_DEFAULTS["hourly_cap"]),
        "allow_level_up": bool(cfg.get("allow_level_up", TUNING_DEFAULTS["allow_level_up"])),
        "preset": str(cfg.get("preset") or TUNING_DEFAULTS["preset"])[:32],
        "hard_cap": min(100, max(0, int(cfg.get("max_intensity_pct", 90) or 90))),
    }
    return t


MIN_PULSE_S = 0.3
MAX_PULSE_S = 3.0
LEASE_MARGIN = 1.35         # 租约宽限系数（ttl*1.35 后必须归零）
DEFAULT_PORTS = (27020, 27021, 27022, 27023, 27800)
PLUGIN_ROOT = Path(os.environ.get("DGHUB_PLUGIN_ROOT") or Path(__file__).resolve().parent)


def load_manifest() -> dict:
    return json.loads((PLUGIN_ROOT / "manifest.json").read_text(encoding="utf-8"))


def schema_defaults(manifest: dict) -> dict:
    out: dict = {}
    for section in manifest.get("config_schema", []):
        for field in section.get("fields", []):
            if "key" in field and "default" in field:
                out[field["key"]] = field["default"]
    return out


def candidate_link_files(config: dict) -> list[Path]:
    files: list[Path] = []
    configured = str(config.get("link_file") or "").strip()
    if configured:
        files.append(Path(configured).expanduser())
    env = os.environ.get("OJHC_LINK_FILE")
    if env:
        files.append(Path(env).expanduser())
    files.append(Path.home() / ".ojhc" / "device_link.json")
    # 沙箱/受限环境回退：engine/.local/device_link.json（从插件目录向上找不到就不管）
    for parent in Path(__file__).resolve().parents:
        files.append(parent / ".local" / "device_link.json")
        if len(files) > 8:
            break
    seen: set[str] = set()
    unique: list[Path] = []
    for f in files:
        key = str(f).lower()
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique


class OjhPlugin:
    def __init__(self, manifest: dict) -> None:
        self.manifest = manifest
        self.cfg = schema_defaults(manifest)
        self.max_pct = int(min(int(self.cfg.get("max_intensity_pct", 90) or 90), HARD_CAP_PCT))
        self.tuning = normalize_tuning(self.cfg)
        self.estop = False
        self.lease_id = 0
        self.lease_at = 0.0
        self.lease_ttl = 5.0
        self.running = True
        # DGHub
        self.dg_ws = None
        self.device_connected = False
        self.device_type = ""
        # Engine
        self.engine_reader: asyncio.StreamReader | None = None
        self.engine_writer: asyncio.StreamWriter | None = None
        self.link_info: dict = {}
        self.outputs = 0
        self.rejected = 0
        self._engine_proc = None          # 由插件启动的引擎子进程
        self._launch_attempted = False
        self._launch_dirty = False        # 启动参数变更，需要重启引擎

    # -------------------------------------------------- DGHub
    async def connect_dghub(self) -> None:
        if websockets is None:
            raise RuntimeError(f"缺少 websockets：{WS_IMPORT_ERROR}")
        host = os.environ.get("DGHUB_HOST", "127.0.0.1")
        port = os.environ.get("DGHUB_PORT", "27020")
        token = os.environ.get("DGHUB_TOKEN")
        if not token:
            token = await self._fetch_token(host, port)
        if not token:
            raise RuntimeError("拿不到 DGHub token")
        if not port or not str(port).isdigit():
            port = "27020"
        url = f"ws://{host}:{port}/ws/plugin?token={token}"
        self.dg_ws = await websockets.connect(url, max_size=2**20)
        await self._dg_send({"op": "hello", "token": token, "manifest": self.manifest})
        ack = json.loads(await asyncio.wait_for(self.dg_ws.recv(), timeout=10))
        if ack.get("op") != "hello_ack" or not ack.get("accepted"):
            raise RuntimeError(f"DGHub 握手被拒：{ack.get('reason')}")
        self.task_ready = True
        asyncio.create_task(self._dghub_loop())
        await self._dg_log(f"{self.manifest.get('name') or PLUGIN_ID} v{PLUGIN_VERSION} 已连接 DGHub")

    async def _fetch_token(self, host: str, port: str) -> str | None:
        import urllib.request
        try:
            with urllib.request.urlopen(
                    f"http://{host}:{port}/api/plugins/_session_token", timeout=2) as r:
                body = r.read().decode("utf-8", "replace").strip()
        except Exception:
            return None
        try:
            data = json.loads(body)
            if isinstance(data, str):
                return data
            for key in ("token", "session_token", "data"):
                if isinstance(data.get(key), str):
                    return data[key]
        except json.JSONDecodeError:
            return body.strip('"') or None
        return None

    async def _dg_send(self, payload: dict) -> None:
        if self.dg_ws is not None:
            await self.dg_ws.send(json.dumps(payload, ensure_ascii=False))

    async def _dg_log(self, message: str, level: str = "info") -> None:
        print(f"[dghub] {level}: {message}")
        try:
            await self._dg_send({"op": "log", "level": level, "message": message})
        except Exception:
            pass

    async def _dghub_loop(self) -> None:
        assert self.dg_ws is not None
        try:
            async for raw in self.dg_ws:
                msg = json.loads(raw)
                op = msg.get("op")
                if op == "device_info":
                    self.device_connected = bool(msg.get("connected"))
                    self.device_type = msg.get("device_type") or ""
                    await self.push_status()
                elif op in ("config", "config_changed"):
                    changed = self._handle_config_msg(msg)
                    if changed is not None:
                        if self.engine_writer is not None:
                            await self._engine_send({"op": "tuning", "tuning": self.tuning})
                        await self._dg_log(
                            "配置已更新："
                            f"{json.dumps(self.tuning, ensure_ascii=False)}"
                            f" | 启动器 auto={self.cfg.get('auto_start_engine')} "
                            f"url={'有' if self.cfg.get('engine_url') else '无'} "
                            f"handle={'有' if self.cfg.get('engine_handle') else '无'}")
                        await self.maybe_launch_engine()
                elif op == "ping":
                    await self._dg_send({"op": "pong", "t": msg.get("t")})
                elif op == "stop":
                    await self._dg_log("DGHub 要求退出", "warning")
                    await self.zero_device("dghub stop")
                    self.running = False
                    return
        except Exception as exc:  # noqa: BLE001
            print(f"[dghub] 连接异常：{exc}")
        finally:
            self.running = False
            await self.zero_device("dghub disconnected")

    def _handle_config_msg(self, msg: dict) -> dict | None:
        """解析 DGHub 的 config / config_changed 消息（协议见 dev-guide §8.2）。

        config:        {"op": "config", "data": {key: value, ...}}
        config_changed:{"op": "config_changed", "key": "xxx", "value": ...}
        """
        op = msg.get("op")
        if op == "config":
            data = msg.get("data")
            if not isinstance(data, dict):
                data = msg.get("config")
            if not isinstance(data, dict):
                return None
            self._apply_config(data)
            return data
        if op == "config_changed":
            key = msg.get("key")
            if not key:
                return None
            self._apply_config({str(key): msg.get("value")})
            return {str(key): msg.get("value")}
        return None

    LAUNCH_KEYS = ("engine_url", "engine_handle", "engine_device",
                   "engine_virtual", "engine_python", "engine_dir")

    def _apply_config(self, data: dict) -> None:
        if not isinstance(data, dict):
            return
        for key, value in data.items():
            if key in self.LAUNCH_KEYS and self.cfg.get(key) != value:
                self._launch_dirty = True
            self.cfg[key] = value
        self.max_pct = int(min(int(self.cfg.get("max_intensity_pct", 90) or 90),
                              HARD_CAP_PCT))
        self.tuning = normalize_tuning(self.cfg)

    async def push_status(self) -> None:
        if not self.cfg.get("show_status", True):
            return
        if self.estop:
            text = "急停中（不会输出）"
        elif self.lease_active:
            text = f"就绪（租约 {self.lease_id}）"
        else:
            text = "等待 Engine 租约"
        try:
            await self._dg_send({"op": "status", "fields": {"display_status": text}})
        except Exception:
            pass

    # -------------------------------------------------- 输出去电
    @property
    def lease_active(self) -> bool:
        return (not self.estop and self.lease_id > 0
                and (time.monotonic() - self.lease_at) < self.lease_ttl * LEASE_MARGIN)

    async def zero_device(self, reason: str) -> None:
        """归零：set_strength 0（本插件层），A/B 都清。"""
        try:
            await self._dg_send({"op": "set_strength", "channel": "a", "pct": 0})
            await self._dg_send({"op": "set_strength", "channel": "b", "pct": 0})
            await self._dg_log(f"设备已归零（{reason}）", "warning")
        except Exception:
            pass
        self.lease_id = 0
        self.lease_at = 0.0

    async def handle_output(self, msg: dict) -> None:
        event_id = str(msg.get("event_id") or "")
        nonce = str(msg.get("nonce") or "")
        ok = False
        note = ""
        applied = 0
        try:
            if self.estop:
                note = "estop"
            elif not self.lease_active:
                note = "lease expired"
            elif int(msg.get("lease_id") or 0) != self.lease_id:
                note = "stale lease"
            elif not nonce:
                note = "missing nonce"
            elif not self.device_connected:
                note = "device not connected"
            else:
                intensity = int(msg.get("intensity_pct") or 0)
                duration = float(msg.get("duration_s") or 0)
                intensity = max(0, min(intensity, self.max_pct, HARD_CAP_PCT))
                duration = max(MIN_PULSE_S, min(duration, MAX_PULSE_S))
                if intensity <= 0:
                    note = "intensity 0"
                else:
                    preset = str(msg.get("preset") or "")
                    channel = str(msg.get("channel") or "both")
                    payload = {
                        "op": "trigger",
                        "action": "both" if preset else "strength",
                        "delta_pct": intensity,
                        "strength_mode": "rollback",
                        "duration_s": round(duration, 2),
                        "channel": channel,
                        "label": str(msg.get("label") or "OJHC"),
                    }
                    if preset:
                        payload["preset"] = preset
                    await self._dg_send(payload)
                    ok = True
                    applied = intensity
                    self.outputs += 1
                    await self.push_status()
        except Exception as exc:  # noqa: BLE001
            note = f"error: {exc}"
        if not ok:
            self.rejected += 1
            await self._dg_log(f"输出被拒（{note}）", "warning")
        await self._engine_send({"op": "ack", "event_id": event_id, "ok": ok,
                                 "applied_pct": applied, "note": note})

    # -------------------------------------------------- Engine 链路
    async def _engine_send(self, payload: dict) -> None:
        if self.engine_writer is None:
            return
        try:
            self.engine_writer.write((json.dumps(payload, ensure_ascii=False) + "\n").encode())
            await self.engine_writer.drain()
        except Exception:
            pass

    # -------------------------------------------------- Engine 启动器（插件页配置）
    def _engine_dir(self):
        configured = str(self.cfg.get("engine_dir") or "").strip()
        candidates = []
        if configured:
            candidates.append(Path(configured))
        env = os.environ.get("OJHC_ENGINE_DIR")
        if env:
            candidates.append(Path(env))
        home = Path.home()
        candidates += [
            home / "Documents/deepseek-harness/default-workspace/OJ_DGHUB/engine",
            home / "OJ_DGHUB/engine",
            Path.cwd() / "engine",
            Path.cwd(),
        ]
        for candidate in candidates:
            try:
                if (Path(candidate) / "ojhC.py").is_file():
                    return Path(candidate)
            except OSError:
                continue
        return None

    def _resolve_python(self) -> str:
        configured = str(self.cfg.get("engine_python") or "").strip()
        if configured and configured.lower() not in ("python", "python.exe"):
            return configured
        exe = sys.executable or ""
        if "python" in os.path.basename(exe).lower():
            return exe
        return configured or "python"

    @staticmethod
    def _normalize_contest_url(raw: str) -> str:
        """把倒计时/带参数的页面 URL 归一化成比赛 URL。"""
        url = (raw or "").strip()
        patterns = [
            r"(https?://codeforces\.com/(?:contest|gym)/\d+)",
            r"(https?://(?:www\.)?vjudge\.net/contest/\d+)",
            r"(https?://atcoder\.jp/contests/[A-Za-z0-9_-]+)",
            r"(https?://(?:www\.)?luogu\.com\.cn/contest/\d+)",
            r"(https?://ac\.nowcoder\.com/acm/contest/\d+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return url

    def _build_engine_args(self):
        url = self._normalize_contest_url(str(self.cfg.get("engine_url") or ""))
        handle = str(self.cfg.get("engine_handle") or "").strip()
        if not url or not handle:
            return None
        args = [self._resolve_python(), "ojhC.py", "run", "--url", url, "--handle", handle]
        if self.cfg.get("engine_device", True):
            args += ["--device", "--trust-app-limit"]
        if self.cfg.get("engine_virtual", False):
            args += ["--virtual"]
        return args

    async def maybe_launch_engine(self) -> None:
        """按插件页配置启动引擎（新终端窗口）。失败会写到 DGHub 运行日志。"""
        if not self.cfg.get("auto_start_engine", False):
            return
        force = False
        # 启动参数（URL/handle/设备/虚拟）变更 → 重启由插件拉起的引擎
        if (self._launch_dirty and self._engine_proc is not None
                and self._engine_proc.poll() is None):
            await self._dg_log("启动器：启动参数已变更，重启引擎终端…")
            try:
                self._engine_proc.terminate()
            except Exception:
                pass
            self._engine_proc = None
            self._launch_attempted = False
            self._launch_dirty = False
            self.engine_reader = None
            self.engine_writer = None
            force = True
        if self.engine_writer is not None:      # 引擎已经连上
            return
        if self._engine_proc is not None:
            if self._engine_proc.poll() is None:
                return
            if not getattr(self, "_logged_engine_exit", False):
                self._logged_engine_exit = True
                await self._dg_log(
                    "启动器：引擎已退出（窗口里的错误可查看）；改配置或重载插件可重试",
                    "warning")
            return
        if self._launch_attempted:
            if not getattr(self, "_logged_attempted", False):
                self._logged_attempted = True
                await self._dg_log("启动器：本次已尝试启动引擎，等待它连接…")
            return
        now = time.monotonic()
        if not force and now - getattr(self, "_last_launch_try", 0.0) < 10.0:
            return
        self._last_launch_try = now
        args = self._build_engine_args()
        engine_dir = self._engine_dir()
        if not args or engine_dir is None:
            await self._dg_log("启动器：URL/用户名未填，或找不到引擎目录（ojhC.py 所在目录）", "warning")
            return
        flags = 0
        spawn_args = args
        if os.name == "nt":
            flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
            log_dir = engine_dir / ".local"
            try:
                log_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
            log_path = log_dir / "launcher_last.log"
            bat_path = log_dir / "launch_engine.bat"
            # 用 .bat 启动，避免 cmd 引号转义问题；输出直接显示在窗口里
            bat = (
                "@echo off\r\n"
                f'cd /d "{engine_dir}"\r\n'
                f'"{args[0]}" ' + " ".join(args[1:]) + "\r\n"
                "echo.\r\n"
                "echo [engine exited] press any key to close\r\n"
                "pause >nul\r\n"
            )
            try:
                bat_path.write_text(bat, encoding="ascii", errors="replace")
            except OSError:
                pass
            spawn_args = ["cmd.exe", "/c", str(bat_path)]
        try:
            self._engine_proc = subprocess.Popen(
                spawn_args, cwd=str(engine_dir), creationflags=flags)
            self._launch_attempted = True
            self._launch_dirty = False
            await self._dg_log("启动器：已打开引擎终端 → " + " ".join(args[2:])
                               + f"（日志：engine/.local/launcher_last.log）")
        except Exception as exc:  # noqa: BLE001
            await self._dg_log(f"启动器：启动引擎失败：{exc}", "warning")

    async def find_and_connect_engine(self, timeout_s: float = 120.0,
                                      engine_port: int | None = None,
                                      engine_token: str | None = None) -> bool:
        if engine_port and engine_token:
            return await self._connect_engine("127.0.0.1", engine_port, engine_token)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline and self.running:
            for path in candidate_link_files(self.cfg):
                try:
                    info = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                host = info.get("host") or "127.0.0.1"
                port = int(info.get("port") or 0)
                token = str(info.get("token") or "")
                if port and token:
                    if await self._connect_engine(host, port, token):
                        return True
            await asyncio.sleep(2.0)
        return False

    async def _connect_engine(self, host: str, port: int, token: str) -> bool:
        try:
            reader, writer = await asyncio.open_connection(host, port)
        except Exception:
            return False
        self.engine_reader, self.engine_writer = reader, writer
        hello = {
            "op": "hello", "token": token, "plugin": PLUGIN_ID,
            "version": PLUGIN_VERSION,
            "tuning": self.tuning,
            "device": {
                "connected": self.device_connected, "type": self.device_type,
                "max_a": 0, "max_b": 0,
            },
        }
        await self._engine_send(hello)
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=5)
        except asyncio.TimeoutError:
            return False
        if not line:
            return False
        ack = json.loads(line)
        if ack.get("op") != "hello_ack" or not ack.get("accepted"):
            await self._dg_log(f"Engine 拒绝连接：{ack.get('reason')}", "warning")
            return False
        self.link_info = {"host": host, "port": port}
        # 新会话 = 新的信任建立：清掉上一场结束时的 estop 标记
        self.estop = False
        self.lease_id = 0
        await self._dg_log(f"已连接 Engine（{host}:{port}）")
        return True

    async def engine_loop(self) -> None:
        assert self.engine_reader is not None
        last_watchdog = 0.0
        while self.running:
            try:
                line = await asyncio.wait_for(self.engine_reader.readline(), timeout=0.5)
            except asyncio.TimeoutError:
                line = None
            if line == b"":
                # 不退出：归零后回到等待，下一场 Engine 启动可直接复用
                await self._dg_log("Engine 已断开，设备归零，回到等待", "warning")
                await self.zero_device("engine disconnected")
                return
            if line:
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                op = msg.get("op")
                if op == "lease":
                    self.lease_id = int(msg.get("lease_id") or 0)
                    self.lease_ttl = float(msg.get("ttl_s") or 5.0)
                    self.lease_at = time.monotonic()
                    if self.estop is False:
                        await self.push_status()
                elif op == "output":
                    await self.handle_output(msg)
                elif op == "estop":
                    self.estop = True
                    self.lease_id = 0
                    await self.zero_device(f"estop: {msg.get('reason')}")
                    await self.push_status()
                elif op == "ping":
                    await self._engine_send({"op": "pong"})
            now = time.monotonic()
            if now - last_watchdog > 0.5:
                last_watchdog = now
                if (self.lease_id > 0 and not self.estop
                        and now - self.lease_at > self.lease_ttl * LEASE_MARGIN):
                    await self._dg_log("租约过期：设备归零并停止输出", "warning")
                    self.lease_id = 0
                    await self.zero_device("lease expired")
                    await self.push_status()

    # -------------------------------------------------- 入口
    async def run(self, engine_port: int | None, engine_token: str | None) -> int:
        await self.connect_dghub()
        await self.maybe_launch_engine()
        if not await self.wait_device(15.0):
            await self._dg_log("设备未连接：仍会常驻等待 Engine，但不会输出", "warning")
        # 常驻模式：先在 DGHub 启用插件、后启动 Engine 也能连上；Engine 断开后回到等待
        try:
            while self.running:
                await self.maybe_launch_engine()
                found = await self.find_and_connect_engine(
                    engine_port=engine_port, engine_token=engine_token, timeout_s=30.0)
                if not found:
                    await self._dg_log("等待 Engine…（先运行 ojhC run --device）", "info")
                    await self.push_status()
                    await asyncio.sleep(3.0)
                    continue
                await self.push_status()
                await self.engine_loop()
                if not self.running:
                    break
                # Engine 断开：归零后回到等待（不退出进程，方便下一场直接复用）
                await self._dg_log("Engine 已断开，回到等待状态", "warning")
                self.engine_reader = None
                self.engine_writer = None
                await asyncio.sleep(2.0)
        finally:
            await self.zero_device("plugin exit")
            if self._engine_proc is not None and self._engine_proc.poll() is None:
                try:
                    self._engine_proc.terminate()
                    print("[launcher] 已停止由插件启动的 Engine")
                except Exception:
                    pass
            if self.dg_ws is not None:
                try:
                    await self.dg_ws.close()
                except Exception:
                    pass
            if self.engine_writer is not None:
                try:
                    self.engine_writer.close()
                except Exception:
                    pass
        return 0

    async def wait_device(self, timeout_s: float) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.device_connected:
                return True
            await asyncio.sleep(0.3)
        return self.device_connected


def selftest() -> int:
    manifest = load_manifest()
    problems: list[str] = []
    if manifest.get("id") != PLUGIN_ID:
        problems.append(f"manifest.id 应为 {PLUGIN_ID}")
    if manifest.get("sdk") != "1":
        problems.append("manifest.sdk 必须是 '1'")
    import re
    if not re.fullmatch(r"[a-z][a-z0-9_-]{1,31}", str(manifest.get("id"))):
        problems.append("manifest.id 不合法")
    p = OjhPlugin(manifest)
    if p.max_pct > HARD_CAP_PCT:
        problems.append("max_pct 超过硬上限")
    # 配置消息协议（dev-guide §8.2）
    p._handle_config_msg({"op": "config", "data": {
        "auto_start_engine": True, "engine_url": "https://codeforces.com/contest/1",
        "engine_handle": "tester", "max_intensity_pct": 95}})
    if not p.cfg.get("auto_start_engine") or p.cfg.get("max_intensity_pct") != 95:
        problems.append(f"config 消息解析失败：{p.cfg.get('auto_start_engine')}")
    p._handle_config_msg({"op": "config_changed", "key": "engine_device", "value": False})
    if p.cfg.get("engine_device") is not False:
        problems.append("config_changed 消息解析失败")
    if p._build_engine_args() is None:
        problems.append("启动器参数构造失败")
    cands = candidate_link_files(p.cfg)
    print(f"  plugin root = {PLUGIN_ROOT}")
    print(f"  max_pct = {p.max_pct}（硬上限 {HARD_CAP_PCT}）")
    print(f"  链路文件候选: {[str(c) for c in cands[:3]]}")
    if problems:
        print("[FAIL] " + "; ".join(problems))
        return 1
    print("[OK] manifest/参数自检通过")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="算法竞赛++ 插件（M4 设备执行端）")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--engine-port", type=int, default=None)
    parser.add_argument("--engine-token", default=None)
    args = parser.parse_args(argv)
    if args.selftest:
        return selftest()
    try:
        return asyncio.run(OjhPlugin(load_manifest()).run(args.engine_port, args.engine_token))
    except KeyboardInterrupt:
        print("已中断")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"[fatal] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
