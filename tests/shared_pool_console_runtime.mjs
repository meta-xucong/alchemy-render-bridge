import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const root = new URL("../", import.meta.url);
const sourcePath = fileURLToPath(new URL("src/render_bridge/shared_pool_console.py", root));
const source = fs.readFileSync(sourcePath, "utf8");
const scriptStart = source.indexOf("<script>") + "<script>".length;
const scriptEnd = source.indexOf("</script>", scriptStart);
assert.ok(scriptStart >= "<script>".length && scriptEnd > scriptStart, "console script exists");
const script = source.slice(scriptStart, scriptEnd);

class FakeElement {
  constructor(tag = "div") {
    this.tag = tag;
    this.children = [];
    this.value = tag === "role" ? "operator" : "";
    this.className = "";
    this.classList = {
      add: (value) => { this.className = `${this.className} ${value}`.trim(); },
      remove: (value) => { this.className = this.className.split(" ").filter((x) => x !== value).join(" "); },
    };
    this.textContent = "";
    this.onclick = null;
    this.disabled = false;
  }
  get childNodes() { return this.children; }
  append(...items) { this.children.push(...items); }
  prepend(...items) { this.children.unshift(...items); }
  replaceChildren(...items) { this.children = [...items]; }
}

const elements = new Map();
const documentListeners = new Map();
const windowListeners = new Map();
const timers = new Map();
const calls = [];
const heldFetches = [];
let nextTimer = 1;
let holdFetches = false;
let hidden = false;

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

function payload(call) {
  if (call.path.includes("/invites")) return { invite_code: "invite-demo" };
  if (call.path.endsWith("/summary")) {
    return {
      pools_active: call.authorization === "Bearer token-two" ? 2 : 1,
      workspaces_joined: 1, nodes_online: 1, nodes_active: 1,
      slots_used: 0, slots_total: 1, queued_tasks: 0, active_assignments: 0,
    };
  }
  if (call.path.endsWith("/pools")) {
    return call.path.includes("/source/") ? { pools: [] } : [];
  }
  if (call.path.endsWith("/nodes")) return { nodes: [] };
  if (call.path.includes("/source/v2/tasks")) return { items: [] };
  throw new Error(`Unexpected fetch path: ${call.path}`);
}

function fakeFetch(path, options = {}) {
  const call = { path: String(path), authorization: options.headers?.Authorization, cache: options.cache };
  calls.push(call);
  return new Promise((resolve, reject) => {
    const signal = options.signal;
    const onAbort = () => {
      const error = new Error("aborted");
      error.name = "AbortError";
      reject(error);
    };
    const finish = () => {
      signal?.removeEventListener("abort", onAbort);
      resolve({ ok: true, json: async () => payload(call) });
    };
    if (signal?.aborted) return onAbort();
    signal?.addEventListener("abort", onAbort, { once: true });
    if (holdFetches) heldFetches.push({ finish, onAbort });
    else queueMicrotask(finish);
  });
}

const context = {
  document,
  window,
  Node: FakeElement,
  fetch: fakeFetch,
  location: { origin: "https://bridge.example" },
  confirm: () => true,
  AbortController,
  setTimeout(callback, delay) { const id = nextTimer++; timers.set(id, { callback, delay }); return id; },
  clearTimeout(id) { timers.delete(id); },
};
vm.runInNewContext(script, context, { filename: "shared_pool_console.js" });

async function flush() {
  for (let i = 0; i < 12; i += 1) await new Promise((resolve) => setImmediate(resolve));
}

function takeTimer() {
  const entry = timers.entries().next().value;
  assert.ok(entry, "an operator refresh is scheduled");
  const [id, timer] = entry;
  timers.delete(id);
  assert.equal(timer.delay, 5000);
  timer.callback();
}

const tokenInput = document.getElementById("token");
const roleInput = document.getElementById("role");
const connect = document.getElementById("connect");
tokenInput.value = "token-one";
await connect.onclick();
assert.equal(calls.length, 3, "operator connect loads summary, pools and nodes");
assert.ok(calls.every((call) => call.cache === "no-store"));
assert.ok(calls.every((call) => !call.path.includes("/tasks")), "operator refresh does not load history");
assert.equal(timers.size, 1);

hidden = true;
documentListeners.get("visibilitychange")();
assert.equal(timers.size, 0, "hidden page stops polling");
hidden = false;
documentListeners.get("visibilitychange")();
await flush();
assert.equal(calls.length, 6, "visible operator page refreshes immediately");
assert.equal(timers.size, 1);

holdFetches = true;
takeTimer();
assert.equal(calls.length, 9, "timer starts one Fleet refresh");
const callsBeforeManualRefresh = calls.length;
const manualRefresh = document.getElementById("refreshNodes").onclick();
assert.equal(calls.length, callsBeforeManualRefresh, "manual click reuses the in-flight refresh");
holdFetches = false;
for (const pending of heldFetches.splice(0)) pending.finish();
await manualRefresh;
await flush();
assert.equal(timers.size, 1);

holdFetches = true;
takeTimer();
assert.equal(calls.length, 12, "second delayed refresh starts once");
holdFetches = false;
tokenInput.value = "token-two";
await connect.onclick();
await flush();
assert.equal(calls.length, 15, "reconnect starts a fresh three-request cycle");
assert.ok(calls.slice(-3).every((call) => call.authorization === "Bearer token-two"));
assert.match(document.getElementById("summary").textContent, /^2 pools/);
assert.equal(timers.size, 1, "old session cannot leave a duplicate timer");

document.getElementById("inviteKind").value = "node";
document.getElementById("profiles").value = "fake-image";
await document.getElementById("createInvite").onclick();
const inviteCard = document.getElementById("inviteResult").children[0];
const inviteText = inviteCard.children.map((child) => child.textContent).join(" ");
assert.ok(inviteText.includes(".\\scripts\\start_shared_pool_node.ps1 -Url 'https://bridge.example' -Profiles 'fake-image'"));

roleInput.value = "workspace";
tokenInput.value = "workspace-token";
await connect.onclick();
await flush();
assert.equal(timers.size, 0, "workspace view does not start Fleet polling");
assert.ok(calls.some((call) => call.path.includes("/source/v2/tasks")), `workspace history remains available: ${JSON.stringify(calls.map((call) => call.path))}`);

for (const handler of windowListeners.values()) handler();
console.log("Fleet refresh lifecycle checks passed: visibility, single-flight, reconnect isolation, and workspace scope.");
