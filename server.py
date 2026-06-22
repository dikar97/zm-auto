"""⚠️ DISCLAIMER: This project is for educational and research purposes only.
Users are solely responsible for complying with all applicable ToS and laws.
本项目仅供学习研究，使用者需自行承担所有后果。

Web 管理面板后端：FastAPI 包装 register.py CLI，提供 REST + SSE 实时日志 + 登录认证。

本地启动:
    cd zm-auto
    pip install -r requirements.txt
    python server.py                 # 默认 0.0.0.0:8000（本地模式，无认证）
    # 或: uvicorn server:app --host 0.0.0.0 --port 8000
    然后浏览器打开 http://localhost:8000

VPS / 公网启动（必须设置认证）:
    export WEB_USERNAME=admin
    export WEB_PASSWORD='你的强密码'
    export SECRET_KEY='随机32字节字符串'   # openssl rand -base64 32
    python server.py
未设置 WEB_USERNAME/WEB_PASSWORD/SECRET_KEY 时走「本地模式」(无认证)，仅供本地调试使用。
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import hmac
import io
import json
import os
import secrets
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware
from sse_starlette.sse import EventSourceResponse

# ---------------------------------------------------------------------------
# register 模块加载（降级机制：缺依赖也能起 Web UI）
# ---------------------------------------------------------------------------
register: Any = None
register_import_error: str | None = None
try:
    import register as _register_mod  # noqa: E402
    register = _register_mod
except Exception as e:
    register_import_error = f"{type(e).__name__}: {e}"

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"
ACCOUNTS_FILE = BASE_DIR / "accounts.json"
WEB_DIR = BASE_DIR / "web"
LOGIN_PAGE = WEB_DIR / "login.html"

# accounts.json 读写锁：避免批量删除/清空/导出之间的并发竞争
_accounts_lock = threading.Lock()


def _read_accounts() -> list[dict[str, Any]]:
    """读取 accounts.json 并返回 list[dict]。文件不存在/损坏/非数组均返回空列表。"""
    with _accounts_lock:
        if not ACCOUNTS_FILE.exists():
            return []
        try:
            data = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
    if not isinstance(data, list):
        return []
    return [x for x in data if isinstance(x, dict)]


def _write_accounts(accounts: list[dict[str, Any]]) -> None:
    """原子化写入 accounts.json。"""
    with _accounts_lock:
        ACCOUNTS_FILE.write_text(
            json.dumps(accounts, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

# ---------------------------------------------------------------------------
# 认证配置（环境变量驱动）
# ---------------------------------------------------------------------------
env_username: str = os.environ.get("WEB_USERNAME", "").strip()
env_password: str = os.environ.get("WEB_PASSWORD", "")
env_secret: str = os.environ.get("SECRET_KEY", "")

# 校验：要么三个都设置，要么三个都不设置（本地模式）
if (env_username or env_password or env_secret) and not (
    env_username and env_password and env_secret
):
    raise RuntimeError(
        "认证配置不完整：WEB_USERNAME / WEB_PASSWORD / SECRET_KEY 必须同时设置，"
        "或同时留空（走本地无认证模式）。"
        "生成 SECRET_KEY 用: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
    )

_AUTH_ENABLED: bool = bool(env_username and env_password and env_secret)

# 进程内 session 签名 key：未显式提供时用一次性随机值（重启会登出所有用户）
_session_secret: str = env_secret
if not _session_secret:
    _session_secret = secrets.token_urlsafe(48)
    if not _AUTH_ENABLED:
        # 仅本地模式才提示
        print("[server] 本地模式：未启用登录认证（WEB_USERNAME/WEB_PASSWORD/SECRET_KEY 全空）。禁止公网暴露此模式。")

# 密码 PBKDF2-HMAC-SHA256 hash（200k 迭代），内存里不存明文
_PWD_SALT: bytes = secrets.token_bytes(16) if env_password else b""
_PWD_HASH: bytes = (
    hashlib.pbkdf2_hmac("sha256", env_password.encode(), _PWD_SALT, 200_000)
    if env_password
    else b""
)


def _verify_password(plain: str) -> bool:
    if not env_password or not _PWD_HASH:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", plain.encode(), _PWD_SALT, 200_000)
    return hmac.compare_digest(dk, _PWD_HASH)


# ---------------------------------------------------------------------------
# 任务状态 + SSE 广播
# ---------------------------------------------------------------------------
_task_lock = threading.Lock()
_task_state: dict[str, Any] = {
    "status": "idle",
    "started_at": None,
    "finished_at": None,
    "total": 0,
    "threads": 0,
    "error": None,
}

_loop: asyncio.AbstractEventLoop | None = None
_subscribers: set[asyncio.Queue[str]] = set()
_log_buffer: deque[str] = deque(maxlen=500)


def _broadcast(line: str) -> None:
    _log_buffer.append(line)
    loop = _loop
    if loop is None:
        return
    for q in list(_subscribers):
        try:
            loop.call_soon_threadsafe(q.put_nowait, line)
        except Exception:
            pass


_orig_log: Any = None


def _patched_log(text: str, color: str = "") -> None:
    if _orig_log:
        _orig_log(text, color)
    ts = datetime.now().strftime("%H:%M:%S")
    _broadcast(json.dumps({"ts": ts, "text": text, "color": color}, ensure_ascii=False))


def _setup_log_patch() -> None:
    global _orig_log
    if register is None:
        return
    _orig_log = register.log
    register.log = _patched_log


_setup_log_patch()


def _run_task(total: int, threads: int) -> None:
    try:
        if register is None:
            raise RuntimeError(f"register 模块未加载: {register_import_error}")
        register.config = register.load_config()
        register.config["total"] = total
        register.config["threads"] = threads
        # 在调 run() 前重置 stats（run() 内部还会再重置一次，但若 load_config 失败
        # 或中途异常，至少前端拿到的是干净状态而非上次 run 的残留）。
        register.stats.update(
            done=0,
            success=0,
            fail=0,
            start_time=time.time(),
            total=total,
            active=0,
            last_error=None,
            last_success_at=None,
            last_error_at=None,
            finished_at=None,
        )
        register.run(total=total, threads=threads)
        _task_state["status"] = "done"
    except Exception as e:
        _task_state["status"] = "error"
        _task_state["error"] = f"{type(e).__name__}: {e}"
    finally:
        _task_state["finished_at"] = datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# FastAPI app + 认证中间件
# ---------------------------------------------------------------------------
app = FastAPI(title="zm-auto Web UI", version="1.1.0")


# 路径白名单：未登录也允许访问
_PUBLIC_PATHS = {"/login", "/logout"}


class AuthMiddleware(BaseHTTPMiddleware):
    """未登录拦截：API 返 401 JSON，页面跳转 /login。本地模式直通。"""

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        if not _AUTH_ENABLED:
            return await call_next(request)

        path = request.url.path
        normalized = path.rstrip("/") or "/"
        if normalized in _PUBLIC_PATHS:
            return await call_next(request)

        if request.session.get("user"):  # type: ignore[attr-defined]
            return await call_next(request)

        # 未登录
        if path.startswith("/api/"):
            return JSONResponse({"detail": "未登录或会话已过期"}, status_code=401)

        # 浏览器请求 → 重定向到登录页
        accept = request.headers.get("accept", "")
        if "text/html" in accept or "application/xhtml" in accept:
            return RedirectResponse(url="/login?next=" + path, status_code=303)
        return JSONResponse({"detail": "未登录或会话已过期"}, status_code=401)


# Starlette middleware 顺序：后 add = 外层。SessionMiddleware 必须在 AuthMiddleware
# 外层，否则 BaseHTTPMiddleware 创建 Request 时 scope 还没有 "session" key → 500
app.add_middleware(AuthMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=_session_secret,
    max_age=86400,
    same_site="strict",
    https_only=_AUTH_ENABLED,
)


@app.on_event("startup")
async def _capture_loop() -> None:
    global _loop
    _loop = asyncio.get_event_loop()
    if _AUTH_ENABLED:
        print(f"[server] 认证已启用，用户名: {env_username}（登录访问 /login）")
    else:
        print("[server] 本地模式：未启用登录认证")


# ---------------------------------------------------------------------------
# 登录 / 登出
# ---------------------------------------------------------------------------
@app.get("/login")
async def login_page(request: Request, next: str = "/") -> Any:
    """显示登录页。已登录直接跳转。"""
    if _AUTH_ENABLED and request.session.get("user"):  # type: ignore[attr-defined]
        return RedirectResponse(url=next or "/", status_code=303)
    if not LOGIN_PAGE.exists():
        return HTMLResponse_NO_LOGIN_PAGE
    return FileResponse(str(LOGIN_PAGE), media_type="text/html; charset=utf-8")


@app.post("/login")
async def login_submit(request: Request) -> Any:
    """表单提交登录：username/password。成功 303 跳转 /，失败 401 返回错误页。"""
    form = await request.form()
    raw_username = form.get("username")
    raw_password = form.get("password")
    raw_next = form.get("next")
    username = (raw_username if isinstance(raw_username, str) else "").strip()
    password = raw_password if isinstance(raw_password, str) else ""
    next_url = (raw_next if isinstance(raw_next, str) else "/").strip() or "/"
    # 仅允许站内跳转，防开放重定向
    if not next_url.startswith("/") or next_url.startswith("//"):
        next_url = "/"

    if (
        _AUTH_ENABLED
        and hmac.compare_digest(username, env_username)
        and _verify_password(password)
    ):
        request.session["user"] = username  # type: ignore[attr-defined]
        request.session["login_at"] = datetime.now(timezone.utc).isoformat()  # type: ignore[attr-defined]
        return RedirectResponse(url=next_url, status_code=303)

    # 失败：返回带错误的 HTML（用模板拼，避免引入 jinja2）
    return _render_login_error(next_url, "用户名或密码错误")


@app.get("/logout")
async def logout(request: Request) -> Any:
    request.session.clear()  # type: ignore[attr-defined]
    return RedirectResponse(url="/login", status_code=303)


# 登录页缺失时的 fallback
HTMLResponse_NO_LOGIN_PAGE = JSONResponse(
    {"detail": "web/login.html 不存在，请先创建登录页"}, status_code=500
)


def _render_login_error(next_url: str, message: str) -> Any:
    """用最简单的方式返回带错误信息的登录页：直接读 login.html 替换占位符。"""
    try:
        html = LOGIN_PAGE.read_text(encoding="utf-8")
        # 在 body 末尾注入错误提示脚本（不破坏原结构）
        inject = (
            '<script>document.addEventListener("DOMContentLoaded",function(){'
            'var e=document.querySelector("[data-err]");'
            f'if(!e){{var d=document.createElement("div");'
            'd.setAttribute("data-err","1");'
            f'd.style.cssText="color:#f87171;padding:8px 0;font-size:14px;";'
            f'd.textContent={json.dumps(message, ensure_ascii=False)};'
            'document.querySelector("form").prepend(d);}}'
            'else{e.textContent=' + json.dumps(message, ensure_ascii=False) + ';}'
            '});</script></body>'
        )
        html = html.replace("</body>", inject, 1)
        from fastapi.responses import HTMLResponse

        return HTMLResponse(html, status_code=401)
    except Exception:
        return JSONResponse({"detail": message}, status_code=401)


# ---------------------------------------------------------------------------
# 业务 API
# ---------------------------------------------------------------------------
@app.get("/api/config")
async def get_config() -> JSONResponse:
    """读取 config.json；不存在则返回 DEFAULT_CONFIG（register 未就绪时给最小模板）。"""
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=400, detail=f"config.json 解析失败: {e}")
    elif register is not None:
        data = json.loads(json.dumps(register.DEFAULT_CONFIG))
    else:
        data = {
            "mail": {"providers": []},
            "captcha": {"provider": "2captcha", "api_key": ""},
            "target_base": "",
            "total": 1,
            "threads": 1,
            "sub2api": {"enabled": False},
        }
    return JSONResponse(data)


@app.put("/api/config")
async def put_config(payload: dict[str, Any]) -> JSONResponse:
    """覆盖写入 config.json。前端发完整 JSON。"""
    if "captcha" not in payload:
        raise HTTPException(status_code=400, detail="缺少 captcha 字段")
    CONFIG_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if register is not None:
        register.config = register.load_config()
    return JSONResponse({"ok": True, "saved_at": datetime.now(timezone.utc).isoformat()})


@app.post("/api/run")
async def start_run(body: dict[str, Any]) -> JSONResponse:
    """启动注册任务。body: {"total": int, "threads": int}"""
    if register is None:
        raise HTTPException(
            status_code=503,
            detail=f"注册依赖未就绪: {register_import_error}. 请运行 pip install -r requirements.txt",
        )
    total = int(body.get("total", 1))
    threads = int(body.get("threads", 1))
    if total < 1 or total > 500:
        raise HTTPException(status_code=400, detail="total 必须在 1-500 之间")
    if threads < 1 or threads > 20:
        raise HTTPException(status_code=400, detail="threads 必须在 1-20 之间")

    with _task_lock:
        if _task_state["status"] == "running":
            raise HTTPException(status_code=409, detail="已有任务在运行")
        _task_state.update(
            status="running",
            started_at=datetime.now(timezone.utc).isoformat(),
            finished_at=None,
            total=total,
            threads=threads,
            error=None,
        )

    t = threading.Thread(
        target=_run_task, args=(total, threads), daemon=True, name="register-runner"
    )
    t.start()
    return JSONResponse({"ok": True, "total": total, "threads": threads})


@app.get("/api/status")
async def get_status() -> JSONResponse:
    """当前任务状态 + 实时 stats。"""
    stats = dict(register.stats) if register is not None else {}
    return JSONResponse(
        {
            "task": dict(_task_state),
            "stats": stats,
            "register_ready": register is not None,
            "register_error": register_import_error,
            "now": datetime.now(timezone.utc).isoformat(),
        }
    )


@app.get("/api/logs")
async def sse_logs():
    """Server-Sent Events: 每行日志推一个 'log' 事件。"""

    async def event_stream():
        queue: asyncio.Queue[str] = asyncio.Queue()
        _subscribers.add(queue)
        try:
            for line in list(_log_buffer):
                yield {"event": "log", "data": line}
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield {"event": "log", "data": msg}
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": ""}
        finally:
            _subscribers.discard(queue)

    return EventSourceResponse(event_stream())


@app.get("/api/accounts")
async def get_accounts(q: str = "") -> JSONResponse:
    """读取 accounts.json。可选 q 参数做大小写不敏感子串匹配（email/user_id/api_key）。"""
    data = _read_accounts()
    if q:
        ql = q.lower()
        data = [
            a
            for a in data
            if ql in str(a.get("email", "")).lower()
            or ql in str(a.get("user_id", "")).lower()
            or ql in str(a.get("api_key", "")).lower()
        ]
    return JSONResponse(data)


@app.delete("/api/accounts")
async def clear_accounts() -> JSONResponse:
    """清空 accounts.json（只清文件，不撤回已导入 Sub2API 的账号）。"""
    _write_accounts([])
    return JSONResponse({"ok": True})


@app.post("/api/accounts/delete")
async def delete_accounts(body: dict[str, Any]) -> JSONResponse:
    """批量删除指定索引的账号。body: {"indices": [0, 2, 5]}。
    索引基于当前 accounts.json 数组顺序，越界自动忽略。
    返回实际删除数量与剩余数量。
    """
    raw_indices = body.get("indices")
    if raw_indices is None:
        raw_indices = body.get("ids", [])
    if not isinstance(raw_indices, list):
        raise HTTPException(status_code=400, detail="indices 必须是数组")
    try:
        indices = sorted({int(i) for i in raw_indices}, reverse=True)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="indices 元素必须是整数")

    data = _read_accounts()
    removed = 0
    for i in indices:
        if 0 <= i < len(data):
            data.pop(i)
            removed += 1
    if removed:
        _write_accounts(data)
    return JSONResponse({"ok": True, "removed": removed, "remaining": len(data)})


@app.get("/api/accounts/export")
async def export_accounts(format: str = "json", indices: str = "") -> Response:
    """导出账号。format: json|csv；indices: 逗号分隔索引，不传=全部。"""
    data = _read_accounts()
    if indices:
        try:
            idx_set = {int(x) for x in indices.split(",") if x.strip()}
        except ValueError:
            raise HTTPException(status_code=400, detail="indices 必须是逗号分隔整数")
        data = [a for i, a in enumerate(data) if i in idx_set]

    fmt = format.lower()
    if fmt == "csv":
        out = io.StringIO()
        # UTF-8 BOM 让 Excel 正确识别编码
        out.write("\ufeff")
        writer = csv.writer(out)
        writer.writerow(["email", "user_id", "api_key", "sub2api_id", "created_at"])
        for a in data:
            writer.writerow(
                [
                    a.get("email", ""),
                    a.get("user_id", ""),
                    a.get("api_key", ""),
                    a.get("sub2api_id", ""),
                    a.get("created_at", ""),
                ]
            )
        return Response(
            content=out.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=accounts.csv"},
        )
    return Response(
        content=json.dumps(data, ensure_ascii=False, indent=2),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=accounts.json"},
    )


# ---------------------------------------------------------------------------
# 静态资源挂载（放最后，catch-all）
# ---------------------------------------------------------------------------
if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
else:

    @app.get("/")
    async def index_fallback() -> JSONResponse:
        return JSONResponse(
            {"error": "web/ 目录不存在，请先创建前端文件"}, status_code=404
        )


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )
