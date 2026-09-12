import { useState } from 'react';
import { api } from '../types';

interface WelcomeScreenProps {
  backendUnavailable: boolean;
  backendReason: string | null;
  onSelectFolder: () => void;
}

export default function WelcomeScreen({
  backendUnavailable,
  backendReason,
  onSelectFolder,
}: WelcomeScreenProps) {
  const [retrying, setRetrying] = useState(false);

  const retry = () => {
    setRetrying(true);
    api()
      .retryBackend()
      .catch(() => setRetrying(false));
    // The backend-status event flips the app out of this screen when the
    // engine answers (or fails again).
    window.setTimeout(() => setRetrying(false), 15000);
  };

  return (
    <div className="welcome">
      <img src="/icon.svg" alt="" className="welcome-logo" />
      <h1>FaceFrame</h1>
      <p className="welcome-tagline">
        Find every face in a photo folder and group them by person. Everything
        runs on this machine — no uploads, no accounts.
      </p>

      {backendUnavailable ? (
        <div className="setup-card">
          <h2>The photo engine could not start</h2>
          {backendReason === 'python-not-found' ? (
            <ol>
              <li>
                Install <strong>Python 3.10 or newer</strong> from python.org
                (on Windows, tick “Add python.exe to PATH”).
              </li>
              <li>
                In the project folder run:
                <code>python -m venv venv</code>
                <code>venv\Scripts\pip install -r python-backend\requirements.txt</code>
                (use <code>venv/bin/pip</code> on Linux and macOS)
              </li>
            </ol>
          ) : (
            <p>
              The background engine exited unexpectedly. If this keeps
              happening, start the app from a terminal to see the error output.
            </p>
          )}
          <button className="btn" onClick={retry} disabled={retrying}>
            {retrying ? (
              <>
                <span className="spinner spinner-inline" /> Trying…
              </>
            ) : (
              'Try again'
            )}
          </button>
        </div>
      ) : (
        <>
          <button className="btn btn-primary btn-lg" onClick={onSelectFolder}>
            Choose a photo folder
          </button>
          <p className="welcome-hint">
            FaceFrame adds a hidden <code>.faceframe</code> folder inside the
            folder you pick. That is where the face index and thumbnails live;
            delete it any time to remove every trace.
          </p>
        </>
      )}
    </div>
  );
}
