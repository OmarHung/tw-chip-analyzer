# 17 · 全站認證與權限（2026-09-17）

> 本文是認證機制的單一真相：規則、啟用步驟、相容模式、已知限制。
> 程式：`app/api/auth.py`、`app/api/auth_deps.py`、`app/services/auth.py`、`app/core/security.py`、
> `scripts/manage_users.py`、前端 `app/(auth)/login`、`app/(app)/layout.tsx`、`components/AccountPanel.tsx`。

## 1. 這次解決的問題

在此之前只有**寫入型 ops 端點**受保護（`OPS_API_KEY` 或本機直連，docs/09 RISK-01）。
所有讀取 API（`/api/dashboard`、`/api/scanner`、`/api/stocks/*`、`/api/ops/status`、
`/api/ops/settings` GET…）與整個前端**沒有任何認證**——線上靠 Tailscale 私網當唯一邊界，
一旦哪天改成公開網域或 tailnet 多了一台裝置，全部資料就是公開的。

現在：**除 `/health` 與 `/api/auth/*` 外，所有 API 都需要通過認證**；前端未登入一律導向 `/login`。

## 2. 三條通過路徑

`app/api/auth_deps.current_principal` 依序判定，任一通過即取得身分：

| 路徑 | 用途 | 角色 |
|------|------|------|
| **session cookie** | 瀏覽器登入後 | 依帳號 `role`（admin / viewer） |
| **`X-Ops-Key`** | 排程腳本、curl、非瀏覽器用戶端 | 視為 admin |
| **相容模式** | 系統尚未建立任何啟用帳號 | 讀取放行；寫入沿用舊規則 |

- `require_user`：讀取型 router 的依賴（`app/main.py` 以 `include_router(..., dependencies=...)` 掛上）。
- `require_admin`：寫入型端點（設定、工作、回補、推播、帳號管理）。`require_ops_auth` 是它的
  薄包裝，回傳稽核字串（`session:<帳號>` / `key` / `local`），既有 router 不必改。
- viewer 呼叫寫入端點得到 **403**（不是 401）——前端據此顯示「需要管理者權限」而不是要人重登。

### 相容模式（升級不會把自己鎖在門外）

`app/services/auth.auth_enabled()`：DB **有至少一個啟用中的帳號**時，認證即強制生效。
還沒有帳號時：

- 讀取端點照常開放（與加認證前完全相同）；
- 寫入端點沿用舊規則：設了 `OPS_API_KEY` 就必須帶金鑰，沒設就只接受本機直連（非代理）。

所以 `alembic upgrade head` 之後行為不變，**建立第一個帳號的那一刻**全站才開始要求登入。
既有 497 個測試也因此不需要改寫。判定結果有 5 秒快取（`is_test` 時不快取）。

## 3. 啟用步驟（線上）

```bash
# 1. migration（新增 app_user / user_session 兩張表）
APP_ENV=prod python -m alembic upgrade head

# 2. 建立第一個管理者（密碼互動輸入，不留在 shell 歷史）
APP_ENV=prod python -m scripts.manage_users create <帳號> --role admin

# 3. 重啟 API（讓新 router 依賴生效）、重新整理前端
```

之後的帳號可在網頁「設定 → 帳號管理」新增（admin 才看得到），或繼續用 CLI：

```bash
python -m scripts.manage_users list
python -m scripts.manage_users create alice --role viewer
python -m scripts.manage_users passwd alice          # 重設密碼（該帳號所有 session 失效）
python -m scripts.manage_users role alice admin
python -m scripts.manage_users disable alice / enable alice
python -m scripts.manage_users delete alice [--force]  # --force 才能刪掉最後一個 admin
```

> **第一個帳號只能從 CLI 建**（或在相容模式下由本機直連 / 帶 `OPS_API_KEY` 呼叫
> `POST /api/auth/users`）。這是刻意的：網頁建帳號需要先有 admin 登入。

## 4. 角色

| | admin | viewer |
|---|---|---|
| 看所有頁面與資料 | ✅ | ✅ |
| 改門檻設定 / Telegram 推播 | ✅ | ❌ |
| 觸發腳本工作、回補、重建 | ✅ | ❌ |
| 帳號管理 | ✅ | ❌ |
| 改自己的密碼 | ✅ | ✅ |

前端對 viewer 會停用寫入按鈕並顯示唯讀提示（`components/ReadOnlyNotice.tsx`），
但**授權判定一律在後端**——UI 只是提前告知，不是防線。

## 5. 安全設計

- **密碼**：`hashlib.scrypt`（N=2^14, r=8, p=1，OWASP 建議值），雜湊字串自帶參數，
  日後調參不會讓舊密碼失效。選標準庫而非 bcrypt/argon2 是為了不增加需編譯的相依。
  雜湊是 CPU-bound，一律 `asyncio.to_thread`，不卡 event loop。
- **session**：不透明亂數 token（256 bit），DB 只存 SHA-256 指紋 → DB 外洩無法還原 cookie。
  不用 JWT 是因為要能**立刻撤銷**：停用帳號、改密碼、登出都會當場讓舊 cookie 失效。
- **cookie**：httpOnly、SameSite=Lax、https 時自動加 Secure（`auth.cookie_secure: auto`）。
- **限流**：同帳號或同來源 IP 連續失敗達 `auth.max_failed_attempts` 次即鎖 `auth.lockout_seconds`
  秒（回 429），鎖定期間連正確密碼也擋。狀態在單行程記憶體，重啟即清空。
- **帳號列舉防護**：查無帳號時仍跑一次雜湊，讓「帳號不存在」與「密碼錯」耗時相近。
- **稽核**：登入成功/失敗、登出、改密碼、帳號異動、所有 ops 寫入都寫 `api.ops.audit` log，
  含來源 IP 與轉發鏈；密碼、token、金鑰本身永不入 log。
- **護欄**：最後一個啟用中的 admin 不可被停用、降級或刪除（API 回 409）；不可刪除自己。

## 6. 前端結構

- `app/(app)/*`：受保護頁面。殼層 `app/(app)/layout.tsx` 呼叫 `/api/auth/me`，
  未登入 → `redirect("/login")`，並把身分放進 `AuthProvider`（`useAuth` / `useCanWrite`）。
- `app/(auth)/login`：登入頁，沒有導覽列。尚未建立帳號時會提示 CLI 指令。
- `lib/api.server.ts`：SSR 專用 client，**把使用者的 cookie 轉發給後端**——SSR 不使用共用服務金鑰，
  「誰在看」與「後端授權對象」是同一個人。client component 仍用 `lib/api.ts` 的 `api`
  （瀏覽器自動帶 cookie；跨來源 dev 靠 `credentials: "include"` + CORS `allow_credentials`）。
- 後端連不上時 `getMe()` 回相容模式匿名身分而**不**導向登入頁：否則後端一掛，使用者只會看到
  登不進去的登入頁，誤以為密碼錯；真正的授權判斷本來就在後端。

### dev 注意：cookie 不分 port，但分主機名

`localhost` 與 `127.0.0.1` 是**不同的 cookie host**。若瀏覽器開 `http://localhost:3000`
而 `NEXT_PUBLIC_API_BASE=http://127.0.0.1:8000`，登入後 cookie 存在 `127.0.0.1` 名下，
SSR 讀 `localhost` 的 cookie jar 會看不到 → 登入後又被踢回 `/login`。
**兩邊主機名要一致**（都用 localhost 或都用 127.0.0.1）。線上走 nginx 同源沒有這個問題。

附帶一提：改用 `127.0.0.1` 開網頁會踩到 Next dev server 的另一道保護——它預設只信任
`localhost`，會擋掉 `/_next` 的 dev 資源與 HMR，症狀是畫面畫得出來但 hydration 沒完成
（打字不進 state、登入按鈕按不動）。已在 `next.config.ts` 的 `allowedDevOrigins` 放行兩者；
只影響 dev，production build 不吃這個設定。

## 7. 設定（`config/thresholds.yaml` 的 `auth` 區塊）

```yaml
auth:
  cookie_name: twchip_session
  session_ttl_hours: 336       # 14 天
  renew_within_hours: 168      # 剩餘時效低於此值才續期（減少 DB 寫入）
  max_failed_attempts: 5
  lockout_seconds: 300
  min_password_length: 10
  cookie_secure: auto          # auto / always / never
```

刻意**不**進 `threshold_registry`：安全參數不開放 UI 調整，也不影響已落地資料（不進 `data_version`）。
改完需重啟 API（或觸發 `reload_thresholds()`）。

## 8. API

| 方法 | 路徑 | 權限 | 說明 |
|------|------|------|------|
| GET | `/api/auth/state` | 公開 | 是否已建立帳號（登入頁用） |
| POST | `/api/auth/login` | 公開 | 帳密登入，設 session cookie |
| POST | `/api/auth/logout` | 公開 | 撤銷 session、清 cookie |
| GET | `/api/auth/me` | 公開 | 目前身分（未登入回 401） |
| POST | `/api/auth/password` | 本人 | 改自己的密碼（需舊密碼；其他裝置登出，本機保留） |
| GET | `/api/auth/users` | admin | 帳號列表 |
| POST | `/api/auth/users` | admin | 建立帳號 |
| PATCH | `/api/auth/users/{id}` | admin | 改角色 / 啟用停用 / 顯示名稱 |
| POST | `/api/auth/users/{id}/password` | admin | 代設密碼（強制對方下次登入改密碼） |
| DELETE | `/api/auth/users/{id}` | admin | 刪除帳號 |

## 9. 已知限制

- **`OPS_API_KEY` 仍等同 admin**：這是刻意保留給排程腳本與 curl 的路徑（使用者決定），
  金鑰外洩等於管理者外洩。線上務必設強金鑰或改走帳號。
- **限流狀態在單行程記憶體**：多 worker 或重啟會各自計數。單機單 worker 部署下夠用。
- **沒有 CSRF token**：寫入端點靠 SameSite=Lax cookie + 自訂 header 的組合防護；
  若日後改用 SameSite=None 或加入跨站表單提交，必須補 CSRF。
- **沒有雙因素、沒有密碼過期策略**：單人／小團隊系統，刻意不做。
- **公司商標 `/api/stocks/{symbol}/logo` 也需認證**：同源部署（nginx）下 `<img>` 會自動帶 cookie，
  沒問題；若把前端與 API 放在不同網域，`<img>` 不帶 cookie，熱力圖的商標會退回首字徽章。
- session 清理只在登入時順手 purge 過期列，沒有獨立排程。
