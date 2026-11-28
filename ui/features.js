/* Live memory and speed, load test comparison, install or update all, release check.
   Loaded before app.js: nothing here runs until app.js calls renderFeatures,
   renderCompare or wireFeatures, so app.js's helpers exist by then. */

const ft = { plan: null, planSig: null, seq: 0, installing: false, releaseUrl: null };
const FT_ENGINES = { mlx: "MLX", optiq: "OptiQ" };
const FT_RELEASE = /^https:\/\/github\.com\/[\w./-]+\/releases\/tag\/v\d+\.\d+\.\d+$/;
const ftNum = (v, unit = "", digits = 1) =>
  typeof v === "number" ? `${v.toFixed(digits)}${unit}` : "unknown";
const ftGB = (v) => (typeof v === "number" ? `${(v / 2 ** 30).toFixed(1)} GB` : "unknown");
const FT_FIGURES = [
  ["read_tps", "Read speed (tok/s)", (v) => ftNum(v)],
  ["write_tps", "Write speed (tok/s)", (v) => ftNum(v)],
  ["first_token_s", "First token (s)", (v) => ftNum(v, "", 2)],
  ["follow_up_first_token_s", "Follow-up first token (s)", (v) => ftNum(v, "", 2)],
  ["peak_percent", "Peak memory (% of this Mac)", (v) => ftNum(v)],
  ["server_peak_bytes", "Peak server memory", ftGB],
];

function renderFeatures(snap) {
  renderLiveStats(snap.server || {});
  ft.installing = !!snap.install?.running;
  if (state.view !== "settings") {
    ft.planSig = null; // asked again on the next visit
    return;
  }
  const signature = JSON.stringify([ft.installing, snap.server?.running, snap.server?.starting,
    !!snap.loadtest?.active, snap.versions?.checked_at]);
  if (signature === ft.planSig) return;
  ft.planSig = signature;
  loadInstallPlan();
}

/* ───────────── live memory and speed ───────────── */
function renderLiveStats(server) {
  const { memory, speed } = server;
  $("#livePanel").hidden = !memory;
  if (!memory) return;
  const used = typeof memory.mac_used_percent === "number"
    ? `${ftNum(memory.mac_used_percent, "%")} of ${ftGB(memory.mac_total_bytes)} used`
    : "unknown";
  setText($("#liveMacMem"), used);
  setText($("#liveServerMem"), typeof memory.server_bytes === "number"
    ? `${ftGB(memory.server_bytes)} (peak ${ftGB(memory.server_peak_bytes)})` : "unknown");
  setText($("#livePressure"), `Pressure: ${memory.pressure || "unknown"}`);
  $("#livePressure").className =
    memory.pressure && memory.pressure !== "normal" ? "pill warn" : "pill";
  const chat = speed?.chat;
  setText($("#liveChat"), chat
    ? [`${ftNum(chat.write_tps, " tok/s")}`,
      `first token ${ftNum(chat.first_token_s, " s", 2)}`,
      `${chat.reply_tokens ?? "unknown"} reply tokens`,
      chat.mode ? `mode ${chat.mode}` : "", `at ${chat.at}`].filter(Boolean).join(", ")
    : "No reply measured for this server yet");
  const test = speed?.test;
  setText($("#liveTest"), test
    ? [`From ${test.time || "an unknown time"}${test.stale
      ? " (older settings or versions)" : ""}`,
    ...test.rows.map((r) => `${r.size} tokens: read ${ftNum(r.read_tps, " tok/s")}, ` +
        `write ${ftNum(r.write_tps, " tok/s")}, first token ${ftNum(r.first_token_s, " s", 2)}`),
    ].join("\n")
    : "No load test measured for this model and engine");
}

/* ───────────── load test, side by side ───────────── */
function renderCompare(compare) {
  const runs = compare?.runs;
  const any = !!(runs?.mlx || runs?.optiq);
  $("#ltComparePanel").hidden = !any;
  if (!any) return;
  setText($("#ltCompareBadge"), compare.comparable ? "Same settings" : "Not comparable");
  $("#ltCompareBadge").className = compare.comparable ? "pill" : "pill warn";
  setText($("#ltCompareReasons"), compare.reasons.map((r) => `${r}.`).join(" "));
  setText($("#ltCompareRuns"), Object.entries(FT_ENGINES).map(([key, name]) => {
    const run = runs[key];
    if (!run) return `${name}: no finished run.`;
    return `${name}: ${[run.status, run.time].filter(Boolean).join(", ")}.`;
  }).join(" "));
  const cell = (row, better, engine, key, format) => {
    if (!row) return "not run";
    if (row.status !== "passed") return esc(row.status);
    return `${format(row[key])}${better[key] === engine ? " (better)" : ""}`;
  };
  setHTML($("#ltCompareTables"), compare.rows.map((row) => `
    <table class="ft-compare"><caption>${esc(row.size)} tokens</caption>
      <thead><tr><th scope="col">Figure</th><th scope="col">MLX</th>
        <th scope="col">OptiQ</th></tr></thead>
      <tbody>${FT_FIGURES.map(([key, label, format]) => `<tr><th scope="row">${label}</th>
        ${Object.keys(FT_ENGINES).map((engine) =>
    `<td>${cell(row[engine], row.better, engine, key, format)}</td>`).join("")}</tr>`).join("")}
      </tbody></table>`).join(""));
}

/* ───────────── install or update all ───────────── */
function ftChange(item) {
  const from = item.action === "update" && item.current ? `${item.current} to ` : "";
  return `${item.id}: ${item.action} ${from}${item.target}`;
}

function ftLine(item) {
  if (item.action === "unsupported") return `${item.id}: not supported. ${item.reason}`;
  if (item.action === "keep") return `${item.id}: up to date`;
  return ftChange(item);
}

function paintPlan(plan) {
  ft.plan = plan;
  setHTML($("#planItems"), plan.items.map((i) => `<li>${esc(ftLine(i))}</li>`).join(""));
  setText($("#planWhy"), ft.installing
    ? "An install is running. Its output appears under Runtime packages."
    : plan.blocked || (plan.needed ? "" : "Nothing to install or update."));
  $("#planBtn").disabled = ft.installing || !!plan.blocked || !plan.needed;
}

async function loadInstallPlan() {
  const seq = ++ft.seq;
  try {
    const plan = await api("/api/install/plan");
    if (seq === ft.seq) paintPlan(plan);
  } catch (err) {
    if (seq !== ft.seq) return;
    ft.plan = null;
    $("#planBtn").disabled = true;
    setText($("#planWhy"), err.message);
  }
}

async function installAll() {
  const plan = ft.plan;
  if (!plan?.needed || plan.blocked) return;
  const changes = plan.items.filter((i) => ["install", "update"].includes(i.action));
  const skipped = plan.items.filter((i) => i.action === "unsupported");
  const question = ["Install or update these now?", ...changes.map(ftChange),
    ...skipped.map(ftLine), "This can be a large download."].join("\n");
  if (!confirm(question)) return;
  $("#planBtn").disabled = true;
  try {
    await api("/api/install/all", { method: "POST", body: "{}" });
    refreshState();
  } catch (err) {
    setText($("#planWhy"), err.message);
    toast(err.message, true);
  }
}

/* ───────────── release check (only when asked) ───────────── */
async function checkRelease() {
  const button = $("#ftUpdateBtn");
  button.disabled = true;
  ft.releaseUrl = null;
  $("#ftUpdateOpen").hidden = true;
  setText($("#ftUpdateMsg"), "Asking GitHub…");
  try {
    const found = await api("/api/update/check", { method: "POST", body: "{}" });
    setText($("#ftUpdateMsg"), found.message);
    if (found.status === "update" && FT_RELEASE.test(found.url)) ft.releaseUrl = found.url;
    $("#ftUpdateOpen").hidden = !ft.releaseUrl;
  } catch (err) {
    setText($("#ftUpdateMsg"), err.message);
  } finally {
    button.disabled = false;
  }
}

function openRelease() {
  if (ft.releaseUrl) window.open(ft.releaseUrl, "_blank", "noopener");
}

function wireFeatures() {
  $("#planBtn").onclick = installAll;
  $("#ftUpdateBtn").onclick = checkRelease;
  $("#ftUpdateOpen").onclick = openRelease;
}
