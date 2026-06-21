# zm-auto 镜像：Python 3.12-slim + curl_cffi + FastAPI server
# 本地构建：docker build -t zm-auto:latest .
FROM python:3.12-slim

# curl_cffi 走 wheel 不需要编译，但保留 gcc 兜底（极少数发行版 wheel 不匹配时）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先装依赖（利用 docker layer 缓存，代码改动不会重装）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目代码（.dockerignore 已排除 data/ .git 等）
COPY . .

# entrypoint 可执行
RUN chmod +x /app/entrypoint.sh

# 持久化目录（host volume 挂载到这里）
VOLUME ["/app/data"]

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["python", "server.py"]
