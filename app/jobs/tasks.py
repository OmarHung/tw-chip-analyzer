"""UI 可觸發的腳本白名單（系統頁「工作」按鈕）。

安全模型：
- 只能執行本表列出的 `python -m <module>`，模組名寫死在程式碼，不接受外部傳入。
- 參數只能是 ParamSpec 宣告過的名稱，逐一驗證型別/範圍/選項後組成 argv 陣列，
  以 exec（非 shell）啟動，不存在字串拼接或 shell 注入路徑。
- 會污染資料或屬一次性維運的腳本（seed_dev、backtest_demo、prod_data_upgrade）不列入。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Literal

ParamKind = Literal["date", "int", "float", "bool", "choice"]
Category = Literal["maintenance", "research"]


@dataclass(frozen=True)
class ParamSpec:
    name: str
    kind: ParamKind
    flag: str | None = None  # None = 位置參數（依宣告順序）
    required: bool = False
    min: float | None = None
    max: float | None = None
    choices: tuple[str, ...] = ()
    label: str = ""
    help: str = ""


@dataclass(frozen=True)
class TaskSpec:
    id: str
    module: str
    label: str
    category: Category
    params: tuple[ParamSpec, ...] = ()
    help: str = ""
    # 會覆寫 feature_daily / signal_snapshot（完成後「待重建」提示應消失）
    rebuilds_scores: bool = False


def _date(name: str, label: str, **kw: Any) -> ParamSpec:
    return ParamSpec(name, "date", flag=f"--{name}", label=label, **kw)


_TASKS: tuple[TaskSpec, ...] = (
    TaskSpec(
        "rebuild_signals", "scripts.rebuild_signals", "重建特徵與分數", "maintenance",
        params=(
            _date("start", "起始日"),
            _date("end", "結束日"),
            ParamSpec("min_lookback", "int", flag="--min-lookback", min=0, max=250,
                      label="跳過前 N 個交易日", help="回看視窗不足的日子分數品質差（預設 20）"),
        ),
        help="逐日重建 feature_daily + market_daily + signal_snapshot（覆寫）。全量約十幾分鐘，"
             "1 vCPU 主機執行期間網站會變慢。",
        rebuilds_scores=True,
    ),
    TaskSpec(
        "backfill_tdcc", "scripts.backfill_tdcc", "回補 TDCC 股權分散", "maintenance",
        params=(
            ParamSpec("top", "int", flag="--top", min=1, max=3000, label="成交值前 N 檔",
                      help="預設 300（約 100 分鐘）"),
            ParamSpec("weeks", "int", flag="--weeks", min=1, max=51, label="最近 N 週"),
            ParamSpec("dry_run", "bool", flag="--dry-run", label="只預估不抓"),
        ),
        help="集保個股頁逐檔逐週抓取，耗時長。完成後需重建分數 holder 才會進分數。",
    ),
    TaskSpec(
        "backfill_sbl", "scripts.backfill_sbl", "回補借券 SBL", "maintenance",
        params=(_date("start", "起始日"), _date("end", "結束日"),
                ParamSpec("dry_run", "bool", flag="--dry-run", label="只列缺哪幾天")),
    ),
    TaskSpec(
        "backfill_corporate_actions", "scripts.backfill_corporate_actions", "回補公司行動",
        "maintenance",
        params=(
            ParamSpec("start", "date", required=True, label="起始日"),
            ParamSpec("end", "date", required=True, label="結束日"),
        ),
        help="除權息/面額變更/減資還原因子。完成後需重建分數還原才會生效。",
    ),
    TaskSpec(
        "backfill_history", "scripts.backfill_history", "回填歷史行情", "maintenance",
        params=(
            ParamSpec("months", "int", flag="--months", min=1, max=24, label="近 N 個月"),
            _date("start", "起始日", help="覆蓋近 N 個月"),
            _date("end", "結束日"),
            ParamSpec("dry_run", "bool", flag="--dry-run", label="只列交易日"),
        ),
    ),
    TaskSpec(
        "score_monotonicity", "scripts.score_monotonicity", "分數單調性檢驗", "research",
        params=(ParamSpec("min_turnover", "float", flag="--min-turnover", min=0, max=1e12,
                          label="最低成交金額"),),
        help="各分數 bucket × horizon 的報酬/勝率/MAE 與 IC（§28 成功標準）。",
    ),
    TaskSpec("sbl_factor_oos", "scripts.sbl_factor_oos", "借券因子 OOS", "research",
             help="SBL 權重是否可啟用的依據（看 NW t 與兩 regime 方向）。"),
    TaskSpec("factor_ic_oos", "scripts.factor_ic_oos", "因子 IC OOS", "research"),
    TaskSpec("price_factor_oos", "scripts.price_factor_oos", "價格因子 OOS", "research"),
    TaskSpec(
        "divergence_backtest", "scripts.divergence_backtest", "背離訊號回測", "research",
        params=(
            ParamSpec("window", "int", flag="--window", min=5, max=250, label="觀察窗"),
            ParamSpec("step", "int", flag="--step", min=1, max=60, label="取樣間隔"),
            ParamSpec("min_turnover", "float", flag="--min-turnover", min=0, max=1e12,
                      label="最低成交金額"),
        ),
    ),
    # --- Phase 2 MOPS（shadow-only，不影響正式分數與建議；docs/14）---
    TaskSpec(
        "backfill_mops_transfers", "scripts.backfill_mops_transfers", "回補 MOPS 轉讓申報",
        "maintenance",
        params=(
            ParamSpec("start", "date", flag="--start", required=True, label="起始日"),
            _date("end", "結束日"),
            ParamSpec("dry_run", "bool", flag="--dry-run", label="只列待抓組數"),
        ),
        help="內部人持股轉讓事前申報，每交易日×上市/上櫃各一次請求，已涵蓋日期略過。",
    ),
    TaskSpec(
        "backfill_mops_holdings", "scripts.backfill_mops_holdings", "回補 MOPS 董監持股/質押",
        "maintenance",
        params=(
            ParamSpec("top", "int", flag="--top", min=1, max=3000, label="成交值前 N 檔"),
            ParamSpec("months", "int", flag="--months", min=1, max=60, label="最近 N 月"),
            ParamSpec("dry_run", "bool", flag="--dry-run", label="只預估不抓"),
        ),
        help="逐公司逐月抓網頁，耗時長；回補值可能含事後更正（標 mops_web）。",
    ),
    TaskSpec(
        "rebuild_mops_features", "scripts.rebuild_mops_features", "重建 MOPS shadow 特徵",
        "maintenance",
        params=(_date("start", "起始日"), _date("end", "結束日")),
        help="只寫 mops_shadow_feature_daily，不影響 Chip Score 與建議。",
    ),
    TaskSpec("mops_factor_oos", "scripts.mops_factor_oos", "MOPS 因子 OOS", "research",
             help="單因子 IC（NW t、regime、coverage、對 chip_score 增量）。只出報告，不改權重。"),
)

TASKS: dict[str, TaskSpec] = {t.id: t for t in _TASKS}


def _clean(p: ParamSpec, value: Any) -> str | None:
    """驗證單一參數並轉成 argv 字串；bool 回 "" 表示只帶 flag、None 表示不帶。"""
    if p.kind == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"{p.name} 必須為 true/false")
        return "" if value else None
    if p.kind == "date":
        try:
            return dt.date.fromisoformat(str(value)).isoformat()
        except ValueError as e:
            raise ValueError(f"{p.name} 日期格式錯誤（YYYY-MM-DD）") from e
    if p.kind == "choice":
        if value not in p.choices:
            raise ValueError(f"{p.name} 必須為 {', '.join(p.choices)} 之一")
        return str(value)
    try:
        num = float(value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{p.name} 必須為數字") from e
    if p.kind == "int":
        if not num.is_integer():
            raise ValueError(f"{p.name} 必須為整數")
        num = int(num)
    if p.min is not None and num < p.min:
        raise ValueError(f"{p.name} 不可小於 {p.min}")
    if p.max is not None and num > p.max:
        raise ValueError(f"{p.name} 不可大於 {p.max}")
    return str(num)


def build_argv(spec: TaskSpec, params: dict[str, Any]) -> list[str]:
    known = {p.name for p in spec.params}
    unknown = set(params) - known
    if unknown:
        raise ValueError(f"不支援的參數：{', '.join(sorted(unknown))}")
    positional: list[str] = []
    flags: list[str] = []
    for p in spec.params:
        value = params.get(p.name)
        if value is None or value == "":
            if p.required:
                raise ValueError(f"缺少必要參數：{p.name}")
            continue
        arg = _clean(p, value)
        if arg is None:
            continue
        if p.flag is None:
            positional.append(arg)
        elif arg == "":
            flags.append(p.flag)
        else:
            flags.extend([p.flag, arg])
    return positional + flags


def spec_to_dict(spec: TaskSpec) -> dict:
    return {
        "id": spec.id, "label": spec.label, "category": spec.category, "help": spec.help,
        "rebuilds_scores": spec.rebuilds_scores,
        "params": [
            {"name": p.name, "kind": p.kind, "required": p.required, "min": p.min, "max": p.max,
             "choices": list(p.choices), "label": p.label or p.name, "help": p.help}
            for p in spec.params
        ],
    }
