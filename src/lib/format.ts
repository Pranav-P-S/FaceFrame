/** Date, size and duration formatting. Day grouping follows the item's
 * naive wall-clock time (the backend stores capture time that way), so all
 * key derivation uses UTC getters — matching the backend's time.gmtime
 * grouping exactly, independent of the viewer's local timezone. */

const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

export function dayKey(ts: number): string {
  const d = new Date(ts * 1000);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
}

export function monthKey(ts: number): string {
  return dayKey(ts).slice(0, 7);
}

export function yearKey(ts: number): string {
  return dayKey(ts).slice(0, 4);
}

/** Human label for a group key ("Today", "Yesterday", "March 10", …). */
export function groupLabel(key: string, view: 'days' | 'months' | 'years'): string {
  const now = new Date();
  const iso = (d: Date) => {
    const pad = (n: number) => String(n).padStart(2, '0');
    return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
  };
  if (view === 'years') return key;
  if (view === 'months') {
    const [y, m] = key.split('-').map(Number);
    return `${MONTHS[m - 1]} ${y}`;
  }
  if (key === iso(now)) return 'Today';
  const yesterday = new Date(now.getTime() - 86400_000);
  if (key === iso(yesterday)) return 'Yesterday';
  const [y, m, d] = key.split('-').map(Number);
  const sameYear = y === now.getUTCFullYear();
  return sameYear ? `${MONTHS[m - 1]} ${d}` : `${MONTHS[m - 1]} ${d}, ${y}`;
}

export function formatDateTime(ts: number): string {
  const d = new Date(ts * 1000);
  return d.toLocaleString(undefined, {
    year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
  });
}

export function formatBytes(bytes: number): string {
  if (!bytes) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  return `${(bytes / 1024 ** i).toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

export function formatDuration(seconds: number | null | undefined): string {
  if (!seconds || seconds <= 0) return '';
  const total = Math.round(seconds);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return m > 0 ? `${m}:${String(s).padStart(2, '0')}` : `0:${String(s).padStart(2, '0')}`;
}

/** Parse EXIF-ish camera settings from the stored exif JSON string. */
export function parseExif(raw: string | null): {
  camera?: string; lens?: string; iso?: number; f_number?: number;
  exposure?: number; focal_length?: number; gps?: [number, number];
} {
  if (!raw) return {};
  try {
    return JSON.parse(raw);
  } catch {
    return {};
  }
}

export function exposureText(exposure?: number): string {
  if (!exposure) return '';
  return exposure >= 1 ? `${exposure}s` : `1/${Math.round(1 / exposure)}s`;
}
