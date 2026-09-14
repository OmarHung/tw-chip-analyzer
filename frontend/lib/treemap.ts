/* Squarified treemap（Bruls, Huizing & van Wijk 2000）。
 *
 * 不用 d3-hierarchy：需要的只是一個純函式，不值得為了它拉進一包相依。
 *
 * 為什麼是 squarified 而非簡單的 slice-and-dice：後者在值差距大時會產生極細長的
 * 長條（台股成交值差三個數量級是常態，2330 一檔就佔全市場一成），細長方塊既讀不出
 * 面積也放不下文字。squarified 以「盡量接近正方形」為目標排列，代價是同層順序不再
 * 嚴格線性——對「看哪個大」這個用途是划算的交換。
 */

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** 面積為 0 或負的項目會被略過：treemap 沒有「不佔面積的方塊」這種東西。 */
export interface Sized {
  value: number;
}

function worst(row: number[], length: number, scale: number): number {
  // 這一列目前最差的長寬比。length=0 時視為無限差，避免除以 0。
  if (length <= 0 || row.length === 0) return Infinity;
  const sum = row.reduce((a, b) => a + b, 0) * scale;
  const max = Math.max(...row) * scale;
  const min = Math.min(...row) * scale;
  const side = length * length;
  return Math.max((side * max) / (sum * sum), (sum * sum) / (side * min));
}

/** 把一列已決定的項目沿著短邊鋪開，回傳剩下的空間。 */
function layoutRow<T extends Sized>(
  items: T[],
  values: number[],
  scale: number,
  rect: Rect,
  out: (T & Rect)[],
): Rect {
  const total = values.reduce((a, b) => a + b, 0) * scale;
  const horizontal = rect.w >= rect.h;
  const thickness = horizontal ? total / rect.h : total / rect.w;
  let offset = 0;
  items.forEach((item, i) => {
    const area = values[i] * scale;
    if (horizontal) {
      const h = area / thickness;
      out.push({ ...item, x: rect.x, y: rect.y + offset, w: thickness, h });
      offset += h;
    } else {
      const w = area / thickness;
      out.push({ ...item, x: rect.x + offset, y: rect.y, w, h: thickness });
      offset += w;
    }
  });
  return horizontal
    ? { x: rect.x + thickness, y: rect.y, w: rect.w - thickness, h: rect.h }
    : { x: rect.x, y: rect.y + thickness, w: rect.w, h: rect.h - thickness };
}

/**
 * 把 items 依 value 佔比填滿 rect。回傳的每個項目帶上 x/y/w/h（單位與 rect 相同）。
 * 輸入不需預先排序——函式內部會由大到小排（squarified 的前提）。
 */
export function squarify<T extends Sized>(items: readonly T[], rect: Rect): (T & Rect)[] {
  const usable = items.filter((d) => d.value > 0);
  if (usable.length === 0 || rect.w <= 0 || rect.h <= 0) return [];

  const sorted = [...usable].sort((a, b) => b.value - a.value);
  const total = sorted.reduce((a, b) => a + b.value, 0);
  const scale = (rect.w * rect.h) / total;

  const out: (T & Rect)[] = [];
  let remaining = { ...rect };
  let row: T[] = [];
  let rowValues: number[] = [];

  for (const item of sorted) {
    const short = Math.min(remaining.w, remaining.h);
    const nextValues = [...rowValues, item.value];
    // 加入這一項若讓長寬比變差，就先把目前這列鋪掉，從剩餘空間重新開始。
    if (row.length > 0 && worst(nextValues, short, scale) > worst(rowValues, short, scale)) {
      remaining = layoutRow(row, rowValues, scale, remaining, out);
      row = [];
      rowValues = [];
    }
    row.push(item);
    rowValues.push(item.value);
  }
  if (row.length > 0) layoutRow(row, rowValues, scale, remaining, out);
  return out;
}

/** 群組內縮。top 通常大於其他邊，用來空出群組標題的位置。 */
export interface GroupPadding {
  top: number;
  right: number;
  bottom: number;
  left: number;
}

/**
 * 兩層 treemap：先照 groupKey 分組決定各組的方塊，再於組內排葉節點。
 * 葉節點座標是絕對座標（已加上組的位移），可直接定位。
 */
export function groupedTreemap<T extends Sized>(
  items: readonly T[],
  rect: Rect,
  groupKey: (item: T) => string,
  groupPadding: GroupPadding,
): { groups: (Rect & { key: string; value: number })[]; leaves: (T & Rect)[] } {
  const buckets = new Map<string, T[]>();
  for (const item of items) {
    if (item.value <= 0) continue;
    const key = groupKey(item);
    const list = buckets.get(key);
    if (list) list.push(item);
    else buckets.set(key, [item]);
  }

  const groupItems = [...buckets.entries()].map(([key, list]) => ({
    key,
    value: list.reduce((a, b) => a + b.value, 0),
  }));
  const groups = squarify(groupItems, rect);

  const leaves: (T & Rect)[] = [];
  for (const g of groups) {
    // 內縮讓群組標題與邊界有呼吸空間；內縮後為負寬高的極小群組直接跳過
    // （小群組寧可只剩一塊底色，也不要讓葉節點蓋掉群組名）。
    const inner: Rect = {
      x: g.x + groupPadding.left,
      y: g.y + groupPadding.top,
      w: g.w - groupPadding.left - groupPadding.right,
      h: g.h - groupPadding.top - groupPadding.bottom,
    };
    if (inner.w <= 0 || inner.h <= 0) continue;
    leaves.push(...squarify(buckets.get(g.key) ?? [], inner));
  }
  return { groups, leaves };
}
