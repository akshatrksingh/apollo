from __future__ import annotations

from typing import TYPE_CHECKING

from verifiers.utils import message_exists_in, no_collateral_damage

if TYPE_CHECKING:
    from env.state import WorkspaceState

TASK_ID = "smoke_test"

INSTRUCTION = "Post the message `env check ok` to the #general channel."

ALLOWED_CHANGES = {"messages": {"added": {"C001": 1}}}


def seed(state: WorkspaceState) -> None:
    pass


def verify(
    state_before: WorkspaceState,
    state_after: WorkspaceState,
    trajectory: list,
) -> float:
    episode_start = state_before.clock.current()

    posted = message_exists_in(
        state_after,
        "C001",
        author=state_after.current_user,
        after_ts=episode_start,
        text_contains="env check ok",
    )
    clean = no_collateral_damage(state_before, state_after, ALLOWED_CHANGES)

    return 1.0 if (posted and clean) else 0.0
