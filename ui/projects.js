/* Project folder panel (row 1104): choose a folder, list, preview, attach. Read only.
   Loaded after app.js, whose $, api, esc, setHTML and setText it uses. */
const ProjectsUI = (() => {
  const view = { projects: [], open: "", files: [], note: "", picked: new Set(), max: 50,
    preview: null, attached: null, taken: null, seq: 0 };

  const post = (name, body) =>
    api(`/api/projects/${name}`, { method: "POST", body: JSON.stringify(body) });
  const say = (text) => setText($("#projectError"), text || "");

  // Shows the words of a refused request instead of throwing them away.
  async function tried(work) {
    say("");
    try {
      await work();
    } catch (err) {
      say(err.message);
    }
  }

  function bind(host, kind, act) {
    host.querySelectorAll(`[data-${kind}]`).forEach((button) =>
      button.addEventListener("click", () => act(button.dataset[kind])));
  }

  // What was previewed no longer matches what is chosen.
  function forget() {
    view.seq += 1;
    view.preview = null;
    setHTML($("#projectPreview"), "");
    setText($("#projectWarning"), "");
    $("#projectAttach").disabled = true;
  }

  function drawProjects() {
    const host = $("#projectList");
    const html = view.projects.map((p) => `<li>
      <button type="button" class="btn btn-ghost" data-open="${esc(p.id)}"
        aria-pressed="${p.id === view.open}">${esc(p.name)}</button>
      <span class="muted project-path">${esc(p.root)}</span>
      <button type="button" class="btn btn-ghost" data-remove="${esc(p.id)}"
        aria-label="Remove project ${esc(p.name)}">Remove</button></li>`).join("");
    if (!setHTML(host, html)) return;
    bind(host, "open", open);
    bind(host, "remove", remove);
  }

  function drawFiles() {
    const host = $("#projectFiles");
    const html = view.files.map((f) => `<li>
      <button type="button" class="project-file" data-pick="${esc(f.path)}"
        aria-pressed="${view.picked.has(f.path)}">${esc(f.path)}</button>
      <span class="muted">${esc(f.size)} bytes</span></li>`).join("");
    setText($("#projectCount"), view.open
      ? `${view.picked.size} of at most ${view.max} files chosen. ${view.note}` : "");
    $("#projectPreviewBtn").disabled = !view.picked.size || view.picked.size > view.max;
    if (setHTML(host, html)) bind(host, "pick", pick);
  }

  function drawAttached() {
    const held = view.attached;
    setText($("#projectAttached"), held
      ? `Goes with your next message: ${held.files.map((f) => f.path).join(", ")}` : "");
    $("#projectDetach").hidden = !held;
  }

  async function load() {
    await tried(async () => {
      const data = await api("/api/projects");
      view.projects = data.projects;
      view.max = data.max_files;
      $("#projectChoose").disabled = !data.picker;
      setText($("#projectNote"), data.picker_reason || "");
      drawProjects();
    });
  }

  async function open(id) {
    forget();
    Object.assign(view, { open: id, files: [], note: "" });
    view.picked.clear();
    drawProjects();
    drawFiles();
    const seq = view.seq;
    await tried(async () => {
      const data = await api(`/api/projects/files?id=${encodeURIComponent(id)}`);
      if (seq !== view.seq) return; // another project, or a newer choice
      view.files = data.files;
      view.note = (data.excluded ? `${data.excluded} entries are not offered ` +
        "(possible secrets, links, large or odd files)." : "") +
        (data.truncated ? " The list is cut short." : "");
      drawFiles();
    });
  }

  async function choose() {
    $("#projectChoose").disabled = true;
    await tried(async () => {
      const data = await post("choose", {});
      if (data.cancelled) return;
      await load();
      await open(data.project.id);
    });
    $("#projectChoose").disabled = false;
  }

  async function remove(id) {
    if (!confirm("Remove this project from the list? The folder and your chats stay.")) return;
    await tried(async () => {
      await post("remove", { id });
      if (view.open === id) {
        forget();
        Object.assign(view, { open: "", files: [], note: "" });
        view.picked.clear();
        drawFiles();
      }
      await load();
    });
  }

  function pick(path) {
    if (!view.picked.delete(path)) view.picked.add(path);
    forget();
    say("");
    drawFiles();
  }

  async function preview() {
    forget();
    const seq = view.seq;
    const id = view.open;
    await tried(async () => {
      const data = await post("preview", { id, paths: [...view.picked] });
      if (seq !== view.seq) return;
      view.preview = { id, files: data.files };
      setHTML($("#projectPreview"), data.files.map((f) => `<article>
        <h4>${esc(f.path)}</h4>
        ${f.warnings.map((w) => `<p class="project-warn">Warning: ${esc(w)}</p>`).join("")}
        <pre tabindex="0">${esc(f.text)}</pre></article>`).join(""));
      setText($("#projectWarning"), data.notice);
      const over = data.tokens > data.limit;
      $("#projectAttach").disabled = over;
      if (over) {
        say(`About ${data.tokens} tokens, over the limit of ${data.limit} for files: untick some.`);
      }
    });
  }

  async function attach() {
    const shown = view.preview;
    if (!shown) return;
    const seq = view.seq;
    $("#projectAttach").disabled = true;
    await tried(async () => {
      const picks = shown.files.map(({ path, sha256 }) => ({ path, sha256 }));
      const data = await post("attach", { id: shown.id, picks });
      if (seq !== view.seq) return;
      view.attached = { id: data.attachment, files: data.files };
      drawAttached();
    });
    // A refusal such as a stopped server may pass: the same preview can be tried again.
    if (seq === view.seq) $("#projectAttach").disabled = false;
  }

  // The chat takes the attachment for the message it is sending.
  function take() {
    view.taken = view.attached;
    view.attached = null;
    drawAttached();
    return view.taken ? view.taken.id : null;
  }

  // The send was refused: the attachment waits for the next try.
  function restore() {
    view.attached = view.attached || view.taken;
    view.taken = null;
    drawAttached();
  }

  function detach() {
    view.attached = null;
    view.taken = null;
    drawAttached();
  }

  $("#projectPanel")?.addEventListener("toggle", () => {
    if ($("#projectPanel").open) load();
  });
  $("#projectChoose")?.addEventListener("click", choose);
  $("#projectPreviewBtn")?.addEventListener("click", preview);
  $("#projectAttach")?.addEventListener("click", attach);
  $("#projectDetach")?.addEventListener("click", detach);

  return { load, take, restore, detach };
})();
