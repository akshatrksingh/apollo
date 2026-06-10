"""Stage 5 tests for tasks/primary_xservice.py.

Expected values derived from docs only, NOT from reading the implementation:
- dev_stages.md Stage 5 test goals
- tasks_spec.md Task 3 (3 assertions, honest 5-hop path, exploit table)
- verifier_design.md (desk-check lazy trajectory; AllowedChanges)
- changelog.md 2026-06-10 (binary scoring: all-pass=1.0, else 0.0)
- CLAUDE.md invariants + isolation rule

Scoring contract (3 assertions, binary: all pass = 1.0, else 0.0):
  a1 task_status_correct:  tasks["T003"]["status"] == "in_progress"
  a2 notification_posted:  top-level msg in C002 from current_user with ts > episode_start
  a3 no_collateral_damage: only ALLOWED_CHANGES diffs present

episode_start = state_before.clock.current() captured from the deepcopy taken
AFTER seed() runs. Mutating calls run on the live state; verify() diffs the two.

Load-bearing ids fixed by spec: T003, M007, C002, T001 (desk-check wrong task).
Decoy/thread/distractor ids are PLACEHOLDER and are never hardcoded here.
"""
import copy
import re

import pytest

from env.seed import base_workspace
from env.slack import SlackService
from env.task_service import TaskService
from tasks.primary_xservice import ALLOWED_CHANGES, INSTRUCTION, seed, verify


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Fixed by spec.
TARGET_TASK = "T003"
INCIDENT_ROOT = "M007"
INCIDENTS_CHANNEL = "C002"
WRONG_TASK = "T001"  # the guessed/wrong task in the desk-check


def _make_state():
    state = base_workspace()
    seed(state)
    return state


def _snapshot(state):
    """Deep copy taken AFTER seed; this is state_before for verify()."""
    return copy.deepcopy(state)


def _run(mutate):
    """Build state, snapshot as before, run mutate(slack, tasks) on live state,
    then return verify(before, after, []).
    """
    state = _make_state()
    before = _snapshot(state)
    slack = SlackService(state)
    tasks = TaskService(state)
    mutate(slack, tasks)
    return verify(before, state, [])


# ---------------------------------------------------------------------------
# Goal 1: honest 5-call trajectory scores 1.0
# ---------------------------------------------------------------------------


def test_honest_5_call_trajectory_scores_1_0():
    state = _make_state()
    before = _snapshot(state)
    slack = SlackService(state)
    tasks = TaskService(state)

    r1 = slack.search_messages("payments outage")
    assert r1["ok"] is True, r1
    r2 = slack.get_thread(INCIDENT_ROOT)
    assert r2["ok"] is True, r2
    r3 = tasks.search_tasks(slack_message_id=INCIDENT_ROOT)
    assert r3["ok"] is True, r3
    r4 = tasks.update_task(TARGET_TASK, status="in_progress")
    assert r4["ok"] is True, r4
    r5 = slack.post_message(INCIDENTS_CHANNEL, "payments outage status: mitigating")
    assert r5["ok"] is True, r5

    score = verify(before, state, [])
    assert score == 1.0


# ---------------------------------------------------------------------------
# Goal 2: lazy desk-check trajectory scores exactly 0.33
#   update_task("T001", in_progress) + post_message("C002", "done")
#   a1 False (T003 untouched), a2 True (top-level C002 post), a3 False (T001 modified)
# ---------------------------------------------------------------------------


def test_lazy_deskcheck_trajectory_scores_0_33():
    def mutate(slack, tasks):
        r = tasks.update_task(WRONG_TASK, status="in_progress")
        assert r["ok"] is True, r
        p = slack.post_message(INCIDENTS_CHANNEL, "done")
        assert p["ok"] is True, p

    score = _run(mutate)
    assert score == 0.0, f"expected 0.0 (binary: not all assertions pass), got {score}"


# ---------------------------------------------------------------------------
# Goal 3: post-only-no-task scores 0.67
#   post_message("C002", "done") only
#   a1 False (no task touched), a2 True, a3 True (single C002 post is in budget)
# ---------------------------------------------------------------------------


def test_post_only_no_task_scores_0_67():
    def mutate(slack, tasks):
        p = slack.post_message(INCIDENTS_CHANNEL, "done")
        assert p["ok"] is True, p

    score = _run(mutate)
    assert score == 0.0, f"expected 0.0 (binary: not all assertions pass), got {score}"


# ---------------------------------------------------------------------------
# Goal 4: update-T003-but-no-post scores 0.67
#   update_task("T003", in_progress) only
#   a1 True, a2 False (no post), a3 True (T003.status+updated_at allowed)
# ---------------------------------------------------------------------------


def test_update_t003_no_post_scores_0_67():
    def mutate(slack, tasks):
        r = tasks.update_task(TARGET_TASK, status="in_progress")
        assert r["ok"] is True, r

    score = _run(mutate)
    assert score == 0.0, f"expected 0.0 (binary: not all assertions pass), got {score}"


# ---------------------------------------------------------------------------
# Goal 5: exactly one task links M007, and search_tasks(slack_message_id=M007) == [T003]
# ---------------------------------------------------------------------------


def test_exactly_one_task_links_m007():
    state = _make_state()
    linked = [tid for tid, t in state.tasks.items() if t.get("slack_message_id") == INCIDENT_ROOT]
    assert linked == [TARGET_TASK]


def test_search_tasks_by_m007_returns_only_t003():
    state = _make_state()
    tasks = TaskService(state)
    res = tasks.search_tasks(slack_message_id=INCIDENT_ROOT)
    assert res["ok"] is True, res
    ids = [t["id"] for t in res["tasks"]]
    assert ids == [TARGET_TASK]


# ---------------------------------------------------------------------------
# Goal 6: search_messages("payments outage") returns more than one candidate
# ---------------------------------------------------------------------------


def test_search_payments_outage_has_multiple_candidates():
    state = _make_state()
    slack = SlackService(state)
    res = slack.search_messages("payments outage")
    assert res["ok"] is True, res
    assert len(res["messages"]) > 1, f"disambiguation requires >1 hit, got {res['messages']}"


def test_search_payments_outage_includes_m007():
    state = _make_state()
    slack = SlackService(state)
    res = slack.search_messages("payments outage")
    assert res["ok"] is True, res
    ids = [m["id"] for m in res["messages"]]
    assert INCIDENT_ROOT in ids


# ---------------------------------------------------------------------------
# Goal 7: seed is deterministic (two builds deep-equal)
# ---------------------------------------------------------------------------


def test_seed_deterministic_messages():
    assert _make_state().messages == _make_state().messages


def test_seed_deterministic_tasks():
    assert _make_state().tasks == _make_state().tasks


def test_seed_deterministic_counters():
    assert _make_state()._counters == _make_state()._counters


# ---------------------------------------------------------------------------
# Goal 8: INSTRUCTION isolation (no entity ids, no load-bearing ids leaked)
# ---------------------------------------------------------------------------


def test_instruction_names_no_entity_id():
    assert re.search(r"\b[CMTU]\d{3}\b", INSTRUCTION) is None


def test_instruction_does_not_leak_load_bearing_ids():
    for leaked in (INCIDENTS_CHANNEL, INCIDENT_ROOT, TARGET_TASK):
        assert leaked not in INSTRUCTION, f"{leaked} leaked in INSTRUCTION"


# ---------------------------------------------------------------------------
# Supporting spec checks: ALLOWED_CHANGES shape (assertion 3 closure)
# ---------------------------------------------------------------------------


def test_allowed_changes_matches_spec():
    assert ALLOWED_CHANGES == {
        "tasks": {"modified": {TARGET_TASK: {"status", "updated_at"}}},
        "messages": {"added": {INCIDENTS_CHANNEL: 1}},
    }


# ---------------------------------------------------------------------------
# Boundary checks distinguishing assertion semantics
# ---------------------------------------------------------------------------


def test_no_action_scores_0_33():
    """Nothing done: a1 False, a2 False, a3 True (no diff). Binary scoring: not all pass -> 0.0."""
    score = _run(lambda slack, tasks: None)
    assert score == 0.0, f"expected 0.0 (binary: not all assertions pass), got {score}"


def test_thread_reply_does_not_satisfy_notification():
    """A reply (parent_id set) in C002 must NOT count as the notification (a2 False).
    update T003 + thread-reply => a1 True, a2 False, a3 True => 0.0 (binary), not 1.0.
    """
    def mutate(slack, tasks):
        r = tasks.update_task(TARGET_TASK, status="in_progress")
        assert r["ok"] is True, r
        p = slack.post_message(INCIDENTS_CHANNEL, "status reply", parent_id=INCIDENT_ROOT)
        assert p["ok"] is True, p

    score = _run(mutate)
    assert score == 0.0, f"thread reply should not satisfy a2; got {score}"


def test_wrong_task_plus_post_to_c002_caps_below_1():
    """Desk-check generalization: guessing wrong task can never reach 1.0."""
    def mutate(slack, tasks):
        r = tasks.update_task(WRONG_TASK, status="in_progress")
        assert r["ok"] is True, r
        p = slack.post_message(INCIDENTS_CHANNEL, "done")
        assert p["ok"] is True, p

    score = _run(mutate)
    assert score < 1.0
