"""兼容层：历史代码/脚本可能从 `cf` 导入。

真正的实现已经迁移到 `platforms/codeforces.py` 与 `platforms/mock.py`。
新代码请直接 `from .platforms import ...`，不要再依赖本文件。
"""
from __future__ import annotations

from .platforms.base import (  # noqa: F401
    ContestMeta, Problem, Standings, Submission, normalize_verdict,
)
from .platforms.codeforces import (  # noqa: F401
    CF_API, MIN_INTERVAL_S, CodeforcesAdapter, RateLimiter, _http_get_json,
)
from .platforms.mock import MockTimeline, load_mock  # noqa: F401

# 旧名字
LiveCodeforces = CodeforcesAdapter

__all__ = [
    "CF_API", "MIN_INTERVAL_S", "CodeforcesAdapter", "LiveCodeforces", "RateLimiter",
    "MockTimeline", "load_mock", "Submission", "Standings", "ContestMeta", "Problem",
    "normalize_verdict", "_http_get_json",
]
