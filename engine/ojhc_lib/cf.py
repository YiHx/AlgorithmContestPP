"""Codeforces 数据源（官方 API，只读）+ 离线回放夹具。

事实依据（DESIGN.md §4.2）：
  * 匿名 `contest.standings` 只允许仅带 `contestId` —— 正好一次拿全「比赛时长 + 题目难度 + 榜单」
  * `contest.status` 支持 `handle` 单数 —— 只取自己的提交，最省额度
  * 官方限流 1 次/2 秒 —— 所有请求过同一个 RateLimiter（默认 2.1s 间隔）

M2 阶段提供两种数据源：
  LiveCodeforces  ：真打比赛（需要网络）
  MockTimeline    ：离线回放夹具（无网络也能跑通整条流水线，便于审阅和测试）
"""
from __future__ import annotations

import asyncio
import json
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

CF_API = "https://codeforces.com/api/"
MIN_INTERVAL_S = 2.1  # 官方 1 次/2 秒，这里留一点余量

TERMINAL_VERDICTS = {
    "OK", "WRONG_ANSWER", "TIME_LIMIT_EXCEEDED", "MEMORY_LIMIT_EXCEEDED",
    "RUNTIME_ERROR", "COMPILATION_ERROR", "PARTIAL", "CHALLENGED", "SKIPPED",
    "IDLENESS_LIMIT_EXCEEDED", "SECURITY_VIOLATED", "CRASHED", "REJECTED",
}


@dataclass
class Problem:
    index: str
    rating: int | None = None


@dataclass
class Submission:
    id: int
    contest_id: int
    rel_s: float          # 相对比赛开始的秒数（CF: relativeTimeSeconds）
    verdict: str
    problem: str
    points: float = 0.0
    creation_ts: float = 0.0


@dataclass
class Standings:
    rel_s: float = 0.0
    rank: int | None = None
    solved: int | None = None
    n_official: int | None = None
    frozen: bool = False


@dataclass
class ContestMeta:
    id: int
    name: str
    duration_s: float
    problems: list[Problem] = field(default_factory=list)
    start_ts: float = 0.0     # 比赛开始的 epoch 秒（mock 里是模拟时间起点）
    frozen_from_s: float | None = None   # 封榜开始于第几秒（None = 未知/无封榜）


def expected_solves(problems: list[Problem], rating: int | None) -> float:
    """E_total = Σ 1/(1+10^((R_p - r)/400))（DESIGN.md §5.3d）。拿不到难度就回退 3.0。"""
    if not problems or rating is None:
        return 3.0
    total = 0.0
    known = 0
    for p in problems:
        if p.rating is None:
            continue
        known += 1
        total += 1.0 / (1.0 + 10 ** ((p.rating - rating) / 400.0))
    return total if known else 3.0


def expected_rank(rows: list[dict], my_handle: str, my_rating: int | None) -> float | None:
    """评级期望排名（CF seed 近似）：需要全场 rating，拿不到就返回 None。

    真实实现里 rating 要另调 user.info（≤10000 handles）；M2 先只支持夹具里直接给出。
    """
    ratings = [r.get("rating") for r in rows if isinstance(r.get("rating"), (int, float))]
    if not ratings or my_rating is None:
        return None
    seed = 1.0 + sum(1.0 / (1.0 + 10 ** ((other - my_rating) / 400.0)) for other in ratings)
    return seed


class RateLimiter:
    """全局串行限流：同一时刻只允许一个请求，且两次之间至少间隔 min_interval。"""

    def __init__(self, min_interval_s: float = MIN_INTERVAL_S) -> None:
        self.min_interval_s = min_interval_s
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            delay = self._last + self.min_interval_s - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self._last = time.monotonic()


def _http_get_json(url: str, timeout: float = 15.0) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "OJ-DGHUB/0.1 (+https://github.com/)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


class LiveCodeforces:
    """真实 CF 数据源（M2 代码路径；沙箱里无网络时用 MockTimeline 验证）。"""

    def __init__(self, handle: str, contest_id: int, rating: int | None = None) -> None:
        self.handle = handle
        self.contest_id = contest_id
        self.rating = rating
        self.limiter = RateLimiter()
        self.meta: ContestMeta | None = None
        self._last_standings: Standings | None = None
        self._last_standings_ts = 0.0

    async def load_meta(self) -> ContestMeta:
        await self.limiter.wait()
        data = _http_get_json(f"{CF_API}contest.standings?contestId={self.contest_id}")
        if data.get("status") != "OK":
            raise RuntimeError(f"CF API 返回失败：{data.get('comment')}")
        res = data["result"]
        contest = res.get("contest", {})
        problems = [Problem(index=str(p.get("index")), rating=p.get("rating"))
                    for p in res.get("problems", [])]
        duration = float(contest.get("durationSeconds") or 7200)
        freeze = contest.get("freezeDurationSeconds")
        frozen_from = (duration - float(freeze)) if freeze else None
        self.meta = ContestMeta(
            id=self.contest_id, name=contest.get("name") or f"Contest {self.contest_id}",
            duration_s=duration, problems=problems,
            start_ts=float(contest.get("startTimeSeconds") or 0), frozen_from_s=frozen_from)
        return self.meta

    async def poll(self, after_id: int | None = None) -> list[Submission]:
        await self.limiter.wait()
        data = _http_get_json(
            f"{CF_API}contest.status?contestId={self.contest_id}"
            f"&handle={self.handle}&from=1&count=100")
        if data.get("status") != "OK":
            raise RuntimeError(f"CF API 返回失败：{data.get('comment')}")
        out: list[Submission] = []
        for item in data["result"]:
            sid = int(item["id"])
            if after_id is not None and sid <= after_id:
                continue
            problem = (item.get("problem") or {}).get("index") or "?"
            out.append(Submission(
                id=sid, contest_id=int(item.get("contestId") or self.contest_id),
                rel_s=float(item.get("relativeTimeSeconds") or 0),
                verdict=str(item.get("verdict") or "TESTING"),
                problem=str(problem), points=float(item.get("points") or 0.0),
                creation_ts=float(item.get("creationTimeSeconds") or 0)))
        out.sort(key=lambda s: s.id)
        return out

    async def standings(self, elapsed_s: float | None = None) -> Standings:
        """榜单刷新（默认 45 秒一次，避免浪费 1/2s 的额度）。"""
        now = time.monotonic()
        if self._last_standings and now - self._last_standings_ts < 45:
            return self._last_standings
        await self.limiter.wait()
        data = _http_get_json(f"{CF_API}contest.standings?contestId={self.contest_id}")
        if data.get("status") != "OK":
            raise RuntimeError(f"CF API 返回失败：{data.get('comment')}")
        res = data["result"]
        rows = res.get("rows", [])
        my_rank = None
        my_solved = None
        for row in rows:
            handles = [m.get("handle") for m in (row.get("party", {}).get("members") or [])]
            if self.handle in handles:
                my_rank = int(row.get("rank") or 0) or None
                my_solved = len([r for r in row.get("problemResults", [])
                                 if float(r.get("points") or 0) > 0])
                break
        contest = res.get("contest", {})
        elapsed = elapsed_s if elapsed_s is not None else 0.0
        frozen = bool(contest.get("frozen"))
        self._last_standings = Standings(
            rel_s=elapsed, rank=my_rank, solved=my_solved,
            n_official=len(rows) or None, frozen=frozen)
        self._last_standings_ts = now
        return self._last_standings


class MockTimeline:
    """离线回放夹具：从 JSON 读一条比赛时间线，接口与 LiveCodeforces 对齐。"""

    def __init__(self, payload: dict) -> None:
        contest = payload["contest"]
        self.meta = ContestMeta(
            id=int(contest["id"]), name=contest.get("name", "Mock Contest"),
            duration_s=float(contest.get("duration_s", 7200)),
            problems=[Problem(index=p["index"], rating=p.get("rating"))
                      for p in contest.get("problems", [])],
            start_ts=float(contest.get("start_ts", 0.0)),
            frozen_from_s=contest.get("frozen_from_s"))
        self._subs = sorted(
            [Submission(id=int(s["id"]), contest_id=self.meta.id, rel_s=float(s["rel_s"]),
                        verdict=s["verdict"], problem=s["problem"],
                        points=float(s.get("points", 0.0)))
             for s in payload.get("submissions", [])],
            key=lambda s: s.id)
        self._snapshots = sorted(payload.get("standings", []), key=lambda x: x["rel_s"])

    def poll(self, after_id: int | None = None) -> list[Submission]:
        return [s for s in self._subs if after_id is None or s.id > after_id]

    def standings(self, elapsed_s: float) -> Standings:
        snap = None
        for item in self._snapshots:
            if float(item["rel_s"]) <= elapsed_s:
                snap = item
            else:
                break
        if snap is None:
            return Standings(rel_s=elapsed_s, rank=None, solved=None, n_official=None)
        return Standings(
            rel_s=float(snap["rel_s"]), rank=snap.get("rank"), solved=snap.get("solved"),
            n_official=snap.get("n_official"), frozen=bool(snap.get("frozen", False)))


def load_mock(path: str | Path) -> MockTimeline:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return MockTimeline(payload)
