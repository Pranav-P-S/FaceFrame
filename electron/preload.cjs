const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
    selectFolder: () => ipcRenderer.invoke('select-folder'),
    scanDirectory: (path, provider) => ipcRenderer.invoke('scan-directory', path, provider),
    cancelScan: () => ipcRenderer.invoke('cancel-scan'),
    getProviders: () => ipcRenderer.invoke('get-providers'),
    clusterFaces: (path) => ipcRenderer.invoke('cluster-faces', path),
    getPersons: (path) => ipcRenderer.invoke('get-persons', path),
    getUnclusteredFaces: (path) => ipcRenderer.invoke('get-unclustered-faces', path),
    clearIndex: (path) => ipcRenderer.invoke('clear-index', path),
    renamePerson: (path, personId, newName) => ipcRenderer.invoke('rename-person', path, personId, newName),
    mergePersons: (path, keepId, mergeId) => ipcRenderer.invoke('merge-persons', path, keepId, mergeId),
    onBackendMessage: (callback) => ipcRenderer.on('backend-message', (_event, value) => callback(value)),
});
