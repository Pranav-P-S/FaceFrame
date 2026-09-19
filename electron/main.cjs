const { app, BrowserWindow, ipcMain, dialog, net, protocol } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const { pathToFileURL } = require('url');
const fs = require('fs');
const { Readable } = require('stream');

let mainWindow = null;
let pythonProcess = null;
let backendReady = false;
let backendError = null;
let restartTimer = null;
let backendStarting = false;
let consecutiveCrashes = 0;
// Last known lifecycle state, so backend-state never has to guess.
let lastBackendStatus = { state: 'starting' };

const pendingRequests = new Map(); // id -> {resolve, reject, timer}
let nextRequestId = 1;

// Per-action response timeouts: ping must fail fast (health checks rely on
// it), clustering can legitimately run for a long time on big libraries.
const ACTION_TIMEOUTS = {
  ping: 5000,
  cluster: 60 * 60 * 1000,
};
const DEFAULT_TIMEOUT = 10 * 60 * 1000;

const isDev = !!process.env.ELECTRON_START_URL;

// The renderer shell is served from app://bundle instead of file://:
// ES module scripts are CORS-blocked on file:// origins, and a standard
// scheme gives the bundle a real origin for CSP and fetch.
const APP_SCHEME = 'app';
const APP_ORIGIN = 'app://bundle';
// media:// streams originals (videos) to the renderer with HTTP range
// support; it is restricted to registered library roots.
const MEDIA_SCHEME = 'media';
protocol.registerSchemesAsPrivileged([
  {
    scheme: APP_SCHEME,
    privileges: { standard: true, secure: true, supportFetchAPI: true },
  },
  {
    scheme: MEDIA_SCHEME,
    privileges: { standard: true, secure: true, stream: true, supportFetchAPI: true },
  },
]);

function registerAppProtocol() {
  const distDir = path.join(__dirname, '..', 'dist');
  protocol.handle(APP_SCHEME, (request) => {
    try {
      const { pathname } = new URL(request.url);
      let rel = decodeURIComponent(pathname).replace(/^\/+/, '');
      if (!rel) rel = 'index.html';
      const filePath = path.normalize(path.join(distDir, rel));
      if (filePath !== distDir && !filePath.startsWith(distDir + path.sep)) {
        return new Response('Forbidden', { status: 403 });
      }
      return net.fetch(pathToFileURL(filePath).toString());
    } catch (e) {
      console.error('app:// request failed:', e);
      return new Response('Not found', { status: 404 });
    }
  });
}

// ---------------------------------------------------------------------------
// media:// — range-capable streaming of originals for <video>
// ---------------------------------------------------------------------------

// Media roots are ONLY the folders the user picked in the OS folder dialog
// (plus their persisted history). open_library/scan with a raw renderer path
// does not grant streaming rights — that call can name any directory.
const mediaRoots = new Set();
const mediaRootsFile = () =>
  path.join(app.getPath('userData'), 'media-roots.json');

function loadMediaRoots() {
  try {
    for (const root of JSON.parse(fs.readFileSync(mediaRootsFile(), 'utf8'))) {
      mediaRoots.add(path.normalize(root));
    }
  } catch {
    // first run / unreadable — start empty
  }
}

function persistMediaRoots() {
  try {
    fs.mkdirSync(path.dirname(mediaRootsFile()), { recursive: true });
    fs.writeFileSync(mediaRootsFile(), JSON.stringify([...mediaRoots], null, 1));
  } catch (e) {
    console.error('could not persist media roots:', e);
  }
}

// Only real media files are ever streamed — a compromised renderer must not
// be able to read documents through the scheme.
const MEDIA_MIME = {
  '.mp4': 'video/mp4',
  '.m4v': 'video/mp4',
  '.mov': 'video/quicktime',
  '.webm': 'video/webm',
  '.avi': 'video/x-msvideo',
  '.mkv': 'video/x-matroska',
  '.wmv': 'video/x-ms-wmv',
  '.3gp': 'video/3gpp',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.png': 'image/png',
  '.webp': 'image/webp',
  '.bmp': 'image/bmp',
  '.tif': 'image/tiff',
  '.tiff': 'image/tiff',
  '.gif': 'image/gif',
};

function registerMediaProtocol() {
  protocol.handle(MEDIA_SCHEME, (request) => {
    try {
      const url = new URL(request.url);
      // searchParams.get already percent-decodes; a second decode would
      // throw on filenames containing '%'.
      const requested = path.normalize(url.searchParams.get('path') || '');
      const lower = requested.toLowerCase();
      // Separator-safe containment (a root must not admit prefix siblings)
      // and our metadata folder is never streamed.
      const underRoot = [...mediaRoots].some((root) => {
        const r = path.normalize(root);
        const rl = r.toLowerCase();
        return requested === r || lower.startsWith(rl.endsWith(path.sep) ? rl : rl + path.sep);
      });
      const allowed =
        underRoot && !lower.includes('.faceframe') && MEDIA_MIME[path.extname(lower)];
      if (!allowed) {
        return new Response('Forbidden', { status: 403 });
      }
      const mime = MEDIA_MIME[path.extname(lower)];
      const stat = fs.statSync(requested);
      const range = request.headers.Range || request.headers.range;
      if (range) {
        const match = /bytes=(\d*)-(\d*)/.exec(range);
        if (match) {
          // RFC 7233: "bytes=-N" is a suffix range — the LAST N bytes.
          let start;
          let end;
          if (!match[1] && match[2]) {
            const suffix = parseInt(match[2], 10);
            start = Math.max(0, stat.size - suffix);
            end = stat.size - 1;
          } else {
            start = match[1] ? parseInt(match[1], 10) : 0;
            end = match[2] ? Math.min(parseInt(match[2], 10), stat.size - 1) : stat.size - 1;
          }
          if (start >= stat.size || start > end) {
            return new Response(null, {
              status: 416,
              headers: { 'Content-Range': `bytes */${stat.size}` },
            });
          }
          return new Response(Readable.toWeb(fs.createReadStream(requested, { start, end })), {
            status: 206,
            headers: {
              'Content-Type': mime,
              'Content-Length': String(end - start + 1),
              'Content-Range': `bytes ${start}-${end}/${stat.size}`,
              'Accept-Ranges': 'bytes',
            },
          });
        }
      }
      return new Response(Readable.toWeb(fs.createReadStream(requested)), {
        status: 200,
        headers: {
          'Content-Type': mime,
          'Content-Length': String(stat.size),
          'Accept-Ranges': 'bytes',
        },
      });
    } catch (e) {
      console.error('media:// request failed:', e);
      return new Response('Not found', { status: 404 });
    }
  });
}

function addMediaRoot(folder) {
  if (folder) {
    mediaRoots.add(path.normalize(folder));
    persistMediaRoots();
  }
}

// ---------------------------------------------------------------------------
// Python backend management
// ---------------------------------------------------------------------------

function backendScriptPath() {
  // The asar archive cannot host a spawnable script; electron-builder
  // copies python-backend into resources via extraResources.
  return app.isPackaged
    ? path.join(process.resourcesPath, 'python-backend', 'main.py')
    : path.join(__dirname, '..', 'python-backend', 'main.py');
}

function candidatePythonPaths() {
  const candidates = [];
  if (process.env.FACEFRAME_PYTHON) {
    candidates.push(process.env.FACEFRAME_PYTHON);
  }
  const venvDirs = [
    path.join(__dirname, '..', 'venv'), // dev layout
  ];
  if (app.isPackaged) {
    venvDirs.push(path.join(process.resourcesPath, 'venv'));
  }
  for (const venvDir of venvDirs) {
    candidates.push(
      path.join(venvDir, 'Scripts', 'python.exe'), // Windows venv
      path.join(venvDir, 'bin', 'python3'), // Linux/macOS venv
      path.join(venvDir, 'bin', 'python')
    );
  }
  if (process.platform === 'win32') {
    candidates.push('python.exe');
  } else {
    candidates.push('python3', 'python');
  }
  return [...new Set(candidates)];
}

function probePython(candidate) {
  // A bare file path that exists wins immediately (venv layouts).
  if (candidate.includes('/') || candidate.includes('\\')) {
    try {
      fs.accessSync(candidate, fs.constants.X_OK);
      return Promise.resolve(candidate);
    } catch {
      return Promise.reject(new Error('not found'));
    }
  }
  // Bare command name (system python): verify it actually runs and has the
  // dependencies. On Windows the Store alias stub exists on PATH but is
  // not a usable interpreter. Time-boxed: a hanging interpreter (antivirus
  // scan, broken install) must not stall startup forever.
  return new Promise((resolve, reject) => {
    const probe = spawn(
      candidate,
      ['-c', 'import sys, cv2, insightface, onnxruntime; print(sys.version_info[:2])'],
      { stdio: 'ignore' }
    );
    const timer = setTimeout(() => {
      probe.kill();
      reject(new Error('timed out'));
    }, 10000);
    probe.on('error', (e) => {
      clearTimeout(timer);
      reject(e);
    });
    probe.on('exit', (code) => {
      clearTimeout(timer);
      if (code === 0) resolve(candidate);
      else reject(new Error(`exit ${code}`));
    });
  });
}

async function findPython() {
  for (const candidate of candidatePythonPaths()) {
    try {
      return await probePython(candidate);
    } catch {
      // try the next candidate
    }
  }
  return null;
}

function killPython() {
  if (!pythonProcess) return;
  try {
    if (process.platform === 'win32') {
      // .kill() would spare ONNX Runtime worker threads; the whole tree
      // has to go.
      spawn('taskkill', ['/pid', String(pythonProcess.pid), '/T', '/F'], {
        stdio: 'ignore',
      });
    } else {
      pythonProcess.kill('SIGTERM');
    }
  } catch {
    // process already gone
  }
  pythonProcess = null;
}

function broadcast(channel, payload) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send(channel, payload);
  }
}

function startPythonBackend() {
  // One supervisor at a time: a live process, a pending backoff timer, or
  // an interpreter probe in flight means startup is already under way.
  if (pythonProcess || restartTimer || backendStarting) return;
  backendStarting = true;

  backendReady = false;
  backendError = null;
  lastBackendStatus = { state: 'starting' };
  broadcast('backend-status', { state: 'starting' });

  findPython()
    .then((pythonPath) => {
      if (!pythonPath) {
        backendStarting = false;
        backendError = 'python-not-found';
        lastBackendStatus = { state: 'unavailable', reason: backendError };
        broadcast('backend-status', lastBackendStatus);
        return;
      }

      const scriptPath = backendScriptPath();
      pythonProcess = spawn(pythonPath, ['-X', 'utf8', scriptPath], {
        cwd: path.dirname(scriptPath),
        stdio: ['pipe', 'pipe', 'pipe'],
        windowsHide: true,
        env: { ...process.env, PYTHONUTF8: '1' },
      });
      backendStarting = false;

      // A backend dying mid-write must not take the app down with an
      // unhandled EPIPE; the exit handler cleans up pending requests.
      pythonProcess.stdin.on('error', () => {});
      pythonProcess.stdout.on('error', () => {});
      pythonProcess.stderr.on('error', () => {});

      // NDJSON over a pipe: chunk boundaries can split a multi-byte UTF-8
      // sequence, so accumulate Buffers and only decode complete lines.
      let stdoutBuffer = Buffer.alloc(0);
      pythonProcess.stdout.on('data', (chunk) => {
        stdoutBuffer = Buffer.concat([stdoutBuffer, chunk]);
        let newlineIndex;
        while ((newlineIndex = stdoutBuffer.indexOf(0x0a)) !== -1) {
          const line = stdoutBuffer.subarray(0, newlineIndex).toString('utf8').trim();
          stdoutBuffer = stdoutBuffer.subarray(newlineIndex + 1);
          if (line) handleBackendLine(line);
        }
      });

      pythonProcess.stderr.on('data', (chunk) => {
        console.error(`[backend] ${chunk.toString().trimEnd()}`);
      });

      pythonProcess.on('exit', (code) => {
        const crashed = backendReady || code !== 0;
        pythonProcess = null;
        backendReady = false;
        for (const { reject, timer } of pendingRequests.values()) {
          clearTimeout(timer);
          reject(new Error('Backend stopped'));
        }
        pendingRequests.clear();
        if (!appQuitting) {
          if (crashed && consecutiveCrashes < 3) {
            consecutiveCrashes += 1;
            lastBackendStatus = { state: 'restarting' };
            broadcast('backend-status', lastBackendStatus);
            const delay = 1500 * consecutiveCrashes;
            restartTimer = setTimeout(() => {
              restartTimer = null;
              startPythonBackend();
            }, delay);
          } else {
            if (crashed) backendError = 'backend-repeated-crash';
            lastBackendStatus = {
              state: 'unavailable',
              reason: backendError || 'backend-exited',
            };
            broadcast('backend-status', lastBackendStatus);
          }
        }
      });

      waitForBackend();
    })
    .catch(() => {
      backendStarting = false;
      backendError = 'python-not-found';
      lastBackendStatus = { state: 'unavailable', reason: backendError };
      broadcast('backend-status', lastBackendStatus);
    });
}

let modelLoadingSeen = false;

function waitForBackend(attempt = 0) {
  // The backend announces model_status:loading before its first-run model
  // download (hundreds of MB): give that generous room instead of killing
  // it at the 30s generic watchdog.
  const maxAttempts = modelLoadingSeen ? 20 * 120 : 30;
  if (attempt > maxAttempts) {
    killPython();
    backendError = 'backend-timeout';
    lastBackendStatus = { state: 'unavailable', reason: backendError };
    broadcast('backend-status', lastBackendStatus);
    return;
  }
  sendToPython({ action: 'ping', id: nextRequestId++ })
    .then(() => {
      backendReady = true;
      consecutiveCrashes = 0;
      lastBackendStatus = { state: 'ready' };
      broadcast('backend-status', lastBackendStatus);
    })
    .catch(() => setTimeout(() => waitForBackend(attempt + 1), 1000));
}

function handleBackendLine(line) {
  let message;
  try {
    message = JSON.parse(line);
  } catch {
    return; // stray non-JSON output
  }

  if (message.id !== undefined && (message.ok !== undefined || message.error)) {
    const pending = pendingRequests.get(message.id);
    if (pending) {
      pendingRequests.delete(message.id);
      clearTimeout(pending.timer);
      if (message.ok) pending.resolve(message.data || {});
      else pending.reject(new Error(message.error || 'Backend error'));
    }
    return;
  }

  if (message.event) {
    const { event, ...payload } = message;
    if (event === 'model_status') {
      modelLoadingSeen = event.state === 'loading' ? true : modelLoadingSeen;
    }
    broadcast('backend-event', { event, ...payload });
  }
}

function sendToPython(message) {
  return new Promise((resolve, reject) => {
    if (!pythonProcess || !pythonProcess.stdin.writable) {
      reject(new Error('Backend is not running'));
      return;
    }
    const timeout = ACTION_TIMEOUTS[message.action] ?? DEFAULT_TIMEOUT;
    const timer = setTimeout(() => {
      pendingRequests.delete(message.id);
      reject(new Error(`Backend did not answer ${message.action} in time`));
    }, timeout);
    pendingRequests.set(message.id, { resolve, reject, timer });
    pythonProcess.stdin.write(JSON.stringify(message) + '\n');
  });
}

function requireBackend() {
  if (!backendReady) {
    const reasonText = {
      'python-not-found':
        'Python was not found. Install Python 3.10+ and create the venv (see README).',
      'backend-repeated-crash':
        'The Python backend crashed repeatedly and has stopped. Use "Try again" on the banner to restart it.',
      'backend-timeout':
        'The Python backend did not become ready in time and was stopped. Use "Try again" to restart it.',
      'backend-exited':
        'The Python backend has exited. Use "Try again" to restart it.',
    };
    throw new Error(
      reasonText[backendError] ||
        'The Python backend is not running. Use "Try again" to restart it.'
    );
  }
}

// ---------------------------------------------------------------------------
// IPC surface
// ---------------------------------------------------------------------------

function registerIpc() {
  const passThrough = (channel, mapArgs) =>
    ipcMain.handle(channel, (_event, ...args) => {
      requireBackend();
      const message = mapArgs(...args);
      message.id = nextRequestId++;
      return sendToPython(message);
    });

  // Generic surface: request('action', params). Kept alongside the named
  // channels so older call sites keep working. NOTE: these calls never
  // grant media:// rights — only the select-folder dialog does.
  ipcMain.handle('backend-request', (_event, action, params) => {
    requireBackend();
    const message = { action, ...(params || {}) };
    message.id = nextRequestId++;
    return sendToPython(message);
  });

  ipcMain.handle('select-folder', async () => {
    const result = await dialog.showOpenDialog(mainWindow, {
      title: 'Choose a photo folder',
      properties: ['openDirectory'],
    });
    if (!result.canceled && result.filePaths.length > 0) {
      addMediaRoot(result.filePaths[0]);
    }
    return result.canceled || result.filePaths.length === 0
      ? null
      : result.filePaths[0];
  });

  passThrough('get-providers', () => ({ action: 'get_providers' }));
  passThrough('scan-directory', (folder, provider) => ({
    action: 'scan',
    path: folder,
    provider,
  }));
  passThrough('cancel-scan', () => ({ action: 'cancel_scan' }));
  passThrough('cluster-faces', (folder) => ({ action: 'cluster', path: folder }));
  passThrough('clear-index', (folder) => ({ action: 'clear_index', path: folder }));
  passThrough('open-library', (folder) => ({ action: 'open_library', path: folder }));

  ipcMain.handle('backend-state', () => lastBackendStatus);

  // The setup screen's retry: a page reload cannot revive the backend,
  // only a fresh spawn can. A deliberate retry also resets the crash
  // budget, so "Try again" gets a full set of spawns with backoff.
  ipcMain.handle('retry-backend', async () => {
    if (backendReady) return { state: 'ready' };
    if (backendError === 'backend-repeated-crash') consecutiveCrashes = 0;
    if (restartTimer) {
      clearTimeout(restartTimer);
      restartTimer = null;
      startPythonBackend();
    } else if (!pythonProcess) {
      startPythonBackend();
    }
    return { state: 'starting' };
  });
}

// ---------------------------------------------------------------------------
// App lifecycle
// ---------------------------------------------------------------------------

let appQuitting = false;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 840,
    minWidth: 900,
    minHeight: 600,
    backgroundColor: '#101418',
    title: 'FaceFrame',
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      spellcheck: false,
    },
  });

  mainWindow.webContents.on('did-fail-load', (_e, code, desc, url) => {
    console.error(`[window] failed to load ${url}: ${code} ${desc}`);
  });
  let rendererGoneCount = 0;
  mainWindow.webContents.on('render-process-gone', (_e, details) => {
    console.error(`[window] renderer gone: ${details.reason}`);
    // A crashed renderer leaves a dead frozen surface; reload it instead of
    // making the user kill the app. The counter keeps a crash loop from
    // spinning forever.
    if (rendererGoneCount < 5) {
      rendererGoneCount += 1;
      setTimeout(() => {
        if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.reload();
      }, 500);
    }
  });
  mainWindow.webContents.on('console-message', (_e, level, message, line, source) => {
    if (level >= 2) console.error(`[renderer] ${message} (${source}:${line})`);
  });

  if (isDev) {
    mainWindow.loadURL(process.env.ELECTRON_START_URL);
    if (process.env.FACEFRAME_DEVTOOLS === '1') {
      mainWindow.webContents.openDevTools();
    }
  } else {
    mainWindow.loadURL(`${APP_ORIGIN}/index.html`);
  }

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });

  app.whenReady().then(() => {
    loadMediaRoots();
    if (!isDev) registerAppProtocol();
    registerMediaProtocol();
    registerIpc();
    createWindow();
    startPythonBackend();
  });

  app.on('window-all-closed', () => {
    // Platform convention: on macOS apps stay alive in the dock until the
    // user quits, and 'activate' can re-create the window.
    if (process.platform !== 'darwin') app.quit();
  });

  app.on('before-quit', () => {
    appQuitting = true;
    killPython();
  });

  app.on('activate', () => {
    if (mainWindow === null) createWindow();
  });
}
