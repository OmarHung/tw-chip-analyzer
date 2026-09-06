# 開發文件索引

本目錄由 `tw_stock_chip_analysis_coding_agent_handoff.md`（v1.0）拆分而來，依主題分檔，方便查閱與維護。原始交接文件仍保留於專案根目錄作為單一來源存檔。

| 檔案 | 內容 | 原文件章節 |
|------|------|-----------|
| [01-overview-architecture.md](01-overview-architecture.md) | 專案目標、核心設計原則、系統架構、技術選型、目錄結構 | §1–5 |
| [02-data-and-schema.md](02-data-and-schema.md) | 資料來源與責任、主要資料表、Tick 寫入策略與 Retention | §6, §7, §20 |
| [03-algorithms-scoring.md](03-algorithms-scoring.md) | Order Flow 演算法、Feature Normalization、Score 設計、Signal 分級 | §8–11 |
| [04-decision-risk.md](04-decision-risk.md) | Entry Filter、Risk Engine、Exit Logic | §12–14 |
| [05-api-ui.md](05-api-ui.md) | API 規格、UI（Dashboard / Scanner / Detail） | §15–16 |
| [06-backtest-validation.md](06-backtest-validation.md) | Backtest、Look-ahead 防護、Testing、Paper Trading | §17, §18, §22, §23 |
| [07-ops-config.md](07-ops-config.md) | 排程（盤後 / 週 / Realtime）、Config | §19, §21 |
| [08-roadmap-delivery.md](08-roadmap-delivery.md) | Roadmap、開發 Phase 與順序、MVP DoD、成功標準、Milestone 交付格式、第一任務 | §24–31 |

> 專案不變量（鐵則）與快速導覽見專案根目錄的 `CLAUDE.md`。
