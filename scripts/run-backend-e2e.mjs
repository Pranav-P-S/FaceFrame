// Runs the Python backend end-to-end test with the project's venv
// interpreter, so `npm test` works the same on Windows, Linux and macOS.
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

const venvCandidates =
  process.platform === 'win32'
    ? [path.join(root, 'venv', 'Scripts', 'python.exe')]
    : [path.join(root, 'venv', 'bin', 'python3'), path.join(root, 'venv', 'bin', 'python')];

const python = venvCandidates.find((p) => existsSync(p));
if (!python) {
  console.error('No venv found. Create one first:\n');
  console.error('  python -m venv venv');
  console.error(
    process.platform === 'win32'
      ? '  venv\\Scripts\\pip install -r python-backend\\requirements.txt'
      : '  venv/bin/pip install -r python-backend/requirements.txt'
  );
  process.exit(1);
}

const child = spawn(python, [path.join(root, 'scripts', 'e2e_backend_test.py')], {
  stdio: 'inherit',
  cwd: root,
});
child.on('exit', (code) => process.exit(code ?? 1));
