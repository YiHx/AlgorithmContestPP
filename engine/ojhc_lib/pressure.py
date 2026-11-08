"""竞赛压力分引擎（DESIGN.md §5 的可执行版本）。

纯函数式核心：不读网络、不读时钟、不做 I/O，输入相同必然输出相同。
`examples/pressure_demo.py` 是它的"教学版"，本模块上线后以本模块为准，
两者对 §5.6 的示例数值必须保持一致（selftest 会断言）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

LEVEL_THRESHOLDS = (25.0, 50.0, 75.0)  # L0 <25 ≤ L1 <50 ≤ L2 <75 ≤ L3


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


@dataclass
class PressureConfig:
    weights: dict = field(default_factory=lambda: {
        "rank": 0.35, "progress": 0.30, "stagnation": 0.20, "recent": 0.15})
    gamma: float = 1.25
    ema_tau_s: float = 45.0
    update_min_interval_s: float = 30.0
    deadband: float = 3.0
    rho_max: float = 2.5
    delta_ref: float = 1.2
    stagnation_min_s: float = 8 * 60
    stagnation_max_s: float = 40 * 60
    ramp_start: float = 0.08
    ramp_end: float = 0.18
    recent_window: int = 8
    recent_max_age_s: float = 25 * 60
    recent_halflife_s: float = 10 * 60
    recent_prior_w: float = 1.0
    streak_bonus: float = 15.0
    hysteresis_up: float = 4.0
    hysteresis_down: float = 6.0
    hysteresis_need: int = 2
    max_upgrades: int = 3
    late_phase_bias: bool = True
    min_confidence_weight: float = 0.55


DEFAULT_CONFIG = PressureConfig()

VERDICT_SCORE = {
    "AC": 1.0, "OK": 1.0,
    "WRONG_ANSWER": -1.0, "TIME_LIMIT_EXCEEDED": -1.0,
    "MEMORY_LIMIT_EXCEEDED": -1.0, "RUNTIME_ERROR": -1.0,
    "COMPILATION_ERROR": -0.3,
}


def verdict_score(verdict: str, points_frac: float | None = None) -> float | None:
    """单次提交得分；未终态（PENDING 等）返回 None 表示不参与。"""
    if verdict in VERDICT_SCORE:
        return VERDICT_SCORE[verdict]
    if verdict == "PARTIAL" and points_frac is not None:
        return 2 * points_frac - 1
    return None


@dataclass
class PressureInput:
    now: float
    tau: float
    duration_s: float
    n_official: int | None
    rank: int | None
    solved: int
    t_since_ac_s: float
    e_total: float
    e_seed: float | None = None
    target_percentile: float = 0.25
    recent: list[tuple[float, float]] = field(default_factory=list)  # (score, age_s)，新的在前
    streak_fail: int = 0
    frozen: bool = False


@dataclass
class PressureState:
    p: float = 0.0
    level: int = 0
    components: dict = field(default_factory=dict)   # 各子分（None=缺失）
    confidence: str = "low"                          # high / low
    updated_at: float = 0.0
    up_cnt: int = 0
    down_cnt: int = 0
    upgrades: int = 0
    p_rank: float | None = None                      # 排名子分的"最后可信值"（封榜冻结用）
    frozen_streams: dict = field(default_factory=dict)


def level_of(p: float) -> int:
    if p < LEVEL_THRESHOLDS[0]:
        return 0
    if p < LEVEL_THRESHOLDS[1]:
        return 1
    if p < LEVEL_THRESHOLDS[2]:
        return 2
    return 3


def recent_score(recent: list[tuple[float, float]], cfg: PressureConfig = DEFAULT_CONFIG) -> float:
    """最近提交趋势（§5.4）：窗口 + 半衰期衰减 + 中性先验。"""
    items = [(score, age) for score, age in recent
             if age <= cfg.recent_max_age_s][: cfg.recent_window]
    if not items:
        return 0.0
    num = 0.0
    den = cfg.recent_prior_w
    for score, age in items:
        # 防御：CF 虚拟参赛/赛外提交可能返回 INT_MAX 之类的时间戳
        age = max(0.0, min(float(age), cfg.recent_max_age_s))
        w = 0.5 ** (age / cfg.recent_halflife_s)
        num += w * score
        den += w
    rbar = num / den if den > 0 else 0.0
    return 100.0 * clamp((0.15 - rbar) / 0.9, 0.0, 1.0)


def _expected_rank(inp: PressureInput) -> float | None:
    """E_eff = max(目标排名, 评级期望排名)；都拿不到则 None。"""
    goal = None
    if inp.n_official and inp.n_official > 0:
        goal = inp.target_percentile * inp.n_official
    seed = inp.e_seed if inp.e_seed and inp.e_seed > 0 else None
    if goal is None and seed is None:
        return None
    if goal is None:
        return seed
    if seed is None:
        return goal
    return max(goal, seed)


def _stagnation_ref(duration_s: float, e_total: float, cfg: PressureConfig) -> float:
    return clamp(1.2 * duration_s / max(e_total, 0.8), cfg.stagnation_min_s, cfg.stagnation_max_s)


def compute(inp: PressureInput, prev: PressureState | None = None,
            cfg: PressureConfig = DEFAULT_CONFIG) -> PressureState:
    """一次压力计算。缺数据的子分记 None，按 §5.5 归一化并降低 confidence。"""
    prev = prev or PressureState()
    st = PressureState(updated_at=inp.now, p_rank=prev.p_rank, upgrades=prev.upgrades,
                       up_cnt=prev.up_cnt, down_cnt=prev.down_cnt, frozen_streams=dict(prev.frozen_streams))

    # --- 期望模型（§5.3）
    e_eff = _expected_rank(inp)
    e_solves = inp.e_total * inp.tau

    # --- 四个子分（§5.4）
    s_rank: float | None = None
    if inp.rank is not None and e_eff is not None:
        s_rank = 100.0 * clamp(math.log(inp.rank / e_eff) / math.log(cfg.rho_max), 0.0, 1.0)
        st.p_rank = s_rank
    # 封榜冻结：沿用最后可信值，权重在聚合时单独降权
    if inp.frozen and prev.p_rank is not None:
        s_rank = prev.p_rank

    deficit = max(0.0, e_solves - inp.solved)
    s_prog = 100.0 * (1.0 - math.exp(-deficit / cfg.delta_ref))

    t_ref = _stagnation_ref(inp.duration_s, inp.e_total, cfg)
    ramp = clamp((inp.tau - cfg.ramp_start) / max(cfg.ramp_end - cfg.ramp_start, 1e-6), 0.0, 1.0)
    s_stag = 100.0 * (1.0 - math.exp(-inp.t_since_ac_s / t_ref)) * ramp

    s_recent = recent_score(inp.recent, cfg)
    if inp.streak_fail >= 3:
        s_recent = min(100.0, s_recent + cfg.streak_bonus)

    parts = {"rank": s_rank, "progress": s_prog, "stagnation": s_stag, "recent": s_recent}

    # --- 聚合（§5.5）
    weights = dict(cfg.weights)
    if cfg.late_phase_bias and inp.tau > 0.75:
        weights["rank"] *= 1.2
        weights["stagnation"] *= 1.2
    if inp.frozen:
        weights["rank"] *= 0.5

    present = {k: w for k, w in weights.items() if parts.get(k) is not None}
    raw_weight_sum = sum(present.values())
    if raw_weight_sum <= 0 or raw_weight_sum < cfg.min_confidence_weight:
        st.components = parts
        st.confidence = "low"
        st.p = prev.p
        return st

    p_raw = sum(present[k] * parts[k] for k in present) / raw_weight_sum
    p_s = 100.0 * (p_raw / 100.0) ** cfg.gamma if p_raw > 0 else 0.0
    p_s = clamp(p_s, 0.0, 100.0)

    # --- 平滑 + 死区（§5.5）
    dt = inp.now - prev.updated_at
    if prev.updated_at <= 0:
        p = p_s
    elif dt < cfg.update_min_interval_s:
        p = prev.p
    else:
        alpha = min(0.6, 1.0 - math.exp(-dt / cfg.ema_tau_s))
        crossed = level_of(p_s) != prev.level
        if abs(p_s - prev.p) < cfg.deadband and not crossed:
            p = prev.p
        else:
            p = prev.p + alpha * (p_s - prev.p)

    # --- 等级滞回（§5.5）
    st.p = p
    st.components = parts
    st.confidence = "high"
    _update_level(st, p, prev, cfg)
    return st


def _update_level(st: PressureState, p: float, prev: PressureState, cfg: PressureConfig) -> None:
    level = prev.level
    upper = LEVEL_THRESHOLDS[level] if level < 3 else None
    lower = LEVEL_THRESHOLDS[level - 1] if level > 0 else None

    if upper is not None and p >= upper + cfg.hysteresis_up:
        st.up_cnt, st.down_cnt = prev.up_cnt + 1, 0
    elif lower is not None and p <= lower - cfg.hysteresis_down:
        st.down_cnt, st.up_cnt = prev.down_cnt + 1, 0
    else:
        st.up_cnt = st.down_cnt = 0

    if st.up_cnt >= cfg.hysteresis_need and st.upgrades < cfg.max_upgrades:
        level += 1
        st.upgrades += 1
        st.up_cnt = st.down_cnt = 0
    elif st.down_cnt >= cfg.hysteresis_need:
        level -= 1
        st.up_cnt = st.down_cnt = 0
    st.level = max(0, min(3, level))
