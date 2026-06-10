"""
Stage 4 tests for tasks/baseline_slack.py.

Derived from dev_stages.md Stage 4 test goals and tasks_spec.md Task 2 spec.
Tests assert the contract, not the implementation.
"""
import copy

import pytest

from env.seed import base_workspace
from env.slack import SlackService
from tasks.baseline_slack import ALLOWED_CHANGES, INSTRUCTION, TARGET_ID, seed, verify


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state():
    state = base_workspace()
    seed(state)
    return state


# ---------------------------------------------------------------------------
# Stage-4 goal: seed is deterministic (two builds equal)
# ---------------------------------------------------------------------------


def test_seed_deterministic_messages():
    s1 = _make_state()
    s2 = _make_state()
    assert s1.messages == s2.messages


def test_seed_deterministic_counters():
    s1 = _make_state()
    s2 = _make_state()
    assert s1._counters == s2._counters


# ---------------------------------------------------------------------------
# Stage-4 goal: target is NOT on page 1 of get_channel_messages("C002")
# ---------------------------------------------------------------------------


def test_target_not_on_page_1():
    state = _make_state()
    slack = SlackService(state)
    result = slack.get_channel_messages("C002", limit=10)
    assert result["ok"] is True
    page1_ids = [m["id"] for m in result["messages"]]
    assert TARGET_ID not in page1_ids


def test_page_1_has_next_cursor():
    state = _make_state()
    slack = SlackService(state)
    result = slack.get_channel_messages("C002", limit=10)
    assert result["ok"] is True
    assert result["next_cursor"] is not None


# ---------------------------------------------------------------------------
# Stage-4 goal: scripted correct trajectory scores 1.0
# ---------------------------------------------------------------------------


def test_correct_reaction_scores_1_0():
    state = _make_state()
    state_before = copy.deepcopy(state)
    slack = SlackService(state)
    resp = slack.add_reaction(TARGET_ID, ":eyes:")
    assert resp["ok"] is True, f"add_reaction failed: {resp}"
    score = verify(state_before, state, [])
    assert score == 1.0


# ---------------------------------------------------------------------------
# Stage-4 goal: reacting to a decoy scores 0.0
# ---------------------------------------------------------------------------


def test_reacting_to_decoy_m027_scores_0_0():
    """React to first decoy (M027) instead of target. Both assertions fail."""
    state = _make_state()
    state_before = copy.deepcopy(state)
    slack = SlackService(state)
    resp = slack.add_reaction("M027", ":eyes:")
    assert resp["ok"] is True, f"add_reaction failed: {resp}"
    score = verify(state_before, state, [])
    assert score == 0.0


def test_reacting_to_decoy_m028_scores_0_0():
    """React to second decoy (M028) instead of target. Both assertions fail."""
    state = _make_state()
    state_before = copy.deepcopy(state)
    slack = SlackService(state)
    resp = slack.add_reaction("M028", ":eyes:")
    assert resp["ok"] is True, f"add_reaction failed: {resp}"
    score = verify(state_before, state, [])
    assert score == 0.0


# ---------------------------------------------------------------------------
# Additional spec-derived tests
# ---------------------------------------------------------------------------


def test_search_finds_target():
    """Alternative honest path: search returns TARGET_ID."""
    state = _make_state()
    slack = SlackService(state)
    result = slack.search_messages("database latency")
    assert result["ok"] is True
    ids = [m["id"] for m in result["messages"]]
    assert TARGET_ID in ids


def test_no_action_scores_0_5():
    """No actions: assertion1 False (no :eyes:), assertion2 True (nothing changed). Binary scoring: not all pass -> 0.0."""
    state = _make_state()
    state_before = copy.deepcopy(state)
    score = verify(state_before, state, [])
    assert score == 0.0


def test_wrong_emoji_scores_0_5():
    """React with wrong emoji: assertion1 False, assertion2 True (M026.reactions allowed). Binary scoring: not all pass -> 0.0."""
    state = _make_state()
    state_before = copy.deepcopy(state)
    slack = SlackService(state)
    resp = slack.add_reaction(TARGET_ID, ":thumbsup:")
    assert resp["ok"] is True
    score = verify(state_before, state, [])
    # assertion1 = False (:eyes: absent), assertion2 = True (M026.reactions is allowed field)
    assert score == 0.0


def test_target_id_is_fixed():
    """TARGET_ID must be the literal string 'M026' per tasks_spec.md comment."""
    assert TARGET_ID == "M026"


def test_allowed_changes_restricts_to_target_reactions():
    """ALLOWED_CHANGES must only permit reaction-field modification on TARGET_ID."""
    assert ALLOWED_CHANGES == {"messages": {"modified": {TARGET_ID: {"reactions"}}}}


def test_instruction_is_non_empty_string():
    assert isinstance(INSTRUCTION, str) and len(INSTRUCTION.strip()) > 0


def test_target_reachable_on_page_2():
    """Paginating to page 2 must surface TARGET_ID."""
    state = _make_state()
    slack = SlackService(state)
    page1 = slack.get_channel_messages("C002", limit=10)
    assert page1["ok"] is True
    cursor = page1["next_cursor"]
    assert cursor is not None
    page2 = slack.get_channel_messages("C002", cursor=cursor)
    assert page2["ok"] is True
    page2_ids = [m["id"] for m in page2["messages"]]
    assert TARGET_ID in page2_ids
