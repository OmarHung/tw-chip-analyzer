#!/bin/sh
# 容器啟動:先套用 DB migration(冪等),再執行 CMD(uvicorn 或 eod.sh 等)。
set -e

echo "[entrypoint] alembic upgrade head ..."
alembic upgrade head

exec "$@"
