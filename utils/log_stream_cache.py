"""
本模块仅供学习与交流用途，请遵守当地法律法规，不得用于任何违反法律或第三方服务条款的场景。
作者不对任何因滥用本代码造成的后果承担责任。

日志流缓存：带自增 ID 的 ring buffer，支持按 last_id 增量获取。
当前 server.py 的 SSE 已用 deque(maxlen=500) 解决重连丢日志，
本模块为未来前端"断线重连只取增量"优化预留，不强制接入。
"""
from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class LogEntry:
    """单条日志的解析结果。"""
    id: int
    ts: str
    text: str
    color: str
    raw: str


def parse_log_line(raw: str | None) -> dict[str, Any]:
    """解析 server._broadcast 写入的 JSON 日志行 {ts, text, color}。
    非合法 JSON 或缺字段时返 {parsed: False, raw}。
    """
    try:
        data = json.loads(raw) if raw is not None else None
    except (json.JSONDecodeError, TypeError):
        return {"parsed": False, "raw": raw}
    if not isinstance(data, dict) or "text" not in data:
        return {"parsed": False, "raw": raw}
    return {
        "parsed": True,
        "ts": str(data.get("ts", "")),
        "text": str(data.get("text", "")),
        "color": str(data.get("color", "")),
        "raw": raw,
    }


class LogRingBuffer:
    """带自增 ID 的环形日志缓冲。

    - append(raw) 解析 JSON 并分配递增 ID（从 1 开始）
    - since(last_id) 返回 ID > last_id 的条目（用于增量获取）
    - recent(limit) 返回最近 limit 条（按时间顺序，旧→新）
    """

    def __init__(self, max_size: int = 500) -> None:
        self._max_size = max(1, int(max_size))
        self._entries: deque[LogEntry] = deque(maxlen=self._max_size)
        self._next_id = 1

    def append(self, raw: str) -> LogEntry:
        parsed = parse_log_line(raw)
        entry = LogEntry(
            id=self._next_id,
            ts=parsed.get("ts", "") if parsed["parsed"] else "",
            text=parsed.get("text", "") if parsed["parsed"] else raw,
            color=parsed.get("color", "") if parsed["parsed"] else "",
            raw=raw,
        )
        self._entries.append(entry)
        self._next_id += 1
        return entry

    def extend(self, lines: Iterable[str]) -> list[LogEntry]:
        return [self.append(line) for line in lines]

    def recent(self, limit: int = 100) -> list[LogEntry]:
        limit = max(0, int(limit))
        if limit == 0:
            return []
        entries = list(self._entries)
        if len(entries) <= limit:
            return entries
        return entries[-limit:]

    def since(self, last_id: int, limit: int = 500) -> list[LogEntry]:
        if last_id < 0:
            last_id = 0
        limit = max(0, int(limit))
        if limit == 0:
            return []
        result: list[LogEntry] = []
        for entry in self._entries:
            if entry.id > last_id:
                result.append(entry)
                if len(result) >= limit:
                    break
        return result

    @property
    def max_size(self) -> int:
        return self._max_size

    @property
    def last_id(self) -> int:
        return self._next_id - 1

    def __len__(self) -> int:
        return len(self._entries)

    def clear(self) -> None:
        self._entries.clear()
        self._next_id = 1
