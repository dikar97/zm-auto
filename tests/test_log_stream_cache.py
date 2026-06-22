import json

from utils.log_stream_cache import LogEntry, LogRingBuffer, parse_log_line


def _make_raw(text: str = "hello", ts: str = "12:00:00", color: str = "green") -> str:
    return json.dumps({"ts": ts, "text": text, "color": color}, ensure_ascii=False)


class TestParseLogLine:
    def test_valid_json(self):
        result = parse_log_line(_make_raw("验证码 123456"))
        assert result["parsed"] is True
        assert result["ts"] == "12:00:00"
        assert result["text"] == "验证码 123456"
        assert result["color"] == "green"

    def test_invalid_json(self):
        result = parse_log_line("not a json string")
        assert result["parsed"] is False
        assert result["raw"] == "not a json string"

    def test_missing_text_field(self):
        raw = json.dumps({"ts": "12:00:00", "color": "red"})
        result = parse_log_line(raw)
        assert result["parsed"] is False
        assert result["raw"] == raw

    def test_not_dict(self):
        raw = json.dumps([1, 2, 3])
        result = parse_log_line(raw)
        assert result["parsed"] is False

    def test_none_input(self):
        result = parse_log_line(None)
        assert result["parsed"] is False


class TestLogRingBufferAppend:
    def test_append_returns_entry_with_incrementing_id(self):
        buf = LogRingBuffer(max_size=10)
        e1 = buf.append(_make_raw("a"))
        e2 = buf.append(_make_raw("b"))
        e3 = buf.append(_make_raw("c"))
        assert e1.id == 1
        assert e2.id == 2
        assert e3.id == 3
        assert e1.text == "a"
        assert e3.ts == "12:00:00"

    def test_append_invalid_json_keeps_raw(self):
        buf = LogRingBuffer()
        entry = buf.append("broken line")
        assert entry.id == 1
        assert entry.text == "broken line"
        assert entry.ts == ""
        assert entry.color == ""

    def test_extend_returns_list(self):
        buf = LogRingBuffer()
        entries = buf.extend([_make_raw("x"), _make_raw("y"), _make_raw("z")])
        assert len(entries) == 3
        assert [e.id for e in entries] == [1, 2, 3]


class TestLogRingBufferRecent:
    def test_recent_empty(self):
        buf = LogRingBuffer()
        assert buf.recent(10) == []

    def test_recent_limit_zero(self):
        buf = LogRingBuffer()
        buf.append(_make_raw("a"))
        assert buf.recent(0) == []

    def test_recent_returns_all_when_below_limit(self):
        buf = LogRingBuffer()
        buf.extend([_make_raw("a"), _make_raw("b")])
        recent = buf.recent(10)
        assert [e.text for e in recent] == ["a", "b"]

    def test_recent_returns_last_n(self):
        buf = LogRingBuffer()
        buf.extend([_make_raw(str(i)) for i in range(5)])
        recent = buf.recent(2)
        assert [e.text for e in recent] == ["3", "4"]

    def test_recent_preserves_chronological_order(self):
        buf = LogRingBuffer()
        buf.extend([_make_raw("old"), _make_raw("new")])
        recent = buf.recent(10)
        assert recent[0].text == "old"
        assert recent[-1].text == "new"


class TestLogRingBufferSince:
    def test_since_zero_returns_all(self):
        buf = LogRingBuffer()
        buf.extend([_make_raw("a"), _make_raw("b"), _make_raw("c")])
        result = buf.since(0)
        assert [e.id for e in result] == [1, 2, 3]

    def test_since_returns_entries_after_last_id(self):
        buf = LogRingBuffer()
        buf.extend([_make_raw("a"), _make_raw("b"), _make_raw("c"), _make_raw("d")])
        result = buf.since(2)
        assert [e.id for e in result] == [3, 4]

    def test_since_at_last_returns_empty(self):
        buf = LogRingBuffer()
        buf.extend([_make_raw("a"), _make_raw("b")])
        result = buf.since(2)
        assert result == []

    def test_since_beyond_last_returns_empty(self):
        buf = LogRingBuffer()
        buf.extend([_make_raw("a"), _make_raw("b")])
        result = buf.since(999)
        assert result == []

    def test_since_negative_last_id_treated_as_zero(self):
        buf = LogRingBuffer()
        buf.extend([_make_raw("a"), _make_raw("b")])
        result = buf.since(-5)
        assert [e.id for e in result] == [1, 2]

    def test_since_limit_caps_result(self):
        buf = LogRingBuffer()
        buf.extend([_make_raw(str(i)) for i in range(10)])
        result = buf.since(0, limit=3)
        assert [e.id for e in result] == [1, 2, 3]

    def test_since_limit_zero_returns_empty(self):
        buf = LogRingBuffer()
        buf.append(_make_raw("a"))
        assert buf.since(0, limit=0) == []


class TestLogRingBufferProperties:
    def test_max_size_property(self):
        buf = LogRingBuffer(max_size=42)
        assert buf.max_size == 42

    def test_last_id_starts_zero(self):
        buf = LogRingBuffer()
        assert buf.last_id == 0

    def test_last_id_updates(self):
        buf = LogRingBuffer()
        buf.extend([_make_raw("a"), _make_raw("b"), _make_raw("c")])
        assert buf.last_id == 3

    def test_len(self):
        buf = LogRingBuffer()
        buf.extend([_make_raw("a"), _make_raw("b")])
        assert len(buf) == 2


class TestLogRingBufferEviction:
    def test_max_size_clamped_to_one(self):
        buf = LogRingBuffer(max_size=0)
        assert buf.max_size == 1
        buf.append(_make_raw("a"))
        assert len(buf) == 1

    def test_evicts_oldest_when_full(self):
        buf = LogRingBuffer(max_size=3)
        buf.extend([_make_raw("a"), _make_raw("b"), _make_raw("c"), _make_raw("d")])
        recent = buf.recent(10)
        assert [e.text for e in recent] == ["b", "c", "d"]
        assert len(buf) == 3

    def test_last_id_continues_after_eviction(self):
        buf = LogRingBuffer(max_size=2)
        buf.extend([_make_raw("a"), _make_raw("b"), _make_raw("c")])
        assert buf.last_id == 3
        result = buf.since(0)
        assert [e.id for e in result] == [2, 3]


class TestLogRingBufferClear:
    def test_clear_empties_entries(self):
        buf = LogRingBuffer()
        buf.extend([_make_raw("a"), _make_raw("b")])
        buf.clear()
        assert len(buf) == 0
        assert buf.recent(10) == []
        assert buf.last_id == 0

    def test_clear_resets_id_counter(self):
        buf = LogRingBuffer()
        buf.append(_make_raw("a"))
        buf.clear()
        e = buf.append(_make_raw("b"))
        assert e.id == 1


class TestLogEntryDataclass:
    def test_entry_is_frozen(self):
        entry = LogEntry(id=1, ts="t", text="x", color="c", raw="r")
        try:
            setattr(entry, "text", "mutated")
            assert False, "frozen dataclass should not allow mutation"
        except AttributeError:
            pass
