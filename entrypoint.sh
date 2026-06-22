#!/bin/sh
# entrypoint.sh — 容器启动前初始化 data/ 目录，把 config.json / accounts.json 软链到 data/
# 这样宿主机只需挂载 ./data:/app/data 一个卷，容器重启后配置和账号数据都持久化。
set -e

DATA_DIR=/app/data
mkdir -p "$DATA_DIR"

link_data_file() {
    f="$1"           # config.json 或 accounts.json
    src="$DATA_DIR/$f"
    dst="/app/$f"

    # 1. data/ 里不存在则初始化
    if [ ! -f "$src" ]; then
        case "$f" in
            config.json)
                if [ -f /app/config.example.json ]; then
                    cp /app/config.example.json "$src"
                    echo "[entrypoint] 初始化 $src（从 config.example.json 复制）"
                else
                    echo "{}" > "$src"
                    echo "[entrypoint] 警告：config.example.json 缺失，$src 初始化为空对象"
                fi
                ;;
            accounts.json)
                echo "[]" > "$src"
                echo "[entrypoint] 初始化 $src（空数组）"
                ;;
        esac
    fi

    # 2. dst 已存在则处理：软链 → 删除重建；真实文件 → 迁移到 data/
    if [ -L "$dst" ]; then
        rm "$dst"
    elif [ -e "$dst" ]; then
        # 首次启动时镜像内可能有这些文件（虽然 .dockerignore 排除了），保险迁移
        mv "$dst" "$src"
        echo "[entrypoint] 迁移 $dst → $src"
    fi

    # 3. 建软链
    ln -s "$src" "$dst"
    echo "[entrypoint] 软链 $dst → $src"
}

link_data_file config.json
link_data_file accounts.json

echo "[entrypoint] 初始化完成，启动: $@"
exec "$@"
