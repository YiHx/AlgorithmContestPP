"""校准向导（M1）：测出个人感知区间 [I_p, I_c]，写入 device_profile.json。

流程对应 DESIGN.md §7.9.1：
    Step 0 连接检查（设备必须在线）
    Step 1 感知阈值 I_p：从低到高，每档脉冲 0.8s，问"有感觉吗"，3 次确认取多数
    Step 2 舒适上限 I_c：继续往上，直到"不适"
    Step 3 手感预览：按 frac=0.35/0.50/0.65 各打一次，确认能分辨强弱
    Step 4 写档案（user_max = min(I_c*0.8, 60)）

安全：校准期脉冲上限 50%、0.8s、间隔 ≥3s、只用 rollback；Ctrl+C 立即中止且不写档案。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from . import profile as profile_mod
from .dghub import DGHubClient, DeviceInfo
from .util import Prompter, clamp, clamp_int, fail, info, median, ok, warn

# 校准期专用限制（都按“设备上限的百分比”，不会超过 App 里设的上限）
CAL_MAX_PCT = 80            # 最多测到设备上限的 80%，留 20% 余量
CAL_PULSE_S = 1.2           # 1.2s 比 0.8s 更容易稳定感觉到
CAL_COOLDOWN_S = 3.0
CAL_STEP_PCT = 10           # 从 10% 起，每档 +10%（更少步骤、更好分辨）
CAL_MIN_SPAN = 15
CAL_MIN_USABLE = 10      # user_max - I_p 至少这么宽，否则档位分不出强弱
CAL_CONFIRM_TIMES = 3
CAL_DEFAULT_PRESET = "呼吸"   # 必须有波形预设，否则 DGHub 只改强度、设备不输出

PREVIEW_FRACS = (0.35, 0.50, 0.65)


def _available_presets(host: str, port: int) -> list[str]:
    """从 DGHub HTTP API 读可用波形预设名（失败就返回空列表）。"""
    import json
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/pulse-presets", timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return [str(p.get("name")) for p in data.get("presets", []) if p.get("name")]
    except Exception:
        return []


def _resolve_preset(host: str, port: int, requested: str) -> str:
    names = _available_presets(host, port)
    requested = (requested or "").strip()
    if not names:
        return requested or CAL_DEFAULT_PRESET
    if requested and requested in names:
        return requested
    if requested:
        print(warn(f"波形预设 {requested!r} 不在 DGHub 预设列表里，改用「呼吸」"))
    return "呼吸" if "呼吸" in names else names[0]


class CalibrationAborted(Exception):
    pass


async def _pulse(client: DGHubClient, pct: int, preset: str, channel: str,
                 dry_run: bool, fast: bool) -> None:
    pct = int(clamp(pct, 0, CAL_MAX_PCT))
    dev_max = max(int(client.device.max_strength_a or 0), int(client.device.max_strength_b or 0))
    if dev_max and not dry_run:
        print(f"      → 实际强度 ≈ {round(pct / 100 * dev_max)}（设备上限 {dev_max}）")
    if not dry_run:
        await client.trigger(delta_pct=pct, duration_s=CAL_PULSE_S, preset=preset,
                             channel=channel, label=f"OJHC 校准 {pct}%")
    else:
        await client.trigger(delta_pct=pct, duration_s=CAL_PULSE_S, preset=preset,
                             channel=channel, label=f"OJHC 校准 {pct}%", dry_run=True)
    if not fast:
        await asyncio.sleep(CAL_COOLDOWN_S)


async def _step_perceptible(client: DGHubClient, prompter: Prompter, preset: str,
                            channel: str, dry_run: bool, fast: bool) -> int:
    print("\n=== Step 1：找「刚能感觉到」的强度（I_p） ===")
    print("每档会打一次 1.2 秒脉冲。轻微麻/刺也算“有感觉”；连续 3 次达 2 次即认定。")
    pct = CAL_STEP_PCT
    while pct <= CAL_MAX_PCT:
        print(f"\n[{pct}%] 打一次…", flush=True)
        await _pulse(client, pct, preset, channel, dry_run, fast)
        if not prompter.confirm(f"{pct}% 有感觉吗？"):
            print(f"  → {pct}% 没感觉，继续升档")
            pct += CAL_STEP_PCT
            continue
        confirms = 1
        for i in range(CAL_CONFIRM_TIMES - 1):
            print(f"  再来一次确认（{i + 2}/{CAL_CONFIRM_TIMES}）…")
            await _pulse(client, pct, preset, channel, dry_run, fast)
            if prompter.confirm(f"第 {i + 2} 次 {pct}% 也有感觉吗？"):
                confirms += 1
        if confirms >= 2:
            print(f"  → I_p = {pct}%（{confirms}/{CAL_CONFIRM_TIMES} 次有感觉）")
            return pct
        print(f"  → {pct}% 确认不足，继续升档")
        pct += CAL_STEP_PCT
    raise CalibrationAborted(
        f"到 {CAL_MAX_PCT}% 仍确认不了感知阈值。检查：电极贴合、电量、通道选择、波形预设。"
        f"若 DGHub 显示的设备上限（max_a/max_b）很低，可在 DG-Lab App 里适当调高上限后重试。"
    )


async def _step_comfort(client: DGHubClient, prompter: Prompter, preset: str,
                        channel: str, dry_run: bool, fast: bool,
                        perceptible: int) -> int:
    print("\n=== Step 2：找「舒适上限」（I_c） ===")
    print("继续升档，问「还能接受吗」。第一次答 n 就停，取上一档为 I_c。")
    comfort = perceptible
    pct = perceptible + CAL_STEP_PCT
    while pct <= CAL_MAX_PCT:
        print(f"\n[{pct}%] 打一次…", flush=True)
        await _pulse(client, pct, preset, channel, dry_run, fast)
        if prompter.confirm(f"{pct}% 还能接受吗（不是不适）？"):
            comfort = pct
            print(f"  → {pct}% 可接受，继续")
            pct += CAL_STEP_PCT
            continue
        print(f"  → {pct}% 不适，I_c 取上一档 {comfort}%")
        break
    else:
        print(f"  → 到 {CAL_MAX_PCT}% 仍可接受，I_c 封顶到 {CAL_MAX_PCT}%（建议找更高上限时改设备档位）")

    if comfort - perceptible < CAL_MIN_SPAN:
        raise CalibrationAborted(
            f"感知区间过窄（I_c={comfort}% − I_p={perceptible}% < {CAL_MIN_SPAN}%）。"
            "检查电极贴合/电量/通道，或换一个波形预设再试。"
        )
    return comfort


def _preview_intensity(prof: profile_mod.DeviceProfile, frac: float) -> int:
    return prof.intensity_for(frac)


async def _step_preview(client: DGHubClient, prompter: Prompter, preset: str,
                        channel: str, dry_run: bool, fast: bool,
                        prof: profile_mod.DeviceProfile) -> bool:
    print("\n=== Step 3：手感预览（s=2/3/4 三档） ===")
    for frac in PREVIEW_FRACS:
        pct = _preview_intensity(prof, frac)
        print(f"\n[强度 {pct}%（档位 {frac:.2f}）] 打一次…", flush=True)
        await _pulse(client, pct, preset, channel, dry_run, fast)
    return prompter.confirm("三档强弱能分辨出来吗？")


async def run_calibration(host: str, port: int, token: str, *,
                          base_dir: Path | None = None,
                          channel: str = "both",
                          preset: str = "",
                          dry_run: bool = False,
                          fast: bool = False,
                          script: list[str] | None = None) -> int:
    prompter = Prompter(script)
    client = DGHubClient(host, port, token)
    try:
        if dry_run:
            print(warn("dry-run：离线模拟整个流程，不连接 DGHub，也不会输出电流"))
            client.device = DeviceInfo(connected=True, device_type="dry-run",
                                       max_strength_a=0, max_strength_b=0)
        else:
            print(info(f"连接 DGHub：ws://{host}:{port}"))
            await client.connect()
            print(ok(f"握手成功（sdk {client.sdk_version}）"))
        if not dry_run:
            if not await client.wait_device(timeout=8.0):
                print(fail("设备未连接。请先在 DGHub 里连上郊狼设备（蓝牙），再重跑。"))
                return 3
            print(ok(f"设备就绪：type={client.device.device_type or '未知'} "
                     f"max_a={client.device.max_strength_a} max_b={client.device.max_strength_b}"))

        print("\n校准开始前请确认：")
        print("  * 电极贴好、电量充足、通道选择正确")
        print("  * 随时可以按 Ctrl+C 中止（不会写档案，也不会留下持续输出）")
        preset = (preset or CAL_DEFAULT_PRESET) if dry_run else _resolve_preset(host, port, preset)
        print(info(f"波形预设：{preset}（必须有波形，DGHub 才会真的输出）"))
        if not prompter.confirm("准备好了吗？", default=True):
            raise CalibrationAborted("用户取消")

        perceptible = await _step_perceptible(client, prompter, preset, channel, dry_run, fast)
        comfort = await _step_comfort(client, prompter, preset, channel, dry_run, fast, perceptible)

        user_max = min(int(comfort * 0.8), profile_mod.HARD_CAP_PCT)
        if user_max - perceptible < CAL_MIN_USABLE:
            raise CalibrationAborted(
                f"可用区间太窄（I_p={perceptible}%，安全上限={user_max}%，只差 "
                f"{user_max - perceptible}%）。档位之间会分不出强弱："
                "换贴合位置/通道/波形预设后重测，或把 I_c 测到更高。"
            )

        prof = profile_mod.new_profile(perceptible, comfort,
                                       client.device.device_type, channel, preset,
                                       device_max=client.device.max_strength_a or 0)
        distinguishable = await _step_preview(client, prompter, preset, channel, dry_run, fast, prof)

        if dry_run:
            print("\n" + ok(f"dry-run 完成（未写档案）：I_p={perceptible}%  I_c={comfort}%  本次上限={user_max}%"))
            return 0
        path = profile_mod.save_profile(prof, base_dir)
        print("\n" + ok(f"校准完成：I_p={perceptible}%  I_c={comfort}%  本次上限={user_max}%"))
        if not distinguishable:
            print(warn("三档分辨度不理想：可以换个波形预设或通道再校准一次（旧档案会被覆盖）"))
        print(ok(f"档案已写入：{path}"))
        print(info("下次校准提醒：换设备/换通道、超过 30 天、或者觉得「没感觉」时重跑。"))
        return 0

    except CalibrationAborted as exc:
        print("\n" + warn(f"校准中止：{exc}"))
        print(info("未写入档案，设备不会被留下持续输出（全部是 rollback 脉冲）。"))
        return 2
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\n" + warn("手动中止，未写入档案。"))
        return 2
    finally:
        await client.close()


_ = median  # 保留导入，便于后续多档取中位
