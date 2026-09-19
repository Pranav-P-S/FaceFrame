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
  clearIndex: (path) => ipcRenderer.invoke('clear-index', path),
  openLibrary: (path) => ipcRenderer.invoke('open-library', path),

  backendState: () => ipcRenderer.invoke('backend-state'),
  retryBackend: () => ipcRenderer.invoke('retry-backend'),

  onBackendEvent: (callback) => {
    backendEventHandler = callback;
  },
  onBackendStatus: (callback) => {
    backendStatusHandler = callback;
  },
});
