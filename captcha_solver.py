"""Captcha solver via 2captcha API — no browser needed.

Solves both Cloudflare Turnstile and Google reCAPTCHA v2 by submitting
to 2captcha and polling for the token. The returned tokens are then
used directly in HTTP API calls (curl_cffi), bypassing the need for a
real browser entirely.

Env vars (or pass directly):
    CAPTCHA_API_KEY   — 2captcha API key
    CAPTCHA_PROVIDER  — "2captcha" (default) or "anticaptcha"
"""
from __future__ import annotations

import os
import time
from typing import Any

import requests

# Target captchas (from protocol analysis + browser debug)
TURNSTILE_SITE_KEY = "0x4AAAAAAB3vWB8HhhtIcASj"
RECAPTCHA_SITE_KEY = "6LdN_REsAAAAAKSlH2k4VNXoCT-Fi1bv_Ufaf86t"


class CaptchaSolver:
    """2captcha-based solver. No browser, pure HTTP."""

    def __init__(self, api_key: str = "", provider: str = "2captcha"):
        self.api_key = api_key or os.environ.get("CAPTCHA_API_KEY", "")
        self.provider = (provider or os.environ.get("CAPTCHA_PROVIDER", "2captcha")).lower()
        if not self.api_key:
            raise RuntimeError("CaptchaSolver 需要 api_key (2captcha)")

    # ------------------------------------------------------------------ #
    # Turnstile
    # ------------------------------------------------------------------ #
    def solve_turnstile(self, page_url: str = "https://example.com/login", timeout: int = 120) -> str:
        """Submit Cloudflare Turnstile to 2captcha and return the token."""
        if self.provider == "2captcha":
            return self._solve_2captcha_turnstile(page_url, timeout)
        raise RuntimeError(f"Turnstile 暂不支持 provider: {self.provider}")

    def _solve_2captcha_turnstile(self, page_url: str, timeout: int) -> str:
        r = requests.post(
            "https://2captcha.com/in.php",
            data={
                "key": self.api_key,
                "method": "turnstile",
                "sitekey": TURNSTILE_SITE_KEY,
                "pageurl": page_url,
                "json": 1,
            },
            timeout=30,
        )
        data = r.json()
        if data.get("status") != 1:
            raise RuntimeError(f"2captcha Turnstile 提交失败: {data}")
        task_id = data["request"]
        return self._poll_2captcha(task_id, timeout)

    # ------------------------------------------------------------------ #
    # reCAPTCHA v2
    # ------------------------------------------------------------------ #
    def solve_recaptcha(self, page_url: str = "https://example.com/verify", timeout: int = 180) -> str:
        """Submit Google reCAPTCHA v2 to 2captcha/anticaptcha and return token."""
        if self.provider == "2captcha":
            return self._solve_2captcha_recaptcha(page_url, timeout)
        if self.provider == "anticaptcha":
            return self._solve_anticaptcha_recaptcha(page_url, timeout)
        raise RuntimeError(f"reCAPTCHA 暂不支持 provider: {self.provider}")

    def _solve_2captcha_recaptcha(self, page_url: str, timeout: int) -> str:
        r = requests.get(
            "https://2captcha.com/in.php",
            params={
                "key": self.api_key,
                "method": "userrecaptcha",
                "googlekey": RECAPTCHA_SITE_KEY,
                "pageurl": page_url,
                "json": 1,
            },
            timeout=30,
        )
        data = r.json()
        if data.get("status") != 1:
            raise RuntimeError(f"2captcha reCAPTCHA 提交失败: {data}")
        task_id = data["request"]
        return self._poll_2captcha(task_id, timeout)

    def _solve_anticaptcha_recaptcha(self, page_url: str, timeout: int) -> str:
        r = requests.post(
            "https://api.anti-captcha.com/createTask",
            json={
                "clientKey": self.api_key,
                "task": {
                    "type": "NoCaptchaTaskProxyless",
                    "websiteURL": page_url,
                    "websiteKey": RECAPTCHA_SITE_KEY,
                },
            },
            timeout=30,
        )
        data = r.json()
        if data.get("errorId"):
            raise RuntimeError(f"anticaptcha 提交失败: {data.get('errorDescription')}")
        task_id = data["taskId"]
        return self._poll_anticaptcha(task_id, timeout)

    # ------------------------------------------------------------------ #
    # Polling
    # ------------------------------------------------------------------ #
    def _poll_2captcha(self, task_id: str, timeout: int) -> str:
        deadline = time.time() + timeout
        time.sleep(5)  # initial wait
        while time.time() < deadline:
            r = requests.get(
                "https://2captcha.com/res.php",
                params={"key": self.api_key, "action": "get", "id": task_id, "json": 1},
                timeout=30,
            )
            data = r.json()
            if data.get("status") == 1:
                return str(data["request"])
            if data.get("request") != "CAPCHA_NOT_READY":
                raise RuntimeError(f"2captcha 轮询失败: {data}")
            time.sleep(5)
        raise RuntimeError(f"2captcha 超时 ({timeout}s)")

    def _poll_anticaptcha(self, task_id: str, timeout: int) -> str:
        deadline = time.time() + timeout
        time.sleep(5)
        while time.time() < deadline:
            r = requests.post(
                "https://api.anti-captcha.com/getTaskResult",
                json={"clientKey": self.api_key, "taskId": task_id},
                timeout=30,
            )
            data = r.json()
            if data.get("errorId"):
                raise RuntimeError(f"anticaptcha 轮询失败: {data.get('errorDescription')}")
            if data.get("status") == "ready":
                return str(data["solution"]["gRecaptchaResponse"])
            time.sleep(5)
        raise RuntimeError(f"anticaptcha 超时 ({timeout}s)")
