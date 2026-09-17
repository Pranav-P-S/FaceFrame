import { describe, expect, it } from 'vitest';

import { groupLabel, formatBytes, formatDuration, dayKey } from './format';
import { justifyRows, visibleRows } from './layout';
import { parseHash, hrefFor } from './router';
import { parse as parseQuery } from './query';

describe('justified layout', () => {
  const opts = {
    containerWidth: 1000,
    targetRowHeight: 200,
    gap: 4,
    aspectOf: (i: number) => [1.5, 1.5, 1.5, 0.75, 2.0, 1.0][i % 6],
  };

  it('fills rows to the container width without overflowing', () => {
    const { rows } = justifyRows(18, opts);
    rows.forEach((row, rowIndex) => {
      const width = row.items.reduce((sum, it) => sum + it.width, 0) + opts.gap * (row.items.length - 1);
      // The final row may legitimately be underfilled at target height.
      if (rowIndex < rows.length - 1) {
        expect(width).toBeGreaterThanOrEqual(opts.containerWidth - 1);
      }
      for (const it of row.items) {
        expect(it.height).toBeCloseTo(row.height, 5);
      }
    });
    expect(rows.length).toBeGreaterThanOrEqual(3);
  });

  it('tiles cover the full total height with correct y stacking', () => {
    const { rows, totalHeight } = justifyRows(30, opts);
    let y = 0;
    for (const row of rows) {
      expect(row.y).toBeCloseTo(y, 5);
      y += row.height + opts.gap;
    }
    expect(totalHeight).toBeCloseTo(y - opts.gap, 5);
  });

  it('empty input yields no rows', () => {
    const { rows, totalHeight } = justifyRows(0, opts);
    expect(rows).toHaveLength(0);
    expect(totalHeight).toBe(0);
  });

  it('visibleRows windows around the scroll position', () => {
    const rows = Array.from({ length: 100 }, (_, i) => ({ y: i * 210, height: 200 }));
    const [first, last] = visibleRows(rows, 10_000, 800);
    expect(first).toBeLessThan(50);
    expect(last).toBeGreaterThan(45);
    expect(last - first).toBeLessThan(20);
  });
});

describe('date grouping', () => {
  it('labels today and yesterday', () => {
    const now = new Date();
    const todayTs = Math.floor(now.getTime() / 1000);
    expect(groupLabel(dayKey(todayTs), 'days')).toBe('Today');
    const yesterdayTs = todayTs - 86400;
    expect(groupLabel(dayKey(yesterdayTs), 'days')).toBe('Yesterday');
  });

  it('formats months and years', () => {
    expect(groupLabel('2024-03', 'months')).toBe('March 2024');
    expect(groupLabel('2024', 'years')).toBe('2024');
  });
});

describe('formatting', () => {
  it('humanizes byte sizes', () => {
    expect(formatBytes(0)).toBe('0 B');
    expect(formatBytes(2048)).toBe('2.0 KB');
    expect(formatBytes(3_500_000)).toBe('3.3 MB');
  });

  it('formats durations', () => {
    expect(formatDuration(null)).toBe('');
    expect(formatDuration(5)).toBe('0:05');
    expect(formatDuration(125)).toBe('2:05');
  });
});

describe('router', () => {
  it('parses and emits routes', () => {
    expect(parseHash('#/photos')).toEqual({ page: 'photos' });
    expect(parseHash('#/search?q=dog%20beach')).toEqual({ page: 'search', query: 'dog beach' });
    expect(parseHash('#/albums/3')).toEqual({ page: 'albums', albumId: 3 });
    expect(parseHash('#/people')).toEqual({ page: 'people', personId: undefined });
    expect(hrefFor({ page: 'search', query: 'a b' })).toBe('#/search?q=a%20b');
  });
});

describe('query parser mirror', () => {
  it('parses plain text and filters', () => {
    const q = parseQuery('dog type:video is:favorite');
    expect(q.text).toEqual(['dog']);
    expect(q.filters.type).toEqual(['video']);
    expect(q.filters.is).toEqual(['favorite']);
  });

  it('expands year into after/before', () => {
    const q = parseQuery('year:2023');
    expect(q.filters.after).toEqual(['2023-01-01']);
    expect(q.filters.before).toEqual(['2024-01-01']);
  });
});
