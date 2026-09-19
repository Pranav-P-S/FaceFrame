import { useEffect, useState } from 'react';
import { backend } from '../../lib/api';
import { useStore } from '../../lib/store';
import { promptText } from '../../lib/prompt';
import type { AlbumDetail, AlbumSummary, Item } from '../../types';
import PhotoGrid from '../../components/PhotoGrid';
import { useImage } from '../../images';

/** Albums list or one album's contents depending on the route. */
export default function AlbumsPage() {
  const route = useStore((s) => s.route);
  if (route.page === 'albums' && route.albumId != null) {
    return <AlbumDetail albumId={route.albumId} />;
  }
  return <AlbumList />;
}

function AlbumList() {
  const [albums, setAlbums] = useState<AlbumSummary[]>([]);
  useEffect(() => {
    void backend.getAlbums().then((res) => setAlbums((res.albums as AlbumSummary[]) ?? []));
  }, []);
  return (
    <div className="page">
      <h1 className="page-title">Albums</h1>
      <div className="album-grid">
        {albums.map((a) => (
          <a key={a.id} className="album-card" href={`#/albums/${a.id}`}>
            <AlbumCover hash={a.cover} />
            <div className="album-meta">
              <strong>{a.name}</strong>
              <span>{a.count} items</span>
            </div>
          </a>
        ))}
      </div>
      {albums.length === 0 && (
        <div className="empty-state">
          <h2>No albums yet</h2>
          <p className="empty-hint">Select photos in the feed and choose “Add to album”.</p>
        </div>
      )}
    </div>
  );
}

function AlbumCover({ hash }: { hash: string | null }) {
  const url = useImage(hash, 384, true);
  return <div className="album-cover">{url && <img src={url} alt="" />}</div>;
}

function AlbumDetail({ albumId }: { albumId: number }) {
  const [album, setAlbum] = useState<AlbumDetail | null>(null);
  const openViewer = useStore((s) => s.openViewer);
  const showToast = useStore((s) => s.showToast);
  const refresh = useStore((s) => s.refresh);
  const selection = useStore((s) => s.selection);
  const clearSelection = useStore((s) => s.clearSelection);
  const [arranging, setArranging] = useState(false);
  const [order, setOrder] = useState<Item[]>([]);

  const setAlbumsState = (res: Record<string, unknown>) => setAlbum((res.album as AlbumDetail) ?? null);
  const reload = () => {
    void backend.getAlbum(albumId).then(setAlbumsState);
  };
  useEffect(reload, [albumId]);

  const hashesFromSelection = (): string[] =>
    [...selection]
      .map((path) => album?.items.find((i) => i.path === path)?.content_hash)
      .filter((h): h is string => Boolean(h));

  const removeSelected = async () => {
    if (!album) return;
    const hashes = hashesFromSelection();
    if (!hashes.length) return;
    await backend.albumRemove(albumId, hashes);
    showToast({ text: 'Removed from album (photos kept)', kind: 'info' });
    clearSelection();
    reload();
  };

  const coverFromSelection = async () => {
    if (!album) return;
    const hashes = hashesFromSelection();
    if (!hashes.length) return;
    await backend.setAlbumCover(albumId, hashes[0]);
    showToast({ text: 'Album cover updated', kind: 'info' });
    clearSelection();
    reload();
  };

  const renameAlbum = async () => {
    if (!album) return;
    const name = await promptText({ title: 'Rename album:', initial: album.name });
    if (!name || name === album.name) return;
    await backend.renameAlbum(albumId, name);
    reload();
  };

  const deleteAlbum = async () => {
    if (!album) return;
    if (!window.confirm(`Delete the album “${album.name}”? The photos themselves are kept.`)) return;
    await backend.deleteAlbum(albumId);
    showToast({ text: 'Album deleted (photos kept)', kind: 'info' });
    refresh();
    window.location.hash = '#/albums';
  };

  const startArrange = () => {
    if (!album) return;
    setOrder(album.items as Item[]);
    setArranging(true);
  };

  const move = (index: number, delta: number) => {
    setOrder((prev) => {
      const next = [...prev];
      const to = index + delta;
      if (to < 0 || to >= next.length) return prev;
      [next[index], next[to]] = [next[to], next[index]];
      return next;
    });
  };

  const saveOrder = async () => {
    await backend.albumReorder(albumId, order.map((i) => i.content_hash));
    showToast({ text: 'Album order saved', kind: 'info' });
    setArranging(false);
    reload();
  };

  if (!album) return <div className="page"><span className="spinner" /></div>;
  return (
    <div className="page">
      <div className="page-head">
        <a className="crumb" href="#/albums">← Albums</a>
        <h1 className="page-title">{album.name}</h1>
        {arranging ? (
          <div className="btn-row">
            <button className="btn btn-small" onClick={() => void saveOrder()}>Done</button>
            <button className="btn-chip" onClick={() => setArranging(false)}>Cancel</button>
          </div>
        ) : (
          <div className="btn-row">
            <button className="btn-chip" onClick={() => void renameAlbum()}>Rename</button>
            {(album.sort_key ?? 'added') === 'added' && album.items.length > 1 && (
              <button className="btn-chip" onClick={startArrange}>Arrange</button>
            )}
            <label className="setting-row album-sort">
              <span className="info-muted">Sort</span>
              <select
                value={album.sort_key}
                onChange={async (e) => {
                  await backend.setAlbumSort(albumId, e.target.value);
                  reload();
                }}
              >
                <option value="added">Manually / date added</option>
                <option value="captured">Date captured</option>
                <option value="filename">Filename</option>
              </select>
            </label>
            <button className="btn-chip btn-danger-ghost" onClick={() => void deleteAlbum()}>Delete album</button>
          </div>
        )}
        {selection.size > 0 && !arranging && (
          <div className="btn-row">
            <button className="btn-ghost" onClick={() => void coverFromSelection()}>
              Set cover ({selection.size})
            </button>
            <button className="btn-ghost btn-danger-ghost" onClick={() => void removeSelected()}>
              Remove {selection.size} from album
            </button>
          </div>
        )}
      </div>
      {arranging ? (
        <div className="arrange-list">
          {order.map((item, i) => (
            <div key={item.path} className="arrange-row">
              <span className="arrange-index">{i + 1}</span>
              <ArrangeThumb item={item} />
              <div className="btn-row">
                <button className="btn-chip" onClick={() => move(i, -1)} disabled={i === 0}>↑</button>
                <button className="btn-chip" onClick={() => move(i, 1)} disabled={i === order.length - 1}>↓</button>
              </div>
              <span className="arrange-name" title={item.path}>{item.path.replace(/\\/g, '/').split('/').pop()}</span>
            </div>
          ))}
        </div>
      ) : (
        <PhotoGrid
          groups={[{ key: 'all', items: album.items as Item[] }]}
          view="days"
          onOpen={openViewer}
          flatten
        />
      )}
    </div>
  );
}

function ArrangeThumb({ item }: { item: Item }) {
  const url = useImage(item.path, 96, true);
  return (
    <div className="arrange-thumb">
      {url ? <img src={url} alt="" /> : <div className="thumb-loading" />}
    </div>
  );
}
