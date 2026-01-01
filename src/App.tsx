import { useState, useEffect } from "react";
import "./App.css";

import PeopleList from './components/PeopleList';
import UnclusteredFaces from './components/UnclusteredFaces';

interface AppState {
  status: "init" | "ready" | "scanning" | "error";
  folderPath: string | null;
}

// Extend window interface for Electron API
declare global {
  interface Window {
    electronAPI: {
      selectFolder: () => Promise<string | null>;
      scanDirectory: (path: string, provider: string) => Promise<any>;
      cancelScan: () => Promise<any>;
      clusterFaces: (path: string) => Promise<any>;
      getPersons: (path: string) => Promise<any>;
      getUnclusteredFaces: (path: string) => Promise<any>;
      clearIndex: (path: string) => Promise<any>;
      renamePerson: (path: string, personId: number, newName: string) => Promise<any>;
      mergePersons: (path: string, keepId: number, mergeId: number) => Promise<any>;
      getProviders: () => Promise<void>;
      onBackendMessage: (callback: (data: any) => void) => void;
    };
  }
}

const PROVIDER_NAMES: Record<string, string> = {
  'CPUExecutionProvider': 'CPU (Standard)',
  'CUDAExecutionProvider': 'NVIDIA GPU (CUDA)',
  'TensorrtExecutionProvider': 'NVIDIA TensorRT',
  'OpenVINOExecutionProvider': 'Intel OpenVINO',
  'DmlExecutionProvider': 'DirectML (Windows)',
};

function App() {
  const [state, setState] = useState<AppState>({ status: "ready", folderPath: null });
  const [providers, setProviders] = useState<string[]>([]);
  const [providerLabels, setProviderLabels] = useState<Record<string, string>>({});
  const [selectedProvider, setSelectedProvider] = useState<string>("CPUExecutionProvider");
  const [progress, setProgress] = useState<{ current: number, total: number, file: string } | null>(null);

  const [persons, setPersons] = useState<any[]>([]);
  const [unclustered, setUnclustered] = useState<any[]>([]);
  const [viewerImage, setViewerImage] = useState<string | null>(null);

  const fetchDisplayData = (path: string) => {
    window.electronAPI.getPersons(path);
    window.electronAPI.getUnclusteredFaces(path);
  };

  useEffect(() => {
    // Listen for backend messages via Electron bridge
    if (window.electronAPI) {
      window.electronAPI.onBackendMessage((data: any) => {

        if (data.status === 'providers') {
          setProviders(data.providers);

          // Build label map with actual GPU name if available
          const labels: Record<string, string> = { ...PROVIDER_NAMES };
          if (data.gpu_info?.cuda_available && data.gpu_info?.gpu_name) {
            labels['CUDAExecutionProvider'] = data.gpu_info.gpu_name;
          }
          // Filter out TensorRT if not truly available
          const filteredProviders = data.providers.filter((p: string) => {
            if (p === 'TensorrtExecutionProvider' && !data.gpu_info?.cuda_available) return false;
            return true;
          });
          setProviders(filteredProviders);
          setProviderLabels(labels);

          // Auto-select CUDA if available
          const cuda = filteredProviders.find((p: string) => p.includes('CUDA'));
          if (cuda) setSelectedProvider(cuda);
          else if (filteredProviders.length > 0) setSelectedProvider(filteredProviders[0]);
        }
        if (data.status === 'progress') {
          setProgress({
            current: data.current || 0,
            total: data.total || 0,
            file: data.file || 'Unknown'
          });
        }
        if (data.status === 'complete' || data.status === 'error' || data.status === 'cancelled') {
          setState(s => ({ ...s, status: "ready" }));
          setProgress(null);
          if (state.folderPath) fetchDisplayData(state.folderPath);
        }
        if (data.status === 'persons') {
          console.log('Received persons:', data.data);
          setPersons(data.data);
        }
        if (data.status === 'unclustered') {
          console.log('Received unclustered faces:', data.data?.length, 'Sample:', data.data?.[0]);
          setUnclustered(data.data);
        }
        if (data.status === 'clustered' || data.status === 'index_cleared') {
          if (state.folderPath) fetchDisplayData(state.folderPath);
        }
      });

      // Fetch providers on mount
      setTimeout(() => {
        window.electronAPI.getProviders().catch(e => console.error(e));
      }, 1000);
    }
  }, [state.folderPath]);

  const handleSelectFolder = async () => {
    try {
      if (!window.electronAPI) {
        console.error("Electron API not found");
        return;
      }
      const path = await window.electronAPI.selectFolder();
      if (path) {
        console.log("Selected folder:", path);
        setState(s => ({ ...s, folderPath: path, status: "scanning" }));

        // Trigger scan
        await window.electronAPI.scanDirectory(path, selectedProvider);
      }
    } catch (err) {
      console.error("Error selecting folder:", err);
    }
  };

  const handleCancelScan = async () => {
    await window.electronAPI.cancelScan();
  };

  const handleClearIndex = async () => {
    if (state.folderPath) {
      if (confirm("Are you sure you want to delete all indexing data? This cannot be undone.")) {
        await window.electronAPI.clearIndex(state.folderPath);
        setPersons([]);
        setUnclustered([]);
      }
    }
  };

  const handleCluster = async () => {
    if (state.folderPath) {
      console.log("Clustering...");
      await window.electronAPI.clusterFaces(state.folderPath);
    }
  };

  return (
    <div className="app-container" style={{ width: '100vw', height: '100vh', overflowY: 'auto', background: '#1e1e1e', color: '#eee' }}>
      {/* Header Section */}
      <div style={{ padding: '20px', background: '#252525', borderBottom: '1px solid #333', display: 'flex', alignItems: 'center', justifyContent: 'space-between', position: 'sticky', top: 0, zIndex: 10 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '20px' }}>
          <h1 style={{ margin: 0, fontSize: '24px' }}>FaceFrame</h1>

          <button className="primary" onClick={handleSelectFolder} disabled={state.status === 'scanning'}>
            {state.folderPath ? "Scan New Folder" : "Select Folder to Scan"}
          </button>

          {state.folderPath && state.status !== 'scanning' && (
            <button className="danger" onClick={handleClearIndex} style={{ background: '#d32f2f', color: 'white', border: 'none', padding: '8px 12px', borderRadius: '4px', cursor: 'pointer' }}>
              Clear Index
            </button>
          )}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '15px' }}>
          {/* Hardware Select */}
          <select
            value={selectedProvider}
            onChange={(e) => setSelectedProvider(e.target.value)}
            disabled={state.status === 'scanning'}
            style={{ padding: '8px', borderRadius: '4px', background: '#333', color: 'white', border: '1px solid #555', maxWidth: '200px' }}
          >
            {providers.length === 0 && <option>Loading Hardware...</option>}
            {providers.map(p => <option key={p} value={p}>{providerLabels[p] || PROVIDER_NAMES[p] || p}</option>)}
          </select>

          {/* Cluster Button (Only if ready and has data) */}
          {state.status === 'ready' && state.folderPath && unclustered.length > 0 && (
            <button
              className="secondary"
              onClick={handleCluster}
              style={{ padding: '8px 16px', borderRadius: '4px', cursor: 'pointer', background: '#2196F3', color: 'white', border: 'none' }}
            >
              Find People ({unclustered.length})
            </button>
          )}
        </div>
      </div>

      {/* Progress Bar & Cancel */}
      {state.status === 'scanning' && progress && (
        <div style={{ padding: '20px', background: '#222', borderBottom: '1px solid #444', display: 'flex', alignItems: 'center', gap: '20px' }}>
          <div style={{ flex: 1 }}>
            <div style={{ marginBottom: '5px', display: 'flex', justifyContent: 'space-between', fontSize: '14px' }}>
              <span style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: '400px' }}>Scanning: {progress.file}</span>
              <span style={{ fontFamily: 'monospace' }}>{progress.current} / {progress.total}</span>
            </div>
            <div style={{ width: '100%', height: '8px', background: '#444', borderRadius: '4px', overflow: 'hidden' }}>
              <div style={{
                width: `${(progress.total > 0 ? (progress.current / progress.total) : 0) * 100}%`,
                height: '100%',
                background: '#4CAF50',
                borderRadius: '4px',
                transition: 'width 0.3s ease-out'
              }}
              />
            </div>
          </div>
          <button onClick={handleCancelScan} style={{ padding: '8px 16px', color: 'white', background: '#d32f2f', border: 'none', borderRadius: '4px', cursor: 'pointer' }}>
            Cancel
          </button>
        </div>
      )}

      {/* Main Content */}
      {!state.folderPath ? (
        <div style={{ padding: '40px', textAlign: 'center', color: '#888' }}>
          <h2>Welcome to FaceFrame</h2>
          <p>Select a folder to start organizing your photos securely.</p>
        </div>
      ) : (
        <div>
          {/* 1. People Clusters */}
          <PeopleList
            persons={persons}
            folderPath={state.folderPath || ''}
            onRefresh={() => state.folderPath && fetchDisplayData(state.folderPath)}
          />

          {/* 2. Unclustered Faces */}
          <UnclusteredFaces
            faces={unclustered}
            onFaceClick={(face) => {
              // Open full image in viewer
              if (face.file_path) {
                const url = 'safe-file://' + face.file_path.replace(/\\/g, '/');
                console.log('Opening image:', url);
                setViewerImage(url);
              }
            }}
          />
        </div>
      )}

      {/* Image Viewer Modal */}
      {viewerImage && (
        <div
          onClick={() => setViewerImage(null)}
          onKeyDown={(e) => e.key === 'Escape' && setViewerImage(null)}
          tabIndex={0}
          style={{
            position: 'fixed',
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            background: 'rgba(0,0,0,0.9)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 1000,
            cursor: 'pointer'
          }}
        >
          <img
            src={viewerImage}
            style={{ maxWidth: '90vw', maxHeight: '90vh', objectFit: 'contain' }}
            onClick={(e) => e.stopPropagation()}
          />
          <div style={{ position: 'absolute', top: '20px', right: '20px', color: 'white', fontSize: '14px' }}>
            Press ESC or click to close
          </div>
        </div>
      )}
    </div>
  );
}

export default App;
