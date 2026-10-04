"""运行期配置：全部来自环境变量，均带默认值，便于容器化部署。"""
from __future__ import annotations

import os


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# SQLite 数据库文件路径；容器内默认挂载卷目录，本地默认实例目录。
DATABASE_PATH = os.getenv("DATABASE_PATH", "/data/licensing.db")

# 服务监听地址与端口。
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8080"))

# Compose 启动时是否写入演示数据（幂等，已存在则跳过）。
SEED_DEMO_DATA = _bool("SEED_DEMO_DATA", True)
