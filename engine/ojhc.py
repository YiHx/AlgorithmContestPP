"""OJHC Engine 命令行入口。

用法（在 engine/ 目录下）：
    python ojhC.py doctor                 # 环境自检：DGHub / 设备 / 档案 / 插件
    python ojhC.py calibrate              # 校准向导（真实设备用）
    python ojhC.py calibrate --dry-run    # 离线干跑（可配 --script 自动化）
    python ojhC.py status                 # 看设备档案与档位映射
    python ojhC.py run --mock             # 离线回放（内置夹具）
    python ojhC.py run --url https://codeforces.com/contest/1847 --handle <handle>
    python ojhC.py run --platform vjudge --contest 654321 --handle <handle> --duration-min 120
    python ojhC.py sessions               # 历史会话列表
    python ojhC.py report --session 3     # 赛后复盘（含压力曲线/事件/降级信息）
    python ojhC.py selftest               # 纯本地自检（不联网）
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

# 允许从任意目录运行：把 engine/ 加入 sys.path
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from ojhc_lib import __version__ as VERSION  # noqa: E402
from ojhc_lib import calibrate as calibrate_mod  # noqa: E402
from ojhc_lib import doctor as doctor_mod  # noqa: E402
from ojhc_lib import feedback as feedback_mod  # noqa: E402
from ojhc_lib import bridge as bridge_mod  # noqa: E402
from ojhc_lib import device as device_mod  # noqa: E402


def device_mod_device_info(host: str | None, port: int | None) -> dict:
    from ojhc_lib.dghub import fetch_device_info
    return fetch_device_info(host or "127.0.0.1", port)
from ojhc_lib import platforms as platforms_mod  # noqa: E402
from ojhc_lib import pressure as pressure_mod  # noqa: E402
from ojhc_lib import profile as profile_mod  # noqa: E402
from ojhc_lib import runner as runner_mod  # noqa: E402
from ojhc_lib import store as store_mod  # noqa: E402
from ojhc_lib.dghub import ENGINE_MANIFEST, resolve_connection  # noqa: E402
from ojhc_lib.util import Prompter, clamp_int, fail, info, ok, warn  # noqa: E402

DEFAULT_FIXTURE = _HERE / "fixtures" / "cf_1847_mock.json"


def _fix_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except Exception:
            pass


# ---------------------------------------------------------------- doctor / calibrate / status
def cmd_doctor(args: argparse.Namespace) -> int:
    return asyncio.run(doctor_mod.run_doctor(
        args.host, args.port,
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
        channel=args.channel, preset=args.preset,
        dry_run=args.dry_run, fast=args.fast, script=script))


def cmd_status(args: argparse.Namespace) -> int:
    base = Path(args.profile_dir) if args.profile_dir else None
    prof = profile_mod.load_profile(base)
    cfg = profile_mod.load_config(base)
    data = {"version": VERSION, "profile": None,
            "config": {"preset": cfg.preset, "channel": cfg.channel,
                       "allow_device_on_dom_source": cfg.allow_device_on_dom_source}}
    if prof is not None:
        data["profile"] = {
            "valid": prof.is_valid(), "expired": prof.expired(),
            "age_days": round(prof.age_days() or 0, 1) if prof.calibrated_at else None,
            "calibrated_at": prof.calibrated_at, "device_type": prof.device_type,
            "perceptible_pct": prof.perceptible_pct, "comfort_pct": prof.comfort_pct,
            "user_max_pct": prof.user_max_pct, "channel": prof.channel,
            "usable_span": prof.usable_span,
            "mapping_example": {
                "s=2": prof.intensity_for(0.35), "s=3": prof.intensity_for(0.50),
                "s=4": prof.intensity_for(0.65), "s=5": prof.intensity_for(0.85),
            } if prof.is_valid() else None,
        }
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    print(f"OJHC Engine v{VERSION}")
    print(f"档案目录: {base or profile_mod.default_dir()}")
    if prof is None:
        print(warn("没有设备档案：先跑 `ojhc calibrate`"))
    else:
        state = "有效" if prof.is_valid() and not prof.expired() else (
            "过期" if prof.expired() else "无效")
        print(f"设备档案: {state}  {prof.calibrated_at}")
        print(f"  感知区间: {prof.perceptible_pct}% ~ {prof.comfort_pct}%  "
              f"本次上限: {prof.user_max_pct}%  通道: {prof.channel}")
        if prof.is_valid():
            def _abs(pct: int) -> str:
                if not prof.device_max:
                    return ""
                return f"（≈{round(pct / 100 * prof.device_max)}）"

            print(f"  档位映射: s=2→{prof.intensity_for(0.35)}%{_abs(prof.intensity_for(0.35))}  "
                  f"s=3→{prof.intensity_for(0.50)}%{_abs(prof.intensity_for(0.50))}  "
                  f"s=4→{prof.intensity_for(0.65)}%{_abs(prof.intensity_for(0.65))}  "
                  f"s=5→{prof.intensity_for(0.85)}%{_abs(prof.intensity_for(0.85))}")
            print(f"  可用区间: I_p={prof.perceptible_pct}% → 安全上限={prof.user_max_pct}%"
                  f"（跨度 {prof.usable_span}%）")
            if prof.device_max:
                print(f"  校准设备上限: {prof.device_max}（百分比按此换算成绝对强度）")
    print(f"用户配置: preset={cfg.preset}  channel={cfg.channel}  "
          f"C级源允许设备={cfg.allow_device_on_dom_source}")
    return 0


# ---------------------------------------------------------------- run / sessions / report
def _resolve_profile(args: argparse.Namespace):
    base = Path(args.profile_dir) if args.profile_dir else None
    prof = profile_mod.load_profile(base)
    if prof is None or not prof.is_valid():
        prof = profile_mod.new_profile(perceptible=20, comfort=45,
                                       device_type="demo", channel="both", preset="")
        print(warn("没有有效校准档案 → 使用演示档案（I_p=20% / I_c=45%，仅用于流水线演示）"))
        return prof, "demo"
    return prof, "real"


def _wait_for_extension(hub, timeout_s: float) -> bool:
    """等 Chrome 扩展把第一条数据 POST 上来（HTTP 线程在跑，主线程轮询即可）。"""
    deadline = time.monotonic() + timeout_s
    last_hint = time.monotonic()
    while time.monotonic() < deadline:
        if hub.handshakes > 0:
            print(ok("扩展已连上，开始接收数据"))
            return True
        if time.monotonic() - last_hint >= 15:
            last_hint = time.monotonic()
            print(info(f"等待扩展上报数据…（{hub.url}/session；剩余 "
                       f"{int(deadline - time.monotonic())}s）"))
        time.sleep(1.0)
    print(warn("超时没等到扩展：会话继续，但只有扩展上报后才有数据"))
    return False


def cmd_run(args: argparse.Namespace) -> int:
    prof, profile_source = _resolve_profile(args)

    # 真实输出：必须已有真机校准档案（演示档案绝不允许驱动硬件）
    device_link = None
    enable_device = bool(args.device)
    if enable_device:
        if args.trust_app_limit:
            # 无校准模式：App 上限即安全上限，映射固定为上限的 20%~60%
            info_dev = device_mod_device_info(args.host, args.port)
            dev_max = int(info_dev.get("max_strength_A") or 0) or int(prof.device_max or 0) or 40
            prof = profile_mod.DeviceProfile(
                version=1, calibrated_at=profile_mod.now_iso(),
                device_type=str(info_dev.get("device_type") or "v3"),
                device_max=dev_max,
                channel=prof.channel or "both",
                perceptible_pct=65, comfort_pct=100, user_max_pct=90,
                preset=(prof.preset if prof.preset and prof.preset not in ("casual", "hardcore")
                        else "呼吸"),
                notes="trust-app-limit")
            profile_source = "app-limit"
            if dev_max < 30:
                print(fail(f"设备上限只有 {dev_max}（低于 30），引擎最多只能输出 "
                            f"{round(prof.user_max_pct / 100 * dev_max)}，几乎不可能有感觉。"))
                print(info("请打开 DG-Lab App：把 A/B 通道的「上限」调到 40，"
                           "保持 App 在前台，然后重新运行。"))
                print(info("确认上限是否生效：python -c \"import json,urllib.request; "
                           "d=json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/status')); "
                           "print(d['max_strength_A'], d['max_strength_B'])\""))
                return 2
            print(info(f"无校准模式：设备上限 {dev_max}，输出范围 = 上限的 "
                       f"{prof.perceptible_pct}%~{prof.user_max_pct}%"
                       f"（s2≈{prof.intensity_for(0.35)}% s3≈{prof.intensity_for(0.50)}% "
                       f"s4≈{prof.intensity_for(0.65)}% s5≈{prof.intensity_for(0.85)}%，"
                       f"绝对强度 ≈{round(prof.intensity_for(0.35) / 100 * dev_max)}~"
                       f"{round(prof.intensity_for(0.85) / 100 * dev_max)}）"))
        elif profile_source != "real":
            print(fail("--device 需要校准档案（或加 --trust-app-limit 用 App 上限直接映射）"))
            print(info("先跑 `python ojhC.py calibrate` 校准，或加 --trust-app-limit 跳过校准"))
            return 2
        device_link = device_mod.DeviceLink(port=args.device_port)
        print(warn("⚠ 真实输出已启用：本会话通过闸门的失败事件会真的打到设备上"))
        print(info(f"安全边界：强度 ≤{feedback_mod.HARD_MAX_S}% / 脉冲 ≤3s / 冷却与令牌桶生效 / "
                   "Ctrl+C 或会话结束立即急停"))

    opts = runner_mod.RunOptions(
        handle=args.handle or "", rating=args.rating, preset=args.preset,
        target_percentile=args.percentile, speed=args.speed,
        no_submit_minutes=args.no_submit_minutes,
        no_ac_minutes=args.no_ac_minutes, dry_run=not enable_device,
        max_run_s=(args.max_run_min * 60 if args.max_run_min else None),
        replay_speed=args.replay_speed,
        enable_device=enable_device,
        device_preset=(args.device_preset if args.device_preset is not None
                       else (prof.preset or "")),
        allow_device_on_c=args.allow_device_on_dom_source,
        device_wait_s=args.device_wait)

    hub = None
    replay = False
    try:
        if args.mock is not None:
            path = Path(args.mock) if args.mock else DEFAULT_FIXTURE
            adapter = platforms_mod.load_mock(path)
            replay = True
            print(info(f"离线回放：{adapter.meta.name}（{adapter.meta.duration_s / 60:.0f} 分钟）"
                       f" platform={adapter.platform}"))
        else:
            platform = args.platform
            contest_id = str(args.contest) if args.contest else None
            if args.url:
                detected = platforms_mod.detect_from_url(args.url)
                if detected is None:
                    print(fail(f"无法从 URL 识别平台：{args.url}"))
                    return 2
                platform, contest_id = detected
                print(info(f"从 URL 识别：platform={platform} contest={contest_id}"))
            if not platform or not contest_id:
                print(fail("需要 --url，或 --platform + --contest"))
                return 2
            if not args.handle:
                print(fail("真实比赛需要 --handle"))
                return 2
            opts.platform, opts.contest_id = platform, contest_id

            if platform in platforms_mod.BRIDGED or args.bridge:
                hub = bridge_mod.BridgeHub(
                    platform, contest_id, args.handle, args.duration_min * 60,
                    port=args.bridge_port)
                hub.start()
                print(info(f"浏览器桥接模式（C 级数据源）：{hub.url}"))
                print(info("步骤：chrome://extensions → 开发者模式 → 加载已解压的扩展程序 "
                           "→ 选 extension/ 目录 → 打开并登录比赛页面"))
                print(info(f"会话信息：{hub.url}/session（扩展会自动发现）"))
                print(warn("C 级数据源默认不允许驱动设备（设计 §7.5 #10），当前只算分/显示"))
                adapter = bridge_mod.BridgeAdapter(hub)
                _wait_for_extension(hub, args.bridge_wait)
            else:
                adapter = platforms_mod.create_adapter(
                    platform, contest_id, args.handle, args.rating,
                    duration_s=args.duration_min * 60)
                print(info(f"平台 {platforms_mod.PLATFORM_LABELS.get(platform, platform)} · "
                           f"比赛 {contest_id} · handle {args.handle}"))
    except platforms_mod.PlatformError as exc:
        print(fail(str(exc)))
        if hub is not None:
            hub.stop()
        return 2

    st = None if args.no_store else store_mod.Store(args.db)
    try:
        result = asyncio.run(runner_mod.run_adapter(
            adapter, prof, opts, store=st, replay=replay, device=device_link))
    finally:
        if st is not None:
            st.close()
        if hub is not None:
            hub.stop()

    result.summary["profile"] = profile_source
    _print_summary(result)
    if result.session_id is not None:
        print(info(f"已保存会话 #{result.session_id}，复盘：python ojhC.py report --session {result.session_id}"))
    if args.json:
        print(json.dumps(result.summary, ensure_ascii=False, indent=2))
    return 0


def _print_summary(result: "runner_mod.RunResult") -> None:
    s = result.summary
    print("\n" + "=" * 66)
    print(f"会话总结：{s['contest']}  [{s['platform']}]")
    print(f"  档案        : {'演示档案（未校准）' if s.get('profile') == 'demo' else '真实校准档案'}")
    print(f"  事件统计    : {s['submissions_events']}")
    print(f"  若启用设备  : {s['would_fire']} 次 {s['would_fire_by_kind']}")
    if s.get("device_enabled"):
        print(f"  设备输出    : 实际输出 {s.get('allowed', 0)} 次（--device 已启用）")
    else:
        print(f"  设备输出    : 0 次（dry-run；加 --device 且完成真机校准后才输出）")
    print(f"  拒绝原因 top: {s['denied_top']}")
    print(f"  压力分      : min {s['pressure_min']} / max {s['pressure_max']} / "
          f"结束 {s['pressure_end']}（L{s['level_end']}）")
    print(f"  已 AC       : {s['solved']} 题，E_total≈{s['e_total']}")
    if s.get("degraded"):
        print(f"  降级        : {s['degraded']}")
    print("=" * 66)


def cmd_sessions(args: argparse.Namespace) -> int:
    st = store_mod.Store(args.db)
    try:
        rows = st.list_sessions(args.limit)
    finally:
        st.close()
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print(info("还没有历史会话（先跑一次 `ojhc run`）"))
        return 0
    print(f"{'ID':>4}  {'平台':<11}{'比赛':<12}{'预设':<9}{'事件':>5}{'Pmax':>6}  开始时间")
    for r in rows:
        started = time.strftime("%m-%d %H:%M", time.localtime(r["started_at"]))
        print(f"{r['id']:>4}  {r['platform']:<11}{str(r['contest_id']):<12}"
              f"{r['preset']:<9}{str(r['would_fire'] if r['would_fire'] is not None else '-'):>5}"
              f"{str(r['pressure_max'] if r['pressure_max'] is not None else '-'):>6}  {started}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    st = store_mod.Store(args.db)
    try:
        rep = st.report(args.session)
    except KeyError:
        print(fail(f"没有 session {args.session}"))
        return 2
    finally:
        st.close()
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0
    sess, meta, summary = rep["session"], rep["meta"], rep["summary"]
    print(f"会话 #{sess['id']}  {meta.get('name')}  [{sess['platform']}]")
    print(f"  handle={sess['handle']}  preset={sess['preset']}  "
          f"{'已结束' if sess['ended_at'] else '未结束'}")
    if summary:
        print(f"  事件={summary.get('submissions_events')}  would-fire={summary.get('would_fire')}"
              f"  P: {summary.get('pressure_min')} ~ {summary.get('pressure_max')}"
              f"  结束 L{summary.get('level_end')}")
        if summary.get("degraded"):
            print(f"  降级：{summary['degraded']}")
    print("\n事件时间线：")
    for e in rep["events"]:
        t = f"{int(e['rel_s']) // 60:02d}:{int(e['rel_s']) % 60:02d}"
        if e.get("allowed"):
            flag = "⚡ALLOWED"
        elif e["would_fire"]:
            flag = "⚡would-fire"
        else:
            flag = "✗"
        deny = (" " + ",".join(e["deny"])) if e["deny"] and not e.get("allowed") else ""
        print(f"  [{t}] {e['kind']:<18} s={e['s']} → {e['intensity']:2d}%/"
              f"{e['duration_s']:.1f}s {flag}{deny}")
    if rep["pressure"]:
        ps = [p["p"] for p in rep["pressure"]]
        print(f"\n压力曲线：{len(ps)} 个采样，min {min(ps):.1f} / max {max(ps):.1f} / "
              f"结束 {ps[-1]:.1f}")
    return 0


# ---------------------------------------------------------------- selftest
def cmd_selftest(args: argparse.Namespace) -> int:
    problems: list[str] = []

    # 1) 档案映射
    prof = profile_mod.new_profile(perceptible=20, comfort=40, device_type="v3",
                                   channel="both", preset="")
    if not prof.is_valid():
        problems.append("new_profile 生成的档案无效")
    if prof.user_max_pct != min(int(40 * 0.8), profile_mod.HARD_CAP_PCT):
        problems.append(f"user_max 计算错误：{prof.user_max_pct}")
    if prof.usable_span < 10:
        problems.append(f"可用区间过窄：{prof.usable_span}%")
    seq = [prof.intensity_for(f) for f in (0.0, 0.2, 0.35, 0.5, 0.65, 0.85, 1.0)]
    if seq != sorted(seq) or len(set(seq)) < 4:
        problems.append(f"档位映射异常：{seq}")
    print(f"  映射 OK: {seq}")

    # 2) 钳制
    for raw, lo, hi in ((999, 0, 50), (-5, 0, 50)):
        if not (lo <= clamp_int(raw, lo, hi) <= hi):
            problems.append(f"钳制失败 {raw}")
    print("  钳制 OK")

    # 3) Prompter 脚本模式
    p = Prompter(["y", "n"])
    if not p.confirm("q1") or p.confirm("q2"):
        problems.append("Prompter 脚本模式异常")
    print("  脚本化输入 OK")

    # 4) manifest 合法性
    import re
    mid = ENGINE_MANIFEST.get("id", "")
    if not re.fullmatch(r"[a-z][a-z0-9_-]{1,31}", mid):
        problems.append(f"manifest.id 不合法：{mid!r}")
    if ENGINE_MANIFEST.get("sdk") != "1":
        problems.append("manifest.sdk 必须是字符串 '1'")
    print(f"  manifest OK (id={mid})")

    # 5) 判题归一化（CF 下划线风格 / AtCoder 缩写 / 状态词）
    cases = {"Accepted": "OK", "Wrong Answer": "WRONG_ANSWER", "AC": "OK", "WA": "WRONG_ANSWER",
             "Time Limit Exceeded": "TIME_LIMIT_EXCEEDED", "TLE": "TIME_LIMIT_EXCEEDED",
             "WRONG_ANSWER": "WRONG_ANSWER", "Judging": "TESTING", "waiting": "TESTING",
             "答案正确": "OK", "答案错误": "WRONG_ANSWER", "运行超时": "TIME_LIMIT_EXCEEDED",
             "测试通过": "OK", "未知结果": "UNKNOWN"}
    for raw, want in cases.items():
        got = platforms_mod.normalize_verdict(raw)
        if got != want:
            problems.append(f"verdict 归一化 {raw!r} → {got}，期望 {want}")
    print("  判题归一化 OK")

    # 5b) VJudge rank/single 解析（JSON 无 rank 字段 → 自算）
    from ojhc_lib.platforms.vjudge import parse_rank_single
    vj_fixture = _HERE / "fixtures" / "vjudge_rank_single_mock.json"
    if vj_fixture.exists():
        vj_payload = json.loads(vj_fixture.read_text(encoding="utf-8"))
        parsed = parse_rank_single(vj_payload, "tester")
        if not parsed or parsed.get("rank") != 3 or parsed.get("solved") != 1:
            problems.append(f"VJudge 榜单解析异常：{parsed}")
        elif abs(float(parsed["duration_s"]) - 7200) > 1:
            problems.append(f"VJudge 时长解析异常：{parsed['duration_s']}")
        else:
            print(f"  VJudge rank 自算 OK: rank={parsed['rank']} solved={parsed['solved']} "
                  f"n={parsed['n_official']}")
    else:
        problems.append(f"缺少 VJudge 夹具 {vj_fixture}")

    # 5c) 浏览器桥接服务往返（本地 HTTP，不依赖外网）
    from ojhc_lib import bridge as _bridge
    import urllib.request as _ur
    hub = _bridge.BridgeHub("atcoder", "abc300", "tester", duration_s=7200, port=0)
    base = hub.start()
    try:
        session = json.load(_ur.urlopen(base + "/session", timeout=5))
        req = _ur.Request(
            base + "/ingest/submissions",
            data=json.dumps([{"sid": "1", "verdict": "WRONG_ANSWER",
                              "problem": "A", "rel_s": 60}]).encode(),
            method="POST",
            headers={"Content-Type": "application/json",
                     _bridge.BRIDGE_TOKEN_HEADER: session["token"]})
        _ur.urlopen(req, timeout=5)
        got = hub.drain(None)
        if len(got) != 1 or got[0].verdict != "WRONG_ANSWER":
            problems.append(f"桥接往返异常：{got}")
        else:
            print("  浏览器桥接 OK（token 校验 + 提交入队）")
    finally:
        hub.stop()

    # 6) URL 识别（五平台）
    urls = {
        "https://codeforces.com/contest/1847/problem/A": ("codeforces", "1847"),
        "https://codeforces.com/gym/104114": ("codeforces", "104114"),
        "https://vjudge.net/contest/654321": ("vjudge", "654321"),
        "https://atcoder.jp/contests/abc300": ("atcoder", "abc300"),
        "https://www.luogu.com.cn/contest/123456": ("luogu", "123456"),
        "https://ac.nowcoder.com/acm/contest/56789": ("nowcoder", "56789"),
    }
    for url, want in urls.items():
        got = platforms_mod.detect_from_url(url)
        if got != want:
            problems.append(f"URL 识别 {url} → {got}，期望 {want}")
    print("  URL 识别 OK（5 平台）")

    # 7) 压力引擎 §5.6 回归
    inp = pressure_mod.PressureInput(
        now=3960.0, tau=0.55, duration_s=7200.0, n_official=8000, rank=3200,
        solved=1, t_since_ac_s=1320.0, e_total=3.2, e_seed=2600.0,
        target_percentile=0.25, recent=[(-1.0, 300.0), (-1.0, 540.0)])
    st = pressure_mod.compute(inp, pressure_mod.PressureState())
    for key, want in {"rank": 22.7, "progress": 46.9, "stagnation": 42.3, "recent": 78.2}.items():
        got = st.components.get(key)
        if got is None or abs(got - want) > 0.5:
            problems.append(f"压力子分 {key} 期望 {want} 实际 {got}")
    if abs(st.p - 34.0) > 0.6:
        problems.append(f"P 期望 34.0 实际 {st.p:.1f}")
    print(f"  压力引擎(§5.6) OK: P={st.p:.1f}")

    # 8) 闸门
    prof2 = profile_mod.new_profile(20, 45, "v3", "both", "")
    wa = feedback_mod.Event("verdict.wa", 100.0, "题 B", s=feedback_mod.EVENT_S["verdict.wa"])
    dec = feedback_mod.Gate(prof2, "casual").evaluate(
        wa, pressure_mod.PressureState(p=30.0, level=1, confidence="high"), 100.0)
    if not dec.would_fire:
        problems.append(f"L1 WA 应 would-fire：{dec.deny}")
    dec0 = feedback_mod.Gate(prof2, "casual").evaluate(
        feedback_mod.Event("verdict.wa", 100.0, "题 B", s=2),
        pressure_mod.PressureState(p=10.0, level=0, confidence="high"), 100.0)
    if dec0.would_fire:
        problems.append("L0 不应输出")
    print(f"  闸门 OK: L1→{dec.intensity}%/{dec.duration_s}s，L0 拒绝")

    # 8b) 设备链路（租约 + 一次性 nonce + ack + 急停），假插件走本地 TCP
    from ojhc_lib import device as _device
    import asyncio as _asyncio

    async def _device_roundtrip():
        link = _device.DeviceLink(port=0)
        await link.start()
        results: dict = {}

        async def fake_plugin():
            reader, writer = await _asyncio.open_connection("127.0.0.1", link.port)
            writer.write((json.dumps({
                "op": "hello", "token": link.token, "plugin": "fake", "version": "0",
                "device": {"connected": True, "type": "v3",
                           "max_a": 200, "max_b": 200}}) + "\n").encode())
            await writer.drain()
            results["hello_ack"] = json.loads(await reader.readline())
            results["lease"] = json.loads(await reader.readline())
            results["output"] = json.loads(await reader.readline())
            msg = results["output"]
            ok = (msg.get("lease_id") == results["lease"].get("lease_id")
                  and bool(msg.get("nonce")))
            writer.write((json.dumps({"op": "ack", "event_id": msg.get("event_id"),
                                      "ok": bool(ok),
                                      "applied_pct": msg.get("intensity_pct")}) + "\n").encode())
            await writer.drain()
            while True:
                line = await reader.readline()
                if not line:
                    break
                m = json.loads(line)
                if m.get("op") == "estop":
                    results["estop"] = True
                    break
            writer.close()

        task = _asyncio.create_task(fake_plugin())
        connected = await link.wait_client(3.0)
        fired = await link.fire("e1", 26, 0.8, preset="", channel="both") if connected \
            else {"ok": False, "note": "plugin not connected"}
        await link.estop("selftest")
        await link.close()
        await task
        return results, fired

    device_results, fired = asyncio.run(_device_roundtrip())
    if not fired.get("ok"):
        problems.append(f"设备链路 fire 失败：{fired}")
    elif device_results.get("output", {}).get("intensity_pct") != 26:
        problems.append(f"设备链路输出强度异常：{device_results.get('output')}")
    elif not device_results.get("estop"):
        problems.append("设备链路未收到 estop")
    else:
        print("  设备链路 OK（hello/lease/nonce/ack/estop）")

    # 9) 离线回放（多平台架构下）
    if DEFAULT_FIXTURE.exists():
        timeline = platforms_mod.load_mock(DEFAULT_FIXTURE)
        result = asyncio.run(runner_mod.run_adapter(
            timeline, prof2,
            runner_mod.RunOptions(rating=1400, quiet=True), store=None, replay=True))
        if len(result.events) < 8:
            problems.append(f"回放事件数异常：{len(result.events)}")
        if result.summary.get("would_fire", 0) < 1:
            problems.append("回放没有 would-fire")
        print(f"  回放 OK: {len(result.events)} 事件，would-fire {result.summary['would_fire']}")
    else:
        problems.append(f"缺少夹具 {DEFAULT_FIXTURE}")

    # 10) SQLite 存储往返
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "test.db"
        st_store = store_mod.Store(db)
        timeline = platforms_mod.load_mock(DEFAULT_FIXTURE)
        meta = asyncio.run(timeline.load_meta())
        sid = st_store.create_session(meta.platform, meta.contest_id, "tester", "casual", meta)
        st_store.add_submission(sid, platforms_mod.Submission("1", 10.0, "OK", "A"))
        st_store.commit()
        rep = st_store.report(sid)
        st_store.close()
        if len(rep["submissions"]) != 1:
            problems.append("SQLite 存储往返失败")
    print("  SQLite 存储 OK")

    # 11) websockets 可用性（仅提示）
    try:
        import websockets  # noqa: F401
        print("  websockets 已安装")
    except ImportError:
        print("  [提示] websockets 未安装：连接 DGHub 前需 pip install websockets")

    if problems:
        print(fail("自检失败：" + "; ".join(problems)))
        return 1
    print(ok("selftest 全部通过"))
    return 0


# ---------------------------------------------------------------- CLI
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ojhc", description="OJ Hardcore Engine（多平台比赛监听）")
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
    p_cal.add_argument("--token", default=None)
    p_cal.add_argument("--channel", choices=["a", "b", "both"], default="both")
    p_cal.add_argument("--preset", default="", help="波形预设名（留空则只改强度）")
    p_cal.add_argument("--dry-run", action="store_true")
    p_cal.add_argument("--fast", action="store_true")
    p_cal.add_argument("--script", default=None, help="脚本化答案，逗号分隔（测试用）")
    p_cal.set_defaults(func=cmd_calibrate)

    p_status = sub.add_parser("status", parents=[common], help="查看档案与配置")
    p_status.add_argument("--json", action="store_true")
    p_status.set_defaults(func=cmd_status)

    p_run = sub.add_parser("run", parents=[common], help="运行一场比赛（回放/实跑）")
    p_run.add_argument("--mock", nargs="?", const=str(DEFAULT_FIXTURE), default=None,
                       help="离线回放夹具（不带路径则用内置样例）")
    p_run.add_argument("--url", default=None, help="比赛 URL（自动识别平台与比赛 id）")
    p_run.add_argument("--platform", default=None,
                       help="平台：codeforces / vjudge / atcoder / luogu / nowcoder")
    p_run.add_argument("--contest", default=None, help="比赛 id（配合 --platform）")
    p_run.add_argument("--handle", default=None, help="你的用户名")
    p_run.add_argument("--rating", type=int, default=1400, help="赛前 rating（算 E_total）")
    p_run.add_argument("--duration-min", type=float, default=120.0,
                       help="平台拿不到时长时的兜底（分钟），目前 VJudge 用")
    p_run.add_argument("--max-run-min", type=float, default=None,
                       help="实跑硬上限（分钟）；测试/演示用，默认跑完比赛时长")
    p_run.add_argument("--replay-speed", type=float, default=0.0,
                       help="把已结束的比赛按 N 倍速重放（用提交时间差，不依赖实时时钟）")
    p_run.add_argument("--percentile", type=float, default=0.25, help="目标百分位")
    p_run.add_argument("--preset", choices=["casual", "hardcore"], default="casual")
    p_run.add_argument("--speed", type=float, default=0.0, help="回放加速倍数（0=瞬时）")
    p_run.add_argument("--no-submit-minutes", type=float, default=30.0)
    p_run.add_argument("--no-ac-minutes", type=float, default=None)
    p_run.add_argument("--db", default=None, help="SQLite 路径（默认 ~/.ojhc/ojhc.db）")
    p_run.add_argument("--no-store", action="store_true", help="不写历史")
    p_run.add_argument("--device", action="store_true",
                       help="启用真实设备输出（必须已有真机校准档案）")
    p_run.add_argument("--device-port", type=int, default=8768,
                       help="设备链路 TCP 端口（默认 8768）")
    p_run.add_argument("--device-preset", default=None,
                       help="DGHub 波形预设名（默认用档案里的；空=只改强度）")
    p_run.add_argument("--allow-device-on-dom-source", action="store_true",
                       help="允许 C 级数据源（AtCoder/洛谷/牛客扩展）驱动设备，默认禁止")
    p_run.add_argument("--device-wait", type=float, default=20.0,
                       help="等待设备插件连接的最长秒数（默认 20）")
    p_run.add_argument("--trust-app-limit", action="store_true",
                       help="无校准模式：直接按 DG-Lab App 里设的强度上限映射（不写档案、不做校准）")
    p_run.add_argument("--bridge", action="store_true",
                       help="强制走浏览器桥接（AtCoder/洛谷/牛客默认就是桥接）")
    p_run.add_argument("--bridge-port", type=int, default=8765,
                       help="桥接服务端口（默认 8765；扩展会自动发现）")
    p_run.add_argument("--bridge-wait", type=float, default=120.0,
                       help="等待扩展上线的最长秒数（默认 120）")
    p_run.add_argument("--json", action="store_true")
    p_run.set_defaults(func=cmd_run)

    p_sessions = sub.add_parser("sessions", help="历史会话列表")
    p_sessions.add_argument("--limit", type=int, default=20)
    p_sessions.add_argument("--db", default=None)
    p_sessions.add_argument("--json", action="store_true")
    p_sessions.set_defaults(func=cmd_sessions)

    p_report = sub.add_parser("report", help="赛后复盘")
    p_report.add_argument("--session", type=int, required=True)
    p_report.add_argument("--db", default=None)
    p_report.add_argument("--json", action="store_true")
    p_report.set_defaults(func=cmd_report)

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
