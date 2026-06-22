"""captcha_solver 单元测试。

只测试不依赖网络的部分：
- CaptchaSolver 构造逻辑（api_key / provider 解析与 env 回退）
- solve_turnstile / solve_recaptcha 路由逻辑（未知 provider 报错）
- _poll_2captcha / _poll_anticaptcha（monkeypatch requests + time.sleep）
"""

from __future__ import annotations

from typing import Any

import pytest

import captcha_solver


# --------------------------------------------------------------------------- #
# CaptchaSolver 构造
# --------------------------------------------------------------------------- #
class TestSolverConstruction:
    def test_explicit_api_key_and_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CAPTCHA_API_KEY", raising=False)
        monkeypatch.delenv("CAPTCHA_PROVIDER", raising=False)
        solver = captcha_solver.CaptchaSolver(api_key="MY_KEY", provider="2captcha")
        assert solver.api_key == "MY_KEY"
        assert solver.provider == "2captcha"

    def test_env_fallback_for_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CAPTCHA_API_KEY", "ENV_KEY")
        monkeypatch.delenv("CAPTCHA_PROVIDER", raising=False)
        solver = captcha_solver.CaptchaSolver()
        assert solver.api_key == "ENV_KEY"

    def test_default_provider_is_2captcha(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CAPTCHA_API_KEY", raising=False)
        monkeypatch.delenv("CAPTCHA_PROVIDER", raising=False)
        solver = captcha_solver.CaptchaSolver(api_key="K")
        assert solver.provider == "2captcha"

    def test_missing_api_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CAPTCHA_API_KEY", raising=False)
        monkeypatch.delenv("CAPTCHA_PROVIDER", raising=False)
        with pytest.raises(RuntimeError, match="(?i)api.?key|missing|empty|没有"):
            captcha_solver.CaptchaSolver(api_key="")


# --------------------------------------------------------------------------- #
# solve_turnstile / solve_recaptcha 路由
# --------------------------------------------------------------------------- #
class TestRouteDispatch:
    def test_turnstile_unsupported_provider_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CAPTCHA_API_KEY", raising=False)
        monkeypatch.delenv("CAPTCHA_PROVIDER", raising=False)
        solver = captcha_solver.CaptchaSolver(api_key="K", provider="anticaptcha")
        with pytest.raises(RuntimeError):
            solver.solve_turnstile("https://example.com")

    def test_recaptcha_unknown_provider_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CAPTCHA_API_KEY", raising=False)
        monkeypatch.delenv("CAPTCHA_PROVIDER", raising=False)
        solver = captcha_solver.CaptchaSolver(api_key="K", provider="unknown_provider")
        with pytest.raises(RuntimeError):
            solver.solve_recaptcha("https://example.com")


# --------------------------------------------------------------------------- #
# _poll_2captcha
# --------------------------------------------------------------------------- #
class _FakeResponse:
    """模拟 requests.Response，只暴露 json() 与 status_code。"""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.status_code = 200

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        pass


class TestPoll2Captcha:
    def test_immediate_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # 第一次 GET res.php 直接返 status=1
        calls: list[str] = []

        def fake_get(url: str, params: dict[str, Any] | None = None, **kw: Any) -> _FakeResponse:
            calls.append(url)
            return _FakeResponse({"status": 1, "request": "TOKEN_OK"})

        monkeypatch.setattr(captcha_solver.requests, "get", fake_get)
        # 跳过初始 5s 等待与循环 5s 等待
        monkeypatch.setattr(captcha_solver.time, "sleep", lambda _s: None)

        solver = captcha_solver.CaptchaSolver(api_key="K", provider="2captcha")
        token = solver._poll_2captcha("task-123", timeout=30)
        assert token == "TOKEN_OK"
        assert len(calls) >= 1

    def test_not_ready_then_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        responses = [
            _FakeResponse({"status": 0, "request": "CAPCHA_NOT_READY"}),
            _FakeResponse({"status": 1, "request": "LATE_TOKEN"}),
        ]
        call_idx = {"i": 0}

        def fake_get(url: str, params: dict[str, Any] | None = None, **kw: Any) -> _FakeResponse:
            r = responses[call_idx["i"]]
            call_idx["i"] += 1
            return r

        monkeypatch.setattr(captcha_solver.requests, "get", fake_get)
        monkeypatch.setattr(captcha_solver.time, "sleep", lambda _s: None)

        solver = captcha_solver.CaptchaSolver(api_key="K", provider="2captcha")
        token = solver._poll_2captcha("task-456", timeout=30)
        assert token == "LATE_TOKEN"

    def test_error_response_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_get(url: str, params: dict[str, Any] | None = None, **kw: Any) -> _FakeResponse:
            return _FakeResponse({"status": 0, "request": "ERROR_KEY_DOES_NOT_EXIST"})

        monkeypatch.setattr(captcha_solver.requests, "get", fake_get)
        monkeypatch.setattr(captcha_solver.time, "sleep", lambda _s: None)

        solver = captcha_solver.CaptchaSolver(api_key="K", provider="2captcha")
        with pytest.raises(RuntimeError, match="ERROR_KEY_DOES_NOT_EXIST"):
            solver._poll_2captcha("task-789", timeout=30)

    def test_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # 永远返回 CAPCHA_NOT_READY，让 deadline 触发
        def fake_get(url: str, params: dict[str, Any] | None = None, **kw: Any) -> _FakeResponse:
            return _FakeResponse({"status": 0, "request": "CAPCHA_NOT_READY"})

        monkeypatch.setattr(captcha_solver.requests, "get", fake_get)
        # 让 time.monotonic 不增加（防止 deadline 提前触发）+ sleep 不阻塞
        monkeypatch.setattr(captcha_solver.time, "sleep", lambda _s: None)
        # 通过直接控制 deadline 缩短：设 timeout=0
        solver = captcha_solver.CaptchaSolver(api_key="K", provider="2captcha")
        with pytest.raises(RuntimeError):
            solver._poll_2captcha("task-timeout", timeout=0)


# --------------------------------------------------------------------------- #
# _poll_anticaptcha
# --------------------------------------------------------------------------- #
class TestPollAntiCaptcha:
    def test_immediate_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_post(url: str, json: Any = None, **kw: Any) -> _FakeResponse:
            return _FakeResponse(
                {"errorId": 0, "status": "ready", "solution": {"gRecaptchaResponse": "ANTI_TOKEN"}}
            )

        monkeypatch.setattr(captcha_solver.requests, "post", fake_post)
        monkeypatch.setattr(captcha_solver.time, "sleep", lambda _s: None)

        solver = captcha_solver.CaptchaSolver(api_key="K", provider="anticaptcha")
        token = solver._poll_anticaptcha("task-a1", timeout=30)
        assert token == "ANTI_TOKEN"

    def test_error_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_post(url: str, json: Any = None, **kw: Any) -> _FakeResponse:
            return _FakeResponse({"errorId": 1, "errorDescription": "bad key"})

        monkeypatch.setattr(captcha_solver.requests, "post", fake_post)
        monkeypatch.setattr(captcha_solver.time, "sleep", lambda _s: None)

        solver = captcha_solver.CaptchaSolver(api_key="K", provider="anticaptcha")
        with pytest.raises(RuntimeError, match="bad key"):
            solver._poll_anticaptcha("task-a2", timeout=30)

    def test_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_post(url: str, json: Any = None, **kw: Any) -> _FakeResponse:
            return _FakeResponse({"errorId": 0, "status": "processing"})

        monkeypatch.setattr(captcha_solver.requests, "post", fake_post)
        monkeypatch.setattr(captcha_solver.time, "sleep", lambda _s: None)

        solver = captcha_solver.CaptchaSolver(api_key="K", provider="anticaptcha")
        with pytest.raises(RuntimeError):
            solver._poll_anticaptcha("task-a3", timeout=0)


# --------------------------------------------------------------------------- #
# 模块常量
# --------------------------------------------------------------------------- #
class TestModuleConstants:
    def test_turnstile_site_key_is_string(self) -> None:
        assert isinstance(captcha_solver.TURNSTILE_SITE_KEY, str)
        assert len(captcha_solver.TURNSTILE_SITE_KEY) > 0

    def test_recaptcha_site_key_is_string(self) -> None:
        assert isinstance(captcha_solver.RECAPTCHA_SITE_KEY, str)
        assert len(captcha_solver.RECAPTCHA_SITE_KEY) > 0

    def test_site_keys_different(self) -> None:
        assert captcha_solver.TURNSTILE_SITE_KEY != captcha_solver.RECAPTCHA_SITE_KEY
