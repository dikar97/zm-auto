"""Account registrar — pure HTTP + 2captcha, no browser needed.

Flow:
  1. Create temp mailbox (7 providers)
  2. GET /login → obtain ctoken cookie (auto via curl_cffi)
  3. 2captcha solve Turnstile → turnstile_token
  4. POST /api/login/email/code/send {email, token} → sends code
  5. Mailbox: wait_for_code → 6-digit code
  6. POST /api/login/email/code/verify {email, code} → sets sessionId
  7. GET /api/user/info → check needVerify
  8. 2captcha solve reCAPTCHA v2 → recaptcha_token
  9. POST /api/login/recaptcha/verification {token} → unlocks whitelist
 10. POST /api/api_key/create {name, tags} → returns sk-ai-... key

All HTTP calls use curl_cffi (impersonate=chrome) to bypass TLS
fingerprinting. ctoken is captured automatically from Set-Cookie.
"""
from __future__ import annotations

import json
import random
import string
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import urllib3
from curl_cffi import requests as curl_requests

from mail_provider import create_mailbox, wait_for_code
from captcha_solver import CaptchaSolver
from sub2api_importer import Sub2APIImporter

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"

TARGET_BASE = "https://example.com"
API_BASE = f"{TARGET_BASE}/api"
X_API_VERSION = "2026-04-20"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

print_lock = threading.Lock()
stats_lock = threading.Lock()
stats = {"done": 0, "success": 0, "fail": 0, "start_time": 0.0}


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
DEFAULT_CONFIG: dict[str, Any] = {
    "mail": {
        "request_timeout": 30,
        "wait_timeout": 120,
        "wait_interval": 3,
        "user_agent": USER_AGENT,
        "providers": [],
    },
    "proxy": "",
    "total": 1,
    "threads": 1,
    "captcha": {
        "provider": "2captcha",
        "api_key": "",
    },
    "api_key_name": "auto",
    "sub2api": {
        "enabled": False,
        "base_url": "",
        "email": "",
        "password": "",
        "group_name": "auto",
        "concurrency": 3,
        "models": ["z-ai/glm-5.2-free", "moonshotai/kimi-k2.7-code-free"],
        "upstream_base_url": "https://example.com/api/anthropic",
    },
}


def load_config() -> dict:
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    if CONFIG_FILE.exists():
        saved = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        for key in ("mail", "proxy", "total", "threads", "captcha", "api_key_name", "sub2api"):
            if key in saved:
                config[key] = saved[key]
    return config


config = load_config()


# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
def log(text: str, color: str = "") -> None:
    colors = {"red": "\033[31m", "green": "\033[32m", "yellow": "\033[33m", "cyan": "\033[36m"}
    with print_lock:
        prefix = colors.get(color, "")
        suffix = "\033[0m" if prefix else ""
        print(f"{prefix}{datetime.now().strftime('%H:%M:%S')} {text}{suffix}", flush=True)


def step(index: int, text: str, color: str = "") -> None:
    log(f"[任务{index}] {text}", color)


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #
def _make_session(proxy: str = "") -> curl_requests.Session:
    session = curl_requests.Session(impersonate="chrome")
    if proxy:
        session.proxies = {"http": proxy, "https": proxy}
    return session


def _api_headers(referer: str = "") -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "x-api-version": X_API_VERSION,
        "User-Agent": USER_AGENT,
    }
    if referer:
        headers["Referer"] = referer
    return headers


class Registrar:
    """Handles one full registration attempt — pure HTTP."""

    def __init__(self, proxy: str = "", captcha_cfg: dict | None = None):
        self.proxy = proxy
        self.captcha_cfg = captcha_cfg or {}
        self.session = _make_session(proxy)
        self.ctoken = ""
        self.solver: CaptchaSolver | None = None
        self.mailbox: dict[str, Any] = {}

    def close(self) -> None:
        try:
            self.session.close()
        except Exception:
            pass

    @property
    def captcha(self) -> CaptchaSolver:
        if not self.solver:
            self.solver = CaptchaSolver(
                api_key=self.captcha_cfg.get("api_key", ""),
                provider=self.captcha_cfg.get("provider", "2captcha"),
            )
        return self.solver

    # ------------------------------------------------------------------ #
    # HTTP helpers
    # ------------------------------------------------------------------ #
    def _url(self, path: str) -> str:
        url = f"{API_BASE}{path}"
        if self.ctoken:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}ctoken={self.ctoken}"
        return url

    def _post(self, path: str, payload: dict | None = None, referer: str = "", expected: tuple[int, ...] = (200,)) -> dict:
        resp = self.session.post(
            self._url(path),
            headers=_api_headers(referer),
            json=payload,
            timeout=30,
            verify=False,
        )
        if resp.status_code not in expected:
            raise RuntimeError(f"POST {path} 失败: HTTP {resp.status_code}, body={resp.text[:500]}")
        try:
            return resp.json() if isinstance(resp.json(), dict) else {}
        except Exception:
            return {}

    def _get(self, path: str, referer: str = "", expected: tuple[int, ...] = (200,)) -> dict:
        resp = self.session.get(
            self._url(path),
            headers=_api_headers(referer),
            timeout=30,
            verify=False,
        )
        if resp.status_code not in expected:
            raise RuntimeError(f"GET {path} 失败: HTTP {resp.status_code}, body={resp.text[:500]}")
        try:
            return resp.json() if isinstance(resp.json(), dict) else {}
        except Exception:
            return {}

    # ------------------------------------------------------------------ #
    # Registration flow
    # ------------------------------------------------------------------ #
    def register(self, index: int) -> dict:
        # 1. Create mailbox
        step(index, "创建临时邮箱", "cyan")
        self.mailbox = create_mailbox(config["mail"])
        email = str(self.mailbox.get("address") or "").strip()
        if not email:
            raise RuntimeError("邮箱服务未返回 address")
        step(index, f"邮箱就绪: {email}", "green")

        # 2. Visit login page → get ctoken cookie (auto-captured by session)
        step(index, "获取 ctoken", "cyan")
        self.session.get(f"{TARGET_BASE}/login", headers={"User-Agent": USER_AGENT}, timeout=15, verify=False)
        self.ctoken = str(self.session.cookies.get("ctoken") or "")
        if not self.ctoken:
            raise RuntimeError("未能获取 ctoken cookie")
        step(index, f"ctoken: {self.ctoken}", "green")

        # 3. Solve Turnstile via 2captcha
        step(index, "2captcha 解 Turnstile", "cyan")
        turnstile_token = self.captcha.solve_turnstile(f"{TARGET_BASE}/login")
        step(index, f"Turnstile 通过 (len={len(turnstile_token)})", "green")

        # 4. Send email code
        step(index, "发送邮箱验证码", "cyan")
        send_resp = self._post(
            "/login/email/code/send",
            payload={"email": email, "token": turnstile_token},
            referer=f"{TARGET_BASE}/login",
        )
        if not send_resp.get("success"):
            raise RuntimeError(f"发送验证码失败: {send_resp}")
        expires_in = send_resp.get("data", {}).get("expiresIn", "?")
        step(index, f"验证码已发送 (有效期 {expires_in}s)", "green")

        # 5. Wait for code
        step(index, "等待邮箱验证码", "cyan")
        code = wait_for_code(config["mail"], self.mailbox)
        if not code:
            raise RuntimeError("等待验证码超时")
        step(index, f"收到验证码: {code}", "green")

        # 6. Verify code → login
        step(index, "验证码登录", "cyan")
        verify_resp = self._post(
            "/login/email/code/verify",
            payload={"email": email, "code": code},
            referer=f"{TARGET_BASE}/login",
        )
        if not verify_resp.get("success"):
            raise RuntimeError(f"验证码登录失败: {verify_resp}")
        is_new = bool(verify_resp.get("data", {}).get("isNew"))
        step(index, f"登录成功 (isNew={is_new})", "green")

        # 7. Check user info
        step(index, "获取用户信息", "cyan")
        user_info = self._get("/user/info", referer=f"{TARGET_BASE}/")
        user_data = user_info.get("data") or {}
        need_verify = bool(user_data.get("needVerify"))
        user_id = str(user_data.get("userId") or user_data.get("accountId") or "")
        step(index, f"userId={user_id}, needVerify={need_verify}", "green")

        # 8. reCAPTCHA (if needed)
        if need_verify:
            step(index, "2captcha 解 reCAPTCHA v2", "cyan")
            recaptcha_token = self.captcha.solve_recaptcha(f"{TARGET_BASE}/verify?method=unknown")
            step(index, f"reCAPTCHA 通过 (len={len(recaptcha_token)})", "green")

            # 9. Submit recaptcha verification
            step(index, "提交 reCAPTCHA 验证", "cyan")
            rc_resp = self._post(
                "/login/recaptcha/verification",
                payload={"token": recaptcha_token},
                referer=f"{TARGET_BASE}/verify?method=unknown",
            )
            if not rc_resp.get("success"):
                raise RuntimeError(f"reCAPTCHA 验证失败: {rc_resp}")
            step(index, "reCAPTCHA 验证成功", "green")

            # Re-check user info
            user_info = self._get("/user/info", referer=f"{TARGET_BASE}/")
            user_data = user_info.get("data") or {}
            if user_data.get("needVerify"):
                raise RuntimeError("reCAPTCHA 后 needVerify 仍为 true")
            step(index, "白名单已解锁", "green")

        # 10. Create API key
        key_name = str(config.get("api_key_name") or "auto")
        if key_name == "auto":
            key_name = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
        step(index, f"创建 API Key (name={key_name})", "cyan")
        create_resp = self._post(
            "/api_key/create",
            payload={"name": key_name, "tags": []},
            referer=f"{TARGET_BASE}/platform/pay-as-you-go",
        )
        create_data = create_resp.get("data") or {}
        api_key = str(create_data.get("token") or create_data.get("key") or "")
        # If the key is masked (e.g. "sk-ai-...a8c9"), fetch the full list
        if not api_key or "*" in api_key:
            step(index, "API Key 被脱敏(***), 从列表接口获取", "yellow")
            list_resp = self._get(
                "/api_key/list",
                referer=f"{TARGET_BASE}/platform/pay-as-you-go",
            )
            keys = list_resp.get("data") or []
            if isinstance(keys, list) and keys:
                # Pick the most recently created key
                latest = keys[0]
                api_key = str(latest.get("token") or latest.get("key") or latest.get("apiKey") or "")
                if not api_key:
                    # Some APIs return the full key only in create, list shows masked
                    api_key = str(create_data.get("token") or create_data.get("key") or "")
        if not api_key:
            raise RuntimeError(f"创建 API Key 失败: {create_resp}")
        step(index, f"API Key: {api_key[:12]}...{api_key[-4:]}", "green")

        # 11. Import to Sub2API
        sub2api_cfg = config.get("sub2api", {})
        if sub2api_cfg.get("enabled", False):
            step(index, "导入 Sub2API", "cyan")
            try:
                importer = Sub2APIImporter(sub2api_cfg)
                account_name = f"auto-{email.split('@')[0][:20]}"
                account_data = importer.import_key(api_key, name=account_name)
                sub2api_id = account_data.get("id", "?")
                step(index, f"Sub2API 导入成功 (account id={sub2api_id})", "green")
            except Exception as e:
                step(index, f"Sub2API 导入失败: {e}", "yellow")

        return {
            "email": email,
            "user_id": user_id,
            "api_key": api_key,
            "key_name": key_name,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }


# --------------------------------------------------------------------------- #
# Worker / runner
# --------------------------------------------------------------------------- #
def worker(index: int) -> dict:
    start = time.time()
    registrar = Registrar(
        proxy=config.get("proxy", ""),
        captcha_cfg=config.get("captcha", {}),
    )
    try:
        step(index, "任务启动", "cyan")
        result = registrar.register(index)
        cost = time.time() - start
        with stats_lock:
            stats["done"] += 1
            stats["success"] += 1
            avg = (time.time() - stats["start_time"]) / max(stats["success"], 1)
        log(
            f'{result["email"]} 注册成功，耗时{cost:.1f}s，平均{avg:.1f}s/个，'
            f'API Key: {result["api_key"][:12]}...{result["api_key"][-4:]}',
            "green",
        )
        return {"ok": True, "index": index, "result": result}
    except Exception as e:
        cost = time.time() - start
        with stats_lock:
            stats["done"] += 1
            stats["fail"] += 1
        log(f"任务{index} 注册失败，耗时{cost:.1f}s，原因: {e}", "red")
        return {"ok": False, "index": index, "error": str(e)}
    finally:
        registrar.close()


def run(total: int | None = None, threads: int | None = None) -> list[dict]:
    total = total if total is not None else config.get("total", 1)
    threads = threads if threads is not None else config.get("threads", 1)
    threads = max(1, min(threads, total))

    stats["start_time"] = time.time()
    log(f"开始注册 {total} 个账号，并发 {threads}", "cyan")

    results: list[dict] = []
    if threads == 1:
        for i in range(1, total + 1):
            results.append(worker(i))
    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        with ThreadPoolExecutor(max_workers=threads) as pool:
            futures = {pool.submit(worker, i): i for i in range(1, total + 1)}
            for future in as_completed(futures):
                results.append(future.result())

    elapsed = time.time() - stats["start_time"]
    success = sum(1 for r in results if r.get("ok"))
    log(
        f"完成: {success}/{total} 成功，{total - success} 失败，总耗时 {elapsed:.1f}s",
        "green" if success == total else "yellow",
    )

    save_results(results)
    return results


def save_results(results: list[dict]) -> Path:
    out_file = BASE_DIR / "accounts.json"
    existing: list = []
    if out_file.exists():
        try:
            existing = json.loads(out_file.read_text(encoding="utf-8"))
            if not isinstance(existing, list):
                existing = []
        except Exception:
            existing = []
    for r in results:
        if r.get("ok") and r.get("result"):
            existing.append(r["result"])
    out_file.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"结果已保存到 {out_file}", "cyan")
    return out_file


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="自动注册机 (纯 HTTP + 2captcha)")
    parser.add_argument("-n", "--total", type=int, help="注册数量")
    parser.add_argument("-t", "--threads", type=int, help="并发数")
    parser.add_argument("--proxy", type=str, help="代理地址")
    args = parser.parse_args()

    if args.total is not None:
        config["total"] = args.total
    if args.threads is not None:
        config["threads"] = args.threads
    if args.proxy:
        config["proxy"] = args.proxy

    run()


if __name__ == "__main__":
    main()
