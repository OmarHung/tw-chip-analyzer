"""可從 UI 調整的門檻登錄表 + 覆寫合併 / 驗證 / 資料版本。

設定來源分兩層：`config/thresholds.yaml` 是預設值與完整結構（走 git 版控），DB 只存
「覆寫值」。只有本登錄表列出的鍵可覆寫——其餘設定（排程、TDCC 級距、回測 bucket 等）
仍只能改 YAML，避免 UI 改到結構性設定。

effect：
- live    ：決策/風控/批次門檻，下次請求即生效（分析結果即時計算）。
- rebuild ：影響已落地的 feature_daily / signal_snapshot，改完需重建歷史分數，
            否則新舊分數混在同一段驗證資料裡。

locked：未經 OOS 驗證而刻意設為 0 的權重（SBL、產業趨勢）。紀律是「未經驗證不進分數」，
UI 需明確解鎖才可修改，且會寫入修改歷史。
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal

Effect = Literal["live", "rebuild"]
Kind = Literal["float", "int", "choice"]


@dataclass(frozen=True)
class SettingSpec:
    key: str
    label: str
    group: str
    effect: Effect
    kind: Kind = "float"
    min: float | None = None
    max: float | None = None
    choices: tuple[str, ...] = ()
    locked: bool = False
    help: str = ""


def _s(key: str, label: str, group: str, effect: Effect, **kw: Any) -> SettingSpec:
    return SettingSpec(key=key, label=label, group=group, effect=effect, **kw)


_SPECS: tuple[SettingSpec, ...] = (
    # --- 訊號分級（live）---
    _s("signal.buy_score", "BUY 分數門檻", "訊號分級", "live", min=0, max=100),
    _s("signal.watch_score", "WATCH 分數門檻", "訊號分級", "live", min=0, max=100),
    _s("signal.reduce_score", "REDUCE 分數門檻（持倉）", "訊號分級", "live", min=0, max=100),
    _s("signal.exit_score", "EXIT 分數門檻（持倉）", "訊號分級", "live", min=0, max=100),
    # --- Entry Filter ---
    _s("entry_filter.min_risk_reward", "最低 RR", "進場過濾", "live", min=0, max=10),
    _s("entry_filter.max_vwap_deviation_pct", "收盤高於 VWAP 上限", "進場過濾", "live", min=0, max=1),
    _s("entry_filter.max_ma20_deviation_pct", "收盤高於 MA20 上限", "進場過濾", "live", min=0, max=1),
    _s("entry_filter.min_turnover", "最低成交金額（TWD）", "進場過濾", "live", min=0, max=1e12),
    _s("entry_filter.strong_bear_market_score", "Strong Bear 門檻（原始大盤趨勢）", "進場過濾",
       "live", min=-1, max=1),
    _s("entry_filter.limit_lock_min_change_pct", "鎖死漲停漲幅門檻", "進場過濾", "rebuild",
       min=0, max=0.2, help="is_limit_locked 於建特徵時計算"),
    # --- 風控（多數 live；壓力位於建特徵時計算）---
    _s("risk.atr_stop_multiplier", "停損 ATR 倍數", "風控", "live", min=0, max=10),
    _s("risk.swing_buffer_atr", "波段低點緩衝（ATR）", "風控", "live", min=0, max=5),
    _s("risk.max_stop_pct", "最大停損比例", "風控", "live", min=0, max=0.5),
    _s("risk.tp1_r", "TP1（R 倍數）", "風控", "live", min=0, max=20),
    _s("risk.tp2_r", "TP2（R 倍數）", "風控", "live", min=0, max=20),
    _s("risk.entry_low_atr", "進場區下緣（ATR）", "風控", "live", min=0, max=5),
    _s("risk.entry_high_atr", "進場區上緣（ATR）", "風控", "live", min=0, max=5),
    _s("risk.min_risk_pct", "風險分母下限比例", "風控", "live", min=0.0001, max=0.5),
    _s("risk.breakout_policy", "突破股處理", "風控", "live", kind="choice",
       choices=("watch", "atr_projection"),
       help="watch＝突破前高不給 BUY；atr_projection＝以 ATR 推估目標（未經 OOS 驗證）"),
    _s("risk.breakout_target_atr", "突破後目標（ATR 倍數）", "風控", "live", min=0, max=20,
       help="僅 atr_projection 用於 RR；未經回測驗證"),
    _s("risk.resistance_lookback_bars", "壓力位回看根數", "風控", "rebuild", kind="int",
       min=5, max=250, help="resistance_high 於建特徵時計算"),
    _s("risk.resistance_min_bars", "壓力位最少根數", "風控", "rebuild", kind="int", min=1, max=250),
    # --- 特徵（rebuild）---
    _s("features.price_window_days", "價格載入視窗（日曆日）", "特徵", "rebuild", kind="int",
       min=30, max=400),
    _s("features.winsorize_quantile", "截尾分位數", "特徵", "rebuild", min=0, max=0.2),
    _s("features.z_clip", "z 上限", "特徵", "rebuild", min=1, max=20),
    _s("scoring.mapping", "分數映射", "特徵", "rebuild", kind="choice",
       choices=("percentile", "linear")),
    # --- 權重（rebuild）---
    _s("weights.composite.intraday", "盤中", "權重：合成", "rebuild", min=0, max=1),
    _s("weights.composite.institutional", "法人/信用", "權重：合成", "rebuild", min=0, max=1),
    _s("weights.composite.holder", "集保", "權重：合成", "rebuild", min=0, max=1),
    _s("weights.composite.market", "大盤", "權重：合成", "rebuild", min=0, max=1),
    _s("weights.institutional.trust", "投信", "權重：法人", "rebuild", min=-1, max=1),
    _s("weights.institutional.foreign", "外資", "權重：法人", "rebuild", min=-1, max=1),
    _s("weights.institutional.dealer", "自營商", "權重：法人", "rebuild", min=-1, max=1),
    _s("weights.institutional.margin_change", "融資變化", "權重：法人", "rebuild", min=-1, max=1),
    _s("weights.institutional.short_change", "融券變化", "權重：法人", "rebuild", min=-1, max=1),
    _s("weights.institutional.sbl_change", "借券變化", "權重：法人", "rebuild", min=-1, max=1,
       locked=True, help="未經 OOS 驗證，權重刻意為 0"),
    _s("weights.holder.large_holder_change", "大戶持股變化", "權重：集保", "rebuild", min=-1, max=1),
    _s("weights.holder.retail_holder_change", "散戶持股變化", "權重：集保", "rebuild", min=-1, max=1),
    _s("weights.holder.holder_count_change", "股東人數變化", "權重：集保", "rebuild", min=-1, max=1),
    _s("weights.market.market_trend", "大盤趨勢", "權重：大盤", "rebuild", min=0, max=1),
    _s("weights.market.industry_trend", "產業趨勢", "權重：大盤", "rebuild", min=0, max=1,
       locked=True, help="未經 OOS 驗證，權重刻意為 0"),
    _s("weights.intraday.large_trade_delta", "大單淨額", "權重：盤中", "rebuild", min=0, max=1),
    _s("weights.intraday.cvd", "主動買賣", "權重：盤中", "rebuild", min=0, max=1),
    _s("weights.intraday.absorption", "吸收", "權重：盤中", "rebuild", min=0, max=1),
    _s("weights.intraday.cvd_slope", "CVD 斜率", "權重：盤中", "rebuild", min=0, max=1),
    _s("weights.intraday.trade_speed", "成交加速", "權重：盤中", "rebuild", min=0, max=1),
    _s("weights.intraday.price_efficiency", "價格效率", "權重：盤中", "rebuild", min=0, max=1),
    # --- 逐筆批次（live，下次批次生效）---
    _s("intraday_batch.max_symbols", "逐筆批次檔數上限", "逐筆批次", "live", kind="int",
       min=0, max=3000),
    _s("intraday_batch.min_turnover", "逐筆批次成交金額下限", "逐筆批次", "live", min=0, max=1e12),
    _s("intraday_batch.usage_stop_pct", "配額停止百分比", "逐筆批次", "live", min=0, max=100),
    _s("intraday_batch.max_consecutive_failures", "連續失敗停止檔數", "逐筆批次", "live",
       kind="int", min=1, max=1000),
)

REGISTRY: dict[str, SettingSpec] = {s.key: s for s in _SPECS}

# 影響已落地資料（feature_daily / market_daily / signal_snapshot）的 YAML 區塊。
# data_version 取這些區塊 + 登錄表中 effect=rebuild 的鍵；不在登錄表的 YAML 修改
# （例如 tdcc 級距）同樣會改變版本，重建提示才不會漏。
_DATA_SECTIONS = ("weights", "features", "scoring", "tdcc", "market_regime", "large_trade")


def get_path(data: dict, key: str) -> Any:
    node: Any = data
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _with_path(data: dict, key: str, value: Any) -> dict:
    out = copy.deepcopy(data)
    node = out
    parts = key.split(".")
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value
    return out


def apply_overrides(base: dict, overrides: dict[str, Any]) -> dict:
    """回傳合併覆寫後的新 dict（不修改 base）。不在登錄表或 YAML 已無此鍵者忽略。"""
    out = copy.deepcopy(base)
    for key, value in overrides.items():
        if key in REGISTRY and get_path(out, key) is not None:
            out = _with_path(out, key, value)
    return out


def validate(key: str, value: Any, unlock: bool = False) -> Any:
    """驗證並轉型。未知鍵 KeyError、鎖定未解鎖 PermissionError、型別/範圍 ValueError。"""
    spec = REGISTRY.get(key)
    if spec is None:
        raise KeyError(key)
    if spec.locked and not unlock:
        raise PermissionError(f"{key} 已鎖定（{spec.help}），需明確解鎖")
    if spec.kind == "choice":
        if value not in spec.choices:
            raise ValueError(f"{key} 必須為 {', '.join(spec.choices)} 之一")
        return value
    try:
        num = float(value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{key} 必須為數字") from e
    if num != num:  # NaN
        raise ValueError(f"{key} 不可為 NaN")
    if spec.kind == "int":
        if not num.is_integer():
            raise ValueError(f"{key} 必須為整數")
        num = int(num)
    if spec.min is not None and num < spec.min:
        raise ValueError(f"{key} 不可小於 {spec.min}")
    if spec.max is not None and num > spec.max:
        raise ValueError(f"{key} 不可大於 {spec.max}")
    return num


def data_version(data: dict) -> str:
    """影響已落地資料的設定指紋（12 碼）。只改 live 設定不變；改權重/特徵參數會變。"""
    payload: dict[str, Any] = {sec: data.get(sec) for sec in _DATA_SECTIONS}
    payload["_rebuild_keys"] = {
        k: get_path(data, k) for k, s in REGISTRY.items() if s.effect == "rebuild"
    }
    raw = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha1(raw).hexdigest()[:12]
