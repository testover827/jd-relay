#!/bin/bash
set -e

echo ">>> 等待数据库就绪..."
sleep 1

echo ">>> 运行数据库迁移..."
alembic upgrade head 2>/dev/null || echo "   (跳过迁移，使用 create_all)"

echo ">>> 启动 FastAPI 服务..."
exec "$@"
