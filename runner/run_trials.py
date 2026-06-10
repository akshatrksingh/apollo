from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
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
        # Lazy import so the module imports without the SDK or a key present.
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(
                base_url=OPENROUTER_BASE_URL, api_key=self.api_key
            )
        return self._client

    def complete(self, messages: list[dict], tools: list[dict]) -> AgentMessage:
        client = self._ensure()
        resp = client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=tools,
        )
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

    def run(self, tools: dict, instruction: str) -> None:
        tools_param = [
            {"type": "function", "function": schema}
            for schema in self.schemas.values()
        ]
        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": instruction},
        ]

        for _ in range(self.max_turns):
            msg = self.client.complete(messages, tools_param)
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


def _json_safe(obj: Any) -> Any:
    # Tool results may carry Python sets (reaction membership); JSON needs lists.
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, set):
        return sorted(_json_safe(v) for v in obj)
    return obj


def _sanitize_model(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", model)


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
    record: dict[str, Any] = {
        "task": task_module.TASK_ID,
        "model": model,
        "instruction": task_module.INSTRUCTION,
        "tool_calls": _json_safe(copy.deepcopy(harbor.logger.trajectory)),
        "score": score,
        "assertions": [
            {"name": name, "passed": passed} for name, passed in assertion_pairs
        ],
    }
    if error is not None:
        record["error"] = error
    return record


def _write_transcript(record: dict, run_dir: Path, trial: int) -> str:
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / f"trial{trial}.json"
    record = dict(record)
    record["created_at"] = datetime.now(timezone.utc).isoformat()
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2)
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

    # One subfolder per run groups its N trial transcripts and makes cross-run
    # collisions impossible. Microsecond stamp so two runs in the same second
    # of the same task+model still get distinct directories.
    run_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    run_dir = Path(transcripts_dir) / f"{task_id}_{_sanitize_model(model)}_{run_stamp}"

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
    pass_k = (
        sum(1 for s in scores if s == 1.0) / len(scores) if scores else 0.0
    )

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

    aggregate = {
        "task": task_id,
        "model": model,
        "trials": trials,
        "scores": scores,
        "distribution": distribution,
        "mean": mean,
        "pass_k": pass_k,
        "per_assertion_pass_rate": per_assertion_pass_rate,
        "records": records,
    }
    _print_summary(aggregate)
    return aggregate


def _print_summary(agg: dict) -> None:
    print(f"task={agg['task']} model={agg['model']} trials={agg['trials']}")
    for r in agg["records"]:
        tag = " ERROR" if "error" in r else ""
        print(f"  trial {r['trial']}: score={r['score']:.2f}{tag}")
    print("distribution:")
    for score in sorted(agg["distribution"]):
        print(f"  {score:.2f}: {agg['distribution'][score]}")
    print(f"mean={agg['mean']:.3f}  pass^k={agg['pass_k']:.3f}")
    print("per-assertion pass rate:")
    for name, rate in agg["per_assertion_pass_rate"].items():
        print(f"  {name}: {rate:.3f}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run N trials of one task.")
    parser.add_argument("--task", required=True)
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument("--model", default=os.environ.get("OPENROUTER_MODEL"))
    parser.add_argument("--max-turns", type=int, default=20)
    parser.add_argument("--transcripts-dir", default="transcripts")
    args = parser.parse_args(argv)

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        parser.error("OPENROUTER_API_KEY is not set in the environment")
    if not args.model:
        parser.error("--model or OPENROUTER_MODEL must be provided")

    client = OpenRouterClient(api_key=api_key, model=args.model)
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
