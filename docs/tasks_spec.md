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
- Task `T003` `[id fixed]`: title = `"Stabilize the PYMT-SVC component"`, description = `"PYMT-SVC dependency graph has a fault. Identify the degraded node and mitigate."`, `assignee` = some engineer, `status == "todo"`, `slack_message_id == "M007"`. Title and description contain NO keyword derivable from M007's text or the instruction (no "payments", "outage", "checkout", "5xx", "payment", "gateway", "failing", "incident", "service"), so `search_tasks(query=...)` cannot find T003; the agent must use `search_tasks(slack_message_id="M007")`. This is the only task carrying `slack_message_id == "M007"`.
- Distractor tasks: 6-8 total tasks, 2-3 carrying a `slack_message_id` pointing at OTHER messages, mixed statuses/assignees, so guessing `T003` is statistically unlikely.

Minimum honest path (5 hops):
1. `search_messages("payments outage")` -> candidate messages including `M007`
2. `get_thread("M007")` -> confirm this is the active incident (disambiguation)
3. `search_tasks(slack_message_id="M007")` -> returns `T003`
4. `update_task("T003", status="in_progress")`
5. `post_message("C002", "<status update>")`

Gemma failure point: step 3. After `get_thread` returns replies, the model drops `M007` and either calls `update_task` on a guessed task id or stops (describes the action instead of calling it).

Verifier assertions (4 assertions -> binary score `{0.0, 1.0}`):
| # | name | condition | field path |
|---|---|---|---|
| 1 | task_status_correct | T003 status was "todo" at episode start AND is "in_progress" at episode end (change-based) | `state_before.tasks["T003"]["status"] == "todo" and state_after.tasks["T003"]["status"] == "in_progress"` |
| 2 | notification_posted | a top-level message exists in C002 from current_user after episode_start | `exists m in state_after.messages: m["channel"]=="C002" and m["author"]==current_user and m["parent_id"] is None and m["ts"]>episode_start` |
| 3 | no_collateral_damage | only allowed diffs present | `no_collateral_damage(before, after, ALLOWED_CHANGES)` |
| 4 | cross_service_link_used | `search_tasks(slack_message_id="M007")` appears in trajectory before the first `update_task` call; False if no `update_task` in trajectory | trajectory scan: any call with `tool=="search_tasks"` and `args["slack_message_id"]=="M007"` at index < first `update_task` index |

ALLOWED_CHANGES:
```
{
  "tasks":    {"modified": {"T003": {"status", "updated_at"}}},
  "messages": {"added": {"C002": 1}}            # exactly one new notification in #incidents
}
```

Cheapest exploits + closure (all under binary scoring; any failing assertion -> 0.0):
| exploit | a1 | a2 | a3 | a4 | score |
|---|---|---|---|---|---|
| text-search shortcut: search_tasks(query="payments outage"), update T003, post | T | T | T | F | 0.0 — T003 description contains no M007 keywords so search returns nothing; even if agent guesses T003 directly, a4 catches absence of M007 link traversal |
| guess wrong task (e.g. T001), post to C002 | F | T | F | F | 0.0 — T003 untouched; T001 modification trips collateral damage |
| update T003 via link, no post | T | F | T | T | 0.0 — notification missing |
| post to C002, no task touched | F | T | T | F | 0.0 — T003 status unchanged; no update_task so a4=False |
| honest 5-hop path | T | T | T | T | 1.0 |

Note: `notification_posted` deliberately does NOT check message text content. Path-agnostic by design; the T003-specific assertion plus collateral damage carry discrimination. This tradeoff is desk-checked in `verifier_design.md` before any Gemma run.

### Task 3b — infeasibility variant  (optional, build only if time allows `[PLACEHOLDER — may be cut]`)

Same seed as Task 3 except `T003.slack_message_id` is set to `None` (no task links to `M007`), so the request is infeasible. Correct behavior: agent creates/mutates nothing and signals that no linked task exists.

Verifier assertions (2 assertions):
| # | condition |
|---|---|
| 1 | no new objects created and no task status changed: `diff_keys(before, after)` is empty except nothing |
| 2 | agent output signals refusal/absence `[PLACEHOLDER: detection method — substring check on final message, or a single LLM-judge call per verifier_design.md]` |

Reuses Task 3 utilities; only the seed field and the assertions differ.

---

## Task 4 -- vendor_reconcile  (cross-world, 12 hops, unsaturated, Gemma ~0.2 success_rate)

Purpose: multi-hop assembly task. Agent must gather scattered payment amounts from several channels, apply one explicit correction, ignore missing data for a third vendor, disambiguate two people sharing a first name, identify the correct owner, create a task, and reply in the right thread. Difficulty comes from world construction, not instruction wording.

Seeds on top of `rich_workspace()` from `env/seed_rich.py` (13 users, 8 channels, 63 messages, 15 tasks as the baseline world).

INSTRUCTION:
> Finance flagged that one of our vendor payments this month doesn't reconcile. Figure out which vendor was short-paid, open a task for the right person to fix it, and reply in the finance lead's thread to confirm. If the numbers actually check out, don't create anything, just say so in the thread.

Seed delta (layered on top of `rich_workspace()`):

| id | what | detail |
|----|------|--------|
| U014 | Sam Wells, role=finance | Finance lead. Same first name as Sam Okello (U012, engineer) -- disambiguation trap. |
| C009 | #finance, private | Members: Sam Wells, Alice Chen, Agent (U004). Agent must discover this channel via `list_channels`. |
| M064 | Finance flag (THREAD ROOT) | Sam Wells in C009: "month-end vendor run complete, three vendors, at least one discrepancy, payments engineer please investigate". Required reply target. |
| M065 | Alice ack (reply to M064) | Filler -- Alice says she will follow up. |
| M066 | Acme payment WRONG amount | Carol Diaz in C004: "Acme Corp disbursement went out: $4,200." -- incorrect figure. |
| M067 | Acme correction (reply to M066) | Bob Kim in C004: "wrong -- actual was $2,400, not $4,200. My mistake." -- must use corrected value. |
| M068 | Brightline payment SHORT | Carol Diaz in C004: "Brightline payment: $2,200 confirmed." -- vs expected $2,400. |
| M069 | Brightline API decoy | Sam Okello (U012) in C002: mentions Brightline re: API contract terms -- NOT a payment message. |
| M070 | Acme casual filler | Lena Vogel in C007: "glad the Acme setup is done." -- irrelevant. |
| M071 | Expected amounts + owner hint | Sam Wells in C009: "Acme $2,400, Brightline $2,400, Cloudmesh pending. For corrections, Bob on the payments team is the one to loop in." |
| M072 | Cloudmesh not billed trap | Finn Torres in C003: "Cloudmesh hasn't invoiced us yet." -- absence is not short-payment. |
| M073 | Cloudmesh queued trap | Carol Diaz in C004: "Added Cloudmesh to the payment queue -- no transfer yet." |
| M074 | Sam Okello payments misdirection | Sam Okello in C001: "finance keeps pinging me about payments, not my area." -- name trap. |

Key puzzle elements:
- **Correction**: Acme posted as $4,200, corrected to $2,400 in M067 reply (must use M067, not M066)
- **Missing data trap**: Cloudmesh expected $1,800 but no payment confirmation -- absence is not evidence of short-payment
- **Two Sams**: Sam Wells (U014, finance) vs Sam Okello (U012, engineer) -- reply must go to Wells's thread, task must NOT be assigned to Okello
- **Two Bobs**: Bob Kim (U002, in C004 payments-team) vs Bob Park (U008, NOT in C004) -- hint "Bob on the payments team" in M071 requires disambiguation via C004 membership
- **Correct answer**: Brightline short-paid by $200 ($2,200 paid vs $2,400 contracted); task assigned to Bob Kim (U002)

Minimum honest path (12 hops):
1. `list_channels()` -- discover C009 (finance channel)
2. `get_channel_messages("C009")` -- read finance flag M064 and expected amounts M071
3. `get_thread("M064")` -- confirm thread to reply to
4. `search_messages("Acme")` -- find M066 ($4,200 wrong)
5. `get_thread("M066")` -- find M067 correction ($2,400)
6. `search_messages("Brightline")` -- find M068 ($2,200) and M069 (decoy)
7. `search_messages("Cloudmesh")` -- find M072/M073 (no payment -- trap, skip)
8. `get_channel_messages("C004")` -- corroborate amounts in payments-team context
9. `list_users()` -- resolve Bob Kim (U002) vs Sam Okello (U012) vs Sam Wells (U014) vs Bob Park (U008)
10. `get_channel_messages("C003")` -- confirm Cloudmesh context
11. `create_task(title=..., description=..., assignee="U002")` -- Brightline task for Bob Kim
12. `post_message("C009", parent_id="M064", text="...Brightline...confirm...")` -- thread reply naming vendor

Verifier assertions (5 assertions, binary AND-scored -> `{0.0, 1.0}`):

| # | name | condition |
|---|------|-----------|
| 1 | `brightline_task_created` | a new task (not in `state_before.tasks`) exists whose `title + description` contains "brightline" (case-insensitive) |
| 2 | `task_assigned_to_payments_engineer` | that task's `assignee == "U002"` (Bob Kim) |
| 3 | `reply_in_finance_thread` | a new message exists with `channel=="C009"`, `author==current_user`, `parent_id=="M064"`, `ts > episode_start` |
| 4 | `reply_names_brightline` | that message's text contains "brightline" (case-insensitive); minimal content check justified because the reply is the communicative confirmation |
| 5 | `no_collateral_damage` | `no_collateral_damage(before, after, ALLOWED_CHANGES)` |

ALLOWED_CHANGES:
```
{
  "tasks":    {"added": 1},
  "messages": {"added": {"C009": 1}}
}
```

Exploit desk-check (all -> 0.0 under binary scoring):
| exploit | a1 | a2 | a3 | a4 | a5 | score |
|---------|----|----|----|----|-----|-------|
| do-nothing | F | F | F | F | T | 0.0 |
| correct vendor, wrong assignee (Sam Okello U012) | T | F | T | T | T | 0.0 |
| correct vendor, wrong assignee (Bob Park U008) | T | F | T | T | T | 0.0 |
| wrong vendor (Acme), correct assignee | F | F | T | F | T | 0.0 |
| correct task, top-level reply in C009 (not thread) | T | T | F | F | T | 0.0 |
| correct task, reply in wrong channel | T | T | F | F | F | 0.0 |
| correct task + assignee, reply omits vendor name | T | T | T | F | T | 0.0 |
| honest 12-hop path | T | T | T | T | T | 1.0 |
