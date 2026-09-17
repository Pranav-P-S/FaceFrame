import { useEffect, useState } from 'react';
import { imageDataUrl } from '../images';

export function useImage(
  path: string | null | undefined,
  size = 384,
  square = true
): string | null | 'failed' {
  const [state, setState] = useState<string | null | 'failed'>(null);
  useEffect(() => {
    if (!path) return undefined;
    let cancelled = false;
    setState(null);
    imageDataUrl(path, size, square).then((url) => {
      if (!cancelled) setState(url ?? 'failed');
    });
    return () => {
      cancelled = true;
    };
  }, [path, size, square]);
  return state;
}
