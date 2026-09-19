// Types mirroring the Python backend's JSON responses (v3 protocol).

export interface Person {
  id: number;
  name: string;
  thumbnail: string | null;
  face_count: number;
  hidden?: number;
}

export interface Face {
  id: number;
  content_hash?: string;
  file_path?: string | null;
  bbox: [number, number, number, number];
  thumbnail: string | null;
}

export interface ComputeInfo {
  providers: string[];
  cuda_listed: boolean;
  device_label: string;
}

export interface BackendStatus {
  state: 'starting' | 'ready' | 'restarting' | 'unavailable';
  reason?: string;
}

export type MediaKind = 'photo' | 'video';

/** Lean item shape used by feeds/search/albums. Paths are absolute. */
export interface Item {
  path: string;
  content_hash: string;
  kind: string; // photo | video | creation
  added_at?: number;
  ts: number;
  media_kind?: MediaKind;
  width?: number | null;
  height?: number | null;
  duration?: number | null;
  favorite?: number;
  archived?: number;
  locked?: number;
  caption?: string | null;
  poster_path?: string | null;
  flags?: Record<string, unknown>;
  labels?: string[];
}

export interface FeedGroup {
  key: string;
  items: Item[];
}

export interface MediaInfo {
  content_hash: string;
  kind: MediaKind;
  width: number | null;
  height: number | null;
  duration: number | null;
  capture_time: number | null;
  tz_offset: string | null;
  exif: string | null;
  phash: string | null;
  labels: string | null;
  flags: string | null;
  caption: string | null;
  edit: string | null;
  date_override: number | null;
  favorite: number;
  archived: number;
  locked: number;
  poster_path: string | null;
}

export interface ItemDetail {
  path: string;
  content_hash: string;
  kind: string;
  size: number;
  mtime: number;
  added_at: number;
  missing: number;
  trashed_at: number | null;
  creation_id: number | null;
  media: MediaInfo | null;
  faces: { id: number; bbox: string; person_id: number | null; thumbnail_path: string | null; person_name: string | null }[];
  albums: { id: number; name: string }[];
  duplicate_paths: string[];
  ts: number;
}

export interface AlbumSummary {
  id: number;
  name: string;
  description: string | null;
  cover_hash: string | null;
  cover: string | null;
  sort_key: string;
  created_at: number;
  count: number;
}

export interface AlbumDetail extends AlbumSummary {
  items: Item[];
}

export interface PlaceGroup {
  geohash: string;
  count: number;
  items: string[];
  cover_hash: string;
  cover: string | null;
  lat: number;
  lon: number;
  name: string | null;
}

export interface MemoryGroup {
  type: 'on_this_day' | 'trip' | 'highlights';
  title: string;
  items: Item[];
  cover_hash: string;
  cover: string | null;
  years_ago?: number;
  date?: string;
  place?: string;
  start?: number;
  end?: number;
}

export interface LibraryInfo {
  path: string;
  items: number;
  missing: number;
  lock_set: boolean;
  labels_enabled: boolean;
  labels_ready: boolean;
  watch_enabled: boolean;
}

export interface StorageStats {
  items: { count: number; bytes: number };
  index_bytes: number;
  caches: Record<string, number>;
}

export interface ScanState {
  current: number;
  total: number;
  file: string;
  hashing?: boolean;
  modelLoading: boolean;
}

export type BackendEvent =
  | { event: 'model_status'; state: 'loading' | 'ready' }
  | { event: 'scan_started'; path: string; total: number }
  | { event: 'scan_progress'; processed: number; total: number; file: string; hashing?: boolean }
  | { event: 'scan_complete'; path: string; processed: number; faces: number; [k: string]: unknown }
  | { event: 'scan_cancelled'; path: string }
  | { event: 'scan_error'; message: string }
  | { event: 'cluster_done'; people: number; new_people: number; assigned: number; unclustered: number }
  | { event: 'index_cleared'; path: string }
  | { event: 'places_updated'; path: string }
  | { event: 'index_restored'; path: string };

/** Declared by the Electron preload script (or the dev mock). */
export interface FaceFrameApi {
  selectFolder(): Promise<string | null>;
  request(action: string, params?: Record<string, unknown>): Promise<Record<string, unknown>>;
  mediaUrl(filePath: string): string;

  getProviders(): Promise<{ compute: ComputeInfo }>;
  scanDirectory(path: string, provider: string): Promise<unknown>;
  cancelScan(): Promise<unknown>;
  clusterFaces(path: string): Promise<{ people: number }>;
  clearIndex(path: string): Promise<unknown>;
  openLibrary(path: string): Promise<{ library: LibraryInfo }>;

  backendState(): Promise<BackendStatus & { state: string; reason?: string }>;
  retryBackend(): Promise<{ state: string }>;

  onBackendEvent(callback: (event: BackendEvent) => void): void;
  onBackendStatus(callback: (status: BackendStatus) => void): void;
}

export function api(): FaceFrameApi {
  const w = window as unknown as {
    faceframe?: FaceFrameApi;
    __mockApi?: FaceFrameApi;
  };
  // ?mock=1 forces the dev mock even where a real preload exists.
  if (new URLSearchParams(window.location.search).has('mock') && w.__mockApi) {
    return w.__mockApi;
  }
  return (w.faceframe ?? w.__mockApi) as FaceFrameApi;
}
