-- raw_tick 並發重複寫入清理（修正見 commit 064d702，app/services/ticks.py advisory lock）
--
-- 判定：同一 symbol-date 內「每一筆不重複成交」出現次數都相同且 >= 2（整批被寫成 k 倍），
-- 才視為並發重複；只保留每組相同成交中 id 最小的一筆（第一批寫入）。
-- 正常資料即使偶有完全相同的兩筆成交，也不會整天每筆都剛好 k 倍，不會被誤刪。
-- 需全表掃描（數千萬列），1 vCPU 主機約數分鐘；請避開 EOD（平日 14:30）。
--
-- 用法：psql ... -v ON_ERROR_STOP=1 -f dedupe_raw_tick.sql
-- 預設最後 COMMIT；只想看影響範圍時把最後一行改成 ROLLBACK。

BEGIN;

CREATE TEMP TABLE dup_pairs ON COMMIT DROP AS
WITH t AS (
  SELECT symbol, data_date, count(*) AS c
  FROM public.raw_tick
  GROUP BY symbol, data_date, ts, price, volume, bid_price, ask_price, aggressor_side
)
SELECT symbol, data_date, min(c) AS k, sum(c) AS rows_before, count(*) AS unique_ticks
FROM t
GROUP BY symbol, data_date
HAVING min(c) >= 2 AND min(c) = max(c);

SELECT data_date, symbol, k, rows_before, unique_ticks FROM dup_pairs ORDER BY data_date, symbol;

DELETE FROM public.raw_tick r
USING (
  SELECT id FROM (
    SELECT rt.id,
           row_number() OVER (
             PARTITION BY rt.symbol, rt.data_date, rt.ts, rt.price, rt.volume,
                          rt.bid_price, rt.ask_price, rt.aggressor_side
             ORDER BY rt.id
           ) AS rn
    FROM public.raw_tick rt
    JOIN dup_pairs p USING (symbol, data_date)
  ) x
  WHERE rn > 1
) d
WHERE r.id = d.id;

-- 驗證：清理後這些 symbol-date 的筆數應等於 unique_ticks（查無資料＝正確）
SELECT p.data_date, p.symbol, p.unique_ticks, count(r.id) AS rows_after
FROM dup_pairs p
JOIN public.raw_tick r USING (symbol, data_date)
GROUP BY 1, 2, 3
HAVING count(r.id) <> p.unique_ticks;

-- 需重建特徵的交易日
SELECT min(data_date) AS rebuild_start, max(data_date) AS rebuild_end, count(DISTINCT data_date) AS days
FROM dup_pairs;

COMMIT;
