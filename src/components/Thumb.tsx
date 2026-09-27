import { memo, useEffect, useState, useSyncExternalStore } from 'react';
import {
  getImageCacheVersion,
  gridPreview,
  subscribeImageCacheVersion,
} from '../images';
import { formatDuration } from '../lib/format';
import { useStore } from '../lib/store';
import type { Item } from '../types';

/**
 * A single grid tile. Memoized: the virtualized grid only mounts rows near
 * the viewport (visibleRows keeps an 800 px buffer), so the preview starts
 * loading on mount — no per-tile IntersectionObserver on top of the
 * virtualization. The selection state is subscribed per path, so clicking a
 * tile re-renders just that tile, never the grid.
 */

interface ThumbProps {
  item: Item;
  height: number;
  selectMode: boolean;
  flatIndex: number;
  onOpenAt: (flatIndex: number) => void;
  onSelectAt: (flatIndex: number, path: string, hash: string | undefined, shift: boolean) => void;
}

function ThumbInner({ item, height, selectMode, flatIndex, onOpenAt, onSelectAt }: ThumbProps) {
  const selected = useStore((s) => s.selection.has(item.path));
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  const path = item.path;
  // Cache bumps (editor save) must refresh already-mounted tiles too.
  const cacheVersion = useSyncExternalStore(
    subscribeImageCacheVersion,
    getImageCacheVersion
  );
  useEffect(() => {
    let cancelled = false;
    setFailed(false);
    gridPreview(path)
      .then((u) => {
        if (!cancelled) {
          if (u) setUrl(u);
          else setFailed(true);
        }
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [path, cacheVersion]);

  const isVideo = item.kind === 'video' || item.media_kind === 'video';
  const flagged = item.flags as { motion?: unknown; screenshot?: unknown; panorama?: unknown } | undefined;

  return (
    <button
      className={`thumb ${selected ? 'thumb-selected' : ''}`}
      style={{ height }}
      onClick={(e) => {
        if (selectMode || e.shiftKey) onSelectAt(flatIndex, item.path, item.content_hash, e.shiftKey);
        else onOpenAt(flatIndex);
      }}
      title={item.caption || item.path}
    >
      {url ? (
        <img src={url} alt="" loading="lazy" />
      ) : failed ? (
        <span className="thumb-broken" aria-label="Image could not be displayed">⚠</span>
      ) : (
        <div className="thumb-loading" />
      )}
      {isVideo && (
        <>
          <span className="thumb-play" aria-hidden>▶</span>
          <span className="thumb-duration">{formatDuration(item.duration)}</span>
        </>
      )}
      {/* Motion pairs arrive as a top-level flag from the view SQL, not
          inside media.flags. */}
      {(item as { motion?: unknown }).motion ? <span className="thumb-play" aria-hidden>▶</span> : null}
      {flagged?.panorama ? <span className="thumb-badge">pano</span> : null}
      {selected && <span className="thumb-check" aria-hidden>✓</span>}
    </button>
  );
}

const Thumb = memo(ThumbInner);
export default Thumb;
