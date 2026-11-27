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
  lastLoaded: null,
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
  keptSet: false,
  keptBox: false,
  serverStartError: "",
  serverSending: false,
  lastApplyNotice: null,
  estimate: null,
  estimateError: "",
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
  // A button with a slot keeps it when its action changes (Load becomes Unload).
  return Object.entries(now.dataset || {})
    .filter(([k]) => !(now.dataset.slot && ["act", "catDownload", "catLoad"].includes(k)))
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

/* ───────────── loading a Chat model ───────────── */
// A refused load stays on screen, and is announced, until the next try or Dismiss.
// A toast was gone before a refusal with figures could be read.
function showLoadProblem(text) {
  setText($("#loadProblem"), text);
  $("#loadProblemBox").classList.toggle("show", !!text);
  $("#loadProblemDismiss").hidden = !text;
}

// True when the app accepted the load.
async function loadModel(repo) {
  showLoadProblem("");
  try {
    await api("/api/load", { method: "POST", body: JSON.stringify({ repo }) });
    return true;
  } catch (err) {
    showLoadProblem(err.message);
    return false;
  }
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
const VIEWS = ["chat", "models", "download", "server", "logs", "settings"];

function showView(name) {
  if (!VIEWS.includes(name)) name = "chat";
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
  const { app, hardware, python, versions, models, loaded, server, downloads, install } = snap;

  // sidebar
  setText($("#appVersion"), `v${app.version}`);
  setText($("#hwInfo"), `${hardware.chip}\n${hardware.ram} unified`);

  const loadedName = loaded.repo ? loaded.repo.split("/").pop() : null;
  const globalDot = $("#globalDot");
  const globalStatus = $("#globalStatus");
  if (loaded.loading) {
    globalDot.className = "dot dot-live";
    setText(globalStatus, `Loading ${loaded.loading.split("/").pop()}…`);
  } else if (loaded.repo) {
    globalDot.className = "dot dot-ok";
    setText(globalStatus, loadedName);
  } else {
    globalDot.className = "dot dot-warn";
    setText(globalStatus, "No model loaded");
  }

  const updateAvailable =
    versions.mlx_update || versions.mlx_lm_update || versions.huggingface_hub_update ||
    versions.mlx_missing || app.update;
  $("#navUpdateDot").classList.toggle("hidden", !updateAvailable);

  // chat header
  const loadState = $("#loadState");
  if (loaded.loading) {
    setHTML(loadState, `<span class="dot dot-live"></span>Loading ${esc(loaded.loading.split("/").pop())}…`);
  } else if (loaded.repo) {
    setHTML(loadState, `<span class="dot dot-ok"></span>Loaded — ${esc(loadedName)}`);
  } else if (loaded.error) {
    setHTML(loadState, `<span class="dot dot-bad"></span>Load failed`);
  } else {
    setHTML(loadState, `<span class="dot dot-warn"></span>Not loaded`);
  }
  $("#loadBtn").hidden = !!loaded.repo || !!loaded.loading;
  $("#unloadBtn").hidden = !loaded.repo;
  $("#loadBtn").disabled = !!loaded.loading;

  // server
  const held = document.activeElement;
  const unguarded = !!(server.running && server.unguarded);
  const runningDot = unguarded ? "dot-warn" : "dot-ok";
  $("#serverUnguarded").hidden = !unguarded;
  if (unguarded && !state.lastServerUnguarded) {
    toast("Restart the API server to apply the 2.0.10 protection (API Server page)", true);
  }
  state.lastServerUnguarded = unguarded;
  setHTML($("#serverState"), server.running
    ? `<span class="dot ${runningDot}"></span>${esc(runningNames(server))}`
    : `<span class="dot dot-warn"></span>Stopped`);
  $("#navServerDot").className = "dot " + (server.running ? runningDot : "");
  setText($("#navServerText"),
    unguarded ? "server unprotected" : server.running ? "server running" : "server stopped");
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
  fillSelect("#chatModelSelect", ready, loaded.repo);
  state.readyModels = ready;
  renderServerMemory(server);
  refreshEstimate(snap);
  paintServerCard();
  // A Restart button goes away with its notice: leave the focus on something that stays.
  const gone = [["#serverRestartBtn", "#serverNotice"],
    ["#serverUnguardedRestart", "#serverUnguarded"]];
  if (gone.some(([button, box]) => held === $(button) && $(box).hidden)) spareControl().focus();
  renderToolsWarning(server);
  renderModelCards(models, loaded, downloads);
  renderDownloads(downloads);
  renderCatalog();
  renderSearch();
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
    $("#serverBackground").checked !== !!server.background);
}

// The model fields of a start request.
function serverChoice(set = serverSet()) {
  const [repo = "", second] = set;
  return second ? { repo, also: [second] } : { repo };
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

function renderServerMemory(server) {
  const memory = server.running ? server.memory : null;
  $("#serverMemory").hidden = !memory;
  if (memory) {
    const used = `Using ${gb(memory.active_bytes)} GB (peak ${gb(memory.peak_bytes)} GB)`;
    // Only a two-model server is held to a budget.
    setText($("#serverMemory"), holdsTwo(server)
      ? `${used} of a ${gb(memory.budget_bytes)} GB budget. ` +
        "Measured by the API server, refreshed about every 10 s."
      : `${used}. Measured at the last model load. A model on the Chat tab is not included.`);
  }
  const tokens = holdsTwo(server) ? server.context_tokens : null;
  $("#serverPromptHint").hidden = !tokens;
  setText($("#serverPromptHint"), tokens
    ? `Keep prompts to ${tokens} tokens or fewer. The prompt cache holds about ` +
      `${2 * tokens} tokens in total; above that, older prompts are dropped and read again.`
    : "");
  const [title, notice] = multiNotice(server);
  $("#serverMulti").hidden = !notice;
  setText($("#serverMultiTitle"), title);
  setText($("#serverMultiText"), notice);
  $("#chatServerNote").hidden = !holdsTwo(server);
  setText($("#chatServerNote"), holdsTwo(server)
    ? "The API server keeps two models loaded. A Chat model loads only if it fits beside them."
    : "");
}

const ESTIMATE_RETRY_MS = 10000;

// Asked again when the choice or the app's own chat model changes, and a while after
// the last answer, as the free memory moves. An unchanged running server keeps its
// answer; an edit waiting for Apply does not.
async function refreshEstimate(snap) {
  const choice = serverChoice();
  const key = choice.repo
    ? JSON.stringify([choice, snap?.loaded?.repo, snap?.loaded?.loading])
    : "";
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
  const fallback = unread ? " Free memory could not be read; using the fixed 52% rule." : "";
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
  const shrinkNote = estimate.free_is_limit ? " Close other apps for more." : "";
  return `Two models: about ${Math.floor(estimate.context_tokens / 1000)}K tokens each ` +
    `(one model alone can use more). ${uses}` +
    (unread ? fallback : shrinkNote);
}

function showEstimate(estimate, error = "") {
  state.estimate = estimate;
  state.estimateError = error;
  paintServerCard();
}

const kilo = (tokens) => Math.floor(tokens / 1024);

// What one row can promise about its context; empty when nothing is known.
function contextWords(repo, view) {
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

  // A refusal is already on screen; a running server is not described as refused.
  const details = !set.length || !estimate || (two && estimate.fits === false) ? ""
    : two ? estimateWords(estimate)
      : `Needs about ${gb(estimate.total_bytes)} GB with a ` +
        `${Math.floor(estimate.context_tokens / 1000)}K-token reply; the API server may use ` +
        `${gb(estimate.budget_bytes)} GB.`;
  showLine("#serverEstimate", details);
  showLine("#serverContextHint", !two || !estimate || estimate.fits === false ? "" :
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
  start.disabled = !!server.starting || !set.length || (server.running && !pending) || refused;
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
function renderModelCards(models, loaded, downloads) {
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
    const isLoaded = loaded.repo === m.repo;
    const partial = m.complete === false;
    const badge = isLoaded ? '<span class="card-tag ok">loaded</span>'
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
      (partial
        ? button("download", "Resume download", "", MAIN)
        : button(isLoaded ? "unload" : "load", isLoaded ? "Unload" : "Load", "", MAIN)) +
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
  if (act === "load") {
    if (await loadModel(repo)) refreshState();
    return;
  }
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
      ? `<button class="btn btn-mini" disabled>Installed</button>
           <button class="btn btn-mini" ${slot} data-cat-load="${esc(m.repo)}">Load</button>`
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
  wrap.querySelectorAll("[data-cat-load]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (!(await loadModel(btn.dataset.catLoad))) return;
      toast("Loading model…");
      refreshState();
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

/* ───────────── chat ───────────── */
function addMessage(role, text, meta) {
  const wrap = $("#messages");
  const el = document.createElement("div");
  el.className = `msg msg-${role}`;
  el.innerHTML = `
    <div class="msg-role">${role === "user" ? "You" : "Assistant"}</div>
    <div class="msg-body"></div>
    ${meta ? `<div class="msg-meta">${esc(meta)}</div>` : ""}`;
  el.querySelector(".msg-body").textContent = text;
  wrap.appendChild(el);
  scrollChat();
  return el;
}

function scrollChat() {
  const s = $("#chatScroll");
  s.scrollTop = s.scrollHeight;
}

async function sendMessage() {
  const input = $("#input");
  const text = input.value.trim();
  if (!text || state.streaming) return;
  if (!state.snapshot?.loaded?.repo) { toast("Load a model first", true); return; }

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
  body.classList.add("cursor");

  state.streaming = true;
  $("#sendBtn").hidden = true;
  $("#stopBtn").hidden = false;

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: apiHeaders(),
      body: JSON.stringify({ messages: state.messages }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: res.statusText }));
      throw new Error(err.error || "chat failed");
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let full = "";

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
        const ev = evLine.slice(7).trim();
        const data = JSON.parse(dataLine.slice(6));
        if (ev === "token") {
          full += data;
          body.textContent = full;
          scrollChat();
        } else if (ev === "done") {
          body.classList.remove("cursor");
          const meta = `${data.tokens} tokens · ${data.tps} tok/s · ${data.peak_gb} GB peak`;
          const metaEl = document.createElement("div");
          metaEl.className = "msg-meta";
          metaEl.textContent = meta;
          assistantEl.appendChild(metaEl);
          if (state.messages === history) history.push({ role: "assistant", content: full });
          replied = true;
        } else if (ev === "error") {
          body.classList.remove("cursor");
          assistantEl.classList.add("msg-error");
          body.textContent = full || data;
          toast(data, true);
        }
      }
    }
  } catch (err) {
    body.classList.remove("cursor");
    assistantEl.classList.add("msg-error");
    body.textContent = err.message;
    toast(err.message, true);
  } finally {
    // A turn without a reply would break role alternation on every later send.
    const at = state.messages.indexOf(turn);
    if (!replied && at !== -1) state.messages.splice(at, 1);
    body.classList.remove("cursor");
    setText($("#chatAnnounce"), replied ? "Reply complete" : "Reply failed");
    state.streaming = false;
    $("#sendBtn").hidden = false;
    $("#stopBtn").hidden = true;
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
  }
  toast(message);
  refreshState().finally(() => { state.serverSending = false; });
}

/* ───────────── wiring ───────────── */
function wire() {
  createPicker("#chatModelSelect");
  createPicker("#serverModelSelect", {
    onLeave(escaped) {
      showAddPicker(false);
      if (escaped) $("#serverAddBtn").focus();
    },
  });

  $("#loadBtn").addEventListener("click", async () => {
    const repo = $("#chatModelSelect").value;
    if (!repo) { toast("No model selected", true); return; }
    if (await loadModel(repo)) refreshState();
  });
  $("#loadProblemDismiss").addEventListener("click", () => {
    showLoadProblem("");
    // The button is gone: leave the focus on something that stays.
    $(".nav-item.active").focus();
  });

  $("#unloadBtn").addEventListener("click", async () => {
    try { await api("/api/unload", { method: "POST", body: "{}" }); refreshState(); }
    catch (err) { toast(err.message, true); }
  });

  $("#newChatBtn").addEventListener("click", () => {
    // The reply being generated belongs to the chat that is being thrown away.
    if (state.streaming) api("/api/chat/stop", { method: "POST", body: "{}" }).catch(() => {});
    state.messages = [];
    $("#messages").innerHTML = "";
    $("#chatEmpty").hidden = false;
  });

  $("#sendBtn").addEventListener("click", sendMessage);
  $("#stopBtn").addEventListener("click", async () => {
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

  $("#serverStartBtn").addEventListener("click", async () => {
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
  });
  $("#serverStopBtn").addEventListener("click", async () => {
    try { await api("/api/server/stop", { method: "POST", body: "{}" }); refreshState(); }
    catch (err) { toast(err.message, true); }
  });
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
    try {
      await sendStart({ ...serverChoice(set), background }, "Restarting server…",
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
  showView(location.hash.slice(1) || "chat");
  // Without a token every request is refused: say so once and ask for nothing.
  if (!API_TOKEN) return toast("This page has no session. Open MLX Studio from its app icon.", true);
  await refreshState();
  loadCatalog();
  state.pollTimer = setInterval(refreshState, 2000);
})();
