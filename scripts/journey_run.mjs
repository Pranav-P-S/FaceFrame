/* Journey test driver: drives the real FaceFrame app over CDP.
 *
 * Usage: node scripts/journey_run.mjs <command> [args]
 * Commands: status | setup [libPath] | j1..j10 | eval "<expr>" | shot <name>
 *
 * Each command opens its own CDP connection to the page target, prints a
 * JSON result to stdout, and closes. Screenshots land in review/journey/.
 */

import { writeFileSync, mkdirSync } from 'node:fs';
import path from 'node:path';

const PORT = 9224;
const OUT_DIR = path.resolve('review/journey');
mkdirSync(OUT_DIR, { recursive: true });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function withPage(fn) {
  const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
  const page = list.find((t) => t.type === 'page' && /localhost:1420/.test(t.url));
  if (!page) {
    throw new Error('page target not found: ' + JSON.stringify(list.map((t) => ({ type: t.type, url: t.url }))));
  }
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error('ws error')); });
  let id = 0;
  const pending = new Map();
  ws.onmessage = (m) => {
    const msg = JSON.parse(m.data);
    if (msg.id && pending.has(msg.id)) {
      const { res, rej } = pending.get(msg.id);
      pending.delete(msg.id);
      if (msg.error) rej(new Error(msg.error.message));
      else res(msg.result);
    }
  };
  const send = (method, params = {}) =>
    new Promise((res, rej) => {
      const i = ++id;
      pending.set(i, { res, rej });
      ws.send(JSON.stringify({ id: i, method, params }));
    });
  // The Electron window can be occluded/minimized -> the renderer stops
  // producing frames, which suppresses scroll events and IntersectionObserver.
  // Restore + foreground the OS window so the app behaves like a foreground app.
  await send('Page.bringToFront').catch(() => undefined);
  const ev = async (expression) => {
    const r = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) {
      throw new Error(
        'page eval failed: ' + (r.exceptionDetails.exception?.description || r.exceptionDetails.text) +
        '\n--- expr: ' + expression.slice(0, 300)
      );
    }
    return r.result ? r.result.value : undefined;
  };
  // Always reinstall the page-side toolkit so every invocation uses the
  // current helper code (window.__h persists across connections).
  await install(ev);
  const shot = async (name) => {
    const { data } = await send('Page.captureScreenshot', { format: 'png' });
    const file = path.join(OUT_DIR, name);
    writeFileSync(file, Buffer.from(data, 'base64'));
    return file;
  };
  try {
    return await fn({ send, ev, shot });
  } finally {
    ws.close();
  }
}

/* Page-side helper toolkit, installed onto window (idempotent). */
const INSTALL = `(() => {
  window.__h = {
    sleep: (ms) => new Promise((r) => setTimeout(r, ms)),
    q: (s) => document.querySelector(s),
    qa: (s) => [...document.querySelectorAll(s)],
    lib: () => localStorage.getItem('faceframe.folder'),
    req: (action, params) => {
      // Mirror the app's api.ts: attach the library path unless the action
      // is file-scoped, and absolutize the relative paths that get_feed and
      // search return.
      const p = { ...(params || {}) };
      const lib = localStorage.getItem('faceframe.folder');
      if (p.path === undefined && !['get_item', 'get_image_preview', 'magic_eraser'].includes(action)) {
        if (lib) p.path = lib;
      }
      const absolutize = (res) => {
        if (!lib) return res;
        const base = lib.replace(/[\\/]+$/, '');
        const fix = (it) => {
          if (it && typeof it.path === 'string' && it.path && it.path.indexOf(':') !== 1 && it.path.indexOf('/') !== 0) {
            it.path = base + '/' + it.path;
          }
        };
        (res.groups || []).forEach((g) => (g.items || []).forEach(fix));
        (res.items || []).forEach(fix);
        return res;
      };
      return window.faceframe.request(action, p).then((res) => {
        if (action === 'get_feed' || action === 'search') return absolutize(res);
        if (action === 'open_library' && res && !res.library) return { library: res };
        return res;
      });
    },
    ready: async () => {
      for (let i = 0; i < 120; i++) {
        if (window.faceframe && document.querySelector('.app')) {
          try {
            const st = await window.faceframe.backendState();
            if (st.state === 'ready') return true;
          } catch (e) {}
        }
        await new Promise((r) => setTimeout(r, 500));
      }
      return false;
    },
    btn: (text, root) => {
      const scope = root || document;
      return [...scope.querySelectorAll('button')].find((b) => b.textContent.trim().includes(text));
    },
    waitFor: async (test, timeout, iv) => {
      for (let i = 0; i < Math.ceil((timeout || 10000) / (iv || 250)); i++) {
        const v = test();
        if (v) return v;
        await new Promise((r) => setTimeout(r, iv || 250));
      }
      return null;
    },
    setNative: (el, value) => {
      const proto = el.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, value);
      el.dispatchEvent(new Event('input', { bubbles: true }));
    },
    thumbByTitle: (match) => [...document.querySelectorAll('.thumb')].find((x) => x.title && x.title.includes(match)),
    clickThumb: async (match) => {
      const grid = document.querySelector('.grid-scroll');
      const tokens = match.split('|');
      const find = () => [...document.querySelectorAll('.thumb')].find((x) => x.title && tokens.some((tk) => x.title.includes(tk)));
      let t = find();
      if (!t && grid) {
        // Virtualized: walk the scroll range until the tile renders. Re-read
        // scrollHeight each step — the feed may still be filling in.
        for (let pass = 0; pass < 3 && !t; pass++) {
          for (let y = 0; y <= grid.scrollHeight + 400; y += 350) {
            grid.scrollTop = y;
            await new Promise((r) => setTimeout(r, 180));
            t = find();
            if (t) break;
          }
          if (!t) await new Promise((r) => setTimeout(r, 600));
        }
        if (!t) grid.scrollTop = 0;
      }
      if (!t) throw new Error('thumb not found: ' + match);
      t.scrollIntoView({ block: 'center' });
      await new Promise((r) => setTimeout(r, 350));
      t.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
    },
    clickThumbShift: async (match) => {
      const grid = document.querySelector('.grid-scroll');
      const tokens = match.split('|');
      const find = () => [...document.querySelectorAll('.thumb')].find((x) => x.title && tokens.some((tk) => x.title.includes(tk)));
      let t = find();
      if (!t && grid) {
        for (let pass = 0; pass < 3 && !t; pass++) {
          for (let y = 0; y <= grid.scrollHeight + 400; y += 350) {
            grid.scrollTop = y;
            await new Promise((r) => setTimeout(r, 180));
            t = find();
            if (t) break;
          }
          if (!t) await new Promise((r) => setTimeout(r, 600));
        }
      }
      if (!t) throw new Error('thumb not found (shift): ' + match);
      t.scrollIntoView({ block: 'center' });
      await new Promise((r) => setTimeout(r, 350));
      t.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, shiftKey: true }));
    },
    waitThumbs: async () => {
      for (let i = 0; i < 60; i++) {
        if (document.querySelector('.thumb')) return true;
        await new Promise((r) => setTimeout(r, 500));
      }
      return false;
    },
    // Walk the virtualized grid and collect everything rendered along the way.
    collectGrid: async () => {
      const grid = document.querySelector('.grid-scroll');
      if (!grid) return { error: 'no .grid-scroll' };
      const headers = new Set(); const titles = new Set();
      const withDuration = new Set(); const withPano = new Set(); const withPlay = new Set();
      let screenshotTiles = 0;
      for (let y = 0; y <= grid.scrollHeight + 400; y += 450) {
        grid.scrollTop = y;
        await new Promise((r) => setTimeout(r, 220));
        document.querySelectorAll('.grid-header span').forEach((e) => headers.add(e.textContent));
        document.querySelectorAll('.thumb').forEach((t) => {
          titles.add(t.title);
          if (t.querySelector('.thumb-duration')) withDuration.add(t.title);
          if (t.querySelector('.thumb-badge')) withPano.add(t.title);
          if (t.querySelector('.thumb-play')) withPlay.add(t.title);
          if (/Screenshot/i.test(t.title || '')) screenshotTiles++;
        });
      }
      grid.scrollTop = 0;
      await new Promise((r) => setTimeout(r, 300));
      return {
        headers: [...headers], thumbCount: titles.size,
        durationTitles: [...withDuration], panoTitles: [...withPano], playTitles: [...withPlay],
        screenshotTiles,
        titles: [...titles],
      };
    },
    themeBtn: () => [...document.querySelectorAll('.topbar .icon-btn')].find((b) => (b.title || '').includes('theme')),
  };
  return true;
})()`;

const READY = `(async () => {
  for (let i = 0; i < 120; i++) {
    if (window.__h && window.faceframe && document.querySelector('.app')) {
      try {
        const st = await window.faceframe.backendState();
        if (st.state === 'ready') return true;
      } catch (e) {}
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  return false;
})()`;

async function install(ev) {
  await ev(INSTALL);
  const ok = await ev(READY);
  if (!ok) throw new Error('app never became ready (faceframe api / backend state)');
}

async function reloadAndInstall(send, ev) {
  await send('Page.enable');
  await send('Page.reload').catch(() => undefined);
  await sleep(2000);
  await install(ev);
}

/* ---------------------------------------------------------------- cmds */

const commands = {
  async status({ ev }) {
    await install(ev);
    const state = await ev('window.faceframe.backendState()');
    const lib = await ev('window.__h.lib()');
    const route = await ev('location.hash');
    const thumbs = await ev("document.querySelectorAll('.thumb').length");
    return { state, lib, route, thumbsOnScreen: thumbs };
  },

  async setup({ ev, send }) {
    await install(ev);
    const libPath = process.argv[3] || 'C:/Projects/FaceFrame/test-data/journey-library';
    await ev(`localStorage.setItem('faceframe.folder', ${JSON.stringify(libPath)})`);
    await reloadAndInstall(send, ev);
    await ev("location.hash = '#/settings'");
    await sleep(600);
    const label = await ev(`(() => {
      const b = window.__h.btn('Rescan folder') || window.__h.btn('Choose folder');
      if (!b) return null;
      b.click();
      return b.textContent.trim();
    })()`);
    if (!label) throw new Error('Rescan/Choose folder button not found on settings page');
    const deadline = Date.now() + 300000;
    let info = null;
    while (Date.now() < deadline) {
      await sleep(5000);
      info = await ev(`(async () => {
        const res = await window.faceframe.request('open_library', { path: window.__h.lib() });
        return { items: res.library && res.library.items, scanbar: !!document.querySelector('.scanbar') };
      })()`);
      console.error(`[scan] items=${info.items} scanbar=${info.scanbar}`);
      if (info.items > 0 && !info.scanbar) break;
    }
    const library = await ev(`(async () => (await window.faceframe.request('open_library', { path: window.__h.lib() })).library)()`);
    return { clicked: label, library };
  },

  async j1({ ev, shot, send }) {
    await ev(`localStorage.setItem('faceframe.density', '1')`);
    await reloadAndInstall(send, ev);
    await ev("location.hash = '#/photos'");
    await ev('window.__h.waitThumbs()');
    const data = await ev('window.__h.collectGrid()');
    const feed = await ev(`(async () => (await window.__h.req('get_feed', { view: 'days' })).groups.map((g) => ({ key: g.key, n: g.items.length })))()`);
    const feedTotal = feed.reduce((a, g) => a + g.n, 0);
    let lib = null;
    for (let i = 0; i < 3 && !lib; i++) {
      lib = await ev(`(async () => (await window.__h.req('open_library', { path: window.__h.lib() })).library)()`).catch(() => null);
    }
    const videoTitles = data.titles.filter((t) => /\.(avi|mp4|mov)$/i.test(t));
    const file = await shot('01_first_contact.png');
    return {
      checks: {
        headerCount: data.headers.length,
        headers: data.headers,
        thumbCount: data.thumbCount,
        feedGroups: feed,
        feedTotal,
        indexItems: lib ? lib.items : null,
        durationBadges: data.durationTitles,
        videoTitles,
        panoBadges: data.panoTitles,
        screenshotTiles: data.screenshotTiles,
        motionPlayBadges: data.playTitles,
      },
      files: [file],
    };
  },

  async j2({ ev, shot }) {
    await ev("location.hash = '#/photos'");
    await ev('window.__h.waitThumbs()');
    await ev(`window.__h.clickThumb('beach2.jpg')`);
    const viewerOpen = await ev(`(async () => {
      for (let i = 0; i < 20; i++) {
        if (document.querySelector('.viewer') && document.querySelector('.viewer-media')) return true;
        await new Promise((r) => setTimeout(r, 250));
      }
      return false;
    })()`);
    const fileNameBefore = await ev("document.querySelector('.viewer-filename') && document.querySelector('.viewer-filename').textContent");
    await ev(`window.__h.btn('Info', document.querySelector('.viewer-actions')).click()`);
    await ev(`(async () => {
      for (let i = 0; i < 20; i++) {
        if (document.querySelector('.info-panel')) return true;
        await new Promise((r) => setTimeout(r, 250));
      }
      return false;
    })()`);
    const infoRows = await ev("[...document.querySelectorAll('.info-panel .info-row')].map((e) => e.textContent.trim())");
    await ev(`(async () => {
      const ta = document.querySelector('.info-edit textarea');
      window.__h.setNative(ta, 'Sunset over the Atlantic');
      ta.focus();
      ta.blur();
    })()`);
    await sleep(900);
    const captionStored = await ev(`(async () => {
      const feed = await window.__h.req('get_feed', { view: 'days' });
      const item = feed.groups.flatMap((g) => g.items).find((i) => i.path.includes('beach2.jpg'));
      return item && item.caption;
    })()`);
    await ev(`window.__h.btn('Info', document.querySelector('.viewer-actions')).click()`);
    const infoGone = await ev("!document.querySelector('.info-panel')");
    await ev(`window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight' }))`);
    await sleep(700);
    const fileNameAfter = await ev("document.querySelector('.viewer-filename') && document.querySelector('.viewer-filename').textContent");
    const shotFile = await shot('02_viewer.png');
    await ev(`window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))`);
    await sleep(400);
    await ev("window.__h.clickThumb('city_night_clip.mp4')");
    const videoOpen = await ev(`(async () => {
      for (let i = 0; i < 20; i++) {
        const v = document.querySelector('.viewer-stage video');
        if (document.querySelector('.viewer') && v) return { video: true, srcPrefix: v.src.slice(0, 50) };
        await new Promise((r) => setTimeout(r, 250));
      }
      return { video: false };
    })()`);
    const shotVideo = await shot('02b_video_viewer.png');
    await ev(`window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))`);
    return {
      checks: {
        viewerOpen, fileNameBefore, infoRows, captionStored, infoGone,
        fileNameAfter, navChanged: fileNameBefore !== fileNameAfter, videoOpen,
      },
      files: [shotFile, shotVideo],
    };
  },

  async j3({ ev, shot }) {
    await ev("location.hash = '#/photos'");
    await sleep(400);
    await ev(`(async () => {
      const input = document.querySelector('#global-search');
      window.__h.setNative(input, 'beach');
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    })()`);
    const beachTitle = await ev(`(async () => {
      // Wait until the result count is stable across samples (React renders
      // the new query with the previous item list for one frame).
      let prev = null;
      for (let i = 0; i < 40; i++) {
        const t = (document.querySelector('.page-title') || {}).textContent || '';
        if (t.includes('beach') && t === prev) return t.trim();
        prev = t;
        await new Promise((r) => setTimeout(r, 250));
      }
      return prev;
    })()`);
    const beachCount = await ev("document.querySelectorAll('.thumb').length");
    const beachShot = await shot('03a_search_beach.png');
    await ev(`(async () => {
      const input = document.querySelector('#global-search');
      window.__h.setNative(input, 'type:video');
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    })()`);
    const videoTitle = await ev(`(async () => {
      let prev = null;
      for (let i = 0; i < 40; i++) {
        const t = (document.querySelector('.page-title') || {}).textContent || '';
        if (t.includes('type:video') && t === prev) return t.trim();
        prev = t;
        await new Promise((r) => setTimeout(r, 250));
      }
      return prev;
    })()`);
    await sleep(500);
    const videoCount = await ev("document.querySelectorAll('.thumb').length");
    const chips = await ev("[...document.querySelectorAll('.chip')].map((c) => c.textContent.trim())");
    const videoShot = await shot('03b_search_type_video.png');
    const backendVideos = await ev(`(async () => (await window.__h.req('search', { query: 'type:video' })).items.map((i) => i.path.split(/[\\\\/]/).pop()))()`);
    const backendBeach = await ev(`(async () => (await window.__h.req('search', { query: 'beach' })).items.map((i) => i.path.split(/[\\\\/]/).pop()))()`);
    return {
      checks: { beachTitle, beachCount, backendBeachPaths: backendBeach, videoTitle, videoCount, chips, backendVideoPaths: backendVideos },
      files: [beachShot, videoShot],
    };
  },

  async j4({ ev, shot }) {
    await ev("location.hash = '#/photos'");
    await ev('window.__h.waitThumbs()');
    // Google-Photos style range selection: the first shift-click selects one
    // item and plants the anchor; the second extends the range (beach0-2 are
    // adjacent inside their day group, so the range is exactly 3 beach
    // photos).
    await ev(`window.__h.clickThumbShift('beach0.jpg')`);
    await sleep(600);
    const selCount1 = await ev(`(document.querySelector('.select-count') || {}).textContent || null`);
    await ev(`window.__h.clickThumbShift('beach2.jpg|Sunset over the Atlantic')`);
    await sleep(600);
    const selCount = await ev(`(async () => {
      for (let i = 0; i < 20; i++) {
        const c = document.querySelector('.select-count');
        if (c) return c.textContent.trim();
        await new Promise((r) => setTimeout(r, 250));
      }
      return null;
    })()`);
    await ev("window.prompt = () => 'Journey album';");
    await ev(`window.__h.btn('Add to album').click()`);
    await sleep(1500);
    await ev("location.hash = '#/albums'");
    const albums = await ev(`(async () => {
      for (let i = 0; i < 30; i++) {
        const cards = [...document.querySelectorAll('.album-card')].map((c) => c.textContent.trim());
        if (cards.length) return cards;
        await new Promise((r) => setTimeout(r, 300));
      }
      return [];
    })()`);
    await ev(`(async () => {
      const card = [...document.querySelectorAll('.album-card')].find((c) => c.textContent.includes('Journey album'));
      if (card) card.click();
    })()`);
    await sleep(1000);
    const albumDetail = await ev(`({
      title: (document.querySelector('.page-title') || {}).textContent || null,
      thumbs: document.querySelectorAll('.thumb').length,
    })`);
    const shotFile = await shot('04_album.png');
    return { checks: { selCount1, selCount, albums, albumDetail }, files: [shotFile] };
  },

  async j5({ ev, shot }) {
    await ev("location.hash = '#/people'");
    await sleep(600);
    await ev(`window.__h.btn('Find people').click()`);
    const result = await ev(`(async () => {
      const btn = window.__h.btn('Find people');
      for (let i = 0; i < 240; i++) {
        if (btn && !btn.disabled && i > 4) break;
        await new Promise((r) => setTimeout(r, 500));
      }
      const persons = (await window.__h.req('get_persons', { path: window.__h.lib() })).persons || [];
      const un = (await window.__h.req('get_unclustered', { path: window.__h.lib() })).faces || [];
      return { persons: persons.map((p) => ({ id: p.id, name: p.name, face_count: p.face_count })), unclustered: un.length };
    })()`);
    const pageState = await ev(`({
      cards: [...document.querySelectorAll('.person-card')].map((c) => c.textContent.trim()),
      notice: (document.querySelector('.notice') || {}).textContent || null,
      empty: !!document.querySelector('.empty-state'),
    })`);
    let renamed = null;
    if (result.persons.length > 0) {
      renamed = await ev(`(async () => {
        const card = document.querySelector('.person-card');
        card.click();
        await new Promise((r) => setTimeout(r, 900));
        const nameBtn = document.querySelector('.person-name');
        if (!nameBtn) return { skipped: 'no .person-name button' };
        nameBtn.click();
        await new Promise((r) => setTimeout(r, 300));
        const input = document.querySelector('.person-name-input');
        if (!input) return { skipped: 'no .person-name-input' };
        window.__h.setNative(input, 'Alex');
        input.form.requestSubmit();
        await new Promise((r) => setTimeout(r, 900));
        const back = [...document.querySelectorAll('.person-card')].map((c) => c.textContent.trim());
        return { renamedCards: back };
      })()`);
    }
    const shotFile = await shot('05b_people.png');
    return { checks: { ...result, pageState, renamed }, files: [shotFile] };
  },

  async j6({ ev, shot }) {
    await ev("location.hash = '#/places'");
    await sleep(600);
    const places = await ev(`(async () => {
      let last = [];
      for (let i = 0; i < 25; i++) {
        last = [...document.querySelectorAll('.album-card')].map((c) => c.textContent.trim());
        if (last.length >= 2) break;
        await new Promise((r) => setTimeout(r, 1000));
      }
      // give background geocoding up to 15 more seconds to fill names
      for (let i = 0; i < 15; i++) {
        const raw = (await window.__h.req('get_places', { geocode: true })).places;
        if (raw.length && raw.every((p) => p.name)) break;
        await new Promise((r) => setTimeout(r, 1000));
      }
      return [...document.querySelectorAll('.album-card')].map((c) => c.textContent.trim());
    })()`);
    const raw = await ev(`(async () => (await window.__h.req('get_places', { geocode: true })).places.map((p) => ({ geohash: p.geohash, name: p.name, count: p.count })))()`);
    const shotFile = await shot('05_places.png');
    return { checks: { cards: places, raw }, files: [shotFile] };
  },

  async j7({ ev, shot, send }) {
    await reloadAndInstall(send, ev);
    await ev("location.hash = '#/photos'");
    await ev('window.__h.waitThumbs()');
    const countBefore = await ev(`(async () => (await window.__h.req('open_library', { path: window.__h.lib() })).library.items)()`);
    await ev("window.__h.clickThumbShift('mix0.jpg')");
    const selCount = await ev(`(async () => {
      for (let i = 0; i < 20; i++) {
        const c = document.querySelector('.select-count');
        if (c) return c.textContent.trim();
        await new Promise((r) => setTimeout(r, 250));
      }
      return null;
    })()`);
    await ev(`window.__h.btn('Trash').click()`);
    const toast = await ev(`(async () => {
      for (let i = 0; i < 30; i++) {
        const t = document.querySelector('.toast');
        if (t && t.textContent.includes('Moved to trash')) {
          return { text: t.textContent.trim(), hasUndo: !!t.querySelector('.toast-action') };
        }
        await new Promise((r) => setTimeout(r, 250));
      }
      return null;
    })()`);
    await sleep(600);
    const countAfter = await ev(`(async () => (await window.__h.req('open_library', { path: window.__h.lib() })).library.items)()`);
    await ev(`(async () => {
      const b = document.querySelector('.toast-action');
      if (b) b.click();
    })()`);
    await sleep(1200);
    const countAfterUndo = await ev(`(async () => (await window.__h.req('open_library', { path: window.__h.lib() })).library.items)()`);
    // Re-trash and keep it trashed this time.
    await ev("window.__h.clickThumbShift('mix0.jpg')");
    await sleep(500);
    await ev(`window.__h.btn('Trash').click()`);
    await sleep(1000);
    await ev("location.hash = '#/trash'");
    await sleep(900);
    const trashState = await ev(`(async () => {
      for (let i = 0; i < 20; i++) {
        if (document.querySelectorAll('.thumb').length > 0) break;
        await new Promise((r) => setTimeout(r, 300));
      }
      return {
        thumbs: document.querySelectorAll('.thumb').length,
        restoreBtn: (window.__h.btn('Restore all') || {}).textContent || null,
      };
    })()`);
    const shotFile = await shot('06_trash.png');
    await ev(`window.__h.btn('Restore all').click()`);
    await sleep(1500);
    const trashAfter = await ev(`({ thumbs: document.querySelectorAll('.thumb').length, empty: !!document.querySelector('.empty-state') })`);
    const countRestored = await ev(`(async () => (await window.__h.req('open_library', { path: window.__h.lib() })).library.items)()`);
    return {
      checks: { countBefore, selCount, toast, countAfter, countAfterUndo, trashState, trashAfter, countRestored },
      files: [shotFile],
    };
  },

  async j8({ ev, shot }) {
    await ev("location.hash = '#/photos'");
    await ev('window.__h.waitThumbs()');
    await ev("window.__h.clickThumb('mix2.jpg')");
    await ev(`(async () => {
      for (let i = 0; i < 30; i++) {
        if (document.querySelector('.viewer') && document.querySelector('.viewer-media')) return true;
        await new Promise((r) => setTimeout(r, 300));
      }
      return false;
    })()`);
    // Enter the editor
    await ev(`window.__h.btn('Edit', document.querySelector('.viewer-actions')).click()`);
    await ev(`(async () => {
      for (let i = 0; i < 50; i++) {
        if (document.querySelector('.editor') && document.querySelector('.editor-preview img')) return true;
        await new Promise((r) => setTimeout(r, 300));
      }
      return false;
    })()`);
    await sleep(600);
    const srcBefore = await ev("document.querySelector('.editor-preview img').src");
    const beforeShot = await shot('07a_editor_before.png');
    await ev(`window.__h.btn('Filters', document.querySelector('.editor-tabs')).click()`);
    await sleep(300);
    await ev(`(async () => {
      const chip = [...document.querySelectorAll('.filter-chip')].find((c) => c.textContent.trim() === 'vivid');
      if (!chip) throw new Error('vivid chip missing');
      chip.click();
    })()`);
    // Wait until the debounced preview actually changes
    const srcAfter = await ev(`(async () => {
      const before = ${JSON.stringify(srcBefore)};
      for (let i = 0; i < 80; i++) {
        const img = document.querySelector('.editor-preview img');
        if (img && img.src && img.src !== before) return img.src;
        await new Promise((r) => setTimeout(r, 300));
      }
      return null;
    })()`);
    const afterShot = await shot('07b_editor_vivid.png');
    const chipActive = await ev("(document.querySelector('.filter-chip-active') || {}).textContent || null");
    await ev(`window.__h.btn('Save', document.querySelector('.editor-topbar')).click()`);
    await ev(`(async () => {
      for (let i = 0; i < 30; i++) {
        if (!document.querySelector('.editor')) return true;
        await new Promise((r) => setTimeout(r, 300));
      }
      return false;
    })()`);
    await sleep(900);
    const editStored = await ev(`(async () => {
      const feed = await window.__h.req('get_feed', { view: 'days' });
      const item = feed.groups.flatMap((g) => g.items).find((i) => i.path.includes('mix2.jpg'));
      const detail = await window.__h.req('get_item', { path: item.path });
      return detail.item.media && detail.item.media.edit;
    })()`);
    // Close and reopen the viewer to inspect what the user sees now.
    await ev(`window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))`);
    await sleep(400);
    await ev("window.__h.clickThumb('mix2.jpg')");
    await ev(`(async () => {
      for (let i = 0; i < 30; i++) {
        const img = document.querySelector('.viewer-media');
        if (img && img.src) return true;
        await new Promise((r) => setTimeout(r, 300));
      }
      return false;
    })()`);
    const viewerSrcAfterSave = await ev("document.querySelector('.viewer-media').src");
    const viewerMatchesEdit = viewerSrcAfterSave === srcAfter;
    // Revert: Edit -> Revert -> Save
    await ev(`window.__h.btn('Edit', document.querySelector('.viewer-actions')).click()`);
    await ev(`(async () => {
      for (let i = 0; i < 40; i++) {
        if (document.querySelector('.editor')) return true;
        await new Promise((r) => setTimeout(r, 300));
      }
      return false;
    })()`);
    await ev(`window.__h.btn('Revert', document.querySelector('.editor-topbar')).click()`);
    await sleep(400);
    await ev(`window.__h.btn('Save', document.querySelector('.editor-topbar')).click()`);
    await ev(`(async () => {
      for (let i = 0; i < 30; i++) {
        if (!document.querySelector('.editor')) return true;
        await new Promise((r) => setTimeout(r, 300));
      }
      return false;
    })()`);
    await sleep(900);
    const editAfterRevert = await ev(`(async () => {
      const feed = await window.__h.req('get_feed', { view: 'days' });
      const item = feed.groups.flatMap((g) => g.items).find((i) => i.path.includes('mix2.jpg'));
      const detail = await window.__h.req('get_item', { path: item.path });
      return detail.item.media && detail.item.media.edit;
    })()`);
    await ev(`window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))`);
    return {
      checks: {
        previewChanged: !!srcAfter,
        previewSrcBeforePrefix: srcBefore.slice(0, 64),
        previewSrcAfterPrefix: srcAfter ? srcAfter.slice(0, 64) : null,
        chipActive,
        editStored,
        viewerMatchesEdit,
        viewerSrcChangedAfterSave: viewerSrcAfterSave !== srcBefore,
        editAfterRevert,
      },
      files: [beforeShot, afterShot],
    };
  },

  async j9({ ev, shot, send }) {
    await reloadAndInstall(send, ev);
    await ev(`(async () => {
      for (let i = 0; i < 60; i++) {
        if (document.querySelector('.memory-card') || document.querySelector('.thumb')) break;
        await new Promise((r) => setTimeout(r, 500));
      }
    })()`);
    const memories = await ev(`(async () => {
      const cards = [...document.querySelectorAll('.memory-card')].map((c) => (c.querySelector('.memory-title') || {}).textContent);
      const raw = (await window.__h.req('get_memories')).memories.map((m) => ({ type: m.type, title: m.title, years_ago: m.years_ago, items: m.items.length }));
      return { cards, raw };
    })()`);
    const opened = await ev(`(async () => {
      const card = [...document.querySelectorAll('.memory-card')].find((c) => c.textContent.includes('3 years ago')) || document.querySelector('.memory-card');
      if (!card) return { opened: false };
      card.click();
      for (let i = 0; i < 20; i++) {
        if (document.querySelector('.memory-player')) break;
        await new Promise((r) => setTimeout(r, 250));
      }
      const player = document.querySelector('.memory-player');
      return {
        opened: !!player,
        caption: player && player.querySelector('.memory-caption').textContent.trim(),
        segs: player ? player.querySelectorAll('.memory-seg').length : 0,
      };
    })()`);
    const memShot = await shot('08a_memory_player.png');
    const advanced = await ev(`(async () => {
      document.querySelector('.memory-player').dispatchEvent(new MouseEvent('click', { bubbles: true }));
      await new Promise((r) => setTimeout(r, 600));
      return document.querySelector('.memory-caption').textContent.trim();
    })()`);
    await ev(`window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))`);
    await sleep(400);
    const closed = await ev("!document.querySelector('.memory-player')");
    const shotFile = await shot('08b_memories_feed.png');
    return { checks: { ...memories, opened, advanced, closed }, files: [memShot, shotFile] };
  },

  async j10({ ev, shot, send }) {
    await ev("location.hash = '#/photos'");
    await sleep(400);
    const before = await ev('document.documentElement.dataset.theme');
    await ev(`window.__h.themeBtn().click()`);
    await sleep(400);
    const afterToggle = await ev(`({ theme: document.documentElement.dataset.theme, stored: localStorage.getItem('faceframe.theme') })`);
    const lightShot = await shot('09_theme_light.png');
    await reloadAndInstall(send, ev);
    const afterReload = await ev('document.documentElement.dataset.theme');
    await ev(`window.__h.themeBtn().click()`);
    await sleep(300);
    const restored = await ev(`({ theme: document.documentElement.dataset.theme, stored: localStorage.getItem('faceframe.theme') })`);
    return { checks: { before, afterToggle, afterReload, restored }, files: [lightShot] };
  },

  async eval({ ev }) {
    await install(ev);
    return { value: await ev(process.argv[3]) };
  },

  async reload({ send, ev }) {
    await reloadAndInstall(send, ev);
    return { reloaded: true, hash: await ev('location.hash') };
  },

  /** Send N trusted wheel ticks at the given viewport point. */
  async wheel({ send, ev }) {
    const ticks = Number(process.argv[3] || 8);
    const dy = Number(process.argv[4] || 400);
    await install(ev);
    await ev(`(window.__wheelLog = [])`);
    for (let i = 0; i < ticks; i++) {
      await send('Input.dispatchMouseEvent', { type: 'mouseWheel', x: 640, y: 400, deltaX: 0, deltaY: dy });
      await sleep(150);
    }
    await sleep(400);
    const log = await ev('window.__wheelLog');
    const firstTiles = await ev("[...document.querySelectorAll('.thumb')].slice(0, 2).map((t) => (t.title || '').split(/[\\\\/]/).pop())");
    return { log, firstTiles };
  },

  shot({ shot }) {
    return shot(process.argv[3] || 'adhoc.png').then((file) => ({ file }));
  },
};

const cmd = process.argv[2] || 'status';
const fn = commands[cmd];
if (!fn) {
  console.error('unknown command:', cmd);
  process.exit(2);
}
withPage(fn)
  .then((out) => {
    console.log(JSON.stringify(out, null, 1));
    process.exit(0);
  })
  .catch((e) => {
    console.error('FAILED:', e.message);
    process.exit(1);
  });
