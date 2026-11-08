"""会话运行器：数据源 → 压力引擎 → 离散事件 → 闸门 → 报告（DESIGN.md §2/§3/§7）。

引擎只依赖 platforms.base 的四个模型，不认识任何具体 OJ：
    adapter.load_meta() / adapter.poll(cursor) / adapter.standings(elapsed)

能力缺失时自动降级并记录在 summary["degraded"] 里（例如 VJudge 没有榜单接口）。
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from .feedback import EVENT_S, Decision, Event, Gate
from .platforms.base import (
    ContestMeta, NotSupported, PlatformAdapter, Standings, Submission,
    expected_solves,
)
from .pressure import (
    PressureConfig, PressureInput, PressureState, compute, verdict_score,
)
from . import profile as profile_mod
from .profile import DeviceProfile
from .store import Store
from .util import clamp

VERDICT_KIND = {
    "OK": "verdict.ac",
    "WRONG_ANSWER": "verdict.wa",
    "TIME_LIMIT_EXCEEDED": "verdict.tle",
    "MEMORY_LIMIT_EXCEEDED": "verdict.mle",
    "RUNTIME_ERROR": "verdict.re",
    "COMPILATION_ERROR": "verdict.ce",
    "PARTIAL": "verdict.partial",
}
KIND_LABEL = {
    "verdict.ac": "AC", "verdict.wa": "WA", "verdict.tle": "TLE",
    "verdict.mle": "MLE", "verdict.re": "RE", "verdict.ce": "CE",
    "verdict.partial": "PARTIAL",
    "timeout_no_submit": "超时未提交", "timeout_no_ac": "超时未 AC",
    "level_up": "等级上升",
}


def fmt_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


@dataclass
class RunOptions:
    handle: str = ""
    platform: str = ""
    contest_id: str = ""
    target_percentile: float = 0.25
    rating: int | None = None
    preset: str = "casual"
    no_submit_minutes: float = 30.0
    no_ac_minutes: float | None = None
    max_run_s: float | None = None   # 实跑硬上限（测试/演示用；None=跑完比赛时长）
    speed: float = 0.0            # 0 = 立即回放；>0 = 每模拟秒等待 1/speed 秒
    replay_speed: float = 0.0     # >0：把已结束比赛按 N 倍速重放（忽略实时时钟）
    dry_run: bool = True
    quiet: bool = False
    mode: str = "simple"              # simple=没 AC 才电；pressure=压力算法
    enable_device: bool = False       # --device：本会话允许真实输出
    device_preset: str = ""           # DGHub 波形预设名（空=只改强度）
    allow_device_on_c: bool = False   # C 级数据源是否允许驱动设备（默认否）
    device_wait_s: float = 20.0       # 等待插件连接的时间


@dataclass
class RunResult:
    events: list[Event] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)
    curve: list[tuple[float, float, int]] = field(default_factory=list)  # (elapsed, P, level)
    summary: dict = field(default_factory=dict)
    session_id: int | None = None


class SessionRunner:
    def __init__(self, meta: ContestMeta, profile: DeviceProfile | None,
                 options: RunOptions, pressure_config: PressureConfig | None = None,
                 store: Store | None = None, session_id: int | None = None,
                 device=None, source_level: str = "A") -> None:
        self.meta = meta
        self.profile = profile
        self.options = options
        self.device = device
        self.source_level = source_level
        self.cfg = pressure_config or PressureConfig()
        self.gate = Gate(profile, options.preset, options.dry_run, mode=options.mode)
        self.e_total = expected_solves(meta.problems, options.rating)
        self.no_ac_s = (options.no_ac_minutes * 60 if options.no_ac_minutes is not None
                        else min(3600.0, meta.duration_s / 2))
        self.store = store
        self.session_id = session_id
        self.state = PressureState()
        self.solved: set[str] = set()
        self.last_sub_s = 0.0
        self.last_ac_s = 0.0
        self.streak_fail = 0
        self.recent: list[tuple[float, float]] = []   # (score, rel_s)
        self.no_submit_armed = True
        self.no_ac_armed = True
        self._no_ac_shocks = 0
        self.result = RunResult(session_id=session_id)
        self.degraded: list[str] = []
        self._last_snapshot_rel: float | None = None

    # -------------------------------------------------- 核心：一次提交
    async def on_submission(self, sub: Submission,
                            standings: Standings) -> list[tuple[Event, Decision]]:
        out: list[tuple[Event, Decision]] = []
        kind = VERDICT_KIND.get(sub.verdict)
        if kind is None:            # TESTING / UNKNOWN：不产生事件（宁可漏报不误报）
            return out

        elapsed = sub.rel_s
        self.last_sub_s = elapsed
        self.no_submit_armed = True

        score = verdict_score(sub.verdict, (sub.points / 100.0) if sub.points else None)
        if score is not None:
            self.recent.append((score, elapsed))
        self.recent = [(s, t) for s, t in self.recent if elapsed - t <= self.cfg.recent_max_age_s]

        if kind == "verdict.ac":
            self.solved.add(sub.problem)
            self.last_ac_s = elapsed
            self.streak_fail = 0
            self.no_ac_armed = True
            self.gate.note_ac()
            self._no_ac_shocks = 0
        elif kind in ("verdict.tle", "verdict.mle", "verdict.re", "verdict.wa",
                      "verdict.partial"):
            self.streak_fail += 1

        prev_level = self.state.level
        self.state = self._pressure(elapsed, standings)
        if self.state.level > prev_level:
            await self._emit(Event("level_up", elapsed, f"L{self.state.level}"), out)

        event = Event(kind=kind, rel_s=elapsed,
                      detail=f"题 {sub.problem}" + (" AC" if kind == "verdict.ac" else f" {sub.verdict}"))
        await self._emit(event, out)
        return out

    async def _emit(self, event: Event, out: list[tuple[Event, Decision]]) -> None:
        event.s = EVENT_S.get(event.kind, 0)
        plugin_tuning = getattr(self.device, "tuning", None) if self.device else None
        if (event.kind == "level_up" and plugin_tuning
                and bool(plugin_tuning.get("allow_level_up"))):
            event.s = 2      # 开启“等级上升也电”时给一个轻档脉冲
        self.gate.set_tuning(plugin_tuning)
        decision = self.gate.evaluate(event, self.state, event.rel_s)
        if decision.would_fire and plugin_tuning:
            base = int(plugin_tuning.get("base_intensity", 74))
            step = int(plugin_tuning.get("intensity_step", 4))
            tuned = base + (max(0, decision.s_eff) - 2) * step
            if event.kind == "timeout_no_ac":
                # 没 AC 的电击逐次加强，封顶在 90%（App 上限的 90%）
                esc = int(plugin_tuning.get("no_ac_escalation", 5))
                tuned += min(self._no_ac_shocks, 20) * esc
            cap = min(profile_mod.HARD_CAP_PCT, int(plugin_tuning.get("hard_cap", 90)))
            decision.intensity = int(max(0, min(round(tuned), cap)))
            if decision.intensity <= 0:
                decision.would_fire = False
                decision.deny = ["插件调参后强度为 0"]
            elif event.kind == "timeout_no_ac":
                self._no_ac_shocks += 1

        # 真实输出：闸门 would_fire + 本会话显式启用 + 设备链路可用
        if decision.would_fire and self.options.enable_device:
            if self.source_level == "C" and not self.options.allow_device_on_c:
                decision.would_fire = False
                decision.deny = ["C 级数据源默认禁设备（需 allow_device_on_dom_source）"]
            elif self.device is None or not self.device.connected:
                decision.would_fire = False
                decision.deny = ["设备插件未连接"]
            else:
                channel = (self.profile.channel if self.profile else "") or "both"
                event_id = f"{event.kind}-{event.rel_s:.1f}-{len(self.result.events)}"
                ack = await self.device.fire(
                    event_id=event_id, intensity_pct=decision.intensity,
                    duration_s=decision.duration_s,
                    preset=((plugin_tuning.get("preset") if plugin_tuning else "") 
                            or self.options.device_preset),
                    channel=channel, label=f"OJHC {event.kind}")
                if ack.get("ok"):
                    decision.allowed = True
                    decision.deny = []
                    applied = ack.get("applied_pct")
                    if applied is not None and abs(int(applied) - decision.intensity) > 2:
                        print(f"[device] 注意：插件把强度从 {decision.intensity}% 钳制到 {applied}%")
                    if ack.get("note"):
                        print(f"[device] 插件提示：{ack.get('note')}")
                else:
                    decision.would_fire = False
                    decision.deny = [f"设备拒绝：{ack.get('note')}"]

        out.append((event, decision))
        self.result.events.append(event)
        self.result.decisions.append(decision)
        if self.store and self.session_id is not None:
            self.store.add_event(self.session_id, event.rel_s, event.kind, event.s,
                                 decision.intensity, decision.duration_s,
                                 decision.would_fire, list(decision.deny),
                                 allowed=decision.allowed)

    def _pressure(self, elapsed: float, standings: Standings) -> PressureState:
        frozen = standings.frozen or (
            self.meta.frozen_from_s is not None and elapsed >= self.meta.frozen_from_s)
        recent = sorted(((s, elapsed - t) for s, t in self.recent), key=lambda x: x[1])
        inp = PressureInput(
            now=elapsed, tau=clamp(elapsed / max(self.meta.duration_s, 1), 0, 1),
            duration_s=self.meta.duration_s,
            n_official=standings.n_official, rank=standings.rank,
            solved=len(self.solved), t_since_ac_s=max(0.0, elapsed - self.last_ac_s),
            e_total=self.e_total, e_seed=None,
            target_percentile=self.options.target_percentile,
            recent=recent, streak_fail=self.streak_fail, frozen=frozen)
        self.state = compute(inp, self.state, self.cfg)
        self.result.curve.append((elapsed, self.state.p, self.state.level))
        if self.store and self.session_id is not None:
            self.store.add_pressure(self.session_id, elapsed, self.state.p,
                                    self.state.level, self.state.confidence)
        return self.state

    # -------------------------------------------------- 模式与时间型事件
    def _plugin_tuning(self) -> dict | None:
        tuning = getattr(self.device, "tuning", None) if self.device else None
        return tuning if tuning else None

    def _mode(self) -> str:
        tuning = self._plugin_tuning()
        if tuning and tuning.get("mode") in ("simple", "pressure"):
            return tuning["mode"]
        return self.options.mode

    def _no_ac_interval_s(self) -> float:
        tuning = self._plugin_tuning()
        if tuning and tuning.get("no_ac_minutes"):
            return float(tuning["no_ac_minutes"]) * 60
        return self.no_ac_s

    def next_timeout_s(self) -> float | None:
        """下一个"时间型事件"应该发生的比赛内时刻（秒）；没有则 None。

        简单模式 = 距上次 AC / 上次"没 AC 电击"满间隔；压力模式 = 超时未提交 / 未 AC。
        回放按这个时刻推进虚拟时钟，保证"到点就电"，而不是等下一次提交。
        """
        if self._mode() == "simple":
            interval = self._no_ac_interval_s()
            last_shock = getattr(self, "_last_no_ac_shock", -1e9)
            due_ac = self.last_ac_s + interval
            if last_shock > -1e8:
                return max(due_ac, last_shock + interval)
            return due_ac
        candidates: list[float] = []
        if self.no_submit_armed:
            candidates.append(self.last_sub_s + self.options.no_submit_minutes * 60)
        if self.no_ac_armed:
            candidates.append(self.last_ac_s + self.no_ac_s)
        return min(candidates) if candidates else None

    async def check_timeouts(self, elapsed: float) -> list[tuple[Event, Decision]]:
        out: list[tuple[Event, Decision]] = []
        if self._mode() == "simple":
            # 简单模式：只要「距上次 AC 超过设定时间」就电一次，之后按同样间隔重复
            interval = self._no_ac_interval_s()
            last_shock = getattr(self, "_last_no_ac_shock", -1e9)
            if elapsed - self.last_ac_s >= interval and elapsed - last_shock >= interval:
                self._last_no_ac_shock = elapsed
                await self._emit(Event("timeout_no_ac", elapsed,
                                       f"距上次 AC {fmt_time(elapsed - self.last_ac_s)}"), out)
            return out
        if self.no_submit_armed and elapsed - self.last_sub_s >= self.options.no_submit_minutes * 60:
            self.no_submit_armed = False
            await self._emit(Event("timeout_no_submit", elapsed,
                                   f"距上次提交 {fmt_time(elapsed - self.last_sub_s)}"), out)
        if self.no_ac_armed and elapsed - self.last_ac_s >= self.no_ac_s:
            self.no_ac_armed = False
            await self._emit(Event("timeout_no_ac", elapsed,
                                   f"距上次 AC {fmt_time(elapsed - self.last_ac_s)}"), out)
        return out

    # -------------------------------------------------- 榜单
    async def _standings(self, adapter: PlatformAdapter, elapsed: float) -> Standings:
        try:
            st = await adapter.standings(elapsed)
        except NotSupported as exc:
            if "standings" not in self.degraded:
                self.degraded.append(f"standings: {exc}")
            return Standings(rel_s=elapsed)
        except Exception as exc:  # noqa: BLE001  平台抖动不应中断会话
            if f"standings error: {type(exc).__name__}" not in self.degraded:
                self.degraded.append(f"standings error: {exc}")
            return Standings(rel_s=elapsed)
        if self.store and self.session_id is not None and st.rel_s != self._last_snapshot_rel:
            self.store.add_snapshot(self.session_id, st)
            self._last_snapshot_rel = st.rel_s
        return st

    # -------------------------------------------------- 输出
    def print_line(self, event: Event, decision: Decision) -> None:
        if self.options.quiet:
            return
        label = KIND_LABEL.get(event.kind, event.kind)
        head = f"[{fmt_time(event.rel_s)}] {label:<8}"
        p = f"P={self.state.p:5.1f} L{self.state.level}"
        if event.kind in ("verdict.ac", "verdict.ce"):
            print(f"{head} {event.detail:<22} {p}  —")
            return
        if decision.would_fire:
            print(f"{head} {event.detail:<22} {p}  s={decision.s_eff} → "
                  f"{decision.intensity:2d}% / {decision.duration_s:.1f}s  ⚡would-fire")
        else:
            reason = decision.deny[0] if decision.deny else "未通过"
            print(f"{head} {event.detail:<22} {p}  ✗ {reason}")

    def finalize(self) -> RunResult:
        counts: dict[str, int] = {}
        for ev in self.result.events:
            counts[ev.kind] = counts.get(ev.kind, 0) + 1
        fired = [d for d in self.result.decisions if d.would_fire]
        denied: dict[str, int] = {}
        for d in self.result.decisions:
            if not d.would_fire:
                for reason in d.deny:
                    key = reason.split("（")[0]
                    denied[key] = denied.get(key, 0) + 1
        ps = [p for _, p, _ in self.result.curve] or [0.0]
        self.result.summary = {
            "platform": self.meta.platform,
            "contest": self.meta.name,
            "contest_id": self.meta.contest_id,
            "duration_s": self.meta.duration_s,
            "submissions_events": counts,
            "would_fire": len(fired),
            "allowed": len([d for d in self.result.decisions if d.allowed]),
            "would_fire_by_kind": _count([e.kind for e, d in zip(self.result.events, self.result.decisions)
                                          if d.would_fire]),
            "denied_top": dict(sorted(denied.items(), key=lambda kv: -kv[1])[:5]),
            "pressure_min": round(min(ps), 1),
            "pressure_max": round(max(ps), 1),
            "pressure_end": round(ps[-1], 1),
            "level_end": self.state.level,
            "solved": len(self.solved),
            "e_total": round(self.e_total, 2),
            "degraded": list(self.degraded),
            "device_enabled": bool(self.options.enable_device),
        }
        if self.store and self.session_id is not None:
            self.store.finish_session(self.session_id, self.result.summary)
        return self.result


async def _replay_paced(adapter: PlatformAdapter, runner: SessionRunner,
                        options: RunOptions) -> RunResult:
    """把一场已结束比赛按 N 倍速重放：先拉全量提交，再按比赛内时间差 sleep。"""
    cursor: str | None = None
    subs: list[Submission] = []
    while True:
        batch = await adapter.poll(cursor)
        if not batch:
            break
        subs.extend(batch)
        cursor = batch[-1].sid
    print(f"[replay] 已取到 {len(subs)} 条提交，按 {options.replay_speed:g}× 倍速重放")
    if not subs:
        print("[replay] 警告：这场比赛没有该 handle 的提交。"
              "检查 --handle 拼写，或换一场你真的参加过的比赛。")
        return runner.finalize()

    async def _fire_due_timeouts(before_rel: float) -> None:
        """把虚拟时钟推进到 before_rel，触发所有"到点"的超时事件。"""
        for _ in range(500):             # 防御：最多补 500 个时间型事件
            due = runner.next_timeout_s()
            if due is None or due > before_rel:
                return
            events = await runner.check_timeouts(due)
            for ev, dec in events:
                runner.print_line(ev, dec)
            if not events:
                return

    prev_rel = 0.0
    for sub in subs:
        delay = max(0.0, sub.rel_s - prev_rel) / options.replay_speed
        if delay > 0:
            await asyncio.sleep(min(delay, 3.0))
        await _fire_due_timeouts(sub.rel_s)
        standings = await runner._standings(adapter, sub.rel_s)
        for ev, dec in await runner.on_submission(sub, standings):
            runner.print_line(ev, dec)
        for ev, dec in await runner.check_timeouts(sub.rel_s):
            runner.print_line(ev, dec)
        prev_rel = sub.rel_s
    await _fire_due_timeouts(runner.meta.duration_s)
    for ev, dec in await runner.check_timeouts(runner.meta.duration_s):
        runner.print_line(ev, dec)
    return runner.finalize()


def _count(items: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in items:
        out[item] = out.get(item, 0) + 1
    return out


def _open_session(adapter_meta: ContestMeta, options: RunOptions,
                  store: Store | None) -> int | None:
    if store is None:
        return None
    return store.create_session(
        adapter_meta.platform, adapter_meta.contest_id,
        options.handle or "-", options.preset, adapter_meta)


async def run_adapter(adapter: PlatformAdapter, profile: DeviceProfile | None,
                      options: RunOptions, store: Store | None = None,
                      replay: bool = False, device=None) -> RunResult:
    """统一入口：replay=True 时按夹具时间线瞬时回放，否则实时轮询。

    device 不为空且 options.enable_device 时：启动设备链路 → 等插件 → 输出真实脉冲；
    会话结束/异常时强制 estop + 关闭链路（设计 §7.6）。
    """
    meta = await adapter.load_meta()
    source_level = getattr(getattr(adapter, "capabilities", None), "source_level", "A")
    session_id = _open_session(meta, options, store)

    device_started = False
    if device is not None and options.enable_device:
        url = await device.start()
        link_file = device.save_link_file()
        device_started = True
        print(f"[device] 设备链路已启动：{url}（token 已写入 {link_file}）")
        print("[device] 下一步：在 DGHub 里启用 OJHC 硬核模式插件（starter/ojhc_link）")
        if await device.wait_client(options.device_wait_s):
            print(f"[device] 插件就绪：device={device.status.device_type or '-'} "
                  f"connected={device.status.connected}")
            effective_mode = (device.tuning or {}).get("mode") or options.mode
            print(f"[device] 电击模式：{'简单（长时间没 AC 才电）' if effective_mode == 'simple' else '压力算法'}")
            if device.tuning:
                print(f"[device] 插件页面调参已生效：{device.tuning}")
        else:
            print("[device] 警告：没等到插件连接，本次会话不会输出（会继续 dry-run 记录）")

    runner = SessionRunner(meta, profile, options, store=store, session_id=session_id,
                           device=device if device_started else None,
                           source_level=source_level)
    try:
        if replay:
            if options.replay_speed and options.replay_speed > 0:
                return await _replay_paced(adapter, runner, options)
            cursor: str | None = None
            while True:
                batch = await adapter.poll(cursor)
                if not batch:
                    break
                for sub in batch:
                    standings = await runner._standings(adapter, sub.rel_s)
                    for ev, dec in await runner.on_submission(sub, standings):
                        runner.print_line(ev, dec)
                    for ev, dec in await runner.check_timeouts(sub.rel_s):
                        runner.print_line(ev, dec)
                    cursor = sub.sid
                if options.speed > 0:
                    await asyncio.sleep(min(2.0, 1.0 / options.speed))
            for ev, dec in await runner.check_timeouts(meta.duration_s):
                runner.print_line(ev, dec)
            return runner.finalize()

        start = meta.start_ts or time.time()
        limit_s = meta.duration_s if options.max_run_s is None else min(meta.duration_s, options.max_run_s)

        # 已结束的比赛：按倍速重放（用提交自带的 relativeTimeSeconds，不依赖实时时钟）
        if options.replay_speed and options.replay_speed > 0:
            return await _replay_paced(adapter, runner, options)

        cursor = None
        last_standings: Standings | None = None
        last_standings_at = 0.0
        while True:
            elapsed = time.time() - start
            if elapsed > limit_s:
                break
            try:
                batch = await adapter.poll(cursor)
            except Exception as exc:  # noqa: BLE001
                if f"poll error: {exc}" not in runner.degraded:
                    runner.degraded.append(f"poll error: {exc}")
                batch = []
            for sub in batch:
                if last_standings is None or time.time() - last_standings_at > 45:
                    last_standings = await runner._standings(adapter, sub.rel_s)
                    last_standings_at = time.time()
                for ev, dec in await runner.on_submission(sub, last_standings):
                    runner.print_line(ev, dec)
                cursor = sub.sid
            for ev, dec in await runner.check_timeouts(elapsed):
                runner.print_line(ev, dec)
            await asyncio.sleep(5.0)
        return runner.finalize()
    finally:
        if device_started:
            await device.estop("session end")
            await device.close()
            device.remove_link_file()
            print("[device] 会话结束：已急停并关闭设备链路")
