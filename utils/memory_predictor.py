"""
本模块仅供学习与交流用途，请遵守当地法律法规，不得用于任何违反法律或第三方服务条款的场景。
作者不对任何因滥用本代码造成的后果承担责任。

内存水位监控 + 线程数建议：基于 psutil 获取内存占用，给出降载建议。
psutil 缺失时所有函数静默返回 None，绝不阻塞主流程。
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Deque

_has_psutil: bool = False
try:
    import psutil  # type: ignore

    _has_psutil = True
except ImportError:
    pass


@dataclass
class MemorySnapshot:
    """单次内存采样。percent 为 0-100。"""
    percent: float
    used_mb: float
    total_mb: float
    available_mb: float
    timestamp: float


@dataclass
class Recommendation:
    """线程数调整建议。level 为 ok/medium/high/critical。"""
    level: str
    suggested_threads: int
    current_threads: int
    reason: str


def get_memory_snapshot() -> MemorySnapshot | None:
    """获取当前内存快照。psutil 缺失返 None。"""
    if not _has_psutil:
        return None
    import time

    import psutil  # type: ignore

    vm = psutil.virtual_memory()
    return MemorySnapshot(
        percent=float(vm.percent),
        used_mb=float(vm.used) / 1024.0 / 1024.0,
        total_mb=float(vm.total) / 1024.0 / 1024.0,
        available_mb=float(vm.available) / 1024.0 / 1024.0,
        timestamp=time.time(),
    )


def recommend_threads(current_threads: int, snapshot: MemorySnapshot | None) -> Recommendation | None:
    """根据内存水位给出线程数建议。snapshot=None 返 None。"""
    if snapshot is None:
        return None
    pct = snapshot.percent
    if pct >= 90:
        suggested = max(1, current_threads // 4)
        level = "critical"
        reason = f"内存 {pct:.0f}% ≥ 90%，建议大幅降载至 1/4"
    elif pct >= 80:
        suggested = max(1, current_threads // 2)
        level = "high"
        reason = f"内存 {pct:.0f}% ≥ 80%，建议降载至 1/2"
    elif pct >= 70:
        suggested = max(1, current_threads - 1)
        level = "medium"
        reason = f"内存 {pct:.0f}% ≥ 70%，建议减 1 线程"
    else:
        suggested = current_threads
        level = "ok"
        reason = f"内存 {pct:.0f}% 正常"
    if suggested == current_threads and level == "ok":
        reason = f"内存 {pct:.0f}% 正常，无需调整"
    return Recommendation(
        level=level,
        suggested_threads=suggested,
        current_threads=current_threads,
        reason=reason,
    )


@dataclass
class MemoryHistory:
    """固定容量的内存采样历史。线程不安全（调用方加锁）。"""
    max_samples: int = 60
    _samples: Deque[MemorySnapshot] = field(default_factory=deque)

    def __post_init__(self) -> None:
        if self.max_samples < 1:
            self.max_samples = 1
        self._samples = deque(maxlen=self.max_samples)

    def add(self, snapshot: MemorySnapshot) -> None:
        self._samples.append(snapshot)

    def latest(self) -> MemorySnapshot | None:
        if not self._samples:
            return None
        return self._samples[-1]

    def clear(self) -> None:
        self._samples.clear()

    def __len__(self) -> int:
        return len(self._samples)

    def trend(self, window: int = 10) -> float | None:
        """最近 window 个采样的趋势。返每样本平均变化百分点。
        正=上升，负=下降，None=样本不足（<2）。"""
        if window < 2:
            window = 2
        samples = list(self._samples)[-window:]
        if len(samples) < 2:
            return None
        diffs = [samples[i + 1].percent - samples[i].percent for i in range(len(samples) - 1)]
        return sum(diffs) / len(diffs)
