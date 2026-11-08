"""极薄的 DGHub 协议客户端（只实现 Engine 现在需要的部分）。

设计约束（对应 DESIGN.md §9）：
  * 只用 trigger + rollback，绝不用 permanent —— 脉冲结束自动回正
  * 强度/时长在这里再钳一次（HARD_CAP_PCT / MAX_PULSE_S）
  * 协议没有租约机制 → 租约由 Engine 层负责（后续 M1 正式桥接时加）
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import urllib.request
from dataclasses import dataclass

def _import_websockets():
    """只使用 pip 安装的真实 websockets（DGHub 是 PyInstaller 打包，借不到它的依赖）。"""
    try:
        import websockets as _ws
    except ImportError:
        return None
    return _ws if hasattr(_ws, "connect") else None


websockets = _import_websockets()

from .profile import HARD_CAP_PCT, MAX_PULSE_S, MIN_PULSE_S
from .util import clamp

DEFAULT_PORTS = (8000, 27020, 27021, 27022, 27023, 27800)

# Engine 自己作为一个"插件"接进来（和 ojhC_probe 用不同 id，避免冲突）
ENGINE_MANIFEST = {
    "id": "ojhc_engine",
    "name": "OJHC Engine",
    "version": "0.1.0",
    "sdk": "1",
    "author": "xuanh08",
    "description": "OJ Hardcore Engine 的校准/诊断连接（无界面）",
}


@dataclass
class DeviceInfo:
    connected: bool = False
    device_type: str = ""
    max_strength_a: int = 0
    max_strength_b: int = 0


def fetch_token(host: str, port: int, timeout: float = 2.0) -> str | None:
    url = f"http://{host}:{port}/api/plugins/_session_token"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace").strip()
    except Exception:
        return None
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return body.strip('"') or None
    if isinstance(data, str):
        return data or None
    for key in ("token", "session_token", "data"):
        if isinstance(data.get(key), str) and data[key]:
            return data[key]
    return None


def discover(host: str, ports: list[int] | None = None) -> tuple[int, str] | None:
    """扫一遍候选端口，返回第一个能拿到 token 的 (port, token)。"""
    for port in (ports or list(DEFAULT_PORTS)):
        token = fetch_token(host, port, timeout=0.6)
        if token:
            return port, token
    return None


class DGHubClient:
    def __init__(self, host: str, port: int, token: str) -> None:
        self.host = host
        self.port = port
        self.token = token
        self.ws = None
        self.device = DeviceInfo()
        self.sdk_version = ""
        self.config: dict = {}

    # -------------------------------------------------- 连接/握手
    async def connect(self, timeout: float = 5.0) -> bool:
        if websockets is None:
            raise RuntimeError("缺少 websockets：pip install websockets")
        url = f"ws://{self.host}:{self.port}/ws/plugin?token={self.token}"
        self.ws = await websockets.connect(url, max_size=2**20, open_timeout=timeout)
        await self.send({"op": "hello", "token": self.token, "manifest": ENGINE_MANIFEST})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                msg = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=0.5))
            except asyncio.TimeoutError:
                continue
            if msg.get("op") == "hello_ack":
                if not msg.get("accepted"):
                    raise RuntimeError(f"握手被拒：{msg.get('reason')}")
                self.sdk_version = msg.get("sdk_version", "")
                return True
        raise TimeoutError("没有收到 hello_ack")

    async def close(self) -> None:
        if self.ws is not None:
            try:
                await self.ws.close()
            finally:
                self.ws = None

    # -------------------------------------------------- 收发
    async def send(self, payload: dict) -> None:
        if self.ws is None:
            raise RuntimeError("未连接")
        await self.ws.send(json.dumps(payload, ensure_ascii=False))

    async def recv(self, timeout: float = 0.5) -> dict | None:
        if self.ws is None:
            return None
        try:
            raw = await asyncio.wait_for(self.ws.recv(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
        msg = json.loads(raw)
        self._absorb(msg)
        return msg

    def _absorb(self, msg: dict) -> None:
        op = msg.get("op")
        if op == "device_info":
            self.device = DeviceInfo(
                connected=bool(msg.get("connected")),
                device_type=msg.get("device_type") or "",
                max_strength_a=int(msg.get("max_strength_a") or 0),
                max_strength_b=int(msg.get("max_strength_b") or 0),
            )
        elif op == "config":
            self.config.update(msg.get("data") or {})
        elif op == "config_changed":
            key = msg.get("key")
            if key:
                self.config[key] = msg.get("value")

    async def pump(self, timeout: float = 0.5) -> None:
        """处理一条消息（没有就返回），主要用来吸收 device_info/config。"""
        await self.recv(timeout)

    async def wait_device(self, timeout: float = 8.0) -> bool:
        """等设备连上；期间吸收 ping/config 等消息。"""
        if self.device.connected:
            return True
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            await self.recv(0.3)
            if self.device.connected:
                return True
        return False

    # -------------------------------------------------- 输出
    async def event(self, label: str, name: str, strength_pct: int | None = None,
                    duration: float = 1.0, event_id: str | None = None) -> None:
        payload = {"op": "event", "label": label, "name": name, "duration": duration}
        if strength_pct is not None:
            payload["strength_pct"] = strength_pct
        if event_id:
            payload["event_id"] = event_id
        await self.send(payload)

    async def trigger(self, delta_pct: int, duration_s: float,
                      preset: str = "", channel: str = "both",
                      label: str = "OJHC", dry_run: bool = False) -> dict:
        """发一次 rollback 脉冲；返回实际使用的参数（便于日志/测试）。"""
        intensity = int(clamp(int(delta_pct), 0, HARD_CAP_PCT))
        duration = clamp(float(duration_s), MIN_PULSE_S, MAX_PULSE_S)
        if preset:
            action = "both"
        else:
            action = "strength"
        payload = {
            "op": "trigger",
            "action": action,
            "delta_pct": intensity,
            "strength_mode": "rollback",
            "duration_s": round(duration, 2),
            "channel": channel,
            "label": label,
        }
        if preset:
            payload["preset"] = preset
        if not dry_run:
            await self.send(payload)
        else:
            print(f"        [dry-run] {json.dumps(payload, ensure_ascii=False)}")
        return payload

    async def log(self, message: str, level: str = "info") -> None:
        await self.send({"op": "log", "level": level, "message": message})


def resolve_connection(host: str, port: int | None, token: str | None) -> tuple[int, str] | None:
    """按优先级拿到 (port, token)：环境变量 > 显式参数 > 自动发现。"""
    env_token = os.environ.get("DGHUB_TOKEN")
    env_port = os.environ.get("DGHUB_PORT")
    if token is None:
        token = env_token
    if port is None and env_port and env_port.isdigit():
        port = int(env_port)
    if token and port:
        return port, token
    found = discover(host, [port] if port else None)
    return found

def fetch_device_info(host: str = "127.0.0.1", port: int | None = None) -> dict:
    """通过 DGHub HTTP API 读取设备状态（强度上限等），失败返回空 dict。"""
    import json as _json
    import urllib.request
    try:
        found = discover(host, [port] if port else None)
        if not found:
            return {}
        api_port, _token = found
        with urllib.request.urlopen(f"http://{host}:{api_port}/api/status", timeout=3) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
