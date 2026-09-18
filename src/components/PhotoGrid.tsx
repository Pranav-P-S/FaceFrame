import { useEffect, useMemo, useRef, useState } from 'react';
import { rememberHashes } from '../lib/store';
import type { Item } from '../types';
import { justifyRows, visibleRows } from '../lib/layout';
import { groupLabel, monthKey, yearKey } from '../lib/format';
import { useStore } from '../lib/store';
import Thumb from './Thumb';

export type GridView = 'days' | 'months' | 'years';

interface PhotoGridProps {
  groups: { key: string; items: Item[] }[];
  view: GridView;
  onOpen: (items: Item[], index: number) => void;
  /** flatten=true renders one continuous grid without headers (albums, search) */
  flatten?: boolean;
}

const DENSITY_TARGET = [340, 260, 190, 130];

/** The feed: justified, virtualized, date-grouped. Also the engine behind
 * albums/search results via flatten. */
export default function PhotoGrid({ groups, view, onOpen, flatten = false }: PhotoGridProps) {
  const density = useStore((s) => s.density);
  const selection = useStore((s) => s.selection);
  const toggleSelect = useStore((s) => s.toggleSelect);
  const selectRange = useStore((s) => s.selectRange);
  const route = useStore((s) => s.route);
  const selectMode = route.page !== 'photos' || selection.size > 0;

  const containerRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(1200);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportH, setViewportH] = useState(900);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return undefined;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    setWidth(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  const gap = 4;
  const target = DENSITY_TARGET[density] ?? 260;

  // Build the flat row layout across all groups; headers become thin bands.
  const layout = useMemo(() => {
    interface Band {
      kind: 'header' | 'items';
      key?: string;
      items?: Item[];
      startIndex: number;
    }
    const bands: Band[] = [];
    if (flatten) {
      const items = groups.flatMap((g) => g.items);
      if (items.length) bands.push({ kind: 'items', items, startIndex: 0 });
    } else {
      let seen = view === 'days' ? '' : '';
      for (const group of groups) {
        const groupKey = view === 'days' ? group.key : view === 'months' ? monthKey(group.items[0]?.ts ?? 0) : yearKey(group.items[0]?.ts ?? 0);
        if (groupKey !== seen) {
          seen = groupKey;
          bands.push({ kind: 'header', key: groupKey, startIndex: 0 });
        }
        bands.push({ kind: 'items', items: group.items, startIndex: 0 });
      }
    }
    return bands;
  }, [groups, view, flatten]);

  const rowsLayout = useMemo(() => {
    const HEADER_H = 56;
    const rows: { y: number; height: number; headerKey?: string; items?: Item[]; positions?: { index: number; x: number; width: number }[] }[] = [];
    let y = 0;
    for (const band of layout) {
      if (band.kind === 'header') {
        rows.push({ y, height: HEADER_H, headerKey: band.key });
        y += HEADER_H;
        continue;
      }
      const items = band.items ?? [];
      const { rows: itemRows, totalHeight } = justifyRows(items.length, {
        containerWidth: width,
        targetRowHeight: target,
        gap,
        aspectOf: (i) => (items[i].width && items[i].height ? items[i].width / items[i].height : 1.5),
      });
      for (const row of itemRows) {
        rows.push({
          y: y + row.y,
          height: row.height,
          items: row.items.map((p) => items[p.index]),
          positions: row.items.map((p) => ({ index: p.index, x: p.x, width: p.width })),
        });
      }
      y += totalHeight;
    }
    return { rows, total: y };
  }, [layout, width, target]);

  const onScroll = () => {
    const el = containerRef.current;
    if (el) setScrollTop(el.scrollTop);
  };

  useEffect(() => {
    const el = containerRef.current;
    if (el) setViewportH(el.clientHeight);
  }, [width]);

  const [first, last] = visibleRows(rowsLayout.rows, scrollTop, viewportH);

  const flatPaths = useMemo(
    () => groups.flatMap((g) => g.items.map((i) => i.path)),
    [groups]
  );
  const flatItems = useMemo(
    () => groups.flatMap((g) => g.items),
    [groups]
  );
  const flatIndexByPath = useMemo(() => {
    const m = new Map<string, number>();
    flatItems.forEach((item, i) => m.set(item.path, i));
    return m;
  }, [flatItems]);
  useEffect(() => {
    rememberHashes(flatItems.map((i) => [i.path, i.content_hash] as [string, string]));
  }, [flatItems]);

  return (
    <div className="grid-scroll" ref={containerRef} onScroll={onScroll}>
      <div style={{ height: rowsLayout.total, position: 'relative' }}>
        {rowsLayout.rows.slice(Math.max(0, first), last + 1).map((row, idx) => {
          const i = Math.max(0, first) + idx;
          if (row.headerKey) {
            return (
              <div key={`h-${i}-${row.headerKey}`} className="grid-header" style={{ top: row.y, height: row.height }}>
                <span>{groupLabel(row.headerKey, view)}</span>
              </div>
            );
          }
          return (
            <div key={`r-${i}`} className="grid-row" style={{ top: row.y, height: row.height }}>
              {row.items?.map((item, j) => {
                const pos = row.positions?.[j];
                const flatIndex = flatIndexByPath.get(item.path) ?? -1;
                return (
                  <div key={item.path} style={{ position: 'absolute', left: pos?.x, width: pos?.width, height: row.height }}>
                    <Thumb
                      item={item}
                      height={row.height}
                      selected={selection.has(item.path)}
                      selectMode={selectMode}
                      onOpen={() => onOpen(flatItems, flatIndex)}
                      onSelect={(shift) =>
                        shift && flatIndex >= 0
                          ? selectRange(flatPaths, item.path)
                          : toggleSelect(item.path, item.content_hash)
                      }
                    />
                  </div>
                );
              })}
            </div>
          );
        })}
      </div>
    </div>
  );
}
