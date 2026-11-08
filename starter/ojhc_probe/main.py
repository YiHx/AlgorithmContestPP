"""OJHC 冒烟插件（M1）—— 验证 DGHub 链路、一次可控测试脉冲、随时可停。

只用标准库 + websockets（DGHub 主程序环境已提供），不依赖 dghub-sdk，
方便手动调试，也方便以后把这段代码直接长成正式插件。

安全设计（对应 DESIGN.md §7）：
  1. 默认只展示：`armed=false` 时只发 op=event 卡片，绝不输出电流；
  2. 只用 trigger + rollback：脉冲结束自动回到 baseline，绝不用 permanent；
  3. 二次钳制：配置里的强度/时长在代码里再钳一次（HARD_CAP_PCT / HARD_MAX_DURATION_S）；
  4. 收到 stop / 断线 / Ctrl+C 立即退出，不残留任何持续状态。

手动调试（纯 CLI，不经过 DGHub 拉起）：
    pip install websockets
    python main.py --port 27020 --token <token>
    python main.py --port 27020 --token <token> --armed --intensity 20 --duration 0.8 --fire-now

自检（不联网，验证 manifest 与钳制逻辑）：
    python main.py --selftest
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

try:
    import websockets
    WS_IMPORT_ERROR: Exception | None = None
except ImportError as exc:  # pragma: no cover - 只在裸机手动调试时发生
    websockets = None  # type: ignore[assignment]
    WS_IMPORT_ERROR = exc

# ---------------------------------------------------------------- 代码级硬边界
HARD_CAP_PCT = 40          # 单次强度硬上限（%），配置改不动
HARD_MAX_DURATION_S = 3.0  # 单次脉冲硬上限（秒）
MIN_PULSE_S = 0.3          # 太短的脉冲感知不到，不允许
DEFAULT_PORT = "27020"     # SDK 文档里手动调试示例用的端口

PLUGIN_ROOT = Path(os.environ.get("DGHUB_PLUGIN_ROOT") or Path(__file__).resolve().parent)


# ---------------------------------------------------------------- 工具函数
def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def load_manifest() -> dict:
    return json.loads((PLUGIN_ROOT / "manifest.json").read_text(encoding="utf-8"))


def schema_defaults(manifest: dict) -> dict:
    out: dict = {}
    for section in manifest.get("config_schema", []):
        for field in section.get("fields", []):
            if "key" in field and "default" in field:
                out[field["key"]] = field["default"]
    return out


def describe_config(cfg: dict) -> str:
    return (
        f"armed={cfg.get('armed')} fire_on_start={cfg.get('fire_on_start')} "
        f"intensity={cfg.get('test_intensity')}% duration={cfg.get('test_duration')}s "
        f"channel={cfg.get('channel')} preset={cfg.get('preset')!r}"
    )


def apply_overrides(st: "State") -> None:
    """CLI 传进来的参数优先级最高（每次配置推送后重新盖一次）。"""
    for key, value in st.overrides.items():
        if value is not None:
            st.cfg[key] = value


def fetch_token(host: str, port: str) -> str | None:
    """调试方便：从主程序 API 拉会话 token（仅本机）。"""
    url = f"http://{host}:{port}/api/plugins/_session_token"
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            body = resp.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        print(f"[ojhc] 拉取 token 失败（{url}）：{exc}", file=sys.stderr)
        return None
    body = body.strip()
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


# ---------------------------------------------------------------- 运行状态
class State:
    def __init__(self, manifest: dict) -> None:
        self.manifest = manifest
        self.cfg: dict = schema_defaults(manifest)
        self.overrides: dict = {}
        self.ready = False
        self.device_connected = False
        self.device_type = ""
        self.next_pulse_at: float | None = None
        self.pulse_retries = 0
        self.running = True


async def send(ws, payload: dict) -> None:
    await ws.send(json.dumps(payload, ensure_ascii=False))


async def send_log(ws, level: str, message: str) -> None:
    print(f"[ojhc] {level.upper()}: {message}")
    await send(ws, {"op": "log", "level": level, "message": message})


async def push_startup_check(ws, st: State) -> None:
    armed = bool(st.cfg.get("armed", False))
    steps = [
        {
            "key": "plugin",
            "title": "插件进程",
            "state": "ok",
            "detail": "已连接 DGHub，握手完成",
        },
        {
            "key": "device",
            "title": "设备连接",
            "state": "ok" if st.device_connected else "pending",
            "detail": (
                f"已连接：{st.device_type or '未知型号'}"
                if st.device_connected
                else "还没有收到 device_info.connected=true"
            ),
            "hint": "在 DGHub 里连接郊狼设备后会自动刷新",
        },
        {
            "key": "armed",
            "title": "真实输出开关",
            "state": "warn" if armed else "idle",
            "detail": "已打开：下一次触发会真的输出" if armed else "关闭：只展示，不输出",
            "hint": "确认好强度和通道再打开",
        },
    ]
    await send(
        ws,
        {"op": "status", "fields": {"startup_check": {"title": "OJHC 冒烟检查", "steps": steps}}},
    )


async def push_display_status(ws, st: State) -> None:
    armed = bool(st.cfg.get("armed", False))
    if not armed:
        text = "待命（仅展示，不输出）"
    elif st.device_connected:
        text = "就绪（真实输出已开启）"
    else:
        text = "等待设备连接"
    await send(ws, {"op": "status", "fields": {"display_status": text}})


async def fire_once(ws, st: State) -> None:
    """发一次测试：先展示卡片；若允许且设备在线，再发 rollback 脉冲。"""
    cfg = st.cfg
    cap = min(int(cfg.get("max_intensity", 30) or 0), HARD_CAP_PCT)
    intensity = int(clamp(int(cfg.get("test_intensity", 15) or 0), 0, cap))
    duration = clamp(float(cfg.get("test_duration", 0.8) or 0.8), MIN_PULSE_S, HARD_MAX_DURATION_S)
    channel = cfg.get("channel", "both") or "both"
    preset = str(cfg.get("preset") or "").strip()
    armed = bool(cfg.get("armed", False))

    # 1) 展示事件：无论是否输出都发，便于在 DGHub 里确认链路
    await send(
        ws,
        {
            "op": "event",
            "label": "OJHC 测试",
            "name": f"强度 {intensity}% / {duration:.1f}s / {'真实输出' if armed else '仅展示'}",
            "strength_pct": intensity,
            "duration": duration,
            "event_id": f"probe-{int(time.time())}",
        },
    )

    # 2) 安全闸门（代码内化简版；正式插件由 Engine 的 11 条闸门负责）
    if not armed:
        await send_log(ws, "info", "未开启『允许真实输出』：只发了展示卡片，没有输出")
        return
    if not st.device_connected:
        await send_log(ws, "warning", "设备未连接：只发了展示卡片，没有输出")
        return
    if intensity <= 0:
        await send_log(ws, "warning", "强度为 0：不输出")
        return

    payload = {
        "op": "trigger",
        "strength_mode": "rollback",   # 用完自动回正，绝不 permanent
        "delta_pct": intensity,
        "duration_s": round(duration, 2),
        "channel": channel,
        "label": "OJHC 测试脉冲",
    }
    if preset:
        payload["action"] = "both"
        payload["preset"] = preset
    else:
        payload["action"] = "strength"  # 没有波形预设时只改强度，避免必然失败的 trigger

    await send(ws, payload)
    await send_log(
        ws,
        "info",
        f"已发送测试脉冲：{intensity}% / {duration:.1f}s / channel={channel} / preset={preset or '(无，仅强度)'}",
    )


def schedule_pulse(st: State, delay_s: float = 2.0) -> None:
    st.next_pulse_at = time.monotonic() + delay_s


# ---------------------------------------------------------------- 主循环
async def run(host: str, port: str, token: str,
              overrides: dict | None = None, fire_now: bool = False) -> int:
    if websockets is None:
        print(f"[ojhc] 缺少 websockets（{WS_IMPORT_ERROR}）：pip install websockets", file=sys.stderr)
        return 2
    manifest = load_manifest()
    st = State(manifest)
    st.overrides = overrides or {}
    apply_overrides(st)
    url = f"ws://{host}:{port}/ws/plugin?token={token}"

    try:
        ws = await websockets.connect(url, max_size=2**20)
    except Exception as exc:  # noqa: BLE001
        print(f"[ojhc] 连接失败：{url}\n       原因：{exc}", file=sys.stderr)
        print("[ojhc] DGHub 是否在运行？手动调试需要 --port 和 --token。", file=sys.stderr)
        return 4

    async with ws:
        # 1) 握手：必须是第一条消息
        await send(
            ws,
            {
                "op": "hello",
                "token": token,
                "manifest": manifest,
            },
        )

        ack_raw = await asyncio.wait_for(ws.recv(), timeout=10)
        ack = json.loads(ack_raw)
        if ack.get("op") != "hello_ack":
            print(f"[ojhc] 期望 hello_ack，收到：{ack_raw[:200]}", file=sys.stderr)
            return 4
        if not ack.get("accepted"):
            print(f"[ojhc] 握手被拒：{ack.get('reason')}", file=sys.stderr)
            return 4
        st.ready = True
        print(f"[ojhc] 握手成功（sdk {ack.get('sdk_version')}），插件 root={PLUGIN_ROOT}")

        await send_log(ws, "info", "OJHC 冒烟插件已连接")
        if st.overrides:
            print(f"[ojhc] CLI 覆盖生效：{describe_config(st.cfg)}")
        if fire_now:
            print("[ojhc] --fire-now：约 1 秒后发一次测试脉冲（会等设备就绪）")
            schedule_pulse(st, 1.0)

        # 2) 消息循环：顺便处理到点的测试脉冲
        while st.running:
            timeout = 0.2
            if st.next_pulse_at is not None:
                remain = st.next_pulse_at - time.monotonic()
                timeout = max(0.02, min(timeout, remain))
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            except asyncio.TimeoutError:
                if st.next_pulse_at is not None and time.monotonic() >= st.next_pulse_at:
                    if not st.device_connected and st.pulse_retries < 10:
                        st.pulse_retries += 1
                        st.next_pulse_at = time.monotonic() + 0.5
                        print(f"[ojhc] 等待设备连接再发脉冲（{st.pulse_retries}/10）")
                    else:
                        st.next_pulse_at = None
                        st.pulse_retries = 0
                        await fire_once(ws, st)
                        await push_display_status(ws, st)
                        await push_startup_check(ws, st)
                continue

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await send_log(ws, "warning", f"收到非 JSON 消息，已忽略：{raw[:80]!r}")
                continue

            op = msg.get("op")

            if op == "config":
                st.cfg.update(msg.get("data") or {})
                apply_overrides(st)
                print(f"[ojhc] 收到配置：{describe_config(st.cfg)}")
                await push_startup_check(ws, st)
                await push_display_status(ws, st)
                if st.cfg.get("fire_on_start") and st.cfg.get("armed"):
                    await send_log(ws, "info", "2 秒后自动发一次测试脉冲")
                    schedule_pulse(st, 2.0)

            elif op == "config_changed":
                key, value = msg.get("key"), msg.get("value")
                st.cfg[key] = value
                apply_overrides(st)
                print(f"[ojhc] 配置变更：{key} = {value!r}")
                await push_startup_check(ws, st)
                await push_display_status(ws, st)
                # 重新打开开关即可重复触发一次，避免必须加按钮
                if key == "fire_on_start" and value and st.cfg.get("armed"):
                    await send_log(ws, "info", "开关已打开：2 秒后发一次测试脉冲")
                    schedule_pulse(st, 2.0)

            elif op == "device_info":
                was = st.device_connected
                st.device_connected = bool(msg.get("connected"))
                st.device_type = msg.get("device_type") or ""
                print(
                    f"[ojhc] 设备状态：connected={st.device_connected} "
                    f"type={st.device_type or '-'} "
                    f"max_a={msg.get('max_strength_a')} max_b={msg.get('max_strength_b')}"
                )
                await push_startup_check(ws, st)
                await push_display_status(ws, st)
                if st.device_connected and not was:
                    await send_log(ws, "info", "设备已连接")
                elif was and not st.device_connected:
                    await send_log(ws, "warning", "设备已断开")

            elif op == "ping":
                await send(ws, {"op": "pong", "t": msg.get("t")})

            elif op == "stop":
                await send_log(ws, "info", f"收到 stop（{msg.get('reason')}），退出")
                st.running = False

            else:
                print(f"[ojhc] 未处理的 op={op!r}")

    return 0


# ---------------------------------------------------------------- 自检
def selftest() -> int:
    manifest = load_manifest()
    problems: list[str] = []

    if not manifest.get("id", "").startswith("ojhc_"):
        problems.append("manifest.id 不符合预期")
    if manifest.get("sdk") != "1":
        problems.append("manifest.sdk 必须是 '1'")
    if manifest.get("entry") != "main.py":
        problems.append("manifest.entry 必须是 main.py")

    defaults = schema_defaults(manifest)
    expected = {"armed", "fire_on_start", "test_intensity", "test_duration", "channel", "preset", "max_intensity"}
    if set(defaults) != expected:
        problems.append(f"config_schema 字段不匹配：{sorted(defaults)}")

    cap = min(int(defaults.get("max_intensity", 30)), HARD_CAP_PCT)
    cases = [
        (int(defaults.get("test_intensity", 15)), cap, "默认强度"),
        (999, cap, "超范围强度"),
    ]
    for raw, limit, name in cases:
        got = int(clamp(raw, 0, limit))
        if got > HARD_CAP_PCT:
            problems.append(f"{name} 钳制失败：{got} > {HARD_CAP_PCT}")
        print(f"  钳制 [{name}] {raw} -> {got}%（上限 {limit}）")

    dur = clamp(float(defaults.get("test_duration", 0.8)), MIN_PULSE_S, HARD_MAX_DURATION_S)
    print(f"  钳制 [时长] {defaults.get('test_duration')} -> {dur:.2f}s（{MIN_PULSE_S}~{HARD_MAX_DURATION_S}）")

    print(f"  plugin_root = {PLUGIN_ROOT}")
    print(f"  配置默认值 = {json.dumps(defaults, ensure_ascii=False)}")

    if problems:
        print("[FAIL] " + "; ".join(problems))
        return 1
    print("[OK] manifest 与钳制逻辑自检通过")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="OJHC 冒烟插件（可纯 CLI 调试）")
    parser.add_argument("--host", default=os.environ.get("DGHUB_HOST", "127.0.0.1"))
    parser.add_argument("--port", default=os.environ.get("DGHUB_PORT", DEFAULT_PORT))
    parser.add_argument("--token", default=os.environ.get("DGHUB_TOKEN"))
    parser.add_argument("--selftest", action="store_true", help="只做本地自检，不联网")

    # ---- CLI 覆盖配置：不用打开 DGHub 配置页也能测 ----
    parser.add_argument("--armed", action="store_true", help="等价配置 armed=true（真实输出）")
    parser.add_argument("--intensity", type=int, default=None, help="强度 %%，覆盖配置")
    parser.add_argument("--duration", type=float, default=None, help="脉冲时长（秒），覆盖配置")
    parser.add_argument("--preset", default=None, help="波形预设名，覆盖配置（留空则只改强度）")
    parser.add_argument("--channel", choices=["a", "b", "both"], default=None, help="通道")
    parser.add_argument("--fire-now", action="store_true", help="握手后立刻发一次测试脉冲")

    args = parser.parse_args()

    if args.selftest:
        return selftest()

    overrides: dict = {}
    if args.armed:
        overrides["armed"] = True
    if args.intensity is not None:
        overrides["test_intensity"] = args.intensity
    if args.duration is not None:
        overrides["test_duration"] = args.duration
    if args.preset is not None:
        overrides["preset"] = args.preset
    if args.channel is not None:
        overrides["channel"] = args.channel

    token = args.token
    if not token:
        print("[ojhc] 未注入 DGHUB_TOKEN，尝试从主程序 API 获取…")
        token = fetch_token(args.host, args.port)
    if not token:
        print(
            "[ojhc] 拿不到 token。\n"
            "       正常由 DGHub 启用插件时自动注入；手动调试请先访问：\n"
            f"       http://{args.host}:{args.port}/api/plugins/_session_token 复制 token 再 --token 传入。",
            file=sys.stderr,
        )
        return 3

    try:
        return asyncio.run(
            run(args.host, str(args.port), token, overrides=overrides, fire_now=args.fire_now)
        )
    except KeyboardInterrupt:
        print("\n[ojhc] 手动中断，退出")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
