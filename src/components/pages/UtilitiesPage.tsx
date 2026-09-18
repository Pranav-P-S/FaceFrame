import { useEffect, useState } from 'react';
import { backend } from '../../lib/api';
import { api } from '../../types';
import { useStore } from '../../lib/store';
import { formatBytes } from '../../lib/format';

/** Utilities: duplicates review, missing files, storage usage, creations. */
export default function UtilitiesPage() {
  const [duplicates, setDuplicates] = useState<{ hash: string; paths: string[]; count: number }[]>([]);
  const [missing, setMissing] = useState<{ path: string; kind: string }[]>([]);
  const [stats, setStats] = useState<{
    items: { count: number; bytes: number };
    index_bytes: number;
    caches: Record<string, number>;
  } | null>(null);
  const [health, setHealth] = useState<Record<string, number | string> | null>(null);
  const showToast = useStore((s) => s.showToast);
  const refresh = useStore((s) => s.refresh);
  const libraryPath = useStore((s) => s.libraryPath);

  const reload = () => {
    void backend.getDuplicates().then((res) => setDuplicates((res.groups as typeof duplicates) ?? []));
    void backend.getMissing().then((res) => setMissing((res.items as typeof missing) ?? []));
    void backend.getStorageStats().then((res) => setStats(res.stats as never));
  };
  useEffect(reload, []);
  useEffect(reload, []);

  return (
    <div className="page utilities">
      <h1 className="page-title">Utilities</h1>

      <section className="utility-card">
        <h2>Storage</h2>
        {stats && (
          <>
            <div className="storage-row">
              <strong>Library total — {stats.items.count} items</strong>
              <strong>{formatBytes(stats.items.bytes)}</strong>
            </div>
            <div className="storage-row info-muted">
              <span>FaceFrame index</span>
              <span>{formatBytes(stats.index_bytes)}</span>
            </div>
            {Object.entries(stats.caches).map(([name, bytes]) => (
              <div key={name} className="storage-row info-muted">
                <span>{name} cache</span>
                <span>{formatBytes(bytes)}</span>
              </div>
            ))}
            <button
              className="btn-chip"
              onClick={async () => {
                const res = await backend.clearCaches();
                showToast({ text: `Cleared ${(res.removed as number) ?? 0} cached files`, kind: 'info' });
                reload();
              }}
            >
              Clear preview caches
            </button>
            <p className="info-muted">Face thumbnails are never cleared here — they require re-running detection.</p>
          </>
        )}
      </section>

      <section className="utility-card">
        <h2>Backup &amp; health</h2>
        <div className="btn-row">
          <button
            className="btn"
            onClick={async () => {
              if (!libraryPath) return;
              const dest = window.prompt('Back up the index to folder:', 'C:/Backup');
              if (!dest) return;
              try {
                const res = await api().request('backup_index', { path: libraryPath, dest });
                showToast({ text: `Backup written to ${res.path} (${Math.round(Number(res.bytes) / 1e6)} MB)`, kind: 'info' });
              } catch (e) {
                showToast({ text: `Backup failed: ${e instanceof Error ? e.message : e}`, kind: 'error' });
              }
            }}
          >
            Back up index…
          </button>
          <button
            className="btn-chip"
            onClick={async () => {
              if (!libraryPath) return;
              try {
                const res = await api().request('check_index', { path: libraryPath, repair: true });
                setHealth(res.report as never);
                showToast({ text: `Integrity: ${String((res.report as { integrity?: string }).integrity)}`, kind: 'info' });
              } catch (e) {
                showToast({ text: `Check failed: ${e instanceof Error ? e.message : e}`, kind: 'error' });
              }
            }}
          >
            Check &amp; repair index
          </button>
        </div>
        {health && (
          <div className="storage-row info-muted">
            <span>
              Integrity: {String(health.integrity)} · orphan media: {String(health.orphan_media)} ·
              missing thumbs: {String(health.missing_thumbs)} · FTS drift: {String(health.fts_drift)}
            </span>
          </div>
        )}
        <p className="info-muted">
          Backups capture the database and face thumbnails — the parts that
          cannot be regenerated. Previews and posters are rebuildable.
        </p>
      </section>

      <section className="utility-card">
        <h2>Duplicates</h2>
        {duplicates.length === 0 ? (
          <p className="info-muted">No exact duplicates found.</p>
        ) : (
          duplicates.map((group) => (
            <div key={group.hash} className="dupe-group">
              <div className="dupe-head">
                <strong>{group.count} identical copies</strong>
                <button
                  className="btn-chip"
                  onClick={() => {
                    void backend.setTrashed(null, true, group.paths.slice(1));
                    showToast({ text: 'Extra copies moved to trash (original kept)', kind: 'info' });
                    reload();
                    refresh();
                  }}
                >
                  Keep newest, trash the rest
                </button>
              </div>
              <ul className="dupe-paths">
                {group.paths.map((p, i) => (
                  <li key={p} className={i === 0 ? 'dupe-keeper' : ''}>{p}</li>
                ))}
              </ul>
            </div>
          ))
        )}
      </section>

      <section className="utility-card">
        <h2>Missing files</h2>
        {missing.length === 0 ? (
          <p className="info-muted">Every indexed file is present on disk.</p>
        ) : (
          <>
            <ul className="dupe-paths">
              {missing.map((m) => (
                <li key={m.path}>{m.path}</li>
              ))}
            </ul>
            <button
              className="btn-chip"
              onClick={() => {
                void backend.resolveMissing(missing.map((m) => m.path));
                showToast({ text: 'Missing files removed from the index', kind: 'info' });
                reload();
              }}
            >
              Remove from index
            </button>
            <p className="info-muted">If a file comes back, it is re-linked automatically on the next scan.</p>
          </>
        )}
      </section>
    </div>
  );
}
