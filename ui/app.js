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
  chatRef: null, // {id, updated} of the saved chat on screen
  chatBusy: false, // its settings are being saved
  saveHistory: "on",
  historySaving: 0,
  historyRows: new Map(),
  historySeq: 0,
  openSeq: 0,
  renameId: "",
  catalog: null,
  search: null,
  aliases: {}, // repo to its saved alias
  aliasRepo: "", // the model the alias editor is open for
  aliasSaving: false,
  searchSeq: 0,
  downloadsOpen: false,
  lastServerRunning: false,
  refreshing: null,
  refreshAgain: false,
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
  boxEngine: null, // the engine the background box shows
  boxPick: {}, // clicks on that box, by engine
  serverStartError: "",
  serverSending: false,
  lastApplyNotice: null,
  view: "",
  // Test Connection: of the server with this pid, while it runs or what it found.
  test: { pid: null, running: false, result: null, error: "" },
  // Start at login: what the app last said, and for which server it was asked.
  autostart: null,
  autostartAt: null,
  autostartBusy: false,
  autostartError: "",
  autostartChanges: 0,
  theme: "system", // as last saved
  themeSaving: 0, // while a choice is being saved, polls may still carry the old one
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
    // answered: the app said no, unlike a request that never got through.
    throw Object.assign(new Error(msg), { answered: true });
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

// What API clients send as "model": the alias if there is one, else the repo id.
// The name a client must send now: until Apply changes, a running server still
// answers to what it was started with, not to a newly saved alias.
function clientName(repo) {
  const server = state.snapshot?.server;
  if (!server?.running || !(server.repos || []).includes(repo)) return aliasOf(repo) || repo;
  return (repo === server.repo && server.alias) || repo;
}

function copyButton(repo) {
  const name = clientName(repo);
  return `<button class="btn btn-mini btn-ghost" type="button" data-copy="${esc(name)}" aria-label="${COPY_LABEL} ${esc(name)}">${COPY_LABEL}</button>`;
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

/* ───────────── appearance ───────────── */
const THEMES = ["system", "light", "dark"];

// The app window takes the colours from its native appearance; data-theme does the
// same in a browser. "System" leaves both to macOS.
function paintTheme(theme) {
  state.theme = THEMES.includes(theme) ? theme : "system";
  const root = document.documentElement;
  if (state.theme === "system") delete root.dataset.theme;
  else root.dataset.theme = state.theme;
  for (const name of THEMES) $(`#theme-${name}`).checked = name === state.theme;
}

async function chooseTheme(theme) {
  const before = state.theme;
  state.themeSaving += 1;
  paintTheme(theme);
  try {
    await api("/api/ui/settings", { method: "POST", body: JSON.stringify({ theme }) });
  } catch (err) {
    paintTheme(before);
    toast(`Could not save the appearance: ${err.message}`, true);
  } finally {
    state.themeSaving -= 1;
  }
  refreshState();
}

function render(snap) {
  state.snapshot = snap; // what the Start areas read about installs
  const { app, hardware, python, versions, models, server, downloads, install } = snap;
  // Before anything is drawn: every screen names a model through these.
  state.aliases = Object.fromEntries(
    models.filter((m) => m.alias).map((m) => [m.repo, m.alias]));
  if (state.aliasRepo && !models.some((m) => m.repo === state.aliasRepo)) hideAlias();

  if (!state.themeSaving) paintTheme(snap.ui?.theme);
  if (!state.historySaving) paintSaveHistory(snap.ui?.save_history);

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
  const unloaded = !!(server.running && server.load_failed);
  const runningDot = unloaded ? "dot-bad" : "dot-ok";
  setHTML($("#serverState"), server.running
    ? `<span class="dot ${runningDot}"></span>${esc(unloaded
      ? "Model not loaded: press Stop, then Start Server" : runningNames(server))}`
    : `<span class="dot dot-warn"></span>Stopped`);
  $("#navServerDot").className = "dot " + (server.running ? runningDot : "");
  setText($("#navServerText"),
    unloaded ? "model not loaded" : server.running ? "server running" : "server stopped");
  setText($("#endpointText"), server.endpoint);
  setText($("#serverModePill"), server.running
    ? (server.background ? "background" : "foreground")
    : "stopped");
  $("#serverModePill").className = "pill " +
    (server.running ? (server.background ? "ok" : "") : "warn");
  const notice = $("#serverNotice");
  if (server.down_notice) {
    notice.hidden = false;
    setText($("#serverNoticeText"), `${server.down_notice} Next: read Server output below, ` +
      "then press Restart, or choose the other engine or a smaller model.");
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
    if (!state.keptBox) {
      $("#serverBackground").checked = !!server.background;
      state.boxEngine = server.engine || "mlx";
      state.boxPick = {};
    }
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
    const lines = server.lines || [];
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
  paintServerCard();
  // A Restart button goes away with its notice: leave the focus on something that stays.
  if (held === $("#serverRestartBtn") && $("#serverNotice").hidden) spareControl().focus();
  renderToolsWarning(server);
  paintTest(server);
  watchAutostart(server);
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

/* ───────────── the API Server card ───────────── */
const MAX_SERVER_MODELS = 1;
const shortName = (repo) => String(repo || "").split("/").pop();
const aliasOf = (repo) =>
  (Object.hasOwn(state.aliases, repo) ? state.aliases[repo] : "");
// A short title, and a label for places with room for the actual name as well.
const displayName = (repo) => aliasOf(repo) || shortName(repo);
const modelLabel = (repo) => (aliasOf(repo) ? `${aliasOf(repo)} (${repo})` : repo);
const runningSet = (server) =>
  server?.repos?.length ? server.repos : server?.repo ? [server.repo] : [];
// Order does not make a different choice.
const sameSet = (a, b) => a.length === b.length && a.every((repo) => b.includes(repo));

// The names that are installed completely; all of them until the list has loaded.
const usable = (set) =>
  set.filter((repo) => !state.readyModels || state.readyModels.includes(repo));

// The model on the card: the user's edit, else what runs, else what ran last.
function serverSet() {
  const server = state.server;
  if (server?.running) return state.serverDraft || runningSet(server);
  // An older version may have remembered two models.
  return usable(state.serverDraft || state.serverLast).slice(0, MAX_SERVER_MODELS);
}

// The card differs from the running server, so Apply has something to do.
function serverPending() {
  const server = state.server;
  return !!server?.running && (!sameSet(serverSet(), runningSet(server)) ||
    engineChanged() || $("#serverBackground").checked !== !!server.background || !!server.settings_pending);
}

// The model fields of a start request.
function serverChoice(set = serverSet(), engine = engineId(), anthropic = anthropicWanted()) {
  const [repo = ""] = set;
  // No engine named means MLX.
  return engine === "mlx" ? { repo } : { repo, engine, anthropic };
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

// "Run in background": what was clicked here, else the running server's mode, else
// the saved choice. With none, on for OptiQ and off for MLX (it has no password).
function backgroundFor(engine) {
  const server = state.server;
  return state.boxPick[engine] ??
    (server.running && engine === runningEngine() ? !!server.background
      : server.last_background?.[engine] ?? engine !== "mlx");
}

// The box follows the engine selection.
function paintBackground() {
  const engine = engineId();
  if (engine === state.boxEngine) return;
  state.boxEngine = engine;
  $("#serverBackground").checked = backgroundFor(engine);
}

function engineChanged() {
  const server = state.server;
  if (!server?.running) return false;
  if (engineId() !== runningEngine()) return true;
  return !!engineInfo()?.anthropic && anthropicWanted() !== !!server.anthropic;
}

// Whether the chosen engine can serve the model, as the app says.
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
  showLine("#engineWhy", engineNeed(engineId()) ? "" : engineReason(set));
  paintInstall("server", engineId());
  const missing = engine?.missing || [];
  const box = $("#engineMissing");
  if (box.hidden && missing.length) box.open = true;
  box.hidden = !missing.length;
  setText($("#engineMissingTitle"), `Not available with this engine (${missing.length})`);
  setHTML($("#engineMissingList"), missing.map(([what, why]) =>
    `<li>${esc(what)}: ${esc(why)}</li>`).join(""));
  paintClientSettings(engine);
}

/* ───────────── install from the Start area ───────────── */
const PACKAGE_KINDS = ["install", "update", "reinstall"];

// What the engine still needs before it can start, or null.
function engineNeed(id) {
  const versions = state.snapshot?.versions || {};
  if (id !== "mlx") {
    const found = engineInfo(id)?.state;
    return ["not_installed", "unsupported", "broken", "update"].includes(found) ? found : null;
  }
  return versions.mlx_missing || versions.mlx_lm_missing ? "not_installed" : null;
}

// The box under a Start button: ``prefix`` is "server" or "lt".
function paintInstall(prefix, id) {
  const need = engineNeed(id);
  $(`#${prefix}Install`).hidden = !need;
  if (!need) return;
  const install = state.snapshot?.install || {};
  const kind = String(install.kind || "");
  const mine = id === "mlx" ? PACKAGE_KINDS.includes(kind) : kind.startsWith(`${id} `);
  const lines = install.lines || [];
  const name = id === "mlx" ? "MLX" : engineInfo(id)?.name || id;
  const never = need === "unsupported";
  const busy = !!install.running;
  const failed = mine && !busy && lines.length > 0;
  let words;
  if (never) {
    words = `${engineInfo(id)?.unsupported || ""} ${name} cannot be installed on this Mac. ` +
      "Press Use MLX instead.";
  } else if (busy) {
    words = mine ? `Installing ${name}… This can take several minutes. Start when it is done.`
      : `${name} is not installed. Another install is running: wait for it to finish.`;
  } else if (failed) {
    const reason = [...lines].reverse().find((line) => /^error/i.test(line)) || lines.at(-1);
    words = `The install did not finish: ${reason.replace(/^error:\s*/i, "")}. Check the ` +
      "internet connection and free disk space, then press Install again" +
      (id === "mlx" ? "." : ", or use MLX instead.");
  } else {
    const what = { broken: "has a broken install", update: "needs an update" }[need] ||
      "is not installed";
    words = `${name} ${what}. Press Install, wait for it to finish, then start.`;
  }
  setText($(`#${prefix}InstallText`), words.trim());
  const log = $(`#${prefix}InstallLog`);
  log.hidden = !(mine && lines.length);
  setText(log, mine ? lines.slice(-8).join("\n") : "");
  const button = $(`#${prefix}InstallBtn`);
  button.hidden = never;
  button.disabled = busy;
  $(`#${prefix}UseMlx`).hidden = id === "mlx";
}

// The same two actions as the Setup page.
async function installEngine(id) {
  if (id !== "mlx") return changeEngine(id, "install");
  const kind = state.snapshot?.versions?.mlx_missing ? "install" : "update";
  try {
    await api("/api/install", { method: "POST", body: JSON.stringify({ kind }) });
    toast("Install started");
    refreshState();
  } catch (err) { toast(err.message, true); }
}

function paintClientSettings(picked) {
  const server = state.server;
  // A running server's own settings, not those of a choice that waits for Apply.
  const running = !!server.running;
  const engine = running ? engineInfo(runningEngine()) : picked;
  const direct = !!engine?.direct;
  const base = `http://127.0.0.1:${(direct ? engine.port : 8080) || 8080}`;
  // What a running server answers to; before a start, what the chosen model would get.
  // Without an alias, default_model: both engines call their start model that.
  const [chosen = ""] = running ? runningSet(server) : serverSet();
  const alias = running ? server.alias || "" : aliasOf(chosen);
  const model = alias || "default_model";
  const keyed = direct && running && server.ready;
  $("#copyKey").hidden = !keyed;
  $("#curlExample").hidden = direct;
  setText($("#curlExample"), `curl ${base}/v1/chat/completions \\
  -H "Content-Type: application/json" \\
  -d '{"model":"${model}","messages":[{"role":"user","content":"Hello!"}]}'`);
  $("#mlxModelHint").hidden = direct;
  setText($("#mlxModelHint"), (alias
    ? `Send the model name ${alias} or default_model. While an alias is set the server ` +
      "lists only this model, and other cached models are not found by name. A folder " +
      "path can still make it load other files."
    : "Send the model name default_model. Another name makes the MLX server load that " +
      "model instead.") + " No password: any program on this Mac, and a website open in " +
    "your browser, can send it requests while it runs.");
  $("#mlxOpenWarn").hidden = direct;
  const auto = state.autostart;
  const stable = direct && !!auto?.key_in_use;
  const keyWords = !direct ? "not needed"
    : stable ? "press Copy key (kept for start at login)" : "press Copy key (new at every start)";
  setText($("#openaiSettings"), `Base URL: ${base}/v1\nModel: ${model}\nAPI key: ${keyWords}`);
  const saved = running && auto?.enabled && auto.default?.repo === chosen &&
    auto.default?.engine === runningEngine();
  $("#keyUse").hidden = !direct;
  setText($("#keyUse"), [
    "Send the key as the header Authorization: Bearer <key> or x-api-key. Claude Code reads " +
      "it from ANTHROPIC_AUTH_TOKEN; in DeepSeek Harness put it in this provider's API key " +
      "field. Keep it in the tool's own secret setting or an environment variable, never in " +
      "a shared file.",
    stable ? "This key stays the same while start at login is on for this model, so set your " +
      `tool up once. It is also in the file ${auto.key_file}.`
      : saved ? "Press Stop, then Start Server, to use the saved start at login key." : "",
  ].filter(Boolean).join(" "));
  $("#connectPanel").hidden = !running;
  const waiting = running && aliasOf(chosen) !== (server.alias || "");
  setText($("#connectState"), [
    running && !server.ready
      ? "The server is still starting. Use these settings once it is ready." : "",
    waiting ? "The saved alias is not in use yet: press Apply changes." : "",
  ].filter(Boolean).join(" "));
  const typed = Number($("#dshContext").value);
  const context = Number.isInteger(typed) && typed > 0 ? typed : null;
  setText($("#dshSettings"), `Type: OpenAI compatible\nBase URL: ${base}/v1\nModel: ${model}\n` +
    `Context window: ${context ?? "(type a number above)"}\nAPI key: ${keyWords}`);
  $("#copyDsh").disabled = !context;
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
    toast(state.autostart?.key_in_use ? "Key copied. It is kept for start at login."
      : "Key copied. It changes at every start.");
  } catch (err) {
    toast(err.message || "The key was not copied. Press Copy key again.", true);
  }
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
  $("#dshContext").addEventListener("input", () => paintClientSettings(engineInfo()));
  $("#serverInstallBtn").addEventListener("click", () => installEngine(engineId()));
  $("#serverUseMlx").addEventListener("click", () => {
    state.engine = "mlx";
    serverCardChanged();
  });
  $("#ltInstallBtn").addEventListener("click", () => installEngine($("#ltEngine").value));
  $("#ltUseMlx").addEventListener("click", () => {
    state.lt.engine = "mlx";
    if (state.snapshot) renderLoadTest(state.snapshot);
  });
  $("#ltEngine").addEventListener("change", () => {
    state.lt.engine = $("#ltEngine").value;
    paintLoadTestControls();
  });
  for (const [button, box] of [["#copyOpenai", "#openaiSettings"],
    ["#copyClaude", "#claudeSettings"], ["#copyDsh", "#dshSettings"]]) {
    $(button).addEventListener("click", async () => {
      try { await navigator.clipboard.writeText($(box).textContent); toast("Settings copied"); }
      catch (_) { toast("Copy failed. Select the text and press Command-C.", true); }
    });
  }
}

function runningNames(server) {
  return `Running — ${runningSet(server).map(displayName).join(" and ")}`;
}

/* ───────────── configuration page ───────────── */
const CONFIG_KEYS = ["max_context_tokens", "server_max_reply_tokens", "chat_max_reply_tokens",
  "chat_temperature", "chat_top_p", "prompt_cache_size", "prompt_cache_bytes",
  "performance_mode", "kv_bits", "kv_group_size", "prefill_step_size", "server_reasoning"];
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
  for (const id of ["#configError", "#configTestWarn"]) setText($(id), "");
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
  $("#configSaveBtn").disabled = $("#configResetBtn").disabled = false;
}

// An OptiQ context above everything a load test has passed is only a hope: say so.
function warnUntested(view, tests) {
  const passed = (tests?.results || []).filter((run) => !run.stale)
    .flatMap((run) => run.rows.filter((r) => r.status === "passed").map((r) => r.size));
  const largest = Math.max(0, ...passed);
  const context = view.settings.max_context_tokens.value;
  setText($("#configTestWarn"), largest && context > largest
    ? `The OptiQ max context here (${context} tokens) is above the largest size that passed a load ` +
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
    setText($("#configModelSelectStatus"), `Kept ${modelLabel(state.config.repo) || ALL_MODELS}.`);
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
    // Every other setting is a number.
    const word = key === "performance_mode" || key === "server_reasoning";
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
  if (!view || !confirm(`Clear every value saved for ${modelLabel(repo) || ALL_MODELS}?`)) return;
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
const LT_NUMBERS = ["reply_tokens", "rounds", "timeout_seconds"];
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
  const engine = $("#ltEngine").value || "mlx";
  paintInstall("lt", engine);
  start.disabled = none || testing || !!why || !view || !!engineNeed(engine);
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
    : `Testing ${modelLabel(run.repo)}${where}: ${run.step || "starting"}.`;
  setText($("#ltStep"), phase);
  setText($("#ltAnnounce"), phase);
  const reading = !cancelling && run.total > 0;
  const bar = $("#ltProgress");
  bar.hidden = !reading;
  if (reading) {
    bar.max = run.total;
    bar.value = run.done;
  }
  setText($("#ltDetail"), [
    reading ? `Reading the prompt: ${run.done} of ${run.total} tokens.` : "",
    run.memory_percent == null ? ""
      : `Memory now: ${run.memory_percent}% of RAM.`,
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
    ? `${last.reason}. Larger sizes were not run.`
    : last.reason);
}

function resultRow(run, row) {
  const used = run.values || {};
  const note = (text) => (text ? `<span class="lt-note">${esc(text)}</span>` : "");
  return `<tr><th scope="row">${esc(row.size)}` +
    note(row.prompt_tokens ? `${row.prompt_tokens} in prompt` : "") +
    `</th><td><span class="pill ${LT_PILL[row.status] || ""}">${esc(row.status)}</span>` +
    note(row.reason) +
    `</td><td>${figure(row.read_tps)}</td><td>${seconds(row.first_token_s)}` +
    note(row.follow_up_first_token_s == null ? ""
      : `follow-up ${seconds(row.follow_up_first_token_s)}`) +
    `</td><td>${figure(row.write_tps)}</td><td>` +
    (row.peak_percent == null ? "n/a" : `${figure(row.peak_percent)}%`) +
    note(row.server_peak_bytes
      ? `server ${(row.server_peak_bytes / 1e9).toFixed(1)} GB` : "") +
    `</td><td>${esc(`reply ${used.reply_tokens} tokens, ${used.rounds} rounds`)}` +
    "</td></tr>";
}

function resultRun(run) {
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
      "Run it again.</p>" : "") +
    `<div class="lt-table" role="region" tabindex="0" aria-label="${esc(name)}">` +
    `<table class="table"><thead><tr>${heads}</tr></thead><tbody>` +
    (run.rows.map((row) => resultRow(run, row)).join("") ||
      '<tr><td colspan="7">No size has finished.</td></tr>') +
    "</tbody></table></div>" +
    `<p class="hint">Server flags: <span class="mono">${esc((run.flags || []).join(" "))}` +
    "</span></p>" +
    '<p class="hint">A pass is a measurement, not a guarantee.</p></section>';
}

function paintResults() {
  const { view } = state.lt;
  if (!view) return;
  const [last, ...older] = [...view.results].reverse();
  paintOutcome(last);
  const box = $("#ltResults");
  setHTML(box, !last ? '<p class="hint">No results for this model yet.</p>'
    : resultRun(last) + (older.length
      ? `<details class="lt-history"><summary>Earlier runs (${older.length})</summary>` +
        `${older.map(resultRun).join("")}</details>` : ""));
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
  // The user's pick, else the saved engine of the API Server.
  $("#ltEngine").value = testing ? run.engine || "mlx"
    : lt.engine || snap.server?.last_engine || "mlx";
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

// Why the button is off, or what to do next.
function serverReason(server, set) {
  if (server.starting) return "Starting the server…";
  if (state.serverStartError) return state.serverStartError;
  if (set.length) return "";
  return server.running ? "Add a model, or press Stop." : "Add a model to start the server.";
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
  paintBackground(); // before serverPending, which reads the box
  const pending = serverPending();

  const list = $("#serverModelList");
  const drawn = setHTML(list, set.map((repo) =>
    `<li><span class="model-name" title="${esc(repo)}">${esc(displayName(repo))}</span>` +
    (aliasOf(repo) ? `<span class="model-id">${esc(repo)}</span>` : "") +
    `<button class="btn btn-mini btn-ghost" type="button" data-remove="${esc(repo)}" ` +
    `aria-label="Remove ${esc(repo)}">×</button></li>`).join(""));
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
    ? "The server serves one model. Remove it to choose another."
    : !ready.length ? "No models installed. Get one on the Download page."
      : !others.length ? "No other installed model to add." : "";
  $("#serverAddBtn").disabled = !!noAdd;
  setText($("#serverAddWhy"), noAdd);
  if (noAdd) showAddPicker(false);

  // For another engine only a refused start is said here; engineWhy says the rest.
  const direct = !!engineInfo()?.direct;
  const reason = direct ? state.serverStartError : serverReason(server, set);
  showLine("#serverWhy", reason);
  // The status region is always in the page, so a screen reader hears its changes.
  setText($("#serverStartWhy"), direct ? "" : reason);
  paintEngine(set);
  // The panel shows one choice: another choice needs a new look.
  if (state.confirm && state.confirm.choice !== JSON.stringify(serverChoice(set))) {
    closeStartConfirm();
  }

  const start = $("#serverStartBtn");
  const stop = $("#serverStopBtn");
  const focused = document.activeElement;
  setText(start, server.starting ? "Starting…" : server.running ? "Apply changes" : "Start Server");
  start.disabled = !!server.starting || !set.length || (server.running && !pending) ||
    !!engineReason(set) || !!engineNeed(engineId());
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
  paintServerCard();
  if (state.snapshot) renderToolsWarning(state.snapshot.server);
}

function addServerModel() {
  const repo = $("#serverModelSelect").value;
  const set = serverSet();
  if (!repo || set.includes(repo) || set.length >= MAX_SERVER_MODELS) return;
  showAddPicker(false);
  editServerSet([...set, repo]);
  setText($("#serverSetStatus"), `Added ${displayName(repo)}.`);
  const add = $("#serverAddBtn");
  const row = Array.from($("#serverModelList").querySelectorAll("[data-remove]"))
    .find((btn) => btn.dataset.remove === repo);
  (add.disabled && row ? row : add).focus();
}

function removeServerModel(repo) {
  editServerSet(serverSet().filter((other) => other !== repo));
  setText($("#serverSetStatus"), `Removed ${displayName(repo)}.`);
  const next = $("#serverModelList").querySelectorAll("[data-remove]")[0];
  (serverSet().length && next ? next : $("#serverAddBtn")).focus();
}

// Warn only for models marked as unable to call tools; unmarked means unknown.
function renderToolsWarning(server) {
  const support = server.tool_support || {};
  const choice = serverChoice();
  const repos = server.running
    ? (server.repos?.length ? server.repos : [server.repo])
    : [choice.repo];
  const without = repos.filter((repo) => support[repo] === false);
  const warn = without.length > 0;
  $("#serverToolsWarn").hidden = !warn;
  if (!warn) return;
  const verified = Object.keys(support).find((r) => support[r]);
  setText($("#serverToolsWarnText"),
    (repos.length > 1 ? `${without.map(displayName).join(", ")}: ` : "") +
    "Agent clients will not get tool calls from it; they may show them as plain text." +
    (verified ? ` For tool use, choose ${modelLabel(verified)}.` : ""));
}

/* ───────────── start at login ───────────── */
// Asked when the page is shown and when the running server changes.
function watchAutostart(server) {
  if (state.view !== "server") {
    state.autostartAt = null;
    return;
  }
  const at = `${server?.pid} ${!!server?.ready}`;
  if (state.autostartAt === at) return;
  state.autostartAt = at;
  loadAutostart();
}

async function loadAutostart() {
  const before = state.autostartChanges;
  let found;
  try {
    found = await api("/api/autostart");
  } catch (_) {
    state.autostartAt = null; // asked again with the next poll
    return;
  }
  // Read before or during a change: it must not overwrite what the change answered.
  if (state.autostartBusy || before !== state.autostartChanges) {
    state.autostartAt = null;
    return;
  }
  state.autostart = found;
  paintAutostart();
  paintServerCard();
}

// Why the switch cannot be turned on. It can always be turned off.
function autostartBlock(auto) {
  if (!auto) return "Reading…";
  if (auto.enabled) return "";
  if (!auto.available) return auto.unavailable_reason;
  if (!auto.default) return "Choose a default model first: pick it above, then press Set as default model.";
  return auto.autostart_refusal || "";
}

function paintAutostart() {
  const auto = state.autostart;
  const block = autostartBlock(auto);
  const chosen = auto?.default;
  $("#setDefault").setAttribute("aria-disabled", String(state.autostartBusy));
  $("#autostartSwitch").checked = !!auto?.enabled;
  $("#autostartSwitch").setAttribute("aria-disabled", String(state.autostartBusy || !!block));
  setText($("#defaultState"), !auto ? "" : !chosen ? "No default model."
    : `Default model: ${modelLabel(chosen.repo)} (${chosen.engine === "mlx" ? "MLX"
      : engineInfo(chosen.engine)?.name || chosen.engine}). Its saved server settings apply.`);
  setText($("#autostartWhy"), block || (auto.enabled
    ? "On: the server starts at your next login." : "Off: nothing starts at login."));
  setText($("#autostartNote"), auto?.password_note || "");
  const last = auto?.last;
  setText($("#autostartLast"), last
    ? `Last login start (${last.at}): ${last.result}${last.reason ? `. ${last.reason}` : ""}` : "");
  setText($("#autostartProblem"), state.autostartError || auto?.problem || "");
}

async function changeAutostart(path, body) {
  if (state.autostartBusy) return;
  Object.assign(state, { autostartBusy: true, autostartError: "" });
  paintAutostart();
  try {
    state.autostart = await api(path, { method: "POST", body: JSON.stringify(body) });
  } catch (err) {
    state.autostartError = err.message;
  }
  state.autostartBusy = false;
  state.autostartChanges += 1;
  paintAutostart();
  paintServerCard();
}

// The model, engine and route as the card above shows them.
function setDefaultModel() {
  const [repo = ""] = serverSet();
  return changeAutostart("/api/autostart/default",
    { repo, engine: engineId(), anthropic: anthropicWanted() });
}

function switchAutostart() {
  const wanted = $("#autostartSwitch").checked;
  if ($("#autostartSwitch").getAttribute("aria-disabled") === "true") {
    paintAutostart(); // puts the box back
    return;
  }
  return changeAutostart("/api/autostart", { enabled: wanted });
}

/* ───────────── test connection ───────────── */
const TEST_POLL_MS = 1000;
const TEST_POLL_MISSES = 5; // polls in a row that may fail before the test is given up
const TEST_WORDS = {
  pass: ["ok", "Passed"], warn: ["warn", "Check"],
  fail: ["bad", "Failed"], skipped: ["", "Not tried"],
};

function testRow(row) {
  const [tone, word] = TEST_WORDS[row?.status] || TEST_WORDS.fail;
  return `<span class="pill ${tone}">${word}</span> ${esc(row?.detail)}`;
}

// A result belongs to one running server: another one starts empty.
function paintTest(server) {
  const test = state.test;
  const pid = server?.running && server.ready ? server.pid : null;
  if (test.pid !== pid) {
    Object.assign(test, { pid, result: null, error: "" });
    if (pid !== null) resumeTest();
  }
  // Not disabled: that would drop the keyboard focus. testConnection ignores the press.
  $("#testBtn").setAttribute("aria-disabled", String(pid === null || test.running));
  $("#testResults").hidden = !(test.result || (test.running && pid !== null));
  const rows = test.result || {};
  setHTML($("#testReply"), test.running ? "Testing…" : test.result ? testRow(rows.reply) : "");
  setHTML($("#testTool"), test.running ? "Testing…" : test.result ? testRow(rows.tool) : "");
  setText($("#testError"), test.error);
}

// The test the app is at work on, or null.
async function runningTest() {
  const now = await api("/api/server/test").catch(() => null);
  return now?.running ? now : null;
}

async function startTest() {
  try {
    return await api("/api/server/test", { method: "POST", body: "{}" });
  } catch (err) {
    // One may be at work already, or this start's answer was lost: join it.
    const found = await runningTest();
    if (found) return found;
    throw err;
  }
}

// After a reload the app may still be testing this server.
async function resumeTest() {
  const found = await runningTest();
  if (found) await followTest(() => found);
}

function testConnection() {
  return followTest(startTest);
}

async function followTest(start) {
  const test = state.test;
  if (test.running || test.pid === null) return;
  const asked = test.pid;
  Object.assign(test, { running: true, result: null, error: "" });
  paintTest(state.server);
  let result = null;
  let error = "";
  try {
    result = await start();
    // A slow model: the app keeps testing and is asked again, so no request times out.
    let missed = 0;
    while (result.running) {
      await new Promise((done) => setTimeout(done, TEST_POLL_MS));
      try {
        result = await api("/api/server/test");
        missed = 0;
      } catch (err) {
        if (err.answered || ++missed >= TEST_POLL_MISSES) throw err;
      }
    }
  } catch (err) {
    result = null;
    error = err.message;
  }
  test.running = false;
  // An answer about a server that is gone says nothing about this one.
  if (test.pid === asked && (!result || result.pid === asked)) Object.assign(test, { result, error });
  paintTest(state.server);
}

function fillSelect(sel, values, current) {
  const el = $(sel);
  if (!el) return;
  const prev = el.value;
  const rebuilt = setHTML(el, values.length
    ? values.map((v) => `<option value="${esc(v)}">${esc(modelLabel(v))}</option>`).join("")
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
    shown = values.filter((v) => words.every((w) => modelLabel(v).toLowerCase().includes(w)));
    if (!shown.includes(active)) {
      active = shown.includes(select.value) ? select.value : shown[0] || "";
    }
    setHTML(list, shown.length
      ? shown.map((v, i) => `<li id="${id}-opt-${i}" role="option" aria-selected="${v === active}" class="${v === select.value ? "chosen" : ""}" data-value="${esc(v)}" title="${esc(modelLabel(v))}">${esc(modelLabel(v))}</li>`).join("")
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
    setText(chosen, `Selected: ${modelLabel(select.value) || "none"}`);
    chosen.title = modelLabel(select.value);
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
    setText(status, `Selected ${modelLabel(value)}`);
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
          <div class="card-name">${esc(displayName(repo))}</div>
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
        aliasButton(m.repo) +
        copyButton(m.repo) +
        button("delete", "Delete", "btn-danger"),
    );
  }).join(""));

  if (changed) {
    wrap.querySelectorAll("[data-act]").forEach((btn) => {
      btn.addEventListener("click", () => modelAction(btn.dataset.act, btn.dataset.repo));
    });
    wrap.querySelectorAll("[data-alias]").forEach((btn) => {
      btn.addEventListener("click", () => openAlias(btn.dataset.alias));
    });
  }
}

/* ───────────── model alias ───────────── */
function aliasButton(repo) {
  const label = aliasOf(repo) ? "Change alias" : "Set alias";
  return `<button class="btn btn-mini" type="button" data-alias="${esc(repo)}" ` +
    `aria-label="${label} for ${esc(repo)}">${label}</button>`;
}

// The editor is outside the cards, so a poll never clears what is typed.
function openAlias(repo) {
  state.aliasRepo = repo;
  setText($("#aliasRepo"), repo);
  $("#aliasInput").value = aliasOf(repo);
  $("#aliasRemoveBtn").hidden = !aliasOf(repo);
  setText($("#aliasError"), "");
  $("#aliasPanel").hidden = false;
  $("#aliasInput").focus();
}

function hideAlias() {
  state.aliasRepo = "";
  $("#aliasPanel").hidden = true;
}

// Closed by the user: the focus goes back to the button that opened it.
function closeAlias() {
  const repo = state.aliasRepo;
  hideAlias();
  Array.from($("#modelCards").querySelectorAll("[data-alias]"))
    .find((btn) => btn.dataset.alias === repo)?.focus();
}

async function saveAlias(alias = $("#aliasInput").value.trim()) {
  const repo = state.aliasRepo;
  if (!repo || state.aliasSaving) return;
  state.aliasSaving = true;
  setText($("#aliasError"), "");
  try {
    await api("/api/models/alias", { method: "POST", body: JSON.stringify({ repo, alias }) });
  } catch (err) {
    setText($("#aliasError"), err.message);
    return;
  } finally {
    state.aliasSaving = false;
  }
  toast(alias ? "Alias saved" : "Alias removed");
  if (state.aliasRepo === repo) closeAlias();
  refreshState();
}

async function modelAction(act, repo) {
  if (act === "delete" && !confirm(`Delete ${modelLabel(repo)} from disk?`)) return;
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
          <div class="card-name">${esc(displayName(d.repo))}</div>
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
    ? served.map((v) => `<option value="${esc(v)}">${esc(modelLabel(v))}</option>`).join("")
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
  setText($("#globalStatus"), ready ? served.map(displayName).join(", ")
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

function addMeta(el, text) {
  if (!text) return;
  const meta = document.createElement("div");
  meta.className = "msg-meta";
  meta.textContent = text;
  el.appendChild(meta);
}

async function sendMessage() {
  const input = $("#input");
  const text = input.value.trim();
  if (!text || state.streaming || state.chatBusy || state.chatStatus !== "ready") return;
  const repo = $("#chatModelSelect").value;
  const extras = chatExtras();
  if (extras.invalid) {
    showChatPanel("settings");
    extras.invalid.focus();
    return;
  }
  // Absent for a temporary chat: nothing of it is saved.
  const save = $("#chatTemporary").checked ? undefined
    : state.chatRef ? { ...state.chatRef } : { id: null };
  setText($("#chatSaveError"), "");

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
      body: JSON.stringify({
        repo, messages: state.messages, system: extras.system, settings: extras.settings, save,
      }),
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
        addMeta(assistantEl, statsLine(data));
        // Only for the chat still on screen: New chat may have replaced it.
        if (state.messages === history) {
          if (data.chat) state.chatRef = { id: data.chat.id, updated: data.chat.updated };
          setText($("#chatSaveError"), data.save_error || "");
        }
        if (data.chat && !$("#historyPanel").hidden) loadHistory();
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
    paintSaveState();
    refreshState();
  }
}

/* ───────────── saved chats ───────────── */
// Chat text, titles and file names are drawn as text nodes only, never as markup.
// field, key, lowest, highest, whole number, name
const CHAT_SETTINGS = [
  ["#chatMaxTokens", "max_tokens", 64, 131072, true, "Max reply tokens"],
  ["#chatTemp", "temperature", 0, 2, false, "Temperature"],
  ["#chatTopP", "top_p", 0.05, 1, false, "Top p"],
];
// button, panel, what takes the focus
const CHAT_PANELS = {
  history: ["#historyBtn", "#historyPanel", "#historySave"],
  settings: ["#chatSettingsBtn", "#chatSettingsPanel", "#chatSystem"],
};
const REPLY_RUNS = "Wait for the reply to end, or press Stop,";

const post = (body) => ({ method: "POST", body: JSON.stringify(body) });

// What the settings panel asks for; an empty field leaves the Configuration value.
// invalid is the first field that cannot be sent.
function chatExtras() {
  const settings = {};
  let invalid = null;
  let problem = "";
  for (const [field, key, low, high, whole, name] of CHAT_SETTINGS) {
    const input = $(field);
    const text = String(input.value).trim();
    const value = Number(text);
    const bad = !!input.validity?.badInput
      || (!!text && !(value >= low && value <= high && (!whole || Number.isInteger(value))));
    input.setAttribute("aria-invalid", String(bad));
    if (bad) {
      invalid ||= input;
      problem ||= `${name} must be ${whole ? "a whole number " : ""}from ${low} to ${high}.`;
    } else if (text) settings[key] = value;
  }
  setText($("#chatSettingsError"), problem);
  return { system: $("#chatSystem").value, settings, invalid };
}

function fillSettings(chat) {
  $("#chatSystem").value = chat?.system || "";
  for (const [field, key] of CHAT_SETTINGS) {
    $(field).value = String(chat?.settings?.[key] ?? "");
    $(field).removeAttribute("aria-invalid");
  }
  setText($("#chatSettingsError"), "");
  setText($("#chatSettingsState"), "");
}

async function saveChatSettings() {
  const { system, settings, invalid } = chatExtras();
  const say = (text) => setText($("#chatSettingsState"), text);
  if (invalid) { say(""); invalid.focus(); return; }
  const ref = state.chatRef;
  if (state.saveHistory === "off" || $("#chatTemporary").checked) {
    return say("Used from the next message. Not saved.");
  }
  if (!ref) return say("Used from the next message.");
  if (state.streaming) return say("A reply is running: used from the next message.");
  if (state.chatBusy) return;
  state.chatBusy = true;
  say("Saving…");
  try {
    const chat = await api("/api/chats/settings",
      post({ id: ref.id, updated: ref.updated, system, settings }));
    if (state.chatRef !== ref) return;
    state.chatRef = { id: chat.id, updated: chat.updated };
    say("Saved with this chat.");
  } catch (err) {
    if (state.chatRef !== ref) return;
    say("");
    setText($("#chatSettingsError"), err.message);
  } finally {
    state.chatBusy = false;
  }
}

function paintSaveState() {
  setText($("#chatSaveState"), $("#chatTemporary").checked
    ? "Temporary chat: nothing is saved."
    : state.saveHistory === "off"
      ? "Saving is off: nothing new is saved. Turn Save history on under History."
      : state.chatRef ? "Saved chat."
        : state.messages.length ? "Not saved yet: the next reply saves this whole chat."
          : "This chat will be saved on this Mac.");
}

function paintSaveHistory(value) {
  state.saveHistory = value === "off" ? "off" : "on";
  $("#historySave").checked = state.saveHistory === "on";
  paintSaveState();
}

async function chooseSaveHistory(value) {
  const before = state.saveHistory;
  state.historySaving += 1;
  paintSaveHistory(value);
  try {
    await api("/api/ui/settings", post({ save_history: value }));
  } catch (err) {
    paintSaveHistory(before);
    toast(`Could not change Save history: ${err.message}`, true);
  } finally {
    state.historySaving -= 1;
  }
  refreshState();
}

// Throws the chat on screen away; a saved one stays in History.
function newChat() {
  // The reply being generated belongs to that chat.
  if (state.streaming) api("/api/chat/stop", post({})).catch(() => {});
  state.messages = [];
  state.chatRef = null;
  state.openSeq += 1;
  $("#messages").replaceChildren();
  $("#chatEmpty").hidden = false;
  fillSettings(null);
  setText($("#chatSaveError"), "");
  paintSaveState();
}

// name is "history", "settings" or "" for neither.
function showChatPanel(name) {
  for (const [key, [button, panel]] of Object.entries(CHAT_PANELS)) {
    $(panel).hidden = key !== name;
    $(button).setAttribute("aria-expanded", String(key === name));
  }
  if (!name) return;
  $(CHAT_PANELS[name][2]).focus();
  if (name === "history") {
    hideRename();
    loadHistory();
  }
}

// Closed by the user: the focus goes back to the button that opened it.
function closeChatPanel(name) {
  showChatPanel("");
  $(CHAT_PANELS[name][0]).focus();
}

const historyStatus = (text) => setText($("#historyStatus"), text);

function textNode(tag, className, text = "") {
  const el = document.createElement(tag);
  el.className = className;
  el.textContent = text;
  return el;
}

function rowButton(className, text, label, onClick) {
  const button = textNode("button", className, text);
  button.setAttribute("type", "button");
  button.setAttribute("aria-label", label);
  button.addEventListener("click", onClick);
  return button;
}

function rowOf(...parts) {
  const row = textNode("li", "history-row");
  for (const part of parts) row.appendChild(part);
  return row;
}

function historyRow(chat) {
  const open = rowButton("history-open", chat.title, `Open chat: ${chat.title}`,
    () => openChat(chat.id));
  if (state.chatRef?.id === chat.id) open.setAttribute("aria-current", "true");
  const meta = textNode("span", "history-meta", [
    new Date(chat.updated).toLocaleString(),
    `${chat.count} messages`,
    chat.model ? displayName(chat.model) : "",
  ].filter(Boolean).join(" · "));
  const rename = rowButton("btn btn-mini", "Rename", `Rename chat: ${chat.title}`,
    () => openRename(chat));
  const remove = rowButton("btn btn-mini btn-danger", "Delete", `Delete chat: ${chat.title}`,
    () => deleteChat(chat.id, `"${chat.title}"`));
  state.historyRows.set(chat.id, rename);
  return rowOf(open, meta, rename, remove);
}

function brokenRow({ id, reason }) {
  const name = `${id}.json`;
  return rowOf(textNode("span", "history-broken", `Broken file ${name}: ${reason}`),
    rowButton("btn btn-mini btn-danger", "Delete", `Delete broken file ${name}`,
      () => deleteChat(id, `the file ${name}`)));
}

async function loadHistory() {
  const seq = ++state.historySeq;
  const query = $("#historyQuery").value.trim();
  try {
    const { chats, broken } = await (query
      ? api("/api/chats/search", post({ query })) : api("/api/chats"));
    if (seq !== state.historySeq) return;
    state.historyRows = new Map();
    $("#historyList").replaceChildren(...chats.map(historyRow));
    $("#historyBroken").replaceChildren(...broken.map(brokenRow));
    historyStatus([
      chats.length ? `${chats.length} saved chat(s)${query ? " match the search" : ""}.`
        : query ? "No saved chat matches the search." : "No saved chats yet.",
      broken.length ? `${broken.length} file(s) cannot be read.` : "",
    ].filter(Boolean).join(" "));
  } catch (err) {
    if (seq === state.historySeq) historyStatus(`Could not read the saved chats: ${err.message}`);
  }
}

async function openChat(id) {
  if (state.streaming) return historyStatus(`${REPLY_RUNS} before opening a chat.`);
  const seq = ++state.openSeq;
  let chat;
  try {
    chat = await api(`/api/chats/get?id=${encodeURIComponent(id)}`);
  } catch (err) {
    return historyStatus(`Could not open the chat: ${err.message}`);
  }
  // A message, New chat or a later Open came first.
  if (seq !== state.openSeq || state.streaming) return;
  state.messages = chat.messages.map(({ role, content }) => ({ role, content }));
  state.chatRef = { id: chat.id, updated: chat.updated };
  $("#chatTemporary").checked = false;
  $("#messages").replaceChildren();
  $("#chatEmpty").hidden = chat.messages.length > 0;
  for (const message of chat.messages) {
    const reply = message.role === "assistant";
    const el = addMessage(message.role, reply ? "" : message.content);
    if (!reply) continue;
    paintReply(el, { thinking: message.thinking || "", answer: message.content });
    addMeta(el, statsLine(message));
  }
  fillSettings(chat);
  setText($("#chatSaveError"), "");
  paintSaveState();
  showChatPanel("");
  setText($("#chatAnnounce"), `Opened chat: ${chat.title}`);
  const input = $("#input");
  (input.disabled ? $("#historyBtn") : input).focus();
}

function openRename(chat) {
  state.renameId = chat.id;
  $("#renameInput").value = chat.title;
  setText($("#renameError"), "");
  $("#renameForm").hidden = false;
  $("#renameInput").focus();
}

function hideRename() {
  state.renameId = "";
  $("#renameForm").hidden = true;
}

function closeRename() {
  const id = state.renameId;
  hideRename();
  (state.historyRows.get(id) || $("#historyQuery")).focus();
}

async function saveTitle() {
  const id = state.renameId;
  if (!id) return;
  setText($("#renameError"), "");
  try {
    await api("/api/chats/rename", post({ id, title: $("#renameInput").value }));
  } catch (err) {
    return setText($("#renameError"), err.message);
  }
  await loadHistory();
  if (state.renameId === id) closeRename();
}

async function deleteChat(id, name) {
  if (!confirm(`Delete ${name}? This cannot be undone, and it is not a secure erase.`)) return;
  try {
    await api("/api/chats/delete", post({ id, confirm: true }));
  } catch (err) {
    return historyStatus(`Could not delete: ${err.message}`);
  }
  if (state.chatRef?.id === id) newChat();
  if (state.renameId === id) hideRename();
  await loadHistory();
  $("#historyQuery").focus(); // the button that was pressed is gone
}

async function deleteAllChats() {
  // A running reply would be saved again right after.
  if (state.streaming) return historyStatus(`${REPLY_RUNS} before deleting all chats.`);
  if (!confirm("Delete every saved chat? This cannot be undone, and it is not a secure erase.")) {
    return;
  }
  let result;
  try {
    result = await api("/api/chats/delete-all", post({ confirm: true }));
  } catch (err) {
    return historyStatus(`Could not delete: ${err.message}`);
  }
  if (state.chatRef) newChat();
  hideRename();
  await loadHistory();
  toast(result.failed
    ? `${result.deleted} chat(s) deleted, ${result.failed} file(s) could not be deleted.`
    : `${result.deleted} chat(s) deleted.`, !!result.failed);
}

function wireSavedChats() {
  for (const [name, [button, panel]] of Object.entries(CHAT_PANELS)) {
    $(button).addEventListener("click", () => {
      if ($(panel).hidden) showChatPanel(name);
      else closeChatPanel(name);
    });
    $(panel).addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      if (name === "history" && state.renameId) closeRename();
      else closeChatPanel(name);
    });
  }
  $("#historyClose").addEventListener("click", () => closeChatPanel("history"));
  $("#chatSettingsClose").addEventListener("click", () => closeChatPanel("settings"));
  // Switching starts a new chat, so no temporary message is ever saved afterwards.
  $("#chatTemporary").addEventListener("change", () => {
    if (state.messages.length || state.chatRef) newChat();
    else paintSaveState();
  });
  $("#historySave").addEventListener("change",
    () => chooseSaveHistory($("#historySave").checked ? "on" : "off"));
  // The page's policy allows no forms: a button, and Enter in the field it belongs to.
  const act = (button, run, ...fields) => {
    $(button).addEventListener("click", run);
    for (const field of fields) {
      $(field).addEventListener("keydown", (e) => {
        if (e.key !== "Enter" || e.isComposing) return;
        e.preventDefault();
        run();
      });
    }
  };
  act("#historySearchBtn", loadHistory, "#historyQuery");
  act("#renameSaveBtn", saveTitle, "#renameInput");
  act("#chatSettingsSave", saveChatSettings, ...CHAT_SETTINGS.map(([field]) => field));
  $("#historyClear").addEventListener("click", () => {
    $("#historyQuery").value = "";
    $("#historyQuery").focus();
    loadHistory();
  });
  $("#renameCancel").addEventListener("click", closeRename);
  $("#historyDeleteAll").addEventListener("click", deleteAllChats);
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
    state.serverLast = [body.repo];
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
  if (!set.length) { toast("No model chosen. Press + Add model, then Start.", true); return; }
  const background = $("#serverBackground").checked;
  const applying = !!state.server?.running;
  state.serverStartError = "";
  try {
    await sendStart({ ...serverChoice(set), background },
      applying ? "Restarting server…"
        : background ? "Starting in background…" : "Starting server…");
  } catch (err) {
    // The reason stays on the card.
    state.serverStartError = err.message;
    paintServerCard();
    toast(err.message, true);
  }
}

/* ───────────── confirm before a start ───────────── */
const CONFIRM_KEYS = ["max_context_tokens", "server_reasoning", "prompt_cache_size",
  "prompt_cache_bytes", "kv_bits", "server_max_reply_tokens"];

function closeStartConfirm() {
  state.confirm = null;
  $("#startConfirm").hidden = true;
  $("#confirmError").textContent = "";
}

// Shows what the start would use. Nothing is saved or started here.
async function openStartConfirm() {
  if (state.serverSending) return;
  const set = serverSet();
  if (!set.length) { toast("No model chosen. Press + Add model, then Start.", true); return; }
  const choice = JSON.stringify(serverChoice(set));
  const engine = engineId();
  let view;
  try {
    view = await api(`/api/config${repoQuery(set[0])}`);
  } catch (err) {
    toast(`Could not read the settings: ${err.message}. Press Start again.`, true);
    return;
  }
  if (choice !== JSON.stringify(serverChoice())) return;
  const { settings = {}, performance } = view;
  const mode = settings.performance_mode?.value;
  const loaded = {};
  setText($("#confirmEngine"), engineInfo(engine)?.name || (engine === "mlx" ? "MLX" : engine));
  setText($("#confirmModel"), modelLabel(set[0]));
  for (const key of CONFIRM_KEYS) {
    const input = $(`#confirm-${key}`);
    const entry = settings[key] || {};
    let value = entry.value ?? "";
    let note = entry.inherited != null ? `Empty: the All models value (${entry.inherited}).`
      : "Empty: automatic.";
    let off = "";
    if (key === "kv_bits") {
      value = mode === "save_memory" ? 8 : mode === "advanced" ? value : "";
      note = "A change sets this model's performance mode to Advanced, or Standard when off.";
      const why = performance?.blocked?.save_memory;
      off = why ? `Not available for this model: ${why}.` : "";
    } else if (key === "server_reasoning") {
      note = "A model without a thinking switch ignores it.";
    } else if (key === "max_context_tokens") {
      if (engine === "mlx") off = "MLX has no context limit.";
      else note += " Older text is dropped beyond the limit.";
    }
    // MLX takes no limit: a value saved for OptiQ is not shown as one.
    loaded[key] = off && key === "max_context_tokens" ? "" : String(value);
    input.value = loaded[key];
    input.disabled = !!off;
    setText($(`#confirm-${key}-note`), off || note);
  }
  $("#confirmBackground").checked = $("#serverBackground").checked;
  $("#confirmError").textContent = "";
  state.confirm = { choice, repo: set[0], loaded, settings };
  $("#startConfirm").hidden = false;
  $("#confirmCancelBtn").focus();
}

// Saves what was changed, then starts. A refused value starts nothing.
async function confirmStart() {
  const open = state.confirm;
  if (!open || open.sending || state.serverSending) return;
  const values = {};
  for (const key of CONFIRM_KEYS) {
    const input = $(`#confirm-${key}`);
    const raw = String(input.value).trim();
    if (input.disabled || (raw === open.loaded[key] && !input.validity?.badInput)) continue;
    const label = $(`label[for="confirm-${key}"]`)?.textContent || key.replace(/_/g, " ");
    const { min, max } = open.settings[key] || {};
    const number = Number(raw);
    if (input.validity?.badInput || (key !== "server_reasoning" && key !== "kv_bits" && raw &&
      !(Number.isInteger(number) && number >= min && number <= max))) {
      $("#confirmError").textContent =
        `${label}: use a whole number from ${min} to ${max}, or leave it empty.`;
      return;
    }
    if (key === "kv_bits") {
      values.performance_mode = raw ? "advanced" : "standard";
      if (raw) values.kv_bits = number;
    } else {
      values[key] = raw === "" ? null : key === "server_reasoning" ? raw : number;
    }
  }
  open.sending = true;
  try {
    if (Object.keys(values).length) {
      await api("/api/server/settings",
        { method: "POST", body: JSON.stringify({ repo: open.repo, values }) });
    }
  } catch (err) {
    open.sending = false;
    $("#confirmError").textContent = `Nothing was started. ${err.message} Change it and try again.`;
    return;
  }
  // Cancel or a changed card while saving: no start.
  if (state.confirm !== open) return;
  $("#serverBackground").checked = $("#confirmBackground").checked;
  state.boxPick[engineId()] = $("#confirmBackground").checked;
  closeStartConfirm();
  // The button just pressed is gone: the focus goes back to Start, which moves it on.
  $("#serverStartBtn").focus();
  await startFromCard();
}

async function stopServer() {
  try { await api("/api/server/stop", { method: "POST", body: "{}" }); refreshState(); }
  catch (err) { toast(err.message, true); }
}

/* ───────────── wiring ───────────── */
function wire() {
  wireEngines();
  $("#testBtn").addEventListener("click", testConnection);
  $("#setDefault").addEventListener("click", setDefaultModel);
  $("#autostartSwitch").addEventListener("change", switchAutostart);
  $("#autostartSwitch").addEventListener("click", (event) => {
    // aria-disabled keeps the focus but not the box: stop it flipping.
    if (event.target.getAttribute("aria-disabled") === "true") event.preventDefault();
  });
  for (const name of THEMES) {
    $(`#theme-${name}`).addEventListener("change", () => chooseTheme(name));
  }
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

  $("#newChatBtn").addEventListener("click", newChat);
  wireSavedChats();

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
  $("#aliasSaveBtn").addEventListener("click", () => saveAlias());
  $("#aliasRemoveBtn").addEventListener("click", () => saveAlias(""));
  $("#aliasCancelBtn").addEventListener("click", closeAlias);
  $("#aliasInput").addEventListener("keydown", (e) => {
    if (e.isComposing) return;
    if (e.key === "Enter") { e.preventDefault(); return saveAlias(); }
    if (e.key === "Escape") { e.preventDefault(); closeAlias(); }
  });
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
    state.boxPick[engineId()] = $("#serverBackground").checked;
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

  $("#serverStartBtn").addEventListener("click", openStartConfirm);
  $("#confirmStartBtn").addEventListener("click", confirmStart);
  $("#confirmCancelBtn").addEventListener("click", () => {
    closeStartConfirm();
    $("#serverStartBtn").focus();
  });
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
  $("#serverRestartBtn").addEventListener("click", async () => {
    // What ran last, not an unapplied choice; the card only when nothing is known.
    let set = usable(state.serverLast);
    if (!set.length) set = serverSet();
    if (!set.length) set = usable([state.snapshot?.server?.repo].filter(Boolean));
    if (!set.length) { toast("No model chosen. Press + Add model, then Start.", true); return; }
    const box = $("#serverBackground").checked;
    const ran = state.serverRan ||
      { engine: state.server?.last_engine || "mlx", anthropic: state.server?.last_anthropic };
    // The box may show another engine's choice: that one is not for this start.
    const background = state.serverLastBackground ??
      (ran.engine === engineId() ? box : backgroundFor(ran.engine));
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

  $("#installBtn").addEventListener("click", () => installEngine("mlx"));

  // Toggle Active Downloads section
  $("#toggleDownloads").addEventListener("click", () => setDownloadsOpen(!state.downloadsOpen));
}

/* ───────────── boot ───────────── */
(async function boot() {
  wire();
  showView(location.hash.slice(1) || VIEWS[0]);
  // Without a token every request is refused: say so once and ask for nothing.
  if (!API_TOKEN) return toast("This page has no session. Open MLX Studio from its app icon.", true);
  await refreshState();
  loadCatalog();
  state.pollTimer = setInterval(refreshState, 2000);
})();
