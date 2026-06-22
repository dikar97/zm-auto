"""utils/memory_predictor 单元测试。"""
from __future__ import annotations

import sys
import time
from unittest.mock import patch

import pytest

from utils import memory_predictor as mp
from utils.memory_predictor import (
    MemoryHistory,
    MemorySnapshot,
    Recommendation,
    get_memory_snapshot,
    recommend_threads,
)


# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------
class _FakeVM:
    def __init__(self, percent: float, total: int = 1024 * 1024 * 1024) -> None:
        self.percent = percent
        self.total = total
        self.used = int(total * percent / 100)
        self.available = total - self.used


def _make_snapshot(percent: float) -> MemorySnapshot:
    return MemorySnapshot(
        percent=percent,
        used_mb=float(percent) * 10,
        total_mb=1000,
        available_mb=1000 - float(percent) * 10,
        timestamp=time.time(),
    )


# ---------------------------------------------------------------------------
# get_memory_snapshot
# ---------------------------------------------------------------------------
class TestGetSnapshot:
    def test_psutil_present(self) -> None:
        with patch.object(mp, "_has_psutil", True):
            with patch("psutil.virtual_memory", return_value=_FakeVM(65.0)):
                snap = get_memory_snapshot()
        assert snap is not None
        assert snap.percent == pytest.approx(65.0)
        assert snap.total_mb == pytest.approx(1024.0)
        assert snap.used_mb == pytest.approx(665.6, rel=1e-2)
        assert snap.available_mb == pytest.approx(358.4, rel=1e-2)
        assert snap.timestamp > 0

    def test_psutil_absent(self) -> None:
        with patch.object(mp, "_has_psutil", False):
            assert get_memory_snapshot() is None


# ---------------------------------------------------------------------------
# recommend_threads
# ---------------------------------------------------------------------------
class TestRecommend:
    def test_snapshot_none(self) -> None:
        assert recommend_threads(5, None) is None

    def test_ok(self) -> None:
        rec = recommend_threads(5, _make_snapshot(50.0))
        assert rec is not None
        assert rec.level == "ok"
        assert rec.suggested_threads == 5
        assert rec.current_threads == 5

    def test_medium(self) -> None:
        rec = recommend_threads(5, _make_snapshot(72.0))
        assert rec is not None
        assert rec.level == "medium"
        assert rec.suggested_threads == 4

    def test_medium_clamp(self) -> None:
        rec = recommend_threads(1, _make_snapshot(72.0))
        assert rec is not None
        assert rec.level == "medium"
        assert rec.suggested_threads == 1

    def test_high(self) -> None:
        rec = recommend_threads(8, _make_snapshot(85.0))
        assert rec is not None
        assert rec.level == "high"
        assert rec.suggested_threads == 4

    def test_high_clamp(self) -> None:
        rec = recommend_threads(1, _make_snapshot(85.0))
        assert rec is not None
        assert rec.level == "high"
        assert rec.suggested_threads == 1

    def test_critical(self) -> None:
        rec = recommend_threads(8, _make_snapshot(95.0))
        assert rec is not None
        assert rec.level == "critical"
        assert rec.suggested_threads == 2

    def test_critical_clamp(self) -> None:
        rec = recommend_threads(3, _make_snapshot(95.0))
        assert rec is not None
        assert rec.level == "critical"
        assert rec.suggested_threads == 1

    def test_boundary_70(self) -> None:
        rec = recommend_threads(5, _make_snapshot(70.0))
        assert rec is not None
        assert rec.level == "medium"

    def test_boundary_80(self) -> None:
        rec = recommend_threads(8, _make_snapshot(80.0))
        assert rec is not None
        assert rec.level == "high"

    def test_boundary_90(self) -> None:
        rec = recommend_threads(8, _make_snapshot(90.0))
        assert rec is not None
        assert rec.level == "critical"

    def test_reason_text(self) -> None:
        rec = recommend_threads(5, _make_snapshot(75.0))
        assert rec is not None
        assert "75%" in rec.reason


# ---------------------------------------------------------------------------
# MemoryHistory
# ---------------------------------------------------------------------------
class TestMemoryHistory:
    def test_default_max(self) -> None:
        h = MemoryHistory()
        assert h.max_samples == 60
        assert len(h) == 0

    def test_custom_max(self) -> None:
        h = MemoryHistory(max_samples=3)
        assert h.max_samples == 3

    def test_clamp_max(self) -> None:
        h = MemoryHistory(max_samples=0)
        assert h.max_samples == 1

    def test_add_latest(self) -> None:
        h = MemoryHistory(max_samples=5)
        assert h.latest() is None
        s1 = _make_snapshot(50.0)
        h.add(s1)
        assert h.latest() is s1
        s2 = _make_snapshot(60.0)
        h.add(s2)
        assert h.latest() is s2
        assert len(h) == 2

    def test_evict_oldest(self) -> None:
        h = MemoryHistory(max_samples=2)
        h.add(_make_snapshot(10.0))
        h.add(_make_snapshot(20.0))
        h.add(_make_snapshot(30.0))
        assert len(h) == 2
        latest = h.latest()
        assert latest is not None
        assert latest.percent == 30.0

    def test_clear(self) -> None:
        h = MemoryHistory(max_samples=5)
        h.add(_make_snapshot(50.0))
        h.clear()
        assert len(h) == 0
        assert h.latest() is None

    def test_trend_empty(self) -> None:
        h = MemoryHistory()
        assert h.trend() is None

    def test_trend_single(self) -> None:
        h = MemoryHistory()
        h.add(_make_snapshot(50.0))
        assert h.trend() is None

    def test_trend_flat(self) -> None:
        h = MemoryHistory()
        for _ in range(5):
            h.add(_make_snapshot(50.0))
        trend = h.trend()
        assert trend is not None
        assert trend == pytest.approx(0.0)

    def test_trend_rising(self) -> None:
        h = MemoryHistory()
        for p in (10.0, 20.0, 30.0, 40.0):
            h.add(_make_snapshot(p))
        trend = h.trend()
        assert trend is not None
        assert trend > 0
        assert trend == pytest.approx(10.0)

    def test_trend_falling(self) -> None:
        h = MemoryHistory()
        for p in (40.0, 30.0, 20.0, 10.0):
            h.add(_make_snapshot(p))
        trend = h.trend()
        assert trend is not None
        assert trend < 0
        assert trend == pytest.approx(-10.0)

    def test_trend_window(self) -> None:
        h = MemoryHistory()
        for p in (10.0, 20.0, 30.0, 40.0, 50.0):
            h.add(_make_snapshot(p))
        trend = h.trend(window=2)
        assert trend is not None
        assert trend == pytest.approx(10.0)

    def test_trend_window_clamp(self) -> None:
        h = MemoryHistory()
        h.add(_make_snapshot(10.0))
        h.add(_make_snapshot(20.0))
        trend = h.trend(window=1)
        assert trend is not None
        assert trend == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# Recommendation dataclass
# ---------------------------------------------------------------------------
class TestRecommendationDataclass:
    def test_fields(self) -> None:
        rec = Recommendation(
            level="ok",
            suggested_threads=3,
            current_threads=3,
            reason="ok",
        )
        assert rec.level == "ok"
        assert rec.suggested_threads == 3
        assert rec.current_threads == 3
        assert rec.reason == "ok"
