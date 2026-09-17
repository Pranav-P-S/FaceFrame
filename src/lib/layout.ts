/** Justified-row layout (Google-Photos-style): pack items into rows that
 * fill the container width at a target row height, cropping the excess via
 * aspect ratios. Pure math — unit tested. */

export interface LayoutInput {
  aspect: number; // width / height
}

export interface PositionedItem {
  index: number;
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface Row {
  items: PositionedItem[];
  height: number;
  y: number;
}

export interface LayoutOptions {
  containerWidth: number;
  targetRowHeight: number;
  gap: number;
  aspectOf: (index: number) => number;
}

export function justifyRows(
  count: number,
  opts: LayoutOptions
): { rows: Row[]; totalHeight: number } {
  const { containerWidth, targetRowHeight, gap, aspectOf } = opts;
  const rows: Row[] = [];
  let current: number[] = [];
  let currentAspectSum = 0;
  let y = 0;

  const flush = (isLast: boolean) => {
    if (!current.length) return;
    let height = (containerWidth - gap * (current.length - 1)) / currentAspectSum;
    if (isLast) {
      // Google-Photos behavior: a leftover row stretches to full width but
      // never grows absurdly tall for one or two wide items.
      height = Math.min(height, targetRowHeight * 2.2);
    }
    const row: Row = { items: [], height, y };
    let x = 0;
    for (const index of current) {
      const width = height * aspectOf(index);
      row.items.push({ index, x, y, width, height });
      x += width + gap;
    }
    rows.push(row);
    y += height + gap;
    current = [];
    currentAspectSum = 0;
  };

  for (let index = 0; index < count; index++) {
    const aspect = Math.min(3, Math.max(0.45, aspectOf(index) || 1.5));
    current.push(index);
    currentAspectSum += aspect;
    const height = (containerWidth - gap * (current.length - 1)) / currentAspectSum;
    if (height < targetRowHeight) flush(false);
  }
  flush(true);

  const totalHeight = rows.length ? rows[rows.length - 1].y + rows[rows.length - 1].height : 0;
  return { rows, totalHeight };
}

/** Which rows are visible (with a pixel buffer) for a scroll position. */
export function visibleRows(
  rows: { y: number; height: number }[],
  scrollTop: number,
  viewportHeight: number,
  bufferPx = 800
): [number, number] {
  let first = -1;
  let last = -1;
  for (let i = 0; i < rows.length; i++) {
    const row = rows[i];
    if (row.y + row.height < scrollTop - bufferPx) continue;
    if (row.y > scrollTop + viewportHeight + bufferPx) break;
    if (first < 0) first = i;
    last = i;
  }
  if (first < 0) return [0, -1];
  return [first, last];
}
