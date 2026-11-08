"""环境自检（doctor）：一次把"为什么不能开始"查清楚。

检查项：
    1. DGHub 是否在跑（扫候选端口的 /api/plugins/_session_token）
    2. WebSocket 握手是否通过（hello → hello_ack.accepted）
    3. 设备是否已连接（device_info.connected）
    4. 校准档案是否存在/有效/过期
    5. 冒烟插件是否已装进 DGHub 的 plugins/ 目录
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from . import profile as profile_mod
from .dghub import DGHubClient, discover
from .util import fail, info, ok, warn

PROBE_ID = "ojhc_probe"


def _candidate_plugin_dirs() -> list[Path]:
    dirs: list[Path] = []
    for env in ("DGHUB_HOME", "DGHUB_PLUGIN_DIR", "DGHUB_DIR"):
        value = os.environ.get(env)
        if value:
            base = Path(value)
            dirs.append(base / "plugins" if env == "DGHUB_HOME" else base)
    roots = [Path(os.environ.get("LOCALAPPDATA", "")) / "DGHub",
             Path(os.environ.get("APPDATA", "")) / "DGHub",
             Path("D:/DGHub"), Path("C:/DGHub")]
    for root in roots:
        if root and root.exists():
            dirs.append(root / "plugins")
    # 盘符根目录下的 DGHub / dghub
    for drive in "CDEFG":
        for name in ("DGHub", "dghub"):
            p = Path(f"{drive}:/{name}")
            if p.exists():
                dirs.append(p / "plugins")
    seen: set[str] = set()
    out: list[Path] = []
    for d in dirs:
        key = str(d).lower()
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


def find_probe_install() -> Path | None:
    for plugins in _candidate_plugin_dirs():
        target = plugins / PROBE_ID
        if (target / "manifest.json").exists():
            return target
    return None


async def _check_connection(host: str, port: int | None) -> dict:
    found = discover(host, [port] if port else None)
    if not found:
        return {"ok": False, "reason": "拿不到 token（DGHub 可能没在运行，或端口不对）",
                "port": port}
    real_port, token = found
    client = DGHubClient(host, real_port, token)
    try:
        await client.connect()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"握手失败：{exc}", "port": real_port}
    try:
        ready = await client.wait_device(timeout=3.0)
        return {
            "ok": True,
            "port": real_port,
            "sdk_version": client.sdk_version,
            "device": {
                "connected": client.device.connected,
                "device_type": client.device.device_type,
                "max_strength_a": client.device.max_strength_a,
                "max_strength_b": client.device.max_strength_b,
            },
            "waited_device": ready,
        }
    finally:
        await client.close()


async def run_doctor(host: str, port: int | None, base_dir: Path | None,
                     as_json: bool = False) -> int:
    result: dict = {"host": host}
    problems: list[str] = []

    # 1+2+3：连接 / 握手 / 设备
    conn = await _check_connection(host, port)
    result["connection"] = conn
    if not conn.get("ok"):
        problems.append(f"DGHub 连接：{conn.get('reason')}")
    else:
        dev = conn.get("device", {})
        if not dev.get("connected"):
            problems.append("设备未连接（DGHub 里先连上郊狼设备）")

    # 4：校准档案
    prof = profile_mod.load_profile(base_dir)
    if prof is None:
        result["profile"] = {"exists": False}
        problems.append("没有校准档案 → 先跑 `ojhc calibrate`")
    elif not prof.is_valid():
        result["profile"] = {"exists": True, "valid": False}
        problems.append("校准档案无效（字段越界）→ 重新校准")
    elif prof.expired():
        result["profile"] = {"exists": True, "valid": True, "expired": True,
                             "age_days": round(prof.age_days() or 0, 1)}
        problems.append(f"校准档案已过期（{prof.age_days():.0f} 天）→ 建议重新校准")
    else:
        result["profile"] = {
            "exists": True, "valid": True, "expired": False,
            "calibrated_at": prof.calibrated_at,
            "device_type": prof.device_type,
            "perceptible_pct": prof.perceptible_pct,
            "comfort_pct": prof.comfort_pct,
            "user_max_pct": prof.user_max_pct,
            "channel": prof.channel,
        }

    # 5：冒烟插件
    probe = find_probe_install()
    result["probe_plugin"] = {"installed": probe is not None,
                              "path": str(probe) if probe else None}
    if probe is None:
        problems.append("冒烟插件未安装 → 跑 starter/install_plugin.ps1")

    result["problems"] = problems
    result["ready"] = not problems

    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if not problems else 1

    print("OJHC Engine · doctor\n")
    print(f"{ok('DGHub 端口') if conn.get('ok') else fail('DGHub 连接')}: "
          f"{conn.get('port')}  {'' if conn.get('ok') else conn.get('reason','')}")
    if conn.get("ok"):
        dev = conn["device"]
        status = "已连接" if dev["connected"] else "未连接"
        print(f"{ok('设备') if dev['connected'] else warn('设备')}: {status}  "
              f"type={dev['device_type'] or '-'}  max_a={dev['max_strength_a']} "
              f"max_b={dev['max_strength_b']}")
    if prof is None:
        print(f"{warn('校准档案')}: 还没有，跑 `ojhc calibrate` 生成")
    elif prof.is_valid() and not prof.expired():
        print(f"{ok('校准档案')}: I_p={prof.perceptible_pct}% I_c={prof.comfort_pct}% "
              f"上限={prof.user_max_pct}%  通道={prof.channel}  于 {prof.calibrated_at}")
    elif prof.expired():
        print(f"{warn('校准档案')}: 已过期（{prof.age_days():.0f} 天），建议重新校准")
    else:
        print(f"{fail('校准档案')}: 内容无效，需重新校准")
    print(f"{ok('冒烟插件') if probe else warn('冒烟插件')}: "
          f"{probe if probe else '未安装（starter/install_plugin.ps1）'}")

    print()
    if problems:
        print(fail(f"还差 {len(problems)} 项才能开始："))
        for i, p in enumerate(problems, 1):
            print(f"  {i}. {p}")
        return 1
    print(ok("一切就绪，可以跑 `ojhc calibrate` 或后续的 `ojhc run` 了"))
    return 0
