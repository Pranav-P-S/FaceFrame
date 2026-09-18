import type { FaceFrameApi, Item } from '../types';

/**
 * Browser-dev fake backend. When the app runs in a plain browser (vite dev
 * without Electron) there is no window.faceframe; this module installs a
 * deterministic mock so the whole UI is explorable — also the basis for the
 * visual-review pass. Data is seeded; photos are generated canvas gradients.
 */

interface MockMedia extends Item {
  exif?: string;
  labels: string[];
  edit: Record<string, unknown> | null;
  dateOverride: number | null;
  trashedAt: number | null;
  missing: number;
  persons: number[];
}

const DAY = 86400;
const NOW = Math.floor(Date.now() / 1000);

let rngState = 42;
function rng(): number {
  rngState = (rngState * 1664525 + 1013904223) % 4294967296;
  return rngState / 4294967296;
}

const PALETTES: [string, string][] = [
  ['#ff9a56', '#ff5e7d'], ['#4facfe', '#00f2fe'], ['#a18cd1', '#fbc2eb'],
  ['#43e97b', '#38f9d7'], ['#fa709a', '#fee140'], ['#30cfd0', '#330867'],
  ['#f093fb', '#f5576c'], ['#4481eb', '#04befe'], ['#0ba360', '#3cba92'],
  ['#c471f5', '#fa71cd'], ['#48c6ef', '#6f86d6'], ['#feada6', '#f5efef'],
];

const canvas = document.createElement('canvas');
canvas.width = 640;
canvas.height = 480;
const ctx = canvas.getContext('2d')!;

function gradientDataUrl(seed: number, label = ''): string {
  const [a, b] = PALETTES[seed % PALETTES.length];
  const g = ctx.createLinearGradient(0, 0, 640, 480);
  g.addColorStop(0, a);
  g.addColorStop(1, b);
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, 640, 480);
  ctx.fillStyle = 'rgba(255,255,255,0.35)';
  for (let i = 0; i < 7; i++) {
    const cx = (rng() * 7 | 0) * 100 + 20;
    const cy = (rng() * 5 | 0) * 96 + 40;
    ctx.beginPath();
    ctx.arc(cx, cy, 30 + rng() * 60, 0, Math.PI * 2);
    ctx.fill();
  }
  if (label) {
    ctx.fillStyle = 'rgba(0,0,0,0.5)';
    ctx.font = 'bold 36px system-ui';
    ctx.fillText(label, 24, 440);
  }
  return canvas.toDataURL('image/jpeg', 0.85);
}

function avatarDataUrl(name: string): string {
  const [a, b] = PALETTES[name.length % PALETTES.length];
  ctx.fillStyle = '#fff';
  ctx.fillRect(0, 0, 640, 480);
  const g = ctx.createLinearGradient(0, 0, 640, 480);
  g.addColorStop(0, a);
  g.addColorStop(1, b);
  ctx.fillStyle = g;
  ctx.beginPath();
  ctx.arc(320, 200, 120, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = '#fff';
  ctx.font = 'bold 120px system-ui';
  ctx.textAlign = 'center';
  ctx.fillText(name[0].toUpperCase(), 320, 245);
  ctx.font = 'bold 44px system-ui';
  ctx.fillText(name, 320, 420);
  ctx.textAlign = 'left';
  return canvas.toDataURL('image/jpeg', 0.9);
}

declare global {
  interface Window {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    faceframe?: any;
    __mock?: boolean;
  }
}

interface MockState {
  items: MockMedia[];
  persons: { id: number; name: string; faceCount: number; avatar: string; hidden: boolean }[];
  albums: { id: number; name: string; cover: string | null; hashes: string[] }[];
  captions: Map<string, string>;
}

let state: MockState;

function seed(): void {
  rngState = 42;
  const items: MockMedia[] = [];
  const persons = [
    { id: 1, name: 'Maya', faceCount: 34, avatar: avatarDataUrl('Maya'), hidden: false },
    { id: 2, name: 'Leo', faceCount: 21, avatar: avatarDataUrl('Leo'), hidden: false },
    { id: 3, name: 'Ana', faceCount: 12, avatar: avatarDataUrl('Ana'), hidden: false },
  ];
  const labelSets = [
    ['dog', 'grass', 'outdoor'], ['beach', 'sea', 'sky'], ['food', 'table', 'indoor'],
    ['mountain', 'hiking', 'sky'], ['city', 'night', 'street'], ['flower', 'garden', 'macro'],
  ];
  let hash = 0;
  const makeHash = () => `h${String(++hash).padStart(6, '0')}`;

  // Three weeks of photos, a few per day, plus a year-old set for memories.
  const places = [[52.52, 13.40], [48.85, 2.35], [40.71, -74.0]];
  for (let day = 0; day < 21; day++) {
    const count = 1 + Math.floor(rng() * 4);
    for (let i = 0; i < count; i++) {
      const isVideo = rng() < 0.12;
      const p = places[Math.floor(rng() * 3)];
      const ts = NOW - day * DAY - Math.floor(rng() * DAY * 0.8);
      const item: MockMedia = {
        path: `C:/Photos/2026/${day}/photo_${day}_${i}.jpg`,
        content_hash: makeHash(),
        kind: isVideo ? 'video' : 'photo',
        media_kind: isVideo ? 'video' : 'photo',
        added_at: ts,
        ts,
        width: 640,
        height: 480,
        duration: isVideo ? 5 + rng() * 40 : null,
        favorite: rng() < 0.15 ? 1 : 0,
        archived: 0,
        locked: 0,
        caption: null,
        poster_path: null,
        flags: {},
        labels: labelSets[Math.floor(rng() * labelSets.length)],
        exif: JSON.stringify({
          camera: 'Pixel 9 Pro',
          iso: 100 + Math.floor(rng() * 800),
          f_number: 1.7,
          exposure: 1 / 120,
          focal_length: 6.8,
          gps: p,
        }),
        edit: null,
        dateOverride: null,
        trashedAt: null,
        missing: 0,
        persons: rng() < 0.5 ? [persons[Math.floor(rng() * 3)].id] : [],
      };
      items.push(item);
    }
  }
  // Year-ago memories
  for (let i = 0; i < 4; i++) {
    const p = places[0];
    const ts = NOW - 365 * DAY + i * 3600;
    items.push({
      path: `C:/Photos/2025/memory_${i}.jpg`,
      content_hash: makeHash(),
      kind: 'photo',
      media_kind: 'photo',
      added_at: ts,
      ts,
      width: 640,
      height: 480,
      duration: null,
      favorite: i === 0 ? 1 : 0,
      archived: 0,
      locked: 0,
      caption: null,
      poster_path: null,
      flags: {},
      labels: ['city', 'street'],
      exif: JSON.stringify({ camera: 'Pixel 9 Pro', gps: p }),
      edit: null,
      dateOverride: null,
      trashedAt: null,
      missing: 0,
      persons: [1],
    });
  }
  // One archived, one trashed item for the library pages
  items[3].archived = 1;
  items[5].trashedAt = Date.now() / 1000 - 2 * DAY;

  const albums: { id: number; name: string; cover: string | null; hashes: string[] }[] = [
    { id: 1, name: 'Summer trip', cover: items[0]?.path ?? null, hashes: items.slice(0, 8).map((i) => i.content_hash) },
    {
      id: 2,
      name: 'Favorites',
      cover: items.find((i) => i.favorite)?.path ?? null,
      hashes: items.filter((i) => i.favorite).slice(0, 10).map((i) => i.content_hash),
    },
  ];

  state = { items, persons, albums, captions: new Map() };
}

const images = new Map<string, string>();
function imageFor(item: MockMedia): string {
  let url = images.get(item.content_hash);
  if (!url) {
    url = gradientDataUrl(Number(item.content_hash.slice(1)) || 1, item.kind === 'video' ? '▶' : '');
    images.set(item.content_hash, url);
  }
  return url;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function ok(data: Record<string, unknown> = {}): Promise<any> {
  return Promise.resolve(data);
}

function findItem(path: string): MockMedia | undefined {
  return state.items.find((i) => i.path === path);
}

function visible(): MockMedia[] {
  return state.items.filter((i) => !i.trashedAt && !i.missing && !i.locked);
}

function installMock(): void {
  seed();
  const api: FaceFrameApi = {
    selectFolder: () => Promise.resolve(null),
    request: (action, params = {}) => {
      switch (action) {
        case 'get_image_preview': {
          const item = findItem(String(params.file_path || ''));
          if (!item) return ok({});
          // The mock serves one gradient per content hash at any size.
          return ok({ data_url: imageFor(item) });
        }
        case 'open_library':
          return ok({
            path: 'C:/Photos', items: visible().length, missing: 0,
            lock_set: true, labels_enabled: true, labels_ready: true, watch_enabled: false,
          });
        case 'get_feed': {
          const view = (params.view as string) || 'days';
          const groups = new Map<string, Item[]>();
          for (const item of visible()) {
            if (item.archived && !params.include_archived) continue;
            const d = new Date(item.ts * 1000);
            const key = view === 'days'
              ? `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
              : view === 'months'
                ? `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
                : String(d.getFullYear());
            if (!groups.has(key)) groups.set(key, []);
            groups.get(key)!.push(item);
          }
          const sorted = [...groups.entries()].sort((a, b) => (a[0] < b[0] ? 1 : -1));
          return ok({ groups: sorted.map(([key, items]) => ({ key, items })) });
        }
        case 'search': {
          const q = String(params.query || '').toLowerCase();
          const items = visible().filter(
            (i) =>
              !q ||
              i.labels.some((l) => l.includes(q)) ||
              (i.caption || '').toLowerCase().includes(q) ||
              i.path.toLowerCase().includes(q)
          );
          return ok({ items, total: items.length, filters: {} });
        }
        case 'get_item': {
          const item = findItem(String(params.path));
          return ok({
            item: item && {
              ...item,
              media: { ...item },
              faces: item.persons.map((pid) => ({
                id: pid, person_id: pid, bbox: '[250,120,400,300]',
                thumbnail_path: null, person_name: state.persons.find((p) => p.id === pid)?.name ?? null,
              })),
              albums: state.albums.filter((a) => a.hashes.includes(item.content_hash)).map((a) => ({ id: a.id, name: a.name })),
              duplicate_paths: [],
              size: 2_400_000,
              mtime: item.ts,
              added_at: item.ts,
              missing: 0,
              trashed_at: item.trashedAt,
            },
          });
        }
        case 'set_favorite': {
          const hashes = params.hashes as string[];
          for (const i of state.items) if (hashes.includes(i.content_hash)) i.favorite = params.favorite ? 1 : 0;
          return ok();
        }
        case 'set_archived': {
          const hashes = params.hashes as string[];
          for (const i of state.items) if (hashes.includes(i.content_hash)) i.archived = params.archived ? 1 : 0;
          return ok();
        }
        case 'set_trashed': {
          const hashes = (params.hashes as string[]) || [];
          for (const i of state.items) {
            if (hashes.includes(i.content_hash)) i.trashedAt = params.trashed ? Date.now() / 1000 : null;
          }
          return ok();
        }
        case 'set_caption': {
          state.captions.set(String(params.hash), String(params.caption));
          const item = state.items.find((i) => i.content_hash === params.hash);
          if (item) item.caption = String(params.caption);
          return ok();
        }
        case 'set_edit': {
          const item = state.items.find((i) => i.content_hash === params.hash);
          if (item) item.edit = (params.edit as Record<string, unknown>) || null;
          return ok();
        }
        case 'set_date_override': {
          const item = state.items.find((i) => i.content_hash === params.hash);
          if (item) item.dateOverride = params.epoch as number | null;
          return ok();
        }
        case 'get_trashed':
          return ok({ items: state.items.filter((i) => i.trashedAt) });
        case 'get_locked_items':
          return ok({ items: [] });
        case 'get_albums':
          return ok({
            albums: state.albums.map((a) => ({
              id: a.id, name: a.name, description: null, cover_hash: a.cover,
              cover: a.cover, sort_key: 'added', created_at: NOW, count: a.hashes.length,
            })),
          });
        case 'get_album': {
          const album = state.albums.find((a) => a.id === params.album_id);
          if (!album) return ok({});
          return ok({
            album: {
              id: album.id, name: album.name, description: null, cover_hash: album.cover,
              cover: album.cover, sort_key: 'added', created_at: NOW, count: album.hashes.length,
              items: visible().filter((i) => album.hashes.includes(i.content_hash)),
            },
          });
        }
        case 'create_album':
          state.albums.push({
            id: state.albums.length + 1,
            name: String(params.name || 'New album'),
            cover: null, hashes: [],
          });
          return ok({ album_id: state.albums.length });
        case 'album_add': {
          const album = state.albums.find((a) => a.id === params.album_id);
          for (const h of (params.hashes as string[]) || []) {
            if (album && !album.hashes.includes(h)) album.hashes.push(h);
          }
          return ok();
        }
        case 'album_remove': {
          const album = state.albums.find((a) => a.id === params.album_id);
          if (album) album.hashes = album.hashes.filter((h) => !(params.hashes as string[]).includes(h));
          return ok();
        }
        case 'get_persons':
          return ok({
            persons: state.persons
              .filter((p) => !p.hidden)
              .map((p) => ({ id: p.id, name: p.name, thumbnail: p.avatar, face_count: p.faceCount })),
          });
        case 'get_unclustered':
          return ok({ faces: [] });
        case 'get_photos_by_person': {
          const pid = params.person_id as number;
          const photos = visible().filter((i) => i.persons.includes(pid));
          return ok({ photos: photos.map((p) => ({ path: p.path, face_count: 1 })) });
        }
        case 'rename_person': {
          const p = state.persons.find((x) => x.id === params.person_id);
          if (p) p.name = String(params.new_name);
          return ok();
        }
        case 'set_person_hidden': {
          const p = state.persons.find((x) => x.id === params.person_id);
          if (p) p.hidden = Boolean(params.hidden);
          return ok();
        }
        case 'get_places': {
          const all = visible();
          const coverAt = (n: number) => all[n % all.length]?.path ?? null;
          return ok({
            places: [
              { geohash: 'u33d', count: 9, items: [], cover: coverAt(2), lat: 52.52, lon: 13.40, name: 'Berlin, Germany' },
              { geohash: 'u09t', count: 6, items: [], cover: coverAt(5), lat: 48.85, lon: 2.35, name: 'Paris, France' },
              { geohash: 'dr5r', count: 4, items: [], cover: coverAt(9), lat: 40.71, lon: -74.0, name: 'New York, USA' },
            ],
          });
        }
        case 'get_memories': {
          const memoryItems = visible().filter((i) => i.path.includes('memory'));
          const favItems = visible().filter((i) => i.favorite).slice(0, 8);
          return ok({
            memories: [
              {
                type: 'on_this_day', title: '1 year ago', years_ago: 1,
                items: memoryItems,
                cover_hash: memoryItems[0]?.content_hash ?? '',
                cover: memoryItems[0]?.path ?? null,
              },
              {
                type: 'highlights', title: 'Recent highlights',
                items: favItems,
                cover_hash: favItems[0]?.content_hash ?? '',
                cover: favItems[0]?.path ?? null,
              },
            ],
          });
        }
        case 'get_duplicates':
          return ok({ groups: [] });
        case 'get_missing':
          return ok({ items: [] });
        case 'get_storage_stats': {
          const bytes = visible().length * 3_200_000;
          return ok({
            stats: {
              items: { count: visible().length, bytes },
              index_bytes: Math.round(bytes * 0.015),
              caches: {
                thumbnails: Math.round(bytes * 0.04),
                previews: Math.round(bytes * 0.09),
                posters: Math.round(bytes * 0.01),
                creations: 0,
              },
            },
          });
        }
        case 'get_settings':
          return ok({ settings: { setting_labels: '1', setting_geocode: '1', setting_watch: '0' } });
        case 'set_settings':
          return ok();
        case 'set_watch':
          return ok();
        case 'verify_locked_passcode':
          return ok({ ok: params.code === '1234' });
        default:
          return ok();
      }
    },
    mediaUrl: (filePath) => `mock-video://${filePath}`,
    getProviders: () => ok({ compute: { providers: ['CPUExecutionProvider'], cuda_listed: false, device_label: 'CPU (mock)' } }),
    scanDirectory: () => ok(),
    cancelScan: () => ok(),
    clusterFaces: () => ok({ people: 3 }),
    openLibrary: () => ok({ library: { path: 'C:/Photos', items: visible().length, missing: 0, lock_set: true, labels_enabled: true, labels_ready: true, watch_enabled: false } }),
    getPersons: () => ok({
      persons: state.persons
        .filter((p) => !p.hidden)
        .map((p) => ({ id: p.id, name: p.name, thumbnail: p.avatar, face_count: p.faceCount })),
    }),
    getUnclusteredFaces: () => ok({ faces: [] }),
    getPhotosByPerson: (_path, personId) =>
      ok({
        photos: visible()
          .filter((i) => i.persons.includes(personId))
          .map((p) => ({ path: p.path, face_count: 1 })),
      }),
    renamePerson: async () => undefined,
    mergePersons: async () => undefined,
    clearIndex: async () => undefined,
    backendState: () => ok({ state: 'ready' }),
    retryBackend: () => ok({ state: 'ready' }),
    readImageDataUrl: (path, maxDim = 640) => {
      const item = findItem(path);
      if (!item) return Promise.resolve(null);
      void maxDim;
      return Promise.resolve(imageFor(item));
    },
    onBackendEvent: () => undefined,
    onBackendStatus: () => undefined,
  };
  (window as { __mockApi?: unknown }).__mockApi = api;
}

export function isMock(): boolean {
  return (window as { __mock?: boolean }).__mock === true;
}

export function installIfMissing(): boolean {
  // Idempotent across React StrictMode's double render.
  if ((window as { __mock?: boolean }).__mock === true) return true;
  if ('faceframe' in window) return false;
  installMock();
  (window as { __mock?: boolean }).__mock = true;
  return true;
}

/** Forced mock (dev/visual review): ?mock=1 overrides a real preload API. */
export function installForced(): boolean {
  installMock();
  (window as { __mock?: boolean }).__mock = true;
  return true;
}
