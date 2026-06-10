from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from env.state import WorkspaceState

from env.ids import next_id
from env.state import Message


def _message_view(msg: Message) -> dict:
    return {
        "id": msg["id"],
        "channel": msg["channel"],
        "author": msg["author"],
        "text": msg["text"],
        "ts": msg["ts"],
        "parent_id": msg["parent_id"],
        "reactions": msg["reactions"],
        "edited": msg["edited"],
    }


def _parse_cursor(cursor: str | None) -> int | None:
    if cursor is None:
        return None
    try:
        return int(cursor)
    except (ValueError, TypeError):
        return -1


class SlackService:
    def __init__(self, state: WorkspaceState) -> None:
        self._state = state

    @property
    def _current_user(self) -> str:
        return self._state.current_user

    def list_channels(self) -> dict:
        result = []
        for ch in self._state.channels.values():
            if self._current_user in ch["members"]:
                result.append({
                    "id": ch["id"],
                    "name": ch["name"],
                    "is_private": ch["is_private"],
                })
        return {"ok": True, "channels": result}

    def get_channel_messages(
        self, channel_id: str, limit: int = 10, cursor: str | None = None
    ) -> dict:
        ch = self._state.channels.get(channel_id)
        if ch is None:
            return {"ok": False, "error": "channel_not_found"}
        if self._current_user not in ch["members"]:
            return {"ok": False, "error": "not_authorized"}

        offset = 0
        if cursor is not None:
            parsed = _parse_cursor(cursor)
            if parsed == -1:
                return {"ok": False, "error": "invalid_cursor"}
            offset = parsed

        top_level = [
            m for m in self._state.messages.values()
            if m["channel"] == channel_id and m["parent_id"] is None
        ]
        top_level.sort(key=lambda m: (m["ts"], m["id"]), reverse=True)

        page = top_level[offset: offset + limit]
        next_offset = offset + limit
        next_cursor = str(next_offset) if next_offset < len(top_level) else None

        return {
            "ok": True,
            "messages": [_message_view(m) for m in page],
            "next_cursor": next_cursor,
        }

    def post_message(
        self, channel_id: str, text: str, parent_id: str | None = None
    ) -> dict:
        ch = self._state.channels.get(channel_id)
        if ch is None:
            return {"ok": False, "error": "channel_not_found"}
        if self._current_user not in ch["members"]:
            return {"ok": False, "error": "not_authorized"}
        if parent_id is not None and parent_id not in self._state.messages:
            return {"ok": False, "error": "message_not_found"}

        msg_id = next_id(self._state, "M")
        ts = self._state.clock.next()
        msg: Message = {
            "id": msg_id,
            "channel": channel_id,
            "author": self._current_user,
            "text": text,
            "ts": ts,
            "parent_id": parent_id,
            "reactions": {},
            "edited": False,
            "original_text": None,
            "edited_by": None,
            "edited_at": None,
        }
        self._state.messages[msg_id] = msg
        return {"ok": True, "message": _message_view(msg)}

    def update_message(self, message_id: str, text: str) -> dict:
        msg = self._state.messages.get(message_id)
        if msg is None:
            return {"ok": False, "error": "message_not_found"}
        if msg["author"] != self._current_user:
            return {"ok": False, "error": "not_authorized"}

        if not msg["edited"]:
            msg["original_text"] = msg["text"]

        msg["text"] = text
        msg["edited"] = True
        msg["edited_by"] = self._current_user
        msg["edited_at"] = self._state.clock.next()

        return {"ok": True, "message": _message_view(msg)}

    def add_reaction(self, message_id: str, emoji: str) -> dict:
        msg = self._state.messages.get(message_id)
        if msg is None:
            return {"ok": False, "error": "message_not_found"}

        if emoji not in msg["reactions"]:
            msg["reactions"][emoji] = set()
        msg["reactions"][emoji].add(self._current_user)

        return {"ok": True}

    def create_channel(self, name: str, is_private: bool = False) -> dict:
        ch_id = next_id(self._state, "C")
        channel = {
            "id": ch_id,
            "name": name,
            "is_private": is_private,
            "members": [self._current_user],
        }
        self._state.channels[ch_id] = channel
        return {"ok": True, "channel": channel}

    def search_messages(
        self, query: str, limit: int = 10, cursor: str | None = None
    ) -> dict:
        offset = 0
        if cursor is not None:
            parsed = _parse_cursor(cursor)
            if parsed == -1:
                return {"ok": False, "error": "invalid_cursor"}
            offset = parsed

        member_channels = {
            ch_id
            for ch_id, ch in self._state.channels.items()
            if self._current_user in ch["members"]
        }

        query_lower = query.lower()
        matched = [
            m for m in self._state.messages.values()
            if m["channel"] in member_channels
            and query_lower in m["text"].lower()
        ]
        matched.sort(key=lambda m: (m["ts"], m["id"]), reverse=True)

        page = matched[offset: offset + limit]
        next_offset = offset + limit
        next_cursor = str(next_offset) if next_offset < len(matched) else None

        return {
            "ok": True,
            "messages": [_message_view(m) for m in page],
            "next_cursor": next_cursor,
        }

    def get_thread(self, message_id: str) -> dict:
        if message_id not in self._state.messages:
            return {"ok": False, "error": "message_not_found"}

        replies = [
            m for m in self._state.messages.values()
            if m["parent_id"] == message_id
        ]
        replies.sort(key=lambda m: (m["ts"], m["id"]))

        return {"ok": True, "messages": [_message_view(m) for m in replies]}

    def list_users(self) -> dict:
        users = [
            {
                "id": u["id"],
                "name": u["name"],
                "role": u["role"],
                "email": u["email"],
            }
            for u in self._state.users.values()
        ]
        return {"ok": True, "users": users}

    def add_member(self, channel_id: str, user_id: str) -> dict:
        ch = self._state.channels.get(channel_id)
        if ch is None:
            return {"ok": False, "error": "channel_not_found"}
        if user_id not in self._state.users:
            return {"ok": False, "error": "user_not_found"}

        if user_id not in ch["members"]:
            ch["members"].append(user_id)

        return {"ok": True}
