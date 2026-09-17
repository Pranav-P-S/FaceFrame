import { api } from './types';

/** Image loading through the backend's preview pipeline. Two flavors:
 * square cover thumbs for grids (cheap) and fit-in previews for the viewer
 * (larger, narrowly cached). LRU + in-flight dedupe per size. */

const GRID = 384;
const VIEWER = 2200;

const caches = new Map<number, Map<string, string>>();
const limits = new Map<number, number>([
  [GRID, 600],
  [VIEWER, 10],
]);
const pending = new Map<string, Promise<string | null>>();

function cacheFor(size: number): Map<string, string> {
  let c = caches.get(size);
  if (!c) {
    c = new Map();
    caches.set(size, c);
  }
  return c;
}

export function imageDataUrl(path: string, size: number = GRID, square = false): Promise<string | null> {
  const cache = cacheFor(size);
  const key = `${size}:${square ? 'sq' : 'fit'}:${path}`;
  const hit = cache.get(key);
  if (hit) {
    cache.delete(key);
    cache.set(key, hit);
    return Promise.resolve(hit);
  }
  const inFlight = pending.get(key);
  if (inFlight) return inFlight;

  const promise = api()
    .request('get_image_preview', {
      file_path: path,
      max_dim: size,
      square,
    })
    .then((res) => {
      pending.delete(key);
      const url = (res as { data_url?: string }).data_url ?? null;
      if (!url) return null;
      cache.set(key, url);
      const limit = limits.get(size) ?? 100;
      if (cache.size > limit) {
        const oldest = cache.keys().next().value;
        if (oldest !== undefined) cache.delete(oldest);
      }
      return url;
    })
    .catch(() => {
      pending.delete(key);
      return null;
    });
  pending.set(key, promise);
  return promise;
}

export function gridPreview(path: string): Promise<string | null> {
  return imageDataUrl(path, GRID, true);
}

export function viewerImage(path: string): Promise<string | null> {
  return imageDataUrl(path, VIEWER, false);
}

export function basename(path: string): string {
  const normalized = path.replace(/\\/g, '/');
  return normalized.slice(normalized.lastIndexOf('/') + 1);
}

export function folderName(path: string): string {
  const trimmed = path.replace(/[\\/]+$/, '');
  return basename(trimmed) || trimmed;
}

// Hook variant for single-image spots (person avatars, the viewer).
export { useImage } from './lib/useImage';
