import { useEffect, useState } from 'react';
import { api } from './types';

// Local images surface to the renderer as data URLs, read and downscaled
// by the backend into a per-library preview cache. Two sizes: grid
// previews (small, cached liberally) and viewer images (large, cached
// narrowly so memory stays bounded).
const GRID = 640;
const VIEWER = 2400;

const caches = new Map<number, Map<string, string>>();
const limits = new Map<number, number>([
  [GRID, 300],
  [VIEWER, 8],
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

export function imageDataUrl(path: string, size: number = GRID): Promise<string | null> {
  const cache = cacheFor(size);
  const key = `${size}:${path}`;
  const hit = cache.get(key);
  if (hit) {
    // Refresh insertion order so hot entries survive eviction.
    cache.delete(key);
    cache.set(key, hit);
    return Promise.resolve(hit);
  }
  const inFlight = pending.get(key);
  if (inFlight) return inFlight;

  const promise = api()
    .readImageDataUrl(path, size)
    .then((url) => {
      pending.delete(key);
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
  return imageDataUrl(path, GRID);
}

export function viewerImage(path: string): Promise<string | null> {
  return imageDataUrl(path, VIEWER);
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
export function useImage(path: string | null | undefined, size = GRID): string | null | 'failed' {
  const [state, setState] = useState<string | null | 'failed'>(null);
  useEffect(() => {
    if (!path) return;
    let cancelled = false;
    setState(null);
    imageDataUrl(path, size).then((url) => {
      if (!cancelled) setState(url ?? 'failed');
    });
    return () => {
      cancelled = true;
    };
  }, [path, size]);
  return state;
}
