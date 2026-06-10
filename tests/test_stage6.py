"""Stage 6 tests: trial runner, aggregate report, transcripts.

Derived from docs/dev_stages.md Stage 6 test goals and
docs/verifier_design.md (per-assertion shape, 3-assertion 0.33 partial credit).

No real network, no real OPENROUTER_* env reads: a scripted fake client drives
every trial. The runner is built for dependency injection.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from runner.run_trials import (
    AgentMessage,
    ToolCallRequest,
    run_one_trial,
    run_trials,
)

MODEL = "test/model"
PRIMARY = "primary_xservice"


class ScriptedClient:
    """Returns a pre-programmed AgentMessage per complete() call.

    The final scripted message must have empty tool_calls so the agent loop
    stops. Each scripted turn here carries a single tool call.

    The program replays from the start after it emits its stop message, so the
    same scripted behavior is reproduced for every trial in a run_trials batch.
    A real OpenRouter client is stateless per complete(); this models that the
    same model would behave the same way on each fresh trial.
    """

    def __init__(self, messages: list[AgentMessage]) -> None:
        self._messages = list(messages)
        self._i = 0

    def complete(self, messages, tools) -> AgentMessage:
        if self._i >= len(self._messages):
            self._i = 0
        msg = self._messages[self._i]
        self._i += 1
        return msg


def _call(idx: int, name: str, args: dict) -> AgentMessage:
    return AgentMessage(
        tool_calls=[ToolCallRequest(id=f"c{idx}", name=name, arguments=args)]
    )


def _stop() -> AgentMessage:
    return AgentMessage(tool_calls=[])


def honest_primary_client() -> ScriptedClient:
    # Honest cross-service sequence scoring 1.0: discover, then act on the
    # load-bearing ids T003 / M007 / C002. These ids live only inside the
    # scripted harness agent, never in any prompt.
    return ScriptedClient(
        [
            _call(0, "search_messages", {"query": "payments outage"}),
            _call(1, "search_tasks", {"slack_message_id": "M007"}),
            _call(2, "update_task", {"task_id": "T003", "status": "in_progress"}),
            _call(
                3,
                "post_message",
                {"channel_id": "C002", "text": "Picking up the payments outage now."},
            ),
            _stop(),
        ]
    )


def lazy_primary_client() -> ScriptedClient:
    # Lazy desk-check sequence: guesses wrong task T001 and posts to C002.
    # Per verifier_design.md this scores exactly 0.33.
    return ScriptedClient(
        [
            _call(0, "update_task", {"task_id": "T001", "status": "in_progress"}),
            _call(1, "post_message", {"channel_id": "C002", "text": "done"}),
            _stop(),
        ]
    )


# --- Goal 1: per-trial structured record ---------------------------------


def test_run_one_trial_honest_record_score_is_one():
    record = run_one_trial(
        __import__("tasks.registry", fromlist=["get_task"]).get_task(PRIMARY),
        MODEL,
        honest_primary_client(),
        max_turns=10,
    )
    assert record["score"] == 1.0


def test_run_one_trial_honest_all_three_assertions_true():
    module = __import__("tasks.registry", fromlist=["get_task"]).get_task(PRIMARY)
    record = run_one_trial(module, MODEL, honest_primary_client(), max_turns=10)
    by_name = {a["name"]: a["passed"] for a in record["assertions"]}
    assert by_name == {
        "task_status_correct": True,
        "notification_posted": True,
        "no_collateral_damage": True,
    }


def test_run_one_trial_tool_calls_preserve_scripted_order():
    module = __import__("tasks.registry", fromlist=["get_task"]).get_task(PRIMARY)
    record = run_one_trial(module, MODEL, honest_primary_client(), max_turns=10)
    names = [c["tool"] for c in record["tool_calls"]]
    assert names == [
        "search_messages",
        "search_tasks",
        "update_task",
        "post_message",
    ]


def test_run_one_trial_tool_calls_have_tool_args_result():
    module = __import__("tasks.registry", fromlist=["get_task"]).get_task(PRIMARY)
    record = run_one_trial(module, MODEL, honest_primary_client(), max_turns=10)
    for call in record["tool_calls"]:
        assert set(("tool", "args", "result")).issubset(call.keys())
    update = next(c for c in record["tool_calls"] if c["tool"] == "update_task")
    assert update["args"]["task_id"] == "T003"
    assert update["result"]["ok"] is True


def test_run_one_trial_record_has_numeric_score_and_assertion_names():
    module = __import__("tasks.registry", fromlist=["get_task"]).get_task(PRIMARY)
    record = run_one_trial(module, MODEL, honest_primary_client(), max_turns=10)
    assert isinstance(record["score"], (int, float))
    names = {a["name"] for a in record["assertions"]}
    assert names == {
        "task_status_correct",
        "notification_posted",
        "no_collateral_damage",
    }


def test_run_one_trial_lazy_record_score_is_one_third():
    module = __import__("tasks.registry", fromlist=["get_task"]).get_task(PRIMARY)
    record = run_one_trial(module, MODEL, lazy_primary_client(), max_turns=10)
    assert record["score"] == pytest.approx(1.0 / 3.0)


def test_run_one_trial_lazy_identifies_failing_assertions():
    module = __import__("tasks.registry", fromlist=["get_task"]).get_task(PRIMARY)
    record = run_one_trial(module, MODEL, lazy_primary_client(), max_turns=10)
    by_name = {a["name"]: a["passed"] for a in record["assertions"]}
    assert by_name == {
        "task_status_correct": False,
        "notification_posted": True,
        "no_collateral_damage": False,
    }


# --- Goal 2: aggregate report (distribution, mean, pass_k, rates) ---------


def test_run_trials_all_honest_pass_k_is_one(tmp_path):
    agg = run_trials(
        PRIMARY,
        trials=3,
        model=MODEL,
        client=honest_primary_client(),
        max_turns=10,
        transcripts_dir=str(tmp_path),
    )
    assert agg["pass_k"] == 1.0


def test_run_trials_all_honest_distribution_and_mean(tmp_path):
    agg = run_trials(
        PRIMARY,
        trials=3,
        model=MODEL,
        client=honest_primary_client(),
        max_turns=10,
        transcripts_dir=str(tmp_path),
    )
    assert agg["distribution"] == {1.0: 3}
    assert agg["mean"] == pytest.approx(1.0)
    assert agg["trials"] == 3


def test_run_trials_all_honest_per_assertion_rates_all_one(tmp_path):
    agg = run_trials(
        PRIMARY,
        trials=3,
        model=MODEL,
        client=honest_primary_client(),
        max_turns=10,
        transcripts_dir=str(tmp_path),
    )
    rates = agg["per_assertion_pass_rate"]
    assert rates["task_status_correct"] == 1.0
    assert rates["notification_posted"] == 1.0
    assert rates["no_collateral_damage"] == 1.0


def test_run_trials_all_lazy_pass_k_is_zero(tmp_path):
    agg = run_trials(
        PRIMARY,
        trials=3,
        model=MODEL,
        client=lazy_primary_client(),
        max_turns=10,
        transcripts_dir=str(tmp_path),
    )
    assert agg["pass_k"] == 0.0
    assert agg["pass_k"] < 1.0


def test_run_trials_all_lazy_distribution_is_third(tmp_path):
    agg = run_trials(
        PRIMARY,
        trials=3,
        model=MODEL,
        client=lazy_primary_client(),
        max_turns=10,
        transcripts_dir=str(tmp_path),
    )
    assert list(agg["distribution"].keys()) == [pytest.approx(1.0 / 3.0)]
    assert sum(agg["distribution"].values()) == 3


def test_run_trials_all_lazy_per_assertion_rates_pinpoint_failure(tmp_path):
    agg = run_trials(
        PRIMARY,
        trials=3,
        model=MODEL,
        client=lazy_primary_client(),
        max_turns=10,
        transcripts_dir=str(tmp_path),
    )
    rates = agg["per_assertion_pass_rate"]
    assert rates["task_status_correct"] == 0.0
    assert rates["no_collateral_damage"] == 0.0
    assert rates["notification_posted"] == 1.0


def test_run_trials_per_assertion_rate_values_are_fractions(tmp_path):
    agg = run_trials(
        PRIMARY,
        trials=3,
        model=MODEL,
        client=lazy_primary_client(),
        max_turns=10,
        transcripts_dir=str(tmp_path),
    )
    for rate in agg["per_assertion_pass_rate"].values():
        assert 0.0 <= rate <= 1.0


# --- Goal 3: forced-failure stub --------------------------------------------


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


# --- Goal 4: transcript file -----------------------------------------------


def test_transcript_files_match_name_pattern(tmp_path):
    run_trials(
        PRIMARY,
        trials=2,
        model=MODEL,
        client=honest_primary_client(),
        max_turns=10,
        transcripts_dir=str(tmp_path),
    )
    # One subfolder per run: <task>_<model_sanitized>_<UTC+micros>Z/trialN.json.
    run_dirs = [p for p in Path(tmp_path).iterdir() if p.is_dir()]
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    # Folder carries task/model/timestamp; "/" sanitized to "_".
    assert run_dir.name.startswith(f"{PRIMARY}_test_model_")
    assert "/" not in run_dir.name
    assert "test/model" not in run_dir.name
    # Exactly `trials` files named trial1.json .. trialN.json.
    files = sorted(run_dir.glob("trial*.json"))
    assert [f.name for f in files] == ["trial1.json", "trial2.json"]


def test_transcript_contents_have_instruction_calls_score_assertions(tmp_path):
    run_trials(
        PRIMARY,
        trials=1,
        model=MODEL,
        client=honest_primary_client(),
        max_turns=10,
        transcripts_dir=str(tmp_path),
    )
    run_dirs = [p for p in Path(tmp_path).iterdir() if p.is_dir()]
    assert len(run_dirs) == 1
    files = sorted(run_dirs[0].glob("trial*.json"))
    assert [f.name for f in files] == ["trial1.json"]
    f = files[0]
    data = json.loads(f.read_text())
    assert isinstance(data["instruction"], str) and data["instruction"]
    assert isinstance(data["score"], (int, float))
    # ordered tool calls each with args and result
    names = [c["tool"] for c in data["tool_calls"]]
    assert names == [
        "search_messages",
        "search_tasks",
        "update_task",
        "post_message",
    ]
    for c in data["tool_calls"]:
        assert "args" in c and "result" in c
    # exactly 3 per-assertion booleans for the primary task
    assert len(data["assertions"]) == 3
    for a in data["assertions"]:
        assert isinstance(a["passed"], bool)


def test_transcript_serializes_sets_as_lists_not_python_sets(tmp_path):
    run_trials(
        PRIMARY,
        trials=1,
        model=MODEL,
        client=honest_primary_client(),
        max_turns=10,
        transcripts_dir=str(tmp_path),
    )
    run_dirs = [p for p in Path(tmp_path).iterdir() if p.is_dir()]
    assert len(run_dirs) == 1
    files = sorted(run_dirs[0].glob("trial*.json"))
    assert [f.name for f in files] == ["trial1.json"]
    raw = files[0].read_text()
    # JSON cannot encode a Python set; valid load proves no set survived.
    data = json.loads(raw)

    # Any reactions field anywhere in the transcript must be a list, not a set.
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


# --- Goal 5: offline / no-hang ----------------------------------------------


def test_empty_client_completes_with_empty_trajectory_and_numeric_score():
    module = __import__("tasks.registry", fromlist=["get_task"]).get_task(PRIMARY)
    # First (and only) message has empty tool_calls: loop must stop immediately.
    record = run_one_trial(module, MODEL, ScriptedClient([_stop()]), max_turns=10)
    assert record["tool_calls"] == []
    assert isinstance(record["score"], (int, float))


def test_tool_result_not_ok_does_not_raise():
    module = __import__("tasks.registry", fromlist=["get_task"]).get_task(PRIMARY)
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
