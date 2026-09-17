import { api } from '../types';
import { useStore } from '../lib/store';

/** First-run onboarding: explains the local model, picks the library. */
export default function Welcome({ onPicked }: { onPicked: (path: string) => void }) {
  const showToast = useStore((s) => s.showToast);
  const setScan = useStore((s) => s.setScan);

  const pick = async () => {
    const folder = await api().selectFolder();
    if (!folder) return;
    onPicked(folder);
    try {
      setScan({ current: 0, total: 0, file: '', modelLoading: true });
      await api().scanDirectory(folder, 'auto');
    } catch (e) {
      setScan(null);
      showToast({ text: e instanceof Error ? e.message : 'Scan failed to start', kind: 'error' });
    }
  };

  return (
    <div className="welcome" role="dialog" aria-modal="true">
      <div className="welcome-card">
        <img src="/icon.svg" alt="" className="welcome-icon" />
        <h1>Your photos, on your machine.</h1>
        <p>
          FaceFrame Photos brings the Google Photos experience fully offline:
          an infinite feed, search across people, places and things, albums,
          memories, a non-destructive editor — and no account, no backup, no
          uploads. Everything is indexed into a hidden <code>.faceframe</code>{' '}
          folder inside the library you choose.
        </p>
        <button className="btn btn-primary" onClick={() => void pick()}>
          Choose your photo folder
        </button>
        <p className="info-muted">
          The first scan downloads face models (~350 MB, one time). After that
          everything works offline.
        </p>
      </div>
    </div>
  );
}
