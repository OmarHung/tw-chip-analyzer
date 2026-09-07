# 用 Tailscale 存取（不需公開網域 / certbot）

不對外開網域，改用 Tailscale 私有網路存取。用 **`tailscale serve`** 在 tailnet 內
自動配發 `*.ts.net` 的 HTTPS 憑證——真 TLS、免網域、免 certbot。傳輸本身由
WireGuard 加密。這是取代 [docker.md](docker.md) / [systemd.md](systemd.md) 裡
「網域 + certbot」那一步的存取層，其餘步驟（DB、build、服務）不變。

```
tailnet 裝置瀏覽器 ─HTTPS(WireGuard)→ tailscale serve  (主機 :443, *.ts.net 憑證)
                                       → nginx 127.0.0.1:8080  (同源路由)
                                         ┬ /      → web 127.0.0.1:3000
                                         └ /api/* → api 127.0.0.1:8000
```

前提：要存取的裝置（你的筆電/手機）也要裝 Tailscale 並登入同一 tailnet。

---

## 1. 安裝並加入 tailnet

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
tailscale status          # 記下本機 MagicDNS 名稱：<主機>.<tailnet>.ts.net
```

## 2. 開啟 MagicDNS + HTTPS 憑證

Tailscale admin console → **DNS** →
- 開 **MagicDNS**
- 開 **HTTPS Certificates**

（`tailscale serve` 的自動憑證需要這兩項。）

## 3. nginx 只綁 localhost（給 tailscale serve 接）

沿用 `deploy/nginx/twchip.conf`，但改成聽 `127.0.0.1:8080`、`server_name _`：

```bash
sudo sed -e 's/listen 80;/listen 127.0.0.1:8080;/' \
         -e 's/server_name __DOMAIN__;/server_name _;/' \
         deploy/nginx/twchip.conf | sudo tee /etc/nginx/sites-available/twchip
sudo ln -sf /etc/nginx/sites-available/twchip /etc/nginx/sites-enabled/twchip
sudo nginx -t && sudo systemctl reload nginx
```

（api/web 一樣綁 `127.0.0.1:8000/3000`；Docker 與 systemd 版皆同。）

## 4. tailscale serve 前置 HTTPS

```bash
sudo tailscale serve --bg 8080        # https://<主機>.<tailnet>.ts.net → 127.0.0.1:8080
tailscale serve status                # 確認對應關係
```

## 5. 設定 NEXT_PUBLIC_API_BASE = MagicDNS 名稱，重建前端

前端 API 基底設為這台的 ts.net 名稱（含 https）：

- **Docker**：改根目錄 `.env` 的 `NEXT_PUBLIC_API_BASE=https://<主機>.<tailnet>.ts.net`
  → `docker compose up -d --build web`
- **systemd**：`cd frontend && NEXT_PUBLIC_API_BASE=https://<主機>.<tailnet>.ts.net pnpm build`
  → `sudo systemctl restart twchip-web`

## 6. 驗證

```bash
# 主機自身（MagicDNS 可解析自己）：
curl -s https://<主機>.<tailnet>.ts.net/api/dashboard | head -c 200
# 另一台 tailnet 裝置的瀏覽器開：https://<主機>.<tailnet>.ts.net
```

---

## 重點與眉角

1. **同源免 CORS 照舊**：瀏覽器與 SSR 都走同一個 `https://<主機>.ts.net`，nginx 同源
   路由 `/api`，後端 CORS（只放行 localhost）不受影響。
2. **SSR（個股頁）**：`/stocks/[symbol]` 是 server-side 抓取，需能從執行前端的程序
   連到 `NEXT_PUBLIC_API_BASE`：
   - **systemd**：前端跑在主機上，主機有 MagicDNS 解析與 tailnet 路由 → 直接可用。
   - **Docker**：web 容器預設在 bridge 網路，**解析不到 ts.net 名稱**。給 `web` 服務
     加 `extra_hosts` 把 MagicDNS 名稱指到本機 Tailscale IP（`tailscale ip -4` 取得）：
     ```yaml
     # docker-compose.yml 的 web 服務底下
     web:
       extra_hosts:
         - "<主機>.<tailnet>.ts.net:100.x.x.x"   # 換成 tailscale ip -4
     ```
     （容器送往本機 100.x 的封包經 host 送達其上的 tailscale serve:443，憑證亦相符。）
     只有個股 SSR 頁需要此項；總覽/選股/背離頁為前端抓取，無此需求。
3. **不想要 HTTPS 也行**：略過 tailscale serve，nginx 改聽 Tailscale 介面，直接連
   `http://<主機>.<tailnet>.ts.net`（WireGuard 已加密傳輸），`NEXT_PUBLIC_API_BASE`
   用 `http://...`。差別只是瀏覽器不顯示鎖頭。
4. **對外**：本方案完全不對公網開埠；只有 tailnet 成員能連。若哪天要對公網，才需
   網域 + certbot（見 docker.md / systemd.md 的 nginx+TLS 步驟）或 `tailscale funnel`。
