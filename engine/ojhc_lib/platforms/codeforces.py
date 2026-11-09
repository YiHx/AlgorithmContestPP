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
from dataclasses import replace

from .base import (
    Capabilities, ContestMeta, PlatformError, Problem, Standings, Submission,
    PENDING_VERDICTS, VERDICT_UNKNOWN,
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
        # 浏览器快速通道（扩展盯页面，绕开 CF API 2s 限速）；None = 未启用
        self.fast = None
        self._fast_connected_logged = False
        self._fast_wait_logged = False
        self._native_task: asyncio.Task | None = None
        self._native_batch: list[Submission] = []
        self._native_baseline: int | None = None
        self._reported: set[str] = set()
        self._native_error = ""
        self.poll_sleep_s = 2.2

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
        self._update_standings(res, 0.0)
        return self._meta

    def attach_fast(self, source) -> None:
        """接入浏览器扩展快速通道（source.drain(cursor)）。"""
        self.fast = source
        self.poll_sleep_s = 0.4

    async def poll(self, after: str | None) -> list[Submission]:
        collected = await self._fetch_submissions()
        after_id = int(after) if after else None
        return sorted((sub for sub in collected if after_id is None or int(sub.sid) > after_id),
                      key=lambda sub: int(sub.sid))

    async def _fetch_submissions(self) -> list[Submission]:
        await self.limiter.wait()
        data = await asyncio.to_thread(
            _http_get_json, f"{CF_API}contest.status?contestId={self.contest_id}"
            f"&handle={self.handle}&from=1&count=100")
        if data.get("status") != "OK":
            raise PlatformError(f"CF API: {data.get('comment')}")
        return [
            Submission(
                sid=str(int(item["id"])),
                rel_s=float(item.get("relativeTimeSeconds") or 0),
                verdict=normalize_verdict(item.get("verdict")),
                problem=str((item.get("problem") or {}).get("index") or "?"),
                points=float(item.get("points") or 0.0),
                raw=item,
            )
            for item in data["result"]
        ]

    async def _refresh_native(self) -> None:
        try:
            batch = await self._fetch_submissions()
            if self._native_baseline is None:
                self._native_baseline = max((int(sub.sid) for sub in batch), default=0)
            else:
                self._native_batch = [sub for sub in batch if int(sub.sid) > self._native_baseline]
            if time.monotonic() - self._standings_at >= 45:
                await self._fetch_standings(0.0)
            self._native_error = ""
        except Exception as exc:  # 网络故障不影响浏览器通道
            message = str(exc)
            if message != self._native_error:
                print(f"[live] CF 官方 API 暂不可用：{message}")
                self._native_error = message

    async def poll_live(self, after: str | None) -> list[Submission]:
        if self.fast is None:
            return await self.poll(after)
        if self.fast.handshakes and not self._fast_connected_logged:
            self._fast_connected_logged = True
            print("[fast] 浏览器扩展已连接，CF 页面采集已建立基线")
        elif not self.fast.handshakes and not self._fast_wait_logged:
            self._fast_wait_logged = True
            print("[fast] 等待浏览器扩展采集 CF 页面；官方 API 在后台运行")

        if self._native_task is None or self._native_task.done():
            self._native_task = asyncio.create_task(self._refresh_native())
        batch, self._native_batch = self._native_batch, []
        collected = {sub.sid: sub for sub in batch}
        fast_ids = set()
        for sub in self.fast.drain():
            if sub.sid.isdecimal() and sub.verdict not in PENDING_VERDICTS and sub.verdict != VERDICT_UNKNOWN:
                collected[sub.sid] = sub
                fast_ids.add(sub.sid)

        ready = []
        for sub in sorted(collected.values(), key=lambda sub: int(sub.sid)):
            if sub.sid in self._reported or sub.verdict in PENDING_VERDICTS or sub.verdict == VERDICT_UNKNOWN:
                continue
            self._reported.add(sub.sid)
            if sub.sid in fast_ids:
                print(f"[fast] 扩展收到判题 #{sub.sid} {sub.verdict}")
            ready.append(sub)
        return ready

    async def close_live(self) -> None:
        if self._native_task is not None:
            self._native_task.cancel()
            await asyncio.gather(self._native_task, return_exceptions=True)
            self._native_task = None

    async def standings(self, elapsed_s: float) -> Standings:
        now = time.monotonic()
        if self._native_task is not None:
            return replace(self._standings, rel_s=elapsed_s) if self._standings else Standings(rel_s=elapsed_s)
        if self._standings and now - self._standings_at < 45:
            return self._standings
        return await self._fetch_standings(elapsed_s)

    async def _fetch_standings(self, elapsed_s: float) -> Standings:
        await self.limiter.wait()
        data = await asyncio.to_thread(
            _http_get_json, f"{CF_API}contest.standings?contestId={self.contest_id}")
        if data.get("status") != "OK":
            raise PlatformError(f"CF API: {data.get('comment')}")
        return self._update_standings(data["result"], elapsed_s)

    def _update_standings(self, res: dict, elapsed_s: float) -> Standings:
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
        self._standings_at = time.monotonic()
        return self._standings

    def my_expected_rank(self) -> float | None:
        """若 rows 里带 rating（user.info 补全后），可算 seed 期望；当前通常为 None。"""
        ratings = [float(r["rating"]) for r in self._rows
                   if isinstance(r.get("rating"), (int, float))]
        return expected_rank_from_ratings(ratings, self.rating)
