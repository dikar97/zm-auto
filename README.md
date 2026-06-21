# 自动注册机

> ⚠️ **免责声明 / Disclaimer**
>
> 本项目仅供学习和研究用途，旨在帮助开发者理解 HTTP 协议分析、API 逆向工程、以及反爬虫机制的工作原理。
>
> - 本项目不针对任何特定网站或服务，所有目标地址均为用户自行配置
> - 使用本项目产生的任何账号、API Key 或其他后果由使用者自行承担
> - 使用者需自行确保遵守目标网站的服务条款（ToS）及当地法律法规
> - 作者不对任何因使用或滥用本项目而导致的后果负责
> - 如目标站点方认为本项目侵犯其权益，请联系移除
>
> This project is for educational and research purposes only. It is designed to help developers understand HTTP protocol analysis, API reverse engineering, and anti-bot mechanisms. Users are solely responsible for their own actions and must comply with all applicable terms of service and laws.

---

全自动账号注册 + API Key 获取。**纯 HTTP + 2captcha，不需要浏览器。**

## 架构

```
mail_provider.py     ← 7 个临时邮箱 provider（复刻自 chatgpt2api）
captcha_solver.py    ← 2captcha 解 Cloudflare Turnstile + reCAPTCHA v2
register.py          ← 注册主流程 + CLI（纯 HTTP, curl_cffi）
server.py            ← Web 管理面板后端（FastAPI + SSE + 登录认证）
web/index.html       ← Web 前端单页（Tailwind CDN + 原生 JS，无构建）
web/login.html       ← 登录页
config.json          ← 运行时配置（gitignored，含明文密钥）
accounts.json        ← 注册结果输出（gitignored）

# VPS 部署文件
Dockerfile           ← 容器镜像构建
entrypoint.sh        ← 容器启动时初始化 data/ + 软链 config.json
Caddyfile            ← 反向代理 + 自动 HTTPS
docker-compose.yml   ← 一键启动 app + caddy
.env.example         ← 环境变量模板（复制为 .env 后修改）
```

## 注册流程（~30s/个）

```
1. 创建临时邮箱（CF temp mail / GPTMail / 等 7 种）
2. GET /login → 自动获取 ctoken cookie
3. 2captcha 解 Turnstile → token（~12s）
4. POST /api/login/email/code/send → 发验证码
5. 邮箱轮询取 6 位验证码（~5s）
6. POST /api/login/email/code/verify → 登录
7. GET /api/user/info → 检查 needVerify
8. 2captcha 解 reCAPTCHA v2 → token（~15s）
9. POST /api/login/recaptcha/verification → 解锁白名单
10. POST /api/api_key/create → 拿到 sk-ai-v1-... 密钥
```

## 依赖

```bash
pip install curl_cffi requests urllib3
```

> 不需要 playwright/camoufox/browser——纯 HTTP！

## 配置

```bash
cp config.example.json config.json
# 编辑 config.json
```

### 邮箱 Provider（7 选 1+）

| type | 说明 | 必填字段 |
|------|------|----------|
| `cloudflare_temp_email` | Cloudflare Workers 自建临时邮箱 | `api_base`, `admin_password`, `domain` |
| `gptmail` | GPTMail (mail.chatgpt.org.uk) | `api_key` |
| `tempmail_lol` | TempMail.lol API v2 | `api_key`(可选), `domain`(可选) |
| `duckmail` | DuckMail | `api_key`, `default_domain` |
| `moemail` | MoEmail 自建 | `api_base`, `api_key`, `domain` |
| `inbucket` | Inbucket 自建 | `api_base`, `domain` |
| `yyds_mail` | YYDSMail | `api_base`, `api_key`, `domain` |

### Captcha

```json
"captcha": {
    "provider": "2captcha",
    "api_key": "你的2captcha-key"
}
```

支持 `2captcha` 和 `anticaptcha`。Turnstile + reCAPTCHA v2 都通过打码 API 解决。

## 使用

```bash
# 单个注册
python register.py -n 1

# 5 个账号，2 并发
python register.py -n 5 -t 2

# 走代理
python register.py -n 3 --proxy http://127.0.0.1:7897
```

## Web 管理面板

本项目附带一个本地 Web 管理面板，把 CLI 包装成可视化操作台，支持配置编辑、任务触发、实时日志（SSE）和账号管理。

### 启动

#### 本地模式（无认证，仅本地调试）

```bash
cd zm-auto
pip install -r requirements.txt        # 含 fastapi/uvicorn/sse-starlette/itsdangerous/python-multipart
python server.py                       # 默认监听 0.0.0.0:8000
# 或: uvicorn server:app --host 0.0.0.0 --port 8000 --reload
```

浏览器打开 <http://localhost:8000> 即可使用。

> 本地模式下不强制登录，适合快速试用。**绝对不要把本地模式直接暴露到公网**——`config.json` 里有 2captcha/Sub2API 明文密钥，任何人能扫到端口就能拿。

#### 认证模式（公网 / 多人共享）

设置三个环境变量后即开启登录认证：

```bash
export WEB_USERNAME=admin
export WEB_PASSWORD='至少16位强密码'
export SECRET_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
python server.py
```

所有访问都会先重定向到 `/login`，登录后写入 session cookie（24h 有效，HTTPS-only，SameSite=Strict）。

### 功能

| Tab | 说明 |
|-----|------|
| **运行** | 填 total/threads → 一键启动注册任务；进度条 + 实时统计 + 状态徽章 |
| **配置** | JSON 编辑器直接改 `config.json`，保存即热加载（下个任务生效） |
| **账号** | 表格展示 `accounts.json`，支持复制单 Key、复制全部、导出 JSON、清空 |
| **日志抽屉** | 底部固定面板，SSE 实时推送 `register.log` 输出，带颜色与时间戳；可折叠/清屏/锁定滚动 |

### 后端 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/config` | 读取 `config.json` |
| PUT | `/api/config` | 覆盖写入 `config.json`（body=完整 JSON） |
| POST | `/api/run` | 启动注册任务，body=`{total, threads}` |
| GET | `/api/status` | 当前任务状态 + stats |
| GET | `/api/logs` | SSE 日志流，事件 `log`/`ping` |
| GET | `/api/accounts` | 读取 `accounts.json` |
| DELETE | `/api/accounts` | 清空 `accounts.json` |

### 实现细节

- **日志流**：`server.py` monkey-patch 了 `register.log`，每条日志同时 `print()` 到终端、推送给所有 SSE 订阅者、写入 500 条滚动 buffer；新客户端连上时会先收到历史 buffer。
- **任务隔离**：任务在后台线程跑，启动前 `register.config = register.load_config()` 重新加载并重置 `register.stats`，避免上轮残留。
- **跨线程广播**：保存启动时的 asyncio loop，通过 `loop.call_soon_threadsafe` 把日志从 worker 线程安全地推到 SSE。

> ⚠️ **公网部署必须走认证模式或反代认证**：本地模式无登录拦截，`config.json` 含明文密钥（2captcha/Sub2API），任何能访问该端口的人都能拿到。生产部署请参考下文 [VPS 部署](#vps-部署docker-compose)。

## VPS 部署（Docker Compose）

公网部署推荐用 Docker Compose 跑 app + Caddy，自动申请 HTTPS 证书并加登录认证。

### 前置条件

1. **一台公网 Linux VPS**（Ubuntu 22.04+ / Debian 12+ 推荐）
2. **一个域名**，DNS A 记录指向 VPS 公网 IP（例如 `zm-auto.example.com → 1.2.3.4`）
3. **VPS 开放 80 + 443 端口**（80 用于 Let's Encrypt 证书验证，443 用于 HTTPS）
4. **安装 Docker + Docker Compose**：
   ```bash
   curl -fsSL https://get.docker.com | sh
   sudo usermod -aG docker $USER  # 然后退出重新登录
   ```

### 部署步骤

```bash
# 1. 把项目代码拷到 VPS
git clone <repo-url> zm-auto      # 或 scp -r ./zm-auto user@vps:/opt/zm-auto
cd zm-auto

# 2. 复制环境变量模板并填值
cp .env.example .env
nano .env
# 必改项：
#   DOMAIN=你的域名
#   WEB_USERNAME=你的用户名（别用 admin）
#   WEB_PASSWORD=至少16位强密码
#   SECRET_KEY=随机32字节（执行 python3 -c "import secrets; print(secrets.token_urlsafe(32))" 生成）

# 3. 构建并启动
docker compose up -d --build

# 4. 检查状态
docker compose ps                 # 两个服务都应是 running / healthy
docker compose logs -f caddy      # 看证书申请是否成功（首次启动 30~60s 内 Caddy 会自动签发）

# 5. 浏览器访问
# https://你的域名  → 登录页
```

### 数据持久化

所有运行时数据都在宿主机的 `./data/` 目录（容器内挂载到 `/app/data`）：

```
./data/
├── config.json       ← Web 配置 Tab 编辑的就是这个
└── accounts.json     ← 注册成功的账号列表
```

容器重启 / 升级 / 重建都不丢数据（`docker compose down` 不会删 volume；`docker compose down -v` 才会删 volume，但本配置用的是 bind mount 不是 named volume，所以 `-v` 也不会删）。

### 常用运维命令

```bash
# 查看实时日志
docker compose logs -f app
docker compose logs -f caddy

# 重启
docker compose restart app

# 升级代码（git pull 后重建）
git pull && docker compose up -d --build

# 停止
docker compose down

# 完全卸载（保留 ./data）
docker compose down --rmi local
```

### 安全清单

- [x] **HTTPS 强制**：Caddy 自动签发 Let's Encrypt 证书，HTTP 自动 301 跳 HTTPS
- [x] **登录认证**：用户名 + PBKDF2-HMAC-SHA256 密码（200k 迭代）+ session cookie
- [x] **HSTS**：Caddy 设置 `Strict-Transport-Security: max-age=31536000; includeSubDomains; preload`
- [x] **X-Frame-Options DENY**：防点击劫持
- [x] **SameSite=Strict cookie**：防 CSRF
- [x] **`./data/` 持久化 + 不被打进镜像**：`.dockerignore` 已排除
- [ ] **改 `WEB_USERNAME`**：别用 admin / root 这种容易被字典攻击的
- [ ] **改 `WEB_PASSWORD`**：至少 16 位混合大小写 + 数字 + 符号
- [ ] **限制 SSH**：`/etc/ssh/sshd_config` 关 `PasswordAuthentication`，只留密钥登录
- [ ] **配置防火墙**：`ufw allow 22,80,443/tcp`，拒绝其他所有入站
- [ ] **定期备份 `./data/`**：含明文密钥，加密备份（`gpg -c data.tar.gz`）

### 故障排查

| 现象 | 排查 |
|------|------|
| 浏览器访问域名打不开 | 1) DNS 是否生效（`dig your.domain`）2) VPS 80/443 端口是否开放（`curl -v http://your.domain`）3) `docker compose ps` 服务是否 running |
| Caddy 申请证书失败 | 1) 域名 DNS 必须指向 VPS 公网 IP（不能是内网或 CDN）2) 80 端口必须可达（Let's Encrypt HTTP-01 验证）3) 看 `docker compose logs caddy` |
| 登录后立刻被踢出 | session cookie 没设对，检查 `.env` 的 `SECRET_KEY` 是否设置且未变；`https_only=true` 要求 HTTPS 访问，HTTP 调试时 cookie 不会被设置 |
| `502 Bad Gateway` | app 容器还没起好或挂了，`docker compose logs app` 看错误（常见：`config.json` 不合法） |

### 备选：不用 Caddy（已有 Nginx）

如果你 VPS 上已经跑了 Nginx，可以只跑 app 容器，让 Nginx 反代到 `127.0.0.1:8000`：

```bash
# 只启动 app
docker compose up -d app

# Nginx 配置（示意）
# server {
#     listen 443 ssl http2;
#     server_name your.domain;
#     ssl_certificate     /etc/letsencrypt/live/your.domain/fullchain.pem;
#     ssl_certificate_key /etc/letsencrypt/live/your.domain/privkey.pem;
#     location / {
#         proxy_pass http://127.0.0.1:8000;
#         proxy_set_header Host $host;
#         proxy_set_header X-Real-IP $remote_addr;
#         proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
#         proxy_set_header X-Forwarded-Proto $scheme;
#         proxy_http_version 1.1;
#         proxy_set_header Upgrade $http_upgrade;   # SSE 需要
#         proxy_set_header Connection "upgrade";
#         proxy_buffering off;                      # SSE 必须关 buffer
#         proxy_read_timeout 3600s;                 # SSE 长连接
#     }
# }
```

认证用 Nginx Basic Auth 或上面的应用层登录都行。

## 输出

`accounts.json`：

```json
[
  {
    "email": "tmpabc@example.com",
    "user_id": "2625US...",
    "api_key": "sk-ai-v1-xxxx...xxxx",
    "key_name": "abc123",
    "created_at": "2026-06-18T..."
  }
]
```

## API Key 用法

```bash
curl https://your-target-site/api/v1/chat/completions \
  -H "Authorization: Bearer sk-ai-v1-..." \
  -H "Content-Type: application/json" \
  -d '{"model":"z-ai/glm-4.6v-flash-free","messages":[{"role":"user","content":"hi"}]}'
```

> ⚠️ endpoint 是 `/api/v1/chat/completions`，不是 `/v1/chat/completions`

## 成本

- 2captcha: Turnstile ~$0.002/次 + reCAPTCHA ~$0.003/次 ≈ **$0.005/账号**
- 邮箱: 免费（CF temp mail）
- 单账号注册耗时: **~30s**

## 更新日志

### 2026-06-21

- **feat**: Web 面板加登录认证（环境变量 `WEB_USERNAME`/`WEB_PASSWORD`/`SECRET_KEY` 驱动，PBKDF2-HMAC-SHA256 密码 hash + session cookie）
- **feat**: 加 VPS 部署套件——`Dockerfile` + `entrypoint.sh`（data 目录软链持久化）+ `Caddyfile`（自动 HTTPS + 安全 header）+ `docker-compose.yml`（app + caddy 一键启动）
- **feat**: 新增登录页 `web/login.html`（暗色风格，POST 表单 + next 跳转 + 开放重定向防护）
- **docs**: README 加 VPS 部署章节（部署步骤 / 数据持久化 / 运维命令 / 安全清单 / 故障排查 / Nginx 备选方案）
- **deps**: requirements.txt 加 `itsdangerous`（session 签名）+ `python-multipart`（表单解析）

### 2026-06-18

- **feat**: 注册后新增"预热"步骤——调用目标模型接口验证 API Key 可用性，只有返回 200 才导入 Sub2API
- **feat**: Sub2API 导入时自动命名为 `zm-N`，支持并发和优先级参数
- **feat**: 启动时自动查询 Sub2API 已有 `zm-*` 账号，避免命名冲突
- **chore**: 清理代码中的真实代理地址、目标域名、作者名等敏感信息，统一改为占位符
- **chore**: `.gitignore` 增加 `*.log`

### 2026-06-17

- **feat**: 初始版本，纯 HTTP + 2captcha 注册流程
- **feat**: 支持 7 个临时邮箱 provider
- **feat**: 支持自动导入 Sub2API
- **docs**: 添加免责声明

## 注意事项

1. **ctoken** 从 `/login` 的 Set-Cookie 自动获取
2. **sessionId** 由 `/login/email/code/verify` 的 Set-Cookie 设置，curl_cffi 自动捕获
3. **API Key 脱敏**: create 返回 `token: "***"`，list 返回 `sk-ai-...末4位`。注册机从 list 取 key
4. **白名单**: 新用户必须过 reCAPTCHA 才能用 API
5. **Free Plan**: 5 Flows / 5h
6. **预热**: 导入网关前会实际调用目标模型验证 key，测试失败不计入成功
