from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType

_REQUIRED = ("TASK_ID", "INSTRUCTION", "ALLOWED_CHANGES", "seed", "verify")
_SKIP = {"registry"}


def _is_task_module(module: ModuleType) -> bool:
    return all(hasattr(module, attr) for attr in _REQUIRED)


def discover_tasks() -> list[ModuleType]:
    import tasks

    found: list[ModuleType] = []
    for info in pkgutil.iter_modules(tasks.__path__):
        if info.name in _SKIP:
            continue
        module = importlib.import_module(f"tasks.{info.name}")
        if _is_task_module(module):
            found.append(module)
    found.sort(key=lambda m: m.TASK_ID)
    return found


def list_tasks() -> list[ModuleType]:
    return discover_tasks()


def get_task(task_id: str) -> ModuleType | None:
    for module in discover_tasks():
        if module.TASK_ID == task_id:
            return module
    return None
