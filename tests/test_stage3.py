"""Stage 3 tests: Harbor adapter + trajectory logger + smoke test + isolation.

Expected behavior derived from docs/dev_stages.md (Stage 3), docs/system_design.md
(Environment isolation + Harbor adapter contract + entity schemas), docs/verifier_design.md
(shared-utility signatures + AllowedChanges schema + collateral-damage pattern), and the
smoke_test entry in docs/tasks_spec.md. NOT derived from reading the implementations.
"""
import copy

import pytest

from env.seed import base_workspace
from verifiers.utils import (
    field_equals,
    message_exists_in,
    diff_keys,
    no_collateral_damage,
    any_tool_called_before,
)
from harbor.trajectory import TrajectoryLogger
from harbor.adapter import ApolloHarborTask
from tasks import registry, smoke_test


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The complete Slack/Task tool surface the agent is allowed to see (and nothing else).
EXPECTED_TOOL_NAMES = {
    "list_channels",
    "get_channel_messages",
    "post_message",
    "update_message",
    "add_reaction",
    "create_channel",
    "search_messages",
    "get_thread",
    "list_users",
    "add_member",
    "create_task",
    "get_task",
    "delete_task",
    "list_tasks",
    "update_task",
    "search_tasks",
}

# Substrings that must never appear in any registered tool name (grading machinery).
FORBIDDEN_TOOL_SUBSTRINGS = [
    "verify",
    "score",
    "allowed",
    "expected",
    "ground",
    "truth",
    "snapshot",
    "trajectory",
    "episode",
]


# ---------------------------------------------------------------------------
# Scripted agents
# ---------------------------------------------------------------------------

class ScriptedAgent:
    """Calls a list of (tool_name, args) pairs against the wrapped tools dict.

    Records the tool-name set it was handed so isolation can be checked on the
    actual run-time surface, not just adapter.tools.
    """

    def __init__(self, calls):
        self._calls = calls
        self.received_tool_names = None

    def run(self, tools, instruction):
        self.received_tool_names = set(tools.keys())
        for name, args in self._calls:
            tools[name](**args)


def _correct_smoke_calls():
    return [
        ("list_channels", {}),
        ("post_message", {"channel_id": "C001", "text": "env check ok"}),
    ]


# ---------------------------------------------------------------------------
# Goal 1: registry discovers smoke_test and exposes required symbols
# ---------------------------------------------------------------------------

def test_registry_discovers_smoke_test():
    ids = {getattr(m, "TASK_ID", None) for m in registry.discover_tasks()}
    assert "smoke_test" in ids


def test_registry_list_tasks_includes_smoke_test():
    # The doc does not pin list_tasks() to ids vs modules; accept either shape.
    listed = registry.list_tasks()
    ids = {item if isinstance(item, str) else getattr(item, "TASK_ID", None) for item in listed}
    assert "smoke_test" in ids


def test_registry_get_task_returns_smoke_module():
    mod = registry.get_task("smoke_test")
    assert mod is not None
    assert mod.TASK_ID == "smoke_test"


def test_registry_get_task_unknown_returns_none():
    assert registry.get_task("does_not_exist") is None


def test_smoke_test_exposes_required_symbols():
    for name in ("TASK_ID", "INSTRUCTION", "ALLOWED_CHANGES", "seed", "verify"):
        assert hasattr(smoke_test, name), name
    assert smoke_test.TASK_ID == "smoke_test"
    assert isinstance(smoke_test.INSTRUCTION, str)
    assert callable(smoke_test.seed)
    assert callable(smoke_test.verify)


# ---------------------------------------------------------------------------
# Goal 2: setup() produces an independent deep-copy state_before
# ---------------------------------------------------------------------------

def test_setup_state_before_is_deep_equal_to_live_state():
    task = ApolloHarborTask(smoke_test)
    task.setup()
    assert task.state.messages == task.state_before.messages
    assert task.state.channels == task.state_before.channels
    assert task.state.users == task.state_before.users
    assert task.state.tasks == task.state_before.tasks
    assert task.state.current_user == task.state_before.current_user


def test_setup_state_before_independent_on_message_add():
    task = ApolloHarborTask(smoke_test)
    task.setup()
    before_count = len(task.state_before.messages)
    task.state.messages["M_INJECT"] = {"id": "M_INJECT", "channel": "C001"}
    assert "M_INJECT" not in task.state_before.messages
    assert len(task.state_before.messages) == before_count


def test_setup_state_before_independent_on_current_user_change():
    task = ApolloHarborTask(smoke_test)
    task.setup()
    original = task.state_before.current_user
    task.state.current_user = "U999"
    assert task.state_before.current_user == original


# ---------------------------------------------------------------------------
# Goal 3: scripted correct trajectory scores 1.0
# ---------------------------------------------------------------------------

def test_scripted_correct_trajectory_scores_one():
    task = ApolloHarborTask(smoke_test)
    task.setup()
    task.run(ScriptedAgent(_correct_smoke_calls()))
    assert task.score() == 1.0


# ---------------------------------------------------------------------------
# Goal 4: correct post PLUS stray task creation scores 0.0 (collateral damage)
# ---------------------------------------------------------------------------

def test_scripted_post_plus_stray_task_scores_zero():
    task = ApolloHarborTask(smoke_test)
    task.setup()
    calls = _correct_smoke_calls() + [
        ("create_task", {"title": "stray", "description": "x", "assignee": "U001"}),
    ]
    task.run(ScriptedAgent(calls))
    assert task.score() == 0.0


# ---------------------------------------------------------------------------
# Goal 5: empty trajectory scores 0.0
# ---------------------------------------------------------------------------

def test_empty_trajectory_scores_zero():
    task = ApolloHarborTask(smoke_test)
    task.setup()
    task.run(ScriptedAgent([]))
    assert task.score() == 0.0


# ---------------------------------------------------------------------------
# Goal 6: no_collateral_damage direction (True only-allowed, False non-allowed)
# ---------------------------------------------------------------------------

def _add_message(state, msg_id, channel_id):
    state.messages[msg_id] = {
        "id": msg_id,
        "channel": channel_id,
        "author": state.current_user,
        "text": "env check ok",
        "ts": 9999,
        "parent_id": None,
        "reactions": {},
        "edited": False,
        "original_text": None,
        "edited_by": None,
        "edited_at": None,
    }


def test_no_collateral_damage_true_when_only_allowed_change():
    before = base_workspace()
    after = copy.deepcopy(before)
    _add_message(after, "M_NEW", "C001")
    allowed = {"messages": {"added": {"C001": 1}}}
    assert no_collateral_damage(before, after, allowed) is True


def test_no_collateral_damage_false_when_extra_object_changes():
    before = base_workspace()
    after = copy.deepcopy(before)
    _add_message(after, "M_NEW", "C001")
    # Extra, non-allowed change: create a stray task.
    after.tasks["T_STRAY"] = {
        "id": "T_STRAY",
        "title": "stray",
        "description": "x",
        "assignee": "U001",
        "status": "todo",
        "slack_message_id": None,
        "created_at": 1,
        "updated_at": 1,
    }
    allowed = {"messages": {"added": {"C001": 1}}}
    assert no_collateral_damage(before, after, allowed) is False


def test_no_collateral_damage_false_when_unallowed_channel_added():
    before = base_workspace()
    after = copy.deepcopy(before)
    _add_message(after, "M_NEW", "C001")
    after.channels["C_STRAY"] = {
        "id": "C_STRAY",
        "name": "stray",
        "is_private": False,
        "members": [after.current_user],
    }
    allowed = {"messages": {"added": {"C001": 1}}}
    assert no_collateral_damage(before, after, allowed) is False


# ---------------------------------------------------------------------------
# Goal 7: diff_keys reports a reaction-set change as a messages modification
# ---------------------------------------------------------------------------

def test_diff_keys_reaction_change_is_message_modification():
    before = base_workspace()
    after = copy.deepcopy(before)
    target = next(iter(after.messages))
    after.messages[target]["reactions"].setdefault(":eyes:", set()).add(after.current_user)
    report = diff_keys(before, after)
    modified = report["messages"]["modified"]
    assert (target, {"reactions"}) in modified


def test_diff_keys_no_change_reports_empty():
    before = base_workspace()
    after = copy.deepcopy(before)
    report = diff_keys(before, after)
    for coll in ("users", "channels", "messages", "tasks"):
        assert report[coll]["added"] == []
        assert report[coll]["removed"] == []
        assert report[coll]["modified"] == []


# ---------------------------------------------------------------------------
# Goal 8: ISOLATION (agent reaches only Slack/Task tools, no grading machinery)
# ---------------------------------------------------------------------------

def test_isolation_registered_tool_set_is_exactly_slack_task():
    task = ApolloHarborTask(smoke_test)
    task.setup()
    assert set(task.tools.keys()) == EXPECTED_TOOL_NAMES


def test_isolation_no_tool_name_references_grading_machinery():
    task = ApolloHarborTask(smoke_test)
    task.setup()
    for name in task.tools.keys():
        lower = name.lower()
        for bad in FORBIDDEN_TOOL_SUBSTRINGS:
            assert bad not in lower, f"tool {name} references forbidden substring {bad}"


def test_isolation_workspace_state_exposes_no_grading_attribute():
    task = ApolloHarborTask(smoke_test)
    task.setup()
    state = task.state
    forbidden_exact = {
        "verify",
        "score",
        "allowed_changes",
        "ALLOWED_CHANGES",
        "state_before",
        "trajectory",
        "episode_start",
    }
    public_attrs = {a for a in dir(state) if not a.startswith("_")}
    assert forbidden_exact.isdisjoint(public_attrs)
    forbidden_substrings = (
        "verify",
        "allowed",
        "expected",
        "trajectory",
        "episode",
        "score",
        "snapshot",
    )
    for attr in dir(state):
        lower = attr.lower()
        for bad in forbidden_substrings:
            assert bad not in lower, f"WorkspaceState exposes attr {attr} matching {bad}"


def test_isolation_runtime_agent_tools_are_slack_task_only():
    task = ApolloHarborTask(smoke_test)
    task.setup()
    agent = ScriptedAgent(_correct_smoke_calls())
    task.run(agent)
    assert agent.received_tool_names == EXPECTED_TOOL_NAMES


# ---------------------------------------------------------------------------
# Goal 9: logger captures every tool call including failed ones
# ---------------------------------------------------------------------------

def test_logger_captures_failed_tool_call():
    task = ApolloHarborTask(smoke_test)
    task.setup()
    calls = [
        ("post_message", {"channel_id": "C999", "text": "x"}),
        ("list_channels", {}),
    ]
    task.run(ScriptedAgent(calls))
    traj = task.logger.trajectory
    failed = [c for c in traj if c["tool"] == "post_message" and c["result"].get("ok") is False]
    assert len(failed) == 1
    assert failed[0]["result"].get("error") == "channel_not_found"


def test_logger_records_all_calls_in_order():
    task = ApolloHarborTask(smoke_test)
    task.setup()
    task.run(ScriptedAgent(_correct_smoke_calls()))
    names = [c["tool"] for c in task.logger.trajectory]
    assert names == ["list_channels", "post_message"]


# ---------------------------------------------------------------------------
# Goal 10: any_tool_called_before ordering
# ---------------------------------------------------------------------------

def _call(tool):
    return {"tool": tool, "args": {}, "result": {"ok": True}, "reasoning": None}


def test_any_tool_called_before_true_when_read_precedes_write():
    traj = [_call("list_channels"), _call("post_message")]
    assert any_tool_called_before(traj, "list_channels", "post_message") is True


def test_any_tool_called_before_false_when_reversed():
    traj = [_call("post_message"), _call("list_channels")]
    assert any_tool_called_before(traj, "list_channels", "post_message") is False


def test_any_tool_called_before_false_when_read_missing():
    traj = [_call("post_message")]
    assert any_tool_called_before(traj, "list_channels", "post_message") is False


# ---------------------------------------------------------------------------
# Isolation sanity: instruction names no explicit target ID
# ---------------------------------------------------------------------------

def test_smoke_instruction_names_no_target_id():
    text = smoke_test.INSTRUCTION
    assert "C001" not in text
    import re
    # No bare load-bearing entity id like C001/M007/T003/U004 in the prompt.
    assert re.search(r"\b[CMTU]\d{3}\b", text) is None


# ---------------------------------------------------------------------------
# Supporting checks for the shared utilities used by the smoke verifier
# ---------------------------------------------------------------------------

def test_message_exists_in_matches_posted_message():
    task = ApolloHarborTask(smoke_test)
    task.setup()
    episode_start = task.state_before.clock.current()
    task.run(ScriptedAgent(_correct_smoke_calls()))
    assert message_exists_in(
        task.state,
        "C001",
        author=task.state.current_user,
        after_ts=episode_start,
        text_contains="env check ok",
    ) is True


def test_field_equals_reads_nested_path():
    state = base_workspace()
    assert field_equals(state, ("current_user",), state.current_user) is True
    assert field_equals(state, ("current_user",), "U_NOPE") is False
