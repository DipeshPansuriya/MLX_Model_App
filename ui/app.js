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
  lastServerRunning: false,
};

/* ───────────── utilities ───────────── */
async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).error || msg; } catch (_) {}
    throw new Error(msg);
  }
  return res.json();
}

function toast(text, bad = false) {
  let el = $("#toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    document.body.appendChild(el);
  }
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
  $$(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${name}`));
  if (location.hash.slice(1) !== name) location.hash = name;
  if (name === "logs") loadLogs();
  if (name === "models") refreshModels();
  if (name === "download") loadCatalog();
}
$$(".nav-item").forEach((btn) => btn.addEventListener("click", () => showView(btn.dataset.view)));
window.addEventListener("hashchange", () => showView(location.hash.slice(1)));

/* ───────────── state refresh ───────────── */
async function refreshState() {
  try {
    const snap = await api("/api/state");
    state.snapshot = snap;
    render(snap);
  } catch (err) {
    console.warn("state refresh failed", err);
  }
}

function render(snap) {
  const { app, hardware, python, versions, models, loaded, server, downloads, install } = snap;

  // sidebar
  $("#appVersion").textContent = `v${app.version}`;
  $("#hwInfo").textContent = `${hardware.chip}\n${hardware.ram} unified`;
  $("#navModelCount").textContent = models.length;

  const loadedName = loaded.repo ? loaded.repo.split("/").pop() : null;
  const globalDot = $("#globalDot");
  const globalStatus = $("#globalStatus");
  if (loaded.loading) {
    globalDot.className = "dot dot-live";
    globalStatus.textContent = `Loading ${loaded.loading.split("/").pop()}…`;
  } else if (loaded.repo) {
    globalDot.className = "dot dot-ok";
    globalStatus.textContent = loadedName;
  } else {
    globalDot.className = "dot dot-warn";
    globalStatus.textContent = "No model loaded";
  }

  const updateAvailable =
    versions.mlx_update || versions.mlx_lm_update || versions.mlx_missing || app.update;
  $("#navUpdateDot").classList.toggle("hidden", !updateAvailable);

  // chat header
  const loadState = $("#loadState");
  if (loaded.loading) {
    loadState.innerHTML = `<span class="dot dot-live"></span>Loading ${esc(loaded.loading.split("/").pop())}…`;
  } else if (loaded.repo) {
    loadState.innerHTML = `<span class="dot dot-ok"></span>Loaded — ${esc(loadedName)}`;
  } else if (loaded.error) {
    loadState.innerHTML = `<span class="dot dot-bad"></span>Load failed`;
  } else {
    loadState.innerHTML = `<span class="dot dot-warn"></span>Not loaded`;
  }
  $("#loadBtn").hidden = !!loaded.repo || !!loaded.loading;
  $("#unloadBtn").hidden = !loaded.repo;
  $("#loadBtn").disabled = !!loaded.loading;

  // server
  $("#serverState").innerHTML = server.running
    ? `<span class="dot dot-ok"></span>Running — ${esc((server.repo || "").split("/").pop())}`
    : `<span class="dot dot-warn"></span>Stopped`;
  $("#serverStartBtn").hidden = server.running;
  $("#serverStopBtn").hidden = !server.running;
  $("#navServerDot").className = "dot " + (server.running ? "dot-ok" : "");
  $("#endpointText").textContent = server.endpoint;
  $("#serverModePill").textContent = server.running
    ? (server.background ? "background" : "foreground")
    : "stopped";
  $("#serverModePill").className = "pill " + (server.running ? (server.background ? "ok" : "") : "warn");
  const notice = $("#serverNotice");
  if (server.down_notice) {
    notice.hidden = false;
    $("#serverNoticeText").textContent = server.down_notice;
  } else {
    notice.hidden = true;
  }
  // Desktop notification the first time we observe the server going down.
  if (state.lastServerRunning && !server.running && server.down_notice) {
    notify("MLX Studio — API server down", server.down_notice);
    toast(server.down_notice, true);
  }
  state.lastServerRunning = server.running;
  const sLog = $("#serverLog");
  if (sLog) {
    const atBottom = sLog.scrollTop + sLog.clientHeight >= sLog.scrollHeight - 40;
    sLog.textContent = (server.lines || []).join("\n");
    if (atBottom) sLog.scrollTop = sLog.scrollHeight;
  }

  // settings
  $("#setAppVersion").textContent = `v${app.version}`;
  $("#setAppLatest").textContent = app.latest || (app.channel === "local build" ? "local build" : "unknown");
  $("#setAppChannel").textContent = app.channel || "—";
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
  if (app.update && rel.url) {
    updPanel.hidden = false;
    $("#appUpdatePill").textContent = `v${rel.version || app.latest}`;
    $("#appUpdateVersion").textContent = `v${rel.version || app.latest}`;
    $("#appUpdateNotes").textContent = rel.notes || "—";
    $("#appUpdateDownload").onclick = () => window.open(rel.url, "_blank");
    $("#appUpdateCopy").onclick = async () => {
      try { await navigator.clipboard.writeText(rel.url); toast("Download link copied"); }
      catch (_) { toast("Copy failed", true); }
    };
  } else {
    updPanel.hidden = true;
  }
  $("#setPython").textContent = python;
  $("#setHardware").textContent = `${hardware.chip} · ${hardware.ram}`;
  $("#setCheckedAt").textContent = versions.checked_at || "never";
  setPkg("setMlx", "setMlxPill", versions.mlx, versions.mlx_latest, versions.mlx_missing);
  setPkg("setMlxLm", "setMlxLmPill", versions.mlx_lm, versions.mlx_lm_latest, versions.mlx_lm_missing);
  setPkg("setHf", "setHfPill", versions.huggingface_hub, null, false);

  const hint = $("#setupHint");
  if (versions.mlx_missing || versions.mlx_lm_missing) {
    hint.textContent = "MLX is not installed yet — install it to run local models.";
  } else if (versions.mlx_update || versions.mlx_lm_update) {
    hint.textContent = "A newer MLX release is available on PyPI.";
  } else if (versions.checked_at) {
    hint.textContent = "Everything is installed and up to date.";
  } else {
    hint.textContent = "";
  }

  const installLog = $("#installLog");
  if (install && install.lines && install.lines.length) {
    installLog.hidden = false;
    installLog.textContent = install.lines.join("\n");
    installLog.scrollTop = installLog.scrollHeight;
  }

  // model dropdowns
  fillSelect("#chatModelSelect", models.map((m) => m.repo), loaded.repo);
  fillSelect("#serverModelSelect", models.map((m) => m.repo), server.repo);
  renderModelCards(models, loaded);
  renderDownloads(downloads);
}

function setPkg(valId, pillId, current, latest, missing) {
  const val = $(`#${valId}`);
  const pill = $(`#${pillId}`);
  val.textContent = current || "not installed";
  if (missing || !current) {
    pill.textContent = "missing";
    pill.className = "pill bad";
  } else if (latest && latest !== current) {
    pill.textContent = `update → ${latest}`;
    pill.className = "pill warn";
  } else if (latest) {
    pill.textContent = "up to date";
    pill.className = "pill ok";
  } else {
    pill.textContent = "";
    pill.className = "pill";
  }
}

function fillSelect(sel, values, current) {
  const el = $(sel);
  if (!el) return;
  const prev = el.value;
  el.innerHTML = values.map((v) => `<option value="${esc(v)}">${esc(v)}</option>`).join("");
  const want = current || prev;
  if (want && values.includes(want)) el.value = want;
  if (!values.length) el.innerHTML = `<option value="">No models installed</option>`;
}

/* ───────────── models view ───────────── */
function renderModelCards(models, loaded) {
  const wrap = $("#modelCards");
  if (!wrap) return;
  $("#modelsSummary").textContent = `${models.length} model(s)`;
  $("#modelsEmpty").hidden = models.length > 0;
  wrap.innerHTML = models.map((m) => {
    const isLoaded = loaded.repo === m.repo;
    const short = m.repo.split("/").pop();
    return `
      <div class="card">
        <div class="card-top">
          <div>
            <div class="card-name">${esc(short)}</div>
            <div class="card-sub">${esc(m.repo)}</div>
          </div>
          ${isLoaded ? '<span class="card-tag ok">loaded</span>' : ""}
        </div>
        <div class="card-sub">${esc(m.size_h)}</div>
        <div class="card-actions">
          <button class="btn btn-mini" data-act="load" data-repo="${esc(m.repo)}">${isLoaded ? "Unload" : "Load"}</button>
          <button class="btn btn-mini" data-act="reveal" data-path="${esc(m.path)}">Reveal</button>
          <button class="btn btn-mini btn-danger" data-act="delete" data-repo="${esc(m.repo)}">Delete</button>
        </div>
      </div>`;
  }).join("");

  wrap.querySelectorAll("[data-act]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const act = btn.dataset.act;
      try {
        if (act === "load") {
          if (btn.textContent === "Unload") await api("/api/unload", { method: "POST", body: "{}" });
          else await api("/api/load", { method: "POST", body: JSON.stringify({ repo: btn.dataset.repo }) });
        } else if (act === "delete") {
          if (!confirm(`Delete ${btn.dataset.repo} from disk?`)) return;
          await api("/api/delete", { method: "POST", body: JSON.stringify({ repo: btn.dataset.repo }) });
          toast("Model deleted");
        } else if (act === "reveal") {
          toast("Open in Finder from the desktop app");
        }
        refreshState();
      } catch (err) { toast(err.message, true); }
    });
  });
}

async function refreshModels() { await refreshState(); }

/* ───────────── catalogue ───────────── */
const FIT_LABEL = { great: "great fit", good: "good fit", tight: "tight fit", "too-large": "too large", unknown: "unknown" };

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
  const filter = $("#catalogFilter").value;
  const rows = state.catalog.filter((m) =>
    filter === "all" || m.fit === filter || m.category === filter);
  $("#catalogEmpty").hidden = rows.length > 0;
  wrap.innerHTML = rows.map((m) => {
    const short = m.repo.split("/").pop();
    const disabled = m.fit === "too-large";
    return `
      <div class="card">
        <div class="card-top">
          <div>
            <div class="card-name">${esc(short)}</div>
            <div class="card-sub">${esc(m.repo)}</div>
          </div>
          <span class="fit fit-${esc(m.fit)}">${esc(FIT_LABEL[m.fit] || m.fit)}</span>
        </div>
        <div class="card-sub">${esc(m.params)} · ${m.size_gb} GB · ${esc(m.category)}</div>
        <div class="card-sub">${esc(m.use)}</div>
        <div class="card-actions">
          ${m.installed
            ? '<button class="btn btn-mini" disabled>Installed</button>'
            : `<button class="btn btn-mini btn-primary" data-cat-download="${esc(m.repo)}" ${disabled ? "disabled" : ""}>${disabled ? "Needs more RAM" : "Download"}</button>`}
          ${m.installed ? `<button class="btn btn-mini" data-cat-load="${esc(m.repo)}">Load</button>` : ""}
        </div>
      </div>`;
  }).join("");

  wrap.querySelectorAll("[data-cat-download]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      btn.textContent = "Downloading…";
      try {
        // Switch to Download view so user can see progress
        showView("download");
        await api("/api/download", { method: "POST", body: JSON.stringify({ repo: btn.dataset.catDownload }) });
        toast("Download started");
      } catch (err) {
        toast(err.message, true);
        btn.disabled = false;
        btn.textContent = "Download";
      }
    });
  });
  wrap.querySelectorAll("[data-cat-load]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        await api("/api/load", { method: "POST", body: JSON.stringify({ repo: btn.dataset.catLoad }) });
        toast("Loading model…");
        refreshState();
      } catch (err) { toast(err.message, true); }
    });
  });
}

/* ───────────── downloads ───────────── */
function renderDownloads(downloads) {
  const wrap = $("#downloadCards");
  if (!wrap) return;
  const downloadCount = document.getElementById("downloadCount");
  if (downloadCount) {
    downloadCount.textContent = `${downloads.length} download${
      downloads.length !== 1 ? "s" : ""
    }`;
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
    const tag = d.status === "success" ? "ok" : d.status === "failed" ? "bad" : "run";
    const progressBar = d.status === "running"
      ? `<div class="progress-bar"><div class="progress-fill" style="width:${pct}%"></div></div>`
      : d.status === "success"
      ? `<div class="progress-done">✓ Complete</div>`
      : "";
    card.innerHTML = `
      <div class="card-top">
        <div>
          <div class="card-name">${esc(d.repo.split("/").pop())}</div>
          <div class="card-sub">${esc(d.repo)}</div>
        </div>
        <span class="card-tag ${tag}">${esc(d.status)}</span>
      </div>
      <div class="card-sub">${esc(d.detail || "")}</div>
      ${progressBar}`;
  });
}

async function doSearch(q) {
  const wrap = $("#searchResults");
  wrap.innerHTML = `<div class="card"><div class="card-sub">Searching…</div></div>`;
  $("#searchEmpty").hidden = true;
  try {
    const { results } = await api(`/api/search?q=${encodeURIComponent(q)}`);
    if (!results.length) {
      wrap.innerHTML = "";
      $("#searchEmpty").hidden = false;
      $("#searchEmpty").querySelector("h2").textContent = "No results";
      $("#searchEmpty").querySelector("p").textContent = "Try a different query.";
      return;
    }
    wrap.innerHTML = results.map((r) => `
      <div class="card">
        <div class="card-top">
          <div>
            <div class="card-name">${esc(r.repo.split("/").pop())}</div>
            <div class="card-sub">${esc(r.repo)}</div>
          </div>
        </div>
        <div class="card-actions">
          <button class="btn btn-mini btn-primary" data-download="${esc(r.repo)}">Download</button>
        </div>
      </div>`).join("");
    wrap.querySelectorAll("[data-download]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        btn.textContent = "Downloading…";
        try {
          // Switch to Download view so user can see progress
          showView("download");
          await api("/api/download", { method: "POST", body: JSON.stringify({ repo: btn.dataset.download }) });
          toast("Download started");
        } catch (err) {
          toast(err.message, true);
          btn.disabled = false;
          btn.textContent = "Download";
        }
      });
    });
  } catch (err) {
    wrap.innerHTML = "";
    $("#searchEmpty").hidden = false;
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

  state.messages.push({ role: "user", content: text });
  addMessage("user", text);

  const assistantEl = addMessage("assistant", "");
  const body = assistantEl.querySelector(".msg-body");
  body.classList.add("cursor");

  state.streaming = true;
  $("#sendBtn").hidden = true;
  $("#stopBtn").hidden = false;

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
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
          state.messages.push({ role: "assistant", content: full });
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
    const res = await fetch("/api/logs/export");
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

/* ───────────── wiring ───────────── */
function wire() {
  $("#loadBtn").addEventListener("click", async () => {
    const repo = $("#chatModelSelect").value;
    if (!repo) { toast("No model selected", true); return; }
    try {
      await api("/api/load", { method: "POST", body: JSON.stringify({ repo }) });
      refreshState();
    } catch (err) { toast(err.message, true); }
  });

  $("#unloadBtn").addEventListener("click", async () => {
    try { await api("/api/unload", { method: "POST", body: "{}" }); refreshState(); }
    catch (err) { toast(err.message, true); }
  });

  $("#newChatBtn").addEventListener("click", () => {
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

  $("#serverStartBtn").addEventListener("click", async () => {
    const repo = $("#serverModelSelect").value;
    if (!repo) { toast("No model selected", true); return; }
    const background = $("#serverBackground").checked;
    try {
      await api("/api/server/start", {
        method: "POST",
        body: JSON.stringify({ repo, background }),
      });
      toast(background ? "Starting in background…" : "Starting server…");
      refreshState();
    } catch (err) { toast(err.message, true); }
  });
  $("#serverStopBtn").addEventListener("click", async () => {
    try { await api("/api/server/stop", { method: "POST", body: "{}" }); refreshState(); }
    catch (err) { toast(err.message, true); }
  });
  $("#serverNoticeDismiss").addEventListener("click", async () => {
    try { await api("/api/server/notice/clear", { method: "POST", body: "{}" }); refreshState(); }
    catch (err) { toast(err.message, true); }
  });
  $("#serverRestartBtn").addEventListener("click", async () => {
    const repo = $("#serverModelSelect").value || state.snapshot?.server?.repo;
    if (!repo) { toast("Pick a model first", true); return; }
    try {
      await api("/api/server/start", {
        method: "POST",
        body: JSON.stringify({ repo, background: $("#serverBackground").checked }),
      });
      toast("Restarting server…");
      refreshState();
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
}

/* ───────────── boot ───────────── */
(async function boot() {
  wire();
  $("#curlExample").textContent =
`curl http://127.0.0.1:8080/v1/chat/completions \\
  -H "Content-Type: application/json" \\
  -d '{"model":"<model>","messages":[{"role":"user","content":"Hello!"}]}'`;
  showView(location.hash.slice(1) || "chat");
  await refreshState();
  loadCatalog();
  state.pollTimer = setInterval(refreshState, 2000);
})();
