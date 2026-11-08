"""会话运行器：数据源 → 压力引擎 → 离散事件 → 闸门 → 报告（DESIGN.md §2/§3/§7）。

M2 交付的是**回放链路**：用离线夹具把整条流水线跑通，并在终端打印
"这一下如果启用设备会不会输出、多强、被哪条规则拒绝"。
真实比赛（live）代码路径已写好，但需要网络与真实 CF 比赛才能验证。
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from . import cf
from .feedback import EVENT_S, Decision, Event, Gate
from .pressure import (
    PressureConfig, PressureInput, PressureState, compute, verdict_score,
)
from .profile import DeviceProfile
from .util import clamp

VERDICT_KIND = {
    "OK": "verdict.ac",
    "WRONG_ANSWER": "verdict.wa",
    "TIME_LIMIT_EXCEEDED": "verdict.tle",
    "MEMORY_LIMIT_EXCEEDED": "verdict.mle",
    "RUNTIME_ERROR": "verdict.re",
    "COMPILATION_ERROR": "verdict.ce",
}
KIND_LABEL = {
    "verdict.ac": "AC", "verdict.wa": "WA", "verdict.tle": "TLE",
    "verdict.mle": "MLE", "verdict.re": "RE", "verdict.ce": "CE",
    "timeout_no_submit": "超时未提交", "timeout_no_ac": "超时未 AC",
    "level_up": "等级上升",
}


def fmt_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


@dataclass
class RunOptions:
    target_percentile: float = 0.25
    rating: int | None = None
    preset: str = "casual"
    no_submit_minutes: float = 30.0
    no_ac_minutes: float | None = None
    speed: float = 0.0            # 0 = 立即回放；>0 = 每模拟秒等待 1/speed 秒
    dry_run: bool = True
    quiet: bool = False


@dataclass
class RunResult:
    events: list[Event] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)
    curve: list[tuple[float, float, int]] = field(default_factory=list)  # (elapsed, P, level)
    summary: dict = field(default_factory=dict)


class SessionRunner:
    def __init__(self, meta: cf.ContestMeta, profile: DeviceProfile | None,
                 options: RunOptions, pressure_config: PressureConfig | None = None) -> None:
        self.meta = meta
        self.options = options
        self.cfg = pressure_config or PressureConfig()
        self.gate = Gate(profile, options.preset, options.dry_run)
        self.e_total = cf.expected_solves(meta.problems, options.rating)
        self.no_ac_s = (options.no_ac_minutes * 60 if options.no_ac_minutes is not None
                        else min(3600.0, meta.duration_s / 2))
        self.state = PressureState()
        self.cursor = 0
        self.solved: set[str] = set()
        self.last_sub_s = 0.0
        self.last_ac_s = 0.0
        self.streak_fail = 0
        self.recent: list[tuple[float, float]] = []   # (score, rel_s)
        self.no_submit_armed = True
        self.no_ac_armed = True
        self.result = RunResult()

    # -------------------------------------------------- 核心：一次提交
    def on_submission(self, sub: cf.Submission, standings: cf.Standings) -> list[tuple[Event, Decision]]:
        out: list[tuple[Event, Decision]] = []
        kind = VERDICT_KIND.get(sub.verdict)
        if kind is None:            # TESTING / SUBMITTED 等未终态：不产生事件
            return out

        elapsed = sub.rel_s
        self.cursor = max(self.cursor, sub.id)
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
        elif kind in ("verdict.tle", "verdict.mle", "verdict.re", "verdict.wa"):
            self.streak_fail += 1

        prev_level = self.state.level
        self.state = self._pressure(elapsed, standings)
        if self.state.level > prev_level:
            self._emit(Event("level_up", elapsed, f"L{self.state.level}"), out)

        event = Event(kind=kind, rel_s=elapsed,
                      detail=f"题 {sub.problem}" + (f" {sub.verdict}" if kind != "verdict.ac" else " AC"))
        self._emit(event, out, s_override=EVENT_S.get(kind, 0))
        return out

    def _emit(self, event: Event, out: list[tuple[Event, Decision]], s_override: int | None = None) -> None:
        event.s = EVENT_S.get(event.kind, 0) if s_override is None else s_override
        decision = self.gate.evaluate(event, self.state, event.rel_s)
        out.append((event, decision))
        self.result.events.append(event)
        self.result.decisions.append(decision)

    def _pressure(self, elapsed: float, standings: cf.Standings) -> PressureState:
        frozen = standings.frozen or (
            self.meta.frozen_from_s is not None and elapsed >= self.meta.frozen_from_s)
        # recent 期望"新的在前"，这里把 (score, 相对时刻) 转成 (score, 年龄)
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

    # -------------------------------------------------- 输出
    def print_line(self, event: Event, decision: Decision) -> None:
        if self.options.quiet:
            return
        label = KIND_LABEL.get(event.kind, event.kind)
        head = f"[{fmt_time(event.rel_s)}] {label:<6}"
        p = f"P={self.state.p:5.1f} L{self.state.level}"
        if event.kind in ("verdict.ac", "verdict.ce"):
            print(f"{head} {event.detail:<18} {p}  —")
            return
        if decision.would_fire:
            print(f"{head} {event.detail:<18} {p}  s={decision.s_eff} → "
                  f"{decision.intensity:2d}% / {decision.duration_s:.1f}s  ⚡would-fire")
        else:
            reason = decision.deny[0] if decision.deny else "未通过"
            print(f"{head} {event.detail:<18} {p}  ✗ {reason}")

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
            "contest": self.meta.name,
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
        }
        return self.result


def _count(items: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in items:
        out[item] = out.get(item, 0) + 1
    return out


async def replay(timeline: cf.MockTimeline, profile: DeviceProfile | None,
                 options: RunOptions) -> RunResult:
    runner = SessionRunner(timeline.meta, profile, options)
    cursor: int | None = None
    for sub in timeline.poll(None):
        standings = timeline.standings(sub.rel_s)
        for ev, dec in runner.on_submission(sub, standings):
            runner.print_line(ev, dec)
        for ev, dec in runner.check_timeouts(sub.rel_s):
            runner.print_line(ev, dec)
        cursor = sub.id
        if options.speed > 0:
            await asyncio.sleep(min(2.0, 1.0 / options.speed))
    for ev, dec in runner.check_timeouts(timeline.meta.duration_s):
        runner.print_line(ev, dec)
    _ = cursor
    return runner.finalize()


async def live(client: cf.LiveCodeforces, profile: DeviceProfile | None,
               options: RunOptions, poll_s: float = 5.0,
               poll_standings_s: float = 45.0) -> RunResult:
    meta = await client.load_meta()
    runner = SessionRunner(meta, profile, options)
    start = meta.start_ts or time.time()
    last_standings: cf.Standings | None = None
    last_standings_at = 0.0
    while True:
        elapsed = time.time() - start
        if elapsed > meta.duration_s:
            break
        for sub in await client.poll(runner.cursor or None):
            if last_standings is None or time.time() - last_standings_at > poll_standings_s:
                last_standings = await client.standings(elapsed)
                last_standings_at = time.time()
            for ev, dec in runner.on_submission(sub, last_standings):
                runner.print_line(ev, dec)
        for ev, dec in runner.check_timeouts(elapsed):
            runner.print_line(ev, dec)
        await asyncio.sleep(poll_s)
    return runner.finalize()
