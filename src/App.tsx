import { useEffect } from 'react';
import './App.css';

import { api, type BackendEvent } from './types';
import { useStore } from './lib/store';
import { installIfMissing } from './lib/mock';

import Rail from './components/Rail';
import TopBar from './components/TopBar';
import FeedPage from './components/pages/FeedPage';
import SearchPage from './components/pages/SearchPage';
import AlbumsPage from './components/pages/AlbumsPage';
import PeoplePage from './components/pages/PeoplePage';
import PlacesPage from './components/pages/PlacesPage';
import { ArchivePage, LockedPage, TrashPage } from './components/pages/LibraryPages';
import UtilitiesPage from './components/pages/UtilitiesPage';
import SettingsPage from './components/pages/SettingsPage';
import ExplorePage from './components/pages/ExplorePage';
import Welcome from './components/Welcome';
import Viewer from './components/Viewer';

export default function App() {
  const route = useStore((s) => s.route);
  const theme = useStore((s) => s.theme);
  const toast = useStore((s) => s.toast);
  const showToast = useStore((s) => s.showToast);
  const refresh = useStore((s) => s.refresh);
  const setScan = useStore((s) => s.setScan);
  const libraryPath = useStore((s) => s.libraryPath);
  const setLibraryPath = useStore((s) => s.setLibraryPath);
  const viewer = useStore((s) => s.viewer);

  // Mock backend for plain-browser dev (also powers visual review).
  const mocked = installIfMissing();
  useEffect(() => {
    if (mocked && !useStore.getState().libraryPath) {
      useStore.getState().setLibraryPath('C:/Photos (mock)');
    }
  }, [mocked]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  // One-time wiring: backend status + event stream.
  useEffect(() => {
    api().onBackendStatus((status) => {
      if (status.state === 'ready' && libraryPath) refresh();
    });

    api().onBackendEvent((event: BackendEvent) => {
      switch (event.event) {
        case 'model_status':
          setScan((s) => (s ? { ...s, modelLoading: event.state === 'loading' } : s));
          break;
        case 'scan_started':
          setScan({ current: 0, total: event.total || 0, file: '', modelLoading: false });
          break;
        case 'scan_progress':
          setScan({
            current: event.processed,
            total: event.total,
            file: event.file,
            hashing: event.hashing,
            modelLoading: false,
          });
          break;
        case 'scan_complete':
          setScan(null);
          refresh();
          showToast({
            text: `Indexed ${event.processed} file(s) · ${event.faces} face(s)`,
            kind: 'info',
          });
          break;
        case 'scan_cancelled':
          setScan(null);
          refresh();
          break;
        case 'scan_error':
          setScan(null);
          showToast({ text: event.message, kind: 'error' });
          break;
        case 'cluster_done':
          showToast({ text: `Found ${event.people} people (${event.unclustered} unsorted)`, kind: 'info' });
          refresh();
          break;
        case 'index_cleared':
          refresh();
          break;
      }
    });

    void api().backendState().then((state) => {
      if (state.state === 'ready' && libraryPath) {
        void api().openLibrary(libraryPath);
        refresh();
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Global shortcuts.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA') return;
      if (e.key === '/') {
        e.preventDefault();
        document.querySelector<HTMLInputElement>('[data-search-input]')?.focus();
      } else if (e.key === '?' ) {
        showToast({ text: 'Shortcuts — / search · ←→ navigate · f favorite · i info · Del trash · Esc close', kind: 'info' });
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [showToast]);

  const page = (() => {
    switch (route.page) {
      case 'photos': return <FeedPage />;
      case 'search': return <SearchPage />;
      case 'albums': return <AlbumsPage />;
      case 'people': return <PeoplePage />;
      case 'places': return <PlacesPage />;
      case 'explore': return <ExplorePage />;
      case 'trash': return <TrashPage />;
      case 'archive': return <ArchivePage />;
      case 'locked': return <LockedPage />;
      case 'utilities': return <UtilitiesPage />;
      case 'settings': return <SettingsPage />;
      default: return <FeedPage />;
    }
  })();

  const bare = route.page === 'settings' || route.page === 'locked';

  return (
    <div className="app">
      <TopBar />
      <div className="app-body">
        {!bare && <Rail />}
        <main className="content">{page}</main>
      </div>

      {viewer && viewer.items[viewer.index] && (
        <Viewer items={viewer.items} index={viewer.index} />
      )}

      {!libraryPath && !mocked && <Welcome onPicked={(p) => setLibraryPath(p)} />}

      {toast && (
        <div className={`toast toast-${toast.kind}`} role="status" aria-live="polite">
          <span>{toast.text}</span>
          {toast.actionLabel && toast.action && (
            <button
              className="toast-action"
              onClick={() => {
                void toast.action?.();
                showToast(null);
              }}
            >
              {toast.actionLabel}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
