import { useEffect, useState } from 'react';
import { backend } from '../../lib/api';
import { useStore } from '../../lib/store';
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
  const selection = useStore((s) => s.selection);
  const clearSelection = useStore((s) => s.clearSelection);

  useEffect(() => {
    void backend.getAlbum(albumId).then((res) => setAlbumsState(res));
  }, [albumId]);

  const setAlbumsState = (res: Record<string, unknown>) => setAlbum((res.album as AlbumDetail) ?? null);

  const removeSelected = async () => {
    if (!album) return;
    const hashes = [...selection]
      .map((path) => album.items.find((i) => i.path === path)?.content_hash)
      .filter((h): h is string => Boolean(h));
    if (!hashes.length) return;
    await backend.albumRemove(albumId, hashes);
    showToast({ text: 'Removed from album (photos kept)', kind: 'info' });
    clearSelection();
    void backend.getAlbum(albumId).then(setAlbumsState);
  };

  if (!album) return <div className="page"><span className="spinner" /></div>;
  return (
    <div className="page">
      <div className="page-head">
        <a className="crumb" href="#/albums">← Albums</a>
        <h1 className="page-title">{album.name}</h1>
        <label className="setting-row album-sort">
          <span className="info-muted">Sort</span>
          <select
            value={album.sort_key}
            onChange={async (e) => {
              await backend.setAlbumSort(albumId, e.target.value);
              const res = await backend.getAlbum(albumId);
              setAlbum(res.album as AlbumDetail);
            }}
          >
            <option value="added">Manually / date added</option>
            <option value="captured">Date captured</option>
            <option value="filename">Filename</option>
          </select>
        </label>
        {selection.size > 0 && (
          <button className="btn-ghost btn-danger-ghost" onClick={() => void removeSelected()}>
            Remove {selection.size} from album
          </button>
        )}
      </div>
      <PhotoGrid
        groups={[{ key: 'all', items: album.items as Item[] }]}
        view="days"
        onOpen={openViewer}
        flatten
      />
    </div>
  );
}
