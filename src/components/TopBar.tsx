import { useEffect, useRef, useState } from 'react';
import { useStore } from '../lib/store';
import { backend } from '../lib/api';
import { api } from '../types';
import { promptText } from '../lib/prompt';
import { folderName } from '../images';

/** Top bar: search box, density slider, theme + app menu; flips into a
 * selection action bar while items are selected. */

function ScanBar() {
  const scan = useStore((s) => s.scan);
  if (!scan) return null;
  const pct = scan.total ? (scan.current / scan.total) * 100 : 0;
  return (
    <div className="scanbar" role="status">
      {scan.modelLoading && scan.total === 0 ? (
        <div className="scanbar-info">
          <span className="spinner spinner-inline" />
          Preparing the photo models — the first run downloads them and may take a few minutes.
        </div>
      ) : (
        <div className="scanbar-progress">
          <div className="scanbar-text">
            <span className="scanbar-file" title={scan.file}>{scan.file || 'Scanning…'}</span>
            <span className="scanbar-count">{scan.current} / {scan.total}</span>
          </div>
          <div className="scanbar-track"><div className="scanbar-fill" style={{ width: `${pct}%` }} /></div>
        </div>
      )}
      <button className="btn btn-small" onClick={() => api().cancelScan().catch(() => undefined)}>
        Cancel
      </button>
    </div>
  );
}

export default function TopBar() {
  const route = useStore((s) => s.route);
  const theme = useStore((s) => s.theme);
  const setTheme = useStore((s) => s.setTheme);
  const density = useStore((s) => s.density);
  const setDensity = useStore((s) => s.setDensity);
  const selection = useStore((s) => s.selection);
  const clearSelection = useStore((s) => s.clearSelection);
  const navigate = useStore((s) => s.navigate);
  const libraryPath = useStore((s) => s.libraryPath);

  const [query, setQuery] = useState(route.page === 'search' ? route.query : '');
  const [menuOpen, setMenuOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const lastRoute = useRef(route);

  useEffect(() => {
    if (lastRoute.current !== route) {
      lastRoute.current = route;
      setQuery(route.page === 'search' ? route.query : '');
    }
  }, [route]);

  const submitSearch = (q: string) => {
    navigate({ page: 'search', query: q });
  };

  if (selection.size > 0) {
    return (
      <>
        <SelectionBar count={selection.size} onDone={clearSelection} />
        <ScanBar />
      </>
    );
  }

  return (
    <header className="topbar">
      <button className="brand" onClick={() => navigate({ page: 'photos' })}>
        <img src="/icon.svg" alt="" className="brand-icon" />
        <span className="brand-name">Photos</span>
        {libraryPath && <span className="brand-library" title={libraryPath}>{folderName(libraryPath)}</span>}
      </button>

      <form
        className="searchbox"
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          submitSearch(query);
        }}
      >
        <span className="searchbox-icon" aria-hidden>⌕</span>
        <input
          ref={inputRef}
          id="global-search"
          data-search-input
          type="search"
          placeholder="Search people, places, things, dates…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') submitSearch(query);
          }}
        />
      </form>

      <div className="topbar-spacer" />

      {route.page === 'photos' && (
        <label className="density" title="Grid density">
          <input
            type="range"
            min={0}
            max={3}
            step={1}
            value={density}
            onChange={(e) => setDensity(Number(e.target.value))}
          />
        </label>
      )}

      <button
        className="icon-btn"
        title={theme === 'light' ? 'Switch to dark theme' : 'Switch to light theme'}
        onClick={() => setTheme(theme === 'light' ? 'dark' : 'light')}
      >
        {theme === 'light' ? '◐' : '◑'}
      </button>

      <div className="app-menu-wrap">
        <button className="avatar" aria-label="App menu" onClick={() => setMenuOpen((v) => !v)}>
          FF
        </button>
        {menuOpen && (
          <div className="app-menu" onMouseLeave={() => setMenuOpen(false)}>
            <button
              onClick={() => {
                setMenuOpen(false);
                navigate({ page: 'settings' });
              }}
            >
              Settings
            </button>
            <button
              onClick={() => {
                setMenuOpen(false);
                window.location.hash = '#/utilities';
              }}
            >
              Storage
            </button>
            <div className="app-menu-note">Fully local. No account, no cloud.</div>
          </div>
        )}
      </div>
      <ScanBar />
    </header>
  );
}

function SelectionBar({ count, onDone }: { count: number; onDone: () => void }) {
  const selection = useStore((s) => s.selection);
  const showToast = useStore((s) => s.showToast);
  const refresh = useStore((s) => s.refresh);
  const hashes = async (): Promise<string[]> => {
    // Preferred: hashes remembered at selection time (no round trips).
    const remembered = useStore.getState().selectionHashes;
    const missing = [...selection].filter((p) => !remembered[p]);
    const extra: Record<string, string> = {};
    for (const path of missing) {
      try {
        const d = await backend.getItem(path);
        const h = (d.item as { media?: { content_hash?: string } } | undefined)?.media
          ?.content_hash;
        if (h) extra[path] = h;
      } catch {
        // unresolvable path (e.g. trashed) — skip
      }
    }
    return [...selection]
      .map((p) => remembered[p] ?? extra[p])
      .filter((h): h is string => Boolean(h));
  };

  const withUndo = (text: string, undo: () => Promise<unknown>) => {
    showToast({ text, kind: 'info', actionLabel: 'Undo', action: undo });
  };

  return (
    <header className="topbar topbar-select">
      <button className="icon-btn" onClick={onDone} aria-label="Clear selection">✕</button>
      <span className="select-count">{count} selected</span>
      <div className="topbar-spacer" />
      <button
        className="btn-ghost"
        onClick={async () => {
          const hs = await hashes();
          await backend.setFavorite(hs, true);
          refresh();
          withUndo('Added to favorites', () => backend.setFavorite(hs, false).then(refresh));
          onDone();
        }}
      >
        Favorite
      </button>
      <button
        className="btn-ghost"
        onClick={async () => {
          const hs = await hashes();
          await backend.setArchived(hs, true);
          refresh();
          withUndo('Archived', () => backend.setArchived(hs, false).then(refresh));
          onDone();
        }}
      >
        Archive
      </button>
      <button
        className="btn-ghost"
        onClick={async () => {
          const hs = await hashes();
          const albumName = await promptText({ title: 'Add to album — create new or pick an existing name:' });
          if (!albumName) return;
          try {
            const created = await backend.createAlbum(albumName);
            const albumId = Number(created.album_id);
            await backend.albumAdd(albumId, hs);
            showToast({ text: `Added to “${albumName}”`, kind: 'info' });
          } catch {
            showToast({ text: 'Could not add to album', kind: 'error' });
          }
          onDone();
        }}
      >
        Add to album
      </button>
      <button
        className="btn-ghost"
        onClick={async () => {
          const hs = await hashes();
          try {
            await backend.setLocked(hs, true);
            showToast({ text: 'Locked — hidden until unlocked', kind: 'info' });
            refresh();
          } catch {
            showToast({ text: 'Set a passcode in Settings first', kind: 'error' });
          }
          onDone();
        }}
      >
        Lock
      </button>
      {count >= 2 && (
        <button
          className="btn-ghost"
          title="Build an animated GIF from the selected photos"
          onClick={async () => {
            const hs = await hashes();
            try {
              await backend.createAnimation(hs);
              showToast({ text: 'Animation created — find it in the feed', kind: 'info' });
              refresh();
            } catch (e) {
              showToast({ text: e instanceof Error ? e.message : 'Animation failed', kind: 'error' });
            }
            onDone();
          }}
        >
          Animate
        </button>
      )}
      {count >= 1 && count <= 4 && (
        <button
          className="btn-ghost"
          title="Build a collage from the selected photos (1–4)"
          onClick={async () => {
            const hs = await hashes();
            try {
              await backend.createCollage(hs);
              showToast({ text: 'Collage created — find it in the feed', kind: 'info' });
              refresh();
            } catch (e) {
              showToast({ text: e instanceof Error ? e.message : 'Collage failed', kind: 'error' });
            }
            onDone();
          }}
        >
          Collage
        </button>
      )}
      <button
        className="btn-ghost btn-danger-ghost"
        onClick={async () => {
          const hs = await hashes();
          await backend.setTrashed(hs, true);
          refresh();
          withUndo('Moved to trash', () => backend.setTrashed(hs, false).then(refresh));
          onDone();
        }}
      >
        Trash
      </button>
    </header>
  );
}
