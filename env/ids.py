from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from env.state import WorkspaceState


def next_id(state: WorkspaceState, prefix: str) -> str:
    if prefix not in state._counters:
        state._counters[prefix] = 0
    state._counters[prefix] += 1
    return f"{prefix}{state._counters[prefix]:03d}"
