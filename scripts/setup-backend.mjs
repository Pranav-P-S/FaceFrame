// One-command backend setup: `npm run setup` creates ./venv (if missing) and
// installs the Python dependencies into it, so the README's multi-step,
// per-platform venv dance collapses to a single cross-platform command.
// Idempotent: safe to rerun; pip is a fast no-op when requirements are met.
import { spawnSync } from 'node:child_process';
import { existsSync, rmSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const requirements = path.join(root, 'python-backend', 'requirements.txt');
const isWin = process.platform === 'win32';
const venvPython = isWin
  ? path.join(root, 'venv', 'Scripts', 'python.exe')
  : path.join(root, 'venv', 'bin', 'python3');

function run(cmd, args) {
  // No shell: every candidate here is a real executable (python.exe, py.exe,
  // the venv interpreter), so args never need shell-style escaping.
  const res = spawnSync(cmd, args, { stdio: 'inherit', cwd: root });
  return { ok: !res.error && res.status === 0 };
}

function systemPython() {
  const candidates = isWin ? ['py', 'python'] : ['python3', 'python'];
  for (const cmd of candidates) {
    const args = cmd === 'py' ? ['-3', '--version'] : ['--version'];
    const res = spawnSync(cmd, args, { encoding: 'utf8' });
    const out = `${res.stdout || ''}${res.stderr || ''}`;
    const match = out.match(/Python (\d+)\.(\d+)/);
    if (res.status === 0 && match) {
      const version = `${match[1]}.${match[2]}`;
      const minor = Number(match[2]);
      if (Number(match[1]) !== 3 || minor < 10 || minor > 13) {
        console.warn(
          `Found Python ${version}. Prebuilt insightface wheels cover 3.10-3.12; ` +
            '3.13 may need a C++ toolchain to compile it.'
        );
      }
      return { cmd, flag: cmd === 'py' ? ['-3'] : [] };
    }
  }
  return null;
}

if (existsSync(venvPython)) {
  console.log('venv already exists — refreshing dependencies…');
} else {
  const found = systemPython();
  if (!found) {
    console.error(
      'No Python found. Install Python 3.10-3.12 from https://python.org\n' +
        '(on Windows check "Add python.exe to PATH") and run `npm run setup` again.'
    );
    process.exit(1);
  }
  console.log(`Creating venv with ${found.cmd}…`);
  const created = run(found.cmd, [...found.flag, '-m', 'venv', 'venv']);
  if (!created.ok || !existsSync(venvPython)) {
    console.error('Could not create the venv. Remove ./venv and retry, or see the README.');
    // A half-created venv breaks every later run; start over next time.
    rmSync(path.join(root, 'venv'), { recursive: true, force: true });
    process.exit(1);
  }
}

console.log('Installing backend dependencies (one-time, ~hundreds of MB)…');
const installed = run(venvPython, ['-m', 'pip', 'install', '-r', requirements]);
if (!installed.ok) {
  console.error('pip install failed — check your network and run `npm run setup` again.');
  process.exit(1);
}

console.log('\nBackend ready. Start the app with:\n  npm run electron:dev');
