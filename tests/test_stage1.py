"""Stage 1 tests: clock, ids, state, grep."""
import copy
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from env.clock import FrozenClock
from env.ids import next_id
from env.state import WorkspaceState, Message, User, Channel, Task


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_state(**counter_overrides) -> WorkspaceState:
    counters = {"M": 0, "C": 0, "T": 0, "U": 0}
    counters.update(counter_overrides)
    return WorkspaceState(
        users={},
        channels={},
        messages={},
        tasks={},
        clock=FrozenClock(),
        current_user="U004",
        counters=counters,
    )


ENV_DIR = Path(__file__).parent.parent / "env"
BASE_ISO = "2026-06-09T10:00:00"
TICK_SECONDS = 60


# ---------------------------------------------------------------------------
# 1. FrozenClock.next() returns strictly increasing ints starting at 1
# ---------------------------------------------------------------------------

def test_frozen_clock_next_starts_at_1():
    clock = FrozenClock()
    assert clock.next() == 1


def test_frozen_clock_next_strictly_increasing():
    clock = FrozenClock()
    results = [clock.next() for _ in range(5)]
    assert results == [1, 2, 3, 4, 5]


def test_frozen_clock_current_does_not_increment():
    clock = FrozenClock()
    clock.next()
    clock.next()
    before = clock.current()
    clock.current()
    clock.current()
    after = clock.current()
    assert before == after == 2, "current() must not increment the tick"


# ---------------------------------------------------------------------------
# 2. to_datetime accuracy
# ---------------------------------------------------------------------------

def test_to_datetime_tick_0_equals_base():
    clock = FrozenClock()
    assert clock.to_datetime(0) == BASE_ISO


def test_to_datetime_tick_2_equals_base_plus_2_minutes():
    clock = FrozenClock()
    expected = (
        datetime.fromisoformat(BASE_ISO) + timedelta(seconds=2 * TICK_SECONDS)
    ).isoformat()
    assert clock.to_datetime(2) == expected


# ---------------------------------------------------------------------------
# 3. next_id with _counters["M"] == 4 returns "M005" and bumps counter to 5
# ---------------------------------------------------------------------------

def test_next_id_returns_correct_formatted_id():
    state = make_state(M=4)
    result = next_id(state, "M")
    assert result == "M005"


def test_next_id_bumps_counter_to_5():
    state = make_state(M=4)
    next_id(state, "M")
    assert state._counters["M"] == 5


# ---------------------------------------------------------------------------
# 4. WorkspaceState deep-copy isolation
# ---------------------------------------------------------------------------

def _make_message(msg_id: str) -> Message:
    return Message(
        id=msg_id,
        channel="C001",
        author="U001",
        text="hello",
        ts=1,
        parent_id=None,
        reactions={},
        edited=False,
        original_text=None,
        edited_by=None,
        edited_at=None,
    )


def test_deepcopy_adding_to_copy_does_not_affect_original():
    state = make_state()
    state.messages["M001"] = _make_message("M001")
    copy_state = copy.deepcopy(state)
    copy_state.messages["M002"] = _make_message("M002")
    assert "M002" not in state.messages, (
        "Adding a key to the copy's messages must not affect the original"
    )


def test_deepcopy_mutating_copy_field_does_not_affect_original():
    state = make_state()
    state.messages["M001"] = _make_message("M001")
    copy_state = copy.deepcopy(state)
    copy_state.messages["M001"]["text"] = "changed"
    assert state.messages["M001"]["text"] == "hello", (
        "Mutating a field in the copy must not affect the original"
    )


# ---------------------------------------------------------------------------
# 5. Grep: no forbidden patterns in env/
# ---------------------------------------------------------------------------

FORBIDDEN_PATTERNS = [
    "datetime.now(",
    "import random",
    "import uuid",
]


@pytest.mark.parametrize("pattern", FORBIDDEN_PATTERNS)
def test_no_forbidden_pattern_in_env(pattern):
    py_files = list(ENV_DIR.rglob("*.py"))
    assert py_files, f"No .py files found in {ENV_DIR}"
    matches = []
    for f in py_files:
        text = f.read_text()
        for lineno, line in enumerate(text.splitlines(), 1):
            if pattern in line:
                matches.append(
                    f"{f.relative_to(ENV_DIR.parent)}:{lineno}: {line.rstrip()}"
                )
    assert not matches, (
        f"Forbidden pattern {pattern!r} found in env/:\n" + "\n".join(matches)
    )
