from __future__ import annotations

from typing import TypedDict

from env.clock import FrozenClock


class User(TypedDict):
    id: str
    name: str
    role: str
    email: str


class Channel(TypedDict):
    id: str
    name: str
    is_private: bool
    members: list[str]


class Message(TypedDict):
    id: str
    channel: str
    author: str
    text: str
    ts: int
    parent_id: str | None
    reactions: dict[str, set[str]]
    edited: bool
    original_text: str | None
    edited_by: str | None
    edited_at: int | None


class Task(TypedDict):
    id: str
    title: str
    description: str
    assignee: str
    status: str
    slack_message_id: str | None
    created_at: int
    updated_at: int


class WorkspaceState:
    def __init__(
        self,
        users: dict[str, User],
        channels: dict[str, Channel],
        messages: dict[str, Message],
        tasks: dict[str, Task],
        clock: FrozenClock,
        current_user: str,
        counters: dict[str, int],
    ) -> None:
        self.users = users
        self.channels = channels
        self.messages = messages
        self.tasks = tasks
        self.clock = clock
        self.current_user = current_user
        self._counters = counters
