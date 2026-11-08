"""离线回放夹具：从 JSON 读一条比赛时间线，接口与真实适配器对齐。

夹具格式（兼容旧的 cf_1847_mock.json）：

{
  "platform": "codeforces",          // 可选，默认 codeforces
  "contest": {
    "id": 1847, "name": "...", "duration_s": 7200, "start_ts": 0,
    "frozen_from_s": 5400,
    "problems": [{"index": "A", "rating": 800}]
  },
  "submissions": [{"id": 101, "rel_s": 300, "verdict": "WRONG_ANSWER", "problem": "A"}],
  "standings": [{"rel_s": 0, "rank": 4000, "solved": 0, "n_official": 8000, "frozen": false}]
}
"""
from __future__ import annotations

import json
from pathlib import Path

from .base import (
    Capabilities, ContestMeta, Problem, Standings, Submission, normalize_verdict,
)


class MockTimeline:
    """可当适配器用的回放数据源（同一份接口：load_meta/poll/standings）。"""

    capabilities = Capabilities(
        realtime_verdict=True, live_standings=True, freeze_aware=True,
        requires_auth=False, source_level="A")

    def __init__(self, payload: dict) -> None:
        contest = payload.get("contest") or {}
        self.platform = str(payload.get("platform") or "codeforces")
        self.contest_id = str(contest.get("id", "mock"))
        self._meta = ContestMeta(
            platform=self.platform,
            contest_id=self.contest_id,
            name=contest.get("name", "Mock Contest"),
            duration_s=float(contest.get("duration_s", 7200)),
            problems=[Problem(index=p["index"], rating=p.get("rating"))
                      for p in contest.get("problems", [])],
            start_ts=contest.get("start_ts"),
            frozen_from_s=contest.get("frozen_from_s"),
            url=contest.get("url", ""),
        )
        self._subs = sorted(
            [Submission(sid=str(s.get("id") or s.get("runId") or i),
                        rel_s=float(s.get("rel_s", 0)),
                        verdict=normalize_verdict(s.get("verdict")),
                        problem=str(s.get("problem", "?")),
                        points=float(s.get("points", 0.0)))
             for i, s in enumerate(payload.get("submissions", []))],
            key=lambda s: _num_or_str(s.sid))
        self._snapshots = sorted(payload.get("standings", []), key=lambda x: x["rel_s"])

    async def load_meta(self) -> ContestMeta:
        return self._meta

    @property
    def meta(self) -> ContestMeta:
        return self._meta

    async def poll(self, after: str | None) -> list[Submission]:
        if after is None:
            return list(self._subs)
        idx = next((i for i, s in enumerate(self._subs) if s.sid == str(after)), None)
        if idx is not None:
            return list(self._subs[idx + 1:])
        after_num = _num_or_str(after)
        if isinstance(after_num, int):
            return [s for s in self._subs
                    if isinstance(_num_or_str(s.sid), int) and _num_or_str(s.sid) > after_num]
        return []

    async def standings(self, elapsed_s: float) -> Standings:
        snap = None
        for item in self._snapshots:
            if float(item["rel_s"]) <= elapsed_s:
                snap = item
            else:
                break
        if snap is None:
            return Standings(rel_s=elapsed_s)
        return Standings(
            rel_s=float(snap["rel_s"]), rank=snap.get("rank"), solved=snap.get("solved"),
            n_official=snap.get("n_official"), frozen=bool(snap.get("frozen", False)))

    def contest_url(self) -> str:
        return self._meta.url


def _num_or_str(value: str):
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def load_mock(path: str | Path) -> MockTimeline:
    return MockTimeline(json.loads(Path(path).read_text(encoding="utf-8")))
