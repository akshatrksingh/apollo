# tasks_spec.md

Three task modules in `tasks/`. Each is self-contained (seed delta + instruction + verifier + allowed_changes) and plugs in without touching `env/`.

Load-bearing IDs (`T003`, `C002`, the linked message id) are design constraints: the verifier asserts on them, so seeds MUST place them exactly as specified. Everything tagged `[PLACEHOLDER — subject to change after experiments]` is tunable; do not over-engineer around it.

Scoring is partial credit: `score = mean(assertions)`. See `verifier_design.md` for the shared utilities and the AllowedChanges schema.

Channel map used across tasks (seeded in `base_workspace`):
| id | name | is_private | agent (U004) member |
|---|---|---|---|
| C001 | general | False | yes |
| C002 | incidents | False | yes |
| C003 | engineering | False | yes |
| C004 | payments-team | True | yes |
| C005 | leadership | True | no |

---

## Task 1 — smoke_test  (1-2 hops, binary, confirms wiring)

Purpose: prove env + tools + snapshot/diff + verifier + Harbor adapter work end to end. Not a discrimination task.

INSTRUCTION `[PLACEHOLDER text]`:
> Post the message `env check ok` to the #general channel.

Seed delta: none beyond `base_workspace()`. (`#general` already exists.)

Minimum honest path (2 hops):
1. `list_channels()` to resolve `general -> C001`
2. `post_message("C001", "env check ok")`

Verifier assertions (binary, single assertion -> score in `{0.0, 1.0}`):
| # | condition | field path |
|---|---|---|
| 1 | a message exists in C001, authored by current_user, ts > episode_start, text (lower+strip) contains `env check ok` | `state_after.messages[*]` where `channel=="C001"`, `author==state_after.current_user`, `ts>episode_start` |

ALLOWED_CHANGES:
```
{ "messages": {"added": {"C001": 1}} }   # exactly the one new post; nothing else may change
```

Cheapest exploit: none meaningful (single direct action). Collateral damage check still applied so a flailing agent that also creates channels/tasks scores 0.

---

## Task 2 — baseline_slack  (pure Slack, 3 hops, saturated, Gemma ~70% `[PLACEHOLDER]`)

Purpose: discrimination floor. Demonstrates Gemma can do single-service chained retrieval, so the gap to Task 3 isolates the cross-service hop as the failure cause.

INSTRUCTION `[PLACEHOLDER text]`:
> Someone reported a database latency spike in #incidents earlier today. Find that report and react to it with :eyes: to acknowledge it.

Seed delta `[PLACEHOLDER contents]`:
- C002 (#incidents) seeded with 10-12 messages so the target is not on page 1 (forces one paginated `get_channel_messages` call). Target message `M_DBLATENCY` `[PLACEHOLDER id, but fixed once chosen]` authored by an `engineer`, text mentions database latency. Include 1-2 decoy messages mentioning "latency" in other contexts.
- No tasks involved.

Minimum honest path (3 hops):
1. `get_channel_messages("C002", limit=10)` -> page 1, target not present, returns `next_cursor`
2. `get_channel_messages("C002", cursor=<next_cursor>)` -> page 2, locate target
3. `add_reaction("M_DBLATENCY", ":eyes:")`

(Alternative honest path: `search_messages("database latency")` then react. Both acceptable, verifier is path-agnostic.)

Verifier assertions (2 assertions -> score in `{0.0, 0.5, 1.0}`):
| # | condition | field path |
|---|---|---|
| 1 | target message carries the reaction from current_user | `state_after.current_user in state_after.messages["M_DBLATENCY"]["reactions"].get(":eyes:", set())` |
| 2 | no collateral damage | `no_collateral_damage(before, after, ALLOWED_CHANGES)` |

ALLOWED_CHANGES:
```
{ "messages": {"modified": {"M_DBLATENCY": {"reactions"}}} }   # only the reaction set on the target may change
```

Cheapest exploit: react to a different "latency" decoy without paginating. Closure: assertion 1 names `M_DBLATENCY` exactly; reacting to a decoy fails assertion 1 and the decoy reaction trips collateral damage, scoring 0.

---

## Task 3 — primary_xservice  (cross-service, 5 hops, unsaturated, Gemma 25-45% `[PLACEHOLDER]`)

Purpose: primary deliverable. Failure point is the cross-service hop (step 3) where Gemma drops the message id between `get_thread` and `search_tasks`.

INSTRUCTION:
> The payments service outage reported in #incidents this morning needs a status update. Find the open task linked to that incident, mark it `in_progress`, then post a short status update to the team in #incidents.

Seed delta (load-bearing parts are fixed; text is `[PLACEHOLDER]`):
- C002 (#incidents) seeded so a `search_messages("payments outage")` returns >1 hit, only one of which is the real incident. Real incident root message `M007` `[id fixed]` authored by an `engineer`. Decoy: a `manager` message also mentioning "payments" / "outage" (e.g. a forwarded summary) `[PLACEHOLDER]`. Disambiguation requires `get_thread("M007")` to confirm replies describe the active outage.
- `M007` has thread replies (children with `parent_id == "M007"`) giving context `[PLACEHOLDER]`.
- Task `T003` `[id fixed]`: title/description about the payments outage, `assignee` = some engineer, `status == "todo"`, `slack_message_id == "M007"`. This is the only task carrying `slack_message_id == "M007"`.
- Distractor tasks: 6-8 total tasks, 2-3 carrying a `slack_message_id` pointing at OTHER messages, mixed statuses/assignees, so guessing `T003` is statistically unlikely.

Minimum honest path (5 hops):
1. `search_messages("payments outage")` -> candidate messages including `M007`
2. `get_thread("M007")` -> confirm this is the active incident (disambiguation)
3. `search_tasks(slack_message_id="M007")` -> returns `T003`
4. `update_task("T003", status="in_progress")`
5. `post_message("C002", "<status update>")`

Gemma failure point: step 3. After `get_thread` returns replies, the model drops `M007` and either calls `update_task` on a guessed task id or stops (describes the action instead of calling it).

Verifier assertions (3 assertions -> score in `{0.0, 0.33, 0.67, 1.0}`):
| # | name | condition | field path |
|---|---|---|---|
| 1 | task_status_correct | T003 moved to in_progress | `state_after.tasks["T003"]["status"] == "in_progress"` |
| 2 | notification_posted | a top-level message exists in C002 from current_user after episode_start | `exists m in state_after.messages: m["channel"]=="C002" and m["author"]==current_user and m["parent_id"] is None and m["ts"]>episode_start` |
| 3 | no_collateral_damage | only allowed diffs present | `no_collateral_damage(before, after, ALLOWED_CHANGES)` |

ALLOWED_CHANGES:
```
{
  "tasks":    {"modified": {"T003": {"status", "updated_at"}}},
  "messages": {"added": {"C002": 1}}            # exactly one new notification in #incidents
}
```

Cheapest exploits + closure:
| exploit | which assertions it fakes | why it does not reach 1.0 |
|---|---|---|
| update a guessed task to in_progress + post anything to C002, no search | 2 (and 1 only if it guesses T003) | assertion 1 names T003 specifically; with 6-8 tasks guessing is unlikely and pass^k averages luck out. Touching a non-T003 task trips assertion 3. |
| post to C002 but never touch any task | 2 only | score capped at 0.33; partial credit surfaces the missing task mutation in loss analysis |
| update T003 but forget to post | 1 (+3) | score 0.67; missing notification visible per-assertion |

Note: `notification_posted` deliberately does NOT check message text content. Path-agnostic by design; the T003-specific assertion plus collateral damage carry discrimination. This tradeoff is desk-checked in `verifier_design.md` before any Gemma run.

### Task 3b — infeasibility variant  (optional, build only if time allows `[PLACEHOLDER — may be cut]`)

Same seed as Task 3 except `T003.slack_message_id` is set to `None` (no task links to `M007`), so the request is infeasible. Correct behavior: agent creates/mutates nothing and signals that no linked task exists.

Verifier assertions (2 assertions):
| # | condition |
|---|---|
| 1 | no new objects created and no task status changed: `diff_keys(before, after)` is empty except nothing |
| 2 | agent output signals refusal/absence `[PLACEHOLDER: detection method — substring check on final message, or a single LLM-judge call per verifier_design.md]` |

Reuses Task 3 utilities; only the seed field and the assertions differ.
