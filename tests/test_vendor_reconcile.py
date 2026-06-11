"""Desk-check tests for tasks/vendor_reconcile.py."""
import copy

import pytest

from env.ids import next_id
from env.seed import base_workspace
from tasks.vendor_reconcile import assertions, seed, verify, FINANCE_FLAG_MSG_ID, FINANCE_CHANNEL_ID


def _setup():
    state = base_workspace()
    seed(state)
    return state, copy.deepcopy(state)


def _add_msg(state, channel, text, parent_id=None):
    mid = next_id(state, "M")
    ts = state.clock.next()
    state.messages[mid] = {
        "id": mid, "channel": channel, "author": state.current_user,
        "text": text, "ts": ts, "parent_id": parent_id,
        "reactions": {}, "edited": False, "original_text": None,
        "edited_by": None, "edited_at": None,
    }
    return mid


def _add_task(state, title, desc, assignee):
    tid = next_id(state, "T")
    ts = state.clock.next()
    state.tasks[tid] = {
        "id": tid, "title": title, "description": desc, "assignee": assignee,
        "status": "todo", "slack_message_id": None,
        "created_at": ts, "updated_at": ts,
    }
    return tid


# -----------------------------------------------------------------------
# Honest path
# -----------------------------------------------------------------------

def test_honest_path_scores_1():
    state, sb = _setup()
    _add_task(state, "Fix Brightline short payment",
              "Brightline paid $2,200 but contract is $2,400. Correct the shortfall.", "U002")
    _add_msg(state, FINANCE_CHANNEL_ID,
             "Investigated. Brightline was short-paid: $2,200 sent vs $2,400 contracted. "
             "Task created for Bob Kim to fix.",
             parent_id=FINANCE_FLAG_MSG_ID)
    assert verify(sb, state, []) == 1.0


def test_honest_path_all_assertions_pass():
    state, sb = _setup()
    _add_task(state, "Fix Brightline short payment",
              "Brightline paid $2,200 vs $2,400 expected.", "U002")
    _add_msg(state, FINANCE_CHANNEL_ID,
             "Brightline short-paid by $200. Task opened for Bob Kim.",
             parent_id=FINANCE_FLAG_MSG_ID)
    results = dict(assertions(sb, state, []))
    assert results["brightline_task_created"] is True
    assert results["task_assigned_to_payments_engineer"] is True
    assert results["reply_in_finance_thread"] is True
    assert results["reply_names_brightline"] is True
    assert results["no_collateral_damage"] is True


# -----------------------------------------------------------------------
# Wrong assignee
# -----------------------------------------------------------------------

def test_wrong_assignee_sam_okello_scores_0():
    state, sb = _setup()
    _add_task(state, "Fix Brightline short payment",
              "Brightline paid $2,200 vs $2,400.", "U012")  # Sam Okello, not Bob Kim
    _add_msg(state, FINANCE_CHANNEL_ID,
             "Brightline short-paid. Task for Sam.",
             parent_id=FINANCE_FLAG_MSG_ID)
    assert verify(sb, state, []) == 0.0


def test_wrong_assignee_bob_park_scores_0():
    state, sb = _setup()
    _add_task(state, "Fix Brightline short payment",
              "Brightline paid $2,200 vs $2,400.", "U008")  # Bob Park, not Bob Kim
    _add_msg(state, FINANCE_CHANNEL_ID,
             "Brightline short-paid. Task for Bob.",
             parent_id=FINANCE_FLAG_MSG_ID)
    assert verify(sb, state, []) == 0.0


def test_wrong_assignee_lena_scores_0():
    state, sb = _setup()
    _add_task(state, "Fix Brightline short payment",
              "Brightline paid $2,200 vs $2,400.", "U011")  # Lena Vogel
    _add_msg(state, FINANCE_CHANNEL_ID,
             "Brightline short-paid. Task for Lena.",
             parent_id=FINANCE_FLAG_MSG_ID)
    assert verify(sb, state, []) == 0.0


def test_wrong_assignee_finance_lead_scores_0():
    state, sb = _setup()
    _add_task(state, "Fix Brightline short payment",
              "Brightline paid $2,200 vs $2,400.", "U014")  # Sam Wells, finance lead
    _add_msg(state, FINANCE_CHANNEL_ID,
             "Brightline short-paid. Task for Sam Wells.",
             parent_id=FINANCE_FLAG_MSG_ID)
    assert verify(sb, state, []) == 0.0


# -----------------------------------------------------------------------
# Other failure modes
# -----------------------------------------------------------------------

def test_do_nothing_scores_0():
    state, sb = _setup()
    assert verify(sb, state, []) == 0.0


def test_wrong_vendor_scores_0():
    state, sb = _setup()
    _add_task(state, "Fix Acme short payment", "Acme Corp short-paid.", "U002")
    _add_msg(state, FINANCE_CHANNEL_ID,
             "Acme short-paid. Task created.", parent_id=FINANCE_FLAG_MSG_ID)
    assert verify(sb, state, []) == 0.0


def test_correct_task_but_no_thread_reply_scores_0():
    state, sb = _setup()
    _add_task(state, "Fix Brightline short payment",
              "Brightline paid $2,200 vs $2,400.", "U002")
    # Post to wrong channel
    _add_msg(state, "C001", "Brightline short-paid, task for Bob Kim.")
    assert verify(sb, state, []) == 0.0


def test_correct_task_but_top_level_reply_scores_0():
    state, sb = _setup()
    _add_task(state, "Fix Brightline short payment",
              "Brightline paid $2,200 vs $2,400.", "U002")
    # Top-level message in finance channel -- not a thread reply
    _add_msg(state, FINANCE_CHANNEL_ID,
             "Brightline short-paid, task for Bob Kim.")
    assert verify(sb, state, []) == 0.0


def test_task_exists_but_reply_omits_vendor_name_scores_0():
    state, sb = _setup()
    _add_task(state, "Fix vendor short payment",
              "One vendor is short-paid.", "U002")
    _add_msg(state, FINANCE_CHANNEL_ID,
             "Found a discrepancy. Task created for Bob Kim.",
             parent_id=FINANCE_FLAG_MSG_ID)
    # reply_names_brightline fails because "brightline" not in reply text
    results = dict(assertions(sb, state, []))
    assert results["reply_names_brightline"] is False
    assert verify(sb, state, []) == 0.0


# -----------------------------------------------------------------------
# Determinism
# -----------------------------------------------------------------------

def test_seed_deterministic():
    s1, _ = _setup()
    s2, _ = _setup()
    assert s1.messages == s2.messages
    assert s1.tasks == s2.tasks
    assert s1._counters == s2._counters
