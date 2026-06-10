# system_design.md

Frozen contract for the Apollo RL environment. Field names, types, signatures, and interface contracts only. No implementation bodies.

## Architectural rule (applies to every doc)

The environment (`env/`) is stable and fully decoupled from tasks and verifiers.

```
env/        services + state + seeding   STABLE   never edited to add a task
tasks/      one file per task            PLUG-IN  seed delta + instruction + verifier + allowed_changes
verifiers/  shared utilities             STABLE   verify() internals live in each task file
harbor/     adapter: Task -> Harbor      STABLE
```

- A task is a self-contained unit. Adding a task means adding one file in `tasks/`, never editing `env/`.
- The verifier signature is fixed: `verify(state_before, state_after, trajectory) -> float` in `[0.0, 1.0]`. What is inside is task-specific.
- The env never imports from `tasks/`. `tasks/` imports from `env/` and `verifiers/`.

## Directory layout

```
apollo/
  env/
    state.py          WorkspaceState + entity TypedDicts
    clock.py          FrozenClock
    ids.py            next_id(state, prefix)
    slack.py          SlackService
    task_service.py   TaskService
    seed.py           base_workspace()
  tasks/
    registry.py       discovers task modules implementing the Task contract
    smoke_test.py
    baseline_slack.py
    primary_xservice.py
  verifiers/
    utils.py          field_equals, message_exists_in, no_collateral_damage, diff_keys, any_tool_called_before
  harbor/
    adapter.py        wraps a Task module into a Harbor Task (setup/run/score)
    trajectory.py     ToolCall schema + TrajectoryLogger
  runner/
    run_trials.py
  docs/
  tests/
```

## Entity schemas

Entities are plain `dict`s (verifier reads `state.tasks["T003"]["status"]`). `WorkspaceState` is the only object with attribute access. TypedDicts below are for documentation and static typing.

### User  (id prefix `U`)
| field | type | notes |
|---|---|---|
| id | str | `U001` |
| name | str | display name |
| role | str | `engineer` / `manager` / `bot` ... drives role-aware reasoning |
| email | str | seeded |

### Channel  (id prefix `C`)
| field | type | notes |
|---|---|---|
| id | str | `C001` |
| name | str | no leading `#` stored |
| is_private | bool | |
| members | list[str] | user_ids; scope + auth derive from this |

### Message  (id prefix `M`)
| field | type | notes |
|---|---|---|
| id | str | `M001` |
| channel | str | channel_id |
| author | str | user_id |
| text | str | |
| ts | int | monotonic tick from `clock.next()`; orderable |
| parent_id | str \| None | reply target; `None` = top-level |
| reactions | dict[str, set[str]] | `emoji -> set of user_ids` |
| edited | bool | default `False` |
| original_text | str \| None | set on first edit |
| edited_by | str \| None | user_id |
| edited_at | int \| None | tick |

`reactions` uses `set` in memory (dedup + membership). Serialize as `{emoji: sorted(user_ids)}` only if JSON output is needed; snapshots use `copy.deepcopy` and keep sets.

### Task  (id prefix `T`)
| field | type | notes |
|---|---|---|
| id | str | `T001` |
| title | str | |
| description | str | |
| assignee | str | validated user_id FK |
| status | str | one of `todo` `in_progress` `done` `blocked` |
| slack_message_id | str \| None | cross-service link |
| created_at | int | tick |
| updated_at | int | tick |

### WorkspaceState
| attr | type | notes |
|---|---|---|
| users | dict[str, User] | |
| channels | dict[str, Channel] | |
| messages | dict[str, Message] | |
| tasks | dict[str, Task] | |
| clock | FrozenClock | |
| current_user | str | agent acts as this user_id (`U004`) |
| _counters | dict[str, int] | last-used int per prefix `{M,C,T,U}` |

Constraints: nested dicts/primitives + sets only. Deep-copyable. Seed sets `_counters[p]` to the max integer used for prefix `p`.

## FrozenClock  (`env/clock.py`)

No `datetime.now()` anywhere in the codebase. Auditor greps for it.

```python
class FrozenClock:
    def __init__(self, base: str = "2026-06-09T10:00:00", tick_seconds: int = 60): ...
    tick: int            # starts at 0
    def next(self) -> int:        # pre-increment, returns new tick; called on every object creation
    def current(self) -> int:     # returns tick without incrementing
    def to_datetime(self, tick: int) -> str   # base + tick*tick_seconds, ISO string
```

`episode_start` is `state_before.clock.current()` captured at snapshot. Verifiers compare `m["ts"] > episode_start`. No hardcoded timestamp constants in verifiers.

## ID scheme  (`env/ids.py`)

```python
def next_id(state: WorkspaceState, prefix: str) -> str
    # n = (state._counters[prefix] += 1); return f"{prefix}{n:03d}"  -> "M001"
```

Seeded objects use explicit IDs in fixtures. Agent-created objects get `next_id`. Verifiers look up agent-created objects by content (author + channel + ts), never by predicted ID.

## Tool API — return and error contract

- Every tool returns a dict with an `ok` key. Success: `{"ok": True, ...}`. Domain failure: `{"ok": False, "error": <code>}`. Python type errors raise naturally (not wrapped).
- Read tools never mutate state. Write tools mutate and are exactly what verifiers inspect.
- Error codes (closed set): `channel_not_found`, `message_not_found`, `not_authorized`, `user_not_found`, `task_not_found`, `invalid_status`, `invalid_cursor`.

Read vs write taxonomy:
| read (no mutation) | write (mutates) |
|---|---|
| list_channels, get_channel_messages, search_messages, get_thread, list_users, get_task, list_tasks, search_tasks | post_message, update_message, add_reaction, create_channel, add_member, create_task, update_task, delete_task |

### SlackService  (`env/slack.py`) — bound to one `WorkspaceState`; acts as `state.current_user`

MessageView returned to agent = subset `{id, channel, author, text, ts, parent_id, reactions, edited}` (edit internals hidden).

| signature | success return | errors |
|---|---|---|
| `list_channels()` | `{"ok":True,"channels":[{id,name,is_private}]}` only channels where current_user in members | — |
| `get_channel_messages(channel_id, limit=10, cursor=None)` | `{"ok":True,"messages":[MessageView],"next_cursor":str\|None}` top-level only (`parent_id is None`), recency desc, tiebreak id, page size 10 | channel_not_found, not_authorized (not a member), invalid_cursor |
| `post_message(channel_id, text, parent_id=None)` | `{"ok":True,"message":MessageView}` | channel_not_found, not_authorized, message_not_found (bad parent_id) |
| `update_message(message_id, text)` | `{"ok":True,"message":MessageView}` sets edited/original_text/edited_by/edited_at | message_not_found, not_authorized (author != current_user) |
| `add_reaction(message_id, emoji)` | `{"ok":True}` adds current_user to `reactions[emoji]` | message_not_found |
| `create_channel(name, is_private=False)` | `{"ok":True,"channel":{...}}` creator added to members | — |
| `search_messages(query, limit=10, cursor=None)` | `{"ok":True,"messages":[MessageView],"next_cursor":str\|None}` text-only, case-insensitive substring, scope = current_user member channels, recency desc, tiebreak id, page size 10 | invalid_cursor |
| `get_thread(message_id)` | `{"ok":True,"messages":[MessageView]}` all messages where `parent_id == message_id`, ts asc | message_not_found |
| `list_users()` | `{"ok":True,"users":[{id,name,role,email}]}` all users | — |
| `add_member(channel_id, user_id)` | `{"ok":True}` | channel_not_found, user_not_found |

### TaskService  (`env/task_service.py`) — bound to the same `WorkspaceState`

| signature | success return | errors |
|---|---|---|
| `create_task(title, description, assignee, status="todo")` | `{"ok":True,"task":{...}}` created_at/updated_at = clock.next() | user_not_found, invalid_status |
| `get_task(task_id)` | `{"ok":True,"task":{...}}` | task_not_found |
| `delete_task(task_id)` | `{"ok":True}` | task_not_found |
| `list_tasks(assignee=None, status=None)` | `{"ok":True,"tasks":[...]}` AND-combined filters | — |
| `update_task(task_id, **fields)` | `{"ok":True,"task":{...}}` allowed fields: title, description, assignee, status, slack_message_id; bumps updated_at | task_not_found, user_not_found (bad assignee), invalid_status |
| `search_tasks(query=None, assignee=None, status=None, slack_message_id=None)` | `{"ok":True,"tasks":[...]}` query = case-insensitive substring over title+description; filters AND-combined | — |

Extra tools (documented as future extensions, NOT implemented now): `get_user_profile`, `pin_message`, `get_channel_info`, `task_history`.

## Seed architecture  (`env/seed.py` + per-task `seed`)

```python
def base_workspace() -> WorkspaceState   # 5 users, 4-5 channels, background messages; pure, no random/time
# in each task file:
def seed(state: WorkspaceState) -> None   # layers task-specific messages/tasks/reactions in place
```

Both pure and deterministic (Python 3.11 dict order is insertion order). No `random`, no `uuid`, no `time`, no `datetime.now()`. Reset = rebuild via `base_workspace()` then `seed()`.

Base density target (fixed, not randomized): 5 users (4 personas + agent `U004`), 4-5 channels (2 public, 2 private member, 1 private non-member), 15-25 messages (8-12 in busiest channel to force pagination, 2-3 in non-member channel as scope noise), 6-8 tasks (mixed assignees/statuses, 2-3 carrying `slack_message_id`).

## Task contract (what every `tasks/*.py` exports)

```python
TASK_ID: str
INSTRUCTION: str                                  # agent-facing prompt
ALLOWED_CHANGES: AllowedChanges                   # schema in verifier_design.md
def seed(state: WorkspaceState) -> None           # per-task delta, in place
def verify(state_before: WorkspaceState,
           state_after: WorkspaceState,
           trajectory: list[ToolCall]) -> float   # returns 0.0..1.0
```

`tasks/registry.py` discovers modules exporting these symbols. No other coupling.

## Harbor adapter contract  (`harbor/adapter.py`)

```python
class ApolloHarborTask:                # one instance per task module
    def setup(self) -> None:
        # state = base_workspace(); task.seed(state)
        # self.state = state
        # self.state_before = copy.deepcopy(state)     # snapshot
        # self.tools = register(SlackService(state), TaskService(state))  # bound callables + JSON schemas
        # self.logger = TrajectoryLogger()
    def run(self, agent) -> None:
        # agent executes with self.tools; logger records every ToolCall
    def score(self) -> float:
        # return task.verify(self.state_before, self.state, self.logger.trajectory)
```

`ToolCall` schema (`harbor/trajectory.py`):
```python
ToolCall = TypedDict("ToolCall", {"tool": str, "args": dict, "result": dict, "reasoning": str | None})
# verifier may read tool names/order; "reasoning" is for loss analysis only, never scored
```
