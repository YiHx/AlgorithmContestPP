"""从离散事件到"会不会真的输出"（DESIGN.md §7 的可执行版本）。

M2 阶段设备还没接入，所以这里做两件事：
  1. 完整跑一遍**判定链路**（事件 → 感受等级 s → 校准映射 → 11 条闸门的核心几条）；
  2. 记录"如果此刻启用了设备，会不会输出、多强"（would_fire），用于审阅与调参。

真正调用 DGHub trigger 放在 M4，且必须再过 lease/急停（§7.6）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .pressure import PressureState
from .profile import HARD_CAP_PCT, DeviceProfile
from .util import clamp

# 感受等级 s → 强度比例 / 脉冲时长（§7.3 / §7.9.3）
FRAC = {0: 0.0, 1: 0.20, 2: 0.35, 3: 0.50, 4: 0.65, 5: 0.85}
DURATION_S = {0: 0.0, 1: 0.6, 2: 0.8, 3: 1.2, 4: 2.0, 5: 2.0}

# 事件 → 默认感受等级（§7.2）
EVENT_S = {
    "verdict.ac": 0,
    "verdict.wa": 2,
    "verdict.tle": 3,
    "verdict.mle": 3,
    "verdict.re": 3,
    "verdict.ce": 0,
    "verdict.partial": 2,
    "timeout_no_submit": 3,
    "timeout_no_ac": 4,
    "level_up": 0,
    "contest_end": 0,
}

# 压力等级只作为"上限门槛"（§7.3 ②）
LEVEL_CAP = {0: 0, 1: 3, 2: 4, 3: 5}

# 预设（§7.9.5）
PRESETS = {
    "casual": {"cooldown_s": 90.0, "same_kind_s": 120.0, "max_s": 4, "enable_level_up": False},
    "hardcore": {"cooldown_s": 60.0, "same_kind_s": 90.0, "max_s": 5, "enable_level_up": True},
}

# 硬边界（§7.7）
HARD_MAX_S = HARD_CAP_PCT
BUCKET_CAPACITY = 5
BUCKET_REFILL_S = 180.0
HOURLY_CAP = 30

# 插件页面可调参数（Engine 端再钳一次，硬边界不可绕过）
TUNING_DEFAULTS = {
    "base_intensity": 74, "intensity_step": 4, "max_s": 4,
    "cooldown_s": 90, "hourly_cap": 30, "allow_level_up": False, "preset": "呼吸",
}


def normalize_tuning(raw: dict | None) -> dict | None:
    if not isinstance(raw, dict) or not raw:
        return None

    def num(key, lo, hi, default):
        try:
            value = int(round(float(raw.get(key, default))))
        except (TypeError, ValueError):
            value = default
        return max(lo, min(hi, value))

    return {
        "base_intensity": num("base_intensity", 40, 90, 74),
        "intensity_step": num("intensity_step", 0, 10, 4),
        "max_s": num("max_s", 2, 5, 4),
        "cooldown_s": num("cooldown_s", 30, 300, 90),
        "hourly_cap": num("hourly_cap", 5, 60, 30),
        "allow_level_up": bool(raw.get("allow_level_up", False)),
        "preset": str(raw.get("preset") or "呼吸")[:32],
    }

NO_AC_CAP = 5
ESCALATION_COOLDOWN_BONUS_S = 60.0


@dataclass
class Event:
    kind: str
    rel_s: float
    detail: str = ""
    s: int = 0


@dataclass
class Decision:
    allowed: bool = False          # 真会输出？M2 恒 False（未接设备）
    would_fire: bool = False       # 除掉"设备开关"之外是否全部通过
    deny: list[str] = field(default_factory=list)
    intensity: int = 0
    duration_s: float = 0.0
    s_eff: int = 0


@dataclass
class _GateState:
    tokens: float = float(BUCKET_CAPACITY)
    tokens_at: float = 0.0
    last_output_s: float = -1e9
    last_kind_s: dict = field(default_factory=dict)
    hourly: list[float] = field(default_factory=list)
    failure_streak: int = 0
    outputs_without_ac: int = 0


class Gate:
    """闸门 + 冷却/令牌桶/升压禁止/无 AC 上限的**模拟**。"""

    def __init__(self, profile: DeviceProfile | None, preset: str = "casual",
                 dry_run: bool = True) -> None:
        self.profile = profile
        self.preset_name = preset if preset in PRESETS else "casual"
        self.preset = PRESETS[self.preset_name]
        self.dry_run = dry_run
        self.st = _GateState()
        self.tuning: dict | None = None

    def set_tuning(self, raw: dict | None) -> None:
        self.tuning = normalize_tuning(raw)

    @property
    def profile_ok(self) -> bool:
        p = self.profile
        return bool(p and p.is_valid() and not p.expired())

    def _refill(self, now_s: float) -> None:
        elapsed = now_s - self.st.tokens_at
        if elapsed > 0:
            self.st.tokens = min(BUCKET_CAPACITY,
                                 self.st.tokens + elapsed / BUCKET_REFILL_S)
            self.st.tokens_at = now_s

    def note_ac(self) -> None:
        """一次 AC：重置连败与无 AC 计数。"""
        self.st.failure_streak = 0
        self.st.outputs_without_ac = 0

    def evaluate(self, event: Event, state: PressureState, now_s: float) -> Decision:
        d = Decision()
        if not self.profile_ok:
            d.deny.append("未校准/档案过期")
            return d
        if state.confidence == "low":
            d.deny.append("confidence=low（数据不足）")
            return d

        tuning = self.tuning
        max_s = self.preset["max_s"]
        cooldown_base = self.preset["cooldown_s"]
        hourly_cap = HOURLY_CAP
        allow_level_up = self.preset["enable_level_up"]
        if tuning:
            max_s = min(max_s, int(tuning["max_s"]))
            cooldown_base = float(tuning["cooldown_s"])
            hourly_cap = int(tuning["hourly_cap"])
            allow_level_up = allow_level_up or bool(tuning["allow_level_up"])

        # 事件本身是否要输出
        if event.kind == "level_up" and not allow_level_up:
            d.deny.append(f"level_up 事件在 {self.preset_name} 预设下关闭")
            return d
        if event.s <= 0:
            d.deny.append("纯展示事件（无输出）")
            return d

        # 感受等级：受压力等级上限约束
        s_eff = min(event.s, LEVEL_CAP[state.level], max_s)
        if s_eff <= 0:
            d.deny.append(f"level=L{state.level} 封顶 s≤{LEVEL_CAP[state.level]}")
            return d

        # 升压禁止（§7.4）：连续 3 次输出无 AC → 封顶 s=2 且额外冷却
        escalation_banned = self.st.failure_streak >= 3 and self.st.outputs_without_ac > 0
        if escalation_banned:
            s_eff = min(s_eff, 2)

        intensity = self.profile.intensity_for(FRAC[s_eff])
        duration = DURATION_S[s_eff]
        if intensity <= 0:
            d.deny.append("映射强度为 0")
            return d

        # 冷却与配额
        self._refill(now_s)
        cooldown = cooldown_base + (
            ESCALATION_COOLDOWN_BONUS_S if escalation_banned else 0.0)
        if now_s - self.st.last_output_s < cooldown:
            d.deny.append(f"全局冷却中（{cooldown:.0f}s）")
            return d
        last_same = self.st.last_kind_s.get(event.kind, -1e9)
        if now_s - last_same < self.preset["same_kind_s"]:
            d.deny.append(f"同类事件冷却中（{event.kind}）")
            return d
        recent_hour = [t for t in self.st.hourly if now_s - t < 3600]
        if len(recent_hour) >= hourly_cap:
            d.deny.append(f"小时窗硬限 {hourly_cap} 次")
            return d
        if self.st.tokens < 1:
            d.deny.append("令牌桶已空")
            return d
        if self.st.outputs_without_ac >= NO_AC_CAP:
            d.deny.append("连续无 AC 输出达上限（强制回 display-only）")
            return d

        # 通过：只有"设备开关"这一条没满足（M2 固定 dry-run）
        d.intensity = int(clamp(intensity, 0, HARD_MAX_S))
        d.duration_s = float(duration)
        d.s_eff = s_eff
        d.would_fire = True
        d.allowed = False
        d.deny.append("dry-run：未接设备（would-fire）")

        # 记账（模拟"如果真输出"）
        self.st.tokens -= 1
        self.st.last_output_s = now_s
        self.st.last_kind_s[event.kind] = now_s
        self.st.hourly.append(now_s)
        self.st.failure_streak = self.st.failure_streak + 1 if event.kind != "verdict.ac" else 0
        self.st.outputs_without_ac += 1
        return d
