"""Routes for the project folder screen: choose, list, preview, attach. Read only.

* The folder comes only from the window's own dialog. No request carries a path to it.
* File text reaches a message only through ``ProjectStore.snapshot`` (or the copy of
  it saved with the chat). A ``files`` list sent by the page is never used.
* The server's port owner is checked before files are attached and before they go.

``app`` is the running ``app`` module, passed in so this file never imports it. Every
route must sit behind the app's token and Host checks, which ``app.py`` makes.
"""

from __future__ import annotations

import collections
import secrets
import threading

import chat_store
import engine_runtime
import projects

NO_PICKER = (
    "Browser mode has no folder picker. Open the MLX Studio window to choose a project folder."
)
ESTIMATE_FACTOR = 1.0  # one for preview, attach and send, so what is shown fits is sent
ATTACHED_MAX = 200  # attachments held for open chats; the oldest goes first
ATTACHED: collections.OrderedDict[str, list[dict]] = collections.OrderedDict()
ATTACHED_LOCK = threading.Lock()
DIALOG_LOCK = threading.Lock()


class NotReady(chat_store.ChatError):
    status = 409


class DialogFailed(chat_store.ChatError):
    status = 500


def _store(app) -> projects.ProjectStore:
    return projects.ProjectStore(app.APP_DIR / "projects.json")


def _ready(app) -> None:
    """Only while the running server is the one on its port."""
    if app.STATE.server_pid is None:
        raise NotReady("Start the API server first, then attach files.")
    try:
        app._chat_headers()
    except engine_runtime.Refused as exc:
        raise NotReady(str(exc)) from None


def _attached(key) -> list[dict]:
    with ATTACHED_LOCK:
        files = ATTACHED.get(key) if isinstance(key, str) else None
    if files is None:
        raise NotReady("The files of an earlier message are not held any more: attach them again.")
    return files


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
def _overview(app) -> dict:
    picker = app.STATE.window is not None
    return {
        "projects": _store(app).list(),
        "picker": picker,
        "picker_reason": None if picker else NO_PICKER,
        "max_files": projects.FILES_PER_MESSAGE,
    }


def _choose(app, body: dict) -> dict:
    if body:
        raise chat_store.Invalid("this route takes no path: the app's own dialog chooses")
    window = app.STATE.window
    if window is None:
        raise NotReady(NO_PICKER)
    if not DIALOG_LOCK.acquire(blocking=False):
        raise NotReady("The folder dialog is already open.")
    try:
        import webview

        kinds = getattr(webview, "FileDialog", None)
        picked = window.create_file_dialog(kinds.FOLDER if kinds else webview.FOLDER_DIALOG)
    except Exception as exc:  # the window library raises many kinds
        app.log_app("warning", "folder dialog failed", error=str(exc)[:200])
        raise DialogFailed("The folder dialog could not be opened.") from None
    finally:
        DIALOG_LOCK.release()
    if not picked:
        return {"cancelled": True}
    return {"project": _store(app).add(picked if isinstance(picked, str) else picked[0])}


def _remove(app, body: dict) -> dict:
    _store(app).remove(body.get("id"))
    return {"ok": True}


def _preview(app, body: dict) -> dict:
    return _store(app).preview(body.get("id"), body.get("paths"), ESTIMATE_FACTOR)


def _attach(app, body: dict) -> dict:
    picks = body.get("picks")
    if not picks:
        raise chat_store.Invalid("choose at least one file and preview it")
    _ready(app)
    files = _store(app).snapshot(body.get("id"), picks)
    tokens = projects.estimate_tokens(projects.file_block(files), ESTIMATE_FACTOR)
    if tokens > projects.FILES_TOKENS:
        raise projects.OverBudget(
            f"the files are over their {projects.FILES_TOKENS} token limit: untick some"
        )
    key = secrets.token_hex(16)
    with ATTACHED_LOCK:
        ATTACHED[key] = files
        while len(ATTACHED) > ATTACHED_MAX:
            ATTACHED.popitem(last=False)
    shown = [{name: f[name] for name in ("path", "size", "sha256")} for f in files]
    return {"attachment": key, "files": shown, "tokens": tokens}


POSTS = {"choose": _choose, "remove": _remove, "preview": _preview, "attach": _attach}


def _answer(work) -> tuple[int, dict]:
    try:
        return 200, work()
    except chat_store.ChatError as exc:
        path = getattr(exc, "path", None)
        return exc.status, {"error": str(exc), **({"path": path} if path is not None else {})}


def handle_get(app, path: str, query: dict) -> tuple[int, dict]:
    """``/api/projects`` and ``/api/projects/files?id=``; ``query`` is ``parse_qs``'s."""
    if path == "/api/projects":
        return _answer(lambda: _overview(app))
    if path == "/api/projects/files":
        return _answer(lambda: _store(app).list_files(query.get("id", [""])[0]))
    return 404, {"error": "not found"}


def handle_post(app, name: str, body: dict) -> tuple[int, dict]:
    """``POST /api/projects/<name>``."""
    work = POSTS.get(name)
    if work is None:
        return 404, {"error": "not found"}
    return _answer(lambda: work(app, body))


# --------------------------------------------------------------------------- #
# For the chat route
# --------------------------------------------------------------------------- #
def saved_messages(app, body: dict) -> list[dict]:
    """The messages of the saved chat a send names, or none."""
    save = body.get("save")
    if not isinstance(save, dict) or not isinstance(save.get("id"), str):
        return []
    try:
        return app.saved_chats().get(save["id"])["messages"]
    except chat_store.ChatError:
        return []


def prepare(
    app, repo: str, messages: list[dict], system: str, own: dict, saved=()
) -> tuple[list[dict], list[dict], dict]:
    """``(to send, to save, settings)`` for one chat request.

    ``messages`` are the page's ``{role, content, attachment?}``. Without files all is
    returned with only role and content. With files, the oldest turns may be left out of what is sent,
    the reply limit is capped, and ChatError says why nothing may be sent.
    """
    keep = []
    for index, message in enumerate(messages):
        item = {"role": message["role"], "content": message["content"]}
        if item["role"] == "user":
            key = message.get("attachment")
            old = saved[index] if index < len(saved) else {}
            same = old.get("role") == "user" and old.get("content") == item["content"]
            with ATTACHED_LOCK:
                held = isinstance(key, str) and key in ATTACHED
            if not held and same and old.get("files"):
                item["files"] = old["files"]
            elif key is not None or message.get("files"):
                item["files"] = _attached(key)  # never the page's own copy
        keep.append(item)
    if not any("files" in item for item in keep):
        return keep, keep, own  # nothing else the page put on a message goes on
    _ready(app)
    reply = own.get("max_tokens") or app.chat_settings(repo)["max_tokens"]
    plan = projects.plan_request(keep, system, reply, ESTIMATE_FACTOR)
    send = [m for m in plan["messages"] if m["role"] != "system"]  # the chat route adds it
    return send, keep, {**own, "max_tokens": plan["max_tokens"]}
