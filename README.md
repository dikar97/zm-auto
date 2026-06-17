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
register.py   ← 注册主流程 + CLI（纯 HTTP, curl_cffi）
config.json          ← 运行时配置
accounts.json        ← 注册结果输出
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

## 注意事项

1. **ctoken** 从 `/login` 的 Set-Cookie 自动获取
2. **sessionId** 由 `/login/email/code/verify` 的 Set-Cookie 设置，curl_cffi 自动捕获
3. **API Key 脱敏**: create 返回 `token: "***"`，list 返回 `sk-ai-...末4位`。注册机从 list 取 key
4. **白名单**: 新用户必须过 reCAPTCHA 才能用 API
5. **Free Plan**: 5 Flows / 5h
