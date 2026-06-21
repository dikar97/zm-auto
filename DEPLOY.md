# zm-auto VPS 部署 Runbook（Claude Code 指令版）

> 本文档是面向 AI agent（Claude Code 等）的可执行指令清单。每一步结构：**命令 → 验收条件 → 失败处理**。
> 所有命令默认从**本地工作站**执行，通过 SSH 操作远端 VPS。若 Claude Code 已直接登录 VPS，跳过 `ssh` 包装。

---

## 0. 必需的用户输入

向用户收集以下变量，**缺一不可**。任何一项未提供 → 向用户提问，禁止使用占位值继续。

| 变量              | 示例                          | 说明                                              |
| ----------------- | ----------------------------- | ------------------------------------------------- |
| `VPS_SSH_HOST`    | `1.2.3.4` 或 `user.tom.com`   | VPS SSH 接入地址                                  |
| `VPS_SSH_USER`    | `root` 或 `ubuntu`            | SSH 用户（必须有 sudo 权限）                      |
| `VPS_SSH_PORT`    | `22`                          | SSH 端口（可选，默认 22）                         |
| `DOMAIN`          | `zm.tom.com`                  | 已解析到 VPS 的域名（用于 Let's Encrypt 自动签发）|
| `WEB_USERNAME`    | `manager`                     | 面板登录用户名（**禁止使用 admin**）              |
| `WEB_PASSWORD`    | ≥16 位强密码                  | 面板登录密码                                      |
| `SECRET_KEY`      | 随机 32 字节                  | session 签名密钥（本 runbook 会自动生成）         |

收集完成后导出为环境变量（本会话内有效）：

```bash
export VPS_SSH_HOST="..."
export VPS_SSH_USER="..."
export VPS_SSH_PORT="22"
export DOMAIN="..."
export WEB_USERNAME="..."
export WEB_PASSWORD="..."
```

---

## 1. 验证 VPS 前置条件

### 1.1 SSH 连通性

```bash
ssh -p "${VPS_SSH_PORT}" -o StrictHostKeyChecking=accept-new "${VPS_SSH_USER}@${VPS_SSH_HOST}" 'echo SSH_OK; hostname; uname -m'
```

- **验收**：输出 `SSH_OK` + hostname（非本地主机名）+ 架构（`x86_64` 或 `aarch64`）
- **失败**：让用户检查 SSH 密钥 / 安全组 / 防火墙；禁止重试超过 3 次

### 1.2 操作系统版本

```bash
ssh -p "${VPS_SSH_PORT}" "${VPS_SSH_USER}@${VPS_SSH_HOST}" 'cat /etc/os-release | grep -E "^(NAME|VERSION_ID)=" '
```

- **验收**：Ubuntu 22.04+ / Debian 12+ / RHEL 9+ / AlmaLinux 9+ 之一
- **失败**：unsupported，abort 并告知用户

### 1.3 Docker 与 Compose

```bash
ssh -p "${VPS_SSH_PORT}" "${VPS_SSH_USER}@${VPS_SSH_HOST}" 'docker --version && docker compose version'
```

- **验收**：两个版本号都打印
- **失败处理（自动安装）**：

```bash
ssh -p "${VPS_SSH_PORT}" "${VPS_SSH_USER}@${VPS_SSH_HOST}" 'curl -fsSL https://get.docker.com | sh && sudo systemctl enable --now docker'
```

安装后重跑 1.3 验证。

### 1.4 端口占用检查

```bash
ssh -p "${VPS_SSH_PORT}" "${VPS_SSH_USER}@${VPS_SSH_HOST}" 'sudo ss -tlnp | grep -E ":(80|443|8000) " || echo PORTS_FREE'
```

- **验收**：输出 `PORTS_FREE`
- **失败**（有占用）：列出占用进程 → 询问用户：杀掉 / 换端口 / abort

### 1.5 DNS 指向验证

```bash
ssh -p "${VPS_SSH_PORT}" "${VPS_SSH_USER}@${VPS_SSH_HOST}" 'VPS_PUBLIC_IP=$(curl -s4 ifconfig.me); DOMAIN_IP=$(dig +short '"${DOMAIN}"' @8.8.8.8 | tail -1); echo "vps=$VPS_PUBLIC_IP domain=$DOMAIN_IP"; test "$VPS_PUBLIC_IP" = "$DOMAIN_IP" && echo DNS_OK || echo DNS_MISMATCH'
```

- **验收**：打印 `DNS_OK`
- **失败**：告知用户去 DNS 服务商添加 A 记录 `DOMAIN → VPS 公网 IP`，等 TTL 过期（通常 5–60 分钟）后重试此步

---

## 2. 上传代码到 VPS

### 2.1 同步项目到 `/opt/zm-auto`

在**本地工作站**项目根目录运行：

```bash
rsync -avz --delete \
  -e "ssh -p ${VPS_SSH_PORT}" \
  --exclude '.git' --exclude '__pycache__' --exclude '*.pyc' \
  --exclude 'data/' --exclude 'config.json' --exclude 'accounts.json' \
  --exclude '.env' --exclude '*.log' --exclude '*.tmp' \
  --exclude '.DS_Store' --exclude 'Thumbs.db' \
  ./ "${VPS_SSH_USER}@${VPS_SSH_HOST}:/tmp/zm-auto-upload/"
```

> Windows 工作站若无 rsync：用 `scp -r` 或 `git clone`（私有仓库走 deploy key）。再不济用 `tar | ssh` 管道传输。

### 2.2 移到 `/opt/zm-auto` 并授权

```bash
ssh -p "${VPS_SSH_PORT}" "${VPS_SSH_USER}@${VPS_SSH_HOST}" '
  sudo mkdir -p /opt/zm-auto
  sudo rsync -a /tmp/zm-auto-upload/ /opt/zm-auto/
  sudo rm -rf /tmp/zm-auto-upload
  sudo chown -R $USER:$USER /opt/zm-auto
  ls /opt/zm-auto/{docker-compose.yml,Dockerfile,Caddyfile,entrypoint.sh,.env.example}
'
```

- **验收**：列出 5 个文件全部存在
- **失败**：rsync 同步异常 → 检查本地路径 / 网络中断

---

## 3. 生成 `.env`

```bash
ssh -p "${VPS_SSH_PORT}" "${VPS_SSH_USER}@${VPS_SSH_HOST}" 'cd /opt/zm-auto && cp .env.example .env && chmod 600 .env'

# 注入变量（在远端用 python 生成 SECRET_KEY，避免本地/远端依赖差异）
ssh -p "${VPS_SSH_PORT}" "${VPS_SSH_USER}@${VPS_SSH_HOST}" "cd /opt/zm-auto && \
  SECRET_KEY_GEN=\$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))' 2>/dev/null || openssl rand -base64 32 | tr -d '\n') && \
  sed -i \
    -e 's|^DOMAIN=.*|DOMAIN=${DOMAIN}|' \
    -e 's|^WEB_USERNAME=.*|WEB_USERNAME=${WEB_USERNAME}|' \
    -e 's|^WEB_PASSWORD=.*|WEB_PASSWORD=${WEB_PASSWORD}|' \
    -e \"s|^SECRET_KEY=.*|SECRET_KEY=\${SECRET_KEY_GEN}|\" \
    .env && \
  echo '.env 已写入，共 '\$(grep -c '=' .env)' 个字段'"
```

- **验收**：输出 `.env 已写入，共 4 个字段`
- **失败**：检查 heredoc / sed 转义；密码含特殊字符（`$`/`!`/`"`）时改用 `cat > .env <<EOF ... EOF`

---

## 4. 构建并启动

### 4.1 构建镜像并启动容器

```bash
ssh -p "${VPS_SSH_PORT}" "${VPS_SSH_USER}@${VPS_SSH_HOST}" '
  cd /opt/zm-auto
  sudo docker compose pull caddy
  sudo docker compose build --pull app
  sudo docker compose up -d
'
```

- **验收**：末尾打印 `Container zm-auto-app-1  Started` 和 `Container zm-auto-caddy-1  Started`
- **失败**：执行 `sudo docker compose logs app caddy --tail 100` 查错误

### 4.2 等待 app 容器健康（最长 60 秒）

```bash
for i in $(seq 1 12); do
  status=$(ssh -p "${VPS_SSH_PORT}" "${VPS_SSH_USER}@${VPS_SSH_HOST}" 'cd /opt/zm-auto && sudo docker inspect -f "{{json .State.Health.Status}}" zm-auto-app-1 2>/dev/null | tr -d "\""')
  echo "[$i/12] app health: ${status:-unknown}"
  [ "$status" = "healthy" ] && break
  sleep 5
done
```

- **验收**：循环退出时 `status = healthy`
- **失败**：

```bash
ssh -p "${VPS_SSH_PORT}" "${VPS_SSH_USER}@${VPS_SSH_HOST}" 'cd /opt/zm-auto && sudo docker compose logs app --tail 100'
```

常见原因：`.env` 字段缺失 / 8000 端口未释放 / requirements 安装失败。

### 4.3 等待 Caddy 签发证书（最长 2 分钟）

```bash
for i in $(seq 1 8); do
  code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "https://${DOMAIN}/login")
  echo "[$i/8] https://${DOMAIN}/login → HTTP ${code}"
  [ "$code" = "200" ] && break
  sleep 15
done
```

- **验收**：`code = 200`
- **失败**：见 [故障排查 A](#a-caddy-证书签发失败)

---

## 5. 端到端验证

### 5.1 HTTPS 重定向

```bash
curl -sSI "http://${DOMAIN}/login" -o /dev/null -w '%{http_code} %{redirect_url}\n'
```

- **验收**：`301` 或 `308`，`redirect_url` 以 `https://` 开头

### 5.2 未登录 API 守护

```bash
curl -sS -o /dev/null -w '%{http_code}\n' "https://${DOMAIN}/api/status"
```

- **验收**：`401`

### 5.3 登录流程

```bash
COOKIE=/tmp/zm-auto-cookie.jar
rm -f "$COOKIE"

# 提交登录表单（关闭自动 follow，手动读 Set-Cookie）
curl -sS -c "$COOKIE" -o /dev/null -w 'login POST → %{http_code}\n' \
  -X POST "https://${DOMAIN}/login" \
  --data-urlencode "username=${WEB_USERNAME}" \
  --data-urlencode "password=${WEB_PASSWORD}" \
  --data-urlencode "next=/"

# 携带 cookie 访问受保护接口
curl -sS -b "$COOKIE" -w '\napi/status → %{http_code}\n' "https://${DOMAIN}/api/status"
```

- **验收**：第一行 `login POST → 303`（或 `200`），第二行 `api/status → 200`
- **失败**（401）：用户名/密码错误或 `.env` 未正确写入
- **失败**（500）：`SECRET_KEY` 含 shell 特殊字符 → 重生成并 `--force-recreate app`

### 5.4 安全响应头

```bash
curl -sSI "https://${DOMAIN}/login" | grep -Ei 'strict-transport-security|x-frame-options|x-content-type-options|referrer-policy|cross-origin-opener-policy'
```

- **验收**：打印 5 行，每行包含对应 header
- **失败**：Caddyfile 未生效 → `docker compose restart caddy`

### 5.5 提示用户人工验证

向用户输出：

> 部署成功。请在浏览器访问 `https://${DOMAIN}/login`，用 `${WEB_USERNAME}` 登录，确认看到「运行 / 配置 / 账号」三个 Tab。

---

## 6. 运维收尾

### 6.1 Docker 开机自启

```bash
ssh -p "${VPS_SSH_PORT}" "${VPS_SSH_USER}@${VPS_SSH_HOST}" 'sudo systemctl enable docker'
```

### 6.2 防火墙（仅放行 22 / 80 / 443）

```bash
ssh -p "${VPS_SSH_PORT}" "${VPS_SSH_USER}@${VPS_SSH_HOST}" '
if command -v ufw >/dev/null 2>&1; then
  sudo ufw allow 22/tcp && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp
  sudo ufw --force enable
  sudo ufw status verbose
elif command -v firewall-cmd >/dev/null 2>&1; then
  sudo firewall-cmd --permanent --add-service=ssh
  sudo firewall-cmd --permanent --add-service=http
  sudo firewall-cmd --permanent --add-service=https
  sudo firewall-cmd --reload
  sudo firewall-cmd --list-all
else
  echo "NO_FIREWALL_TOOL"
fi
'
```

- **验收**：列出 22/80/443 放行规则
- **失败**：未安装防火墙工具 → 告知用户依赖云厂商安全组限制端口

### 6.3 备份提示（输出给用户，不自动执行）

```
关键数据：/opt/zm-auto/data/
  ├── config.json      （含 2captcha / API key 等敏感配置）
  └── accounts.json    （已注册账号的 API key）

建议：每日 cron 任务打包到异地（OSS/S3/其他 VPS）
示例：
  0 3 * * * tar czf /backup/zm-auto-$(date +\%F).tar.gz /opt/zm-auto/data/
```

### 6.4 清理本地 cookie jar

```bash
rm -f /tmp/zm-auto-cookie.jar
```

---

## 故障排查

### A. Caddy 证书签发失败

**症状**：`curl https://${DOMAIN}/login` 报 SSL 错误或 502。

**排查**：

```bash
ssh -p "${VPS_SSH_PORT}" "${VPS_SSH_USER}@${VPS_SSH_HOST}" 'cd /opt/zm-auto && sudo docker compose logs caddy --tail 100'
```

**常见原因 / 处理**：

| 原因                                | 处理                                                                  |
| ----------------------------------- | --------------------------------------------------------------------- |
| DNS 未生效                          | 等 TTL 过期（最长 1 小时）；`dig +short ${DOMAIN}` 验证               |
| VPS 80 端口被云厂商安全组拦截        | 在云控制台开放 80/443 入站                                            |
| Let's Encrypt 限频（5 次/同域名/周） | 改用 `acme_ca_dir` 指向 staging，或等次日                              |
| 之前装过 Caddy 但证书已过期          | `sudo docker compose down -v && sudo docker volume rm zm-auto_caddy_data` 再重启 |

### B. app 容器 crash / 不健康

```bash
ssh -p "${VPS_SSH_PORT}" "${VPS_SSH_USER}@${VPS_SSH_HOST}" 'cd /opt/zm-auto && sudo docker compose logs app --tail 200'
```

| 原因                       | 处理                                                                          |
| -------------------------- | ----------------------------------------------------------------------------- |
| `.env` 缺字段              | `cat .env \| grep -c '='` 应 ≥ 4                                              |
| 8000 端口被宿主机占用       | 见 [1.4](#14-端口占用检查)                                                     |
| `requirements.txt` 装失败   | 镜像 build 时报错；`docker compose build --no-cache app`                      |

### C. 登录返回 500

罕见。通常是 `SECRET_KEY` 含 shell 特殊字符或太短。

```bash
ssh -p "${VPS_SSH_PORT}" "${VPS_SSH_USER}@${VPS_SSH_HOST}" 'cd /opt/zm-auto && grep "^SECRET_KEY=" .env | awk -F= "{print length(\`\$2\`)}"'
```

- 长度 < 32 → 重生成：

```bash
ssh -p "${VPS_SSH_PORT}" "${VPS_SSH_USER}@${VPS_SSH_HOST}" 'cd /opt/zm-auto && \
  NEW_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))") && \
  sed -i "s|^SECRET_KEY=.*|SECRET_KEY=${NEW_KEY}|" .env && \
  sudo docker compose up -d --force-recreate app'
```

> 注意：重建后所有已登录 session 失效。

### D. 部署后修改账号 / 密码

```bash
ssh -p "${VPS_SSH_PORT}" "${VPS_SSH_USER}@${VPS_SSH_HOST}" 'cd /opt/zm-auto && \
  sudo nano .env && \
  sudo docker compose up -d --force-recreate app'
```

### E. 完全卸载

```bash
ssh -p "${VPS_SSH_PORT}" "${VPS_SSH_USER}@${VPS_SSH_HOST}" 'cd /opt/zm-auto && \
  sudo docker compose down -v && \
  cd / && sudo rm -rf /opt/zm-auto'
```

> **警告**：`-v` 会删除 named volumes（Caddy 证书），但 `/opt/zm-auto/data/` 挂载在 bind mount，需要单独 `rm -rf /opt/zm-auto` 才会删。执行前**必须**告知用户。

---

## 重要约束（Claude Code 必读）

- **禁止** 把 `.env`、`config.json`、`accounts.json`、`data/` 提交到 git（项目 `.gitignore` 已含，勿改动）
- **禁止** 把面板暴露公网而不启用认证（`WEB_USERNAME` / `WEB_PASSWORD` / `SECRET_KEY` 三者必须同时设置）
- **禁止** 把 8000 端口直接暴露公网（只让 caddy 反代 80/443）
- 任何破坏性操作（删容器 / 删数据卷 / 改密码 / 清空账号）执行**前**必须先告知用户并等待确认
- SSH 命令失败时优先看 stderr，对同一操作不要重试超过 3 次
- 每一步的 `ssh` 调用都会新开连接；若用户 SSH 有限频，可改为「一次 ssh 进 VPS，本地执行多条命令」
- 部署成功后向用户汇总：
  1. 访问域名（`https://${DOMAIN}/`）
  2. 登录用户名（`${WEB_USERNAME}`）
  3. 数据备份位置（`/opt/zm-auto/data/`）
  4. 常用运维命令（`docker compose ps` / `logs` / `restart`）

## 附：常用运维命令速查

```bash
# 进入 VPS 项目目录后：
sudo docker compose ps                     # 查看容器状态
sudo docker compose logs -f app            # 跟踪 app 日志
sudo docker compose logs -f caddy          # 跟踪 Caddy 日志
sudo docker compose restart app            # 重启 app（保留数据）
sudo docker compose up -d --build app      # 改代码后重建 app
sudo docker compose down                   # 停止并删除容器（数据保留）
sudo docker compose down -v                # 同上 + 删除命名卷（含证书）
sudo ls -la /opt/zm-auto/data/             # 查看持久化数据
```
