"""VJudge 适配器（B 级数据源：非官方接口，匿名可读）。

已实测（research/platform-data-sources.md）：
  * `GET /status/data?draw&start&length` 匿名 200，返回 data[] 含
    runId / userName / status / statusType / processing / time / probNum
  * `/solution/data/{runId}` 有反爬（400 crawler_blocked）→ 不依赖它

未完全核实（等 research/platform-endpoints-v2.md 回填）：
  * 比赛榜单接口（rank）→ 暂不支持，返回 NotSupported → 引擎降级为"仅提交信号"
  * statusType 数值映射 → 优先用 status 字符串，数值映射只作兜底且标 UNKNOWN 风险

时间基准：VJudge 的 status 记录里 `time` 是提交时刻（毫秒级 epoch）。
本适配器在 run 开始时记录 session_start_ts，用它换算 rel_s。
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import urllib.parse
import urllib.request

from .base import (
    Capabilities, ContestMeta, PlatformError, Problem, Standings, Submission,
    normalize_verdict, NotSupported,
)

VJ_BASE = "https://vjudge.net"
MIN_INTERVAL_S = 1.2          # 非官方接口，保守一点


class VJudgeAdapter:
    platform = "vjudge"
    capabilities = Capabilities(
        realtime_verdict=True, live_standings=False, freeze_aware=False,
        requires_auth=False, source_level="B")

    def __init__(self, contest_id: str, handle: str, rating: int | None = None,
                 duration_s: float = 7200.0, session_start_ts: float | None = None,
                 min_interval_s: float = MIN_INTERVAL_S) -> None:
        self.contest_id = str(contest_id)
        self.handle = handle
        self.rating = rating
        self.duration_s = float(duration_s)
        self.session_start_ts = session_start_ts or time.time()
        self.min_interval_s = min_interval_s
        self._last_request = 0.0
        self._meta: ContestMeta | None = None
        self._name: str = f"VJudge Contest {contest_id}"

    # -------------------------------------------------- 基础设施
    async def _throttle(self) -> None:
        delay = self._last_request + self.min_interval_s - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
        self._last_request = time.monotonic()

    def _headers(self) -> dict:
        return {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/124.0 Safari/537.36 OJ-DGHUB/0.2"),
            "Referer": f"{VJ_BASE}/contest/{self.contest_id}",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
        }

    def _get(self, url: str, timeout: float = 15.0) -> str:
        req = urllib.request.Request(url, headers=self._headers())
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "replace")

    # -------------------------------------------------- 元信息
    async def load_meta(self) -> ContestMeta:
        await self._throttle()
        try:
            html = self._get(f"{VJ_BASE}/contest/{self.contest_id}")
            m = re.search(r"<title>\s*(.*?)\s*</title>", html, re.S | re.I)
            if m:
                title = re.sub(r"\s+", " ", m.group(1)).strip()
                if title:
                    self._name = title[:120]
        except Exception:
            # 页面拿不到不致命：提交接口是匿名可读的
            pass
        self._meta = ContestMeta(
            platform=self.platform,
            contest_id=self.contest_id,
            name=self._name,
            duration_s=self.duration_s,
            problems=[],                    # 非官方接口拿不到题目难度 → E_total 回退 3.0
            start_ts=self.session_start_ts,
            frozen_from_s=None,             # 封榜信息未知 → 引擎按"未知"保守处理
            url=f"{VJ_BASE}/contest/{self.contest_id}",
        )
        return self._meta

    # -------------------------------------------------- 提交
    async def poll(self, after: str | None) -> list[Submission]:
        await self._throttle()
        url = (f"{VJ_BASE}/status/data?draw=1&start=0&length=50"
               f"&un={urllib.parse.quote(self.handle)}&contestId={self.contest_id}"
               f"&_={int(time.time() * 1000)}")
        try:
            body = self._get(url)
            payload = json.loads(body)
        except Exception as exc:  # noqa: BLE001
            raise PlatformError(f"VJudge 接口异常：{exc}") from exc

        records = payload.get("data") or []
        after_id = int(after) if after and str(after).isdigit() else None
        out: list[Submission] = []
        for rec in records:
            try:
                run_id = int(rec.get("runId") or 0)
            except (TypeError, ValueError):
                continue
            if after_id is not None and run_id <= after_id:
                continue
            if rec.get("contestId") not in (None, self.contest_id, int(self.contest_id)
                                            if self.contest_id.isdigit() else None):
                continue
            user = str(rec.get("userName") or "")
            if user and user.lower() != self.handle.lower():
                continue
            verdict = self._verdict_of(rec)
            submitted_ms = rec.get("time")
            rel_s = max(0.0, time.time() - self.session_start_ts)
            if isinstance(submitted_ms, (int, float)) and submitted_ms > 1e12:
                rel_s = max(0.0, submitted_ms / 1000.0 - self.session_start_ts)
            out.append(Submission(
                sid=str(run_id), rel_s=rel_s, verdict=verdict,
                problem=str(rec.get("probNum") or rec.get("problemId") or "?"),
                raw=rec))
        out.sort(key=lambda s: int(s.sid))
        return out

    @staticmethod
    def _verdict_of(rec: dict) -> str:
        """优先 status 字符串；statusType 映射未核实，宁可 UNKNOWN 也不误判。"""
        status = rec.get("status")
        if isinstance(status, str) and status.strip():
            return normalize_verdict(status)
        return normalize_verdict(None)

    # -------------------------------------------------- 榜单
    async def standings(self, elapsed_s: float) -> Standings:
        # 比赛榜单接口尚未核实（研究回填后再实现）；先明确降级，不猜
        raise NotSupported("VJudge 实时榜单接口未核实，当前降级为仅提交信号")

    # -------------------------------------------------- 辅助
    def contest_url(self) -> str:
        return f"{VJ_BASE}/contest/{self.contest_id}"
