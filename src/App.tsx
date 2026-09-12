import { useCallback, useEffect, useRef, useState } from 'react';
import './App.css';

import {
  api,
  BackendEvent,
  ComputeInfo,
  Face,
  Person,
} from './types';
import { folderName } from './images';

import Dialog from './components/Dialog';
import ImageViewer from './components/ImageViewer';
import PeopleGrid from './components/PeopleGrid';
import PersonDetail from './components/PersonDetail';
import UnclusteredStrip from './components/UnclusteredStrip';
import WelcomeScreen from './components/WelcomeScreen';

type BackendState = 'checking' | 'starting' | 'ready' | 'restarting' | 'unavailable';

interface ScanState {
  current: number;
  total: number;
  file: string;
  modelLoading: boolean;
}

interface DialogSpec {
  title: string;
  body: string;
  confirmLabel: string;
  danger: boolean;
  action: () => void;
}

const FOLDER_KEY = 'faceframe.folder';

export default function App() {
  const [backend, setBackend] = useState<BackendState>('checking');
  const [backendReason, setBackendReason] = useState<string | null>(null);
  const [folder, setFolder] = useState<string | null>(() =>
    localStorage.getItem(FOLDER_KEY)
  );
  const [scan, setScan] = useState<ScanState | null>(null);
  const [clustering, setClustering] = useState(false);
  const [persons, setPersons] = useState<Person[]>([]);
  const [unclustered, setUnclustered] = useState<Face[]>([]);
  const [detail, setDetail] = useState<{ person: Person } | null>(null);
  const [detailPhotos, setDetailPhotos] = useState<
    { path: string; face_count: number }[] | null
  >(null);
  const [viewer, setViewer] = useState<{ images: string[]; index: number } | null>(
    null
  );
  const [dialog, setDialog] = useState<DialogSpec | null>(null);
  const [toast, setToast] = useState<{ text: string; kind: 'info' | 'error' } | null>(
    null
  );
  const [compute, setCompute] = useState<ComputeInfo | null>(null);
  const [provider, setProvider] = useState<string>('auto');

  const folderRef = useRef(folder);
  folderRef.current = folder;

  const toastTimer = useRef<number | null>(null);

  const showToast = useCallback((text: string, kind: 'info' | 'error' = 'info') => {
    setToast({ text, kind });
    if (toastTimer.current !== null) window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(
      () => setToast(null),
      kind === 'error' ? 8000 : 4000
    );
  }, []);

  const refreshLibrary = useCallback(async (target?: string) => {
    const path = target ?? folderRef.current;
    if (!path) return;
    try {
      const [personsRes, facesRes] = await Promise.all([
        api().getPersons(path),
        api().getUnclusteredFaces(path),
      ]);
      setPersons(personsRes.persons ?? []);
      setUnclustered(facesRes.faces ?? []);
    } catch (e) {
      showToast(e instanceof Error ? e.message : 'Could not read the index', 'error');
    }
  }, [showToast]);

  const startScan = useCallback(async (path: string, providerChoice: string) => {
    setFolder(path);
    localStorage.setItem(FOLDER_KEY, path);
    setDetail(null);
    setViewer(null);
    setPersons([]);
    setUnclustered([]);
    setScan({ current: 0, total: 0, file: '', modelLoading: true });
    try {
      await api().scanDirectory(path, providerChoice);
    } catch (e) {
      setScan(null);
      showToast(e instanceof Error ? e.message : 'Could not start the scan', 'error');
    }
  }, [showToast]);

  // One-time wiring: backend status + event stream.
  useEffect(() => {
    api().onBackendStatus((status) => {
      if (status.state === 'ready') {
        setBackend('ready');
        setBackendReason(null);
        const saved = localStorage.getItem(FOLDER_KEY);
        if (saved) refreshLibrary(saved);
      } else if (status.state === 'unavailable') {
        // A dead backend can never deliver a scan or clustering verdict;
        // drop any in-flight spinners so the UI does not lock up.
        setBackend('unavailable');
        setBackendReason(status.reason ?? null);
        setScan(null);
        setClustering(false);
      } else if (status.state === 'restarting') {
        setBackend('restarting');
        setScan(null);
        setClustering(false);
      } else {
        setBackend('starting');
      }
    });

    api().onBackendEvent((event: BackendEvent) => {
      switch (event.event) {
        case 'model_status':
          setScan((s) => (s ? { ...s, modelLoading: event.state === 'loading' } : s));
          break;
        case 'scan_started':
          setScan((s) => (s ? { ...s, modelLoading: false } : s));
          break;
        case 'scan_progress':
          setScan({
            current: event.current,
            total: event.total,
            file: event.file,
            modelLoading: false,
          });
          break;
        case 'scan_complete':
          setScan(null);
          refreshLibrary(event.path);
          if (event.processed === 0) {
            showToast('No readable images were found in that folder');
          } else if (event.faces === 0) {
            showToast(
              `Checked ${event.processed} ${event.processed === 1 ? 'file' : 'files'} — no new faces`
            );
          } else {
            showToast(
              `Scanned ${event.processed} ${event.processed === 1 ? 'file' : 'files'}, found ${event.faces} ${event.faces === 1 ? 'face' : 'faces'}`
            );
          }
          break;
        case 'scan_cancelled':
          setScan(null);
          refreshLibrary(event.path);
          showToast('Scan cancelled');
          break;
        case 'scan_error':
          setScan(null);
          showToast(event.message, 'error');
          break;
        case 'cluster_done': {
          setClustering(false);
          refreshLibrary();
          const { people, unclustered: left } = event;
          showToast(
            left > 0
              ? `Found ${people} ${people === 1 ? 'person' : 'people'} (${left} ${left === 1 ? 'face' : 'faces'} still unsorted)`
              : `Found ${people} ${people === 1 ? 'person' : 'people'}`
          );
          break;
        }
        case 'index_cleared':
          setPersons([]);
          setUnclustered([]);
          setDetail(null);
          setViewer(null);
          showToast('Index cleared');
          break;
      }
    });

    api()
      .backendState()
      .then((state) => {
        if (state.state === 'ready') {
          setBackend('ready');
          const saved = localStorage.getItem(FOLDER_KEY);
          if (saved) refreshLibrary(saved);
        } else if (state.state === 'unavailable') {
          setBackend('unavailable');
          setBackendReason(state.reason ?? null);
        } else {
          setBackend('starting');
        }
      })
      .catch(() => setBackend('unavailable'));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Hardware options, once the backend is up.
  useEffect(() => {
    if (backend !== 'ready') return;
    api()
      .getProviders()
      .then((res) => setCompute(res.compute))
      .catch(() => setCompute(null));
  }, [backend]);

  const openPerson = (person: Person) => {
    setDetail({ person });
    setDetailPhotos(null);
    api()
      .getPhotosByPerson(folder ?? '', person.id)
      .then((res) => setDetailPhotos(res.photos))
      .catch(() => {
        setDetailPhotos([]);
        showToast('Could not load photos for this person', 'error');
      });
  };

  const handleRename = async (person: Person, name: string) => {
    if (!folder) return;
    try {
      await api().renamePerson(folder, person.id, name);
      refreshLibrary();
      if (detail?.person.id === person.id) setDetail({ ...detail, person: { ...person, name } });
    } catch (e) {
      showToast(e instanceof Error ? e.message : 'Rename failed', 'error');
    }
  };

  const handleMerge = (keep: Person, merge: Person) => {
    setDialog({
      title: 'Merge people?',
      body: `Move every face of “${merge.name}” into “${keep.name}”. “${merge.name}” is removed. This cannot be undone.`,
      confirmLabel: 'Merge',
      danger: true,
      action: () => {
        api()
          .mergePersons(folder ?? '', keep.id, merge.id)
          .then(() => refreshLibrary())
          .catch((e) =>
            showToast(e instanceof Error ? e.message : 'Merge failed', 'error')
          );
      },
    });
  };

  const handleFindPeople = async () => {
    if (!folder || clustering) return;
    setClustering(true);
    try {
      await api().clusterFaces(folder);
    } catch (e) {
      setClustering(false);
      showToast(e instanceof Error ? e.message : 'Could not group faces', 'error');
    }
  };

  const retryBackend = () => {
    setBackend('starting');
    api()
      .retryBackend()
      .catch(() => setBackend('unavailable'));
  };

  const handleClearIndex = () => {
    if (!folder) return;
    setDialog({
      title: 'Clear the index?',
      body: 'Deletes the .faceframe folder inside the library: all detected faces, people and thumbnails for this folder. Your photos are not touched.',
      confirmLabel: 'Delete index',
      danger: true,
      action: () => {
        api()
          .clearIndex(folder)
          .catch((e) =>
            showToast(e instanceof Error ? e.message : 'Clearing failed', 'error')
          );
      },
    });
  };

  const handleCancelScan = () => {
    api().cancelScan().catch(() => setScan(null));
  };

  const pickAndScanFolder = () => {
    api()
      .selectFolder()
      .then((p) => {
        if (p) startScan(p, provider);
      });
  };

  const busy = scan !== null || clustering || backend !== 'ready';

  const providerOptions = [
    { value: 'auto', label: 'Auto' },
    { value: 'cpu', label: 'CPU' },
    ...(compute?.cuda_listed ? [{ value: 'cuda', label: 'GPU (CUDA)' }] : []),
  ];

  return (
    <div className="app">
      <header className="titlebar">
        <div className="titlebar-brand" onClick={() => setDetail(null)} role="presentation">
          <img src="/icon.svg" alt="" className="brand-icon" />
          <span className="brand-name">FaceFrame</span>
        </div>

        {folder && (
          <button
            className="folder-chip"
            onClick={() => {
              if (!scan) pickAndScanFolder();
            }}
            title={folder}
            disabled={scan !== null}
          >
            <span className="folder-icon" aria-hidden>▸</span>
            {folderName(folder)}
          </button>
        )}

        <div className="titlebar-spacer" />

        {folder && backend === 'ready' && (
          <>
            <label className="provider-label" title="Where face detection runs">
              <span>Engine</span>
              <select
                value={provider}
                onChange={(e) => setProvider(e.target.value)}
                disabled={scan !== null}
              >
                {providerOptions.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.value === 'auto' && compute ? `${o.label} · ${compute.device_label}` : o.label}
                  </option>
                ))}
              </select>
            </label>
            <button
              className="btn"
              disabled={scan !== null}
              onClick={pickAndScanFolder}
            >
              Scan folder
            </button>
            <button className="btn btn-danger-ghost" disabled={scan !== null} onClick={handleClearIndex}>
              Clear index
            </button>
          </>
        )}
      </header>

      {scan && (
        <div className="scanbar">
          {scan.modelLoading && scan.total === 0 ? (
            <div className="scanbar-info">
              <span className="spinner spinner-inline" />
              Preparing the face model — the first run downloads it (~350 MB)
              and may take a few minutes.
            </div>
          ) : (
            <div className="scanbar-progress">
              <div className="scanbar-text">
                <span className="scanbar-file" title={scan.file}>
                  {scan.file || 'Scanning…'}
                </span>
                <span className="scanbar-count">
                  {scan.current} / {scan.total}
                </span>
              </div>
              <div className="scanbar-track">
                <div
                  className="scanbar-fill"
                  style={{
                    width: scan.total ? `${(scan.current / scan.total) * 100}%` : '0%',
                  }}
                />
              </div>
            </div>
          )}
          <button className="btn" onClick={handleCancelScan}>
            Cancel
          </button>
        </div>
      )}

      {backend === 'restarting' && (
        <div className="notice">Restarting the photo engine…</div>
      )}

      {folder && backend !== 'ready' && backend !== 'restarting' && (
        <div className="notice notice-warning">
          {backend === 'unavailable' ? (
            <>
              <span>
                {backendReason === 'python-not-found'
                  ? 'Python was not found, so face detection cannot run. Install Python 3.10+ and the venv (see the README), then try again.'
                  : 'The photo engine is not running.'}
              </span>
              <button className="btn btn-small" onClick={retryBackend}>
                Try again
              </button>
            </>
          ) : (
            <>
              <span className="spinner spinner-inline" /> Starting the photo engine…
            </>
          )}
        </div>
      )}

      <main className="content">
        {!folder ? (
          <WelcomeScreen
            backendUnavailable={backend === 'unavailable'}
            backendReason={backendReason}
            onSelectFolder={pickAndScanFolder}
          />
        ) : (
          <>
            {detail ? (
              <PersonDetail
                person={detail.person}
                photos={detailPhotos}
                onBack={() => setDetail(null)}
                onOpenPhoto={(path) => {
                  const paths = (detailPhotos ?? []).map((p) => p.path);
                  setViewer({ images: paths.length ? paths : [path], index: Math.max(0, paths.indexOf(path)) });
                }}
              />
            ) : (
              <>
                <PeopleGrid
                  persons={persons}
                  busy={busy}
                  onOpenPerson={openPerson}
                  onRename={handleRename}
                  onMerge={handleMerge}
                />
                <UnclusteredStrip
                  faces={unclustered}
                  clustering={clustering}
                  onFindPeople={handleFindPeople}
                  onOpenFace={(face) =>
                    setViewer({ images: [face.file_path], index: 0 })
                  }
                />
                {!scan && persons.length === 0 && unclustered.length === 0 && (
                  <div className="empty-state">
                    {clustering ? (
                      <p className="empty-hint">
                        <span className="spinner" /> Finding people…
                      </p>
                    ) : backend !== 'ready' ? (
                      <p className="empty-hint">
                        <span className="spinner" /> Loading library…
                      </p>
                    ) : (
                      <>
                        <h2>No faces indexed here yet</h2>
                        <p className="empty-hint">
                          Scan this folder to detect faces, or choose another
                          folder from the top bar.
                        </p>
                      </>
                    )}
                  </div>
                )}
                {scan && persons.length === 0 && unclustered.length === 0 && (
                  <div className="empty-state">
                    <p className="empty-hint">
                      <span className="spinner" /> Detecting faces…
                    </p>
                  </div>
                )}
              </>
            )}
          </>
        )}
      </main>

      {viewer && (
        <ImageViewer
          images={viewer.images}
          index={viewer.index}
          onNavigate={(index) => setViewer((v) => (v ? { ...v, index } : v))}
          onClose={() => setViewer(null)}
        />
      )}

      {dialog && (
        <Dialog
          title={dialog.title}
          body={dialog.body}
          confirmLabel={dialog.confirmLabel}
          danger={dialog.danger}
          onConfirm={() => {
            dialog.action();
            setDialog(null);
          }}
          onCancel={() => setDialog(null)}
        />
      )}

      {toast && (
        <div className={`toast toast-${toast.kind}`} role="status" aria-live="polite">
          {toast.text}
        </div>
      )}
    </div>
  );
}
