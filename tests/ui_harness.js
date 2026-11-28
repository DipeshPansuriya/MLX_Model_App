// Runs ui/app.js against a minimal DOM stub, then evaluates one scenario.
// Usage: node ui_harness.js <app.js> <scenario.js> [boot]   (prints one JSON line)
// boot is JSON: {hash, storage, storageBroken, answered}, the page as it is before
// app.js runs; answered makes every request fail at once instead of never settling.
const fs = require("fs");
const vm = require("vm");

function element(selector) {
  const el = {
    selector, writes: 0, _html: "", _text: "", textWrites: 0, value: "", hidden: false,
    className: "", disabled: false, checked: false, style: {}, dataset: {}, attrs: {},
    children: [], listeners: {}, captured: {}, scrollTop: 0, clientHeight: 0, scrollHeight: 0,
    classList: {
      set: new Set(),
      add(c) { this.set.add(c); },
      remove(c) { this.set.delete(c); },
      contains(c) { return this.set.has(c); },
      toggle(c, on) {
        const next = on === undefined ? !this.set.has(c) : !!on;
        next ? this.set.add(c) : this.set.delete(c);
        return next;
      },
    },
    get innerHTML() { return this._html; },
    set innerHTML(v) { this._html = String(v); this.writes += 1; },
    get textContent() { return this._text; },
    set textContent(v) { this._text = String(v); this.textWrites += 1; },
    setAttribute(k, v) { this.attrs[k] = String(v); },
    getAttribute(k) { return this.attrs[k] ?? null; },
    removeAttribute(k) { delete this.attrs[k]; },
    addEventListener(name, fn, capture) {
      this.listeners[name] = fn;
      if (capture === true) this.captured[name] = true;
    },
    querySelector(s) { return child(this, s); },
    querySelectorAll(s) { return buttons(this, s); },
    appendChild(c) { this.children.push(c); return c; },
    prepend(c) { this.children.unshift(c); return c; },
    replaceChildren(...c) { this.children = c; },
    remove() { this.removed = true; },
    click() {},
    focus() { ctx.document.activeElement = this; },
    select() { this.selected = true; },
    contains() { return false; },
    dispatchEvent(e) { this.listeners[e.type]?.(e); return true; },
    scrollIntoView() {},
  };
  return el;
}
// [data-x] finds the buttons in the element's markup. The same objects come back
// until the markup changes, so a scenario can click what app.js wired up.
const ENTITIES = { "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'" };
const unescape = (v) => v.replace(/&(amp|lt|gt|quot|#39);/g, (e) => ENTITIES[e]);
const camel = (k) => k.replace(/-([a-z])/g, (_, c) => c.toUpperCase());
function buttons(el, selector) {
  const wanted = /^\[data-([a-z-]+)\]$/.exec(selector)?.[1];
  if (!wanted) return [];
  if (el._parsed !== el._html) {
    el._parsed = el._html;
    el._buttons = [...el._html.matchAll(/<button\b([^>]*)>/g)].map(([, attrs]) => {
      const button = element("<button>");
      for (const [, k, v] of attrs.matchAll(/\sdata-([a-z-]+)="([^"]*)"/g)) {
        button.dataset[camel(k)] = unescape(v);
      }
      button.disabled = /\sdisabled(\s|=|$)/.test(attrs.replace(/"[^"]*"/g, '""'));
      const aria = /\saria-disabled="([^"]*)"/.exec(attrs);
      if (aria) button.attrs["aria-disabled"] = aria[1];
      return button;
    });
  }
  return el._buttons.filter((b) => camel(wanted) in b.dataset);
}
const nodes = new Map();
function child(parent, selector) {
  if (selector.startsWith("[data-dl=")) {
    return parent.children.find((c) => selector.includes(c.dataset.dl)) || null;
  }
  const key = `${parent.selector} ${selector}`;
  if (!nodes.has(key)) nodes.set(key, element(key));
  return nodes.get(key);
}
const get = (s) => { if (!nodes.has(s)) nodes.set(s, element(s)); return nodes.get(s); };
const boot = JSON.parse(process.argv[4] || "{}");
const stored = new Map(Object.entries(boot.storage ?? { "mlx-token": "test-token" }));
const refuse = () => { if (boot.storageBroken) throw new Error("storage is off"); };
const sessionStorage = {
  getItem(k) { refuse(); return stored.get(k) ?? null; },
  setItem(k, v) { refuse(); stored.set(k, String(v)); },
};
const location = { hash: boot.hash || "", pathname: "/", search: "" };
const replaced = [];
const history = {
  replaceState(_state, _title, url) {
    replaced.push(url);
    location.hash = url.includes("#") ? url.slice(url.indexOf("#")) : "";
  },
};
const intervals = [];
for (const s of ["chat", "server", "config", "lt"]) get(`#${s}ModelSelectList`).hidden = true;

const calls = [];
const opened = [];
const toasts = [];
const ctx = {
  console: { warn() {}, log() {}, error() {} },
  document: {
    querySelector: get,
    querySelectorAll: () => [],
    getElementById: (id) => get(`#${id}`),
    createElement: (tag) => element(`<${tag}>${Math.random()}`),
    body: element("body"),
    documentElement: element("html"),
    listeners: {},
    addEventListener(name, fn) { this.listeners[name] = fn; },
  },
  window: { addEventListener() {}, open: (u) => opened.push(u) },
  location, history, sessionStorage, stored, replaced, intervals,
  navigator: { clipboard: { writeText: async () => {} } },
  CSS: { escape: (s) => s },
  URL, TextDecoder, TextEncoder,
  Event: class { constructor(type) { this.type = type; } },
  setTimeout, clearTimeout,
  setInterval: (fn, ms) => intervals.push(ms),
  confirm: () => true,
  // Boot requests never settle; a scenario installs its own handler.
  fetch: (path, opts) => {
    calls.push({ path, opts });
    return ctx.respond ? ctx.respond(path, opts) : new Promise(() => {});
  },
  calls, opened, toasts, $$nodes: nodes, element,
};
if (boot.answered) {
  ctx.respond = () => Promise.resolve({ ok: false, statusText: "refused", json: async () => ({}) });
}
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), ctx);
const scenario = fs.readFileSync(process.argv[3], "utf8");
vm.runInContext(`(async () => { ${scenario} })()`, ctx)
  .then((result) => { process.stdout.write(JSON.stringify(result)); process.exit(0); })
  .catch((err) => { console.error(err.stack || String(err)); process.exit(1); });
