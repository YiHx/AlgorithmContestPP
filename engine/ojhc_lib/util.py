"""小工具：钳制、中位数、交互输入（支持脚本化，便于测试）、输出。"""
from __future__ import annotations

import datetime as _dt
import statistics
import sys
from typing import Iterable


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def clamp_int(value: int, lo: int, hi: int) -> int:
    return int(clamp(int(value), lo, hi))


def median(values: Iterable[float]) -> float:
    vals = list(values)
    if not vals:
        raise ValueError("median() of empty sequence")
    return float(statistics.median(vals))


def now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def is_tty() -> bool:
    return sys.stdout.isatty()


def ok(msg: str) -> str:
    return f"[OK]   {msg}"


def warn(msg: str) -> str:
    return f"[WARN] {msg}"


def fail(msg: str) -> str:
    return f"[FAIL] {msg}"


def info(msg: str) -> str:
    return f"[INFO] {msg}"


class Prompter:
    """交互输入；--script 时按顺序取答案，便于自动化测试。"""

    def __init__(self, script: list[str] | None = None) -> None:
        self._script = list(script or [])
        self.history: list[tuple[str, str]] = []

    def ask(self, question: str, default: str = "") -> str:
        if self._script:
            answer = self._script.pop(0)
            print(f"{question} {answer}   (script)")
        else:
            answer = input(f"{question} ").strip()
        if not answer:
            answer = default
        self.history.append((question, answer))
        return answer

    def confirm(self, question: str, default: bool = False) -> bool:
        hint = "[Y/n]" if default else "[y/N]"
        raw = self.ask(f"{question} {hint}", default="y" if default else "n").lower()
        return raw.startswith("y")

    def choose_int(self, question: str, lo: int, hi: int, default: int) -> int:
        raw = self.ask(f"{question}（{lo}~{hi}，回车={default}）", default=str(default))
        try:
            return clamp_int(int(raw), lo, hi)
        except ValueError:
            return default


def human_pct(pct: float) -> str:
    return f"{pct:.0f}%"
