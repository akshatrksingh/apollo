"""Integrity tests for env/seed_rich.py :: rich_workspace()."""
import copy

from env.seed_rich import rich_workspace


def _build() -> object:
    return rich_workspace()


def test_rich_workspace_deterministic():
    s1 = _build()
    s2 = _build()
    assert s1.users == s2.users
    assert s1.channels == s2.channels
    assert s1.messages == s2.messages
    assert s1.tasks == s2.tasks
    assert s1._counters == s2._counters


def test_rich_workspace_no_duplicate_message_ids():
    s = _build()
    ids = list(s.messages.keys())
    assert len(ids) == len(set(ids))


def test_rich_workspace_no_duplicate_task_ids():
    s = _build()
    ids = list(s.tasks.keys())
    assert len(ids) == len(set(ids))


def test_rich_workspace_slack_message_ids_resolve():
    s = _build()
    for task_id, task in s.tasks.items():
        mid = task["slack_message_id"]
        if mid is not None:
            assert mid in s.messages, f"Task {task_id} links to unknown message {mid}"


def test_rich_workspace_task_assignees_resolve():
    s = _build()
    for task_id, task in s.tasks.items():
        uid = task["assignee"]
        assert uid in s.users, f"Task {task_id} has unknown assignee {uid}"


def test_rich_workspace_message_authors_resolve():
    s = _build()
    for mid, msg in s.messages.items():
        assert msg["author"] in s.users, f"Message {mid} has unknown author {msg['author']}"


def test_rich_workspace_message_channels_resolve():
    s = _build()
    for mid, msg in s.messages.items():
        assert msg["channel"] in s.channels, f"Message {mid} references unknown channel {msg['channel']}"


def test_rich_workspace_parent_ids_resolve():
    s = _build()
    for mid, msg in s.messages.items():
        pid = msg["parent_id"]
        if pid is not None:
            assert pid in s.messages, f"Message {mid} has dangling parent_id {pid}"


def test_rich_workspace_message_counter_matches():
    s = _build()
    max_num = max(int(k[1:]) for k in s.messages)
    assert s._counters["M"] == max_num, f"Counter M={s._counters['M']} but max message is M{max_num:03d}"


def test_rich_workspace_task_counter_matches():
    s = _build()
    max_num = max(int(k[1:]) for k in s.tasks)
    assert s._counters["T"] == max_num, f"Counter T={s._counters['T']} but max task is T{max_num:03d}"


def test_rich_workspace_user_counter_matches():
    s = _build()
    max_num = max(int(k[1:]) for k in s.users)
    assert s._counters["U"] == max_num


def test_rich_workspace_channel_counter_matches():
    s = _build()
    max_num = max(int(k[1:]) for k in s.channels)
    assert s._counters["C"] == max_num


def test_agent_is_in_expected_channels():
    s = _build()
    # U004 (agent) must be a member of these visible channels
    for cid in ("C001", "C002", "C003", "C004", "C006", "C007"):
        assert "U004" in s.channels[cid]["members"], f"Agent not in {cid}"


def test_agent_not_in_private_excluded_channels():
    s = _build()
    # U004 must NOT be a member of leadership or design
    for cid in ("C005", "C008"):
        assert "U004" not in s.channels[cid]["members"], f"Agent unexpectedly in {cid}"


def test_rich_workspace_size():
    s = _build()
    assert len(s.users) >= 10
    assert len(s.channels) >= 7
    assert len(s.messages) >= 60
    assert len(s.tasks) >= 12


def test_near_duplicate_users_present():
    s = _build()
    names = {u["name"] for u in s.users.values()}
    assert "Bob Kim" in names and "Bob Park" in names
    assert "Maya Patel" in names and "Maya Rao" in names


def test_reactions_reference_valid_users():
    s = _build()
    for mid, msg in s.messages.items():
        for emoji, reactors in msg["reactions"].items():
            for uid in reactors:
                assert uid in s.users, f"Message {mid} reaction '{emoji}' from unknown user {uid}"
