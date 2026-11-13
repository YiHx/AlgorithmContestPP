"""平台适配器注册表：URL 识别 + 工厂。

已实现（P1）：codeforces（A 级，官方 API）、vjudge（B 级，非官方接口）
规划中（P2）：atcoder / luogu / nowcoder —— 需要浏览器扩展在登录态页面取数
"""
from __future__ import annotations

import re
from typing import Callable

from .base import (
    Capabilities, ContestMeta, FAILURE_VERDICTS, NotSupported, PENDING_VERDICTS,
    PlatformAdapter, PlatformError, Problem, Standings, Submission, VERDICT_CE,
    VERDICT_MLE, VERDICT_OK, VERDICT_PARTIAL, VERDICT_RE, VERDICT_TESTING,
    VERDICT_TLE, VERDICT_UNKNOWN, VERDICT_WA, expected_rank_from_ratings,
    expected_solves, normalize_verdict,
)
from .codeforces import CodeforcesAdapter
from .mock import MockTimeline, load_mock
from .vjudge import VJudgeAdapter, parse_rank_single

# 五平台：CF/VJudge 原生 HTTP；AtCoder/洛谷/牛客 走浏览器扩展桥接（C 级）
SUPPORTED = {"codeforces", "vjudge", "atcoder", "luogu", "nowcoder"}
BRIDGED = {"atcoder", "luogu", "nowcoder"}
PLANNED = set()          # 兼容旧引用；现在没有"未实现"的平台
PLATFORM_LABELS = {
    "codeforces": "Codeforces",
    "vjudge": "VJudge",
    "atcoder": "AtCoder",
    "luogu": "洛谷",
    "nowcoder": "牛客",
}
PLATFORM_CAPABILITIES = {
    "codeforces": "A 级：官方 API，实时判题 + 榜单 + 封榜",
    "vjudge": "B 级：非官方接口，实时判题；榜单自算，封榜不可判定",
    "atcoder": "C 级：扩展取数（需登录），standings/json + 本人提交页",
    "luogu": "C 级：扩展取数（需登录），当前只接成绩表",
    "nowcoder": "C 级：扩展取数，status-list 提交可用；无实时榜单",
}

_URL_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("codeforces", re.compile(r"codeforces\.com/(?:contest|gym)/(\d+)", re.I)),
    ("vjudge", re.compile(r"vjudge\.(?:net|org)/contest/(\d+)", re.I)),
    ("atcoder", re.compile(r"atcoder\.jp/contests/([A-Za-z0-9_-]+)", re.I)),
    ("luogu", re.compile(r"luogu\.(?:com\.cn|com)/contest/(\d+)", re.I)),
    ("nowcoder", re.compile(r"nowcoder\.com/(?:acm|contest)/contest/(\d+)", re.I)),
]


def detect_from_url(url: str) -> tuple[str, str] | None:
    """从比赛 URL 识别 (platform, contest_id)；识别不了返回 None。"""
    for platform, pattern in _URL_PATTERNS:
        m = pattern.search(url or "")
        if m:
            return platform, m.group(1)
    return None


def create_adapter(platform: str, contest_id: str, handle: str,
                   rating: int | None = None, duration_s: float = 7200.0,
                   session_start_ts: float | None = None) -> PlatformAdapter:
    platform = (platform or "").lower()
    if platform == "codeforces":
        return CodeforcesAdapter(contest_id, handle, rating, duration_s=duration_s)
    if platform == "vjudge":
        return VJudgeAdapter(contest_id, handle, rating, duration_s, session_start_ts)
    if platform in BRIDGED:
        raise PlatformError(
            f"{PLATFORM_LABELS.get(platform, platform)} 需要浏览器扩展桥接"
            "（用 `ojhc run --url ... --handle ...`，引擎会自动启动桥接）")
    if platform in PLANNED:
        raise PlatformError(f"{PLATFORM_LABELS.get(platform, platform)} 适配器开发中")
    raise PlatformError(f"未知平台：{platform!r}")


__all__ = [
    "Capabilities", "ContestMeta", "PlatformAdapter", "PlatformError", "Problem",
    "Standings", "Submission", "normalize_verdict", "expected_solves",
    "expected_rank_from_ratings", "detect_from_url", "create_adapter",
    "CodeforcesAdapter", "VJudgeAdapter", "MockTimeline", "load_mock",
    "SUPPORTED", "BRIDGED", "PLANNED", "PLATFORM_LABELS", "PLATFORM_CAPABILITIES",
    "VERDICT_OK", "VERDICT_WA", "VERDICT_TLE", "VERDICT_MLE", "VERDICT_RE",
    "VERDICT_CE", "VERDICT_PARTIAL", "VERDICT_TESTING", "VERDICT_UNKNOWN",
    "FAILURE_VERDICTS", "PENDING_VERDICTS", "NotSupported", "parse_rank_single",
]
