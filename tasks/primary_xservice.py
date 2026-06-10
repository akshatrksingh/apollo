from __future__ import annotations

from typing import TYPE_CHECKING

from verifiers.utils import (
    field_equals,
    message_exists_in,
    no_collateral_damage,
)

if TYPE_CHECKING:
    from env.state import WorkspaceState

from env.ids import next_id

TASK_ID = "primary_xservice"

INSTRUCTION = (
    "The payments service outage that was reported in #incidents this morning "
    "still needs to be picked up. Find the open task linked to that incident, "
    "mark it as in progress, then post a short status update for the team in "
    "#incidents so everyone knows it is being worked on."
)

ALLOWED_CHANGES = {
    "tasks": {"modified": {"T003": {"status", "updated_at"}}},
    "messages": {"added": {"C002": 1}},
}


def seed(state: WorkspaceState) -> None:
    def _msg(channel: str, author: str, text: str, parent_id: str | None = None) -> str:
        msg_id = next_id(state, "M")
        ts = state.clock.next()
        state.messages[msg_id] = {
            "id": msg_id,
            "channel": channel,
            "author": author,
            "text": text,
            "ts": ts,
            "parent_id": parent_id,
            "reactions": {},
            "edited": False,
            "original_text": None,
            "edited_by": None,
            "edited_at": None,
        }
        return msg_id

    def _task(
        title: str,
        description: str,
        assignee: str,
        status: str,
        slack_message_id: str | None = None,
    ) -> str:
        task_id = next_id(state, "T")
        ts = state.clock.next()
        state.tasks[task_id] = {
            "id": task_id,
            "title": title,
            "description": description,
            "assignee": assignee,
            "status": status,
            "slack_message_id": slack_message_id,
            "created_at": ts,
            "updated_at": ts,
        }
        return task_id

    # A. Reconfigure M007 in place into the incident root. No clock.next so ts stays.
    # "payments outage" must appear as a contiguous substring (search_messages is substring).
    m007 = state.messages["M007"]
    m007["author"] = "U002"
    m007["text"] = (
        "INC-010: payments outage on prod, checkout requests failing with 5xx, "
        "customers cannot complete purchases. Opening an incident."
    )

    # B. Two thread replies on M007 confirming the active outage.
    # Must NOT contain the contiguous substring "payments outage".
    _msg(
        "C002",
        "U003",
        "Confirmed, the checkout flow is down for all regions, error rate at 100 percent.",
        parent_id="M007",
    )
    _msg(
        "C002",
        "U002",
        "Still active, payment gateway timing out, no mitigation in place yet.",
        parent_id="M007",
    )

    # C. Manager decoy: top-level, contains "payments outage", no linked task.
    _msg(
        "C002",
        "U001",
        "Forwarding a summary from leadership: the payments outage from last quarter "
        "is closed out, this is for the retro doc only.",
    )

    # D. Reconfigure T003 in place into the TARGET linked to M007.
    t003 = state.tasks["T003"]
    t003["title"] = "Investigate payments checkout outage"
    t003["description"] = (
        "Checkout is failing with 5xx during the payments outage. "
        "Find the failing dependency and restore purchase flow."
    )
    t003["assignee"] = "U002"
    t003["status"] = "todo"
    t003["slack_message_id"] = "M007"

    # E. Repoint T001 off M007 to M004 and make it non-in_progress (distractor).
    t001 = state.tasks["T001"]
    t001["slack_message_id"] = "M004"
    t001["status"] = "todo"

    # F. Payments + todo decoy task with no slack link, so status+keyword cannot pin T003.
    _task(
        "Add payments retry budget",
        "Define a retry budget for the payments service to absorb transient outage blips.",
        "U003",
        "todo",
        slack_message_id=None,
    )


def assertions(
    state_before: WorkspaceState,
    state_after: WorkspaceState,
    trajectory: list,
) -> list[tuple[str, bool]]:
    episode_start = state_before.clock.current()

    a1 = field_equals(
        state_after, ("tasks", "T003", "status"), "in_progress"
    )

    a2 = message_exists_in(
        state_after,
        "C002",
        author=state_after.current_user,
        after_ts=episode_start,
        top_level=True,
    )

    a3 = no_collateral_damage(state_before, state_after, ALLOWED_CHANGES)

    return [
        ("task_status_correct", a1),
        ("notification_posted", a2),
        ("no_collateral_damage", a3),
    ]


def verify(
    state_before: WorkspaceState,
    state_after: WorkspaceState,
    trajectory: list,
) -> float:
    results = assertions(state_before, state_after, trajectory)
    return sum(1.0 for _, passed in results if passed) / 3.0
