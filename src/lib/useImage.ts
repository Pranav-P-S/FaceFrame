import { useEffect, useState, useSyncExternalStore } from 'react';
import {
  getImageCacheVersion,
  imageDataUrl,
  subscribeImageCacheVersion,
} from '../images';

export function useImage(
  path: string | null | undefined,
  size = 384,
  square = true
): string | null | 'failed' {
  const [state, setState] = useState<string | null | 'failed'>(null);
  // A cache bump (editor save) must refresh already-mounted consumers, not
  // just future mounts.
  const cacheVersion = useSyncExternalStore(
    subscribeImageCacheVersion,
    getImageCacheVersion
  );
  useEffect(() => {
    if (!path) {
      setState(null);
      return undefined;
    }
    let cancelled = false;
    if (path.startsWith('data:')) {
      setState(path);
      return undefined;
    }
    setState(null);
    imageDataUrl(path, size, square).then((url) => {
      if (!cancelled) setState(url ?? 'failed');
    });
    return () => {
      cancelled = true;
    };
  }, [path, size, square, cacheVersion]);
  return state;
}
