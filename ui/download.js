/* Download models screen: recommended picks and Hub search in one list.
   Loaded before app.js, whose helpers ($, api, state, esc and the rest) are used only
   inside functions. Server values are untrusted: text goes through esc(), class names
   are fixed here, and links go only to huggingface.co model pages. */

const downloadScreen = {
  loading: false,
  error: "",
  sizes: new Map(), // repo to its checked size
  asking: new Set(), // queued or being asked
  queue: [],
  active: 0,
  unchecked: new Set(), // drawn cards that still have no size
  observer: null,
};
const DL_SIZE_CALLS = 4; // the server's limit
const DL_HUB_PAGE = /^https:\/\/huggingface\.co\/[A-Za-z0-9._-]+\/[A-Za-z0-9._-]+$/;
const DL_SIZE_TEXT = {
  unchecked: "checking size",
  not_published: "size not published",
  failed: "could not check size",
};
const DL_FIT_FILTERS = ["great", "good", "tight"];

/* ───────────── loading ───────────── */
async function loadCatalog() {
  forgetFailedSizes();
  try {
    const { catalog, hardware } = await api("/api/catalog");
    state.catalog = catalog;
    setText($("#catalogHardware"),
      `${hardware.chip} · ${hardware.ram} unified. Fit counts weight files only, not context memory.`);
    renderCatalog();
  } catch (err) { toast(err.message, true); }
}

// An empty query lists more of mlx-community ("More from Hugging Face").
async function doSearch(q) {
  const seq = ++state.searchSeq; // only the newest search may draw
  for (const repo of downloadScreen.queue) downloadScreen.asking.delete(repo);
  downloadScreen.queue = [];
  forgetFailedSizes();
  Object.assign(downloadScreen, { loading: true, error: "" });
  state.search = null;
  renderCatalog();
  try {
    const { results, error } = await api(`/api/search?q=${encodeURIComponent(q)}`);
    if (seq !== state.searchSeq) return;
    state.search = results;
    downloadScreen.error = error || "";
  } catch (err) {
    if (seq !== state.searchSeq) return;
    downloadScreen.error = "Search failed. Showing recommended models. Check the connection and try again.";
    toast(err.message, true);
  }
  downloadScreen.loading = false;
  renderCatalog();
}

function showRecommended() {
  state.searchSeq += 1;
  state.search = null;
  Object.assign(downloadScreen, { loading: false, error: "" });
  $("#searchInput").value = "";
  renderCatalog();
}

/* ───────────── sizes, only for cards on screen ───────────── */
function forgetFailedSizes() {
  for (const [repo, found] of downloadScreen.sizes) {
    if (found.size_status === "failed") downloadScreen.sizes.delete(repo);
  }
}

function withSize(m) {
  const found = m.size_status === "unchecked" && downloadScreen.sizes.get(m.repo);
  return found ? { ...m, ...found } : m;
}

function watchSizes(wrap) {
  downloadScreen.observer?.disconnect();
  const buttons = Array.from(wrap.querySelectorAll("[data-slot]"));
  if (typeof IntersectionObserver !== "function") {
    buttons.forEach((btn) => wantSize(btn.dataset.repo));
    return;
  }
  downloadScreen.observer = new IntersectionObserver((entries) => entries
    .filter((e) => e.isIntersecting)
    .forEach((e) => wantSize(e.target.dataset.repo)));
  buttons.forEach((btn) => downloadScreen.observer.observe(btn));
}

function wantSize(repo) {
  const s = downloadScreen;
  if (!s.unchecked.has(repo) || s.asking.has(repo) || s.sizes.has(repo)) return;
  s.asking.add(repo);
  s.queue.push(repo);
  pumpSizes();
}

function pumpSizes() {
  const s = downloadScreen;
  while (s.active < DL_SIZE_CALLS && s.queue.length) {
    s.active += 1;
    askSize(s.queue.shift());
  }
}

async function askSize(repo) {
  const s = downloadScreen;
  let found = { size_status: "failed" };
  try {
    const answer = await api(`/api/model-size?repo=${encodeURIComponent(repo)}`);
    found = { size_status: answer.size_status, size_gb: answer.size_gb, fit: answer.fit };
  } catch (_) { /* the card says so; asked again after the next search or visit */ }
  s.active -= 1;
  s.asking.delete(repo);
  s.sizes.set(repo, found);
  pumpSizes();
  renderCatalog();
}

/* ───────────── drawing ───────────── */
function matchesFilter(m, filter) {
  if (filter === "all") return true;
  if (filter === "best") return m.pick_tag === "Best for Mac";
  return [m.fit, m.pick, m.category, ...(m.chips || [])].includes(filter);
}

// Called after every poll too: setHTML and setText write only what changed.
function renderCatalog() {
  const wrap = $("#catalogCards");
  if (!wrap) return;
  const { loading, error } = downloadScreen;
  const list = loading ? null : state.search || state.catalog;
  const all = (list || []).map(withSize);
  const filter = $("#catalogFilter").value || "all";
  const rows = all.filter((m) => matchesFilter(m, filter));

  const more = $("#showAllBtn");
  const back = $("#picksBtn");
  const held = document.activeElement;
  more.hidden = loading || !!state.search;
  back.hidden = !(loading || state.search || error);
  // Never leave the keyboard on a button that has just gone.
  if (held === more && more.hidden) back.focus();
  if (held === back && back.hidden) more.focus();

  const waiting = DL_FIT_FILTERS.includes(filter)
    ? all.filter((m) => m.size_status === "unchecked").length : 0;
  setText($("#catalogNotice"), loading ? "Searching Hugging Face…" : [
    error,
    waiting && `${waiting} ${waiting === 1 ? "model without a checked size is" : "models without a checked size are"} hidden by this filter. Choose All models and scroll to check sizes.`,
  ].filter(Boolean).join(" "));

  const empty = $("#catalogEmpty");
  empty.hidden = !list || rows.length > 0;
  if (!empty.hidden) {
    const [title, text] = all.length ? ["Nothing matches that filter", "Pick another fit or category."]
      : error ? ["No recommended model matches", "Search again when Hugging Face can be reached."]
      : ["No results", "Try a different query."];
    setText(empty.querySelector("h2"), title);
    setText(empty.querySelector("p"), text);
  }

  if (loading) {
    downloadScreen.observer?.disconnect();
    setHTML(wrap, `<div class="card"><div class="card-sub">Searching…</div></div>`);
  } else if (list) {
    renderOfferCards(wrap, rows);
  }
}

function dlTag(text, kind = "", title = "") {
  return text
    ? `<span class="card-tag ${kind}"${title ? ` title="${title}"` : ""}>${esc(text)}</span>`
    : "";
}

function hubLink(url, repo, kind, text, label) {
  if (typeof url !== "string" || !DL_HUB_PAGE.test(url)) return "";
  return `<a class="dl-link" href="${esc(url)}" target="_blank" rel="noopener noreferrer" ` +
    `data-link="${esc(repo)}" data-kind="${kind}" aria-label="${esc(label)}">${esc(text)}</a>`;
}

// A model that can be downloaded: picks and search hits are the same card.
function offerCard(m, busy, ready) {
  const running = busy.has(m.repo);
  const fit = Object.hasOwn(FIT_LABEL, m.fit) ? m.fit : "unknown";
  const disabled = fit === "too-large";
  const label = running ? "Downloading…" : disabled ? "Needs more RAM" : "Download";
  // The snapshot is live; the catalogue flag is only used before the first one.
  const installed = state.snapshot ? ready.has(m.repo) : m.installed;
  const slot = `data-slot="main" data-repo="${esc(m.repo)}"`;
  // category is null when the Hub was not asked: then nothing is called unpublished.
  const asked = m.category != null;
  const missing = (what) => (asked ? `${what} not published` : "");
  const count = (n, word) => (typeof n === "number" ? `${n.toLocaleString("en-US")} ${word}` : "");
  const size = DL_SIZE_TEXT[m.size_status] ||
    (m.size_gb != null ? `${m.size_gb} GB` : "size not published");
  const tags = [
    dlTag(m.pick_tag, m.pick_tag === "Best for Mac" ? "ok" : "dl-tag-warn",
      "A recommended pick and how it fits this Mac"),
    dlTag(m.pick && `Pick: ${m.pick}`, "run"),
    ...(m.chips || []).map((chip) => dlTag(chip)),
    dlTag(m.gated === true && "Gated", "dl-tag-warn", "Needs access approval on Hugging Face"),
  ].join("");
  const base = m.base_model
    ? hubLink(m.base_model_url, m.repo, "base", `Base model: ${m.base_model}`,
      `Base model: ${m.base_model} on Hugging Face (new window)`) ||
      metaLine(`Base model: ${m.base_model}`)
    : metaLine(asked && "Base model not published");
  return modelCard(
    m.repo,
    `<span title="Rated on weight file size only; context memory is not included">${fitBadge(fit)}</span>`,
    `${tags ? `<div class="dl-tags">${tags}</div>` : ""}
       ${metaLine(m.params ? `${m.params} params` : missing("parameters"),
    m.bits ? `${m.bits}-bit` : missing("bits"), size,
    m.category === "Not published" ? "task not published" : m.category)}
       ${metaLine(m.use)}
       ${metaLine(m.license ? `license ${m.license}` : missing("license"), m.library,
    count(m.downloads, "downloads"), count(m.likes, "likes"),
    m.updated && `updated ${m.updated}`)}
       ${base}
       ${hubLink(m.url, m.repo, "model", "Open on Hugging Face",
    `Open on Hugging Face: ${m.repo} (new window)`)}`,
    (!running && installed
      // aria-disabled keeps the focus the Download button had.
      ? `<button class="btn btn-mini" type="button" ${slot} aria-disabled="true">Installed</button>`
      // aria-disabled, not disabled: a busy button must keep keyboard focus.
      : `<button class="btn btn-mini btn-primary" type="button" ${slot} data-cat-download="${esc(m.repo)}" ${disabled ? "disabled" : running ? 'aria-disabled="true"' : ""}>${label}</button>`) +
      copyButton(m.repo),
  );
}

function renderOfferCards(wrap, rows) {
  // Button state comes from the latest snapshot, so a cancel or failure restores it.
  const busy = new Set((state.snapshot?.downloads || [])
    .filter((d) => d.status === "running").map((d) => d.repo));
  const ready = new Set((state.snapshot?.models || [])
    .filter((m) => m.complete !== false).map((m) => m.repo));
  downloadScreen.unchecked = new Set(rows
    .filter((m) => m.size_status === "unchecked").map((m) => m.repo));
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
  watchSizes(wrap);
}

document.querySelector("#picksBtn")?.addEventListener("click", showRecommended);
