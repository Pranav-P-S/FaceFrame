import { useEffect, useRef, useState } from 'react';
import { gridPreview } from '../images';

interface LazyImageProps {
  path: string;
  alt?: string;
  className?: string;
}

// Loads a grid-sized preview through IPC only once the element scrolls
// into view, so large photo grids do not read every file up front.
export default function LazyImage({ path, alt = '', className }: LazyImageProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [src, setSrc] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setSrc(null);
    setFailed(false);
    const element = ref.current;
    if (!element) return;

    let cancelled = false;
    const observer = new IntersectionObserver(
      (entries) => {
        if (!entries.some((entry) => entry.isIntersecting)) return;
        observer.disconnect();
        gridPreview(path).then((url) => {
          if (cancelled) return;
          if (url) setSrc(url);
          else setFailed(true);
        });
      },
      { rootMargin: '300px' }
    );
    observer.observe(element);
    return () => {
      cancelled = true;
      observer.disconnect();
    };
  }, [path]);

  return (
    <div ref={ref} className={`lazy-image ${className ?? ''}`}>
      {src ? (
        <img src={src} alt={alt} loading="lazy" draggable={false} />
      ) : failed ? (
        <span className="lazy-image-fallback">missing</span>
      ) : (
        <span className="spinner" aria-label="loading" />
      )}
    </div>
  );
}
