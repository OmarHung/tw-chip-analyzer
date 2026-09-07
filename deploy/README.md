# 部署到 Ubuntu 主機

tw_chip_analyzer 正式部署。架構為 nginx 同源反向代理，前端 Next.js 與後端 FastAPI
同網域（免 CORS），PostgreSQL 儲存，每交易日盤後（EOD）自動累積資料。

```
瀏覽器 ─HTTPS→ nginx ┬ /      → Next.js  (127.0.0.1:3000)  前端
                     └ /api/* → uvicorn  (127.0.0.1:8000)  後端 FastAPI
                                   PostgreSQL 16 ◄┘
        排程(週一–五 14:30 Asia/Taipei) → scripts/eod.sh 每日累積
```

## 兩種部署方式（擇一）

| 方式 | 文件 | 適合 |
|---|---|---|
| **Docker Compose** | [docker.md](docker.md) | 快速上手、環境隔離、少碰主機依賴（推薦） |
| **裸機 systemd** | [systemd.md](systemd.md) | 不想用容器、貼近主機、無容器負擔 |

兩者的對外 nginx 同源代理做法相同，各文件內都有對應步驟。

**存取層**（如何連到服務）：
- **公開網域 + TLS**：nginx + certbot（見 docker.md / systemd.md）。
- **Tailscale 跳板（不需網域）**：[tailscale.md](tailscale.md)——`tailscale serve` 自動
  配 `*.ts.net` HTTPS，免網域免 certbot，只有 tailnet 成員能連。

## 檔案清單

| 檔案 | 用途 |
|---|---|
| `docker.md` / `systemd.md` | 兩種部署方式的完整步驟 |
| `tailscale.md` | 用 Tailscale 存取（免網域，`tailscale serve` 自動 HTTPS） |
| `env.example` | systemd 版 `.env` 範本（`DATABASE_URL` + Shioaji 金鑰） |
| `docker/env.example` | Docker 版 `.env` 範本（`POSTGRES_PASSWORD` + 網域） |
| `nginx/twchip.conf` | nginx 反向代理設定（兩種方式共用） |
| `systemd/twchip-{api,web,eod}.service` `.timer` | systemd 單元 |
| `install.sh` | systemd 版一鍵佈署（替換佔位符 → 啟用服務） |
| `com.twchip.eod.plist` | macOS launchd 版（開發機用，Ubuntu 不需要） |

根目錄另有 Docker 相關檔：`Dockerfile`、`docker-entrypoint.sh`、`docker-compose.yml`、
`frontend/Dockerfile`。

## 跨方式共通重點

1. **單 worker**：API 不可多 worker——APScheduler 在 lifespan 內，多實例會重複跑 EOD。
2. **同源免 CORS**：後端 CORS 只放行 `localhost`（見 `app/main.py`），靠 nginx 同源代理。
3. **`NEXT_PUBLIC_API_BASE` build 時烤入**：設公開網域；改網域要重新 build 前端。
4. **逐筆非必需**：不填 `SJ_*` 金鑰也能運作，只是 intraday 中性，不影響法人/背離/主力成本。
5. **秘密**：`.env` 已 gitignore、勿進版控；憑證（Shioaji CA）亦然。
