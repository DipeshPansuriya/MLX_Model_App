"""Saved chats: one private JSON file per chat, ``<folder>/<32 hex id>.json``.

Standard library only and no logging: chat text must never reach a log, and no
error repeats it. Every ``ChatError`` carries the HTTP status to answer with.
Nothing expires, and a file is removed only by ``delete`` or ``delete_all``.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import re
import secrets
import stat
import tempfile
import threading
import time
from pathlib import Path

FORMAT = 1
MAX_CHAT_BYTES = 16 * 1024 * 1024
NEW_TITLE = "New chat"
TITLE_MAX = 120
AUTO_TITLE = 60
SYSTEM_MAX_CHARS = 8000
MODEL_MAX = 200
QUERY_MAX = 200
# Per chat settings: (lowest, highest), the same as the app's chat_* settings.
SETTING_RANGES = {"max_tokens": (64, 131072), "temperature": (0, 2), "top_p": (0.05, 1)}
DECIMAL_SETTINGS = frozenset({"temperature", "top_p"})
ID_PATTERN = re.compile(r"[0-9a-f]{32}")
SHA_PATTERN = re.compile(r"[0-9a-f]{64}")
CHAT_NAME = re.compile(r"([0-9a-f]{32})\.json")
# A copy left behind when the app died in the middle of a save. It holds chat text.
LEFTOVER_NAME = re.compile(r"\.([0-9a-f]{32})\.json\..*\.tmp")
MESSAGE_KEYS = frozenset({"role", "content", "thinking", "stats", "stopped", "ts", "files"})
FILE_KEYS = frozenset({"path", "sha256", "size", "text"})
STATS_MAX = 20
STAT_TEXT_MAX = 100
# One writer at a time, whichever store object it comes through.
WRITE_LOCK = threading.Lock()
_UNCHANGED = object()


class ChatError(Exception):
    status = 400


class Invalid(ChatError):
    status = 400


class NotFound(ChatError):
    status = 404


class Stale(ChatError):
    status = 409


class TooLarge(ChatError):
    status = 413


class Broken(ChatError):
    status = 422


class Unavailable(ChatError):
    status = 500


# --------------------------------------------------------------------------- #
# Private files (also used by projects.py)
# --------------------------------------------------------------------------- #
def private_folder(folder: Path, create: bool) -> bool:
    """Whether ``folder`` is there; it is made, or kept, owner-only.

    Raises OSError when something else, a link included, has its name.
    """
    if create:
        folder.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(FileExistsError):
            folder.mkdir(mode=0o700)
    try:
        found = os.lstat(folder)
    except FileNotFoundError:
        return False
    if not stat.S_ISDIR(found.st_mode) or found.st_uid != os.getuid():
        raise OSError("not a folder of this user")
    if stat.S_IMODE(found.st_mode) != 0o700:
        folder.chmod(0o700)
    return True


def write_private(path: Path, data: bytes) -> None:
    """Replace ``path`` in one step with an owner-only file."""
    # A new name, created exclusively with mode 0600: a planted link is never followed.
    handle, temp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, path)
    finally:
        with contextlib.suppress(OSError):  # already gone after a good replace
            os.unlink(temp)


def read_regular(path: Path, limit: int) -> bytes:
    """A regular file of at most ``limit`` bytes; no link is followed, no pipe waited on."""
    handle = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(handle).st_mode):
            raise OSError("not a regular file")
        # closefd=False: open() on a refused handle would otherwise leave it open.
        with os.fdopen(handle, "rb", closefd=False) as source:
            data = source.read(limit + 1)
    finally:
        os.close(handle)
    if len(data) > limit:
        raise OSError("the file is too large")
    return data


# --------------------------------------------------------------------------- #
# What a chat may hold
# --------------------------------------------------------------------------- #
def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _text(value, what: str, most: int | None = None) -> str:
    if not isinstance(value, str):
        raise Invalid(f"{what} must be text")
    # Half of a character pair cannot be written as UTF-8; it becomes "?".
    value = value.encode("utf-8", "replace").decode("utf-8")
    if most is not None and len(value) > most:
        raise Invalid(f"{what} is longer than {most} characters")
    return value


def _whole(value, what: str) -> int:
    if type(value) is not int or value < 0:
        raise Invalid(f"{what} must be a whole number")
    return value


def _title(value) -> str:
    title = " ".join(_text(value, "the title").split())
    if not title or len(title) > TITLE_MAX:
        raise Invalid(f"the title must have 1 to {TITLE_MAX} characters")
    return title


def _auto_title(messages: list[dict]) -> str:
    for message in messages:
        if message["role"] == "user" and (text := message["content"].strip()):
            return " ".join(text.splitlines()[0].split())[:AUTO_TITLE].rstrip()
    return NEW_TITLE


def _project(value) -> str | None:
    if value is None or (isinstance(value, str) and ID_PATTERN.fullmatch(value)):
        return value
    raise Invalid("unknown project id")


def _settings(value) -> dict:
    if not isinstance(value, dict) or not set(value) <= set(SETTING_RANGES):
        raise Invalid("chat settings may hold only " + ", ".join(SETTING_RANGES))
    for key, number in value.items():
        low, high = SETTING_RANGES[key]
        kinds = (int, float) if key in DECIMAL_SETTINGS else (int,)
        if type(number) not in kinds or not low <= number <= high:
            raise Invalid(f"{key} must be from {low} to {high}")
    return dict(value)


def _stats(value) -> dict:
    if not isinstance(value, dict) or len(value) > STATS_MAX:
        raise Invalid("message figures are not usable")
    clean = {}
    for key, figure in value.items():
        usable = (
            figure is None
            or type(figure) in (bool, int)
            or (type(figure) is float and math.isfinite(figure))
            or (isinstance(figure, str) and len(figure) <= STAT_TEXT_MAX)
        )
        if not (isinstance(key, str) and len(key) <= STAT_TEXT_MAX and usable):
            raise Invalid("message figures are not usable")
        clean[_text(key, "a message figure")] = (
            _text(figure, "a message figure") if isinstance(figure, str) else figure
        )
    return clean


def _file(value) -> dict:
    """The copy of a project file that went out with a message."""
    if not isinstance(value, dict) or set(value) != FILE_KEYS:
        raise Invalid("an attached file needs " + ", ".join(sorted(FILE_KEYS)))
    sha = value["sha256"]
    if not (isinstance(sha, str) and SHA_PATTERN.fullmatch(sha)):
        raise Invalid("an attached file has an unusable hash")
    return {
        "path": _text(value["path"], "a file path", 1024),
        "sha256": sha,
        "size": _whole(value["size"], "a file size"),
        "text": _text(value["text"], "a file text"),
    }


def _message(value, now: int | None) -> dict:
    """One checked message. ``now`` stamps a new one; None means it must have its own."""
    if not isinstance(value, dict) or not set(value) <= MESSAGE_KEYS:
        raise Invalid("a message holds something unknown")
    role = value.get("role")
    if role not in ("user", "assistant"):
        raise Invalid("a message role must be user or assistant")
    clean = {"role": role, "content": _text(value.get("content"), "a message")}
    if "thinking" in value:
        clean["thinking"] = _text(value["thinking"], "the thinking")
    if "stats" in value:
        clean["stats"] = _stats(value["stats"])
    if "stopped" in value:
        if not isinstance(value["stopped"], bool):
            raise Invalid("stopped must be true or false")
        clean["stopped"] = value["stopped"]
    if "files" in value:
        if role != "user" or not isinstance(value["files"], list):
            raise Invalid("only a user message may carry a list of files")
        clean["files"] = [_file(item) for item in value["files"]]
    clean["ts"] = _whole(value.get("ts", now), "a message time")
    return clean


def clean_message(value) -> dict:
    """A checked copy of one message; Invalid otherwise."""
    return _message(value, 0)


def clean_extras(system, settings) -> tuple[str, dict]:
    """A checked system prompt and per chat settings; Invalid otherwise."""
    return _text(system, "the system prompt", SYSTEM_MAX_CHARS), _settings(settings)


def _messages(value, now: int | None) -> list[dict]:
    if not isinstance(value, (list, tuple)):
        raise Invalid("messages must be a list")
    return [_message(item, now) for item in value]


def _from_file(data: bytes, chat_id: str) -> dict:
    try:
        record = json.loads(data.decode("utf-8"))
        if record["v"] != FORMAT or record["id"] != chat_id:
            raise Broken("this chat file is from another version or has been renamed")
        return {
            "v": FORMAT,
            "id": chat_id,
            "title": _title(record["title"]),
            "created": _whole(record["created"], "created"),
            "updated": _whole(record["updated"], "updated"),
            "project_id": _project(record["project_id"]),
            "model": _text(record["model"], "the model", MODEL_MAX),
            "system": _text(record["system"], "the system prompt", SYSTEM_MAX_CHARS),
            "settings": _settings(record["settings"]),
            "messages": _messages(record["messages"], None),
        }
    except (ValueError, KeyError, TypeError, RecursionError, Invalid):
        raise Broken("this chat file cannot be read") from None


def _summary(chat: dict) -> dict:
    keys = ("id", "title", "created", "updated", "model", "project_id")
    return {**{key: chat[key] for key in keys}, "count": len(chat["messages"])}


# --------------------------------------------------------------------------- #
# The store
# --------------------------------------------------------------------------- #
class ChatStore:
    """The chats in one folder. An id is always made here; no caller gives a path."""

    def __init__(self, folder: Path) -> None:
        self.folder = Path(folder)

    def _there(self, create: bool = False) -> bool:
        try:
            return private_folder(self.folder, create)
        except OSError:
            raise Unavailable("the chats folder cannot be used") from None

    def _path(self, chat_id) -> Path:
        if not (isinstance(chat_id, str) and ID_PATTERN.fullmatch(chat_id)):
            raise NotFound("no such chat")
        return self.folder / f"{chat_id}.json"

    def _names(self) -> list[str]:
        if not self._there():
            return []
        try:
            return os.listdir(self.folder)
        except OSError:
            raise Unavailable("the chats folder cannot be read") from None

    def get(self, chat_id: str) -> dict:
        path = self._path(chat_id)
        if not self._there():
            raise NotFound("no such chat")
        try:
            data = read_regular(path, MAX_CHAT_BYTES)
        except FileNotFoundError:
            raise NotFound("no such chat") from None
        except OSError:
            raise Broken("this chat file cannot be read") from None
        return _from_file(data, chat_id)

    def _write(self, chat: dict) -> dict:
        data = json.dumps(chat, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(data) > MAX_CHAT_BYTES:
            raise TooLarge("this chat is full: start a new one")
        self._there(create=True)
        try:
            write_private(self._path(chat["id"]), data)
        except OSError:
            raise Unavailable("the chat could not be saved") from None
        return chat

    def create(
        self,
        messages=(),
        *,
        model: str = "",
        project_id: str | None = None,
        title: str | None = None,
        system: str = "",
        settings: dict | None = None,
    ) -> dict:
        now = _now_ms()
        kept = _messages(messages, now)
        chat = {
            "v": FORMAT,
            "id": secrets.token_hex(16),
            "title": _auto_title(kept) if title is None else _title(title),
            "created": now,
            "updated": now,
            "project_id": _project(project_id),
            "model": _text(model, "the model", MODEL_MAX),
            "system": _text(system, "the system prompt", SYSTEM_MAX_CHARS),
            "settings": _settings({} if settings is None else settings),
            "messages": kept,
        }
        with WRITE_LOCK:
            return self._write(chat)

    def _current(self, chat_id: str, updated: int) -> dict:
        """The saved chat, only if the writer has seen its latest change. Hold the lock."""
        if type(updated) is not int:
            raise Invalid("updated must be a whole number")
        chat = self.get(chat_id)
        if chat["updated"] != updated:
            raise Stale("this chat was changed in another window: open it again")
        return chat

    def current(self, chat_id: str, updated: int) -> dict:
        """The saved chat, if it has not changed since ``updated``."""
        with WRITE_LOCK:
            return self._current(chat_id, updated)

    def append(
        self,
        chat_id: str,
        updated: int,
        messages,
        *,
        model: str | None = None,
        system: str | None = None,
        settings: dict | None = None,
    ) -> dict:
        """Add a turn to the chat last seen at ``updated``, with what it was asked with."""
        now = _now_ms()
        added = _messages(messages, now)
        if not added:
            raise Invalid("there is nothing to add")
        if system is not None:
            system = _text(system, "the system prompt", SYSTEM_MAX_CHARS)
        if settings is not None:
            settings = _settings(settings)
        with WRITE_LOCK:
            chat = self._current(chat_id, updated)
            if system is not None:
                chat["system"] = system
            if settings is not None:
                chat["settings"] = settings
            if not chat["messages"] and chat["title"] == NEW_TITLE:
                chat["title"] = _auto_title(added)
            chat["messages"] += added
            if model is not None:
                chat["model"] = _text(model, "the model", MODEL_MAX)
            chat["updated"] = max(now, updated + 1)
            return self._write(chat)

    def update(
        self,
        chat_id: str,
        updated: int,
        *,
        system: str | None = None,
        settings: dict | None = None,
        model: str | None = None,
        project_id=_UNCHANGED,
    ) -> dict:
        """Change what is given. ``project_id=None`` takes the chat out of its project."""
        changes = {}
        if system is not None:
            changes["system"] = _text(system, "the system prompt", SYSTEM_MAX_CHARS)
        if settings is not None:
            changes["settings"] = _settings(settings)
        if model is not None:
            changes["model"] = _text(model, "the model", MODEL_MAX)
        if project_id is not _UNCHANGED:
            changes["project_id"] = _project(project_id)
        with WRITE_LOCK:
            chat = self._current(chat_id, updated)
            return self._write({**chat, **changes, "updated": max(_now_ms(), updated + 1)})

    @staticmethod
    def summary(chat: dict) -> dict:
        """A chat without its text, as ``list`` gives it."""
        return _summary(chat)

    def rename(self, chat_id: str, title: str) -> dict:
        """``updated`` stays, so a reply being written can still be saved."""
        title = _title(title)
        with WRITE_LOCK:
            return self._write({**self.get(chat_id), "title": title})

    def _scan(self, wanted: str) -> dict:
        chats, broken = [], []
        for name in self._names():
            if not (match := CHAT_NAME.fullmatch(name)):
                continue
            try:
                chat = self.get(match[1])
            except NotFound:  # deleted while we were reading
                continue
            except Broken as exc:
                broken.append({"id": match[1], "reason": str(exc)})
                continue
            texts = [chat["title"], *(m["content"] for m in chat["messages"])]
            if not wanted or any(wanted in text.casefold() for text in texts):
                chats.append(_summary(chat))
        chats.sort(key=lambda chat: (chat["updated"], chat["id"]), reverse=True)
        broken.sort(key=lambda entry: entry["id"])
        return {"chats": chats, "broken": broken}

    def list(self) -> dict:
        """Newest first, without message text; unreadable files are named in ``broken``."""
        return self._scan("")

    def search(self, query: str) -> dict:
        """Like ``list``, for chats whose title or messages hold ``query``."""
        return self._scan(_text(query, "the search", QUERY_MAX).strip().casefold())

    def delete(self, chat_id: str) -> None:
        """Remove one chat, readable or not. Not a secure erase."""
        path = self._path(chat_id)
        with WRITE_LOCK:
            found = [
                name
                for name in self._names()
                if (match := CHAT_NAME.fullmatch(name) or LEFTOVER_NAME.fullmatch(name))
                and match[1] == chat_id
            ]
            if not found:
                raise NotFound("no such chat")
            try:
                for name in found:
                    (path.parent / name).unlink(missing_ok=True)
            except OSError:
                raise Unavailable("the chat could not be deleted") from None

    def delete_all(self) -> dict:
        """Remove every chat file, unreadable ones included; other files stay.

        ``deleted`` counts chats (leftover copies go too); ``failed`` counts files left.
        """
        deleted = failed = 0
        with WRITE_LOCK:
            for name in self._names():
                chat = CHAT_NAME.fullmatch(name)
                if not (chat or LEFTOVER_NAME.fullmatch(name)):
                    continue
                try:
                    (self.folder / name).unlink(missing_ok=True)
                except OSError:
                    failed += 1
                else:
                    deleted += bool(chat)
        return {"deleted": deleted, "failed": failed}
