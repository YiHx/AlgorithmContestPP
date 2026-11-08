"""Codeforces 适配器（官方 API，A 级数据源）。

事实依据（research/platform-data-sources.md）：
  * 匿名 contest.standings 只允许仅带 contestId（一次拿时长 + 题目难度 + 榜单）
  * contest.status 支持 handle 单数，只取本人提交
  * 官方限流 1 次/2 秒 → 全局串行 RateLimiter（默认 2.1s）
"""
from __future__ import annotations

import asyncio
import json
import time
import urllib.request

from .base import (
    Capabilities, ContestMeta, PlatformError, Problem, Standings, Submission,
    normalize_verdict, expected_rank_from_ratings,
)

CF_API = "https://codeforces.com/api/"
MIN_INTERVAL_S = 2.1


class RateLimiter:
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
    req = urllib.request.Request(url, headers={
        "User-Agent": "OJ-DGHUB/0.2 (+https://github.com/YiHx/OJ_DGHUB)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


class CodeforcesAdapter:
    platform = "codeforces"
    capabilities = Capabilities(
        realtime_verdict=True, live_standings=True, freeze_aware=True,
        requires_auth=False, source_level="A")

    def __init__(self, contest_id: str, handle: str, rating: int | None = None,
                 min_interval_s: float = MIN_INTERVAL_S) -> None:
        self.contest_id = str(contest_id)
        self.handle = handle
        self.rating = rating
        self.limiter = RateLimiter(min_interval_s)
        self._meta: ContestMeta | None = None
        self._rows: list[dict] = []
        self._standings: Standings | None = None
        self._standings_at = 0.0

    async def load_meta(self) -> ContestMeta:
        await self.limiter.wait()
        data = _http_get_json(f"{CF_API}contest.standings?contestId={self.contest_id}")
        if data.get("status") != "OK":
            raise PlatformError(f"CF API: {data.get('comment')}")
        res = data["result"]
        contest = res.get("contest", {})
        self._rows = res.get("rows", [])
        duration = float(contest.get("durationSeconds") or 7200)
        freeze = contest.get("freezeDurationSeconds")
        self._meta = ContestMeta(
            platform=self.platform,
            contest_id=self.contest_id,
            name=contest.get("name") or f"Contest {self.contest_id}",
            duration_s=duration,
            problems=[Problem(index=str(p.get("index")), rating=p.get("rating"))
                      for p in res.get("problems", [])],
            start_ts=float(contest.get("startTimeSeconds") or 0) or None,
            frozen_from_s=(duration - float(freeze)) if freeze else None,
            url=f"https://codeforces.com/contest/{self.contest_id}",
        )
        return self._meta

    async def poll(self, after: str | None) -> list[Submission]:
        await self.limiter.wait()
        data = _http_get_json(
            f"{CF_API}contest.status?contestId={self.contest_id}"
            f"&handle={self.handle}&from=1&count=100")
        if data.get("status") != "OK":
            raise PlatformError(f"CF API: {data.get('comment')}")
        after_id = int(after) if after else None
        out: list[Submission] = []
        duration_s = getattr(getattr(self, "meta", None), "duration_s", None)
        for item in data["result"]:
            sid = int(item["id"])
            if after_id is not None and sid <= after_id:
                continue
            rel_s = float(item.get("relativeTimeSeconds") or 0)
            if duration_s and rel_s > duration_s:   # CF 对赛外提交返回 INT_MAX
                rel_s = float(duration_s)
            out.append(Submission(
                sid=str(sid),
                rel_s=rel_s,
                verdict=normalize_verdict(item.get("verdict")),
                problem=str((item.get("problem") or {}).get("index") or "?"),
                points=float(item.get("points") or 0.0),
                raw=item,
            ))
        out.sort(key=lambda s: int(s.sid))
        return out

    async def standings(self, elapsed_s: float) -> Standings:
        now = time.monotonic()
        if self._standings and now - self._standings_at < 45:
            return self._standings
        await self.limiter.wait()
        data = _http_get_json(f"{CF_API}contest.standings?contestId={self.contest_id}")
        if data.get("status") != "OK":
            raise PlatformError(f"CF API: {data.get('comment')}")
        res = data["result"]
        rows = res.get("rows", [])
        self._rows = rows
        my_rank = my_solved = None
        for row in rows:
            handles = [m.get("handle") for m in (row.get("party", {}).get("members") or [])]
            if self.handle in handles:
                my_rank = int(row.get("rank") or 0) or None
                my_solved = len([r for r in row.get("problemResults", [])
                                 if float(r.get("points") or 0) > 0])
                break
        contest = res.get("contest", {})
        self._standings = Standings(
            rel_s=elapsed_s, rank=my_rank, solved=my_solved,
            n_official=len(rows) or None, frozen=bool(contest.get("frozen")))
        self._standings_at = now
        return self._standings

    def my_expected_rank(self) -> float | None:
        """若 rows 里带 rating（user.info 补全后），可算 seed 期望；当前通常为 None。"""
        ratings = [float(r["rating"]) for r in self._rows
                   if isinstance(r.get("rating"), (int, float))]
        return expected_rank_from_ratings(ratings, self.rating)
