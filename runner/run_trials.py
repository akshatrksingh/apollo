from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from harbor.adapter import ApolloHarborTask
from tasks.registry import get_task

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

SYSTEM_PROMPT = (
    "You are an operator working inside a workspace with Slack-like and task-management "
    "tools. Use the provided tools to accomplish the user's request. Call tools to inspect "
    "state before acting. When the task is complete, stop calling tools."
)

READ_TOOLS = frozenset({
    "list_channels", "get_channel_messages", "search_messages", "get_thread",
    "list_users", "get_task", "list_tasks", "search_tasks",
})

NUDGE_MESSAGE = (
    "You have not called a tool yet. "
    "You must call one of the available tools to act."
)


class TransientApiError(Exception):
    pass


@dataclass
class ToolCallRequest:
    id: str
    name: str
    arguments: dict
    reasoning: str | None = None


@dataclass
class AgentMessage:
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    content: str | None = None


class Client(Protocol):
    def complete(self, messages: list[dict], tools: list[dict]) -> AgentMessage: ...


class OpenRouterClient:
    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model
        self._client = None

    def _ensure(self) -> Any:
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(
                base_url=OPENROUTER_BASE_URL, api_key=self.api_key
            )
        return self._client

    def complete(self, messages: list[dict], tools: list[dict]) -> AgentMessage:
        client = self._ensure()
        try:
            resp = client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools,
            )
        except Exception as exc:
            try:
                from openai import (
                    RateLimitError,
                    InternalServerError,
                    APIConnectionError,
                    APITimeoutError,
                )
                if isinstance(exc, (RateLimitError, InternalServerError,
                                    APIConnectionError, APITimeoutError)):
                    raise TransientApiError(str(exc)) from exc
            except ImportError:
                pass
            raise
        choice = resp.choices[0].message
        reasoning = getattr(choice, "reasoning", None)
        calls: list[ToolCallRequest] = []
        for tc in getattr(choice, "tool_calls", None) or []:
            raw = tc.function.arguments
            try:
                args = json.loads(raw) if raw else {}
            except (json.JSONDecodeError, TypeError):
                args = {}
            calls.append(
                ToolCallRequest(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=args,
                    reasoning=reasoning,
                )
            )
        return AgentMessage(tool_calls=calls, content=choice.content)


class OpenRouterAgent:
    def __init__(
        self,
        client: Client,
        model: str,
        schemas: dict[str, dict],
        max_turns: int,
    ) -> None:
        self.client = client
        self.model = model
        self.schemas = schemas
        self.max_turns = max_turns
        self.raw_responses: list[str] = []
        self.stop_reason: str = "natural_stop"

    def _call_with_retry(
        self, messages: list[dict], tools_param: list[dict]
    ) -> AgentMessage:
        """Up to 3 total tries: immediate, then 1s, then 2s delays."""
        delays = [1, 2]
        try:
            return self.client.complete(messages, tools_param)
        except TransientApiError:
            pass
        for delay in delays:
            time.sleep(delay)
            try:
                return self.client.complete(messages, tools_param)
            except TransientApiError:
                pass
        raise TransientApiError("max retries exceeded")

    def run(self, tools: dict, instruction: str) -> None:
        tools_param = [
            {"type": "function", "function": schema}
            for schema in self.schemas.values()
        ]
        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": instruction},
        ]
        raw_responses: list[str] = []
        api_error_occurred = False
        tool_calls_made = 0

        for _ in range(self.max_turns):
            try:
                msg = self._call_with_retry(messages, tools_param)
            except TransientApiError:
                api_error_occurred = True
                break

            raw_responses.append(msg.content or "")

            if not msg.tool_calls:
                messages.append({
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [],
                })
                messages.append({"role": "user", "content": NUDGE_MESSAGE})
                try:
                    msg = self._call_with_retry(messages, tools_param)
                except TransientApiError:
                    api_error_occurred = True
                    break
                raw_responses.append(msg.content or "")
                if not msg.tool_calls:
                    break

            assistant_entry: dict[str, Any] = {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in msg.tool_calls
                ],
            }
            messages.append(assistant_entry)

            for tc in msg.tool_calls:
                if tc.name not in tools:
                    result = {"ok": False, "error": "unknown_tool"}
                else:
                    try:
                        result = tools[tc.name](
                            _reasoning=tc.reasoning, **tc.arguments
                        )
                    except Exception:
                        result = {"ok": False, "error": "tool_exception"}
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(_json_safe(result)),
                    }
                )
            tool_calls_made += len(msg.tool_calls)
        else:
            self.raw_responses = raw_responses
            self.stop_reason = "max_turns"
            return

        self.raw_responses = raw_responses
        if api_error_occurred:
            self.stop_reason = "api_error"
        elif tool_calls_made == 0:
            self.stop_reason = "no_tool_call"
        else:
            self.stop_reason = "natural_stop"


def _json_safe(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, set):
        return sorted(_json_safe(v) for v in obj)
    return obj


def _sanitize_model(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", model)


def _compute_failure_stage(trajectory: list, score: float) -> str:
    if not trajectory:
        return "no_tool_call"
    if score == 1.0:
        return "success"
    writes = [tc for tc in trajectory if tc["tool"] not in READ_TOOLS]
    if not writes:
        return "read_only"
    if all(not tc["result"].get("ok", False) for tc in writes):
        return "write_errors_only"
    return "partial_writes"


def run_one_trial(
    task_module: Any,
    model: str,
    client: Client,
    max_turns: int,
) -> dict:
    harbor = ApolloHarborTask(task_module)
    harbor.setup()
    schemas = {name: tool.schema for name, tool in harbor.tools.items()}
    agent = OpenRouterAgent(client, model, schemas, max_turns)

    error: str | None = None
    try:
        harbor.run(agent)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"

    score = harbor.score()
    assertion_pairs = task_module.assertions(
        harbor.state_before, harbor.state, harbor.logger.trajectory
    )

    tool_calls_with_ok = []
    for tc in _json_safe(copy.deepcopy(harbor.logger.trajectory)):
        tc["ok"] = bool(tc.get("result", {}).get("ok", False))
        tool_calls_with_ok.append(tc)

    failure_stage = _compute_failure_stage(harbor.logger.trajectory, score)

    record: dict[str, Any] = {
        "task": task_module.TASK_ID,
        "model": model,
        "instruction": task_module.INSTRUCTION,
        "tool_calls": tool_calls_with_ok,
        "tool_call_count": len(harbor.logger.trajectory),
        "raw_responses": agent.raw_responses,
        "score": score,
        "assertions": [
            {"name": name, "passed": passed} for name, passed in assertion_pairs
        ],
        "stop_reason": agent.stop_reason if error is None else "api_error",
        "failure_stage": failure_stage,
    }
    if error is not None:
        record["error"] = error
    return record


def _write_transcript(record: dict, run_dir: Path, trial: int) -> str:
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / f"trial_{trial:02d}.json"
    record = dict(record)
    record["created_at"] = datetime.now(timezone.utc).isoformat()
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2)
    return str(path)


def _write_summary(
    aggregate: dict,
    run_dir: Path,
    system_prompt: str,
    tool_schemas: list[dict],
) -> str:
    run_dir.mkdir(parents=True, exist_ok=True)
    trials = aggregate["trials"]
    summary = {
        "task": aggregate["task"],
        "model": aggregate["model"],
        "trials": trials,
        "system_prompt": system_prompt,
        "tool_schemas": tool_schemas,
        "success_rate": aggregate["success_rate"],
        "pass_at_k": aggregate["pass_at_k"],
        "pass_all_k": aggregate["pass_all_k"],
        "mean_score": aggregate["mean"],
        "scores": aggregate["scores"],
        "distribution": {str(k): v for k, v in aggregate["distribution"].items()},
        "per_assertion_pass_rate": aggregate["per_assertion_pass_rate"],
        "failure_stage_counts": aggregate["failure_stage_counts"],
        "stop_reason_counts": aggregate["stop_reason_counts"],
        "trial_files": [f"trial_{n:02d}.json" for n in range(1, trials + 1)],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    path = run_dir / "summary.json"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
    return str(path)


def run_trials(
    task_id: str,
    trials: int,
    model: str,
    client: Client,
    max_turns: int,
    transcripts_dir: str = "transcripts",
) -> dict:
    task_module = get_task(task_id)
    if task_module is None:
        raise ValueError(f"unknown task: {task_id}")

    run_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    run_dir = Path(transcripts_dir) / f"{task_id}_{_sanitize_model(model)}_{run_stamp}"

    tool_schemas = ApolloHarborTask.static_tool_schemas()

    records: list[dict] = []
    for n in range(1, trials + 1):
        record = run_one_trial(task_module, model, client, max_turns)
        record["trial"] = n
        _write_transcript(record, run_dir, n)
        records.append(record)

    scores = [r["score"] for r in records]
    distribution: dict[float, int] = {}
    for s in scores:
        distribution[s] = distribution.get(s, 0) + 1
    mean = sum(scores) / len(scores) if scores else 0.0
    success_rate = (
        sum(1 for s in scores if s == 1.0) / len(scores) if scores else 0.0
    )
    pass_at_k = 1.0 - (1.0 - success_rate) ** trials if trials else 0.0
    pass_all_k = success_rate ** trials if trials else 0.0

    assertion_names: list[str] = []
    for r in records:
        for a in r["assertions"]:
            if a["name"] not in assertion_names:
                assertion_names.append(a["name"])
    per_assertion_pass_rate: dict[str, float] = {}
    for name in assertion_names:
        passes = sum(
            1
            for r in records
            for a in r["assertions"]
            if a["name"] == name and a["passed"]
        )
        per_assertion_pass_rate[name] = passes / len(records) if records else 0.0

    failure_stage_counts: dict[str, int] = {}
    stop_reason_counts: dict[str, int] = {}
    for r in records:
        fs = r.get("failure_stage", "other")
        failure_stage_counts[fs] = failure_stage_counts.get(fs, 0) + 1
        sr = r.get("stop_reason", "natural_stop")
        stop_reason_counts[sr] = stop_reason_counts.get(sr, 0) + 1

    aggregate = {
        "task": task_id,
        "model": model,
        "trials": trials,
        "scores": scores,
        "distribution": distribution,
        "mean": mean,
        "success_rate": success_rate,
        "pass_at_k": pass_at_k,
        "pass_all_k": pass_all_k,
        "per_assertion_pass_rate": per_assertion_pass_rate,
        "failure_stage_counts": failure_stage_counts,
        "stop_reason_counts": stop_reason_counts,
        "records": records,
        "run_dir": str(run_dir),
    }

    _write_summary(aggregate, run_dir, SYSTEM_PROMPT, tool_schemas)
    _print_summary(aggregate)
    return aggregate


def _print_summary(agg: dict) -> None:
    print(f"task={agg['task']} model={agg['model']} trials={agg['trials']}")
    for r in agg["records"]:
        tag = " ERROR" if "error" in r else ""
        print(
            f"  trial {r['trial']}: score={r['score']:.2f}"
            f" stop={r.get('stop_reason', '')} stage={r.get('failure_stage', '')}{tag}"
        )
    print("distribution:")
    for score in sorted(agg["distribution"]):
        print(f"  {score:.2f}: {agg['distribution'][score]}")
    print(
        f"mean={agg['mean']:.3f}  success_rate={agg['success_rate']:.3f}"
        f"  pass@k={agg['pass_at_k']:.3f}  pass^k={agg['pass_all_k']:.3f}"
    )
    print("per-assertion pass rate:")
    for name, rate in agg["per_assertion_pass_rate"].items():
        print(f"  {name}: {rate:.3f}")
    print("failure stages:", dict(agg["failure_stage_counts"]))
    print("stop reasons:", dict(agg["stop_reason_counts"]))


def run_sweep(
    task_ids: list[str],
    trials: int,
    model: str,
    client: Client,
    max_turns: int,
    transcripts_dir: str = "transcripts",
    runs_dir: str = "runs",
) -> dict:
    sweep_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    sweep_dir = Path(runs_dir) / f"sweep_{sweep_stamp}"
    sweep_dir.mkdir(parents=True, exist_ok=True)

    aggregates: list[dict] = []
    for task_id in task_ids:
        agg = run_trials(task_id, trials, model, client, max_turns, transcripts_dir)
        aggregates.append(agg)

    index = {
        "tasks": task_ids,
        "model": model,
        "trials": trials,
        "task_runs": [
            {"task": agg["task"], "run_dir": agg["run_dir"]}
            for agg in aggregates
        ],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(sweep_dir / "index.json", "w", encoding="utf-8") as fh:
        json.dump(index, fh, indent=2)

    comparison = {
        "model": model,
        "tasks": [
            {
                "task": agg["task"],
                "success_rate": agg["success_rate"],
                "pass_at_k": agg["pass_at_k"],
                "pass_all_k": agg["pass_all_k"],
                "per_assertion_pass_rate": agg["per_assertion_pass_rate"],
                "failure_stage_counts": agg["failure_stage_counts"],
                "stop_reason_counts": agg["stop_reason_counts"],
            }
            for agg in aggregates
        ],
    }
    with open(sweep_dir / "comparison.json", "w", encoding="utf-8") as fh:
        json.dump(comparison, fh, indent=2)

    return {
        "sweep_dir": str(sweep_dir),
        "index": index,
        "comparison": comparison,
        "aggregates": aggregates,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run N trials of a task (or a multi-task sweep)."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--task", default=None, help="single task id")
    group.add_argument("--tasks", nargs="+", default=None, help="multiple task ids for sweep")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--model", default=os.environ.get("OPENROUTER_MODEL"))
    parser.add_argument("--max-turns", type=int, default=20)
    parser.add_argument("--transcripts-dir", default="transcripts")
    parser.add_argument("--runs-dir", default="runs")
    args = parser.parse_args(argv)

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        parser.error("OPENROUTER_API_KEY is not set in the environment")
    if not args.model:
        parser.error("--model or OPENROUTER_MODEL must be provided")

    client = OpenRouterClient(api_key=api_key, model=args.model)
    if args.tasks:
        run_sweep(
            task_ids=args.tasks,
            trials=args.trials,
            model=args.model,
            client=client,
            max_turns=args.max_turns,
            transcripts_dir=args.transcripts_dir,
            runs_dir=args.runs_dir,
        )
    else:
        run_trials(
            task_id=args.task,
            trials=args.trials,
            model=args.model,
            client=client,
            max_turns=args.max_turns,
            transcripts_dir=args.transcripts_dir,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
