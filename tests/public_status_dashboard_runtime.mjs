import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const root = new URL("../", import.meta.url);
const sourcePath = fileURLToPath(new URL("src/render_bridge/shared_pool_dashboard.py", root));
const source = fs.readFileSync(sourcePath, "utf8");
const scriptStart = source.indexOf("<script>") + "<script>".length;
const scriptEnd = source.indexOf("</script>", scriptStart);
assert.ok(scriptStart >= "<script>".length && scriptEnd > scriptStart, "dashboard script exists");
const script = source.slice(scriptStart, scriptEnd);

class FakeElement {
  constructor(tag = "div") {
    this.tag = tag;
    this.children = [];
    this.className = "";
    this.textContent = "";
    this.onclick = null;
  }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = [...items]; }
}

const elements = new Map();
const documentListeners = new Map();
const windowListeners = new Map();
const timers = new Map();
const calls = [];
let nextTimer = 1;
let hidden = false;
const payload = {
  summary: {
    nodes_total: 2, nodes_online: 1, nodes_available: 1, nodes_busy: 0,
    nodes_attention: 0, nodes_draining: 0, nodes_offline: 1, nodes_unknown: 0,
  },
  nodes: [
    { label: "Node 01", state: "active", availability: "available", heartbeat_age_seconds: 3, profiles: ["codex-image"] },
    { label: "Node 02", state: "active", availability: "offline", heartbeat_age_seconds: null, profiles: ["fake-image"] },
  ],
};
const document = {
  get hidden() { return hidden; },
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, new FakeElement(id));
    return elements.get(id);
  },
  createElement(tag) { return new FakeElement(tag); },
  addEventListener(name, handler) { documentListeners.set(name, handler); },
};
const window = { addEventListener(name, handler) { windowListeners.set(name, handler); } };
const context = {
  document,
  window,
  fetch: async (path, options = {}) => {
    calls.push({ path: String(path), options });
    return { ok: true, json: async () => payload };
  },
  setTimeout(callback, delay) { const id = nextTimer++; timers.set(id, { callback, delay }); return id; },
  clearTimeout(id) { timers.delete(id); },
  Date,
  Number,
  String,
  Object,
  Array,
};
vm.runInNewContext(script, context, { filename: "shared_pool_dashboard.js" });

async function flush() {
  for (let i = 0; i < 8; i += 1) await new Promise((resolve) => setImmediate(resolve));
}

function takeTimer() {
  const entry = timers.entries().next().value;
  assert.ok(entry, "a dashboard refresh is scheduled");
  const [id, timer] = entry;
  timers.delete(id);
  assert.equal(timer.delay, 5000);
  timer.callback();
}

await flush();
assert.equal(calls.length, 1, "dashboard reads status on page load");
assert.equal(calls[0].path, "/bridge/status/v1");
assert.equal(calls[0].options.method, "GET");
assert.equal(calls[0].options.cache, "no-store");
assert.equal(Object.hasOwn(calls[0].options.headers, "Authorization"), false, "no token is required");
assert.equal(elements.get("summary").children.length, 8);
assert.equal(elements.get("nodes").children.length, 2);
assert.equal(elements.get("nodes").children[0].children[0].children[0].textContent, "Node 01");
assert.match(elements.get("refreshState").textContent, /每 5 秒自动刷新/);
assert.equal(timers.size, 1);

hidden = true;
documentListeners.get("visibilitychange")();
assert.equal(timers.size, 0, "hidden dashboard stops polling");
hidden = false;
documentListeners.get("visibilitychange")();
await flush();
assert.equal(calls.length, 2, "visible dashboard refreshes immediately");
assert.equal(timers.size, 1);
takeTimer();
await flush();
assert.equal(calls.length, 3, "visible dashboard refreshes every five seconds");
assert.equal(timers.size, 1);
for (const handler of windowListeners.values()) handler();
console.log("public dashboard lifecycle checks passed: no-token load, redacted status rendering, and five-second refresh.");
