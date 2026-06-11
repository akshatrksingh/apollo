"""Stage 6 tests: trial runner, aggregate report, transcripts, observability.

Derived from docs/dev_stages.md Stage 6 test goals and
docs/verifier_design.md (per-assertion shape; tasks use binary scoring from Stage 6 Step 2).

No real network, no real OPENROUTER_* env reads: a scripted fake client drives
every trial. The runner is built for dependency injection.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from harbor.adapter import ApolloHarborTask
from runner.run_trials import (
    AgentMessage,
    ToolCallRequest,
    TransientApiError,
    run_one_trial,
    run_sweep,
    run_trials,
)
from tasks.registry import get_task

MODEL = "test/model"
PRIMARY = "vendor_reconcile"


class ScriptedClient:
    """Returns pre-programmed AgentMessages per complete() call.

    After exhausting the script, returns permanent empty AgentMessage (no tool
    calls). This is critical: the nudge logic fires one retry on empty tool
    calls; if the retry also returns empty, the trial ends. A replay-from-0
    would cause an infinite loop with the nudge mechanic.

    Use for single-trial tests (run_one_trial) only. For multi-trial tests
    (run_trials), use CyclingClient instead.
    """

    def __init__(self, messages: list[AgentMessage]) -> None:
        self._messages = list(messages)
        self._i = 0

    def complete(self, messages, tools) -> AgentMessage:
        if self._i >= len(self._messages):
            return AgentMessage(tool_calls=[])
        msg = self._messages[self._i]
        self._i += 1
        return msg


class MultiTrialClient:
    """Pre-loads N copies of a script for use with run_trials.

    run_trials calls one client instance across all N trials. This client
    holds N repetitions of the given scripted sequence. Each trial sequence
    must end with TWO consecutive _stop() messages: the first triggers the
    nudge, and the second (nudge retry) causes the trial to end cleanly.

    The input `messages` must end with exactly one _stop(). MultiTrialClient
    automatically appends a second _stop() to each repetition so each trial
    ends with two consecutive empty-tool_calls messages.

    After the full tape is exhausted, returns permanent empty.
    """

    def __init__(self, messages: list[AgentMessage], trials: int) -> None:
        assert messages, "script must be non-empty"
        assert not messages[-1].tool_calls, "last message must be _stop()"
        # Each trial needs TWO consecutive stops: first triggers nudge, second
        # ends the trial. Append an extra stop to each trial's sequence.
        one_trial = list(messages) + [_stop()]
        self._tape = one_trial * trials
        self._i = 0

    def complete(self, messages, tools) -> AgentMessage:
        if self._i >= len(self._tape):
            return AgentMessage(tool_calls=[])
        msg = self._tape[self._i]
        self._i += 1
        return msg


def _call(idx: int, name: str, args: dict) -> AgentMessage:
    return AgentMessage(
        tool_calls=[ToolCallRequest(id=f"c{idx}", name=name, arguments=args)]
    )


def _stop() -> AgentMessage:
    return AgentMessage(tool_calls=[])


_HONEST_SCRIPT = [
    _call(0, "get_channel_messages", {"channel_id": "C009"}),
    _call(1, "get_channel_messages", {"channel_id": "C004"}),
    _call(
        2,
        "create_task",
        {
            "title": "Fix Brightline short payment",
            "description": "Brightline was paid $2,200 vs contracted $2,400. Needs correction.",
            "assignee": "U002",
            "status": "todo",
        },
    ),
    _call(
        3,
        "post_message",
        {
            "channel_id": "C009",
            "text": "Confirmed: Brightline was short-paid ($2,200 vs $2,400 contracted). Fix task opened and assigned to the payments engineer.",
            "parent_id": "M064",
        },
    ),
    _stop(),
]

# Lazy: creates a Brightline task but assigns to wrong person (U001 instead of U002),
# still posts a valid reply. A2 fails -> score 0.0, but writes succeed -> partial_writes.
_LAZY_SCRIPT = [
    _call(
        0,
        "create_task",
        {
            "title": "Fix Brightline short payment",
            "description": "Brightline was short-paid.",
            "assignee": "U001",
            "status": "todo",
        },
    ),
    _call(
        1,
        "post_message",
        {
            "channel_id": "C009",
            "text": "Brightline payment issue -- task created.",
            "parent_id": "M064",
        },
    ),
    _stop(),
]


def honest_primary_client() -> ScriptedClient:
    # Honest cross-service sequence scoring 1.0: discover, then act on the
    # load-bearing ids T003 / M007 / C002. These ids live only inside the
    # scripted harness agent, never in any prompt.
    # For single-trial use (run_one_trial). For run_trials, use
    # honest_primary_multi(n).
    return ScriptedClient(_HONEST_SCRIPT)


def honest_primary_multi(trials: int) -> MultiTrialClient:
    """Honest sequence repeated for N trials. Use with run_trials."""
    return MultiTrialClient(_HONEST_SCRIPT, trials)


def lazy_primary_client() -> ScriptedClient:
    # Lazy desk-check sequence: guesses wrong task T001 and posts to C002.
    # Under binary scoring this scores 0.0 (a1 and a3 fail).
    # For single-trial use (run_one_trial). For run_trials, use
    # lazy_primary_multi(n).
    return ScriptedClient(_LAZY_SCRIPT)


def lazy_primary_multi(trials: int) -> MultiTrialClient:
    """Lazy sequence repeated for N trials. Use with run_trials."""
    return MultiTrialClient(_LAZY_SCRIPT, trials)


# ---------------------------------------------------------------------------
# Goal 1: per-trial structured record
# ---------------------------------------------------------------------------


def test_run_one_trial_honest_record_score_is_one():
    record = run_one_trial(
        get_task(PRIMARY),
        MODEL,
        honest_primary_client(),
        max_turns=10,
    )
    assert record["score"] == 1.0


def test_run_one_trial_honest_all_three_assertions_true():
    module = get_task(PRIMARY)
    record = run_one_trial(module, MODEL, honest_primary_client(), max_turns=10)
    by_name = {a["name"]: a["passed"] for a in record["assertions"]}
    assert by_name == {
        "brightline_task_created": True,
        "task_assigned_to_payments_engineer": True,
        "reply_in_finance_thread": True,
        "reply_names_brightline": True,
        "no_collateral_damage": True,
    }


def test_run_one_trial_tool_calls_preserve_scripted_order():
    module = get_task(PRIMARY)
    record = run_one_trial(module, MODEL, honest_primary_client(), max_turns=10)
    names = [c["tool"] for c in record["tool_calls"]]
    assert names == [
        "get_channel_messages",
        "get_channel_messages",
        "create_task",
        "post_message",
    ]


def test_run_one_trial_tool_calls_have_tool_args_result():
    module = get_task(PRIMARY)
    record = run_one_trial(module, MODEL, honest_primary_client(), max_turns=10)
    for call in record["tool_calls"]:
        assert {"tool", "args", "result"}.issubset(call.keys())
    create = next(c for c in record["tool_calls"] if c["tool"] == "create_task")
    assert create["args"]["assignee"] == "U002"
    assert create["result"]["ok"] is True


def test_run_one_trial_record_has_numeric_score_and_assertion_names():
    module = get_task(PRIMARY)
    record = run_one_trial(module, MODEL, honest_primary_client(), max_turns=10)
    assert isinstance(record["score"], (int, float))
    names = {a["name"] for a in record["assertions"]}
    assert names == {
        "brightline_task_created",
        "task_assigned_to_payments_engineer",
        "reply_in_finance_thread",
        "reply_names_brightline",
        "no_collateral_damage",
    }


def test_run_one_trial_lazy_record_score_is_one_third():
    module = get_task(PRIMARY)
    record = run_one_trial(module, MODEL, lazy_primary_client(), max_turns=10)
    assert record["score"] == 0.0


def test_run_one_trial_lazy_identifies_failing_assertions():
    module = get_task(PRIMARY)
    record = run_one_trial(module, MODEL, lazy_primary_client(), max_turns=10)
    by_name = {a["name"]: a["passed"] for a in record["assertions"]}
    assert by_name == {
        "brightline_task_created": True,
        "task_assigned_to_payments_engineer": False,
        "reply_in_finance_thread": True,
        "reply_names_brightline": True,
        "no_collateral_damage": True,
    }


# ---------------------------------------------------------------------------
# Goal 2: aggregate report (distribution, mean, success_rate, pass_at_k)
# ---------------------------------------------------------------------------


def test_run_trials_all_honest_pass_k_is_one(tmp_path):
    agg = run_trials(
        PRIMARY,
        trials=3,
        model=MODEL,
        client=honest_primary_multi(3),
        max_turns=10,
        transcripts_dir=str(tmp_path),
    )
    assert agg["success_rate"] == 1.0


def test_run_trials_all_honest_distribution_and_mean(tmp_path):
    agg = run_trials(
        PRIMARY,
        trials=3,
        model=MODEL,
        client=honest_primary_multi(3),
        max_turns=10,
        transcripts_dir=str(tmp_path),
    )
    assert agg["distribution"] == {1.0: 3}
    assert agg["mean"] == pytest.approx(1.0)
    assert agg["trials"] == 3
    assert agg["success_rate"] == pytest.approx(1.0)
    assert agg["pass_at_k"] == pytest.approx(1.0)


def test_run_trials_all_honest_per_assertion_rates_all_one(tmp_path):
    agg = run_trials(
        PRIMARY,
        trials=3,
        model=MODEL,
        client=honest_primary_multi(3),
        max_turns=10,
        transcripts_dir=str(tmp_path),
    )
    rates = agg["per_assertion_pass_rate"]
    assert rates["brightline_task_created"] == 1.0
    assert rates["task_assigned_to_payments_engineer"] == 1.0
    assert rates["no_collateral_damage"] == 1.0


def test_run_trials_all_lazy_pass_k_is_zero(tmp_path):
    agg = run_trials(
        PRIMARY,
        trials=3,
        model=MODEL,
        client=lazy_primary_multi(3),
        max_turns=10,
        transcripts_dir=str(tmp_path),
    )
    assert agg["success_rate"] == 0.0
    assert agg["success_rate"] < 1.0


def test_run_trials_all_lazy_distribution_is_third(tmp_path):
    agg = run_trials(
        PRIMARY,
        trials=3,
        model=MODEL,
        client=lazy_primary_multi(3),
        max_turns=10,
        transcripts_dir=str(tmp_path),
    )
    assert agg["distribution"] == {0.0: 3}
    assert sum(agg["distribution"].values()) == 3


def test_run_trials_all_lazy_per_assertion_rates_pinpoint_failure(tmp_path):
    agg = run_trials(
        PRIMARY,
        trials=3,
        model=MODEL,
        client=lazy_primary_multi(3),
        max_turns=10,
        transcripts_dir=str(tmp_path),
    )
    rates = agg["per_assertion_pass_rate"]
    assert rates["task_assigned_to_payments_engineer"] == 0.0
    assert rates["brightline_task_created"] == 1.0
    assert rates["reply_in_finance_thread"] == 1.0


def test_run_trials_per_assertion_rate_values_are_fractions(tmp_path):
    agg = run_trials(
        PRIMARY,
        trials=3,
        model=MODEL,
        client=lazy_primary_multi(3),
        max_turns=10,
        transcripts_dir=str(tmp_path),
    )
    for rate in agg["per_assertion_pass_rate"].values():
        assert 0.0 <= rate <= 1.0


# ---------------------------------------------------------------------------
# Goal 3: forced-failure stub
# ---------------------------------------------------------------------------


def _make_stub_module():
    """A minimal task module with a deterministic failing assertion.

    seed is a no-op so the base workspace is used unchanged. assertions always
    returns one failing and one passing pair; verify derives its float from it.
    """

    def seed(state):
        return None

    def assertions(state_before, state_after, trajectory):
        return [("always_fails", False), ("always_passes", True)]

    def verify(state_before, state_after, trajectory):
        pairs = assertions(state_before, state_after, trajectory)
        return sum(1.0 for _, p in pairs if p) / len(pairs)

    return SimpleNamespace(
        TASK_ID="forced_failure_stub",
        INSTRUCTION="stub instruction",
        ALLOWED_CHANGES={},
        seed=seed,
        verify=verify,
        assertions=assertions,
    )


def test_stub_record_identifies_failing_assertion():
    stub = _make_stub_module()
    record = run_one_trial(stub, MODEL, ScriptedClient([_stop()]), max_turns=5)
    by_name = {a["name"]: a["passed"] for a in record["assertions"]}
    assert by_name["always_fails"] is False
    assert by_name["always_passes"] is True


def test_stub_score_reflects_one_passing_of_two():
    stub = _make_stub_module()
    record = run_one_trial(stub, MODEL, ScriptedClient([_stop()]), max_turns=5)
    assert record["score"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Goal 4: transcript file
# ---------------------------------------------------------------------------


def test_transcript_files_match_name_pattern(tmp_path):
    run_trials(
        PRIMARY,
        trials=2,
        model=MODEL,
        client=honest_primary_multi(2),
        max_turns=10,
        transcripts_dir=str(tmp_path),
    )
    run_dirs = [p for p in Path(tmp_path).iterdir() if p.is_dir()]
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    # Folder carries task/model/timestamp; "/" sanitized to "_".
    assert run_dir.name.startswith(f"{PRIMARY}_test_model_")
    assert "/" not in run_dir.name
    assert "test/model" not in run_dir.name
    # Exactly `trials` files named trial_01.json .. trial_NN.json (zero-padded).
    files = sorted(f for f in run_dir.iterdir() if f.suffix == ".json" and f.stem.startswith("trial_"))
    assert [f.name for f in files] == ["trial_01.json", "trial_02.json"]


def test_transcript_contents_have_instruction_calls_score_assertions(tmp_path):
    run_trials(
        PRIMARY,
        trials=1,
        model=MODEL,
        client=honest_primary_multi(1),
        max_turns=10,
        transcripts_dir=str(tmp_path),
    )
    run_dirs = [p for p in Path(tmp_path).iterdir() if p.is_dir()]
    assert len(run_dirs) == 1
    files = sorted(f for f in run_dirs[0].iterdir() if f.suffix == ".json" and f.stem.startswith("trial_"))
    assert [f.name for f in files] == ["trial_01.json"]
    data = json.loads(files[0].read_text())
    assert isinstance(data["instruction"], str) and data["instruction"]
    assert isinstance(data["score"], (int, float))
    # ordered tool calls each with args and result
    names = [c["tool"] for c in data["tool_calls"]]
    assert names == [
        "get_channel_messages",
        "get_channel_messages",
        "create_task",
        "post_message",
    ]
    for c in data["tool_calls"]:
        assert "args" in c and "result" in c
    # exactly 5 per-assertion booleans for vendor_reconcile
    assert len(data["assertions"]) == 5
    for a in data["assertions"]:
        assert isinstance(a["passed"], bool)


def test_transcript_serializes_sets_as_lists_not_python_sets(tmp_path):
    run_trials(
        PRIMARY,
        trials=1,
        model=MODEL,
        client=honest_primary_multi(1),
        max_turns=10,
        transcripts_dir=str(tmp_path),
    )
    run_dirs = [p for p in Path(tmp_path).iterdir() if p.is_dir()]
    assert len(run_dirs) == 1
    files = sorted(f for f in run_dirs[0].iterdir() if f.suffix == ".json" and f.stem.startswith("trial_"))
    assert [f.name for f in files] == ["trial_01.json"]
    raw = files[0].read_text()
    # JSON cannot encode a Python set; valid load proves no set survived.
    data = json.loads(raw)

    def _check(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "reactions":
                    assert isinstance(v, (list, dict))
                _check(v)
        elif isinstance(node, list):
            for v in node:
                _check(v)

    _check(data)


# ---------------------------------------------------------------------------
# Goal 5: offline / no-hang
# ---------------------------------------------------------------------------


def test_empty_client_completes_with_empty_trajectory_and_numeric_score():
    module = get_task(PRIMARY)
    record = run_one_trial(module, MODEL, ScriptedClient([_stop()]), max_turns=10)
    assert record["tool_calls"] == []
    assert isinstance(record["score"], (int, float))


def test_tool_result_not_ok_does_not_raise():
    module = get_task(PRIMARY)
    # update_task on a nonexistent task returns {"ok": False, ...}; must not raise.
    client = ScriptedClient(
        [
            _call(0, "update_task", {"task_id": "T999", "status": "in_progress"}),
            _stop(),
        ]
    )
    record = run_one_trial(module, MODEL, client, max_turns=10)
    assert "error" not in record
    failed = record["tool_calls"][0]
    assert failed["result"]["ok"] is False


# ---------------------------------------------------------------------------
# Nudge behavior tests
# ---------------------------------------------------------------------------


def test_nudge_fires_once_then_no_tool_call_stop_reason():
    # Client exhausts immediately; nudge retry also returns empty.
    # Trial must end with stop_reason == "no_tool_call" and tool_call_count == 0.
    module = get_task(PRIMARY)
    record = run_one_trial(module, MODEL, ScriptedClient([_stop()]), max_turns=10)
    assert record["stop_reason"] == "no_tool_call"
    assert record["tool_call_count"] == 0


def test_nudge_fires_once_then_succeeds():
    # First call returns empty (nudge fires), second call (nudge retry) returns a
    # tool call, third call (turn 2) stops. Trial completes with natural_stop and
    # exactly 1 tool call in the trajectory.
    module = get_task(PRIMARY)
    client = ScriptedClient(
        [
            _stop(),
            _call(0, "list_channels", {}),
            _stop(),
        ]
    )
    record = run_one_trial(module, MODEL, client, max_turns=10)
    assert record["stop_reason"] == "natural_stop"
    assert record["tool_call_count"] == 1


def test_nudge_count_exactly_two_model_calls_on_empty_trial():
    # For a client that always returns empty: exactly 2 complete() calls occur
    # (initial call + one nudge retry), then the trial ends.
    module = get_task(PRIMARY)

    class CountingClient:
        def __init__(self):
            self.calls = 0

        def complete(self, messages, tools) -> AgentMessage:
            self.calls += 1
            return AgentMessage(tool_calls=[])

    counting = CountingClient()
    run_one_trial(module, MODEL, counting, max_turns=10)
    assert counting.calls == 2


# ---------------------------------------------------------------------------
# Backoff retry tests (time.sleep patched to avoid real delays)
# ---------------------------------------------------------------------------


def test_transient_error_retries_then_succeeds():
    # Client raises TransientApiError on the first complete(), succeeds on retry.
    # Trial must complete normally (stop_reason != "api_error").
    module = get_task(PRIMARY)

    class TransientThenSucceed:
        def __init__(self):
            self._attempt = 0

        def complete(self, messages, tools) -> AgentMessage:
            self._attempt += 1
            if self._attempt == 1:
                raise TransientApiError("simulated transient failure")
            # Return a stop message on all subsequent calls.
            return _stop()

    with patch("runner.run_trials.time.sleep"):
        record = run_one_trial(module, MODEL, TransientThenSucceed(), max_turns=10)
    assert record["stop_reason"] != "api_error"


def test_transient_error_all_retries_fail_records_api_error():
    # Client always raises TransientApiError. After exhausting retries, the trial
    # records stop_reason == "api_error".
    module = get_task(PRIMARY)

    class AlwaysTransient:
        def complete(self, messages, tools) -> AgentMessage:
            raise TransientApiError("always fails")

    with patch("runner.run_trials.time.sleep"):
        record = run_one_trial(module, MODEL, AlwaysTransient(), max_turns=10)
    assert record["stop_reason"] == "api_error"


# ---------------------------------------------------------------------------
# Observability field tests
# ---------------------------------------------------------------------------


def test_trial_record_has_stop_reason():
    module = get_task(PRIMARY)
    record = run_one_trial(module, MODEL, honest_primary_client(), max_turns=10)
    assert "stop_reason" in record
    assert isinstance(record["stop_reason"], str)


def test_trial_record_has_failure_stage():
    module = get_task(PRIMARY)
    record = run_one_trial(module, MODEL, honest_primary_client(), max_turns=10)
    assert "failure_stage" in record
    assert isinstance(record["failure_stage"], str)


def test_trial_record_has_raw_responses():
    module = get_task(PRIMARY)
    record = run_one_trial(module, MODEL, honest_primary_client(), max_turns=10)
    assert "raw_responses" in record
    assert isinstance(record["raw_responses"], list)


def test_trial_record_has_tool_call_count():
    module = get_task(PRIMARY)
    record = run_one_trial(module, MODEL, honest_primary_client(), max_turns=10)
    assert record["tool_call_count"] == len(record["tool_calls"])


def test_tool_calls_have_ok_flag():
    module = get_task(PRIMARY)
    record = run_one_trial(module, MODEL, honest_primary_client(), max_turns=10)
    for tc in record["tool_calls"]:
        assert "ok" in tc
        assert isinstance(tc["ok"], bool)
    create = next(c for c in record["tool_calls"] if c["tool"] == "create_task")
    assert create["ok"] is True


def test_empty_trajectory_stop_reason_is_no_tool_call():
    module = get_task(PRIMARY)
    record = run_one_trial(module, MODEL, ScriptedClient([_stop()]), max_turns=10)
    assert record["stop_reason"] == "no_tool_call"


def test_empty_trajectory_failure_stage_is_no_tool_call():
    module = get_task(PRIMARY)
    record = run_one_trial(module, MODEL, ScriptedClient([_stop()]), max_turns=10)
    assert record["failure_stage"] == "no_tool_call"


def test_honest_trajectory_failure_stage_is_success():
    module = get_task(PRIMARY)
    record = run_one_trial(module, MODEL, honest_primary_client(), max_turns=10)
    assert record["failure_stage"] == "success"


def test_read_only_trajectory_failure_stage_is_read_only():
    # Client calls only a read tool then stops. failure_stage must be "read_only".
    module = get_task(PRIMARY)
    client = ScriptedClient(
        [
            _call(0, "search_messages", {"query": "x"}),
            _stop(),
        ]
    )
    record = run_one_trial(module, MODEL, client, max_turns=10)
    assert record["failure_stage"] == "read_only"


def test_write_errors_only_trajectory_failure_stage():
    # Client calls a write tool on a nonexistent task (ok=False) then stops.
    # failure_stage must be "write_errors_only".
    module = get_task(PRIMARY)
    client = ScriptedClient(
        [
            _call(0, "update_task", {"task_id": "T999", "status": "in_progress"}),
            _stop(),
        ]
    )
    record = run_one_trial(module, MODEL, client, max_turns=10)
    assert record["failure_stage"] == "write_errors_only"


def test_partial_writes_trajectory_failure_stage():
    # lazy_primary_client makes writes that succeed (ok=True) but score < 1.0.
    # failure_stage must be "partial_writes".
    module = get_task(PRIMARY)
    record = run_one_trial(module, MODEL, lazy_primary_client(), max_turns=10)
    assert record["failure_stage"] == "partial_writes"


def test_aggregate_has_failure_stage_counts(tmp_path):
    agg = run_trials(
        PRIMARY,
        trials=2,
        model=MODEL,
        client=honest_primary_multi(2),
        max_turns=10,
        transcripts_dir=str(tmp_path),
    )
    counts = agg["failure_stage_counts"]
    assert isinstance(counts, dict)
    assert all(isinstance(v, int) for v in counts.values())
    assert sum(counts.values()) == 2


def test_aggregate_has_stop_reason_counts(tmp_path):
    agg = run_trials(
        PRIMARY,
        trials=2,
        model=MODEL,
        client=honest_primary_multi(2),
        max_turns=10,
        transcripts_dir=str(tmp_path),
    )
    counts = agg["stop_reason_counts"]
    assert isinstance(counts, dict)
    assert all(isinstance(v, int) for v in counts.values())
    assert sum(counts.values()) == 2


def test_aggregate_has_pass_at_k_and_pass_all_k_honest(tmp_path):
    agg = run_trials(
        PRIMARY,
        trials=3,
        model=MODEL,
        client=honest_primary_multi(3),
        max_turns=10,
        transcripts_dir=str(tmp_path),
    )
    assert agg["pass_at_k"] == pytest.approx(1.0)


def test_aggregate_has_pass_at_k_lazy(tmp_path):
    agg = run_trials(
        PRIMARY,
        trials=3,
        model=MODEL,
        client=lazy_primary_multi(3),
        max_turns=10,
        transcripts_dir=str(tmp_path),
    )
    assert agg["pass_at_k"] == pytest.approx(0.0)


def test_pass_at_k_formula_is_correct():
    # Formula: pass_at_k = 1 - (1 - success_rate)^trials
    # For success_rate=0.6, trials=3: 1 - (0.4)^3 = 1 - 0.064 = 0.936
    p, k = 0.6, 3
    expected = 1 - (1 - p) ** k
    assert expected == pytest.approx(0.936)


# ---------------------------------------------------------------------------
# Summary.json tests
# ---------------------------------------------------------------------------


def test_summary_json_written_to_run_dir(tmp_path):
    agg = run_trials(
        PRIMARY,
        trials=1,
        model=MODEL,
        client=honest_primary_multi(1),
        max_turns=10,
        transcripts_dir=str(tmp_path),
    )
    run_dir = Path(agg["run_dir"])
    assert (run_dir / "summary.json").exists()


def test_summary_json_has_system_prompt(tmp_path):
    agg = run_trials(
        PRIMARY,
        trials=1,
        model=MODEL,
        client=honest_primary_multi(1),
        max_turns=10,
        transcripts_dir=str(tmp_path),
    )
    summary = json.loads((Path(agg["run_dir"]) / "summary.json").read_text())
    assert isinstance(summary["system_prompt"], str) and summary["system_prompt"]


def test_summary_json_has_tool_schemas(tmp_path):
    agg = run_trials(
        PRIMARY,
        trials=1,
        model=MODEL,
        client=honest_primary_multi(1),
        max_turns=10,
        transcripts_dir=str(tmp_path),
    )
    summary = json.loads((Path(agg["run_dir"]) / "summary.json").read_text())
    assert isinstance(summary["tool_schemas"], list)
    assert len(summary["tool_schemas"]) > 0
    assert all(isinstance(s, dict) for s in summary["tool_schemas"])


def test_summary_json_has_aggregate_metrics(tmp_path):
    agg = run_trials(
        PRIMARY,
        trials=1,
        model=MODEL,
        client=honest_primary_multi(1),
        max_turns=10,
        transcripts_dir=str(tmp_path),
    )
    summary = json.loads((Path(agg["run_dir"]) / "summary.json").read_text())
    for key in ("success_rate", "pass_at_k",
                "failure_stage_counts", "stop_reason_counts"):
        assert key in summary, f"summary.json missing key: {key}"


# ---------------------------------------------------------------------------
# Sweep tests
# ---------------------------------------------------------------------------


def _noop_client() -> ScriptedClient:
    """Client that immediately stops. Suitable for structural sweep tests where
    scores do not matter -- only filesystem artifacts are checked."""
    return ScriptedClient([_stop()])


def test_sweep_writes_index_json(tmp_path):
    runs_dir = str(tmp_path / "runs")
    run_sweep(
        task_ids=["smoke_test", "vendor_reconcile"],
        trials=1,
        model=MODEL,
        client=_noop_client(),
        max_turns=10,
        transcripts_dir=str(tmp_path / "transcripts"),
        runs_dir=runs_dir,
    )
    sweep_dirs = list(Path(runs_dir).glob("sweep_*"))
    assert len(sweep_dirs) == 1
    assert (sweep_dirs[0] / "index.json").exists()


def test_sweep_index_has_task_list_and_model(tmp_path):
    runs_dir = str(tmp_path / "runs")
    run_sweep(
        task_ids=["smoke_test", "vendor_reconcile"],
        trials=1,
        model=MODEL,
        client=_noop_client(),
        max_turns=10,
        transcripts_dir=str(tmp_path / "transcripts"),
        runs_dir=runs_dir,
    )
    sweep_dirs = list(Path(runs_dir).glob("sweep_*"))
    index = json.loads((sweep_dirs[0] / "index.json").read_text())
    assert index["tasks"] == ["smoke_test", "vendor_reconcile"]
    assert index["model"] == MODEL


def test_sweep_writes_comparison_json(tmp_path):
    runs_dir = str(tmp_path / "runs")
    run_sweep(
        task_ids=["smoke_test", "vendor_reconcile"],
        trials=1,
        model=MODEL,
        client=_noop_client(),
        max_turns=10,
        transcripts_dir=str(tmp_path / "transcripts"),
        runs_dir=runs_dir,
    )
    sweep_dirs = list(Path(runs_dir).glob("sweep_*"))
    assert (sweep_dirs[0] / "comparison.json").exists()


def test_sweep_comparison_has_both_tasks(tmp_path):
    runs_dir = str(tmp_path / "runs")
    run_sweep(
        task_ids=["smoke_test", "vendor_reconcile"],
        trials=1,
        model=MODEL,
        client=_noop_client(),
        max_turns=10,
        transcripts_dir=str(tmp_path / "transcripts"),
        runs_dir=runs_dir,
    )
    sweep_dirs = list(Path(runs_dir).glob("sweep_*"))
    comparison = json.loads((sweep_dirs[0] / "comparison.json").read_text())
    assert len(comparison["tasks"]) == 2
    for entry in comparison["tasks"]:
        for key in ("task", "success_rate", "pass_at_k"):
            assert key in entry, f"comparison entry missing key: {key}"


# ---------------------------------------------------------------------------
# Fresh setup per trial -- isolation
# ---------------------------------------------------------------------------


def test_two_independent_trials_both_score_1_0():
    # If state is reset between trials, both honest runs must score 1.0.
    # If state carried over, the second run would create a second task and post a
    # second message, violating the ALLOWED_CHANGES budget (1 task, 1 message in C009)
    # and causing no_collateral_damage to fail.
    module = get_task(PRIMARY)
    r1 = run_one_trial(module, MODEL, honest_primary_client(), max_turns=10)
    r2 = run_one_trial(module, MODEL, honest_primary_client(), max_turns=10)
    assert r1["score"] == 1.0
    assert r2["score"] == 1.0


# ---------------------------------------------------------------------------
# BUG-01: trajectory logger deep-copies results at call time
# ---------------------------------------------------------------------------


def test_logged_result_is_call_time_snapshot_not_retroactively_mutated():
    """BUG-01: logged result for get_task must show pre-mutation value after update_task."""
    harbor = ApolloHarborTask(get_task(PRIMARY))
    harbor.setup()
    tools = harbor._logged_tools()

    # Log a read: T003.status is "todo" at this point.
    tools["get_task"](task_id="T003")

    # Mutate T003 in live state.
    tools["update_task"](task_id="T003", status="in_progress")

    # The FIRST logged call must still carry the pre-mutation value.
    first_logged = harbor.logger.trajectory[0]
    assert first_logged["tool"] == "get_task"
    assert first_logged["result"]["task"]["status"] == "todo"


# ---------------------------------------------------------------------------
# BUG-07: static_tool_schemas requires no setup or seeded workspace
# ---------------------------------------------------------------------------


def test_static_tool_schemas_requires_no_setup():
    """BUG-07: tool schemas are available without calling setup() or seeding a workspace."""
    schemas = ApolloHarborTask.static_tool_schemas()
    assert isinstance(schemas, list)
    assert len(schemas) > 0
    assert all(isinstance(s, dict) for s in schemas)
    # verify a representative tool is present
    names = {s["name"] for s in schemas}
    assert "search_tasks" in names
    assert "add_reaction" in names
    assert "post_message" in names
