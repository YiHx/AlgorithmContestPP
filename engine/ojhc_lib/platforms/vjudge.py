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
import time
import urllib.parse
import urllib.request

from .base import (
    Capabilities, ContestMeta, PlatformError, Standings, Submission,
    normalize_verdict,
)

VJ_BASE = "https://vjudge.net"
MIN_INTERVAL_S = 1.2          # 非官方接口，保守一点


class VJudgeAdapter:
    platform = "vjudge"
    capabilities = Capabilities(
        realtime_verdict=True, live_standings=True, freeze_aware=False,
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
        self._standings: Standings | None = None
        self._standings_at = 0.0
        self._is_replay = False

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
        """优先用 rank/single（自带 title/begin/length），页面 HTML 只作兜底。"""
        await self._throttle()
        try:
            payload = json.loads(self._get(f"{VJ_BASE}/contest/rank/single/{self.contest_id}"))
            parsed = parse_rank_single(payload, self.handle) or {}
            if parsed.get("title"):
                self._name = str(parsed["title"])[:160]
            if parsed.get("duration_s"):
                self.duration_s = float(parsed["duration_s"])
            if parsed.get("begin_ts"):
                self.session_start_ts = float(parsed["begin_ts"])
            self._is_replay = bool(parsed.get("is_replay"))
        except Exception:
            # 拿不到不影响提交监听（status/data 匿名可读）
            pass
        self._meta = ContestMeta(
            platform=self.platform,
            contest_id=self.contest_id,
            name=self._name,
            duration_s=self.duration_s,
            problems=[],                    # 非官方接口拿不到题目难度 → E_total 回退 3.0
            start_ts=self.session_start_ts,
            frozen_from_s=None,             # 封榜无法程序化识别 → adapter 内做保守启发式
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
        """实测接口：GET /contest/rank/single/<cid>（匿名 JSON，无 rank 字段 → 自算）。"""
        now = time.monotonic()
        if self._standings and now - self._standings_at < 20:
            return self._standings
        await self._throttle()
        try:
            payload = json.loads(self._get(f"{VJ_BASE}/contest/rank/single/{self.contest_id}"))
        except Exception as exc:  # noqa: BLE001
            raise PlatformError(f"VJudge 榜单接口异常：{exc}") from exc
        parsed = parse_rank_single(payload, self.handle)
        if parsed is None:
            raise PlatformError("VJudge 榜单里找不到你的账号（检查 --handle 是否与 VJudge 用户名一致）")
        if parsed.get("title"):
            self._name = str(parsed["title"])[:160]
        if parsed.get("duration_s"):
            self.duration_s = float(parsed["duration_s"])
        # 封榜无法从 JSON 判定：保守启发式——非 replay 比赛末 60 分钟视为可能封榜
        frozen = bool(parsed.get("is_replay") is False
                      and self.duration_s > 3600
                      and elapsed_s > self.duration_s - 3600)
        self._standings = Standings(
            rel_s=elapsed_s, rank=parsed.get("rank"), solved=parsed.get("solved"),
            n_official=parsed.get("n_official"), frozen=frozen)
        self._standings_at = now
        return self._standings

    # -------------------------------------------------- 辅助
    def contest_url(self) -> str:
        return f"{VJ_BASE}/contest/{self.contest_id}"


# ---------------------------------------------------------------- rank/single 解析
def _duration_seconds(length) -> float:
    """VJudge 的 length 常见为分钟；防御性地兼容已经是秒的情况。"""
    if not isinstance(length, (int, float)) or length <= 0:
        return 7200.0
    return float(length) * 60.0 if length <= 24 * 60 else float(length)


def _names_of(info: dict) -> set[str]:
    names: set[str] = set()
    for key in ("name", "nickname", "username"):
        value = info.get(key)
        if isinstance(value, str) and value:
            names.add(value.lower())
    for member in info.get("members") or []:
        if isinstance(member, dict):
            for key in ("name", "nickname", "username"):
                value = member.get(key)
                if isinstance(value, str) and value:
                    names.add(value.lower())
    return names


def parse_rank_single(payload: dict, handle: str) -> dict | None:
    """解析 /contest/rank/single 返回，算出我的排名/过题数（JSON 里没有 rank 字段）。

    submissions 行两种格式（研究实测）：
        4 元组： [uid, probIdx, flag, 耗时ms]                 （ACM 赛制，flag=1 表示 AC）
        6 元组： [uid, probIdx, flag, 耗时ms, 得分, 满分]      （计分赛制）
    """
    participants = payload.get("participants") or {}
    if not participants:
        return None
    rows = payload.get("submissions") or []
    handle_l = handle.strip().lower()

    my_uid = None
    for uid, info in participants.items():
        if isinstance(info, dict) and handle_l in _names_of(info):
            my_uid = str(uid)
            break
    if my_uid is None:
        return None

    scored = any(isinstance(r, (list, tuple)) and len(r) >= 6 for r in rows)
    ac_time: dict[tuple[str, str], float] = {}
    wa_before: dict[tuple[str, str], int] = {}
    best_score: dict[tuple[str, str], float] = {}

    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 4:
            continue
        uid, prob, flag = str(row[0]), str(row[1]), row[2]
        try:
            t_ms = float(row[3] or 0)
        except (TypeError, ValueError):
            t_ms = 0.0
        key = (uid, prob)
        if scored:
            try:
                score = float(row[4] or 0)
            except (TypeError, ValueError):
                score = 0.0
            best_score[key] = max(best_score.get(key, 0.0), score)
        elif int(flag or 0) == 1:
            ac_time.setdefault(key, t_ms)
        else:
            if key not in ac_time:
                wa_before[key] = wa_before.get(key, 0) + 1

    stats: dict[str, dict] = {}
    for uid in participants:
        if scored:
            score = sum(v for (u, _p), v in best_score.items() if u == uid)
            solved = sum(1 for (u, _p), v in best_score.items() if u == uid and v > 0)
            stats[uid] = {"solved": solved, "score": score, "penalty": 0.0}
        else:
            mine = [p for (u, p) in ac_time if u == uid]
            penalty = sum(ac_time[(uid, p)] for p in mine)
            penalty += sum(wa_before.get((uid, p), 0) * 20 * 60 * 1000 for p in mine)
            stats[uid] = {"solved": len(mine), "score": 0.0, "penalty": penalty}

    if scored:
        order = sorted(stats, key=lambda u: (-stats[u]["score"], stats[u]["penalty"], u))
    else:
        order = sorted(stats, key=lambda u: (-stats[u]["solved"], stats[u]["penalty"], u))
    rank = order.index(my_uid) + 1 if my_uid in order else None

    begin = payload.get("begin")
    begin_ts = float(begin) / 1000.0 if isinstance(begin, (int, float)) and begin > 1e12 else None
    return {
        "title": payload.get("title") or "",
        "begin_ts": begin_ts,
        "duration_s": _duration_seconds(payload.get("length")),
        "rank": rank,
        "solved": stats[my_uid]["solved"],
        "n_official": len(participants),
        "is_replay": bool(payload.get("isReplay", False)),
    }
