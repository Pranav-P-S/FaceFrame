import { useCallback, useEffect, useState } from 'react';
import type { Item } from '../../types';
import { backend } from '../../lib/api';
import { useStore } from '../../lib/store';
import PhotoGrid from '../../components/PhotoGrid';

/** Trash / Archive / Locked views: same grid, different lifecycle actions. */

export function TrashPage() {
  const [items, setItems] = useState<Item[]>([]);
  const showToast = useStore((s) => s.showToast);
  const refresh = useStore((s) => s.refresh);

  const reload = useCallback(() => {
    void backend.getTrashed().then((res) => setItems((res.items as Item[]) ?? []));
  }, []);
  useEffect(reload, [reload]);

  return (
    <div className="page">
      <div className="page-head">
        <h1 className="page-title">Trash</h1>
        <div className="btn-row">
          <button
            className="btn-ghost"
            onClick={() => {
              void backend.emptyTrash();
              showToast({ text: 'Trash emptied — files went to the OS Recycle Bin', kind: 'info' });
              refresh();
              reload();
            }}
          >
            Empty trash
          </button>
          <span className="info-muted">Items are purged automatically after 60 days. Emptying moves files to the OS Recycle Bin.</span>
        </div>
      </div>
      {items.length > 0 ? (
        <div>
          <div className="chip-row">
            <RestoreAllBar paths={items.map((i) => i.path)} onDone={reload} />
          </div>
          <PhotoGrid groups={[{ key: 'trash', items }]} view="days" onOpen={() => undefined} flatten />
        </div>
      ) : (
        <div className="empty-state"><h2>Trash is empty</h2></div>
      )}
    </div>
  );
}

function RestoreAllBar({ paths, onDone }: { paths: string[]; onDone: () => void }) {
  const showToast = useStore((s) => s.showToast);
  return (
    <>
      <button
        className="btn-ghost"
        onClick={() => {
          void backend.setTrashed(null, false, paths);
          showToast({ text: 'Items restored', kind: 'info' });
          onDone();
        }}
      >
        Restore all ({paths.length})
      </button>
      <button
        className="btn-ghost btn-danger-ghost"
        onClick={() => {
          if (!window.confirm(`Delete ${paths.length} file(s) from disk? They go to the OS Recycle Bin.`)) return;
          void backend.deleteFromDisk(paths);
          showToast({ text: 'Deleted from disk (OS Recycle Bin)', kind: 'info' });
          onDone();
        }}
      >
        Delete from disk
      </button>
    </>
  );
}

export function ArchivePage() {
  const openViewer = useStore((s) => s.openViewer);
  const refreshToken = useStore((s) => s.refreshToken);
  const [groups, setGroups] = useState<{ key: string; items: Item[] }[]>([]);
  useEffect(() => {
    void backend.getFeed({ view: 'days', archived_only: true }).then((res) => {
      setGroups((res.groups as { key: string; items: Item[] }[]) ?? []);
    });
  }, [refreshToken]);
  return (
    <div className="page">
      <h1 className="page-title">Archive</h1>
      <div className="notice notice-info">Archived photos are hidden from the main feed but still show up in search.</div>
      {groups.length ? (
        <PhotoGrid groups={groups} view="days" onOpen={openViewer} />
      ) : (
        <div className="empty-state"><h2>Nothing archived</h2></div>
      )}
    </div>
  );
}

export function LockedPage() {
  const [unlocked, setUnlocked] = useState(false);
  const [code, setCode] = useState('');
  const [items, setItems] = useState<Item[]>([]);
  const showToast = useStore((s) => s.showToast);

  const load = () => {
    void backend.getLockedItems().then((res) => setItems((res.items as Item[]) ?? []));
  };
  useEffect(() => {
    if (unlocked) load();
  }, [unlocked]);

  if (!unlocked) {
    return (
      <div className="page locked-gate">
        <div className="empty-state">
          <h2>🔒 Locked folder</h2>
          <p className="empty-hint">
            Enter the passcode to show items you have hidden. Files stay on disk —
            this hides them from FaceFrame's views, it does not encrypt.
          </p>
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              const res = await backend.verifyLockedPasscode(code);
              if (res.ok) setUnlocked(true);
              else showToast({ text: 'Wrong passcode', kind: 'error' });
            }}
          >
            <input
              type="password"
              autoFocus
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder="Passcode"
            />
            <button className="btn" type="submit">Unlock</button>
          </form>
        </div>
      </div>
    );
  }
  return (
    <div className="page">
      <h1 className="page-title">Locked items</h1>
      {items.length ? (
        <PhotoGrid groups={[{ key: 'locked', items }]} view="days" onOpen={() => undefined} flatten />
      ) : (
        <div className="empty-state"><h2>Nothing locked</h2><p className="empty-hint">Select items in the feed and lock them from the selection bar.</p></div>
      )}
    </div>
  );
}
