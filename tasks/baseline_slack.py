from __future__ import annotations

from typing import TYPE_CHECKING

from verifiers.utils import no_collateral_damage

if TYPE_CHECKING:
    from env.state import WorkspaceState

from env.ids import next_id

TASK_ID = "baseline_slack"

# M counter starts at 25 in base_workspace; first next_id call yields M026.
TARGET_ID = "M026"

INSTRUCTION = "Someone reported a database latency spike in #incidents earlier today. Find that report and react to it with :eyes: to acknowledge it."

ALLOWED_CHANGES = {"messages": {"modified": {"M026": {"reactions"}}}}


def seed(state: WorkspaceState) -> None:
    def _msg(channel: str, author: str, text: str) -> str:
        msg_id = next_id(state, "M")
        ts = state.clock.next()
        state.messages[msg_id] = {
            "id": msg_id,
            "channel": channel,
            "author": author,
            "text": text,
            "ts": ts,
            "parent_id": None,
            "reactions": {},
            "edited": False,
            "original_text": None,
            "edited_by": None,
            "edited_at": None,
        }
        return msg_id

    # M026 (TARGET): database latency report - added first so it lands at ts=33
    # and falls off page 1 once 10 newer messages exist.
    _msg("C002", "U002", "INC-005: Seeing elevated database latency on prod, queries taking 3-5x longer than baseline.")

    # M027 (DECOY 1): mentions latency but network context, not database
    _msg("C002", "U001", "FYI: network latency to US-East CDN edge nodes is higher than usual, investigating with infra.")

    # M028 (DECOY 2): mentions latency but API response context, not database
    _msg("C002", "U003", "API response latency for the /search endpoint spiked briefly, traced to a slow upstream.")

    # M029-M036: 8 filler incident messages to push M027/M026 off easy reach
    _msg("C002", "U001", "INC-006: Deploy pipeline blocked, runner queue depth over threshold.")
    _msg("C002", "U002", "INC-006: Scaled runner pool, queue draining now.")
    _msg("C002", "U003", "INC-007: S3 bucket policy change caused read errors in staging.")
    _msg("C002", "U001", "INC-007: Reverted policy change, staging reads restored.")
    _msg("C002", "U002", "INC-008: Memory pressure on worker-node-03, OOMKiller triggered.")
    _msg("C002", "U003", "INC-008: Restarted affected pods, monitoring for recurrence.")
    _msg("C002", "U001", "INC-009: TLS cert for api.example.com expires in 48 hours.")
    _msg("C002", "U002", "INC-009: Cert renewal initiated, will auto-deploy on completion.")


def assertions(
    state_before: WorkspaceState,
    state_after: WorkspaceState,
    trajectory: list,
) -> list[tuple[str, bool]]:
    target_before = state_before.messages.get(TARGET_ID, {})
    reactions_before = target_before.get("reactions", {})
    target_after = state_after.messages.get(TARGET_ID, {})
    target_reactions_after = target_after.get("reactions", {})

    a1 = (
        state_after.current_user not in reactions_before.get("eyes", set())
        and state_after.current_user in target_reactions_after.get("eyes", set())
    )

    # assertion2: nothing changed outside ALLOWED_CHANGES
    a2 = no_collateral_damage(state_before, state_after, ALLOWED_CHANGES)

    return [
        ("target_reacted", a1),
        ("no_collateral_damage", a2),
    ]


def verify(
    state_before: WorkspaceState,
    state_after: WorkspaceState,
    trajectory: list,
) -> float:
    results = assertions(state_before, state_after, trajectory)
    return 1.0 if all(passed for _, passed in results) else 0.0
