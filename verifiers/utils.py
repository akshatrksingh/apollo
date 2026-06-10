from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from env.state import WorkspaceState

DiffReport = dict
AllowedChanges = dict

_COLLECTIONS = ("users", "channels", "messages", "tasks")


def field_equals(state: WorkspaceState, path: tuple, expected: Any) -> bool:
    cur: Any = state
    for key in path:
        if isinstance(cur, dict):
            if key not in cur:
                return False
            cur = cur[key]
        else:
            if not hasattr(cur, key):
                return False
            cur = getattr(cur, key)
    return cur == expected


def message_exists_in(
    state: WorkspaceState,
    channel_id: str,
    *,
    author: str | None = None,
    after_ts: int | None = None,
    top_level: bool = False,
    text_contains: str | None = None,
) -> bool:
    needle = text_contains.lower().strip() if text_contains is not None else None
    for m in state.messages.values():
        if m["channel"] != channel_id:
            continue
        if author is not None and m["author"] != author:
            continue
        if after_ts is not None and not m["ts"] > after_ts:
            continue
        if top_level and m["parent_id"] is not None:
            continue
        if needle is not None and needle not in m["text"].lower().strip():
            continue
        return True
    return False


def _reactions_equal(a: dict, b: dict) -> bool:
    if set(a.keys()) != set(b.keys()):
        return False
    return all(set(a[k]) == set(b[k]) for k in a)


def _changed_fields(before: dict, after: dict) -> set[str]:
    changed: set[str] = set()
    for key in set(before.keys()) | set(after.keys()):
        bv = before.get(key)
        av = after.get(key)
        if key == "reactions" and isinstance(bv, dict) and isinstance(av, dict):
            if not _reactions_equal(bv, av):
                changed.add(key)
            continue
        if bv != av:
            changed.add(key)
    return changed


def diff_keys(before: WorkspaceState, after: WorkspaceState) -> DiffReport:
    report: DiffReport = {}
    for coll in _COLLECTIONS:
        b = getattr(before, coll)
        a = getattr(after, coll)
        b_ids = set(b.keys())
        a_ids = set(a.keys())
        added = sorted(a_ids - b_ids)
        removed = sorted(b_ids - a_ids)
        modified: list[tuple] = []
        for _id in sorted(b_ids & a_ids):
            fields = _changed_fields(b[_id], a[_id])
            if fields:
                modified.append((_id, fields))
        report[coll] = {"added": added, "removed": removed, "modified": modified}
    return report


def no_collateral_damage(
    before: WorkspaceState, after: WorkspaceState, allowed: AllowedChanges
) -> bool:
    report = diff_keys(before, after)

    for coll in _COLLECTIONS:
        coll_diff = report[coll]
        coll_allowed = allowed.get(coll)

        added = coll_diff["added"]
        removed = coll_diff["removed"]
        modified = coll_diff["modified"]

        if coll_allowed is None:
            if added or removed or modified:
                return False
            continue

        if coll == "messages":
            budget = coll_allowed.get("added", {})
            per_channel: dict[str, int] = {}
            for _id in added:
                channel = after.messages[_id]["channel"]
                per_channel[channel] = per_channel.get(channel, 0) + 1
            for channel, count in per_channel.items():
                if count > budget.get(channel, 0):
                    return False
        else:
            budget_max = coll_allowed.get("added", 0)
            if len(added) > budget_max:
                return False

        allowed_removed = set(coll_allowed.get("removed", []))
        for _id in removed:
            if _id not in allowed_removed:
                return False

        allowed_modified = coll_allowed.get("modified", {})
        for _id, fields in modified:
            if _id not in allowed_modified:
                return False
            if not fields <= set(allowed_modified[_id]):
                return False

    return True


def any_tool_called_before(
    trajectory: list, read_tool: str, write_tool: str
) -> bool:
    write_idx = None
    for i, call in enumerate(trajectory):
        if call["tool"] == write_tool:
            write_idx = i
            break
    if write_idx is None:
        return False
    for call in trajectory[:write_idx]:
        if call["tool"] == read_tool:
            return True
    return False
