/* ─────────────────────────────────────────────────────────────
   MLX Studio — front-end controller
   ───────────────────────────────────────────────────────────── */
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const state = {
  snapshot: null,
  messages: [],
  streaming: false,
  pollTimer: null,
  // The model the user chats with, and whether the server can be asked.
  chatModel: "",
  chatStatus: "",
  catalog: null,
  search: null,
  searchSeq: 0,
  downloadsOpen: false,
  lastServerRunning: false,
  refreshing: null,
  refreshAgain: false,
  lastServerUnguarded: false,
  serverMode: "",
  // The API Server card: the user's edits (null: follow the server), what ran last
  // (when just opened: the last set that started), and edits to keep over a start.
  server: null,
  serverDraft: null,
  serverLast: [],
  serverLastBackground: null,
  serverRan: null, // { engine, anthropic } of the server that ran last
  keptSet: false,
  keptBox: false,
  serverStartError: "",
  serverSending: false,
  lastApplyNotice: null,
  estimate: null,
  estimateError: "",
  view: "",
  // The Configuration page: whose values it shows, and each field as it was loaded.
  config: { repo: "", view: null, loaded: {}, seq: 0 },
  // The Load Test page: the model, its figures and what the user chose.
  lt: { repo: "", view: null, seq: 0, picked: new Set(), extra: [], loaded: {},
    signature: "", testing: false, sending: false },
};

/* ───────────── utilities ───────────── */
// The app opens this window at /#t=<token>. A fragment is never sent to the
// server; we keep it for this tab only and take it out of the address bar.
// Every /api request carries it.
const API_TOKEN = (() => {
  const m = /^#t=([A-Za-z0-9_-]{32,})$/.exec(location.hash);
  let token = m ? m[1] : "";
  try {
    if (token) sessionStorage.setItem("mlx-token", token);
    else token = sessionStorage.getItem("mlx-token") || "";
  } catch (_) { /* storage off: the token lives in memory until reload */ }
  if (m) history.replaceState(null, "", location.pathname + location.search);
  return token;
})();

function apiHeaders() {
  return { "Content-Type": "application/json", "X-MLX-Token": API_TOKEN };
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: apiHeaders(),
    ...opts,
  });
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).error || msg; } catch (_) {}
    throw new Error(msg);
  }
  return res.json();
}

// Touch the DOM only when content changes, so polling never steals focus,
// clears a selection, closes a dropdown or repeats a screen reader message.
function setHTML(el, html) {
  if (el._lastHtml === html) return false;
  const focused = focusedSelector(el);
  el._lastHtml = html;
  el.innerHTML = html;
  if (focused) el.querySelector(focused)?.focus({ preventScroll: true });
  return true;
}

// A redraw replaces card buttons; this finds the same one again by its data-*.
function focusedSelector(el) {
  const now = document.activeElement;
  if (!now || !el.contains?.(now)) return "";
  // A button with a slot keeps it when its action changes (Download becomes Installed).
  return Object.entries(now.dataset || {})
    .filter(([k]) => !(now.dataset.slot && ["act", "catDownload"].includes(k)))
    .map(([k, v]) => `[data-${k.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`)}="${CSS.escape(v)}"]`)
    .join("");
}

function setText(el, text) {
  if (el._lastText === text) return false;
  el._lastText = text;
  el.textContent = text;
  return true;
}

function toast(text, bad = false) {
  const el = $("#toast");
  el.textContent = text;
  el.className = "show" + (bad ? " bad" : "");
  clearTimeout(el._t);
  el._t = setTimeout(() => { el.className = bad ? "bad" : ""; }, 3200);
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

/* ───────────── copy model name ───────────── */
async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (_) { /* API missing or refused: copy through a selection instead */ }
  const before = document.activeElement;
  const area = document.createElement("textarea");
  area.value = text;
  area.className = "sr-only";
  area.setAttribute("readonly", "");
  document.body.appendChild(area);
  area.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch (_) { /* reported by the caller */ }
  area.remove();
  before?.focus?.();
  return ok;
}

const COPY_LABEL = "Copy model name";

// The full repo id is what API clients send as "model".
function copyButton(repo) {
  return `<button class="btn btn-mini btn-ghost" type="button" data-copy="${esc(repo)}" aria-label="${COPY_LABEL} ${esc(repo)}">${COPY_LABEL}</button>`;
}

// One listener for every card, including cards drawn later.
document.addEventListener("click", async (e) => {
  const btn = e.target.closest?.("[data-copy]");
  if (!btn) return;
  const repo = btn.dataset.copy;
  if (!(await copyText(repo))) {
    toast(`Copy failed. Model name: ${repo}`, true);
    return;
  }
  toast(`Copied ${repo}`);
  btn.textContent = "Copied ✓";
  clearTimeout(btn._t);
  btn._t = setTimeout(() => { btn.textContent = COPY_LABEL; }, 1600);
});

function notify(title, body) {
  try {
    if (typeof Notification === "undefined") return;
    if (Notification.permission === "granted") new Notification(title, { body });
    else if (Notification.permission !== "denied") Notification.requestPermission();
  } catch (_) { /* notifications are best effort */ }
}

/* ───────────── navigation ───────────── */
const VIEWS = ["server", "chat", "models", "download", "config", "loadtest",
  "logs", "settings"];

function showView(name) {
  if (!VIEWS.includes(name)) name = VIEWS[0];
  $$(".nav-item").forEach((b) => {
    const current = b.dataset.view === name;
    b.classList.toggle("active", current);
    if (current) b.setAttribute("aria-current", "page");
    else b.removeAttribute("aria-current");
  });
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${name}`));
  if (location.hash.slice(1) !== name) location.hash = name;
  if (name === "logs") loadLogs();
  if (name === "models") refreshModels();
  if (name === "download") loadCatalog();
  state.view = name;
  if (name === "config") loadConfig();
  if (name === "loadtest") loadLoadTest();
}
$$(".nav-item").forEach((btn) => btn.addEventListener("click", () => showView(btn.dataset.view)));
window.addEventListener("hashchange", () => showView(location.hash.slice(1)));

/* ───────────── state refresh ───────────── */
// One request at a time: a call made while one is running asks for one more.
function refreshState() {
  if (state.refreshing) {
    state.refreshAgain = true;
    return state.refreshing;
  }
  state.refreshing = (async () => {
    do {
      state.refreshAgain = false;
      try {
        const snap = await api("/api/state");
        state.snapshot = snap;
        render(snap);
      } catch (err) {
        console.warn("state refresh failed", err);
      }
    } while (state.refreshAgain);
    state.refreshing = null;
  })();
  return state.refreshing;
}

function render(snap) {
  const { app, hardware, python, versions, models, server, downloads, install } = snap;

  // sidebar
  setText($("#appVersion"), `v${app.version}`);
  setText($("#hwInfo"), `${hardware.chip}\n${hardware.ram} unified`);

  const updateAvailable =
    versions.mlx_update || versions.mlx_lm_update || versions.huggingface_hub_update ||
    versions.mlx_missing || app.update;
  $("#navUpdateDot").classList.toggle("hidden", !updateAvailable);

  renderChat(server);

  // server
  const held = document.activeElement;
  const unguarded = !!(server.running && server.unguarded);
  const unloaded = !!(server.running && server.load_failed);
  const runningDot = unloaded ? "dot-bad" : unguarded ? "dot-warn" : "dot-ok";
  $("#serverUnguarded").hidden = !unguarded;
  if (unguarded && !state.lastServerUnguarded) {
    toast("Restart the API server to apply the 2.0.10 protection (API Server page)", true);
  }
  state.lastServerUnguarded = unguarded;
  setHTML($("#serverState"), server.running
    ? `<span class="dot ${runningDot}"></span>${esc(unloaded
      ? "Model not loaded: press Stop, then Start Server" : runningNames(server))}`
    : `<span class="dot dot-warn"></span>Stopped`);
  $("#navServerDot").className = "dot " + (server.running ? runningDot : "");
  setText($("#navServerText"),
    unloaded ? "model not loaded" : unguarded ? "server unprotected"
      : server.running ? "server running" : "server stopped");
  setText($("#endpointText"), server.endpoint);
  setText($("#serverModePill"), unguarded ? "unprotected" : server.running
    ? (server.background ? "background" : "foreground")
    : "stopped");
  $("#serverModePill").className = "pill " +
    (server.running && !unguarded ? (server.background ? "ok" : "") : "warn");
  const notice = $("#serverNotice");
  if (server.down_notice) {
    notice.hidden = false;
    setText($("#serverNoticeText"), server.down_notice);
  } else {
    notice.hidden = true;
  }
  // Desktop notification the first time we observe the server going down.
  if (state.lastServerRunning && !server.running && server.down_notice) {
    notify("MLX Studio — API server down", server.down_notice);
    toast(server.down_notice, true);
  }
  state.lastServerRunning = server.running;
  // The box shows a running server's mode. Set once per server, so a later click
  // stays, and a stopped server keeps the mode for the next start. A new server
  // also replaces the edits on the card: the list shows what is really running.
  const mode = server.running ? `${server.pid}:${!!server.background}` : "";
  if (mode && mode !== state.serverMode) {
    // Edits made after that server's start was sent are the newer wish: they stay.
    if (!state.keptBox) $("#serverBackground").checked = !!server.background;
    if (!state.keptSet) state.serverDraft = null;
    state.keptSet = state.keptBox = false;
    state.serverMode = mode;
    state.serverStartError = "";
  }
  state.server = server;
  if (server.running) {
    state.serverLast = runningSet(server);
    state.serverLastBackground = !!server.background;
    const engine = server.engine || "mlx";
    // MLX has no Anthropic switch, so it says nothing about it.
    const anthropic = engineInfo(engine)?.direct ? !!server.anthropic : undefined;
    state.serverRan = { engine, anthropic };
  } else if (!state.serverLast.length) {
    // Just opened: the card starts with the last set that came up.
    state.serverLast = server.last_models || [];
  }
  $("#serverApplyNotice").hidden = !server.apply_notice;
  setText($("#serverApplyText"), server.apply_notice || "");
  // Said once, whichever page is open.
  if (server.apply_notice && server.apply_notice !== state.lastApplyNotice) {
    toast(server.apply_notice, true);
  }
  state.lastApplyNotice = server.apply_notice;
  const sLog = $("#serverLog");
  if (sLog) {
    const atBottom = sLog.scrollTop + sLog.clientHeight >= sLog.scrollHeight - 40;
    const lines = (server.lines || []).concat((server.events || [])
      .filter((e) => LOGGED_EVENTS.includes(e.event))
      .map((e) => `[memory] ${e.event}${e.model ? ` ${e.model}` : ""}`));
    if (setText(sLog, lines.join("\n")) && atBottom) sLog.scrollTop = sLog.scrollHeight;
  }

  // settings
  setText($("#setAppVersion"), `v${app.version}`);
  setText($("#setAppLatest"), app.latest || (app.channel === "local build" ? "local build" : "unknown"));
  setText($("#setAppChannel"), app.channel || "—");
  const appPill = $("#setAppPill");
  if (app.update) {
    appPill.textContent = `update → ${app.latest}`;
    appPill.className = "pill warn";
  } else if (app.channel && app.channel !== "local build") {
    appPill.textContent = "up to date";
    appPill.className = "pill ok";
  } else {
    appPill.textContent = "local build";
    appPill.className = "pill";
  }

  const rel = app.release || {};
  const updPanel = $("#appUpdatePanel");
  // Only an https link is ever opened or copied.
  const url = /^https:\/\//i.test(rel.url || "") ? rel.url : "";
  if (app.update && url) {
    updPanel.hidden = false;
    setText($("#appUpdatePill"), `v${rel.version || app.latest}`);
    setText($("#appUpdateVersion"), `v${rel.version || app.latest}`);
    setText($("#appUpdateNotes"), rel.notes || "—");
    setText($("#appUpdateSha"), rel.sha256 || "not published");
    $("#appUpdateDownload").onclick = () => window.open(url, "_blank", "noopener");
    $("#appUpdateCopy").onclick = async () => {
      try { await navigator.clipboard.writeText(url); toast("Download link copied"); }
      catch (_) { toast("Copy failed", true); }
    };
  } else {
    updPanel.hidden = true;
  }
  setText($("#setPython"), python);
  setText($("#setHardware"), `${hardware.chip} · ${hardware.ram}`);
  setText($("#setCheckedAt"), versions.checked_at || "never");
  setPkg("setMlx", "setMlxPill", versions.mlx, versions.mlx_update && versions.mlx_target, versions.mlx_missing);
  setPkg("setMlxLm", "setMlxLmPill", versions.mlx_lm, versions.mlx_lm_update && versions.mlx_lm_target, versions.mlx_lm_missing);
  setPkg("setHf", "setHfPill", versions.huggingface_hub,
    versions.huggingface_hub_update && versions.huggingface_hub_target, false);

  const hint = $("#setupHint");
  if (versions.mlx_missing || versions.mlx_lm_missing) {
    hint.textContent = "MLX is not installed yet — install it to run local models.";
  } else if (versions.mlx_update || versions.mlx_lm_update || versions.huggingface_hub_update) {
    hint.textContent = "A newer verified package release can be installed.";
  } else if (versions.checked_at) {
    hint.textContent = "Everything is installed and up to date.";
  } else {
    hint.textContent = "";
  }

  const installLog = $("#installLog");
  if (install && install.lines && install.lines.length) {
    installLog.hidden = false;
    if (setText(installLog, install.lines.join("\n"))) installLog.scrollTop = installLog.scrollHeight;
  }

  // model dropdowns
  const ready = models.filter((m) => m.complete !== false).map((m) => m.repo);
  state.readyModels = ready;
  renderServerMemory(server);
  refreshEstimate(snap);
  paintServerCard();
  // A Restart button goes away with its notice: leave the focus on something that stays.
  const gone = [["#serverRestartBtn", "#serverNotice"],
    ["#serverUnguardedRestart", "#serverUnguarded"]];
  if (gone.some(([button, box]) => held === $(button) && $(box).hidden)) spareControl().focus();
  renderToolsWarning(server);
  renderModelCards(models, server, downloads);
  renderDownloads(downloads);
  renderCatalog();
  renderSearch();
  renderConfigPage(server);
  renderLoadTest(snap);
  paintEngineCards(install);
}

// target is the version the Install button would apply, if newer than current.
function setPkg(valId, pillId, current, target, missing) {
  const val = $(`#${valId}`);
  const pill = $(`#${pillId}`);
  val.textContent = current || "not installed";
  if (missing || !current) {
    pill.textContent = "missing";
    pill.className = "pill bad";
  } else if (target) {
    pill.textContent = `update → ${target}`;
    pill.className = "pill warn";
  } else {
    pill.textContent = "installed";
    pill.className = "pill ok";
  }
}

/* ───────────── two models on the API server ───────────── */
const MAX_SERVER_MODELS = 2;
const LOGGED_EVENTS = ["evict", "trim", "alone", "preload-skipped", "load-failed"];
const shortName = (repo) => String(repo || "").split("/").pop();
const gb = (bytes) => (bytes / 1e9).toFixed(1);
const runningSet = (server) =>
  server?.repos?.length ? server.repos : server?.repo ? [server.repo] : [];
// Order does not make a different choice.
const sameSet = (a, b) => a.length === b.length && a.every((repo) => b.includes(repo));

// The names that are installed completely; all of them until the list has loaded.
const usable = (set) =>
  set.filter((repo) => !state.readyModels || state.readyModels.includes(repo));

// The models on the card: the user's edits, else what runs, else what ran last.
function serverSet() {
  const server = state.server;
  if (server?.running) return state.serverDraft || runningSet(server);
  return usable(state.serverDraft || state.serverLast);
}

// The card differs from the running server, so Apply has something to do.
function serverPending() {
  const server = state.server;
  return !!server?.running && (!sameSet(serverSet(), runningSet(server)) ||
    engineChanged() || $("#serverBackground").checked !== !!server.background || !!server.settings_pending);
}

// The model fields of a start request.
function serverChoice(set = serverSet(), engine = engineId(), anthropic = anthropicWanted()) {
  const [repo = "", second] = set;
  const models = second ? { repo, also: [second] } : { repo };
  // No engine named means MLX.
  return engine === "mlx" ? models : { ...models, engine, anthropic };
}

/* ───────────── engines ───────────── */
const engineList = () => state.server?.engines || [];
const runningEngine = () => state.server?.engine || "mlx";
const engineId = () => state.engine || (state.server?.running ? runningEngine()
  : state.serverRan?.engine || state.server?.last_engine) || "mlx";
const engineInfo = (id = engineId()) => engineList().find((engine) => engine.id === id);
// The switch as the user left it; else as the server runs, or ran; else on.
const anthropicWanted = () => state.anthropic ?? (state.server?.running
  ? (engineInfo(runningEngine())?.direct ? !!state.server.anthropic : true)
  : state.serverRan?.anthropic ?? state.server?.last_anthropic ?? true);

function engineChanged() {
  const server = state.server;
  if (!server?.running) return false;
  if (engineId() !== runningEngine()) return true;
  return !!engineInfo()?.anthropic && anthropicWanted() !== !!server.anthropic;
}

// Whether the chosen engine can serve the first model, as the app says.
async function loadEligibility(engine, repo) {
  const install = engineInfo(engine)?.state;
  const key = `${engine} ${repo} ${install}`;
  if (state.eligibilityKey === key) return;
  state.eligibilityKey = key;
  state.eligibility = null;
  try {
    const { engines } = await api(`/api/engines${repoQuery(repo)}`);
    if (state.eligibilityKey !== key) return;
    state.eligibility = engines.find((entry) => entry.id === engine) || null;
    paintServerCard();
  } catch (_) {
    state.eligibilityKey = "";
  }
}

function engineReason(set) {
  const engine = engineInfo();
  if (!engine?.direct) return "";
  if (set.length > 1) return `${engine.name} serves one model. Remove the second one.`;
  if (!set.length) return "";
  loadEligibility(engine.id, set[0]);
  const found = state.eligibility;
  return found && found.id === engine.id && !found.eligible ? found.reason : "";
}

function paintEngine(set) {
  const select = $("#engineSelect");
  setHTML(select, engineList().map((engine) =>
    `<option value="${esc(engine.id)}"${engine.selectable ? "" : " disabled"}>` +
    `${esc(engine.name)}${engine.selectable ? "" : ` (${esc(engine.reason)})`}</option>`).join(""));
  const engine = engineInfo();
  select.value = engineId();
  $("#anthropicBox").hidden = !engine?.anthropic;
  $("#engineAnthropic").checked = anthropicWanted();
  showLine("#engineWhy", engineReason(set));
  const missing = engine?.missing || [];
  const box = $("#engineMissing");
  if (box.hidden && missing.length) box.open = true;
  box.hidden = !missing.length;
  setText($("#engineMissingTitle"), `Not available with this engine (${missing.length})`);
  setHTML($("#engineMissingList"), missing.map(([what, why]) =>
    `<li>${esc(what)}: ${esc(why)}</li>`).join(""));
  paintClientSettings(set, engine);
}

function paintClientSettings(set, picked) {
  const server = state.server;
  // A running server's own settings, not those of a choice that waits for Apply.
  const running = !!server.running;
  const engine = running ? engineInfo(runningEngine()) : picked;
  const repos = running && server.repos?.length ? server.repos : set;
  const direct = !!engine?.direct;
  const base = `http://127.0.0.1:${(direct ? engine.port : 8080) || 8080}`;
  const model = direct ? "default_model" : repos[0] || "(choose a model)";
  const keyed = direct && running && server.ready;
  $("#copyKey").hidden = !keyed;
  $("#curlExample").hidden = direct;
  setText($("#openaiSettings"), `Base URL: ${base}/v1\nModel: ${model}\nAPI key: ` +
    (direct ? "press Copy key (new at every start)" : "not needed"));
  const claude = direct && (running ? !!server.anthropic : anthropicWanted());
  setText($("#claudeSettings"), claude
    ? [`export ANTHROPIC_BASE_URL=${base}`,
      "export ANTHROPIC_AUTH_TOKEN=<paste the copied key>",
      `export ANTHROPIC_MODEL=${model}`,
      `export ANTHROPIC_DEFAULT_HAIKU_MODEL=${model}`,
      "claude"].join("\n")
    : direct ? "Switch on the Anthropic route, then apply, to use Claude Code."
      : "Not available: the MLX engine has no Anthropic route. Choose OptiQ.");
  $("#copyClaude").hidden = !claude;
}

const ENGINE_STATES = { installed: "installed", not_installed: "not installed",
  update: "update ready", broken: "broken: remove, or install again",
  unsupported: "not supported on this Mac", unavailable: "not available yet" };

function paintEngineCards(install) {
  setHTML($("#engineCards"), engineList().filter((engine) => engine.id !== "mlx").map((engine) => {
    const busy = !!install?.running && String(install.kind).startsWith(engine.id);
    const off = install?.running ? " disabled" : "";
    const button = (action, words, disabled = "") =>
      `<button type="button" class="btn btn-mini" data-engine="${esc(engine.id)}" ` +
      `data-action="${action}" aria-label="${words} ${esc(engine.name)}"${off || disabled}>` +
      `${words}</button>`;
    const unsupported = engine.state === "unsupported";
    const serving = !!state.server?.running && runningEngine() === engine.id;
    const note = !engine.installable ? engine.reason : unsupported ? engine.unsupported
      : serving ? "Stop the API server to remove this engine." : "";
    const buttons = !engine.installable ? "" :
      button("install", "Install / Update",
        unsupported || engine.state === "installed" ? " disabled" : "") +
      (["installed", "update", "broken"].includes(engine.state)
        ? button("remove", "Remove", serving ? " disabled" : "") : "");
    return `<div class="kv-row"><span>${esc(engine.name)}</span>` +
      `<span class="mono">${esc(engine.version || "—")}</span>` +
      `<span class="pill">${busy ? "working…" : ENGINE_STATES[engine.state] || ""}</span>` +
      `<span>${buttons}</span></div>` + (note ? `<p class="hint">${esc(note)}</p>` : "");
  }).join(""));
}

async function changeEngine(engine, action) {
  if (action === "remove" && !confirm("Remove this engine? Your models stay.")) return;
  if (action === "install" && ["update", "broken"].includes(engineInfo(engine)?.state) &&
    !confirm("The current engine folder is removed before the download. " +
      "If the download fails, there is no engine until you install again. Continue?")) return;
  try {
    await api(`/api/engines/${action}`, { method: "POST", body: JSON.stringify({ engine }) });
    toast(action === "remove" ? "Engine removed" : "Install started");
  } catch (err) { toast(err.message, true); }
  state.eligibilityKey = "";
  refreshState();
}

async function copyServerKey() {
  try {
    const { key } = await api("/api/server/key", { method: "POST", body: "{}" });
    await navigator.clipboard.writeText(key);
    toast("Key copied. It changes at every start.");
  } catch (err) { toast(err.message || "Copy failed", true); }
}

function wireEngines() {
  $("#engineSelect").addEventListener("change", () => {
    state.engine = $("#engineSelect").value;
    state.serverStartError = "";
    serverCardChanged();
  });
  $("#engineAnthropic").addEventListener("change", () => {
    state.anthropic = $("#engineAnthropic").checked;
    serverCardChanged();
  });
  $("#engineCards").addEventListener("click", (event) => {
    const button = event.target.closest?.("[data-engine]");
    if (button) changeEngine(button.dataset.engine, button.dataset.action);
  });
  $("#copyKey").addEventListener("click", copyServerKey);
  for (const [button, box] of [["#copyOpenai", "#openaiSettings"],
    ["#copyClaude", "#claudeSettings"]]) {
    $(button).addEventListener("click", async () => {
      try { await navigator.clipboard.writeText($(box).textContent); toast("Settings copied"); }
      catch (_) { toast("Copy failed", true); }
    });
  }
}

function runningNames(server) {
  const resident = server.resident || [];
  const repos = server.repos || [];
  if (Math.max(resident.length, repos.length) < 2) return `Running — ${shortName(server.repo)}`;
  if (!resident.length) return `Running — ${repos.map(shortName).join(" and ")}`;
  const [now, ...rest] = resident.map(shortName);
  return `Running: ${[`${now} (current)`, ...rest].join(", ")}`;
}

const holdsTwo = (server) =>
  !!server.running && (server.repos || []).length > 1 && !server.multi_off;

// [title, text] of what differs from the pair that was asked for; empty if nothing.
function multiNotice(server) {
  const nothing = ["", ""];
  if (!server.running) return nothing;
  if (server.multi_off) {
    return ["One model at a time",
      `Only one model is kept loaded with this mlx-lm version (${server.multi_off}). ` +
      "A request for the other model loads it from disk."];
  }
  const oversize = server.oversize || [];
  if (oversize.length) {
    const budget = server.memory?.budget_bytes;
    const space = budget == null ? "the memory" : `the ${gb(budget)} GB`;
    const tokens = server.context_tokens;
    // Only an eviction shows that the other model was unloaded; a first load has none.
    const events = server.events || [];
    const evicted = new Set(
      events.filter((e) => e.event === "evict").map((e) => e.model)
    );
    const otherModel = (server.repos || []).find((r) => !oversize.includes(r));
    const wasEvicted = otherModel && evicted.has(otherModel);
    const unloaded = wasEvicted ? "The other model was unloaded. " : "";
    return ["Loaded without enough memory",
      `${oversize.map(shortName).join(", ")} was loaded on its own: it does not fit in ` +
      `${space} the API server may use now` +
      (tokens ? `, with room for a ${tokens}-token reply` : "") +
      `. ${unloaded}macOS may slow down or stop the API server. ` +
      "Free some memory or choose a smaller model."];
  }
  if (server.memory_low) {
    return ["Free memory is low",
      "Other apps now use memory the API server was counting on " +
      `(${gb(server.memory_low.short_by_bytes)} GB short). Replies may slow down. ` +
      "Close apps, or restart the server for a smaller context."];
  }
  const asked = server.repos || [];
  const resident = server.resident || [];
  if (asked.length < 2 || !resident.length) return nothing;
  const events = server.events || [];
  const gone = asked.filter((repo) => !resident.includes(repo)).map(shortName);
  const added = resident.filter((repo) => !asked.includes(repo)).map(shortName);
  if (gone.length && added.length) {
    return ["Loaded models changed",
      `${gone.join(", ")} was replaced by ${added.join(", ")} on request.`];
  }
  const skipped = events.find(
    (e) => e.event === "preload-skipped" && !resident.includes(e.model));
  if (skipped) {
    return ["Second model not preloaded",
      `${shortName(skipped.model)} was not preloaded: it did not fit in memory or could not ` +
      "be loaded. It loads when a client asks for it."];
  }
  // Without an evict event the second model may simply still be loading.
  const evicted = asked.filter((repo) => !resident.includes(repo) &&
    events.some((e) => e.event === "evict" && e.model === repo));
  return evicted.length
    ? ["Loaded models changed",
      `${evicted.map(shortName).join(", ")} was unloaded to make room in memory. ` +
      "It loads again when a client asks for it."]
    : nothing;
}

/* ───────────── configuration page ───────────── */
const CONFIG_KEYS = ["max_context_tokens", "server_max_reply_tokens", "chat_max_reply_tokens",
  "chat_temperature", "chat_top_p", "prompt_cache_size", "prompt_cache_bytes",
  "mlx_cache_limit_mb", "two_model_context_tokens", "memory_limit_percent",
  "performance_mode", "kv_bits", "kv_group_size", "prefill_step_size"];
const PERF_NAMES = { standard: "Standard", save_memory: "Save memory", advanced: "Advanced" };
const KV_GROUPS = [32, 64, 128];
const ALL_MODELS = "All models (defaults)";

const configField = (key) => $(`#cfg-${key}`);
const repoQuery = (repo) => (repo ? `?repo=${encodeURIComponent(repo)}` : "");

// Whether a field holds something the user typed and has not saved.
function configEdited(key) {
  const input = configField(key);
  const loaded = state.config.loaded[key];
  return loaded !== undefined && !input.disabled &&
    (input.value.trim() !== loaded || !!input.validity?.badInput);
}
const configDirty = () => CONFIG_KEYS.some(configEdited);

function fieldError(key, message) {
  const input = configField(key);
  if (message) input.setAttribute("aria-invalid", "true");
  else input.removeAttribute("aria-invalid");
  setText($(`#cfg-${key}-error`), message);
}

function clearConfig() {
  state.config.view = null;
  state.config.loaded = {};
  for (const key of CONFIG_KEYS) {
    configField(key).value = "";
    fieldError(key, "");
    setText($(`#cfg-${key}-now`), "");
  }
  for (const id of ["#configError", "#configClamp", "#configTestWarn"]) setText($(id), "");
  $("#configSaveBtn").disabled = $("#configResetBtn").disabled = true;
  setText($("#cfg-performance_mode-blocked"), "");
  paintPerformance();
}

// The raw fields show only under Advanced; 4-bit is never chosen without a warning.
function paintPerformance() {
  const view = state.config.view;
  const inherited = (key) => (view?.repo ? view.settings[key]?.inherited : null);
  const mode = configField("performance_mode").value || inherited("performance_mode") || "standard";
  $("#cfg-advanced").hidden = mode !== "advanced";
  const bits = configField("kv_bits").value || String(inherited("kv_bits") ?? "");
  $("#cfg-kv4-warn").hidden = !(mode === "advanced" && bits === "4");
}

// What the chosen model cannot do is switched off, with the reason.
function paintBlocked(view) {
  const blocked = view.performance?.blocked || {};
  const groups = blocked.kv_group_size || {};
  const shared = view.settings.performance_mode?.inherited;
  setText($("#cfg-mode-default"), view.repo
    ? `Same as All models (${PERF_NAMES[shared] || PERF_NAMES.standard})`
    : "Standard (recommended)");
  // Under All models the first entry already is Standard.
  $("#cfg-mode-standard").hidden = !view.repo && view.settings.performance_mode?.value !== "standard";
  $("#cfg-mode-save_memory").disabled = !!blocked.save_memory;
  for (const g of KV_GROUPS) $(`#cfg-group-${g}`).disabled = !!groups[g];
  const none = KV_GROUPS.every((g) => groups[g]);
  if (none) configField("kv_bits").disabled = configField("kv_group_size").disabled = true;
  const unfit = [...new Set(Object.values(groups))].join("; ");
  setText($("#cfg-performance_mode-blocked"), blocked.save_memory
    ? `Save memory is not available for this model, so it stays on Standard: ${blocked.save_memory}.`
    : unfit ? `Some group sizes are not available for this model: ${unfit}.` : "");
}

function configHint(view, setting) {
  const now = `Effective: ${setting.effective}`;
  if (!view.repo) return now;
  if (setting.global_only) return `${now}. Change it under ${ALL_MODELS}.`;
  const shared = setting.inherited;
  if (setting.own != null) {
    return `${now} (set for this model${shared != null ? `; All models: ${shared}` : ""})`;
  }
  return shared != null ? `${now} (from All models)` : now;
}

// Typed, unsaved text stays; every other field follows what is saved.
function paintConfig(view) {
  state.config.view = view;
  for (const key of CONFIG_KEYS) {
    const setting = view.settings[key];
    const input = configField(key);
    const locked = !!(view.repo && setting.global_only);
    input.disabled = locked;
    const edited = configEdited(key);
    const saved = String((view.repo ? setting.own : setting.value) ?? "");
    input.min = setting.min;
    input.max = setting.max;
    input.step = setting.decimal ? "any" : "1";
    input.placeholder = view.repo && setting.inherited != null
      ? `inherited: ${setting.inherited}` : "automatic";
    if (!edited) input.value = locked ? String(setting.value ?? "") : saved;
    state.config.loaded[key] = saved;
    setText($(`#cfg-${key}-now`), configHint(view, setting));
  }
  paintBlocked(view);
  paintPerformance();
  setText($("#configClamp"), view.context_message || "");
  $("#configSaveBtn").disabled = $("#configResetBtn").disabled = false;
}

// A context above everything a load test has passed is only a hope: say so.
function warnUntested(view, tests) {
  const passed = (tests?.results || []).filter((run) => !run.stale)
    .flatMap((run) => run.rows.filter((r) => r.status === "passed").map((r) => r.size));
  const largest = Math.max(0, ...passed);
  const context = view.applied_context || view.settings.max_context_tokens.value;
  setText($("#configTestWarn"), largest && context > largest
    ? `The context here (${context} tokens) is above the largest size that passed a load ` +
      `test (${largest} tokens).` : "");
}

async function loadConfig() {
  if (!API_TOKEN) return;
  const { repo } = state.config;
  const seq = ++state.config.seq;
  try {
    const view = await api(`/api/config${repoQuery(repo)}`);
    if (seq !== state.config.seq) return;
    if (state.config.failed) setText($("#configError"), "");
    state.config.failed = false;
    paintConfig(view);
    if (!repo) return setText($("#configTestWarn"), "");
    // The warning is extra: the page works when these figures cannot be read.
    api(`/api/loadtest${repoQuery(repo)}`)
      .then((tests) => { if (seq === state.config.seq) warnUntested(view, tests); })
      .catch(() => {});
  } catch (err) {
    if (seq !== state.config.seq) return;
    state.config.failed = true;
    setText($("#configError"), err.message);
  }
}

// Puts the app's choice in a page's searchable model list.
function setModelPick(sel, values, value) {
  fillSelect(sel, values, value);
  $(sel).value = value;
  pickers[sel]?.sync(values);
}
const paintConfigPick = () => setModelPick("#configModelSelect",
  [ALL_MODELS, ...(state.readyModels || [])], state.config.repo || ALL_MODELS);

async function chooseConfigModel(value) {
  const repo = value === ALL_MODELS ? "" : value;
  if (repo === state.config.repo) return;
  if (configDirty() && !confirm("Discard your unsaved changes?")) {
    paintConfigPick();
    await null; // the list says "Selected" when this first returns
    setText($("#configModelSelectStatus"), `Kept ${state.config.repo || ALL_MODELS}.`);
    return;
  }
  state.config.repo = repo;
  paintConfigPick();
  clearConfig();
  await loadConfig();
}

function renderConfigPage(server) {
  const { repo } = state.config;
  const gone = !!repo && !(state.readyModels || []).includes(repo);
  if (gone) {
    state.config.repo = "";
    clearConfig();
  }
  paintConfigPick();
  if (gone && state.view === "config") loadConfig();
  const behind = !!(server.running && server.settings_pending);
  // The button goes away once applied: leave the focus on something that stays.
  const held = document.activeElement === $("#configApplyBtn");
  $("#configPending").hidden = !behind;
  const optiq = engineId() === "optiq" || (!!server.running && runningEngine() === "optiq");
  $("#configEngine").hidden = !optiq;
  if (held && !behind) $("#configModelSelectSearch").focus();
}

async function postSettings(body) {
  await api("/api/server/settings", { method: "POST", body: JSON.stringify(body) });
  state.estimateKey = null; // the figures and any warning are asked for again
}

// Saves only the changed fields; an emptied one goes back to automatic or inherited.
async function saveConfig() {
  const { view, repo } = state.config;
  if (!view) return;
  const values = {};
  const bad = [];
  for (const key of CONFIG_KEYS) {
    fieldError(key, "");
    if (!configEdited(key)) continue;
    const { min, max, decimal } = view.settings[key];
    const input = configField(key);
    const raw = input.value.trim();
    const word = key === "performance_mode"; // every other setting is a number
    const value = raw === "" ? null : word ? raw : Number(raw);
    const fits = value === null || word ||
      (value >= min && value <= max && (decimal || Number.isInteger(value)));
    if (input.validity?.badInput || !fits) {
      const kind = decimal ? "number" : "whole number";
      fieldError(key, `Use a ${kind} from ${min} to ${max}, or leave it empty.`);
      bad.push(input);
    } else {
      values[key] = value;
    }
  }
  if (bad.length) {
    setText($("#configError"), bad.length === 1 ? "Not saved: 1 field needs a change."
      : `Not saved: ${bad.length} fields need a change.`);
    bad[0].focus();
    return;
  }
  setText($("#configError"), "");
  if (!Object.keys(values).length) return toast("Nothing changed");
  try {
    await postSettings({ values, ...(repo ? { repo } : {}) });
  } catch (err) {
    setText($("#configError"), err.message);
    return;
  }
  state.config.loaded = {};
  toast("Saved. Server limits apply at the next start or Apply changes.");
  refreshState();
  await loadConfig();
}

async function resetConfig() {
  const { view, repo } = state.config;
  if (!view || !confirm(`Clear every value saved for ${repo || ALL_MODELS}?`)) return;
  try {
    await postSettings({ reset: true, ...(repo ? { repo } : {}) });
  } catch (err) {
    setText($("#configError"), err.message);
    return;
  }
  clearConfig();
  toast(repo ? "This model uses the All models values again." : "Defaults reset to automatic.");
  refreshState();
  await loadConfig();
}

/* ───────────── load test page ───────────── */
const LT_NUMBERS = ["reply_tokens", "rounds", "margin_percent", "timeout_seconds"];
const LT_MAX_SIZES = 12;
const LT_ENDED = { done: "finished", memory: "stopped for memory safety",
  cancelled: "cancelled", failed: "failed", interrupted: "interrupted" };
const LT_PILL = { passed: "ok", skipped: "warn", cancelled: "warn", interrupted: "warn",
  stopped: "bad", failed: "bad" };

const ltError = (message) => setText($("#ltError"), message);
const figure = (v) => (typeof v === "number" ? String(Math.round(v * 10) / 10) : "n/a");
const seconds = (v) => (typeof v === "number" ? `${figure(v)} s` : "n/a");

function setLoadTestModel(repo) {
  Object.assign(state.lt, { repo, view: null, extra: [] });
  state.lt.picked.clear();
  ltError("");
  setHTML($("#ltSizes"), "");
  setHTML($("#ltResults"), "");
  setText($("#ltRange"), "");
  $("#ltOutcome").hidden = true;
}

async function loadLoadTest() {
  const { repo } = state.lt;
  if (!API_TOKEN || !repo) return;
  const seq = ++state.lt.seq;
  try {
    const view = await api(`/api/loadtest${repoQuery(repo)}`);
    if (seq !== state.lt.seq) return;
    if (state.lt.failed) ltError("");
    state.lt.failed = false;
    state.lt.view = view;
    paintLoadTest();
  } catch (err) {
    if (seq !== state.lt.seq) return;
    state.lt.failed = true;
    ltError(err.message);
  }
}

function paintLoadTest() {
  const { view, loaded } = state.lt;
  for (const key of LT_NUMBERS) {
    const { value, min, max } = view.load_test[key];
    const input = $(`#lt-${key}`);
    input.min = min;
    input.max = max;
    if (loaded[key] === undefined || input.value === loaded[key]) input.value = String(value);
    loaded[key] = String(value);
  }
  const { min, max } = view.sizes;
  $("#ltCustom").min = min;
  $("#ltCustom").max = max;
  setText($("#ltRange"),
    `This model takes ${min} to ${max} tokens. Up to ${LT_MAX_SIZES} sizes in one test.`);
  paintLoadTestControls();
  paintResults();
}

// The settings' sizes, the ones used last and the ones typed now.
function listedSizes() {
  const { view, extra } = state.lt;
  const all = [...view.sizes.offered.map((s) => s.size),
    ...view.load_test.last_sizes.value, ...extra];
  return [...new Set(all)].sort((a, b) => a - b);
}

function paintSizes(off) {
  const { view, picked } = state.lt;
  if (!view) return;
  const { min, max } = view.sizes;
  setHTML($("#ltSizes"), listedSizes().map((size) => {
    const outside = size < min || size > max;
    return `<label class="check"><input type="checkbox" value="${esc(size)}" ` +
      `data-size="${esc(size)}"${picked.has(size) && !outside ? " checked" : ""}` +
      `${outside || off ? " disabled" : ""} /><span>${esc(size)} tokens` +
      `${outside ? ` (this model takes up to ${esc(max)})` : ""}</span></label>`;
  }).join(""));
}

function paintLoadTestControls() {
  const snap = state.snapshot;
  if (!snap) return;
  const { view } = state.lt;
  const testing = !!snap.loadtest?.active;
  const none = !(state.readyModels || []).length;
  const serving = !!(snap.server?.running || snap.server?.starting);
  let why = "";
  if (!none && !testing) {
    why = serving ? "The API server is running. A load test needs it stopped."
      : view?.blocked ? `Cannot start now: ${view.blocked}.` : "";
  }
  const start = $("#ltStartBtn");
  const cancel = $("#ltCancelBtn");
  const held = document.activeElement;
  $("#ltNoModel").hidden = !none;
  setText($("#ltWhy"), why);
  start.disabled = none || testing || !!why || !view;
  cancel.hidden = !testing;
  // aria-disabled, not disabled: the button keeps the focus while the test stops.
  if (snap.loadtest?.status === "cancelling") cancel.setAttribute("aria-disabled", "true");
  else cancel.removeAttribute("aria-disabled");
  const stopServing = $("#ltStopServerBtn");
  stopServing.hidden = !(serving && !testing);
  for (const id of ["#ltModelSelectSearch", "#ltCustom", "#ltAddBtn", "#ltEngine",
    ...LT_NUMBERS.map((key) => `#lt-${key}`)]) {
    $(id).disabled = none || testing;
  }
  paintSizes(testing);
  paintThinking(none || testing);
  // Focus follows Start to Cancel and back.
  if (held === start && testing) cancel.focus();
  const gone = (held === cancel && !testing) || (held === stopServing && stopServing.hidden);
  if (gone) (start.disabled ? $("#ltModelSelectSearch") : start).focus();
}

// Offered only where the model's chat template has the switch.
function paintThinking(off) {
  const found = state.lt.view?.thinking;
  const box = $("#ltThinking");
  box.disabled = off || !found?.supported;
  box.checked = !!found?.supported && state.lt.thinking !== false;
  setText($("#ltThinkingNote"), found && !found.supported ? found.reason
    : "Thinking uses the reply token budget (Reply tokens per request).");
}

// Only a new step is read out; the counts that move with every poll are not.
function paintProgress(run) {
  const testing = !!run?.active;
  $("#ltRunning").hidden = !testing;
  if (!testing) {
    if (state.lt.testing && run) {
      setText($("#ltAnnounce"), `Load test ${LT_ENDED[run.status] || run.status}.`);
    }
    state.lt.testing = false;
    return;
  }
  state.lt.testing = true;
  const cancelling = run.status === "cancelling";
  const where = run.size ? `, size ${run.index} of ${run.of} (${run.size} tokens)` : "";
  const phase = cancelling ? "Stopping the test…"
    : `Testing ${run.repo}${where}: ${run.step || "starting"}.`;
  setText($("#ltStep"), phase);
  setText($("#ltAnnounce"), phase);
  const reading = !cancelling && run.total > 0;
  const bar = $("#ltProgress");
  bar.hidden = !reading;
  if (reading) {
    bar.max = run.total;
    bar.value = run.done;
  }
  const limit = state.lt.view?.results.find((r) => r.id === run.run_id)?.limit_percent;
  setText($("#ltDetail"), [
    reading ? `Reading the prompt: ${run.done} of ${run.total} tokens.` : "",
    run.memory_percent == null ? ""
      : `Memory now: ${run.memory_percent}% of RAM${limit ? ` (limit ${limit}%)` : ""}.`,
  ].filter(Boolean).join(" "));
}

// Why the last test of this model did not finish, in one place.
function paintOutcome(last) {
  const told = !!last && !state.snapshot?.loadtest?.active &&
    !["done", "running", "cancelling"].includes(last.status);
  $("#ltOutcome").hidden = !told;
  if (!told) return;
  const memory = last.status === "memory";
  setText($("#ltOutcomeTitle"), memory ? "Stopped for memory safety"
    : `Test ${LT_ENDED[last.status] || last.status}`);
  setText($("#ltOutcomeText"), memory
    ? `${last.reason}. Memory limit: ${last.limit_percent}%. Larger sizes were not run.`
    : last.reason);
}

function resultRow(run, row, testing) {
  const used = run.values || {};
  const note = (text) => (text ? `<span class="lt-note">${esc(text)}</span>` : "");
  // A saved size is MLX's own setting.
  const apply = row.status === "passed" && !run.stale && run.status !== "running" && !run.engine
    ? `<button type="button" class="btn btn-mini" data-apply="${esc(row.size)}" ` +
      `data-run="${esc(run.id)}" aria-label="Apply this size to config: ${esc(row.size)} ` +
      `tokens"${testing ? " disabled" : ""}>Apply this size to config</button>`
    : "";
  return `<tr><th scope="row">${esc(row.size)}` +
    note(row.prompt_tokens ? `${row.prompt_tokens} in prompt` : "") +
    `</th><td><span class="pill ${LT_PILL[row.status] || ""}">${esc(row.status)}</span>` +
    note(row.reason) +
    `</td><td>${figure(row.read_tps)}</td><td>${seconds(row.first_token_s)}` +
    note(row.follow_up_first_token_s == null ? ""
      : `follow-up ${seconds(row.follow_up_first_token_s)}`) +
    `</td><td>${figure(row.write_tps)}</td><td>` +
    (row.peak_percent == null ? "n/a" : `${figure(row.peak_percent)}%`) +
    note(row.server_peak_bytes ? `server ${gb(row.server_peak_bytes)} GB` : "") +
    note(row.projected_percent == null ? "" : `projected ${figure(row.projected_percent)}%`) +
    `</td><td>${esc(`reply ${used.reply_tokens} tokens, ${used.rounds} rounds`)}` +
    note(`margin ${used.margin_percent}%, limit ${run.limit_percent}%`) +
    `</td><td>${apply}</td></tr>`;
}

function resultRun(run, testing) {
  const name = `Run of ${run.time}`;
  const ended = LT_ENDED[run.status] || run.status;
  const heads = ["Size", "Status", "Read tok/s", "First token", "Write tok/s",
    "Peak memory", "Settings used"].map((h) => `<th scope="col">${h}</th>`).join("");
  const thinking = typeof run.thinking === "boolean"
    ? `, thinking ${run.thinking ? "on" : "off"}` : "";
  const named = run.engine ? engineInfo(run.engine)?.name || run.engine : "MLX";
  const engine = ` (${esc(named)})${thinking}`;
  return `<section class="panel lt-run"><h2>${esc(name)}${engine}: ${esc(ended)}` +
    `${run.stale ? ' <span class="pill warn">stale</span>' : ""}</h2>` +
    (run.stale ? '<p class="hint">Settings, software or the model changed since this run. ' +
      "Run it again before applying a size.</p>" : "") +
    `<div class="lt-table" role="region" tabindex="0" aria-label="${esc(name)}">` +
    `<table class="table"><thead><tr>${heads}<th scope="col"><span class="sr-only">Config` +
    "</span></th></tr></thead><tbody>" +
    (run.rows.map((row) => resultRow(run, row, testing)).join("") ||
      '<tr><td colspan="8">No size has finished.</td></tr>') +
    "</tbody></table></div>" +
    `<p class="hint">Server flags: <span class="mono">${esc((run.flags || []).join(" "))}` +
    "</span></p>" +
    `<p class="hint">${run.engine ? "" : "Apply saves the size as this model's max context " +
      "(one-model mode). "}A pass is a measurement, not a guarantee.</p></section>`;
}

function paintResults() {
  const { view } = state.lt;
  if (!view) return;
  const testing = !!state.snapshot?.loadtest?.active;
  const [last, ...older] = [...view.results].reverse();
  paintOutcome(last);
  const box = $("#ltResults");
  const drawn = setHTML(box, !last ? '<p class="hint">No results for this model yet.</p>'
    : resultRun(last, testing) + (older.length
      ? `<details class="lt-history"><summary>Earlier runs (${older.length})</summary>` +
        `${older.map((run) => resultRun(run, testing)).join("")}</details>` : ""));
  if (!drawn) return;
  box.querySelectorAll("[data-apply]").forEach((button) => button.addEventListener(
    "click", () => applyLoadTest(button.dataset.run, Number(button.dataset.apply))));
}

function renderLoadTest(snap) {
  const lt = state.lt;
  const run = snap.loadtest;
  const testing = !!run?.active;
  const ready = state.readyModels || [];
  // A running test is shown whatever was picked before.
  const repo = testing ? run.repo : ready.includes(lt.repo) ? lt.repo
    : ready.includes(run?.repo) ? run.repo : ready[0] || "";
  const moved = repo !== lt.repo;
  if (moved) setLoadTestModel(repo);
  setModelPick("#ltModelSelect", ready, repo);
  const signature = JSON.stringify([repo, snap.server?.running, snap.server?.starting,
    snap.install?.running, (snap.downloads || []).find((d) => d.repo === repo)?.status,
    run?.run_id, run?.status, run?.active, run?.size]);
  const changed = signature !== lt.signature;
  lt.signature = signature;
  // What blocked a start may be over: it is asked for again below.
  if (changed && lt.view) lt.view = { ...lt.view, blocked: null };
  paintProgress(run);
  paintLoadTestControls();
  if (state.view === "loadtest" && (changed || (!testing && lt.view?.blocked))) loadLoadTest();
}

// The typed size: {size: null} when the field is empty.
function typedSize() {
  const input = $("#ltCustom");
  const raw = input.value.trim();
  if (raw === "" && !input.validity?.badInput) return { size: null };
  const { min, max } = state.lt.view.sizes;
  const size = Number(raw);
  return Number.isInteger(size) && size >= min && size <= max && raw !== "" ? { size }
    : { problem: `Custom size: use a whole number from ${min} to ${max}.` };
}

function keepSize(size) {
  state.lt.extra.push(size);
  state.lt.picked.add(size);
  $("#ltCustom").value = "";
  paintLoadTestControls();
}

function addCustomSize() {
  if (!state.lt.view) return;
  const { size, problem } = typedSize();
  if (problem || size === null) {
    const { min, max } = state.lt.view.sizes;
    ltError(problem || `Custom size: use a whole number from ${min} to ${max}.`);
    $("#ltCustom").focus();
    return;
  }
  ltError("");
  keepSize(size);
}

// No engine named means MLX.
function ltRequest(repo, sizes) {
  const engine = $("#ltEngine").value;
  const request = engine && engine !== "mlx" ? { repo, sizes, engine } : { repo, sizes };
  if (state.lt.view?.thinking?.supported) request.thinking = state.lt.thinking !== false;
  return request;
}

async function startLoadTest() {
  const lt = state.lt;
  if (!lt.view || lt.sending || $("#ltStartBtn").disabled) return;
  const typed = typedSize();
  if (typed.problem) return ltError(typed.problem);
  const { max } = lt.view.sizes;
  const sizes = [...new Set([...lt.picked, ...(typed.size ? [typed.size] : [])])]
    .filter((size) => size <= max).sort((a, b) => a - b);
  if (!sizes.length) return ltError("Choose at least one context size.");
  if (sizes.length > LT_MAX_SIZES) {
    return ltError(`Choose at most ${LT_MAX_SIZES} sizes; ${sizes.length} are chosen.`);
  }
  const values = {};
  for (const key of LT_NUMBERS) {
    const { min, max: most } = lt.view.load_test[key];
    const input = $(`#lt-${key}`);
    const raw = input.value.trim();
    const value = raw === "" ? null : Number(raw);
    if (input.validity?.badInput || (value !== null &&
      !(Number.isInteger(value) && value >= min && value <= most))) {
      return ltError(`Run values, ${key.replace(/_/g, " ")}: use a whole number from ` +
        `${min} to ${most}, or leave it empty.`);
    }
    values[key] = value;
  }
  if (typed.size) keepSize(typed.size);
  ltError("");
  lt.sending = true;
  try {
    await postSettings({ load_test: { ...values, last_sizes: sizes } });
    await api("/api/loadtest/start",
      { method: "POST", body: JSON.stringify(ltRequest(lt.repo, sizes)) });
    lt.loaded = {};
    toast("Load test started");
  } catch (err) {
    ltError(err.message);
  }
  lt.sending = false;
  refreshState();
  await loadLoadTest();
}

async function cancelLoadTest() {
  if ($("#ltCancelBtn").getAttribute("aria-disabled") === "true") return;
  try { await api("/api/loadtest/cancel", { method: "POST", body: "{}" }); }
  catch (err) { toast(err.message, true); }
  refreshState();
}

async function applyLoadTest(runId, size) {
  const { repo } = state.lt;
  if (state.config.repo === repo && configDirty()) {
    if (!confirm("Discard your unsaved Configuration changes for this model?")) return;
    clearConfig();
  }
  try {
    await api("/api/loadtest/apply",
      { method: "POST", body: JSON.stringify({ repo, run_id: runId, size }) });
  } catch (err) {
    ltError(err.message);
    toast(err.message, true);
    return;
  }
  state.estimateKey = null;
  toast(`Saved ${size} tokens as max context. It applies at the next server start.`);
  refreshState();
  await loadLoadTest();
}

const acceptsNothing = (server, repo) =>
  !!server?.running && server.prompt_limits?.[repo] === 0;

// Said for a running server that refuses every prompt of a model, else empty.
function noPromptWords(server) {
  const names = Object.keys(server?.prompt_limits || {})
    .filter((repo) => acceptsNothing(server, repo))
    .map((repo) => `${shortName(repo)} accepts no prompt`);
  if (!names.length) return "";
  const used = server.memory_used_percent;
  return `${names.join(", ")}. ` +
    (used == null ? "" : `Memory in use now: ${used}%. `) +
    "Close other apps (web browsers, code editors, virtual machines, other model apps), " +
    "then press Stop, then Start Server.";
}

function renderServerMemory(server) {
  const memory = server.running ? server.memory : null;
  $("#serverMemory").hidden = !memory;
  if (memory) {
    const used = `Using ${gb(memory.active_bytes)} GB (peak ${gb(memory.peak_bytes)} GB)`;
    // Only a two-model server is held to a budget.
    setText($("#serverMemory"), holdsTwo(server)
      ? `${used} of a ${gb(memory.budget_bytes)} GB budget. ` +
        "Measured by the API server, refreshed about every 10 s."
      : `${used}. Measured at the last model load.`);
  }
  const tokens = holdsTwo(server) ? server.context_tokens : null;
  $("#serverPromptHint").hidden = !tokens;
  setText($("#serverPromptHint"), tokens
    ? `Keep prompts to ${tokens} tokens or fewer. The prompt cache holds about ` +
      `${2 * tokens} tokens in total; above that, older prompts are dropped and read again.`
    : "");
  const limits = Object.entries((server.running && server.prompt_limits) || {})
    .filter(([, most]) => most > 0)
    .map(([repo, most]) => `${shortName(repo)} ${most} tokens`);
  const words = [noPromptWords(server), limits.length
    ? `Prompt limit: ${limits.join(", ")}. A longer prompt is refused at once.`
    : ""].filter(Boolean).join(" ");
  $("#serverPromptLimit").hidden = !words;
  setText($("#serverPromptLimit"), words);
  const [title, notice] = multiNotice(server);
  $("#serverMulti").hidden = !notice;
  setText($("#serverMultiTitle"), title);
  setText($("#serverMultiText"), notice);
}

const ESTIMATE_RETRY_MS = 10000;

// Asked again when the choice changes, and a while after the last answer, as the free
// memory moves. An unchanged running server keeps its
// answer; an edit waiting for Apply does not.
async function refreshEstimate(snap) {
  const choice = serverChoice();
  const key = choice.repo ? JSON.stringify(choice) : "";
  const same = key === (state.estimateKey || "");
  const settled = !!snap?.server?.running && !serverPending();
  if (same && (!(Date.now() >= state.estimateRetryAt) || (settled && state.estimated))) return;
  state.estimateKey = key;
  state.estimateRetryAt = Infinity;
  if (!same) {
    state.estimated = false;
    showEstimate(null, "");
  }
  if (!key) return;
  try {
    const estimate = await api("/api/server/estimate", {
      method: "POST",
      body: JSON.stringify(choice),
    });
    if (state.estimateKey !== key) return;
    state.estimateRetryAt = Date.now() + ESTIMATE_RETRY_MS;
    state.estimated = true;
    showEstimate(estimate);
  } catch (err) {
    if (state.estimateKey !== key) return;
    state.estimated = false;
    state.estimateRetryAt = Date.now() + ESTIMATE_RETRY_MS;
    showEstimate(null, err.message);
  }
}

const FULL_CONTEXT = 32768;

// What the picked pair will get, or why it cannot be started.
function estimateWords(estimate) {
  const unread = estimate.free_bytes === null;
  const fallback = unread ? " Free memory could not be read; using the fixed " +
    `${estimate.budget_percent ?? 52}% rule.` : "";
  if (estimate.fits === false) return `${estimate.refused}${fallback}`;

  let uses;
  if (!unread && estimate.held_bytes > 0) {
    // A running server's memory is not in the free figure, and a new start frees it.
    const available = estimate.free_bytes + estimate.held_bytes;
    const need = estimate.total_bytes;
    if (need > available) {
      uses = `Needs about ${gb(need)} GB, ${gb(available)} GB would be available.`;
    } else {
      uses = `Uses about ${gb(need)} of ${gb(available)} GB available.`;
    }
  } else {
    uses = `Uses about ${gb(estimate.total_bytes)}` +
      (unread ? " GB." : ` of ${gb(estimate.free_bytes)} GB free.`);
  }

  if (estimate.context_tokens >= FULL_CONTEXT) {
    return `Two models: 32K tokens each. ${uses}${fallback}`;
  }
  if (estimate.capped_by_setting) {
    return `Two models: about ${Math.floor(estimate.context_tokens / 1000)}K tokens each ` +
      `(your Settings cap). ${uses}${fallback}`;
  }
  const shrinkNote = estimate.free_is_limit ? " Close other apps for more." : "";
  return `Two models: about ${Math.floor(estimate.context_tokens / 1000)}K tokens each ` +
    `(one model alone can use more). ${uses}` +
    (unread ? fallback : shrinkNote);
}

function showEstimate(estimate, error = "") {
  state.estimate = estimate;
  state.estimateError = error;
  setText($("#serverContextWarn"), estimate?.context_warning || "");
  paintServerCard();
}

const kilo = (tokens) => Math.floor(tokens / 1024);

// What one row can promise about its context; empty when nothing is known.
function contextWords(repo, view) {
  // The running server's own figure beats an older estimate.
  if (acceptsNothing(view.server, repo)) return "accepts no prompt";
  const found = view.estimate?.models?.find((m) => m.repo === repo);
  let tokens = view.estimate?.fits === false && view.two ? null : found?.safe_context;
  if (view.live && view.two && holdsTwo(view.server) && view.server.context_tokens) {
    tokens = Math.min(view.server.context_tokens, found?.context_limit || Infinity);
  }
  return tokens >= 1024 ? `about ${kilo(tokens)}K context` : "";
}

// The one thing to read without opening Details: why the button is off, or a warning.
function serverReason(view) {
  const { server, set, two, live, estimate, refused } = view;
  if (server.starting) return "Starting the server…";
  if (state.serverStartError) return state.serverStartError;
  const closed = noPromptWords(server);
  if (closed) return closed;
  if (!set.length) {
    return server.running ? "Add a model, or press Stop." : "Add a model to start the server.";
  }
  if (state.estimateError) return `No memory estimate: ${state.estimateError}`;
  if (refused) return estimateWords(estimate);
  if (two) {
    const running = live && holdsTwo(server);
    const tokens = running ? server.context_tokens
      : !live && estimate?.fits !== false ? estimate?.context_tokens : null;
    if (!tokens || tokens >= FULL_CONTEXT) return "";
    const more = !running && estimate.free_is_limit && estimate.free_bytes !== null;
    return `Shorter context: about ${Math.floor(tokens / 1000)}K tokens per model.` +
      (more ? " Close other apps for more." : "");
  }
  // One model is never refused: the server may still run it with less room.
  if (live || estimate?.fits !== false) return "";
  const safe = estimate.models?.[0]?.safe_context;
  return safe >= 8192
    ? `Shorter context: about ${kilo(safe)}K tokens.`
    : `This model may not fit: it needs about ${gb(estimate.total_bytes)} GB and ` +
      `the API server may use ${gb(estimate.budget_bytes)} GB.`;
}

// A line inside the card, hidden while it has nothing to say.
function showLine(id, words) {
  $(id).hidden = !words;
  setText($(id), words);
}

function paintServerCard() {
  const server = state.server;
  if (!server) return;
  const set = serverSet();
  const two = set.length > 1;
  const pending = serverPending();
  // The card lists exactly what the running server holds.
  const live = !!server.running && sameSet(set, runningSet(server));
  const estimate = state.estimate;
  // A running pair is not called refused: its own memory is what is in use.
  const refused = two && estimate?.fits === false && (!server.running || pending);
  const view = { server, set, two, live, estimate, refused };

  const list = $("#serverModelList");
  const drawn = setHTML(list, set.map((repo) => {
    const context = contextWords(repo, view);
    return `<li><span class="model-name" title="${esc(repo)}">${esc(shortName(repo))}</span>` +
      (context ? `<span class="model-context">${context}</span>` : "") +
      `<button class="btn btn-mini btn-ghost" type="button" data-remove="${esc(repo)}" ` +
      `aria-label="Remove ${esc(repo)}">×</button></li>`;
  }).join(""));
  if (drawn) {
    list.querySelectorAll("[data-remove]").forEach((btn) => {
      btn.addEventListener("click", () => removeServerModel(btn.dataset.remove));
    });
  }
  $("#serverModelEmpty").hidden = set.length > 0;

  const ready = state.readyModels || [];
  const others = ready.filter((repo) => !set.includes(repo));
  fillSelect("#serverModelSelect", others, null);
  // Nothing is preselected: every pick in the list adds that model.
  $("#serverModelSelect").value = "";
  const noAdd = set.length >= MAX_SERVER_MODELS
    ? "Two models is the maximum. Remove one to add another."
    : !ready.length ? "No models installed. Get one on the Download page."
      : !others.length ? "No other installed model to add." : "";
  $("#serverAddBtn").disabled = !!noAdd;
  setText($("#serverAddWhy"), noAdd);
  if (noAdd) showAddPicker(false);

  const measured = live ? server.memory : null;
  const used = measured ? measured.active_bytes : estimate?.total_bytes;
  const allowed = measured ? measured.budget_bytes : estimate?.budget_bytes;
  // A refusal gives its own figures; this line beside them reads as a contradiction.
  const memory = !refused && set.length && used != null && allowed != null
    ? `Memory: ${gb(used)} / ${gb(allowed)} GB` : "";
  showLine("#serverMemoryLine", memory);
  showLine("#serverMemoryHint", !memory ? "" : measured
    ? "Memory: used now, of what the API server may use."
    : "Memory: what this choice needs, of what the API server may use.");

  const reason = serverReason(view);
  showLine("#serverWhy", reason);
  // The status region is always in the page, so a screen reader hears its changes.
  setText($("#serverStartWhy"), reason);

  paintEngine(set);
  // The built-in server's memory figures say nothing about another engine.
  if (engineInfo()?.direct) {
    showLine("#serverMemoryLine", set.length ? "Memory limit: not available with this engine." : "");
    for (const id of ["#serverMemoryHint", "#serverWhy", "#serverEstimate", "#serverContextHint"]) {
      showLine(id, "");
    }
    setText($("#serverStartWhy"), "");
  }

  // A refusal is already on screen; a running server is not described as refused.
  const details = !set.length || !estimate || (two && estimate.fits === false) ? ""
    : two ? estimateWords(estimate)
      : `Needs about ${gb(estimate.total_bytes)} GB with a ` +
        `${Math.floor(estimate.context_tokens / 1000)}K-token reply; the API server may use ` +
        `${gb(estimate.budget_bytes)} GB.`;
  showLine("#serverEstimate", engineInfo()?.direct ? "" : details);
  showLine("#serverContextHint",
    engineInfo()?.direct || !two || !estimate || estimate.fits === false ? "" :
    "Safe context while both are loaded (estimate): " +
    estimate.models.map((m) => {
      if (m.safe_context == null) return `${shortName(m.repo)} unknown`;
      const limit = m.safe_context === m.context_limit ? " (model limit)" : "";
      return `${shortName(m.repo)} about ${kilo(m.safe_context)}K${limit}`;
    }).join("; ") + ". Set your client's context window to this or lower.");
  $("#serverPairHint").hidden = !two;
  $("#serverDetails").hidden = !set.length && !server.running;

  const start = $("#serverStartBtn");
  const stop = $("#serverStopBtn");
  const focused = document.activeElement;
  setText(start, server.starting ? "Starting…" : server.running ? "Apply changes" : "Start Server");
  const blocked = engineInfo()?.direct ? !!engineReason(set) : refused;
  start.disabled = !!server.starting || !set.length || (server.running && !pending) || blocked;
  if (engineInfo()?.direct && state.serverStartError) showLine("#serverWhy", state.serverStartError);
  stop.hidden = !server.running && !server.starting;
  // Focus must not stay on a button that can no longer be used, nor move from Start
  // to Stop, where a second Enter would stop the server.
  if (focused === start && start.disabled) spareControl().focus();
  if (focused === stop && stop.hidden) (start.disabled ? spareControl() : start).focus();
  setText($("#serverApplyNote"), pending && set.length && !server.starting
    ? "Applying restarts the server: a reply in progress will be interrupted." : "");
}

function showAddPicker(show) {
  $("#serverAddPicker").hidden = !show;
  $("#serverAddBtn").setAttribute("aria-expanded", String(show));
}

// Where focus goes when its button cannot be used: never Start or Stop.
function spareControl() {
  const add = $("#serverAddBtn");
  return add.disabled ? $("#serverBackground") : add;
}

// A start has been sent and the server it makes is not on screen yet.
const startOnItsWay = () => !!(state.serverSending || state.server?.starting);

// An edit of the card. Back to what runs, the card follows the server again; while
// a start is on its way the old server is about to go, so the edit is kept.
function editServerSet(next) {
  const server = state.server;
  const busy = startOnItsWay();
  if (busy) state.keptSet = true;
  state.serverDraft =
    !busy && server?.running && sameSet(next, runningSet(server)) ? null : next;
  serverCardChanged();
}

function serverCardChanged() {
  state.serverStartError = "";
  refreshEstimate(state.snapshot);
  paintServerCard();
  if (state.snapshot) renderToolsWarning(state.snapshot.server);
}

function addServerModel() {
  const repo = $("#serverModelSelect").value;
  const set = serverSet();
  if (!repo || set.includes(repo) || set.length >= MAX_SERVER_MODELS) return;
  showAddPicker(false);
  editServerSet([...set, repo]);
  setText($("#serverSetStatus"), `Added ${shortName(repo)}.`);
  const add = $("#serverAddBtn");
  const row = Array.from($("#serverModelList").querySelectorAll("[data-remove]"))
    .find((btn) => btn.dataset.remove === repo);
  (add.disabled && row ? row : add).focus();
}

function removeServerModel(repo) {
  editServerSet(serverSet().filter((other) => other !== repo));
  setText($("#serverSetStatus"), `Removed ${shortName(repo)}.`);
  const next = $("#serverModelList").querySelectorAll("[data-remove]")[0];
  (serverSet().length && next ? next : $("#serverAddBtn")).focus();
}

// Warn only for models marked as unable to call tools; unmarked means unknown.
function renderToolsWarning(server) {
  const support = server.tool_support || {};
  const choice = serverChoice();
  const repos = server.running
    ? (server.repos?.length ? server.repos : [server.repo])
    : [choice.repo, ...(choice.also || [])];
  const without = repos.filter((repo) => support[repo] === false);
  const warn = without.length > 0;
  $("#serverToolsWarn").hidden = !warn;
  if (!warn) return;
  const verified = Object.keys(support).find((r) => support[r]);
  setText($("#serverToolsWarnText"),
    (repos.length > 1 ? `${without.map(shortName).join(", ")}: ` : "") +
    "Agent clients will not get tool calls from it; they may show them as plain text." +
    (verified ? ` For tool use, choose ${verified}.` : ""));
}

function fillSelect(sel, values, current) {
  const el = $(sel);
  if (!el) return;
  const prev = el.value;
  const rebuilt = setHTML(el, values.length
    ? values.map((v) => `<option value="${esc(v)}">${esc(v)}</option>`).join("")
    : `<option value="">No models installed</option>`);
  // Follow the app only when its model changes; otherwise keep the user's choice.
  const moved = el._current !== current;
  el._current = current;
  if (rebuilt || moved) {
    const want = (moved && current) || prev || current;
    if (want && values.includes(want)) el.value = want;
  }
  pickers[sel]?.sync(values);
}

/* ───────────── searchable model dropdown ───────────── */
// A search box and list in front of a hidden <select>, which stays the value the
// app reads. Polling never overwrites an open list's typed text or highlight.
const pickers = {};

function createPicker(sel, { onLeave } = {}) {
  const select = $(sel);
  const box = $(`${sel}Search`);
  const list = $(`${sel}List`);
  const status = $(`${sel}Status`);
  const clear = $(`${sel}Clear`);
  const chosen = $(`${sel}Chosen`);
  const id = sel.slice(1);
  let values = [];
  let shown = [];
  let query = "";
  let active = "";

  function draw() {
    const words = query.toLowerCase().split(/\s+/).filter(Boolean);
    shown = values.filter((v) => words.every((w) => v.toLowerCase().includes(w)));
    if (!shown.includes(active)) {
      active = shown.includes(select.value) ? select.value : shown[0] || "";
    }
    setHTML(list, shown.length
      ? shown.map((v, i) => `<li id="${id}-opt-${i}" role="option" aria-selected="${v === active}" class="${v === select.value ? "chosen" : ""}" data-value="${esc(v)}" title="${esc(v)}">${esc(v)}</li>`).join("")
      : `<li class="picker-empty" role="presentation">No models match</li>`);
    if (active) box.setAttribute("aria-activedescendant", `${id}-opt-${shown.indexOf(active)}`);
    else box.removeAttribute("aria-activedescendant");
    const n = shown.length;
    setText(status, n ? `${n} model${n === 1 ? " matches" : "s match"}` : "No models match");
  }

  function reveal() {
    list.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
  }

  // The box only ever holds the search text; the choice is written beside it.
  function showState() {
    clear.hidden = !box.value;
    if (onLeave) return; // an "add" picker keeps no choice to show
    setText(chosen, `Selected: ${select.value || "none"}`);
    chosen.title = select.value;
  }

  function open(text = "") {
    if (!values.length) return;
    query = text;
    active = text ? "" : select.value;
    list.hidden = false;
    box.setAttribute("aria-expanded", "true");
    showState();
    draw();
    reveal();
  }

  function close() {
    const wasOpen = !list.hidden;
    list.hidden = true;
    box.setAttribute("aria-expanded", "false");
    box.removeAttribute("aria-activedescendant");
    if (wasOpen) setText(status, "");
    if (box.value) box.value = "";
    showState();
  }

  function choose(value) {
    select.value = value;
    select.dispatchEvent(new Event("change"));
    close();
    setText(status, `Selected ${value}`);
  }

  box.addEventListener("click", () => {
    if (list.hidden) open(box.value);
  });
  box.addEventListener("blur", () => {
    close();
    onLeave?.(false);
  });
  box.addEventListener("input", () => open(box.value));
  box.addEventListener("keydown", (e) => {
    if (e.isComposing) return;
    if (e.key === "Escape" && onLeave) {
      e.preventDefault();
      close();
      onLeave(true);
    } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (list.hidden) { open(); return; }
      if (!shown.length) return;
      const step = e.key === "ArrowDown" ? 1 : -1;
      active = shown[(shown.indexOf(active) + step + shown.length) % shown.length];
      draw();
      reveal();
    } else if (list.hidden) {
      return;
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (active) choose(active);
    } else if (e.key === "Escape") {
      e.preventDefault();
      close();
    } else if (e.key === "Tab") {
      close();
    }
  });
  clear.addEventListener("mousedown", (e) => e.preventDefault());
  clear.addEventListener("click", () => {
    box.value = "";
    box.focus();
    open();
  });
  // Keeps focus in the search box while an option or the scrollbar is pressed.
  list.addEventListener("mousedown", (e) => e.preventDefault());
  list.addEventListener("click", (e) => {
    const option = e.target.closest("[data-value]");
    if (option) choose(option.dataset.value);
  });

  pickers[sel] = {
    open() { open(); },
    sync(next) {
      values = next;
      box.readOnly = !values.length;
      box.placeholder = values.length ? "Search models…" : "No models installed";
      if (list.hidden || !values.length) close();
      else draw();
    },
  };
}

/* ───────────── model cards ───────────── */
const FIT_LABEL = { great: "great fit", good: "good fit", tight: "tight fit", "too-large": "too large", unknown: "fit unknown" };

function fitBadge(fit) {
  return fit ? `<span class="fit fit-${esc(fit)}">${esc(FIT_LABEL[fit] || fit)}</span>` : "";
}

// Empty values are left out, so unknown models never show blank metadata.
function metaLine(...parts) {
  const text = parts.filter(Boolean).map(esc).join(" · ");
  return text ? `<div class="card-sub">${text}</div>` : "";
}

function modelCard(repo, badge, body, actions) {
  return `
    <div class="card">
      <div class="card-top">
        <div>
          <div class="card-name">${esc(repo.split("/").pop())}</div>
          <div class="card-sub">${esc(repo)}</div>
        </div>
        ${badge}
      </div>
      ${body}
      <div class="card-actions">${actions}</div>
    </div>`;
}

/* ───────────── models view ───────────── */
function renderModelCards(models, server, downloads) {
  const wrap = $("#modelCards");
  if (!wrap) return;
  const busy = new Set((downloads || []).filter((d) => d.status === "running").map((d) => d.repo));
  const shown = models.filter((m) => !busy.has(m.repo));
  $("#navModelCount").textContent = shown.length;
  $("#modelsSummary").textContent = `${shown.length} model(s)`;
  $("#modelsEmpty").hidden = shown.length > 0;
  const catalog = new Map((state.catalog || []).map((m) => [m.repo, m]));

  const changed = setHTML(wrap, shown.map((m) => {
    const info = catalog.get(m.repo) || {};
    const served = !!server.running && (server.repos || []).includes(m.repo);
    const partial = m.complete === false;
    const badge = served ? '<span class="card-tag ok">served</span>'
      : partial ? '<span class="card-tag bad">incomplete</span>' : fitBadge(info.fit);
    const button = (act, label, extra = "", slot = "") =>
      `<button class="btn btn-mini ${extra}" data-act="${act}" data-repo="${esc(m.repo)}"${slot}>${label}</button>`;
    const MAIN = ' data-slot="main"';
    return modelCard(
      m.repo,
      badge,
      `${metaLine(m.size_h, info.params, info.category)}
       ${metaLine(info.use)}
       <div class="card-path" title="${esc(m.path)}">📁 ${esc(m.path)}</div>`,
      (partial ? button("download", "Resume download", "", MAIN) : "") +
        button("reveal", "Reveal") +
        copyButton(m.repo) +
        button("delete", "Delete", "btn-danger"),
    );
  }).join(""));

  if (changed) {
    wrap.querySelectorAll("[data-act]").forEach((btn) => {
      btn.addEventListener("click", () => modelAction(btn.dataset.act, btn.dataset.repo));
    });
  }
}

async function modelAction(act, repo) {
  if (act === "delete" && !confirm(`Delete ${repo} from disk?`)) return;
  try {
    await api(`/api/${act}`, { method: "POST", body: JSON.stringify({ repo }) });
    if (act === "delete") toast("Model deleted");
    if (act === "download") showView("download");
    refreshState();
  } catch (err) { toast(err.message, true); }
}

async function refreshModels() { await refreshState(); }

/* ───────────── catalogue ───────────── */
async function loadCatalog() {
  try {
    const { catalog, hardware } = await api("/api/catalog");
    state.catalog = catalog;
    $("#catalogHardware").textContent =
      `${hardware.chip} · ${hardware.ram} unified — rated on 4-bit weight size`;
    renderCatalog();
  } catch (err) { toast(err.message, true); }
}

function renderCatalog() {
  const wrap = $("#catalogCards");
  if (!wrap || !state.catalog) return;
  const filter = $("#catalogFilter").value || "all";
  const rows = state.catalog.filter((m) =>
    filter === "all" || m.fit === filter || m.category === filter);
  $("#catalogEmpty").hidden = rows.length > 0;
  renderOfferCards(wrap, rows);
}

function renderSearch() {
  if (state.search) renderOfferCards($("#searchResults"), state.search);
}

// A model that can be downloaded: recommended and search cards are the same card.
function offerCard(m, busy, ready) {
  const running = busy.has(m.repo);
  const disabled = m.fit === "too-large";
  const label = running ? "Downloading…" : disabled ? "Needs more RAM" : "Download";
  // The snapshot is live; the catalogue flag is only used before the first one.
  const installed = state.snapshot ? ready.has(m.repo) : m.installed;
  const slot = `data-slot="main" data-repo="${esc(m.repo)}"`;
  const size = !m.size_gb ? "size unknown"
    : m.size_estimated ? `~${m.size_gb} GB (estimate)` : `${m.size_gb} GB`;
  return modelCard(
    m.repo,
    fitBadge(m.fit || "unknown"),
    `${metaLine(m.params || "params unknown", size, m.category || "category unknown")}
       ${metaLine(m.use || "use unknown")}`,
    (!running && installed
      // aria-disabled keeps the focus the Download button had.
      ? `<button class="btn btn-mini" ${slot} aria-disabled="true">Installed</button>`
      // aria-disabled, not disabled: a busy button must keep keyboard focus.
      : `<button class="btn btn-mini btn-primary" ${slot} data-cat-download="${esc(m.repo)}" ${disabled ? "disabled" : running ? 'aria-disabled="true"' : ""}>${label}</button>`) +
      copyButton(m.repo),
  );
}

function renderOfferCards(wrap, rows) {
  // Button state comes from the latest snapshot, so a cancel or failure restores it.
  const busy = new Set((state.snapshot?.downloads || [])
    .filter((d) => d.status === "running").map((d) => d.repo));
  const ready = new Set((state.snapshot?.models || [])
    .filter((m) => m.complete !== false).map((m) => m.repo));
  if (!setHTML(wrap, rows.map((m) => offerCard(m, busy, ready)).join(""))) return;

  wrap.querySelectorAll("[data-cat-download]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (btn.getAttribute("aria-disabled") === "true") return;
      btn.setAttribute("aria-disabled", "true");
      btn.textContent = "Downloading…";
      // The button no longer matches the cached markup: the next render redraws it.
      wrap._lastHtml = null;
      try {
        // Switch to Download view so user can see progress
        showView("download");
        await api("/api/download", { method: "POST", body: JSON.stringify({ repo: btn.dataset.catDownload }) });
        toast("Download started");
        refreshState();
      } catch (err) {
        toast(err.message, true);
        btn.removeAttribute("aria-disabled");
        btn.textContent = "Download";
      }
    });
  });
}

/* ───────────── downloads ───────────── */
// Shown in the header, so a collapsed section still reports progress.
function downloadSummary(downloads) {
  const parts = [`${downloads.length} download${downloads.length !== 1 ? "s" : ""}`];
  const running = downloads.filter((d) => d.status === "running");
  if (running.length) {
    const pct = Math.round(running.reduce((sum, d) => sum + (d.pct || 0), 0) / running.length);
    parts.push(`${running.length} running, ${running.length > 1 ? "average " : ""}${pct}%`);
  }
  for (const [status, label] of [["success", "complete"], ["failed", "failed"], ["cancelled", "cancelled"]]) {
    const n = downloads.filter((d) => d.status === status).length;
    if (n) parts.push(`${n} ${label}`);
  }
  return parts.join(" · ");
}

// Closed until the user opens it; polling never changes the choice.
function setDownloadsOpen(open) {
  state.downloadsOpen = open;
  $("#downloadCards").classList.toggle("collapsed", !open);
  $("#downloadEmpty").classList.toggle("collapsed", !open);
  $("#toggleDownloadsIcon").textContent = open ? "▼" : "▶";
  $("#toggleDownloads").setAttribute("aria-expanded", open);
}

function renderDownloads(downloads) {
  const wrap = $("#downloadCards");
  if (!wrap) return;
  const downloadCount = document.getElementById("downloadCount");
  if (downloadCount) {
    setText(downloadCount, downloadSummary(downloads || []));
  }
  if (!downloads || !downloads.length) {
    document.getElementById("downloadEmpty").hidden = false;
    document.getElementById("downloadCards").hidden = true;
    return;
  }
  document.getElementById("downloadEmpty").hidden = true;
  document.getElementById("downloadCards").hidden = false;
  downloads.forEach((d) => {
    let card = wrap.querySelector(`[data-dl="${CSS.escape(d.repo)}"]`);
    if (!card) {
      card = document.createElement("div");
      card.className = "card";
      card.dataset.dl = d.repo;
      wrap.prepend(card);
    }
    const pct = d.pct || 0;
    const tag = d.status === "success" ? "ok" : d.status === "running" ? "run" : "bad";
    const progressBar = d.status === "running"
      ? `<div class="progress-bar" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}" aria-label="Download progress for ${esc(d.repo)}"><div class="progress-fill" style="width:${pct}%"></div><span class="progress-pct">${pct}%</span></div>`
      : d.status === "success"
      ? `<div class="progress-done">✓ Complete</div>`
      : "";
    // Show the on-disk path once the download has finished.
    const installed = (state.snapshot?.models || []).find((m) => m.repo === d.repo);
    const pathLine = d.status === "success" && installed
      ? `<div class="card-path" title="${esc(installed.path)}">📁 ${esc(installed.path)}</div>`
      : "";
    const cancel = d.status === "running"
      ? `<button class="btn btn-mini" data-cancel="${esc(d.repo)}" aria-label="Cancel download of ${esc(d.repo)}">Cancel</button>`
      : "";
    const changed = setHTML(card, `
      <div class="card-top">
        <div>
          <div class="card-name">${esc(d.repo.split("/").pop())}</div>
          <div class="card-sub">${esc(d.repo)}</div>
        </div>
        <span class="card-tag ${tag}">${esc(d.status)}</span>
      </div>
      <div class="card-sub">${esc(d.detail || "")}</div>
      ${progressBar}
      ${pathLine}
      <div class="card-actions">${cancel}${copyButton(d.repo)}</div>`);
    if (changed) {
      card.querySelectorAll("[data-cancel]").forEach((btn) => {
        btn.addEventListener("click", async () => {
          try {
            await api("/api/download/cancel", { method: "POST", body: JSON.stringify({ repo: btn.dataset.cancel }) });
            refreshState();
          } catch (err) { toast(err.message, true); }
        });
      });
    }
  });
}

async function doSearch(q) {
  const wrap = $("#searchResults");
  const seq = ++state.searchSeq; // only the newest search may draw
  const show = (html) => { wrap._lastHtml = null; wrap.innerHTML = html; };
  const nothing = (title, text) => {
    show("");
    $("#searchEmpty").hidden = false;
    $("#searchEmpty").querySelector("h2").textContent = title;
    $("#searchEmpty").querySelector("p").textContent = text;
  };
  state.search = null;
  show(`<div class="card"><div class="card-sub">Searching…</div></div>`);
  $("#searchEmpty").hidden = true;
  try {
    const { results } = await api(`/api/search?q=${encodeURIComponent(q)}`);
    if (seq !== state.searchSeq) return;
    if (!results.length) { nothing("No results", "Try a different query."); return; }
    state.search = results;
    renderSearch();
  } catch (err) {
    if (seq !== state.searchSeq) return;
    nothing("Search failed", "Check the connection and try again.");
    toast(err.message, true);
  }
}

/* ───────────── chat model ───────────── */
// Chat asks the running API server, so only the models it serves can be chosen.
const CHAT_PLACEHOLDER = "Send a message…  (Enter to send, Shift+Enter for newline)";
// status: [dot, short text, longer text, placeholder, offer the API Server page]
const CHAT_STATUS = {
  ready: ["dot-ok", "Ready", "", CHAT_PLACEHOLDER, false],
  starting: ["dot-live", "Starting…",
    "The API server is starting. You can type when it is ready.",
    "Waiting for the API server…", false],
  restarting: ["dot-live", "Restarting…",
    "The API server is restarting. You can type when it is ready.",
    "Waiting for the API server…", false],
  stopped: ["dot-warn", "Server stopped",
    "Start the API server first. Chat uses the models it serves.",
    "Start the API server first…", true],
  failed: ["dot-bad", "Model not loaded",
    "The API server could not load its model. On the API Server page, press Stop, then Start Server.",
    "Model not loaded: press Stop, then Start Server", true],
};

function chatStatus(server) {
  if (server.starting) return server.running ? "restarting" : "starting";
  if (!server.running) return "stopped";
  if (server.load_failed) return "failed";
  return server.ready ? "ready" : "starting";
}

function renderChat(server) {
  const status = chatStatus(server);
  const served = server.running ? server.repos || [] : [];
  // The choice stays while it is still served; a server on its way does not change it.
  const chosen = served.includes(state.chatModel) ? state.chatModel : served[0] || "";
  if (status === "ready") state.chatModel = chosen;
  const select = $("#chatModelSelect");
  setHTML(select, served.length
    ? served.map((v) => `<option value="${esc(v)}">${esc(v)}</option>`).join("")
    : '<option value="">No model served</option>');
  select.value = chosen;
  const ready = status === "ready";
  select.disabled = !ready;

  const [dot, short, long, placeholder, offer] = CHAT_STATUS[status];
  setHTML($("#chatState"), `<span class="dot ${dot}"></span>${short}`);
  $("#chatGate").hidden = ready;
  setText($("#chatGateText"), long);
  $("#chatGoServer").hidden = !offer;
  // Disabling keeps the typed text; the conversation is not touched.
  const input = $("#input");
  input.disabled = !ready;
  input.placeholder = placeholder;
  $("#sendBtn").disabled = !ready;
  renderChatMode(server);
  const became = ready && state.chatStatus !== "ready";
  state.chatStatus = status;
  const idle = !document.activeElement || document.activeElement === document.body;
  if (became && idle && $("#view-chat").classList.contains("active")) input.focus();

  $("#globalDot").className = `dot ${dot}`;
  setText($("#globalStatus"), ready ? served.map(shortName).join(", ")
    : server.running || server.starting ? short : "No model served");
}

// The mode the server reports for the chosen model; never guessed from what is saved.
function renderChatMode(server) {
  const perf = server?.performance;
  $("#chatMode").hidden = !perf;
  if (!perf) return;
  const model = server.running ? perf.models[$("#chatModelSelect").value] : null;
  const direct = !!server.engines?.find((e) => e.id === server.engine)?.direct;
  // OptiQ reports no mode back: only what is saved can be said.
  const unreported = !direct ? "not reported yet"
    : model ? `${model.saved} (saved; not reported by OptiQ)` : "not reported by OptiQ";
  setText($("#chatModeName"), server.running
    ? model?.in_force || unreported : `${perf.saved} (used at the next Start)`);
  const behind = server.settings_pending && model?.in_force && model.saved !== model.in_force;
  setText($("#chatModeNote"), [
    behind ? `${model.saved} is saved but not applied yet.` : "",
    model?.note || "",
    server.running && direct ? "In a long chat OptiQ drops older text." : "",
  ].filter(Boolean).join(" "));
}

/* ───────────── chat ───────────── */
function addMessage(role, text) {
  const wrap = $("#messages");
  const el = document.createElement("div");
  el.className = `msg msg-${role}`;
  const reply = role === "assistant";
  el.innerHTML = `
    <div class="msg-role">${reply ? "Assistant" : "You"}</div>
    ${reply ? `<details class="msg-think" hidden><summary>Thinking</summary>
      <div class="msg-think-text"></div></details>
    <div class="msg-wait" hidden></div>` : ""}
    <div class="msg-body"></div>
    ${reply ? '<div class="msg-note" hidden></div>' : ""}`;
  el.querySelector(".msg-body").textContent = text;
  if (reply) {
    for (const part of [".msg-think", ".msg-wait", ".msg-note"]) {
      el.querySelector(part).hidden = true;
    }
  }
  wrap.appendChild(el);
  scrollChat();
  return el;
}

function scrollChat() {
  const s = $("#chatScroll");
  s.scrollTop = s.scrollHeight;
}

// A reply's thinking and answer. The server sends thinking apart (mlx_lm calls it
// "reasoning"); some chat templates leave it in the text between <think> tags.
function splitReply(reasoning, content) {
  const text = content.trimStart();
  if (text.startsWith("<think>")) {
    const end = text.indexOf("</think>");
    return {
      thinking: reasoning + text.slice(7, end === -1 ? undefined : end),
      answer: end === -1 ? "" : text.slice(end + 8).trimStart(),
    };
  }
  // The first tokens may be the start of the tag.
  if ("<think>".startsWith(text)) return { thinking: reasoning, answer: "" };
  return { thinking: reasoning, answer: reasoning ? text : content };
}

function paintReply(el, { thinking, answer }) {
  el.querySelector(".msg-think").hidden = !thinking.trim();
  setText(el.querySelector(".msg-think-text"), thinking.trim());
  setText(el.querySelector(".msg-body"), answer);
}

function replyNote(el, text) {
  const note = el.querySelector(".msg-note");
  note.hidden = !text;
  setText(note, text);
}

// Calls onEvent(name, data) for every event of the app's reply stream.
async function readEvents(res, onEvent) {
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() || "";
    for (const frame of frames) {
      const evLine = frame.split("\n").find((l) => l.startsWith("event: "));
      const dataLine = frame.split("\n").find((l) => l.startsWith("data: "));
      if (!evLine || !dataLine) continue;
      onEvent(evLine.slice(7).trim(), JSON.parse(dataLine.slice(6)));
    }
  }
}

// The small line under a reply. A figure that is not known is left out.
function statsLine(data) {
  const s = data.stats || {};
  const known = (value, words) => (value === null || value === undefined ? "" : words);
  return [
    known(s.reply_tokens, `${s.reply_tokens} tokens`),
    known(s.first_s, `First answer after ${s.first_s} s`),
    known(s.tps, `Writing ${s.tps} tokens/s`),
    known(s.prompt_tokens, `Prompt ${s.prompt_tokens} tokens`),
    known(s.mode, `${s.mode} mode`),
    data.stopped ? "stopped" : "",
    data.truncated ? "length limit reached" : "",
  ].filter(Boolean).join(" · ");
}

async function sendMessage() {
  const input = $("#input");
  const text = input.value.trim();
  if (!text || state.streaming || state.chatStatus !== "ready") return;
  const repo = $("#chatModelSelect").value;

  input.value = "";
  input.style.height = "auto";
  $("#chatEmpty").hidden = true;

  const history = state.messages; // New chat replaces it while a reply is running
  const turn = { role: "user", content: text };
  state.messages.push(turn);
  addMessage("user", text);
  let replied = false;
  setText($("#chatAnnounce"), "Assistant is replying");

  const assistantEl = addMessage("assistant", "");
  const body = assistantEl.querySelector(".msg-body");
  const wait = assistantEl.querySelector(".msg-wait");
  body.classList.add("cursor");

  state.streaming = true;
  $("#sendBtn").hidden = true;
  $("#stopBtn").hidden = false;

  let reasoning = "";
  let content = "";
  let ended = false;
  let stopped = false;
  let unanswered = false;
  // Shows what arrived. Once the reply is over nothing may stay folded away
  // without an answer under it: then the thinking is the reply.
  const settle = () => {
    const parts = splitReply(reasoning, content);
    let alone = "";
    if (!parts.answer) {
      alone = parts.thinking.trim();
      parts.answer = alone || content;
      parts.thinking = "";
    }
    paintReply(assistantEl, parts);
    return { answer: parts.answer, alone: !!alone };
  };
  const fail = (message) => {
    ended = true;
    assistantEl.classList.add("msg-error");
    if (reasoning || content) {
      settle();
      replyNote(assistantEl, message);
    } else {
      body.textContent = message;
    }
    toast(message, true);
  };

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: apiHeaders(),
      body: JSON.stringify({ repo, messages: state.messages }),
    });
    if (!res.ok) {
      // Refused before anything was asked: give the typed text back.
      if (!input.value) input.value = text;
      const err = await res.json().catch(() => ({ error: res.statusText }));
      throw new Error(err.error || "chat failed");
    }

    await readEvents(res, (ev, data) => {
      if (ev === "status") {
        // Once the reply has begun, a pause needs no words.
        if (reasoning || content) return;
        wait.hidden = false;
        setText(wait, data.state === "prompt"
          ? `Reading the prompt… ${data.done} of ${data.total} tokens`
          : "Waiting for the API server… It may be answering another client or loading the model.");
        return;
      }
      wait.hidden = true;
      if (ev === "token" || ev === "reasoning") {
        if (ev === "token") content += data;
        else reasoning += data;
        paintReply(assistantEl, splitReply(reasoning, content));
        scrollChat();
      } else if (ev === "done") {
        ended = true;
        stopped = !!data.stopped;
        const { answer, alone } = settle();
        const notes = [];
        if (alone) notes.push("No answer came after the thinking, so it is shown here.");
        if (!answer) notes.push(stopped ? "Stopped before any reply." : "The reply was empty.");
        replyNote(assistantEl, notes.join(" "));
        const metaEl = document.createElement("div");
        metaEl.className = "msg-meta";
        metaEl.textContent = statsLine(data);
        if (metaEl.textContent) assistantEl.appendChild(metaEl);
        // A turn with no answer is dropped below, like a failed one: an empty reply,
        // or thinking alone, which the model must not take for what it answered.
        unanswered = alone;
        if (answer && !alone) {
          if (state.messages === history) history.push({ role: "assistant", content: answer });
          replied = true;
        }
      } else if (ev === "error") {
        fail(data);
      }
    });
    if (!ended) fail("The reply was cut off.");
  } catch (err) {
    fail(err.message);
  } finally {
    // A turn without a reply would break role alternation on every later send.
    const at = state.messages.indexOf(turn);
    if (!replied && at !== -1) state.messages.splice(at, 1);
    wait.hidden = true;
    body.classList.remove("cursor");
    setText($("#chatAnnounce"),
      stopped ? "Reply stopped" : replied ? "Reply complete"
        : unanswered ? "Reply ended without an answer" : "Reply failed");
    state.streaming = false;
    const held = document.activeElement === $("#stopBtn");
    $("#sendBtn").hidden = false;
    $("#stopBtn").hidden = true;
    if (held) $("#input").focus();
    refreshState();
  }
}

/* ───────────── logs ───────────── */
async function loadLogs() {
  try {
    const { entries } = await api("/api/logs");
    const filter = $("#logFilter").value;
    const rows = entries.filter((e) => filter === "all" || e.action === filter).reverse();
    $("#logsEmpty").hidden = rows.length > 0;
    $("#logTable").hidden = rows.length === 0;
    $("#logBody").innerHTML = rows.map((e) => {
      const cls = e.status === "success" ? "st-ok" : e.status === "failed" ? "st-bad" : e.status === "running" ? "st-run" : "";
      return `<tr>
        <td>${esc(e.timestamp)}</td>
        <td>${esc(e.action)}</td>
        <td>${esc(e.model || "—")}</td>
        <td class="${cls}">${esc(e.status)}</td>
        <td>${esc(e.detail)}</td>
      </tr>`;
    }).join("");
  } catch (err) { toast(err.message, true); }
  loadLogFiles();
  loadAppLog();
}

async function loadLogFiles() {
  try {
    const { dir, files } = await api("/api/logs/files");
    $("#logDirPill").textContent = `${files.length} file${files.length === 1 ? "" : "s"}`;
    $("#logDirPath").textContent = dir;
    $("#logFileTable").hidden = files.length === 0;
    $("#logFileBody").innerHTML = files.map((f) => `<tr>
        <td class="mono">${esc(f.name)}</td>
        <td>${esc(f.size_h)}</td>
        <td>${esc(f.modified)}</td>
      </tr>`).join("");
  } catch (err) { toast(err.message, true); }
}

async function loadAppLog() {
  try {
    const { entries } = await api("/api/logs/app?limit=300");
    const level = $("#appLogLevel").value;
    const rows = entries.filter((e) => level === "all" || e.level === level).reverse();
    $("#appLogEmpty").hidden = rows.length > 0;
    $("#appLogTable").hidden = rows.length === 0;
    $("#appLogBody").innerHTML = rows.map((e) => {
      const cls = e.level === "error" ? "st-bad" : e.level === "warning" ? "st-run" : "st-ok";
      const extra = Object.entries(e)
        .filter(([k]) => !["timestamp", "level", "message"].includes(k))
        .map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`)
        .join(" ");
      return `<tr>
        <td>${esc(e.timestamp)}</td>
        <td class="${cls}">${esc(e.level)}</td>
        <td>${esc(e.message)}${extra ? ` <span class="mono">${esc(extra)}</span>` : ""}</td>
      </tr>`;
    }).join("");
  } catch (err) { toast(err.message, true); }
}

async function exportDiagnostics() {
  try {
    toast("Building diagnostics bundle…");
    const res = await fetch("/api/logs/export", { headers: apiHeaders() });
    if (!res.ok) throw new Error(`export failed (${res.status})`);
    const blob = await res.blob();
    const disp = res.headers.get("Content-Disposition") || "";
    const match = disp.match(/filename="([^"]+)"/);
    const name = match ? match[1] : "mlx-studio-diagnostics.zip";
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 4000);
    toast(`Saved ${name} (${Math.round(blob.size / 1024)} KB)`);
  } catch (err) { toast(err.message, true); }
}

// One start at a time, held until the card shows what it did. ``keep`` names the
// card edits that are not part of this start.
async function sendStart(body, message, keep = {}) {
  if (state.serverSending) return;
  const stopped = !state.server?.running;
  state.keptSet = !!keep.set;
  state.keptBox = !!keep.box;
  state.serverSending = true;
  try {
    await api("/api/server/start", { method: "POST", body: JSON.stringify(body) });
  } catch (err) {
    state.serverSending = false;
    throw err;
  }
  // From stopped, this is now the set that Restart would try again.
  if (stopped) {
    state.serverLast = [body.repo, ...(body.also || [])];
    state.serverLastBackground = body.background;
    state.serverRan = { engine: body.engine || "mlx", anthropic: body.anthropic };
  }
  toast(message);
  refreshState().finally(() => { state.serverSending = false; });
}

// Start, or Apply changes, with what the API Server card shows.
async function startFromCard() {
  if (state.serverSending) return;
  const set = serverSet();
  if (!set.length) { toast("No model selected", true); return; }
  const background = $("#serverBackground").checked;
  const applying = !!state.server?.running;
  state.serverStartError = "";
  try {
    await sendStart({ ...serverChoice(set), background },
      applying ? "Restarting server…"
        : background ? "Starting in background…" : "Starting server…");
  } catch (err) {
    // The reason stays on the card; the next poll asks for the figures again.
    state.estimateRetryAt = 0;
    state.serverStartError = err.message;
    paintServerCard();
    toast(err.message, true);
  }
}

async function stopServer() {
  try { await api("/api/server/stop", { method: "POST", body: "{}" }); refreshState(); }
  catch (err) { toast(err.message, true); }
}

/* ───────────── wiring ───────────── */
function wire() {
  wireEngines();
  createPicker("#serverModelSelect", {
    onLeave(escaped) {
      showAddPicker(false);
      if (escaped) $("#serverAddBtn").focus();
    },
  });

  $("#chatModelSelect").addEventListener("change", () => {
    state.chatModel = $("#chatModelSelect").value;
    renderChatMode(state.server);
  });
  for (const key of ["performance_mode", "kv_bits"]) {
    configField(key).addEventListener("change", paintPerformance);
  }
  $("#chatGoServer").addEventListener("click", () => {
    showView("server");
    // The button leaves the screen: keep the focus on something that stays.
    $(".nav-item.active").focus();
  });

  $("#newChatBtn").addEventListener("click", () => {
    // The reply being generated belongs to the chat that is being thrown away.
    if (state.streaming) api("/api/chat/stop", { method: "POST", body: "{}" }).catch(() => {});
    state.messages = [];
    $("#messages").innerHTML = "";
    $("#chatEmpty").hidden = false;
  });

  $("#sendBtn").addEventListener("click", sendMessage);
  $("#stopBtn").addEventListener("click", async (e) => {
    // Stop takes Send's place: the second click of a double click is not a Stop.
    if (e.detail > 1) return;
    try { await api("/api/chat/stop", { method: "POST", body: "{}" }); } catch (_) {}
  });

  const input = $("#input");
  input.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 190) + "px";
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  });

  $("#searchBtn").addEventListener("click", () => doSearch($("#searchInput").value.trim()));
  $("#searchInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") doSearch($("#searchInput").value.trim());
  });
  $("#showAllBtn").addEventListener("click", () => { $("#searchInput").value = ""; doSearch(""); });

  $("#refreshModelsBtn").addEventListener("click", refreshModels);
  $("#catalogFilter").addEventListener("change", renderCatalog);

  $("#serverModelSelect").addEventListener("change", addServerModel);
  $("#serverAddBtn").addEventListener("click", () => {
    showAddPicker(true);
    $("#serverModelSelectSearch").focus();
    pickers["#serverModelSelect"].open();
  });
  createPicker("#configModelSelect");
  createPicker("#ltModelSelect");
  $("#configModelSelect").addEventListener("change",
    (e) => chooseConfigModel(e.target.value));
  $("#configFields").addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || e.isComposing) return;
    e.preventDefault();
    return saveConfig();
  });
  $("#configSaveBtn").addEventListener("click", saveConfig);
  $("#configResetBtn").addEventListener("click", resetConfig);
  $("#configApplyBtn").addEventListener("click", startFromCard);
  $("#ltModelSelect").addEventListener("change", (e) => {
    setLoadTestModel(e.target.value);
    paintLoadTestControls();
    return loadLoadTest();
  });
  $("#ltSizes").addEventListener("change", (e) => {
    const size = Number(e.target.value);
    if (e.target.checked) state.lt.picked.add(size);
    else state.lt.picked.delete(size);
    ltError("");
    paintLoadTestControls();
  });
  $("#ltAddBtn").addEventListener("click", addCustomSize);
  $("#ltCustom").addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    addCustomSize();
  });
  $("#ltStartBtn").addEventListener("click", startLoadTest);
  $("#ltThinking").addEventListener("change", (e) => { state.lt.thinking = e.target.checked; });
  $("#ltCancelBtn").addEventListener("click", cancelLoadTest);
  $("#ltStopServerBtn").addEventListener("click", stopServer);
  $("#serverBackground").addEventListener("change", () => {
    if (startOnItsWay()) state.keptBox = true;
    serverCardChanged();
  });
  // The first click of a double click moves things: the second would press whatever
  // came under the pointer. Keyboard presses report no click count.
  const secondClick = (e) => {
    if (e.detail > 1 && e.target.closest("button, label")) {
      e.preventDefault();
      e.stopPropagation();
    }
  };
  $("#view-server").addEventListener("click", secondClick, true);
  $("#view-server").addEventListener("mousedown", secondClick, true);

  $("#serverStartBtn").addEventListener("click", startFromCard);
  $("#serverStopBtn").addEventListener("click", stopServer);
  const dismissNotice = async () => {
    try { await api("/api/server/notice/clear", { method: "POST", body: "{}" }); refreshState(); }
    catch (err) { toast(err.message, true); }
  };
  $("#serverNoticeDismiss").addEventListener("click", dismissNotice);
  $("#serverApplyDismiss").addEventListener("click", () => {
    // The notice goes away with its button: leave the focus on something that stays.
    spareControl().focus();
    return dismissNotice();
  });
  $("#serverUnguardedRestart").addEventListener("click", async () => {
    // The model and mode that are running now, whatever the card shows.
    const { repo, repos, background } = state.snapshot?.server || {};
    if (!repo) return;
    try {
      await sendStart({
        repo, ...(repos?.[1] ? { also: [repos[1]] } : {}), background: !!background,
      }, "Restarting server…");
    } catch (err) { toast(err.message, true); }
  });
  $("#serverRestartBtn").addEventListener("click", async () => {
    // What ran last, not an unapplied choice; the card only when nothing is known.
    let set = usable(state.serverLast);
    if (!set.length) set = serverSet();
    if (!set.length) set = usable([state.snapshot?.server?.repo].filter(Boolean));
    if (!set.length) { toast("Pick a model first", true); return; }
    const box = $("#serverBackground").checked;
    const background = state.serverLastBackground ?? box;
    const ran = state.serverRan ||
      { engine: state.server?.last_engine || "mlx", anthropic: state.server?.last_anthropic };
    const choice = serverChoice(set, ran.engine, ran.anthropic ?? true);
    try {
      await sendStart({ ...choice, background }, "Restarting server…",
        { set: !!state.serverDraft, box: box !== background });
    } catch (err) { toast(err.message, true); }
  });
  $("#copyEndpoint").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText($("#endpointText").textContent); toast("Endpoint copied"); }
    catch (_) { toast("Copy failed", true); }
  });

  $("#refreshLogsBtn").addEventListener("click", loadLogs);
  $("#logFilter").addEventListener("change", loadLogs);
  $("#appLogLevel").addEventListener("change", loadAppLog);
  $("#refreshLogFiles").addEventListener("click", loadLogFiles);
  $("#exportLogsBtn").addEventListener("click", exportDiagnostics);
  $("#copyLogDir").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText($("#logDirPath").textContent);
      toast("Folder path copied");
    } catch (_) { toast("Copy failed", true); }
  });
  $("#clearLogsBtn").addEventListener("click", async () => {
    if (!confirm("Clear the activity log? app.log and launch.log are kept.")) return;
    try { await api("/api/logs/clear", { method: "POST", body: "{}" }); loadLogs(); toast("Log cleared"); }
    catch (err) { toast(err.message, true); }
  });

  $("#checkUpdatesBtn").addEventListener("click", async () => {
    toast("Checking PyPI…");
    try { await api("/api/versions"); refreshState(); toast("Version check complete"); }
    catch (err) { toast(err.message, true); }
  });

  $("#installBtn").addEventListener("click", async () => {
    const kind = state.snapshot?.versions?.mlx_missing ? "install" : "update";
    try { await api("/api/install", { method: "POST", body: JSON.stringify({ kind }) }); toast("Install started"); refreshState(); }
    catch (err) { toast(err.message, true); }
  });

  // Toggle Active Downloads section
  $("#toggleDownloads").addEventListener("click", () => setDownloadsOpen(!state.downloadsOpen));
}

/* ───────────── boot ───────────── */
(async function boot() {
  wire();
  $("#curlExample").textContent =
`curl http://127.0.0.1:8080/v1/chat/completions \\
  -H "Content-Type: application/json" \\
  -d '{"model":"<model>","messages":[{"role":"user","content":"Hello!"}]}'`;
  showView(location.hash.slice(1) || VIEWS[0]);
  // Without a token every request is refused: say so once and ask for nothing.
  if (!API_TOKEN) return toast("This page has no session. Open MLX Studio from its app icon.", true);
  await refreshState();
  loadCatalog();
  state.pollTimer = setInterval(refreshState, 2000);
})();
