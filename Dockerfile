# tw_chip_analyzer 後端（FastAPI + APScheduler EOD）。
# 注意:shioaji 為 linux/amd64 wheel;在 Apple Silicon build 請加 --platform linux/amd64。
FROM python:3.12-slim

# tzdata 供 APScheduler Asia/Taipei;ca-certificates 供 httpx 抓 TWSE/TDCC。
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# PY=python:scripts/eod.sh 預設 .venv/bin/python,容器內改用系統 python
ENV TZ=Asia/Taipei \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PY=python

WORKDIR /app

# 先裝相依,善用快取層
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# 啟動前跑 alembic upgrade head(冪等),再 exec CMD
ENTRYPOINT ["./docker-entrypoint.sh"]

EXPOSE 8000
# 單 worker:APScheduler 在 lifespan 內,多 worker 會重複跑 EOD
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
