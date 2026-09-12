// Types mirroring the Python backend's JSON responses.

export interface Person {
  id: number;
  name: string;
  thumbnail: string | null;
  face_count: number;
}

export interface Face {
  id: number;
  file_path: string;
  bbox: [number, number, number, number];
  thumbnail: string | null;
}

export interface PhotoEntry {
  path: string;
  face_count: number;
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

export interface ScanProgress {
  current: number;
  total: number;
  file: string;
}

export type BackendEvent =
  | { event: 'model_status'; state: 'loading' | 'ready' }
  | { event: 'scan_started'; path: string }
  | { event: 'scan_progress'; current: number; total: number; file: string }
  | { event: 'scan_complete'; path: string; processed: number; faces: number }
  | { event: 'scan_cancelled'; path: string }
  | { event: 'scan_error'; message: string }
  | { event: 'cluster_done'; people: number; new_people: number; assigned: number; unclustered: number }
  | { event: 'index_cleared'; path: string };

// Declared by the Electron preload script.
export interface FaceFrameApi {
  selectFolder(): Promise<string | null>;
  getProviders(): Promise<{ compute: ComputeInfo }>;
  scanDirectory(path: string, provider: string): Promise<unknown>;
  cancelScan(): Promise<unknown>;
  clusterFaces(path: string): Promise<{ people: number }>;
  getPersons(path: string): Promise<{ persons: Person[] }>;
  getUnclusteredFaces(path: string): Promise<{ faces: Face[] }>;
  getPhotosByPerson(path: string, personId: number): Promise<{ photos: PhotoEntry[] }>;
  renamePerson(path: string, personId: number, newName: string): Promise<unknown>;
  mergePersons(path: string, keepId: number, mergeId: number): Promise<unknown>;
  clearIndex(path: string): Promise<unknown>;
  backendState(): Promise<BackendStatus & { state: string; reason?: string }>;
  retryBackend(): Promise<{ state: string }>;
  readImageDataUrl(path: string, maxDim?: number): Promise<string | null>;
  onBackendEvent(callback: (event: BackendEvent) => void): void;
  onBackendStatus(callback: (status: BackendStatus) => void): void;
}

export function api(): FaceFrameApi {
  return (window as unknown as { faceframe: FaceFrameApi }).faceframe;
}
