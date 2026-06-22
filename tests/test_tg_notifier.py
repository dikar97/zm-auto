"""utils/tg_notifier.py 单元测试。"""
from __future__ import annotations

from typing import Any

import pytest

import utils.tg_notifier as tg_notifier
from utils.tg_notifier import TgConfig, TgNotifier


class _FakeResp:
    def __init__(self, ok: bool = True) -> None:
        self._ok = ok
        self.status_code = 200

    def json(self) -> dict[str, Any]:
        return {"ok": self._ok}


class TestTgConfig:
    def test_defaults(self) -> None:
        cfg = TgConfig()
        assert cfg.enable is False
        assert cfg.token == ""
        assert cfg.chat_id == ""
        assert cfg.mask_email is True
        assert cfg.mask_password is True


class TestFromConfig:
    def test_missing_section(self) -> None:
        assert TgNotifier.from_config({}) is None

    def test_not_dict(self) -> None:
        assert TgNotifier.from_config({"tg_bot": "not dict"}) is None

    def test_disabled(self) -> None:
        cfg = {"tg_bot": {"enable": False, "token": "T", "chat_id": "C"}}
        assert TgNotifier.from_config(cfg) is None

    def test_missing_token(self) -> None:
        cfg = {"tg_bot": {"enable": True, "chat_id": "C"}}
        assert TgNotifier.from_config(cfg) is None

    def test_missing_chat_id(self) -> None:
        cfg = {"tg_bot": {"enable": True, "token": "T"}}
        assert TgNotifier.from_config(cfg) is None

    def test_empty_strings(self) -> None:
        cfg = {"tg_bot": {"enable": True, "token": "  ", "chat_id": "C"}}
        assert TgNotifier.from_config(cfg) is None

    def test_full_config(self) -> None:
        cfg = {
            "tg_bot": {
                "enable": True,
                "token": "123:ABC",
                "chat_id": "98765",
                "mask_email": False,
                "mask_password": False,
            }
        }
        notifier = TgNotifier.from_config(cfg)
        assert notifier is not None
        assert notifier.config.token == "123:ABC"
        assert notifier.config.chat_id == "98765"
        assert notifier.config.mask_email is False
        assert notifier.config.mask_password is False

    def test_default_mask_flags(self) -> None:
        cfg = {"tg_bot": {"enable": True, "token": "T", "chat_id": "C"}}
        notifier = TgNotifier.from_config(cfg)
        assert notifier is not None
        assert notifier.config.mask_email is True
        assert notifier.config.mask_password is True


class TestSend:
    def test_send_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        called: list[Any] = []

        def fake_post(url: str, **kw: Any) -> _FakeResp:
            called.append((url, kw))
            return _FakeResp(ok=True)

        monkeypatch.setattr(tg_notifier.requests, "post", fake_post)
        notifier = TgNotifier(TgConfig(enable=False, token="T", chat_id="C"))
        assert notifier.send("hi") is False
        assert called == []

    def test_send_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        def fake_post(url: str, **kw: Any) -> _FakeResp:
            captured["url"] = url
            captured["payload"] = kw.get("json")
            return _FakeResp(ok=True)

        monkeypatch.setattr(tg_notifier.requests, "post", fake_post)
        notifier = TgNotifier(TgConfig(enable=True, token="123:ABC", chat_id="98765"))
        assert notifier.send("hello") is True
        assert captured["url"] == "https://api.telegram.org/bot123:ABC/sendMessage"
        assert captured["payload"]["chat_id"] == "98765"
        assert captured["payload"]["text"] == "hello"
        assert captured["payload"]["disable_web_page_preview"] is True

    def test_send_api_returns_not_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_post(url: str, **kw: Any) -> _FakeResp:
            return _FakeResp(ok=False)

        monkeypatch.setattr(tg_notifier.requests, "post", fake_post)
        notifier = TgNotifier(TgConfig(enable=True, token="T", chat_id="C"))
        assert notifier.send("hi") is False

    def test_send_network_exception_silent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_post(url: str, **kw: Any) -> _FakeResp:
            raise ConnectionError("network down")

        monkeypatch.setattr(tg_notifier.requests, "post", fake_post)
        notifier = TgNotifier(TgConfig(enable=True, token="T", chat_id="C"))
        assert notifier.send("hi") is False


class TestSendSuccess:
    def test_format_with_masking(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        def fake_post(url: str, **kw: Any) -> _FakeResp:
            captured["payload"] = kw.get("json")
            return _FakeResp(ok=True)

        monkeypatch.setattr(tg_notifier.requests, "post", fake_post)
        notifier = TgNotifier(TgConfig(enable=True, token="T", chat_id="C"))
        account = {
            "email": "alice@example.com",
            "api_key": "sk-abcdef123456789",
            "user_id": "u123",
            "proxy_name": "node-1",
            "elapsed_sec": 12.5,
        }
        assert notifier.send_success(account) is True
        text = captured["payload"]["text"]
        assert "✅" in text
        assert "alice@example.com" not in text
        assert "a**@example.com" in text
        assert "sk-abcdef123456789" not in text
        assert "sk-a" in text
        assert "u123" in text
        assert "node-1" in text
        assert "12.5" in text

    def test_mask_email_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        def fake_post(url: str, **kw: Any) -> _FakeResp:
            captured["payload"] = kw.get("json")
            return _FakeResp(ok=True)

        monkeypatch.setattr(tg_notifier.requests, "post", fake_post)
        notifier = TgNotifier(
            TgConfig(enable=True, token="T", chat_id="C", mask_email=False, mask_password=False)
        )
        account = {"email": "bob@example.com", "password": "secret123"}
        assert notifier.send_success(account) is True
        text = captured["payload"]["text"]
        assert "bob@example.com" in text
        assert "secret123" not in text

    def test_minimal_account(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        def fake_post(url: str, **kw: Any) -> _FakeResp:
            captured["payload"] = kw.get("json")
            return _FakeResp(ok=True)

        monkeypatch.setattr(tg_notifier.requests, "post", fake_post)
        notifier = TgNotifier(TgConfig(enable=True, token="T", chat_id="C"))
        assert notifier.send_success({}) is True
        text = captured["payload"]["text"]
        assert "✅" in text
        assert "邮箱:" in text


class TestSendFailure:
    def test_format(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        def fake_post(url: str, **kw: Any) -> _FakeResp:
            captured["payload"] = kw.get("json")
            return _FakeResp(ok=True)

        monkeypatch.setattr(tg_notifier.requests, "post", fake_post)
        notifier = TgNotifier(TgConfig(enable=True, token="T", chat_id="C"))
        assert notifier.send_failure("captcha timeout", {"email": "x@example.com", "index": 3}) is True
        text = captured["payload"]["text"]
        assert "❌" in text
        assert "captcha timeout" in text
        assert "x@example.com" not in text
        assert "x**@example.com" in text
        assert "3" in text

    def test_no_context(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        def fake_post(url: str, **kw: Any) -> _FakeResp:
            captured["payload"] = kw.get("json")
            return _FakeResp(ok=True)

        monkeypatch.setattr(tg_notifier.requests, "post", fake_post)
        notifier = TgNotifier(TgConfig(enable=True, token="T", chat_id="C"))
        assert notifier.send_failure("boom") is True
        text = captured["payload"]["text"]
        assert "❌" in text
        assert "boom" in text
