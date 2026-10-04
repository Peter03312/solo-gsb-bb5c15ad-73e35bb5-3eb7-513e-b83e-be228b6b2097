#!/bin/sh
# 容器入口：先完成依赖数据库初始化与（可选）演示数据写入，再启动 HTTP 服务。
set -e

echo "[entrypoint] initializing database schema at ${DATABASE_PATH:-/data/licensing.db}"
python -m app.init_db

if [ "${SEED_DEMO_DATA:-true}" = "true" ]; then
  echo "[entrypoint] seeding demo data (idempotent)"
  python -m app.seed || echo "[entrypoint] seed skipped/failed (non-fatal)"
fi

echo "[entrypoint] starting uvicorn on ${HOST:-0.0.0.0}:${PORT:-8080}"
exec uvicorn app.main:app --host "${HOST:-0.0.0.0}" --port "${PORT:-8080}"
