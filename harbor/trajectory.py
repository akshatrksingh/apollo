from __future__ import annotations

from typing import TypedDict


class ToolCall(TypedDict):
    tool: str
    args: dict
    result: dict
    reasoning: str | None


class TrajectoryLogger:
    def __init__(self) -> None:
        self.trajectory: list[ToolCall] = []

    def log(
        self,
        tool: str,
        args: dict,
        result: dict,
        reasoning: str | None = None,
    ) -> None:
        self.trajectory.append(
            {
                "tool": tool,
                "args": args,
                "result": result,
                "reasoning": reasoning,
            }
        )
