const { contextBridge, ipcRenderer } = require('electron');

// Listeners are registered exactly once at preload load; the renderer only
// ever swaps the handler. Registering per-subscription would double-handle
// events under React StrictMode's double effect invocation.
let backendEventHandler = null;
let backendStatusHandler = null;

ipcRenderer.on('backend-event', (_event, payload) => {
  if (backendEventHandler) backendEventHandler(payload);
});
ipcRenderer.on('backend-status', (_event, payload) => {
  if (backendStatusHandler) backendStatusHandler(payload);
});

contextBridge.exposeInMainWorld('faceframe', {
  selectFolder: () => ipcRenderer.invoke('select-folder'),

  // Generic backend call: request('action', {params}) mirrors the NDJSON
  // protocol one-to-one.
  request: (action, params) => ipcRenderer.invoke('backend-request', action, params),

  // Streaming URL for a media file (videos, motion-photo clips). Only
  // registered library roots are served.
  mediaUrl: (filePath) =>
    `media://file?path=${encodeURIComponent(filePath)}`,

  getProviders: () => ipcRenderer.invoke('get-providers'),
  scanDirectory: (path, provider) =>
    ipcRenderer.invoke('scan-directory', path, provider),
  cancelScan: () => ipcRenderer.invoke('cancel-scan'),
  clusterFaces: (path) => ipcRenderer.invoke('cluster-faces', path),
  openLibrary: (path) => ipcRenderer.invoke('open-library', path),

  getPersons: (path) => ipcRenderer.invoke('get-persons', path),
  getUnclusteredFaces: (path) => ipcRenderer.invoke('get-unclustered-faces', path),
  getPhotosByPerson: (path, personId) =>
    ipcRenderer.invoke('get-photos-by-person', path, personId),
  renamePerson: (path, personId, newName) =>
    ipcRenderer.invoke('rename-person', path, personId, newName),
  mergePersons: (path, keepId, mergeId) =>
    ipcRenderer.invoke('merge-persons', path, keepId, mergeId),
  clearIndex: (path) => ipcRenderer.invoke('clear-index', path),

  backendState: () => ipcRenderer.invoke('backend-state'),
  retryBackend: () => ipcRenderer.invoke('retry-backend'),
  readImageDataUrl: (path, maxDim) =>
    ipcRenderer.invoke('read-image-data-url', path, maxDim),

  onBackendEvent: (callback) => {
    backendEventHandler = callback;
  },
  onBackendStatus: (callback) => {
    backendStatusHandler = callback;
  },
});
