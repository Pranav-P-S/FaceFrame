import { useEffect, useRef, useState } from 'react';
import { gridPreview } from '../images';
import { formatDuration } from '../lib/format';
import type { Item } from '../types';

/** A single grid tile. Loads its square preview lazily when scrolled close. */

interface ThumbProps {
  item: Item;
  height: number;
  selected: boolean;
  selectMode: boolean;
  onOpen: () => void;
  onSelect: (shift: boolean) => void;
}

export default function Thumb({ item, height, selected, selectMode, onOpen, onSelect }: ThumbProps) {
  const [url, setUrl] = useState<string | null>(null);
  const ref = useRef<HTMLButtonElement>(null);
  const [near, setNear] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setNear(true);
          io.disconnect();
        }
      },
      { rootMargin: '600px' }
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  useEffect(() => {
    if (!near) return undefined;
    let cancelled = false;
    gridPreview(item.path).then((u) => {
      if (!cancelled) setUrl(u);
    });
    return () => {
      cancelled = true;
    };
  }, [near, item.path]);

  const isVideo = item.kind === 'video' || item.media_kind === 'video';
  const flagged = item.flags as { motion?: unknown; screenshot?: unknown; panorama?: unknown } | undefined;

  return (
    <button
      ref={ref}
      className={`thumb ${selected ? 'thumb-selected' : ''}`}
      style={{ height }}
      onClick={(e) => (selectMode || e.shiftKey ? onSelect(e.shiftKey) : onOpen())}
      title={item.caption || item.path}
    >
      {url ? <img src={url} alt="" loading="lazy" /> : <div className="thumb-loading" />}
      {isVideo && (
        <>
          <span className="thumb-play" aria-hidden>▶</span>
          <span className="thumb-duration">{formatDuration(item.duration)}</span>
        </>
      )}
      {flagged?.panorama ? <span className="thumb-badge">pano</span> : null}
      {selected && <span className="thumb-check" aria-hidden>✓</span>}
    </button>
  );
}
