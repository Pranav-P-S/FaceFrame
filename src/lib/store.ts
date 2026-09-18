import { create } from 'zustand';
import type { Item, ScanState } from '../types';

import { parseHash, hrefFor, type Route } from './router';

export type { Route } from './router';

export interface Toast {
  text: string;
  kind: 'info' | 'error';
  actionLabel?: string;
  action?: () => void;
}

interface AppState {
  route: Route;
  theme: 'light' | 'dark';
  libraryPath: string | null;
  density: number; // grid zoom level 0..3
  selection: Set<string>; // absolute item paths
  selectionOrder: string[];
  anchor: string | null;
  viewer: { items: Item[]; index: number } | null;
  infoOpen: boolean;
  toast: Toast | null;
  scan: ScanState | null;
  /** Bumped by mutation flows so pages refetch. */
  refreshToken: number;

  navigate: (route: Route) => void;
  setTheme: (theme: 'light' | 'dark') => void;
  setLibraryPath: (path: string | null) => void;
  setDensity: (level: number) => void;
  setScan: (scan: ScanState | null | ((s: ScanState | null) => ScanState | null)) => void;
  refresh: () => void;
  toggleSelect: (path: string) => void;
  selectRange: (paths: string[], anchor: string) => void;
  selectAll: (paths: string[]) => void;
  clearSelection: () => void;
  openViewer: (items: Item[], index: number) => void;
  closeViewer: () => void;
  setViewerIndex: (index: number) => void;
  setInfoOpen: (open: boolean) => void;
  showToast: (toast: Toast | null) => void;
}

export const useStore = create<AppState>((set, get) => ({
  route: parseHash(window.location.hash),
  theme:
    (localStorage.getItem('faceframe.theme') as 'light' | 'dark') ||
    (window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'),
  libraryPath: localStorage.getItem('faceframe.folder'),
  density: Number(localStorage.getItem('faceframe.density') ?? 1),
  selection: new Set(),
  selectionOrder: [],
  anchor: null,
  viewer: null,
  infoOpen: false,
  toast: null,
  scan: null,
  refreshToken: 0,

  navigate: (route) => {
    window.location.hash = hrefFor(route);
    set({ route, selection: new Set(), selectionOrder: [], anchor: null });
  },
  setTheme: (theme) => {
    localStorage.setItem('faceframe.theme', theme);
    document.documentElement.dataset.theme = theme;
    set({ theme });
  },
  setLibraryPath: (path) => {
    if (path) localStorage.setItem('faceframe.folder', path);
    else localStorage.removeItem('faceframe.folder');
    set({ libraryPath: path });
  },
  setDensity: (level) => {
    localStorage.setItem('faceframe.density', String(level));
    set({ density: level });
  },
  setScan: (scan) => set((s) => ({ scan: typeof scan === 'function' ? scan(s.scan) : scan })),
  refresh: () => set((s) => ({ refreshToken: s.refreshToken + 1 })),
  toggleSelect: (path) => {
    const { selection, selectionOrder } = get();
    const next = new Set(selection);
    if (next.has(path)) {
      next.delete(path);
      set({ selection: next, selectionOrder: selectionOrder.filter((p) => p !== path) });
    } else {
      next.add(path);
      set({ selection: next, selectionOrder: [...selectionOrder, path], anchor: path });
    }
  },
  selectRange: (paths, anchor) => {
    const state = get();
    const from = state.anchor ? paths.indexOf(state.anchor) : paths.indexOf(anchor);
    const to = paths.indexOf(anchor);
    if (from < 0 || to < 0) return;
    const start = Math.min(from, to);
    const end = Math.max(from, to);
    set({
      selection: new Set(paths.slice(start, end + 1)),
      selectionOrder: paths.slice(start, end + 1),
      anchor,
    });
  },
  selectAll: (paths) => set({ selection: new Set(paths), selectionOrder: [...paths] }),
  clearSelection: () => set({ selection: new Set(), selectionOrder: [], anchor: null }),

  openViewer: (items, index) => set({ viewer: { items, index } }),
  closeViewer: () => set({ viewer: null, infoOpen: false }),
  setViewerIndex: (index) => {
    const { viewer } = get();
    if (viewer && index >= 0 && index < viewer.items.length) set({ viewer: { ...viewer, index } });
  },
  setInfoOpen: (open) => set({ infoOpen: open }),
  showToast: (toast) => set({ toast }),
}));

if (typeof window !== 'undefined') {
  (window as unknown as { __ffstore: unknown }).__ffstore = useStore;
}

window.addEventListener('hashchange', () => {
  useStore.setState({ route: parseHash(window.location.hash) });
});
