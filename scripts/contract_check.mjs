// One-off double-check: extract backend action names, renderer api calls,
// mock implementations, and electron->backend requests, then report drift.
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';

const read = (p) => readFileSync(p, 'utf8');
const mainPy = read('python-backend/main.py');
const mainCjs = read('electron/main.cjs');
const mockTs = read('src/lib/mock.ts');

// collect all src .ts/.tsx files
const files = [];
(function walk(dir) {
  for (const f of readdirSync(dir)) {
    const p = join(dir, f);
    if (statSync(p).isDirectory()) walk(p);
    else if (/\.(tsx?|mjs)$/.test(f) && !/\.test\./.test(f)) files.push(p);
  }
})('src');

const backend = new Set();
for (const m of mainPy.matchAll(/@action\(["']([\w.-]+)["']\)/g)) backend.add(m[1]);

const renderer = new Set();
for (const f of files) {
  const t = read(f);
  for (const m of t.matchAll(/call\(["']([\w.-]+)["']/g)) renderer.add(m[1]);
  for (const m of t.matchAll(/request\(["']([\w.-]+)["']/g)) renderer.add(m[1]);
}

const mock = new Set();
for (const m of mockTs.matchAll(/case\s+["']([\w.-]+)["']/g)) mock.add(m[1]);

const electron = new Set();
for (const m of mainCjs.matchAll(/action\s*:\s*["']([\w.-]+)["']/g)) electron.add(m[1]);
for (const m of mainCjs.matchAll(/sendCommand\(\s*["']([\w.-]+)["']/g)) electron.add(m[1]);

const sorted = (s) => [...s].sort();
console.log(`backend @action (${backend.size}):\n  ${sorted(backend).join(' ')}`);
console.log(`\nrenderer call/request (${renderer.size}):\n  ${sorted(renderer).join(' ')}`);
console.log(`\nmock cases (${mock.size}):\n  ${sorted(mock).join(' ')}`);
console.log(`\nelectron->backend (${electron.size}):\n  ${sorted(electron).join(' ')}`);

const missingInBackend = sorted(renderer).filter((a) => !backend.has(a));
console.log(`\n!! renderer calls MISSING in backend:\n  ${missingInBackend.join(' ') || '(none)'}`);
const missingInMock = sorted(renderer).filter((a) => !mock.has(a));
console.log(`\n!! renderer calls with NO mock case (browser ?mock=1 breaks):\n  ${missingInMock.join(' ') || '(none)'}`);
const deadBackend = sorted(backend).filter((a) => !renderer.has(a) && !electron.has(a));
console.log(`\nbackend actions never called by renderer/electron (may be e2e-only):\n  ${deadBackend.join(' ') || '(none)'}`);
