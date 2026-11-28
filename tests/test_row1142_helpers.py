"""Shared by the row 1142 tests."""

from __future__ import annotations

from test_row837_chat import ONE, events, post


def user(text, **more):
    return {"role": "user", "content": text, **more}


def send(chats, messages, **extra):
    body = {"repo": ONE, "messages": messages, **extra}
    return events(post(chats.base, "/api/chat", body), "done")[0][1]
