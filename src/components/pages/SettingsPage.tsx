import { useEffect, useState } from 'react';
import { api } from '../../types';
import { backend } from '../../lib/api';
import { useStore } from '../../lib/store';
import { promptText } from '../../lib/prompt';

/** Settings: engine, feature toggles, watch mode, library management. */
export default function SettingsPage() {
  const libraryPath = useStore((s) => s.libraryPath);
  const setLibraryPath = useStore((s) => s.setLibraryPath);
  const refresh = useStore((s) => s.refresh);
  const showToast = useStore((s) => s.showToast);
  const [compute, setCompute] = useState<{ device_label: string; cuda_listed: boolean } | null>(null);
  const [settings, setSettings] = useState<Record<string, string>>({});
  const [scanning, setScanning] = useState(false);
  const [lockCode, setLockCode] = useState('');
  const [lockSet, setLockSet] = useState<boolean | null>(null);
  const [provider, setProvider] = useState(() => localStorage.getItem('faceframe.provider') || 'auto');

  useEffect(() => {
    void api().getProviders().then((res) => setCompute(res.compute)).catch(() => setCompute(null));
    if (libraryPath) {
      void backend.openLibrary(libraryPath).then((res) => {
        const lib = res.library as { lock_set?: boolean } | undefined;
        setLockSet(lib?.lock_set ?? false);
      });
      void backend.getSettings().then((res) => {
        const raw = (res.settings as Record<string, string | null>) ?? {};
        setSettings({
          setting_labels: raw.setting_labels ?? '1',
          setting_geocode: raw.setting_geocode ?? '0',
          setting_watch: raw.setting_watch ?? '0',
        });
      });
    }
  }, [libraryPath]);

  const pickFolder = async () => {
    const folder = await api().selectFolder();
    if (!folder) return;
    setLibraryPath(folder);
    setScanning(true);
    try {
      await api().scanDirectory(folder, localStorage.getItem('faceframe.provider') || 'auto');
      showToast({ text: 'Scan started', kind: 'info' });
    } catch (e) {
      showToast({ text: e instanceof Error ? e.message : 'Scan failed to start', kind: 'error' });
    } finally {
      setScanning(false);
      refresh();
    }
  };

  return (
    <div className="page settings">
      <h1 className="page-title">Settings</h1>

      <section className="utility-card">
        <h2>Library</h2>
        <div className="info-muted">{libraryPath ?? 'No library selected'}</div>
        <div className="btn-row">
          <button className="btn" onClick={() => void pickFolder()} disabled={scanning}>
            {scanning ? <span className="spinner spinner-inline" /> : null}
            {libraryPath ? 'Rescan folder' : 'Choose folder'}
          </button>
          {libraryPath && (
            <button
              className="btn-ghost btn-danger-ghost"
              onClick={async () => {
                if (!window.confirm('Clear the index? Faces, albums and edits stored by FaceFrame are removed. Your photos are not touched.')) return;
                try {
                  await api().clearIndex(libraryPath);
                  showToast({ text: 'Index cleared', kind: 'info' });
                } catch (e) {
                  showToast({ text: e instanceof Error ? e.message : 'Could not clear the index', kind: 'error' });
                }
                refresh();
              }}
            >
              Clear index
            </button>
          )}
        </div>
      </section>

      <section className="utility-card">
        <h2>Processing</h2>
        <div className="setting-row">
          <span>Detection engine</span>
          <select
            value={provider}
            onChange={(e) => {
              setProvider(e.target.value);
              localStorage.setItem('faceframe.provider', e.target.value);
            }}
          >
            <option value="auto">Auto{compute ? ` (${compute.device_label})` : ''}</option>
            <option value="cpu">CPU</option>
            {compute?.cuda_listed && <option value="cuda">GPU (CUDA)</option>}
          </select>
        </div>
        {(
          [
            ['setting_labels', 'Things labels (downloads a 14 MB model once)'],
            ['setting_geocode', 'Reverse geocoding for place names (online, cached, off by default)'],
            ['setting_watch', 'Watch mode — rescan every 30 s while running'],
          ] as const
        ).map(([key, label]) => (
          <label key={key} className="setting-row setting-toggle">
            <span>{label}</span>
            <input
              type="checkbox"
              checked={settings[key] === '1'}
              onChange={async (e) => {
                const next = { ...settings, [key]: e.target.checked ? '1' : '0' };
                setSettings(next);
                if (libraryPath) await backend.setSettings({ [key]: next[key] });
              }}
            />
          </label>
        ))}
      </section>

      <section className="utility-card">
        <h2>Locked folder</h2>
        {lockSet === null ? (
          <p className="info-muted">…</p>
        ) : lockSet ? (
          <p className="info-muted">A passcode is set for this library.</p>
        ) : (
          <p className="info-muted">No passcode set. Hiding items requires one.</p>
        )}
        <form
          className="btn-row"
          onSubmit={async (e) => {
            e.preventDefault();
            if (!libraryPath || !lockCode) return;
            try {
              await backend.setLockedPasscode(lockCode);
              showToast({ text: 'Passcode saved', kind: 'info' });
              setLockSet(true);
              setLockCode('');
            } catch {
              showToast({ text: 'Could not set passcode', kind: 'error' });
            }
          }}
        >
          <input
            type="password"
            placeholder={lockSet ? 'New passcode' : 'Set a passcode'}
            value={lockCode}
            onChange={(e) => setLockCode(e.target.value)}
          />
          <button className="btn btn-small" type="submit">Save</button>
        </form>
        {lockSet && (
          <button
            className="btn-chip btn-danger-ghost"
            onClick={async () => {
              if (!libraryPath) return;
              const code = await promptText({ title: 'Passcode to remove the locked folder:', placeholder: 'Current passcode' });
              if (!code) return;
              try {
                await backend.removeLockedPasscode(code);
                showToast({ text: 'Passcode removed — locked items are visible again', kind: 'info' });
                setLockSet(false);
                refresh();
              } catch {
                showToast({ text: 'Wrong passcode', kind: 'error' });
              }
            }}
          >
            Remove passcode…
          </button>
        )}
        <p className="info-muted">Hides items from FaceFrame's views — files stay unencrypted on disk.</p>
      </section>

      <section className="utility-card">
        <h2>About</h2>
        <p className="info-muted">
          FaceFrame Photos — a local photo manager. No account, no backup, no
          uploads: your photos never leave this machine. The entire index lives
          in a hidden <code>.faceframe</code> folder inside your library;
          delete it and every trace of the app is gone. Optional online
          conveniences (place-name lookup, one-time model downloads) can each
          be switched off above.
        </p>
      </section>
    </div>
  );
}
