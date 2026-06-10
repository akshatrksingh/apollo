# verifier_design.md

How verifiers are built so any new one drops in by implementing one signature. Internals are task-specific; the contract and the shared utilities are stable.

## Fixed interface contract

```python
def verify(state_before: WorkspaceState,
           state_after:  WorkspaceState,
           trajectory:   list[ToolCall]) -> float   # 0.0 .. 1.0
```

- Reads final state only (`state_after`), plus `state_before` for diffing. Does NOT read agent answer text for pass/fail (except the optional infeasibility judge, below).
- Accesses state by direct attribute/dict access (`state_after.tasks["T003"]["status"]`). Not through the tool API. This keeps verifier bugs and agent bugs separable.
- `episode_start = state_before.clock.current()`. No hardcoded timestamps.
- Lives inside the task module; uses `verifiers/utils.py`.
- Runs after the rollout only, in the harness `score()` step. The agent has no tool to call it and no field on `WorkspaceState` that reaches it, `ALLOWED_CHANGES`, or any ground-truth. See Environment isolation in system_design.md.

## Shared utilities  (`verifiers/utils.py`)

```python
def field_equals(state: WorkspaceState, path: tuple, expected) -> bool
    # path = ("tasks","T003","status"); safe nested get; missing -> False

def message_exists_in(state: WorkspaceState, channel_id: str, *,
                      author: str | None = None,
                      after_ts: int | None = None,
                      top_level: bool = False,
                      text_contains: str | None = None) -> bool
    # any message matching all provided predicates; text match is lower+strip substring

def diff_keys(before: WorkspaceState, after: WorkspaceState) -> DiffReport
    # per collection: added ids, removed ids, modified [(id, changed_field_set)]

def no_collateral_damage(before: WorkspaceState, after: WorkspaceState,
                         allowed: AllowedChanges) -> bool
    # True iff every change in diff_keys(before, after) is permitted by `allowed`

def any_tool_called_before(trajectory: list[ToolCall],
                           read_tool: str, write_tool: str) -> bool
    # presence + order only: some read_tool call appears before the first write_tool call
```

`DiffReport`:
```python
DiffReport = {
  "users":    {"added": [id], "removed": [id], "modified": [(id, {field})]},
  "channels": {...same shape...},
  "messages": {...},
  "tasks":    {...},
}
```
A field is "modified" when the value differs under the normalization rules below. `reactions` compares as set equality per emoji.

## Normalization rules

| target | rule | rationale |
|---|---|---|
| timestamps on agent-created objects | assert `ts > episode_start`, never exact value | exact ts is nondeterministic across runs |
| ids of agent-created objects | content lookup (author + channel + ts window), never predicted id | id depends on call count |
| free text (message body) | `text.lower().strip()` then substring | case/whitespace are not errors |
| status enum | exact match | wrong enum value is a real agent error |
| assignee user_id | exact match | guessing `alice` vs `U001` is a real failure |
| reactions | set equality per emoji | order irrelevant, membership is the signal |
| `updated_at` / `edited_at` | treated as expected churn when the parent field legitimately changed; listed in ALLOWED_CHANGES | avoids false collateral-damage hits |

## Scoring

Tasks use binary scoring: `score = 1.0 if all(assertions) else 0.0`. Per-assertion booleans are logged in every transcript for loss analysis. Equal-weighted partial credit was used in Stages 1-5 but is excluded from all post-Stage-6 comparisons (see changelog 2026-06-10 binary-scoring entry).

## AllowedChanges schema + collateral damage pattern

```python
AllowedChanges = {
  "users":    {"added": int_max, "removed": [id], "modified": {id: {field}}},
  "channels": {"added": int_max, "removed": [id], "modified": {id: {field}}},
  "messages": {"added": {channel_id: int_max}, "removed": [id], "modified": {id: {field}}},
  "tasks":    {"added": int_max, "removed": [id], "modified": {id: {field}}},
}   # any key omitted means "no change of that kind permitted"
```

`no_collateral_damage` algorithm (design, not code):
1. Compute `diff_keys(before, after)`.
2. For each collection, every added id must fit an `added` budget (count, or channel-scoped count for messages); every removed id must be listed; every modified `(id, fields)` must have `fields` ⊆ the allowed field set for that id.
3. Any change not covered -> return `False`.

So "update T003 status + post one message to C002" passes; "also delete T005" or "edit M002" fails.

## Desk-check exploit trajectories (primary task, re-run before any Gemma run)

Scoring is binary: 1.0 iff all 4 assertions pass, else 0.0.

**Lazy desk-check** (guesses wrong task, posts):
```
trajectory = [ update_task("T001", status="in_progress"),   # guessed, wrong task
               post_message("C002", "done") ]
```
- a1 task_status_correct: state_before T003.status=="todo" True, state_after T003.status=="in_progress" False -> False
- a2 notification_posted: top-level C002 post -> True
- a3 no_collateral_damage: T001 modified, not in ALLOWED_CHANGES -> False
- a4 cross_service_link_used: no search_tasks(slack_message_id="M007") before update_task -> False
- score = 0.0 (binary: not all pass)

**Shortcut desk-check** (updates T003 and posts, but bypasses the M007 link):
```
trajectory = [ update_task("T003", status="in_progress"),
               post_message("C002", "done") ]
```
- a1: state_before=="todo" True AND state_after=="in_progress" True -> True
- a2: top-level C002 post -> True
- a3: T003.status+updated_at changed (allowed), one C002 message added (allowed) -> True
- a4: no search_tasks(slack_message_id="M007") before update_task -> False
- score = 0.0

**Honest cross-service desk-check**:
```
trajectory = [ search_messages("payments outage"),
               get_thread("M007"),
               search_tasks(slack_message_id="M007"),   # cross-service link
               update_task("T003", status="in_progress"),
               post_message("C002", "status update") ]
```
- a1: True (T003: todo -> in_progress)
- a2: True (top-level C002 post)
- a3: True (only allowed changes)
- a4: search_tasks(slack_message_id="M007") at index 2, before update_task at index 3 -> True
- score = 1.0

Pass condition: shortcut scores 0.0, honest scores 1.0. `[Re-run this desk-check whenever an assertion, ALLOWED_CHANGES, or T003 seed text changes.]`

## LLM-judge decision rule

Default for Tasks 1-3: no judge. All success conditions are state-verifiable.

Use a judge only when success is irreducibly about message-content quality with no state proxy (currently only the optional infeasibility-refusal signal in Task 3b). When used:
| rule | value |
|---|---|
| model | a different family than the agent under test (agent = Gemma 4 26B -> judge = Claude Sonnet or GPT-4o) |
| input | only the relevant agent message text + a fixed rubric; NOT full workspace state, NOT agent reasoning |
| output | constrained to `{"pass": bool}` or a `0..1` scalar; parsed, never free-form |
| placement | one assertion among the others, same equal weight |
| logging | judge prompt + raw judge output stored in the trajectory for audit |

Judges are a fallback, not the spine. If a task starts needing a judge for its core check, redesign the task toward a state outcome instead.

## Adding a new verifier

Implement `verify(before, after, trajectory) -> float` in the new task file, compose it from the utilities above, define its `ALLOWED_CHANGES`, run the desk-check. No change to `env/`, `verifiers/utils.py`, or the Harbor adapter.
