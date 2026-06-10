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

## Partial credit

```python
score = sum(1.0 for a in assertions if a) / len(assertions)
```
2 assertions -> `{0.0, 0.5, 1.0}`. 3 assertions -> `{0.0, 0.33, 0.67, 1.0}`. Equal weights. Each assertion must check a DIFFERENT aspect (a state field, a posting, collateral damage), so the vector of assertion results is the loss-analysis signal. The runner logs per-assertion booleans, not just the mean.

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

## Desk-check exploit trajectory (primary task, run before any Gemma run)

Walk the verifier by hand against a deliberately lazy trajectory:

```
trajectory = [ update_task("T001", status="in_progress"),   # guessed, wrong task
               post_message("C002", "done") ]
```
Expected scoring:
- assertion 1 task_status_correct: `tasks["T003"].status == "in_progress"` -> False (T003 untouched)
- assertion 2 notification_posted: message in C002 after start -> True
- assertion 3 no_collateral_damage: T001 modified, not in ALLOWED_CHANGES -> False
- score = 1/3 = 0.33

Pass condition for the check: lazy trajectory scores < 1.0 (here 0.33). If it scored 1.0, the assertions are too loose. This 10-minute check is cheaper than discovering reward hacking after the runs. `[Re-run this desk-check whenever an assertion or ALLOWED_CHANGES changes.]`

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
