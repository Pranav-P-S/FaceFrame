/** Drive the running Electron app over CDP: navigate to each page and save
 * a PNG. Usage: node scripts/cdp_capture.mjs [port] */

import { writeFileSync } from "node:fs";

const port = process.argv[2] || "9223";
const targets = await (await fetch(`http://127.0.0.1:${port}/json`)).json();
const page = targets.find((t) => t.type === "page" && t.webSocketDebuggerUrl);
if (!page) {
  console.error("No page target");
  process.exit(1);
}

const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((res, rej) => {
  ws.onopen = res;
  ws.onerror = rej;
});

let msgId = 0;
const pending = new Map();
ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);
  if (msg.id && pending.has(msg.id)) {
    pending.get(msg.id)(msg);
    pending.delete(msg.id);
  }
};
function send(method, params = {}) {
  return new Promise((resolve) => {
    const id = ++msgId;
    pending.set(id, resolve);
    ws.send(JSON.stringify({ id, method, params }));
  });
}
async function evaluate(expression) {
  const res = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
  return res.result?.result?.value;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

await send("Page.enable");
await sleep(1500);

const shots = [
  ["01-feed-dark", "#/photos", 2200],
  ["02-people", "#/people", 900],
  ["03-albums", "#/albums", 900],
  ["04-search", "#/search?q=dog", 900],
  ["05-places", "#/places", 900],
  ["06-utilities", "#/utilities", 900],
  ["07-settings", "#/settings", 900],
];

for (const [name, hash, wait] of shots) {
  await evaluate(`window.location.hash = ${JSON.stringify(hash)}`);
  await sleep(wait);
  const shot = await send("Page.captureScreenshot", { format: "png" });
  writeFileSync(`review/${name}.png`, Buffer.from(shot.result.data, "base64"));
  console.log("saved", name);
}

// Viewer: click the second tile, wait for decode, capture
await evaluate(`window.location.hash = "#/photos"`);
await sleep(1600);
await evaluate(
  `document.querySelectorAll(".thumb")[1].dispatchEvent(new MouseEvent("click", { bubbles: true }))`
);
await sleep(1500);
{
  const shot = await send("Page.captureScreenshot", { format: "png" });
  writeFileSync("review/08-viewer.png", Buffer.from(shot.result.data, "base64"));
  console.log("saved 08-viewer");
}

// Light-theme feed
await evaluate(
  `document.querySelector(".viewer-topbar .icon-btn")?.click(); document.documentElement.dataset.theme = "light";`
);
await sleep(900);
{
  const shot = await send("Page.captureScreenshot", { format: "png" });
  writeFileSync("review/09-feed-light.png", Buffer.from(shot.result.data, "base64"));
  console.log("saved 09-feed-light");
}

ws.close();
console.log("done");
