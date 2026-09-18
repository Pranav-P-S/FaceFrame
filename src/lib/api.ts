import { api } from '../types';
import { useStore } from './store';

/** Typed convenience wrappers over the generic request channel. */
export const backend = {
  // The backend returns the LibraryInfo fields flat (path, items, …); the
  // UI contract is { library } — normalize here so call sites agree.
  openLibrary: async (path: string) => {
    const res = await call('open_library', { path });
    return ('library' in res ? res : { library: res }) as { library: import('../types').LibraryInfo };
  },
  getFeed: (opts: { view?: string; include_archived?: boolean; include_locked?: boolean; favorite?: boolean; archived_only?: boolean; limit?: number } = {}) =>
    call('get_feed', opts),
  search: (query: string, include_locked = false) => call('search', { query, include_locked }),
  getItem: (path: string) => call('get_item', { path }),

  setFavorite: (hashes: string[], favorite: boolean) => call('set_favorite', { hashes, favorite }),
  setArchived: (hashes: string[], archived: boolean) => call('set_archived', { hashes, archived }),
  setLocked: (hashes: string[], locked: boolean) => call('set_locked', { hashes, locked }),
  setTrashed: (hashes: string[] | null, trashed: boolean, paths?: string[]) =>
    call('set_trashed', { hashes, trashed, paths }),
  getTrashed: () => call('get_trashed'),
  getLockedItems: () => call('get_locked_items'),
  emptyTrash: () => call('empty_trash'),
  deleteFromDisk: (paths: string[]) => call('delete_from_disk', { paths }),

  setCaption: (hash: string, caption: string) => call('set_caption', { hash, caption }),
  setDateOverride: (hash: string, epoch: number | null) => call('set_date_override', { hash, epoch }),
  setEdit: (hash: string, edit: Record<string, unknown> | null) => call('set_edit', { hash, edit }),

  setLockedPasscode: (code: string) => call('set_locked_passcode', { code }),
  verifyLockedPasscode: (code: string) => call('verify_locked_passcode', { code }),
  removeLockedPasscode: (code: string) => call('remove_locked_passcode', { code }),

  getAlbums: () => call('get_albums'),
  createAlbum: (name: string) => call('create_album', { name }),
  renameAlbum: (albumId: number, name: string) => call('rename_album', { album_id: albumId, name }),
  deleteAlbum: (albumId: number) => call('delete_album', { album_id: albumId }),
  setAlbumCover: (albumId: number, hash: string) => call('set_album_cover', { album_id: albumId, hash }),
  albumAdd: (albumId: number, hashes: string[]) => call('album_add', { album_id: albumId, hashes }),
  albumRemove: (albumId: number, hashes: string[]) => call('album_remove', { album_id: albumId, hashes }),
  getAlbum: (albumId: number) => call('get_album', { album_id: albumId }),
  setAlbumSort: (albumId: number, sort: string) => call('set_album_sort', { album_id: albumId, sort }),

  getPersons: (path: string) => call('get_persons', { path }),
  getUnclustered: (path: string) => call('get_unclustered', { path }),
  getPhotosByPerson: (path: string, personId: number) =>
    call('get_photos_by_person', { path, person_id: personId }),
  renamePerson: (path: string, personId: number, newName: string) =>
    call('rename_person', { path, person_id: personId, new_name: newName }),
  mergePersons: (path: string, keepId: number, mergeId: number) =>
    call('merge_persons', { path, keep_id: keepId, merge_id: mergeId }),
  splitPerson: (path: string, personId: number, faceIds: number[], newName?: string) =>
    call('split_person', { path, person_id: personId, face_ids: faceIds, new_name: newName }),
  assignFaces: (path: string, faceIds: number[], personId: number | null) =>
    call('assign_faces', { path, face_ids: faceIds, person_id: personId }),
  setPersonHidden: (path: string, personId: number, hidden: boolean) =>
    call('set_person_hidden', { path, person_id: personId, hidden }),
  setPersonThumbnail: (path: string, personId: number, thumbnail: string) =>
    call('set_person_thumbnail', { path, person_id: personId, thumbnail }),

  magicEraser: (filePath: string, rects: number[][]) => call('magic_eraser', { file_path: filePath, rects }),
  getPlaces: (geocode: boolean) => call('get_places', { geocode }),
  getMemories: () => call('get_memories'),
  getDuplicates: () => call('get_duplicates'),
  getMissing: () => call('get_missing'),
  resolveMissing: (paths: string[]) => call('resolve_missing', { paths }),
  getStorageStats: () => call('get_storage_stats'),
  clearCaches: () => call('clear_caches'),
  exportItems: (paths: string[], dest: string) => call('export_items', { paths, dest }),

  createCollage: (hashes: string[]) => call('create_collage', { hashes }),
  createAnimation: (hashes: string[]) => call('create_animation', { hashes }),

  getSettings: () => call('get_settings'),
  setSettings: (settings: Record<string, string | number>) => call('set_settings', { settings }),
  setWatch: (enabled: boolean, interval?: number) => call('set_watch', { enabled, interval }),
};

// Actions whose context comes from a file path instead of the library root
// (or that carry their own path). Everything else the backend resolves via
// req["path"] — the library root — which we attach automatically so call
// sites don't have to thread it through every wrapper.
const FILE_SCOPED_ACTIONS = new Set(['get_item', 'get_image_preview', 'magic_eraser']);

// get_feed/search are the two views whose SQL returns library-RELATIVE file
// paths (every other handler absolutizes via _as_item). The UI contract is
// absolute paths (see types.ts), so re-anchor them to the library root here.
function isAbsolutePath(p: string): boolean {
  return /^([a-zA-Z]:[\\/]|\/|\\\\)/.test(p);
}

function absolutizeItems(root: string, res: Record<string, unknown>): Record<string, unknown> {
  const base = root.replace(/[\\/]+$/, '');
  const fix = (item: unknown) => {
    const it = item as { path?: string } | undefined;
    if (it && typeof it.path === 'string' && it.path && !isAbsolutePath(it.path)) {
      it.path = `${base}/${it.path}`;
    }
  };
  const groups = res.groups as { items?: unknown[] }[] | undefined;
  if (Array.isArray(groups)) for (const g of groups) (g.items ?? []).forEach(fix);
  const items = res.items as unknown[] | undefined;
  if (Array.isArray(items)) items.forEach(fix);
  return res;
}

async function call(action: string, params?: Record<string, unknown>): Promise<Record<string, unknown>> {
  const merged = { ...params };
  const libraryPath = useStore.getState().libraryPath;
  if (!FILE_SCOPED_ACTIONS.has(action) && merged.path === undefined) {
    if (libraryPath) merged.path = libraryPath;
  }
  const res = await api().request(action, merged);
  if ((action === 'get_feed' || action === 'search') && libraryPath) {
    return absolutizeItems(libraryPath, res);
  }
  return res;
}
