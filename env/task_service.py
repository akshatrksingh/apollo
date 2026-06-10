from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from env.state import WorkspaceState

from env.ids import next_id

VALID_STATUSES = {"todo", "in_progress", "done", "blocked"}
ALLOWED_UPDATE_FIELDS = {"title", "description", "assignee", "status", "slack_message_id"}


class TaskService:
    def __init__(self, state: WorkspaceState) -> None:
        self._state = state

    def create_task(
        self,
        title: str,
        description: str,
        assignee: str,
        status: str = "todo",
    ) -> dict:
        if assignee not in self._state.users:
            return {"ok": False, "error": "user_not_found"}
        if status not in VALID_STATUSES:
            return {"ok": False, "error": "invalid_status"}

        task_id = next_id(self._state, "T")
        ts = self._state.clock.next()
        task = {
            "id": task_id,
            "title": title,
            "description": description,
            "assignee": assignee,
            "status": status,
            "slack_message_id": None,
            "created_at": ts,
            "updated_at": ts,
        }
        self._state.tasks[task_id] = task
        return {"ok": True, "task": task}

    def get_task(self, task_id: str) -> dict:
        task = self._state.tasks.get(task_id)
        if task is None:
            return {"ok": False, "error": "task_not_found"}
        return {"ok": True, "task": task}

    def delete_task(self, task_id: str) -> dict:
        if task_id not in self._state.tasks:
            return {"ok": False, "error": "task_not_found"}
        del self._state.tasks[task_id]
        return {"ok": True}

    def list_tasks(self, assignee: str | None = None, status: str | None = None) -> dict:
        tasks = list(self._state.tasks.values())
        if assignee is not None:
            tasks = [t for t in tasks if t["assignee"] == assignee]
        if status is not None:
            tasks = [t for t in tasks if t["status"] == status]
        return {"ok": True, "tasks": tasks}

    def update_task(self, task_id: str, **fields: Any) -> dict:
        task = self._state.tasks.get(task_id)
        if task is None:
            return {"ok": False, "error": "task_not_found"}

        for key, value in fields.items():
            if key not in ALLOWED_UPDATE_FIELDS:
                continue
            if key == "assignee" and value not in self._state.users:
                return {"ok": False, "error": "user_not_found"}
            if key == "status" and value not in VALID_STATUSES:
                return {"ok": False, "error": "invalid_status"}

        for key, value in fields.items():
            if key in ALLOWED_UPDATE_FIELDS:
                task[key] = value  # type: ignore[literal-required]

        task["updated_at"] = self._state.clock.next()
        return {"ok": True, "task": task}

    def search_tasks(
        self,
        query: str | None = None,
        assignee: str | None = None,
        status: str | None = None,
        slack_message_id: str | None = None,
    ) -> dict:
        tasks = list(self._state.tasks.values())

        if query is not None:
            q = query.lower()
            tasks = [
                t for t in tasks
                if q in t["title"].lower() or q in t["description"].lower()
            ]
        if assignee is not None:
            tasks = [t for t in tasks if t["assignee"] == assignee]
        if status is not None:
            tasks = [t for t in tasks if t["status"] == status]
        if slack_message_id is not None:
            tasks = [t for t in tasks if t["slack_message_id"] == slack_message_id]

        return {"ok": True, "tasks": tasks}
