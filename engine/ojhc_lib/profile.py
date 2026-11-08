"""设备档案与用户配置的读写（纯 JSON，避免额外依赖）。

文件位置（默认 ~/.ojhc/，可用 --profile-dir 覆盖，便于测试）：
    device_profile.json   校准结果：感知区间、上限、通道、预设
    config.json           用户配置：预设、通道、平台参数
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .util import now_iso

_LOCAL_FALLBACK = Path(__file__).resolve().parents[1] / ".local" / "profile"


def default_dir() -> Path:
    """默认 ~/.ojhc/；不可写时回退到 engine/.local/profile（沙箱/受限环境）。"""
    preferred = Path(os.environ.get("OJHC_HOME") or (Path.home() / ".ojhc"))
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        return preferred
    except OSError:
        _LOCAL_FALLBACK.mkdir(parents=True, exist_ok=True)
        return _LOCAL_FALLBACK


# 兼容旧引用（优先使用 default_dir()）
DEFAULT_DIR = Path(os.environ.get("OJHC_HOME") or (Path.home() / ".ojhc"))

HARD_CAP_PCT = 90          # 代码级硬上限：最多用到 App 上限的 90%（App 上限本身是安全边界）
MIN_PULSE_S = 0.3
MAX_PULSE_S = 3.0
PROFILE_EXPIRE_DAYS = 30


@dataclass
class DeviceProfile:
    version: int = 1
    calibrated_at: str = ""
    device_type: str = ""
    device_max: int = 0           # 校准时设备上限（max_a）；用于把百分比换算成绝对强度
    channel: str = "both"
    perceptible_pct: int = 0      # I_p：刚能感觉到
    comfort_pct: int = 0          # I_c：舒适上限
    user_max_pct: int = 0         # = min(I_c * 0.8, HARD_CAP_PCT)
    preset: str = ""
    notes: str = ""

    @property
    def span(self) -> int:
        return max(0, self.comfort_pct - self.perceptible_pct)

    def is_valid(self) -> bool:
        # comfort_pct 是「设备上限的百分比」，可以到 100；真正封顶的是 user_max_pct
        return (
            self.version == 1
            and 0 < self.perceptible_pct < self.comfort_pct <= 100
            and 0 < self.user_max_pct <= HARD_CAP_PCT
        )

    def age_days(self) -> float | None:
        if not self.calibrated_at:
            return None
        import datetime as dt

        try:
            then = dt.datetime.fromisoformat(self.calibrated_at)
        except ValueError:
            return None
        now = dt.datetime.now().astimezone()
        return (now - then.astimezone()).total_seconds() / 86400.0

    def expired(self, days: int = PROFILE_EXPIRE_DAYS) -> bool:
        age = self.age_days()
        return age is not None and age > days

    def intensity_for(self, frac: float) -> int:
        """按感受比例映射到设备强度（§7.3）。

        映射区间是 [I_p, user_max]，而不是 [I_p, I_c]：
        否则 user_max = 0.8*I_c 会把高档位全部压到同一个值（实测过的坑）。
        下限保证不低于 I_p —— 低于感知阈值的输出等于没输出。
        """
        frac = max(0.0, min(1.0, frac))
        lo = self.perceptible_pct
        hi = max(lo, self.user_max_pct)
        value = lo + (hi - lo) * frac
        return int(round(min(value, self.user_max_pct, HARD_CAP_PCT)))

    @property
    def usable_span(self) -> int:
        """可用于区分档位的强度区间（user_max - I_p）。太窄就没有手感层次。"""
        return max(0, self.user_max_pct - self.perceptible_pct)


@dataclass
class UserConfig:
    version: int = 1
    preset: str = "casual"           # casual | hardcore
    channel: str = "both"
    allow_device_on_dom_source: bool = False
    feedback: dict = field(default_factory=lambda: {"audio": True, "enable_ac_reward": False})
    platforms: dict = field(default_factory=lambda: {
        "codeforces": {"poll_standings_s": 60, "poll_submissions_s": 5},
    })


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def profile_path(base: Path | None = None) -> Path:
    return (base or default_dir()) / "device_profile.json"


def config_path(base: Path | None = None) -> Path:
    return (base or default_dir()) / "config.json"


def load_profile(base: Path | None = None) -> DeviceProfile | None:
    path = profile_path(base)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    allowed = {f for f in DeviceProfile.__dataclass_fields__}
    return DeviceProfile(**{k: v for k, v in data.items() if k in allowed})


def save_profile(profile: DeviceProfile, base: Path | None = None) -> Path:
    ensure_dir(base or default_dir())
    path = profile_path(base)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(profile), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def load_config(base: Path | None = None) -> UserConfig:
    path = config_path(base)
    if not path.exists():
        return UserConfig()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return UserConfig()
    allowed = {f for f in UserConfig.__dataclass_fields__}
    return UserConfig(**{k: v for k, v in data.items() if k in allowed})


def save_config(cfg: UserConfig, base: Path | None = None) -> Path:
    ensure_dir(base or default_dir())
    path = config_path(base)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(cfg), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def new_profile(perceptible: int, comfort: int, device_type: str,
                channel: str, preset: str, device_max: int = 0) -> DeviceProfile:
    user_max = min(int(comfort * 0.8), HARD_CAP_PCT)
    return DeviceProfile(
        calibrated_at=now_iso(),
        device_type=device_type,
        device_max=int(device_max or 0),
        channel=channel,
        perceptible_pct=perceptible,
        comfort_pct=comfort,
        user_max_pct=user_max,
        preset=preset,
    )
