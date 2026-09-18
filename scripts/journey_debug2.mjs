/* Debug the editor preview: hook requests, open editor, report. */
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
await send('Page.bringToFront').catch(() => {});

await ev(`(function () {
  window.__log = [];
  var orig = window.faceframe.request.bind(window.faceframe);
  window.faceframe.request = function (action, params) {
    var p = Promise.resolve(orig(action, params));
    p.then(function (res) {
      window.__log.push({ action: action, sent: JSON.stringify(params).slice(0, 140), failed: Boolean(res.error), err: res.error || null, len: res.data_url ? res.data_url.length : 0 });
    }, function (e) {
      window.__log.push({ action: action, sent: JSON.stringify(params).slice(0, 140), rejected: String(e).slice(0, 140) });
    });
    return p;
  };
  return 'ok';
})()`);

// open a photo in the viewer, then the editor
await ev(`(async function () {
  location.hash = '#/photos';
  await new Promise(function (r) { setTimeout(r, 1200); });
  var t = [].filter.call(document.querySelectorAll('.thumb'), function (x) { return x.title.indexOf('.jpg') !== -1; })[1];
  t.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  await new Promise(function (r) { setTimeout(r, 1200); });
  var b = [].filter.call(document.querySelectorAll('.viewer-action'), function (x) { return x.textContent.indexOf('Edit') !== -1; })[0];
  b.click();
  await new Promise(function (r) { setTimeout(r, 3500); });
  return 'opened';
})()`);

console.log(JSON.stringify(await ev('window.__log'), null, 1));
console.log('editor state:', JSON.stringify(await ev(`({
  editor: Boolean(document.querySelector('.editor')),
  img: Boolean(document.querySelector('.editor-preview img')),
  canvasText: (document.querySelector('.editor-canvas') || {}).textContent || null,
})`)));
ws.close();
