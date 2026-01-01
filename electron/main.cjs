const { app, BrowserWindow, ipcMain, dialog, protocol, net } = require('electron');
const path = require('path');
const { spawn } = require('child_process');

let mainWindow;
let pythonProcess;

function createWindow() {
    mainWindow = new BrowserWindow({
        width: 1200,
        height: 800,
        webPreferences: {
            preload: path.join(__dirname, 'preload.cjs'),
            nodeIntegration: false,
            contextIsolation: true,
        },
    });

    // In dev, load Vite's dev server. In prod, load index.html.
    const startUrl = process.env.ELECTRON_START_URL || `file://${path.join(__dirname, '../dist/index.html')}`;
    mainWindow.loadURL(startUrl);

    // Open DevTools in dev
    if (process.env.ELECTRON_START_URL) {
        mainWindow.webContents.openDevTools();
    }

    mainWindow.on('closed', () => (mainWindow = null));
}

function startPythonBackend() {
    // Path to python executable (ideally bundled or found via env)
    // For Dev: assume running from root, use venv
    const pythonPath = path.join(__dirname, '../venv/Scripts/python.exe'); // Windows specific
    const scriptPath = path.join(__dirname, '../python-backend/main.py');

    console.log("Starting Python Backend:", pythonPath, scriptPath);

    pythonProcess = spawn(pythonPath, [scriptPath]);

    pythonProcess.on('error', (err) => {
        console.error("Failed to spawn Python process:", err);
    });

    pythonProcess.stdout.on('data', (data) => {
        const lines = data.toString().split('\n');
        lines.forEach(line => {
            if (line.trim()) {
                console.log(`[Python]: ${line}`);
                try {
                    const json = JSON.parse(line);
                    if (mainWindow) {
                        mainWindow.webContents.send('backend-message', json);
                    }
                } catch (e) {
                    // Not JSON
                }
            }
        });
    });

    pythonProcess.stderr.on('data', (data) => {
        console.error(`[Python Error]: ${data}`);
    });

    pythonProcess.on('close', (code) => {
        console.log(`Python process exited with code ${code}`);
    });
}

const sendToPython = (data) => {
    if (pythonProcess && pythonProcess.stdin) {
        pythonProcess.stdin.write(JSON.stringify(data) + '\n');
    }
}

app.on('ready', () => {
    // Register 'safe-file' protocol to read local files
    protocol.handle('safe-file', (request) => {
        let url = request.url.replace('safe-file://', '');
        // Decode URL (spaces etc)
        let filePath = decodeURIComponent(url);
        // Normalize Windows backslashes to forward slashes for file:// URL
        filePath = filePath.replace(/\\/g, '/');
        // Ensure path starts correctly for Windows drives (e.g., D:/ -> /D:/)
        if (/^[A-Za-z]:/.test(filePath)) {
            filePath = '/' + filePath;
        }
        return net.fetch('file://' + filePath);
    });

    createWindow();
    startPythonBackend();

    ipcMain.handle('select-folder', async () => {
        const result = await dialog.showOpenDialog(mainWindow, {
            properties: ['openDirectory']
        });
        if (!result.canceled && result.filePaths.length > 0) {
            return result.filePaths[0];
        }
        return null;
    });

    ipcMain.handle('scan-directory', async (event, folderPath, provider) => {
        console.log("Sending scan command for:", folderPath, "with provider:", provider);
        sendToPython({ action: 'SCAN', path: folderPath, provider: provider });
        return { status: 'started' };
    });

    ipcMain.handle('cancel-scan', async () => {
        sendToPython({ action: 'CANCEL_SCAN' });
        return true;
    });

    ipcMain.handle('get-providers', async () => {
        sendToPython({ action: 'GET_PROVIDERS' });
        // We can't easily wait for the reply here since it's async over stdout. 
        // Frontend must wait for 'providers' message.
        return true;
    });

    ipcMain.handle('cluster-faces', async (event, folderPath) => {
        sendToPython({ action: 'CLUSTER', path: folderPath });
        return { status: 'started' };
    });

    ipcMain.handle('get-persons', async (event, folderPath) => {
        sendToPython({ action: 'GET_PERSONS', path: folderPath });
        return true;
    });

    ipcMain.handle('get-unclustered-faces', async (event, folderPath) => {
        sendToPython({ action: 'GET_UNCLUSTERED', path: folderPath });
        return true;
    });

    ipcMain.handle('clear-index', async (event, folderPath) => {
        // Confirmation dialog could go here, but let's trust the frontend UI to ask.
        sendToPython({ action: 'CLEAR_INDEX', path: folderPath });
        return true;
    });

    ipcMain.handle('rename-person', async (event, folderPath, personId, newName) => {
        sendToPython({ action: 'RENAME_PERSON', path: folderPath, person_id: personId, new_name: newName });
        return true;
    });

    ipcMain.handle('merge-persons', async (event, folderPath, keepId, mergeId) => {
        sendToPython({ action: 'MERGE_PERSONS', path: folderPath, keep_id: keepId, merge_id: mergeId });
        return true;
    });
});

app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') {
        if (pythonProcess) pythonProcess.kill();
        app.quit();
    }
});

app.on('activate', () => {
    if (mainWindow === null) createWindow();
});
