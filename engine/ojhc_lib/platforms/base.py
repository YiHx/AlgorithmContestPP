"""多平台适配层：统一的数据模型 + 适配器接口（DESIGN.md §4）。

所有平台把数据归一化成这里的四个模型，引擎只认这四样：
    ContestMeta / Submission / Standings / Problem

约定：
  * submission 的 id 统一为字符串（CF 用 int 但转 str；VJudge 是 runId）
  * verdict 统一成 Codeforces 风格枚举（见 VERDICT_* / normalize_verdict）
  * 时间统一用"相对比赛开始的秒数"（rel_s）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

# ---------------------------------------------------------------- 判题枚举（CF 风格）
VERDICT_OK = "OK"
VERDICT_WA = "WRONG_ANSWER"
VERDICT_TLE = "TIME_LIMIT_EXCEEDED"
VERDICT_MLE = "MEMORY_LIMIT_EXCEEDED"
VERDICT_RE = "RUNTIME_ERROR"
VERDICT_CE = "COMPILATION_ERROR"
VERDICT_PARTIAL = "PARTIAL"
VERDICT_TESTING = "TESTING"
VERDICT_UNKNOWN = "UNKNOWN"

# 失败类判题（会触发压力子分与事件）
FAILURE_VERDICTS = {VERDICT_WA, VERDICT_TLE, VERDICT_MLE, VERDICT_RE, VERDICT_CE}
PENDING_VERDICTS = {VERDICT_TESTING}

_VERDICT_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("accepted", "ok", "correct"), VERDICT_OK),
    (("wrong answer", "wrong_answer", "wa"), VERDICT_WA),
    (("time limit", "tle"), VERDICT_TLE),
    (("memory limit", "mle"), VERDICT_MLE),
    (("runtime error", "re"), VERDICT_RE),
    (("compilation error", "compile error", "ce"), VERDICT_CE),
    (("presentation error", "output limit"), VERDICT_WA),   # 归到失败类，但不区分子类型
    (("partial", "partially accepted"), VERDICT_PARTIAL),
    (("judging", "pending", "queued", "submitted", "testing", "running", "compiling", "waiting"),
     VERDICT_TESTING),
]


def normalize_verdict(raw: str | None) -> str:
    """把各平台的判题字符串归一成统一枚举；无法识别返回 UNKNOWN。"""
    if not raw:
        return VERDICT_UNKNOWN
    # CF 用 WRONG_ANSWER / TIME_LIMIT_EXCEEDED 这种下划线风格，先统一成空格再匹配
    text = str(raw).strip().lower().replace("_", " ").replace("-", " ")
    text = " ".join(text.split())
    for keywords, canonical in _VERDICT_KEYWORDS:
        for kw in keywords:
            if kw == text or kw in text:
                return canonical
    return VERDICT_UNKNOWN


@dataclass
class Problem:
    index: str
    rating: int | None = None


@dataclass
class Submission:
    sid: str
    rel_s: float
    verdict: str
    problem: str
    points: float = 0.0
    raw: dict = field(default_factory=dict)


@dataclass
class Standings:
    rel_s: float = 0.0
    rank: int | None = None
    solved: int | None = None
    n_official: int | None = None
    frozen: bool = False


@dataclass
class ContestMeta:
    platform: str
    contest_id: str
    name: str
    duration_s: float
    problems: list[Problem] = field(default_factory=list)
    start_ts: float | None = None          # epoch 秒；None 表示"从会话开始算"
    frozen_from_s: float | None = None
    url: str = ""
    extras: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Capabilities:
    realtime_verdict: bool     # 能否拿到实时判题
    live_standings: bool       # 能否拿到实时榜单
    freeze_aware: bool         # 是否能识别封榜
    requires_auth: bool        # 是否需要登录态（扩展）
    source_level: str          # A / B / C（§6.1）


class PlatformError(RuntimeError):
    """平台数据不可用（网络/接口变更/登录态缺失）。"""


class NotSupported(PlatformError):
    """该平台不支持此能力（走降级）。"""


@runtime_checkable
class PlatformAdapter(Protocol):
    platform: str
    capabilities: Capabilities

    async def load_meta(self) -> ContestMeta: ...
    async def poll(self, after: str | None) -> list[Submission]: ...
    async def standings(self, elapsed_s: float) -> Standings: ...


def expected_solves(problems: list[Problem], rating: int | None) -> float:
    """E_total = Σ 1/(1+10^((R_p - r)/400))（§5.3d）；拿不到难度回退 3.0。"""
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


def expected_rank_from_ratings(ratings: list[float], my_rating: float | None) -> float | None:
    """CF seed 公式近似（§5.3b）；需要全场 rating，拿不到返回 None。"""
    if not ratings or my_rating is None:
        return None
    seed = 1.0
    for other in ratings:
        seed += 1.0 / (1.0 + 10 ** ((other - my_rating) / 400.0))
    return seed
