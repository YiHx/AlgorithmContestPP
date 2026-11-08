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
    dry_run: bool = True
    quiet: bool = False


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
                 store: Store | None = None, session_id: int | None = None) -> None:
        self.meta = meta
        self.options = options
        self.cfg = pressure_config or PressureConfig()
        self.gate = Gate(profile, options.preset, options.dry_run)
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
        self.result = RunResult(session_id=session_id)
        self.degraded: list[str] = []
        self._last_snapshot_rel: float | None = None

    # -------------------------------------------------- 核心：一次提交
    def on_submission(self, sub: Submission, standings: Standings) -> list[tuple[Event, Decision]]:
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
        elif kind in ("verdict.tle", "verdict.mle", "verdict.re", "verdict.wa",
                      "verdict.partial"):
            self.streak_fail += 1

        prev_level = self.state.level
        self.state = self._pressure(elapsed, standings)
        if self.state.level > prev_level:
            self._emit(Event("level_up", elapsed, f"L{self.state.level}"), out)

        event = Event(kind=kind, rel_s=elapsed,
                      detail=f"题 {sub.problem}" + (" AC" if kind == "verdict.ac" else f" {sub.verdict}"))
        self._emit(event, out)
        return out

    def _emit(self, event: Event, out: list[tuple[Event, Decision]]) -> None:
        event.s = EVENT_S.get(event.kind, 0)
        decision = self.gate.evaluate(event, self.state, event.rel_s)
        out.append((event, decision))
        self.result.events.append(event)
        self.result.decisions.append(decision)
        if self.store and self.session_id is not None:
            self.store.add_event(self.session_id, event.rel_s, event.kind, event.s,
                                 decision.intensity, decision.duration_s,
                                 decision.would_fire, list(decision.deny))

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

    # -------------------------------------------------- 时间型事件
    def check_timeouts(self, elapsed: float) -> list[tuple[Event, Decision]]:
        out: list[tuple[Event, Decision]] = []
        if self.no_submit_armed and elapsed - self.last_sub_s > self.options.no_submit_minutes * 60:
            self.no_submit_armed = False
            self._emit(Event("timeout_no_submit", elapsed,
                             f"距上次提交 {fmt_time(elapsed - self.last_sub_s)}"), out)
        if self.no_ac_armed and elapsed - self.last_ac_s > self.no_ac_s:
            self.no_ac_armed = False
            self._emit(Event("timeout_no_ac", elapsed,
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
        }
        if self.store and self.session_id is not None:
            self.store.finish_session(self.session_id, self.result.summary)
        return self.result


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
                      replay: bool = False) -> RunResult:
    """统一入口：replay=True 时按夹具时间线瞬时回放，否则实时轮询。"""
    meta = await adapter.load_meta()
    session_id = _open_session(meta, options, store)
    runner = SessionRunner(meta, profile, options, store=store, session_id=session_id)
    if replay:
        cursor: str | None = None
        while True:
            batch = await adapter.poll(cursor)
            if not batch:
                break
            for sub in batch:
                standings = await runner._standings(adapter, sub.rel_s)
                for ev, dec in runner.on_submission(sub, standings):
                    runner.print_line(ev, dec)
                for ev, dec in runner.check_timeouts(sub.rel_s):
                    runner.print_line(ev, dec)
                cursor = sub.sid
            if options.speed > 0:
                await asyncio.sleep(min(2.0, 1.0 / options.speed))
        for ev, dec in runner.check_timeouts(meta.duration_s):
            runner.print_line(ev, dec)
        return runner.finalize()

    start = meta.start_ts or time.time()
    limit_s = meta.duration_s if options.max_run_s is None else min(meta.duration_s, options.max_run_s)
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
            for ev, dec in runner.on_submission(sub, last_standings):
                runner.print_line(ev, dec)
            cursor = sub.sid
        for ev, dec in runner.check_timeouts(elapsed):
            runner.print_line(ev, dec)
        await asyncio.sleep(5.0)
    return runner.finalize()
