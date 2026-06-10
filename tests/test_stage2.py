"""Stage 2 tests: services + base seed."""
import copy

import pytest

from env.seed import base_workspace
from env.slack import SlackService
from env.task_service import TaskService
from env.state import WorkspaceState


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_ERROR_CODES = {
    "channel_not_found",
    "message_not_found",
    "not_authorized",
    "user_not_found",
    "task_not_found",
    "invalid_status",
    "invalid_cursor",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _snapshot(state: WorkspaceState) -> dict:
    """Return a deep snapshot of the mutable collections only."""
    return {
        "users": copy.deepcopy(state.users),
        "channels": copy.deepcopy(state.channels),
        "messages": copy.deepcopy(state.messages),
        "tasks": copy.deepcopy(state.tasks),
    }


def _assert_no_mutation(before: dict, state: WorkspaceState) -> None:
    after = {
        "users": state.users,
        "channels": state.channels,
        "messages": state.messages,
        "tasks": state.tasks,
    }
    assert before == after, "Read tool mutated state"


def _fresh():
    """Return a fresh state plus bound services."""
    state = base_workspace()
    slack = SlackService(state)
    tasks = TaskService(state)
    return state, slack, tasks


# ---------------------------------------------------------------------------
# 1. Read tools never mutate state
# ---------------------------------------------------------------------------

READ_TOOL_CALLS = [
    lambda s, t: s.list_channels(),
    lambda s, t: s.get_channel_messages("C001"),
    lambda s, t: s.search_messages("INC"),
    lambda s, t: s.get_thread("M004"),
    lambda s, t: s.list_users(),
    lambda s, t: t.get_task("T001"),
    lambda s, t: t.list_tasks(),
    lambda s, t: t.search_tasks(query="post-mortem"),
]

READ_TOOL_NAMES = [
    "list_channels",
    "get_channel_messages",
    "search_messages",
    "get_thread",
    "list_users",
    "get_task",
    "list_tasks",
    "search_tasks",
]


@pytest.mark.parametrize("tool_fn,name", zip(READ_TOOL_CALLS, READ_TOOL_NAMES))
def test_read_tool_does_not_mutate_state(tool_fn, name):
    state, slack, tasks_svc = _fresh()
    before = _snapshot(state)
    tool_fn(slack, tasks_svc)
    _assert_no_mutation(before, state)


# ---------------------------------------------------------------------------
# 2. Every domain error returns the right shape + code from the closed set
# ---------------------------------------------------------------------------

ERROR_SCENARIOS = [
    # (label, lambda state slack tasks -> result)
    ("get_channel_messages_channel_not_found", lambda s, t: s.get_channel_messages("ZZZZ")),
    ("get_channel_messages_not_authorized", lambda s, t: s.get_channel_messages("C005")),
    ("get_channel_messages_invalid_cursor", lambda s, t: s.get_channel_messages("C001", cursor="not-an-int")),
    ("post_message_channel_not_found", lambda s, t: s.post_message("ZZZZ", "hi")),
    ("post_message_not_authorized", lambda s, t: s.post_message("C005", "hi")),
    ("post_message_message_not_found_parent", lambda s, t: s.post_message("C001", "hi", parent_id="MZZZ")),
    ("update_message_message_not_found", lambda s, t: s.update_message("MZZZ", "new")),
    ("update_message_not_authorized", lambda s, t: s.update_message("M001", "new")),
    ("add_reaction_message_not_found", lambda s, t: s.add_reaction("MZZZ", "thumbsup")),
    ("get_thread_message_not_found", lambda s, t: s.get_thread("MZZZ")),
    ("add_member_channel_not_found", lambda s, t: s.add_member("ZZZZ", "U001")),
    ("add_member_user_not_found", lambda s, t: s.add_member("C001", "UZZZ")),
    ("search_messages_invalid_cursor", lambda s, t: s.search_messages("INC", cursor="bad")),
    ("get_task_task_not_found", lambda s, t: t.get_task("TZZZ")),
    ("delete_task_task_not_found", lambda s, t: t.delete_task("TZZZ")),
    ("update_task_task_not_found", lambda s, t: t.update_task("TZZZ", status="done")),
    ("update_task_invalid_status", lambda s, t: t.update_task("T001", status="bogus")),
    ("create_task_user_not_found", lambda s, t: t.create_task("title", "desc", "UZZZ")),
    ("create_task_invalid_status", lambda s, t: t.create_task("title", "desc", "U001", status="bogus")),
]


@pytest.mark.parametrize("label,scenario", ERROR_SCENARIOS)
def test_domain_error_shape_and_closed_set(label, scenario):
    state, slack, tasks_svc = _fresh()
    result = scenario(slack, tasks_svc)
    assert isinstance(result, dict), f"{label}: result is not a dict"
    assert result.get("ok") is False, f"{label}: ok is not False"
    assert "error" in result, f"{label}: 'error' key missing"
    assert result["error"] in VALID_ERROR_CODES, (
        f"{label}: error code {result['error']!r} not in closed set"
    )


# ---------------------------------------------------------------------------
# 3. post_message error paths
# ---------------------------------------------------------------------------

def test_post_message_nonexistent_channel():
    state, slack, _ = _fresh()
    result = slack.post_message("ZZZZ", "hello")
    assert result == {"ok": False, "error": "channel_not_found"}


def test_post_message_c005_non_member_returns_not_authorized():
    """U004 is not a member of C005; posting there must return not_authorized."""
    state, slack, _ = _fresh()
    assert "U004" not in state.channels["C005"]["members"]
    result = slack.post_message("C005", "hello")
    assert result == {"ok": False, "error": "not_authorized"}


# ---------------------------------------------------------------------------
# 4. update_message authorship
# ---------------------------------------------------------------------------

def test_update_message_other_author_returns_not_authorized():
    """M001 is authored by U001; current_user U004 must get not_authorized."""
    state, slack, _ = _fresh()
    assert state.messages["M001"]["author"] != state.current_user
    result = slack.update_message("M001", "tampered")
    assert result == {"ok": False, "error": "not_authorized"}


# ---------------------------------------------------------------------------
# 5. add_reaction
# ---------------------------------------------------------------------------

def test_add_reaction_missing_message_returns_message_not_found():
    state, slack, _ = _fresh()
    result = slack.add_reaction("MZZZ", "thumbsup")
    assert result == {"ok": False, "error": "message_not_found"}


def test_add_reaction_valid_adds_current_user_to_emoji_set():
    state, slack, _ = _fresh()
    result = slack.add_reaction("M001", "thumbsup")
    assert result == {"ok": True}
    assert "thumbsup" in state.messages["M001"]["reactions"]
    assert state.current_user in state.messages["M001"]["reactions"]["thumbsup"]


# ---------------------------------------------------------------------------
# 6. create_task
# ---------------------------------------------------------------------------

def test_create_task_unknown_assignee_returns_user_not_found():
    state, _, tasks_svc = _fresh()
    result = tasks_svc.create_task("title", "desc", "UZZZ")
    assert result == {"ok": False, "error": "user_not_found"}


def test_create_task_created_at_equals_updated_at():
    state, _, tasks_svc = _fresh()
    result = tasks_svc.create_task("New task", "desc", "U001")
    assert result["ok"] is True
    task = result["task"]
    assert task["created_at"] == task["updated_at"], (
        "created_at and updated_at must be equal on creation"
    )
    assert isinstance(task["created_at"], int) and task["created_at"] > 0


def test_create_task_timestamps_are_clock_tick():
    """created_at and updated_at must both equal the tick returned by clock.next()."""
    state, _, tasks_svc = _fresh()
    tick_before = state.clock.current()
    result = tasks_svc.create_task("New task", "desc", "U001")
    tick_after = state.clock.current()
    assert result["ok"] is True
    task = result["task"]
    # The tick used must be strictly greater than tick_before and <= tick_after
    assert tick_before < task["created_at"] <= tick_after


# ---------------------------------------------------------------------------
# 7. update_task invalid status
# ---------------------------------------------------------------------------

def test_update_task_bogus_status_returns_invalid_status():
    state, _, tasks_svc = _fresh()
    result = tasks_svc.update_task("T001", status="bogus")
    assert result == {"ok": False, "error": "invalid_status"}


# ---------------------------------------------------------------------------
# 8. get_channel_messages pagination + get_thread
# ---------------------------------------------------------------------------

def test_get_channel_messages_returns_top_level_only():
    """No message in the result must have a non-None parent_id."""
    state, slack, _ = _fresh()
    result = slack.get_channel_messages("C002")
    assert result["ok"] is True
    for msg in result["messages"]:
        assert msg["parent_id"] is None, (
            f"Message {msg['id']} has parent_id={msg['parent_id']!r}, must be None"
        )


def test_get_channel_messages_page_size_lte_10():
    state, slack, _ = _fresh()
    result = slack.get_channel_messages("C002")
    assert result["ok"] is True
    assert len(result["messages"]) <= 10


def test_get_channel_messages_returns_next_cursor_when_more_remain():
    """C002 has 12 top-level messages; first page of 10 must have a next_cursor."""
    state, slack, _ = _fresh()
    # Count top-level messages in C002
    top_level_count = sum(
        1 for m in state.messages.values()
        if m["channel"] == "C002" and m["parent_id"] is None
    )
    assert top_level_count > 10, (
        f"Expected >10 top-level messages in C002, found {top_level_count}"
    )
    result = slack.get_channel_messages("C002")
    assert result["ok"] is True
    assert result["next_cursor"] is not None, (
        "next_cursor must be set when more messages remain"
    )


def test_get_thread_returns_children_sorted_ts_asc():
    """M004 has exactly one child (M016); get_thread must return it."""
    state, slack, _ = _fresh()
    # Verify the seed assumption
    children = [m for m in state.messages.values() if m["parent_id"] == "M004"]
    assert len(children) >= 1, "Expected at least one reply to M004"

    result = slack.get_thread("M004")
    assert result["ok"] is True
    returned_ids = {m["id"] for m in result["messages"]}
    expected_ids = {m["id"] for m in children}
    assert returned_ids == expected_ids, (
        f"get_thread returned {returned_ids}, expected {expected_ids}"
    )
    # Sorted ts asc
    ts_list = [m["ts"] for m in result["messages"]]
    assert ts_list == sorted(ts_list), "get_thread results must be sorted by ts ascending"


def test_get_thread_all_results_have_correct_parent_id():
    state, slack, _ = _fresh()
    result = slack.get_thread("M004")
    assert result["ok"] is True
    for msg in result["messages"]:
        assert msg["parent_id"] == "M004", (
            f"Message {msg['id']} has parent_id={msg['parent_id']!r}, expected 'M004'"
        )


# ---------------------------------------------------------------------------
# 9. search_messages properties
# ---------------------------------------------------------------------------

def test_search_messages_case_insensitive():
    """Searching uppercase version of a known term finds the message."""
    state, slack, _ = _fresh()
    # M004 contains "INC-001" - search with all uppercase
    result = slack.search_messages("INC-001")
    assert result["ok"] is True
    ids_lower = {m["id"] for m in result["messages"]}

    result_upper = slack.search_messages("inc-001")
    assert result_upper["ok"] is True
    ids_upper = {m["id"] for m in result_upper["messages"]}

    assert ids_lower == ids_upper, (
        "search_messages must be case-insensitive: uppercase and lowercase queries must match the same messages"
    )
    assert len(ids_lower) > 0, "Expected at least one result for 'INC-001'"


def test_search_messages_scoped_to_member_channels():
    """A term only in C005 messages must return empty results for U004 (non-member)."""
    state, slack, _ = _fresh()
    # Verify U004 is not in C005
    assert "U004" not in state.channels["C005"]["members"]
    # "headcount" only appears in C005 messages (M023: "I will prepare the headcount slides.")
    # Confirm the term exists in C005 but not elsewhere
    c005_texts = [state.messages[mid]["text"] for mid in state.messages if state.messages[mid]["channel"] == "C005"]
    other_texts = [state.messages[mid]["text"] for mid in state.messages if state.messages[mid]["channel"] != "C005"]
    assert any("headcount" in t.lower() for t in c005_texts), "Test setup: 'headcount' must appear in C005"
    assert not any("headcount" in t.lower() for t in other_texts), "Test setup: 'headcount' must not appear outside C005"

    result = slack.search_messages("headcount")
    assert result["ok"] is True
    assert result["messages"] == [], (
        "search_messages must not return messages from non-member channels"
    )


def test_search_messages_recency_ordered():
    """Results must be most-recent first (descending ts)."""
    state, slack, _ = _fresh()
    result = slack.search_messages("INC")
    assert result["ok"] is True
    messages = result["messages"]
    assert len(messages) > 1, "Need at least 2 results to check ordering"
    ts_list = [m["ts"] for m in messages]
    assert ts_list == sorted(ts_list, reverse=True), (
        "search_messages results must be ordered by ts descending (most recent first)"
    )


def test_search_messages_capped_at_10():
    """Results page size must not exceed 10."""
    state, slack, _ = _fresh()
    # Use a query matching many messages
    result = slack.search_messages("INC")
    assert result["ok"] is True
    assert len(result["messages"]) <= 10


# ---------------------------------------------------------------------------
# 10. base_workspace() is deterministic
# ---------------------------------------------------------------------------

def test_base_workspace_is_deterministic():
    """Two calls to base_workspace() must produce deeply equal states."""
    state1 = base_workspace()
    state2 = base_workspace()

    assert state1.users == state2.users, "users differ between two base_workspace() calls"
    assert state1.channels == state2.channels, "channels differ between two base_workspace() calls"
    assert state1.tasks == state2.tasks, "tasks differ between two base_workspace() calls"

    # Messages may contain sets (reactions); deep-compare field by field
    assert set(state1.messages.keys()) == set(state2.messages.keys()), (
        "message keys differ"
    )
    for mid in state1.messages:
        m1 = state1.messages[mid]
        m2 = state2.messages[mid]
        for field in ("id", "channel", "author", "text", "ts", "parent_id", "edited"):
            assert m1[field] == m2[field], (
                f"messages[{mid}][{field!r}] differs: {m1[field]!r} vs {m2[field]!r}"
            )
        assert m1["reactions"] == m2["reactions"], (
            f"messages[{mid}]['reactions'] differs"
        )


def test_base_workspace_current_user_is_u004():
    state = base_workspace()
    assert state.current_user == "U004"


def test_base_workspace_c005_exists_and_u004_not_member():
    state = base_workspace()
    assert "C005" in state.channels, "C005 must exist in base_workspace"
    assert "U004" not in state.channels["C005"]["members"], (
        "U004 must not be a member of C005"
    )


def test_base_workspace_has_thread_replies():
    """At least one message must have a non-None parent_id."""
    state = base_workspace()
    threaded = [m for m in state.messages.values() if m["parent_id"] is not None]
    assert len(threaded) >= 1, "base_workspace must seed at least one thread reply"
