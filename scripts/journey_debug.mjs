/* Debug: hook the app's request channel, reload, and report what FeedPage
 * actually sends/receives. One-shot CDP script. */
import { writeFileSync } from 'node:fs';

const PORT = '9224';
const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
const page = list.find((t) => t.type === 'page' && /localhost:1420/.test(t.url));
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error('ws')); });
let id = 0;
const pending = new Map();
ws.onmessage = (m) => {
  const msg = JSON.parse(m.data);
  if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg.result); pending.delete(msg.id); }
};
const send = (method, params = {}) => new Promise((res) => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });
const ev = async (expression) => {
  const r = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
  return r.result?.value;
};

await send('Page.enable');
const hook = `(function () {
  window.__log = [];
  const orig = window.faceframe.request.bind(window.faceframe);
  window.faceframe.request = function (action, params) {
    const p = Promise.resolve(orig(action, params));
    p.then(function (res) {
      if (['get_feed', 'get_image_preview', 'get_item'].indexOf(action) !== -1) {
        window.__log.push({
          action: action,
          sent: params && (params.file_path || params.path),
          failed: Boolean(res.error),
          count: res.groups ? res.groups.reduce(function (n, g) { return n + g.items.length; }, 0) : (res.data_url ? res.data_url.length : (res.item ? 1 : 0)),
        });
      }
    }, function (e) {
      window.__log.push({ action: action, rejected: String(e).slice(0, 90) });
    });
    return p;
  };
  return 'hooked';
})()`;
console.log('hook:', await ev(hook));
await ev("location.reload(); 'reloading'");
await new Promise((r) => setTimeout(r, 9000));
// re-hook after reload and read the log captured by the pre-reload hook is
// impossible (page reset) — instead read current DOM + do a fresh capture of
// what a feed fetch looks like from the app's own channel.
const log = await ev("window.__log || 'gone'");
console.log('post-reload log (expected gone):', log);
await ev(hook);
await ev("window.__h ? window.__h.req('get_feed', { view: 'days' }).then(function () { return 'fed'; }) : 'no helper'");
await new Promise((r) => setTimeout(r, 1500));
console.log('log after app-style feed fetch:', await ev('window.__log'));
console.log('thumbs:', await ev("document.querySelectorAll('.thumb').length + ' / headers ' + document.querySelectorAll('.grid-header').length"));
ws.close();
