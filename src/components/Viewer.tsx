import { useCallback, useEffect, useState } from 'react';
import { api, type Item, type ItemDetail } from '../types';
import { backend } from '../lib/api';
import { viewerImage } from '../images';
import { useStore } from '../lib/store';
import { exposureText, formatBytes, formatDateTime, parseExif } from '../lib/format';
import Editor from './Editor';

/** Fullscreen viewer: zoom, navigation, actions, info panel, editor. */
export default function Viewer({ items, index }: { items: Item[]; index: number }) {
  const setViewerIndex = useStore((s) => s.setViewerIndex);
  const closeViewer = useStore((s) => s.closeViewer);
  const infoOpen = useStore((s) => s.infoOpen);
  const setInfoOpen = useStore((s) => s.setInfoOpen);
  const showToast = useStore((s) => s.showToast);
  const [zoom, setZoom] = useState(1);
  const [detail, setDetail] = useState<ItemDetail | null>(null);
  const [editing, setEditing] = useState(false);

  const item = items[index];

  const refreshDetail = useCallback(async () => {
    if (!item) return;
    try {
      const res = await backend.getItem(item.path);
      setDetail((res.item as ItemDetail) ?? null);
    } catch {
      setDetail(null);
    }
  }, [item]);

  useEffect(() => {
    void refreshDetail();
    setZoom(1);
  }, [refreshDetail, index]);

  const move = useCallback(
    (delta: number) => {
      setZoom(1);
      setViewerIndex(index + delta);
    },
    [index, setViewerIndex]
  );

  const toggleFavorite = useCallback(async () => {
    if (!detail?.media) return;
    const next = !detail.media.favorite;
    await backend.setFavorite([detail.content_hash], next);
    setDetail((d) => (d?.media ? { ...d, media: { ...d.media, favorite: next ? 1 : 0 } } : d));
  }, [detail]);

  const trash = useCallback(async () => {
    if (!detail) return;
    const hash = detail.content_hash;
    await backend.setTrashed([hash], true);
    showToast({
      text: 'Moved to trash',
      kind: 'info',
      actionLabel: 'Undo',
      action: () => backend.setTrashed([hash], false),
    });
    if (items.length <= 1) closeViewer();
    else move(index === items.length - 1 ? -1 : 1);
  }, [detail, items.length, index, move, closeViewer, showToast]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (editing) return;
      if (e.key === 'ArrowRight') move(1);
      else if (e.key === 'ArrowLeft') move(-1);
      else if (e.key === 'Escape') closeViewer();
      else if (e.key === 'i') setInfoOpen(!infoOpen);
      else if (e.key === 'f') void toggleFavorite();
      else if (e.key === 'Delete') void trash();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [editing, infoOpen, move, closeViewer, setInfoOpen, toggleFavorite, trash]);

  const isVideo = item.kind === 'video' || item.media_kind === 'video';

  if (editing && detail) {
    return (
      <Editor
        filePath={detail.path}
        hash={detail.content_hash}
        initial={safeJson<import('./Editor').EditState>(detail.media?.edit ?? null)}
        width={detail.media?.width}
        height={detail.media?.height}
        onClose={() => {
          setEditing(false);
          void refreshDetail();
        }}
      />
    );
  }

  const addToAlbum = () => {
    const name = window.prompt('Add to album — name:', '');
    if (!name) return;
    void backend
      .getAlbums()
      .then(async (albums) => {
        const list = (albums.albums as { id: number; name: string }[]) || [];
        const existing = list.find((a) => a.name === name);
        const albumId = existing
          ? existing.id
          : Number(((await backend.createAlbum(name)) as { album_id: number }).album_id);
        await backend.albumAdd(albumId, [detail?.content_hash ?? '']);
        showToast({ text: `Added to “${name}”`, kind: 'info' });
      })
      .catch(() => showToast({ text: 'Could not add to album', kind: 'error' }));
  };

  const exportItem = () => {
    const dest = window.prompt('Export to folder:', 'C:/Export');
    if (dest) {
      void backend
        .exportItems([item.path], dest)
        .then(() => showToast({ text: `Exported to ${dest}`, kind: 'info' }))
        .catch(() => showToast({ text: 'Export failed', kind: 'error' }));
    }
  };

  return (
    <div className="viewer" role="dialog" aria-modal="true">
      <div className="viewer-topbar">
        <button className="icon-btn" onClick={closeViewer} aria-label="Close (Esc)">✕</button>
        <span className="viewer-filename">{fileName(item.path)}</span>
        {detail && detail.duplicate_paths.length > 0 && (
          <span className="viewer-dupnote" title={detail.duplicate_paths.join('\n')}>
            {detail.duplicate_paths.length} exact duplicate(s)
          </span>
        )}
        <div className="topbar-spacer" />
        <button className="icon-btn" onClick={() => setZoom((z) => Math.max(0.2, z / 1.3))} aria-label="Zoom out">−</button>
        <button className="icon-btn" onClick={() => setZoom((z) => Math.min(6, z * 1.3))} aria-label="Zoom in">+</button>
        <button className="icon-btn" onClick={exportItem} aria-label="Download">⭳</button>
      </div>

      <div className="viewer-stage">
        {index > 0 && (
          <button className="viewer-nav viewer-nav-prev" onClick={() => move(-1)} aria-label="Previous">‹</button>
        )}
        <ViewerImage item={item} isVideo={isVideo} zoom={zoom} setZoom={setZoom} />
        {index < items.length - 1 && (
          <button className="viewer-nav viewer-nav-next" onClick={() => move(1)} aria-label="Next">›</button>
        )}
      </div>

      <div className="viewer-actions">
        <button className={`viewer-action ${detail?.media?.favorite ? 'viewer-action-active' : ''}`} onClick={() => void toggleFavorite()} title="Favorite (f)">
          <span className="va-icon">{detail?.media?.favorite ? '★' : '☆'}</span>
          <span>Favorite</span>
        </button>
        <button className="viewer-action" onClick={() => setEditing(true)} title="Edit">
          <span className="va-icon">✎</span>
          <span>Edit</span>
        </button>
        <button className={`viewer-action ${infoOpen ? 'viewer-action-active' : ''}`} onClick={() => setInfoOpen(!infoOpen)} title="Info (i)">
          <span className="va-icon">ⓘ</span>
          <span>Info</span>
        </button>
        <button className="viewer-action" onClick={addToAlbum} title="Add to album">
          <span className="va-icon">⊞</span>
          <span>Add</span>
        </button>
        <button className="viewer-action viewer-action-danger" onClick={() => void trash()} title="Move to trash (Del)">
          <span className="va-icon">🗑</span>
          <span>Trash</span>
        </button>
      </div>

      {infoOpen && detail && <InfoPanel detail={detail} onRefresh={() => void refreshDetail()} />}
    </div>
  );
}

function ViewerImage({
  item,
  isVideo,
  zoom,
  setZoom,
}: {
  item: Item;
  isVideo: boolean;
  zoom: number;
  setZoom: (fn: (z: number) => number) => void;
}) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    setUrl(null);
    viewerImage(item.path).then((u) => {
      if (!cancelled) setUrl(u);
    });
    return () => {
      cancelled = true;
    };
  }, [item.path]);

  if (isVideo) {
    return (
      <video
        key={item.path}
        src={api().mediaUrl(item.path)}
        controls
        autoPlay
        className="viewer-media"
        style={{ transform: `scale(${zoom})` }}
      />
    );
  }
  return (
    <img
      key={item.path}
      src={url ?? ''}
      alt=""
      className={`viewer-media ${url ? '' : 'viewer-media-loading'}`}
      style={{ transform: `scale(${zoom})` }}
      onDoubleClick={() => setZoom((z) => (z > 1 ? 1 : 2.5))}
      onWheel={(e) => {
        if (e.deltaY < 0) setZoom((z) => Math.min(6, z * 1.15));
        else setZoom((z) => Math.max(0.2, z / 1.15));
      }}
    />
  );
}

function InfoPanel({ detail, onRefresh }: { detail: ItemDetail; onRefresh: () => void }) {
  const media = detail.media;
  const exif = parseExif(media?.exif ?? null);
  const showToast = useStore((s) => s.showToast);
  const [caption, setCaption] = useState(media?.caption ?? '');
  const [dateText, setDateText] = useState(
    detail.ts ? new Date(detail.ts * 1000).toISOString().slice(0, 16) : ''
  );

  return (
    <aside className="info-panel">
      <div className="info-row"><strong>{fileName(detail.path)}</strong></div>
      {media && (
        <>
          <div className="info-row">{formatDateTime(detail.ts)}</div>
          <label className="info-edit">
            <span>Date & time</span>
            <input
              type="datetime-local"
              value={dateText}
              onChange={(e) => setDateText(e.target.value)}
              onBlur={async () => {
                const epoch = new Date(dateText).getTime() / 1000;
                if (Number.isFinite(epoch) && Math.abs(epoch - detail.ts) > 1) {
                  await backend.setDateOverride(detail.content_hash, epoch);
                  showToast({
                    text: 'Date updated',
                    kind: 'info',
                    actionLabel: 'Undo',
                    action: () => backend.setDateOverride(detail.content_hash, null),
                  });
                  onRefresh();
                }
              }}
            />
          </label>
          <label className="info-edit">
            <span>Description</span>
            <textarea
              rows={2}
              value={caption}
              onChange={(e) => setCaption(e.target.value)}
              onBlur={async () => {
                if (caption !== (media.caption ?? '')) {
                  await backend.setCaption(detail.content_hash, caption);
                  onRefresh();
                }
              }}
            />
          </label>
          {exif.camera && <div className="info-row">{exif.camera}</div>}
          {(exif.iso || exif.f_number || exif.exposure || exif.focal_length) && (
            <div className="info-row info-muted">
              {exif.iso ? `ISO ${exif.iso}` : ''} {exif.f_number ? `ƒ/${exif.f_number}` : ''}{' '}
              {exposureText(exif.exposure)} {exif.focal_length ? `${exif.focal_length}mm` : ''}
            </div>
          )}
          <div className="info-row info-muted">
            {media.width}×{media.height} · {formatBytes(detail.size)}
          </div>
          {exif.gps && (
            <div className="info-row info-muted">{exif.gps[0].toFixed(4)}, {exif.gps[1].toFixed(4)}</div>
          )}
          {detail.faces.length > 0 && (
            <div className="info-faces">
              {detail.faces.map((f) => (
                <span key={f.id} className="face-chip">{f.person_name ?? 'Unnamed'}</span>
              ))}
            </div>
          )}
          {detail.albums.length > 0 && (
            <div className="info-row info-muted">In albums: {detail.albums.map((a) => a.name).join(', ')}</div>
          )}
        </>
      )}
    </aside>
  );
}

function fileName(path: string): string {
  const n = path.replace(/\\/g, '/');
  return n.slice(n.lastIndexOf('/') + 1);
}

function safeJson<T>(raw: string | null): T | null {
  if (!raw) return null;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}
