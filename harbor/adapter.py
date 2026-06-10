from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Callable

from env.seed import base_workspace
from env.slack import SlackService
from env.task_service import TaskService
from harbor.trajectory import TrajectoryLogger


@dataclass
class Tool:
    callable: Callable[..., dict]
    schema: dict


def _schema(name: str, properties: dict, required: list[str]) -> dict:
    return {
        "name": name,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
    }


# Tool name -> (service_attr, schema). Only Slack/Task tools; nothing reaches the grader.
_SLACK_SCHEMAS = {
    "list_channels": _schema("list_channels", {}, []),
    "get_channel_messages": _schema(
        "get_channel_messages",
        {
            "channel_id": {"type": "string"},
            "limit": {"type": "integer"},
            "cursor": {"type": ["string", "null"]},
        },
        ["channel_id"],
    ),
    "post_message": _schema(
        "post_message",
        {
            "channel_id": {"type": "string"},
            "text": {"type": "string"},
            "parent_id": {"type": ["string", "null"]},
        },
        ["channel_id", "text"],
    ),
    "update_message": _schema(
        "update_message",
        {"message_id": {"type": "string"}, "text": {"type": "string"}},
        ["message_id", "text"],
    ),
    "add_reaction": _schema(
        "add_reaction",
        {
            "message_id": {"type": "string"},
            "emoji": {
                "type": "string",
                "description": "Emoji name with or without surrounding colons, e.g. 'eyes' or ':eyes:'. Both forms are accepted.",
            },
        },
        ["message_id", "emoji"],
    ),
    "create_channel": _schema(
        "create_channel",
        {"name": {"type": "string"}, "is_private": {"type": "boolean"}},
        ["name"],
    ),
    "search_messages": _schema(
        "search_messages",
        {
            "query": {"type": "string"},
            "limit": {"type": "integer"},
            "cursor": {"type": ["string", "null"]},
        },
        ["query"],
    ),
    "get_thread": _schema(
        "get_thread", {"message_id": {"type": "string"}}, ["message_id"]
    ),
    "list_users": _schema("list_users", {}, []),
    "add_member": _schema(
        "add_member",
        {"channel_id": {"type": "string"}, "user_id": {"type": "string"}},
        ["channel_id", "user_id"],
    ),
}

_TASK_SCHEMAS = {
    "create_task": _schema(
        "create_task",
        {
            "title": {"type": "string"},
            "description": {"type": "string"},
            "assignee": {"type": "string"},
            "status": {"type": "string"},
        },
        ["title", "description", "assignee"],
    ),
    "get_task": _schema(
        "get_task", {"task_id": {"type": "string"}}, ["task_id"]
    ),
    "delete_task": _schema(
        "delete_task", {"task_id": {"type": "string"}}, ["task_id"]
    ),
    "list_tasks": _schema(
        "list_tasks",
        {
            "assignee": {"type": ["string", "null"]},
            "status": {"type": ["string", "null"]},
        },
        [],
    ),
    "update_task": _schema(
        "update_task",
        {
            "task_id": {"type": "string"},
            "title": {"type": "string"},
            "description": {"type": "string"},
            "assignee": {"type": "string"},
            "status": {"type": "string"},
            "slack_message_id": {"type": ["string", "null"]},
        },
        ["task_id"],
    ),
    "search_tasks": _schema(
        "search_tasks",
        {
            "query": {"type": ["string", "null"]},
            "assignee": {"type": ["string", "null"]},
            "status": {"type": ["string", "null"]},
            "slack_message_id": {"type": ["string", "null"]},
        },
        [],
    ),
}


class ApolloHarborTask:
    def __init__(self, task: Any) -> None:
        self.task = task
        self.state = None
        self.state_before = None
        self.tools: dict[str, Tool] = {}
        self.logger = TrajectoryLogger()

    @staticmethod
    def static_tool_schemas() -> list[dict]:
        """Return all tool schemas without requiring setup() or a seeded workspace."""
        return [s for s in {**_SLACK_SCHEMAS, **_TASK_SCHEMAS}.values()]

    def setup(self) -> None:
        state = base_workspace()
        self.task.seed(state)
        self.state = state
        self.state_before = copy.deepcopy(state)

        slack = SlackService(state)
        tasks = TaskService(state)

        self.logger = TrajectoryLogger()
        self.tools = {}
        for name, schema in _SLACK_SCHEMAS.items():
            self.tools[name] = Tool(callable=getattr(slack, name), schema=schema)
        for name, schema in _TASK_SCHEMAS.items():
            self.tools[name] = Tool(callable=getattr(tasks, name), schema=schema)

    def _logged_tools(self) -> dict[str, Callable[..., dict]]:
        wrapped: dict[str, Callable[..., dict]] = {}
        for name, tool in self.tools.items():
            wrapped[name] = self._wrap(name, tool.callable)
        return wrapped

    def _wrap(self, name: str, fn: Callable[..., dict]) -> Callable[..., dict]:
        logger = self.logger

        def call(*args: Any, _reasoning: str | None = None, **kwargs: Any) -> dict:
            result = fn(*args, **kwargs)
            logged_args = dict(kwargs)
            if args:
                logged_args["_positional"] = list(args)
            logger.log(name, logged_args, copy.deepcopy(result), _reasoning)
            return result

        return call

    def run(self, agent: Any) -> None:
        tools = self._logged_tools()
        instruction = self.task.INSTRUCTION
        if hasattr(agent, "run"):
            agent.run(tools, instruction)
        else:
            agent(tools, instruction)

    def score(self) -> float:
        return self.task.verify(
            self.state_before, self.state, self.logger.trajectory
        )
