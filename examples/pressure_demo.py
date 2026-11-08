"""压力算法参考实现（与 oj-hardcore-design.md §5 一一对应）。

用途：
  1. 验证设计文档 §5.6 的数值示例（可直接运行核对）；
  2. 作为 M1 实现压力引擎时的公式基线。

注意：这是"公式演示"，不是完整引擎。缺少输入（如封榜、EMA 历史）时按文档约定处理。
运行：python examples/pressure_demo.py
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

# ---------------------------------------------------------------- 参数默认值（§5.9）
GAMMA = 1.25
RHO_MAX = 2.5
DELTA_REF = 1.2          # 题
RAMP_START, RAMP_END = 0.08, 0.18
RECENT_HALFLIFE_S = 600
RECENT_PRIOR_W = 1.0     # 中性先验权重：没有证据 = 中性，且让衰减真正生效
STREAK_BONUS = 15
WEIGHTS = {"rank": 0.35, "progress": 0.30, "stagnation": 0.20, "recent": 0.15}
LEVELS = [("L0", 0, 25), ("L1", 25, 50), ("L2", 50, 75), ("L3", 75, 100)]


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


@dataclass
class Scenario:
    name: str
    duration_s: float
    tau: float
    rating: int
    n_official: int
    percentile: float
    e_total: float
    rank: int | None
    solved: int
    t_since_ac_s: float
    e_seed: int | None = None
    recent_scores: list[float] = field(default_factory=list)  # 新的在前，AC=+1, WA=-1 ...
    recent_ages_s: list[float] = field(default_factory=list)
    streak_fail: int = 0


def compute(s: Scenario) -> dict:
    # ---------- 期望模型 §5.3
    r_goal = s.percentile * s.n_official
    e_eff = max(r_goal, s.e_seed) if s.e_seed else r_goal
    e_solves = s.e_total * s.tau

    # ---------- 子分 §5.4
    s_rank = None
    if s.rank is not None and e_eff > 0:          # E_eff<=0 时 log 会崩，直接判缺失
        s_rank = 100 * clamp(math.log(s.rank / e_eff) / math.log(RHO_MAX), 0, 1)

    deficit = max(0.0, e_solves - s.solved)
    s_prog = 100 * (1 - math.exp(-deficit / DELTA_REF))

    t_ref = clamp(1.2 * s.duration_s / max(s.e_total, 0.8), 8 * 60, 40 * 60)
    ramp = clamp((s.tau - RAMP_START) / (RAMP_END - RAMP_START), 0, 1)
    s_stag = 100 * (1 - math.exp(-s.t_since_ac_s / t_ref)) * ramp

    s_recent = 0.0
    if s.recent_scores:
        w = [0.5 ** (age / RECENT_HALFLIFE_S) for age in s.recent_ages_s]
        # 分母加中性先验：否则符号一致时衰减会被约掉（R̄ 恒为 ±1，旧失败永不衰减）
        rbar = sum(wi * si for wi, si in zip(w, s.recent_scores)) / (sum(w) + RECENT_PRIOR_W)
        s_recent = 100 * clamp((0.15 - rbar) / 0.9, 0, 1)
        if s.streak_fail >= 3:
            s_recent = min(100.0, s_recent + STREAK_BONUS)

    # ---------- 聚合 §5.5（缺失子分归一化）
    parts = {"rank": s_rank, "progress": s_prog, "stagnation": s_stag, "recent": s_recent}
    w = dict(WEIGHTS)
    if s.tau > 0.75:
        w["rank"] *= 1.2
        w["stagnation"] *= 1.2
    num = sum(w[k] * v for k, v in parts.items() if v is not None)
    den = sum(w[k] for k, v in parts.items() if v is not None)
    p_raw = num / den
    p_s = 100 * (p_raw / 100) ** GAMMA

    return {"r_goal": r_goal, "e_eff": e_eff, "e_solves": e_solves, "t_ref": t_ref,
            "s_rank": s_rank, "s_prog": s_prog, "s_stag": s_stag, "s_recent": s_recent,
            "p_raw": p_raw, "p_s": p_s}


def ema(p_prev: float, p_new: float, dt_s: float = 30.0) -> float:
    """按真实间隔计算 α：α = 1 - exp(-dt/45)，上限 0.6（文档 §5.5）。"""
    if abs(p_new - p_prev) < 3:           # 死区（真实实现还要加"未跨越等级"条件）
        return p_prev
    alpha = min(0.6, 1 - math.exp(-dt_s / 45.0))
    return p_prev + alpha * (p_new - p_prev)


def level_of(p: float) -> str:
    for name, lo, hi in LEVELS:
        if lo <= p < hi:
            return name
    return "L3"


BASE = dict(duration_s=7200, tau=0.55, rating=1400, n_official=8000,
            percentile=0.25, e_total=3.2, e_seed=2600)

SCENARIOS = [
    Scenario(name="leading", rank=900, solved=2, t_since_ac_s=240,
             recent_scores=[1.0], recent_ages_s=[60], **BASE),
    Scenario(name="example", rank=3200, solved=1, t_since_ac_s=1320,
             recent_scores=[-1.0, -1.0], recent_ages_s=[300, 540], **BASE),
    Scenario(name="collapse", rank=5200, solved=0, t_since_ac_s=3960, streak_fail=3,
             recent_scores=[-1.0, -1.0, -1.0, -1.0], recent_ages_s=[120, 420, 780, 1100], **BASE),
]

if __name__ == "__main__":
    print(f"{'scenario':<10}{'S_rank':>8}{'S_prog':>8}{'S_stag':>8}{'S_rec':>7}{'P_raw':>8}{'P_s':>8}{'EMA→':>7}  等级")
    prev = {"example": 28.0}
    for sc in SCENARIOS:
        r = compute(sc)
        p = ema(prev.get(sc.name, r["p_s"]), r["p_s"], dt_s=30.0)
        print(f"{sc.name:<6}{r['s_rank']:>8.1f}{r['s_prog']:>8.1f}{r['s_stag']:>8.1f}"
              f"{r['s_recent']:>7.1f}{r['p_raw']:>8.1f}{r['p_s']:>8.1f}{p:>7.1f}  {level_of(p)}")
