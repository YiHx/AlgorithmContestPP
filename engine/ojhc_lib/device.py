"""设备输出链路（M4）：Engine ↔ 插件，带租约（lease）+ 死手开关 + 急停。

设计依据（DESIGN.md §7.5/§7.6）：
  * 设备输出必须过 11 条闸门；本模块只负责"闸门放行后如何安全地落到设备"
  * 租约：Engine 每 2s 续期（ttl 5s），插件每次输出前校验 lease_id + 一次性 nonce
  * 死手开关：租约过期 → 插件必须把设备归零并拒绝后续输出
  * 急停：estop 消息立即归零 + 标记失效；连接断开也视为失效

为什么用裸 TCP + JSONL 而不是 WebSocket：
  * Engine 侧不需要额外依赖（插件在 DGHub 环境里本来就有 websockets）
  * 本地回环、单客户端、按行 JSON，足够简单可审计

协议（每行一个 JSON）：
  plugin → engine:  hello{token, plugin, version, device{...}}
                    ack{event_id, ok, applied_pct, note}
                    pong{}
  engine → plugin:  hello_ack{session}
                    lease{lease_id, ttl_s}
                    output{event_id, lease_id, nonce, intensity_pct, duration_s,
                           preset, channel, label}
                    estop{reason}
                    ping{}
"""
from __future__ import annotations

import asyncio
import json
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

PROTOCOL_VERSION = 1
DEFAULT_DEVICE_PORT = 8768
LINK_FILE_NAME = "device_link.json"


@dataclass
class DeviceStatus:
    connected: bool = False
    plugin: str = ""
    plugin_version: str = ""
    device_type: str = ""
    max_strength_a: int = 0
    max_strength_b: int = 0
    last_error: str = ""
    outputs: int = 0
    rejected: int = 0
    estop: bool = False


class DeviceLink:
    """Engine 侧设备链路：TCP 服务器 + 租约管理 + 输出请求。"""

    def __init__(self, host: str = "127.0.0.1", port: int = DEFAULT_DEVICE_PORT,
                 lease_ttl_s: float = 5.0, renew_s: float = 2.0) -> None:
        self.host = host
        self.port = port
        self.lease_ttl_s = lease_ttl_s
        self.renew_s = renew_s
        self.token = secrets.token_urlsafe(12)
        self.status = DeviceStatus()
        self.lease_id = 0
        self.lease_sent_at = 0.0
        self._server: asyncio.AbstractServer | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._renew_task: asyncio.Task | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._used_nonces: set[str] = set()

    # -------------------------------------------------- 生命周期
    async def start(self) -> str:
        self._server = await asyncio.start_server(self._handle_client, self.host, self.port)
        sockets = self._server.sockets or []
        if sockets:
            self.port = sockets[0].getsockname()[1]
        self._renew_task = asyncio.create_task(self._renew_loop())
        return f"tcp://{self.host}:{self.port}"

    async def wait_client(self, timeout_s: float = 20.0) -> bool:
        """等插件连上来（期间保持租约续期任务运行）。"""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.connected:
                return True
            await asyncio.sleep(0.5)
        return False

    # -------------------------------------------------- 连接信息文件（给插件发现用）
    def save_link_file(self, base_dir: Path | None = None) -> Path:
        from .profile import default_dir
        directory = Path(base_dir) if base_dir else default_dir()
        path = directory / LINK_FILE_NAME
        payload = {"host": self.host, "port": self.port, "token": self.token,
                   "protocol": PROTOCOL_VERSION, "written_at": time.time()}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    @staticmethod
    def remove_link_file(base_dir: Path | None = None) -> None:
        from .profile import default_dir
        directory = Path(base_dir) if base_dir else default_dir()
        try:
            (directory / LINK_FILE_NAME).unlink()
        except FileNotFoundError:
            pass

    async def close(self) -> None:
        await self.estop("engine closing")
        if self._renew_task:
            self._renew_task.cancel()
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    # -------------------------------------------------- 连接处理
    async def _handle_client(self, reader: asyncio.StreamReader,
                             writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=10)
            hello = json.loads(line.decode("utf-8", "replace"))
            if hello.get("op") != "hello" or hello.get("token") != self.token:
                writer.write(b'{"op":"hello_ack","accepted":false,"reason":"bad token"}\n')
                await writer.drain()
                return
            device = hello.get("device") or {}
            self.status = DeviceStatus(
                connected=bool(device.get("connected")),
                plugin=str(hello.get("plugin") or ""),
                plugin_version=str(hello.get("version") or ""),
                device_type=str(device.get("type") or ""),
                max_strength_a=int(device.get("max_a") or 0),
                max_strength_b=int(device.get("max_b") or 0))
            self._reader, self._writer = reader, writer
            await self._send({"op": "hello_ack", "accepted": True,
                              "protocol": PROTOCOL_VERSION})
            await self._send_lease()
            print(f"[device] 插件已连接（{self.status.plugin} "
                  f"{self.status.plugin_version}）设备={self.status.device_type or '-'} "
                  f"connected={self.status.connected} peer={peer}")
            await self._read_loop()
        except Exception as exc:  # noqa: BLE001
            self.status.last_error = f"client error: {exc}"
        finally:
            self.status.connected = False
            self._reader = self._writer = None
            try:
                writer.close()
            except Exception:
                pass
            print("[device] 插件已断开，设备输出失效")

    async def _read_loop(self) -> None:
        assert self._reader is not None
        while True:
            try:
                line = await self._reader.readline()
            except Exception:
                return
            if not line:
                return
            try:
                msg = json.loads(line.decode("utf-8", "replace"))
            except json.JSONDecodeError:
                continue
            op = msg.get("op")
            if op == "ack":
                event_id = str(msg.get("event_id") or "")
                fut = self._pending.pop(event_id, None)
                if fut and not fut.done():
                    fut.set_result(msg)
            elif op == "pong":
                pass
            elif op == "error":
                self.status.last_error = str(msg.get("message") or msg.get("msg") or "")

    # -------------------------------------------------- 租约
    async def _renew_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self.renew_s)
                if self._writer is not None:
                    await self._send_lease()
            except asyncio.CancelledError:
                return
            except Exception:
                continue

    async def _send_lease(self) -> None:
        self.lease_id += 1
        self.lease_sent_at = time.monotonic()
        await self._send({"op": "lease", "lease_id": self.lease_id,
                          "ttl_s": self.lease_ttl_s})

    @property
    def lease_valid(self) -> bool:
        return (self._writer is not None
                and time.monotonic() - self.lease_sent_at < self.lease_ttl_s * 1.2)

    # -------------------------------------------------- 输出 / 急停
    async def fire(self, event_id: str, intensity_pct: int, duration_s: float,
                   preset: str = "", channel: str = "both",
                   label: str = "OJHC") -> dict:
        if not self.connected:
            return {"ok": False, "note": "插件未连接"}
        if not self.lease_valid:
            return {"ok": False, "note": "租约已过期"}
        if self.status.estop:
            return {"ok": False, "note": "急停中"}
        nonce = secrets.token_hex(8)
        if nonce in self._used_nonces:          # 理论不可能，保险
            return {"ok": False, "note": "nonce 重放"}
        self._used_nonces.add(nonce)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[event_id] = fut
        await self._send({
            "op": "output", "event_id": event_id,
            "lease_id": self.lease_id, "nonce": nonce,
            "intensity_pct": int(intensity_pct),
            "duration_s": round(float(duration_s), 2),
            "preset": preset or "", "channel": channel, "label": label,
        })
        try:
            ack = await asyncio.wait_for(fut, timeout=2.0)
        except asyncio.TimeoutError:
            self._pending.pop(event_id, None)
            self.status.last_error = "ack timeout"
            return {"ok": False, "note": "插件 2s 内没有回执"}
        if ack.get("ok"):
            self.status.outputs += 1
        else:
            self.status.rejected += 1
        return ack

    async def estop(self, reason: str = "manual") -> None:
        self.status.estop = True
        self.lease_id += 1          # 让旧租约立即作废
        if self._writer is not None:
            try:
                await self._send({"op": "estop", "reason": reason})
            except Exception:
                pass

    async def resume(self) -> None:
        """允许再次输出（新会话/用户确认后）。"""
        self.status.estop = False
        if self._writer is not None:
            await self._send_lease()

    # -------------------------------------------------- 底层
    @property
    def connected(self) -> bool:
        return self._writer is not None and self.status.connected

    async def _send(self, payload: dict) -> None:
        if self._writer is None:
            return
        data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        async with self._write_lock:
            self._writer.write(data)
            await self._writer.drain()
