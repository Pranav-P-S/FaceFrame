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
      if (!filePath.startsWith(distDir)) {
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

const mediaRoots = new Set();

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
          const start = match[1] ? parseInt(match[1], 10) : 0;
          const end = match[2] ? Math.min(parseInt(match[2], 10), stat.size - 1) : stat.size - 1;
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
  if (folder) mediaRoots.add(path.normalize(folder));
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
      pythonProcess = spawn(pythonPath, [scriptPath], {
        cwd: path.dirname(scriptPath),
        stdio: ['pipe', 'pipe', 'pipe'],
        windowsHide: true,
      });
      backendStarting = false;

      // A backend dying mid-write must not take the app down with an
      // unhandled EPIPE; the exit handler cleans up pending requests.
      pythonProcess.stdin.on('error', () => {});
      pythonProcess.stdout.on('error', () => {});
      pythonProcess.stderr.on('error', () => {});

      let stdoutBuffer = '';
      pythonProcess.stdout.on('data', (chunk) => {
        stdoutBuffer += chunk.toString();
        let newlineIndex;
        while ((newlineIndex = stdoutBuffer.indexOf('\n')) !== -1) {
          const line = stdoutBuffer.slice(0, newlineIndex).trim();
          stdoutBuffer = stdoutBuffer.slice(newlineIndex + 1);
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

function waitForBackend(attempt = 0) {
  if (attempt > 30) {
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
    throw new Error(
      backendError === 'python-not-found'
        ? 'Python was not found. Install Python 3.10+ and create the venv (see README).'
        : 'The Python backend is still starting. Try again in a moment.'
    );
  }
}

// ---------------------------------------------------------------------------
// IPC surface
// ---------------------------------------------------------------------------

function registerIpc() {
  ipcMain.handle('select-folder', async () => {
    const result = await dialog.showOpenDialog(mainWindow, {
      title: 'Choose a photo folder',
      properties: ['openDirectory'],
    });
    return result.canceled || result.filePaths.length === 0
      ? null
      : result.filePaths[0];
  });

  // open_library/scan name a library folder; once the backend confirms the
  // folder is real, the main process registers it as a media root. Roots are
  // therefore only ever backend-verified paths — never raw renderer input.
  const LIBRARY_ACTIONS = new Set(['open_library', 'scan']);

  const passThrough = (channel, mapArgs) =>
    ipcMain.handle(channel, (_event, ...args) => {
      requireBackend();
      const message = mapArgs(...args);
      message.id = nextRequestId++;
      const folder =
        LIBRARY_ACTIONS.has(message.action) && typeof message.path === 'string'
          ? message.path
          : null;
      return sendToPython(message).then((result) => {
        if (folder) addMediaRoot(folder);
        return result;
      });
    });

  // Generic surface: request('action', params). Kept alongside the named
  // channels so older call sites keep working.
  ipcMain.handle('backend-request', (_event, action, params) => {
    requireBackend();
    const message = { action, ...(params || {}) };
    message.id = nextRequestId++;
    const folder =
      LIBRARY_ACTIONS.has(action) && typeof message.path === 'string'
        ? message.path
        : null;
    return sendToPython(message).then((result) => {
      if (folder) addMediaRoot(folder);
      return result;
    });
  });

  passThrough('get-providers', () => ({ action: 'get_providers' }));
  passThrough('scan-directory', (folder, provider) => ({
    action: 'scan',
    path: folder,
    provider,
  }));
  passThrough('cancel-scan', () => ({ action: 'cancel_scan' }));
  passThrough('cluster-faces', (folder) => ({ action: 'cluster', path: folder }));
  passThrough('get-persons', (folder) => ({ action: 'get_persons', path: folder }));
  passThrough('get-unclustered-faces', (folder) => ({
    action: 'get_unclustered',
    path: folder,
  }));
  passThrough('get-photos-by-person', (folder, personId) => ({
    action: 'get_photos_by_person',
    path: folder,
    person_id: personId,
  }));
  passThrough('rename-person', (folder, personId, newName) => ({
    action: 'rename_person',
    path: folder,
    person_id: personId,
    new_name: newName,
  }));
  passThrough('merge-persons', (folder, keepId, mergeId) => ({
    action: 'merge_persons',
    path: folder,
    keep_id: keepId,
    merge_id: mergeId,
  }));
  passThrough('clear-index', (folder) => ({ action: 'clear_index', path: folder }));
  passThrough('open-library', (folder) => ({ action: 'open_library', path: folder }));

  ipcMain.handle('backend-state', () => lastBackendStatus);

  // The setup screen's retry: a page reload cannot revive the backend,
  // only a fresh spawn can.
  ipcMain.handle('retry-backend', async () => {
    if (backendReady) return { state: 'ready' };
    if (restartTimer) {
      clearTimeout(restartTimer);
      restartTimer = null;
      startPythonBackend();
    } else if (!pythonProcess) {
      startPythonBackend();
    }
    return { state: 'starting' };
  });

  // Images travel through the Python backend's preview pipeline, which
  // downscales into a per-library cache: bounded memory, and formats the
  // renderer cannot display (TIFF) become viewable.
  ipcMain.handle('read-image-data-url', async (_event, filePath, maxDim = 640) => {
    if (!backendReady) return null;
    try {
      return await sendToPython({
        action: 'get_image_preview',
        id: nextRequestId++,
        file_path: filePath,
        max_dim: maxDim,
      }).then((data) => data.data_url);
    } catch (e) {
      console.error('read-image-data-url failed:', e.message);
      return null;
    }
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
  mainWindow.webContents.on('render-process-gone', (_e, details) => {
    console.error(`[window] renderer gone: ${details.reason}`);
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
    if (!isDev) registerAppProtocol();
    registerMediaProtocol();
    registerIpc();
    createWindow();
    startPythonBackend();
  });

  app.on('window-all-closed', () => {
    app.quit();
  });

  app.on('before-quit', () => {
    appQuitting = true;
    killPython();
  });

  app.on('activate', () => {
    if (mainWindow === null) createWindow();
  });
}
