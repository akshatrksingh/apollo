from __future__ import annotations

from env.clock import FrozenClock
from env.state import WorkspaceState


def base_workspace() -> WorkspaceState:
    clock = FrozenClock()

    users = {
        "U001": {"id": "U001", "name": "Alice Chen", "role": "manager", "email": "alice@example.com"},
        "U002": {"id": "U002", "name": "Bob Kim", "role": "engineer", "email": "bob@example.com"},
        "U003": {"id": "U003", "name": "Carol Diaz", "role": "engineer", "email": "carol@example.com"},
        "U004": {"id": "U004", "name": "Agent", "role": "bot", "email": "agent@example.com"},
        "U005": {"id": "U005", "name": "Dave Park", "role": "manager", "email": "dave@example.com"},
    }

    channels = {
        "C001": {"id": "C001", "name": "general", "is_private": False, "members": ["U001", "U002", "U003", "U004", "U005"]},
        "C002": {"id": "C002", "name": "incidents", "is_private": False, "members": ["U001", "U002", "U003", "U004", "U005"]},
        "C003": {"id": "C003", "name": "engineering", "is_private": False, "members": ["U001", "U002", "U003", "U004"]},
        "C004": {"id": "C004", "name": "payments-team", "is_private": True, "members": ["U002", "U003", "U004"]},
        "C005": {"id": "C005", "name": "leadership", "is_private": True, "members": ["U001", "U005"]},
    }

    messages: dict = {}
    tasks: dict = {}

    def _msg(msg_id: str, channel: str, author: str, text: str, parent_id=None) -> None:
        ts = clock.next()
        messages[msg_id] = {
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

    # C001 #general (3 messages)
    _msg("M001", "C001", "U001", "Welcome to the team everyone!")
    _msg("M002", "C001", "U002", "Thanks Alice, excited to be here.")
    _msg("M003", "C001", "U003", "Same here, looking forward to working with you all.")

    # C002 #incidents (12 top-level messages to force pagination past limit=10)
    _msg("M004", "C002", "U001", "INC-001: Payment service latency spike detected.")
    _msg("M005", "C002", "U002", "INC-001: Investigating root cause, likely DB contention.")
    _msg("M006", "C002", "U003", "INC-001: Rolled back last deploy, monitoring now.")
    _msg("M007", "C002", "U001", "INC-001: Resolved. Post-mortem scheduled for Friday.")
    _msg("M008", "C002", "U002", "INC-002: Auth service returning 500s intermittently.")
    _msg("M009", "C002", "U003", "INC-002: Traced to a misconfigured rate limit.")
    _msg("M010", "C002", "U001", "INC-002: Fix deployed, error rate back to zero.")
    _msg("M011", "C002", "U002", "INC-003: CDN cache invalidation causing stale responses.")
    _msg("M012", "C002", "U003", "INC-003: Purged CDN cache manually, issue resolved.")
    _msg("M013", "C002", "U001", "INC-004: Disk usage at 92% on prod-db-02.")
    _msg("M014", "C002", "U002", "INC-004: Archived old logs, usage now at 61%.")
    _msg("M015", "C002", "U003", "INC-004: Added disk usage alert threshold at 80%.")

    # Thread reply on M004 to demonstrate threading
    _msg("M016", "C002", "U004", "On it, I will watch the metrics dashboard.", parent_id="M004")

    # C003 #engineering (3 messages)
    _msg("M017", "C003", "U002", "PR #42 is up for the new retry logic.")
    _msg("M018", "C003", "U003", "Left comments on PR #42, looks good overall.")
    _msg("M019", "C003", "U001", "Merging PR #42 after CI passes.")

    # C004 #payments-team (2 messages)
    _msg("M020", "C004", "U002", "Stripe integration tests all passing.")
    _msg("M021", "C004", "U003", "Payment reconciliation job scheduled for Monday.")

    # C005 #leadership (3 messages, agent cannot see via member-scoped tools)
    _msg("M022", "C005", "U001", "Q3 budget review on Thursday at 2pm.")
    _msg("M023", "C005", "U005", "I will prepare the headcount slides.")
    _msg("M024", "C005", "U001", "Roadmap priorities: finalize top 5 by EOW.")

    # Thread reply on M017 to demonstrate threading in C003
    _msg("M025", "C003", "U004", "Running the tests now, will report back.", parent_id="M017")

    # max message number used = 25

    def _task(task_id: str, title: str, description: str, assignee: str, status: str, slack_message_id=None) -> None:
        ts = clock.next()
        tasks[task_id] = {
            "id": task_id,
            "title": title,
            "description": description,
            "assignee": assignee,
            "status": status,
            "slack_message_id": slack_message_id,
            "created_at": ts,
            "updated_at": ts,
        }

    _task("T001", "Write post-mortem for INC-001", "Document timeline, root cause, and action items for the payment latency incident.", "U001", "in_progress", slack_message_id="M007")
    _task("T002", "Fix auth rate limit config", "Update rate limit values in auth service config and add regression test.", "U002", "done", slack_message_id="M010")
    _task("T003", "Automate disk usage alerting", "Wire disk usage metrics to PagerDuty and set 80% threshold.", "U003", "todo", slack_message_id="M015")
    _task("T004", "Review Stripe integration", "Audit Stripe webhook handlers for idempotency gaps.", "U002", "in_progress")
    _task("T005", "Merge retry logic PR", "Review and merge PR #42 after all reviewers approve.", "U001", "done")
    _task("T006", "Reconciliation job monitoring", "Add alerting for failures in the payment reconciliation job.", "U003", "blocked")
    _task("T007", "Onboarding checklist update", "Update the onboarding doc to reflect new tooling.", "U001", "todo")

    state = WorkspaceState(
        users=users,
        channels=channels,
        messages=messages,
        tasks=tasks,
        clock=clock,
        current_user="U004",
        counters={"M": 25, "C": 5, "T": 7, "U": 5},
    )

    return state
