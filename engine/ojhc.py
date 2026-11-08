"""OJHC Engine 命令行入口。

用法（在 engine/ 目录下）：
    python ojhC.py doctor            # 环境自检：DGHub / 设备 / 档案 / 插件
    python ojhC.py calibrate         # 校准向导，测出个人感知区间
    python ojhC.py calibrate --dry-run   # 不输出电流，只走流程（可配 --script 自动化）
    python ojhC.py status            # 看当前设备档案与配置
    python ojhC.py selftest          # 纯本地自检，不联网

设计：M1 阶段只做"设备链路 + 校准"，比赛会话（run）在 M2 接 CF 适配器后加入。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# 允许从任意目录运行：把 engine/ 加入 sys.path
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from ojhc_lib import __version__ as VERSION  # noqa: E402
from ojhc_lib import calibrate as calibrate_mod  # noqa: E402
from ojhc_lib import cf as cf_mod  # noqa: E402
from ojhc_lib import doctor as doctor_mod  # noqa: E402
from ojhc_lib import feedback as feedback_mod  # noqa: E402
from ojhc_lib import pressure as pressure_mod  # noqa: E402
from ojhc_lib import profile as profile_mod  # noqa: E402
from ojhc_lib import runner as runner_mod  # noqa: E402
from ojhc_lib.dghub import ENGINE_MANIFEST, resolve_connection  # noqa: E402
from ojhc_lib.util import Prompter, clamp_int, fail, info, ok, warn  # noqa: E402


def _fix_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except Exception:
            pass


def cmd_doctor(args: argparse.Namespace) -> int:
    return asyncio.run(doctor_mod.run_doctor(args.host, args.port,
                                             Path(args.profile_dir) if args.profile_dir else None,
                                             as_json=args.json))


def cmd_calibrate(args: argparse.Namespace) -> int:
    if args.dry_run:
        conn = (args.port or 0, "dry-run")
    else:
        conn = resolve_connection(args.host, args.port, args.token)
    if conn is None:
        print(fail("找不到 DGHub：token 拿不到。先确认 DGHub 已启动，或用 --port 指定端口。"))
        print(info("也可以用 `python ojhC.py doctor` 看详细原因。"))
        return 4
    port, token = conn
    script = [s.strip() for s in args.script.split(",")] if args.script else None
    return asyncio.run(calibrate_mod.run_calibration(
        args.host, port, token,
        base_dir=Path(args.profile_dir) if args.profile_dir else None,
        channel=args.channel,
        preset=args.preset,
        dry_run=args.dry_run,
        fast=args.fast,
        script=script,
    ))


def cmd_run(args: argparse.Namespace) -> int:
    base = Path(args.profile_dir) if args.profile_dir else None
    prof = profile_mod.load_profile(base)
    profile_source = "real"
    if prof is None or not prof.is_valid():
        prof = profile_mod.new_profile(perceptible=20, comfort=45,
                                       device_type="demo", channel="both", preset="")
        profile_source = "demo"
        print(warn("没有有效校准档案 → 使用演示档案（I_p=20% / I_c=45%）"))
        print(info("演示档案只为展示流水线，不代表你的真实手感；正式使用前先跑 calibrate。"))

    opts = runner_mod.RunOptions(
        rating=args.rating, preset=args.preset, target_percentile=args.percentile,
        speed=args.speed, dry_run=True)

    if args.mock is not None:
        path = args.mock or str(_HERE / "fixtures" / "cf_1847_mock.json")
        timeline = cf_mod.load_mock(path)
        print(info(f"离线回放：{timeline.meta.name}（{timeline.meta.duration_s / 60:.0f} 分钟，"
                   f"{len(timeline.poll(None))} 次提交）"))
        result = asyncio.run(runner_mod.replay(timeline, prof, opts))
    elif args.contest and args.handle:
        print(info(f"live 模式：contest {args.contest} / handle {args.handle}（需要网络与官方额度）"))
        client = cf_mod.LiveCodeforces(args.handle, args.contest, args.rating)
        result = asyncio.run(runner_mod.live(client, prof, opts))
    else:
        print(fail("需要 `--mock`（离线回放）或 `--contest + --handle`（真实比赛）"))
        return 2

    result.summary["profile"] = profile_source
    _print_summary(result)
    if args.json:
        print(json.dumps(result.summary, ensure_ascii=False, indent=2))
    return 0


def _print_summary(result: "runner_mod.RunResult") -> None:
    s = result.summary
    print("\n" + "=" * 62)
    print(f"会话总结：{s['contest']}")
    print(f"  档案        : {'演示档案（未校准）' if s.get('profile') == 'demo' else '真实校准档案'}")
    print(f"  事件统计    : {s['submissions_events']}")
    print(f"  若启用设备  : {s['would_fire']} 次 {s['would_fire_by_kind']}")
    print(f"  拒绝原因 top: {s['denied_top']}")
    print(f"  压力分      : min {s['pressure_min']} / max {s['pressure_max']} / "
          f"结束 {s['pressure_end']}（L{s['level_end']}）")
    print(f"  已 AC       : {s['solved']} 题，E_total≈{s['e_total']}")
    print("=" * 62)


def cmd_status(args: argparse.Namespace) -> int:
    base = Path(args.profile_dir) if args.profile_dir else None
    prof = profile_mod.load_profile(base)
    cfg = profile_mod.load_config(base)
    data = {
        "version": VERSION,
        "profile": None,
        "config": {
            "preset": cfg.preset,
            "channel": cfg.channel,
            "allow_device_on_dom_source": cfg.allow_device_on_dom_source,
        },
    }
    if prof is not None:
        data["profile"] = {
            "valid": prof.is_valid(),
            "expired": prof.expired(),
            "age_days": round(prof.age_days() or 0, 1) if prof.calibrated_at else None,
            "calibrated_at": prof.calibrated_at,
            "device_type": prof.device_type,
            "perceptible_pct": prof.perceptible_pct,
            "comfort_pct": prof.comfort_pct,
            "user_max_pct": prof.user_max_pct,
            "channel": prof.channel,
            "preset": prof.preset,
            "mapping_example": {
                "s=2": prof.intensity_for(0.35),
                "s=3": prof.intensity_for(0.50),
                "s=4": prof.intensity_for(0.65),
                "s=5": prof.intensity_for(0.85),
            } if prof.is_valid() else None,
        }
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    print(f"OJHC Engine v{VERSION}")
    print(f"档案目录: {base or profile_mod.DEFAULT_DIR}")
    if prof is None:
        print(warn("没有设备档案：先跑 `ojhc calibrate`"))
    else:
        state = "有效" if prof.is_valid() and not prof.expired() else (
            "过期" if prof.expired() else "无效")
        print(f"设备档案: {state}  {prof.calibrated_at}")
        print(f"  感知区间: {prof.perceptible_pct}% ~ {prof.comfort_pct}%  "
              f"本次上限: {prof.user_max_pct}%  通道: {prof.channel}")
        if prof.is_valid():
            print(f"  档位映射示例: s=2→{prof.intensity_for(0.35)}%  s=3→{prof.intensity_for(0.50)}%  "
                  f"s=4→{prof.intensity_for(0.65)}%  s=5→{prof.intensity_for(0.85)}%")
            print(f"  可用区间: I_p={prof.perceptible_pct}% → 安全上限={prof.user_max_pct}%"
                  f"（跨度 {prof.usable_span}%）")
    print(f"用户配置: preset={cfg.preset}  channel={cfg.channel}  "
          f"C级源允许设备={cfg.allow_device_on_dom_source}")
    return 0


def cmd_selftest(args: argparse.Namespace) -> int:
    problems: list[str] = []

    # 1) 档案映射：单调、有界
    prof = profile_mod.new_profile(perceptible=20, comfort=40, device_type="v3",
                                   channel="both", preset="")
    if not prof.is_valid():
        problems.append("new_profile 生成的档案无效")
    if prof.user_max_pct != min(int(40 * 0.8), profile_mod.HARD_CAP_PCT):
        problems.append(f"user_max 计算错误：{prof.user_max_pct}")
    if prof.usable_span < 10:
        problems.append(f"可用区间过窄：{prof.usable_span}%")
    seq = [prof.intensity_for(f) for f in (0.0, 0.2, 0.35, 0.5, 0.65, 0.85, 1.0)]
    if seq != sorted(seq):
        problems.append(f"档位映射不单调：{seq}")
    if len(set(seq)) < 4:
        problems.append(f"档位映射区分度不足（只有 {len(set(seq))} 个不同值）：{seq}")
    if min(seq) < prof.perceptible_pct:
        problems.append(f"档位映射低于感知阈值：{seq}")
    print(f"  映射(20~40, 上限{prof.user_max_pct}) = {seq}")

    # 2) 钳制
    for raw, lo, hi in ((999, 0, 50), (-5, 0, 50)):
        got = clamp_int(raw, lo, hi)
        if not (lo <= got <= hi):
            problems.append(f"钳制失败 {raw}->{got}")
    print("  钳制 OK")

    # 3) Prompter 脚本模式
    p = Prompter(["y", "n"])
    if not p.confirm("q1") or p.confirm("q2"):
        problems.append("Prompter 脚本模式异常")
    print("  脚本化输入 OK")

    # 4) manifest 合法性（协议要求 id ^[a-z][a-z0-9_-]{1,31}$，sdk="1"）
    import re
    mid = ENGINE_MANIFEST.get("id", "")
    if not re.fullmatch(r"[a-z][a-z0-9_-]{1,31}", mid):
        problems.append(f"manifest.id 不合法：{mid!r}")
    if ENGINE_MANIFEST.get("sdk") != "1":
        problems.append("manifest.sdk 必须是字符串 '1'")
    print(f"  manifest OK (id={mid})")

    # 5) 压力引擎与 DESIGN.md §5.6 的数值一致（回归保护）
    inp = pressure_mod.PressureInput(
        now=3960.0, tau=0.55, duration_s=7200.0, n_official=8000, rank=3200,
        solved=1, t_since_ac_s=1320.0, e_total=3.2, e_seed=2600.0,
        target_percentile=0.25, recent=[(-1.0, 300.0), (-1.0, 540.0)])
    st = pressure_mod.compute(inp, pressure_mod.PressureState())
    expected_parts = {"rank": 22.7, "progress": 46.9, "stagnation": 42.3, "recent": 78.2}
    for key, want in expected_parts.items():
        got = st.components.get(key)
        if got is None or abs(got - want) > 0.5:
            problems.append(f"压力子分 {key} 期望 {want} 实际 {got}")
    if abs(st.p - 34.0) > 0.6:
        problems.append(f"压力分 P 期望 34.0 实际 {st.p:.1f}")
    print(f"  压力引擎(§5.6) OK: P={st.p:.1f} " +
          " ".join(f"{k}={v:.1f}" for k, v in st.components.items() if v is not None))

    # 6) 事件 → 感受等级 → 校准映射 → 闸门
    prof2 = profile_mod.new_profile(perceptible=20, comfort=45, device_type="v3",
                                    channel="both", preset="")
    gate = feedback_mod.Gate(prof2, "casual")
    state_l1 = pressure_mod.PressureState(p=30.0, level=1, confidence="high")
    wa_event = feedback_mod.Event("verdict.wa", 100.0, "题 B",
                                  s=feedback_mod.EVENT_S["verdict.wa"])
    dec = gate.evaluate(wa_event, state_l1, 100.0)
    if not dec.would_fire:
        problems.append(f"WA 事件应 would-fire，实际拒绝：{dec.deny}")
    elif dec.intensity != prof2.intensity_for(feedback_mod.FRAC[2]):
        problems.append(f"强度映射错误：{dec.intensity} vs {prof2.intensity_for(feedback_mod.FRAC[2])}")
    # L0 必须拒绝
    dec0 = feedback_mod.Gate(prof2, "casual").evaluate(
        feedback_mod.Event("verdict.wa", 100.0, "题 B", s=feedback_mod.EVENT_S["verdict.wa"]),
        pressure_mod.PressureState(p=10.0, level=0, confidence="high"), 100.0)
    if dec0.would_fire:
        problems.append("L0 状态下不应输出")
    print(f"  闸门 OK: L1→{dec.intensity}%/{dec.duration_s}s（would-fire={dec.would_fire}），L0→拒绝")

    # 7) 离线回放全链路（夹具）
    fixture = _HERE / "fixtures" / "cf_1847_mock.json"
    if fixture.exists():
        timeline = cf_mod.load_mock(fixture)
        result = asyncio.run(runner_mod.replay(
            timeline, prof2, runner_mod.RunOptions(rating=1400, quiet=True)))
        if len(result.events) < 8:
            problems.append(f"回放事件数异常：{len(result.events)}")
        if result.summary.get("would_fire", 0) < 1:
            problems.append("回放没有任何 would-fire（闸门可能过严）")
        print(f"  回放 OK: {len(result.events)} 事件，would-fire "
              f"{result.summary.get('would_fire')} 次，P max {result.summary.get('pressure_max')}")
    else:
        problems.append(f"缺少夹具：{fixture}")

    # 8) websockets 可用性（仅提示，不阻断 selftest）
    try:
        import websockets  # noqa: F401
        print("  websockets 已安装")
    except ImportError:
        print("  [提示] websockets 未安装：真实连接前需 pip install websockets")

    if problems:
        print(fail("自检失败：" + "; ".join(problems)))
        return 1
    print(ok("selftest 全部通过"))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ojhc", description="OJ Hardcore Engine（M1：设备链路 + 校准）")
    parser.add_argument("--version", action="version", version=f"ojhc {VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--host", default="127.0.0.1")
    common.add_argument("--port", type=int, default=None, help="DGHub API 端口（默认自动探测）")
    common.add_argument("--profile-dir", default=None, help="档案目录（默认 ~/.ojhc）")

    p_doc = sub.add_parser("doctor", parents=[common], help="环境自检")
    p_doc.add_argument("--json", action="store_true")
    p_doc.set_defaults(func=cmd_doctor)

    p_cal = sub.add_parser("calibrate", parents=[common], help="校准向导")
    p_cal.add_argument("--token", default=None, help="手动指定 token（默认自动获取）")
    p_cal.add_argument("--channel", choices=["a", "b", "both"], default="both")
    p_cal.add_argument("--preset", default="", help="波形预设名（留空则只改强度）")
    p_cal.add_argument("--dry-run", action="store_true", help="不输出电流，只走流程")
    p_cal.add_argument("--fast", action="store_true", help="跳过脉冲间 3 秒冷却（仅测试用）")
    p_cal.add_argument("--script", default=None,
                       help="脚本化答案，逗号分隔，例如 y,y,n（测试用）")
    p_cal.set_defaults(func=cmd_calibrate)

    p_run = sub.add_parser("run", parents=[common], help="会话回放/实跑（M2）")
    p_run.add_argument("--mock", nargs="?", const=str(_HERE / "fixtures" / "cf_1847_mock.json"),
                       default=None, help="离线回放夹具（不带路径则用内置样例）")
    p_run.add_argument("--contest", type=int, default=None, help="CF 比赛 id（live 模式）")
    p_run.add_argument("--handle", default=None, help="CF handle（live 模式）")
    p_run.add_argument("--rating", type=int, default=1400, help="你的赛前 rating（算 E_total）")
    p_run.add_argument("--percentile", type=float, default=0.25, help="目标百分位（0.25=前 25%%）")
    p_run.add_argument("--preset", choices=["casual", "hardcore"], default="casual")
    p_run.add_argument("--speed", type=float, default=0.0, help="回放加速倍数（0=瞬时）")
    p_run.add_argument("--json", action="store_true")
    p_run.set_defaults(func=cmd_run)

    p_status = sub.add_parser("status", parents=[common], help="查看档案与配置")
    p_status.add_argument("--json", action="store_true")
    p_status.set_defaults(func=cmd_status)

    p_self = sub.add_parser("selftest", help="纯本地自检（不联网）")
    p_self.set_defaults(func=cmd_selftest)
    return parser


def main(argv: list[str] | None = None) -> int:
    _fix_console()
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("\n已中止")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
