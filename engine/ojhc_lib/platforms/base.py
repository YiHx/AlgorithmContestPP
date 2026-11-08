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

# 精确匹配（缩写/状态词）优先，避免 "waiting" 被当成 "wa"
_EXACT_VERDICT = {
    "ac": VERDICT_OK, "ok": VERDICT_OK, "accepted": VERDICT_OK,
    "wa": VERDICT_WA, "wrong answer": VERDICT_WA,
    "tle": VERDICT_TLE, "mle": VERDICT_MLE, "re": VERDICT_RE, "ce": VERDICT_CE,
    "pe": VERDICT_WA, "ole": VERDICT_WA,
    "partial": VERDICT_PARTIAL,
    "judging": VERDICT_TESTING, "pending": VERDICT_TESTING,
    "queued": VERDICT_TESTING, "submitted": VERDICT_TESTING,
    "testing": VERDICT_TESTING, "running": VERDICT_TESTING,
    "compiling": VERDICT_TESTING, "waiting": VERDICT_TESTING,
}

# 短语包含匹配（长句优先；不放 "wa"/"re" 这类短词，否则 "waiting" 会误判）
_VERDICT_PHRASES: list[tuple[str, str]] = [
    ("accepted", VERDICT_OK),
    ("correct", VERDICT_OK),
    ("wrong answer", VERDICT_WA),
    ("time limit", VERDICT_TLE),
    ("memory limit", VERDICT_MLE),
    ("runtime error", VERDICT_RE),
    ("compilation error", VERDICT_CE),
    ("compile error", VERDICT_CE),
    ("presentation error", VERDICT_WA),
    ("output limit", VERDICT_WA),
    ("idleness limit", VERDICT_WA),
    ("partially accepted", VERDICT_PARTIAL),
    ("judging", VERDICT_TESTING),
    ("pending", VERDICT_TESTING),
    ("queued", VERDICT_TESTING),
    ("testing", VERDICT_TESTING),
    ("running", VERDICT_TESTING),
    ("waiting", VERDICT_TESTING),
    ("submitted", VERDICT_TESTING),
    # 牛客 statusMessage 是中文（实测样本）
    ("答案正确", VERDICT_OK),
    ("答案错误", VERDICT_WA),
    ("运行超时", VERDICT_TLE),
    ("内存超限", VERDICT_MLE),
    ("运行错误", VERDICT_RE),
    ("编译错误", VERDICT_CE),
    ("通过", VERDICT_OK),
]


def normalize_verdict(raw: str | None) -> str:
    """把各平台的判题字符串归一成统一枚举；无法识别返回 UNKNOWN。"""
    if not raw:
        return VERDICT_UNKNOWN
    # CF 用 WRONG_ANSWER / TIME_LIMIT_EXCEEDED 这种下划线风格，先统一成空格再匹配
    text = str(raw).strip().lower().replace("_", " ").replace("-", " ")
    text = " ".join(text.split())
    if text in _EXACT_VERDICT:
        return _EXACT_VERDICT[text]
    for phrase, canonical in _VERDICT_PHRASES:
        if phrase in text:
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
