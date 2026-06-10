# dev_stages.md

Build plan for Claude Code. One single agent across sessions, human gate between sessions. After each coding session the agent switches to auditor mode and runs the checklist for that stage before reporting done.

Legend: `[AUTO]` agent proceeds without asking. `[PROMPT]` needs a user decision/action first. `[PLACEHOLDER]` temporary, expected to change, do not over-engineer.

Test goals are written as verifiable conditions. The agent generates the actual tests from them.

---

## Stage 0 — Contract freeze (human, no code)

The five docs in `docs/` are the frozen contract. The agent reads all five before writing anything and treats `system_design.md` field names, signatures, and the verifier interface as immutable. Deviations require a `changelog.md` entry approved by the human.

Clarification questions before Stage 1:

- Is the Python entrypoint a package (`apollo/`) or flat module layout? (default: package per the layout in system_design.md)
- Confirm `current_user == "U004"` and that the agent user is seeded by `base_workspace()`.

---

## Stage 1 — Env core: state, clock, ids

Build `[AUTO]`:

- `env/clock.py` FrozenClock (`tick`, `next`, `current`, `to_datetime`)
- `env/ids.py` `next_id(state, prefix)` -> `f"{prefix}{n:03d}"`, bumps `_counters`
- `env/state.py` WorkspaceState + entity TypedDicts per system_design.md

Test goals (passing = all true):

- `FrozenClock().next()` returns strictly increasing ints starting at 1; `current()` does not increment
- `to_datetime(0)` equals the base ISO; `to_datetime(2)` equals base + 2\*tick_seconds
- `next_id(state,"M")` on a state with `_counters["M"]==4` returns `"M005"` and sets counter to 5
- a WorkspaceState round-trips through `copy.deepcopy` with no shared mutable refs (mutating the copy's `messages` does not touch the original)
- grep: no `datetime.now(`, no `import random`, no `import uuid` in `env/`

Auditor checklist: ids are prefix + zero-padded counter; clock has no wall-clock call; state is deep-copyable.

Human gate: none yet (continues into Stage 2 in the same session).

Clarification questions before starting:

- Should `ts` be the raw tick int (per system_design.md) confirmed, or a datetime string? (default: tick int)

---

## Stage 2 — Services + base seed

Build `[AUTO]`:

- `env/slack.py` SlackService: all 10 tools with exact return/error shapes from system_design.md
- `env/task_service.py` TaskService: all 6 tools with exact return/error shapes
- `env/seed.py` `base_workspace()` building the channel map from tasks_spec.md and the base density (5 users, 5 channels, background messages)

`[PLACEHOLDER]`: exact background message texts, persona names/emails, exact non-load-bearing message ids. The channel map (C001-C005, names, privacy, membership) is fixed, not placeholder.

Test goals:

- read tools never mutate: snapshot state, call every read tool, assert `diff_keys` empty
- every domain error returns `{"ok": False, "error": <code>}` with a code from the closed set; no exceptions raised for domain errors
- `post_message` to a nonexistent channel -> `channel_not_found`; to C005 (non-member) -> `not_authorized`
- `update_message` on a message authored by another user -> `not_authorized`
- `add_reaction` on missing message -> `message_not_found`; valid call adds `current_user` to the emoji set
- `create_task` with unknown assignee -> `user_not_found`; valid call sets `created_at == updated_at == ` the tick from `clock.next()`
- `update_task(status="bogus")` -> `invalid_status`
- `get_channel_messages` returns top-level only (no message with `parent_id` present), page size <= 10, returns `next_cursor` when more remain; `get_thread(m)` returns exactly the children of `m`
- `search_messages` is case-insensitive, scoped to member channels (a term only in C005 returns nothing), recency-ordered, capped at 10
- `base_workspace()` is deterministic: two calls produce equal states (deep equality)

Auditor checklist: no `random`/`time`/`uuid` imports; all errors return dicts; search scope excludes non-member channels; pagination present on both paginated tools.

Human gate: none (continues into Stage 3).

Clarification questions before starting:

- For `get_channel_messages`/`post_message` on a non-member channel, confirm `not_authorized` vs `channel_not_found` (default: `not_authorized`; existence is not hidden)
- Cursor format: opaque string encoding offset, confirmed acceptable?

---

## Stage 3 — Harbor adapter + trajectory logger + smoke test

Build `[AUTO]`:

- `harbor/trajectory.py` ToolCall schema + TrajectoryLogger (records tool, args, result, optional reasoning)
- `harbor/adapter.py` ApolloHarborTask (setup snapshots before-state and registers bound tools as callables + JSON schemas; run logs every call; score calls `task.verify`)
- `tasks/registry.py` discovery of task modules
- `tasks/smoke_test.py` per tasks_spec.md (instruction, no extra seed, binary verifier, ALLOWED_CHANGES)
- `verifiers/utils.py` `field_equals`, `message_exists_in`, `diff_keys`, `no_collateral_damage`, `any_tool_called_before`

`[PLACEHOLDER]`: smoke test instruction wording.

Test goals:

- registry discovers `smoke_test` and exposes its required symbols
- adapter `setup()` produces `state_before` that is a deep copy (independent) of the live state
- a scripted correct trajectory (`list_channels` then `post_message("C001","env check ok")`) scores `1.0`
- a scripted trajectory that posts to C001 AND creates a stray task scores `0.0` (collateral damage)
- an empty trajectory scores `0.0`
- `no_collateral_damage` returns False when a non-allowed object changes, True when only allowed changes occur
- `diff_keys` correctly reports a reaction-set change as a `messages` modification
- isolation: the tool set registered for the agent contains ONLY Slack/Task tools; assert no registered tool name references verify/score/allowed/expected/ground-truth, and `WorkspaceState` exposes no attribute reaching `ALLOWED_CHANGES`, the verifier, the snapshot, the trajectory, or the score

Auditor checklist: verifier reads state directly (not via tools); smoke verifier names the specific channel C001; logger captures every tool call including failed ones; registered agent tools are Slack/Task only (grep the tool registration, confirm no grading symbol is reachable from a tool or from `WorkspaceState`).

Human gate (SESSION 1 BOUNDARY): `[PROMPT]` human runs the smoke test end to end through the Harbor adapter with a real agent loop (cheap model is fine). Green (score 1.0 on a competent run, 0.0 on the stray-task run) = proceed. Red = stop, fix wiring before any task work.

---

## Stage 4 — Baseline task (saturated)

Build `[AUTO]`:

- `tasks/baseline_slack.py`: seed delta (pagination-forcing #incidents messages + target `M_DBLATENCY` + decoys), instruction, 2-assertion verifier, ALLOWED_CHANGES per tasks_spec.md

`[PLACEHOLDER]`: target message id once chosen becomes fixed; decoy contents; exact message texts; the ~70% expectation.

Test goals:

- scripted correct trajectory (reach page 2 / search, then react to `M_DBLATENCY`) scores `1.0`
- scripted trajectory that reacts to a decoy scores `0.0` (assertion 1 false + collateral damage)
- target message is NOT on page 1 of `get_channel_messages("C002")` (pagination is actually required)
- seed is deterministic (two builds equal)

Auditor checklist: assertion names the exact target id; ALLOWED_CHANGES restricts to the target's reaction set only.

Human gate: none (continue into Stage 5).

Clarification questions before starting:

- Which seeded user is the engineer author of the target, and is the target guaranteed off page 1? (confirm against base density)

---

## Stage 5 — Primary task (unsaturated) + desk-check

Build `[AUTO]`:

- `tasks/primary_xservice.py`: seed delta (M007 + thread replies + manager decoy; T003 linked via `slack_message_id="M007"`; 6-8 tasks with distractor links), instruction, 3-assertion verifier, ALLOWED_CHANGES per tasks_spec.md
- `[AUTO]` run the reward-hacking desk-check from verifier_design.md and record the lazy-trajectory score in the build report

`[PLACEHOLDER]`: all message/task texts; decoy specifics; the 25-45% expectation. Load-bearing and fixed: `M007`, `T003`, the `slack_message_id` link, channel C002, the three assertions.

Test goals:

- scripted honest 5-call trajectory scores `1.0`
- lazy desk-check trajectory (`update_task("T001",...)` + post to C002) scores exactly `0.33`
- trajectory that posts to C002 but touches no task scores `0.67` (fails only assertion 1; the single C002 post is inside the allowed message budget so it is not collateral damage); trajectory that updates T003 but does not post scores `0.67` (fails only assertion 2)
- exactly one task has `slack_message_id == "M007"` (`search_tasks(slack_message_id="M007")` returns only T003)
- `search_messages("payments outage")` returns more than one candidate (disambiguation is real)
- seed deterministic

Auditor checklist: assertion 1 names T003 exactly; assertion 2 requires `parent_id is None` and `author == current_user` and `ts > episode_start`; ALLOWED_CHANGES allows only `T003.{status,updated_at}` and one new C002 message; desk-check recorded < 1.0.

Human gate (SESSION 2 BOUNDARY): `[PROMPT]` human supplies OpenRouter access and runs >= 5 trials of baseline + primary against Gemma 4 26B. Check: baseline lands high (~60-70%), primary lands 25-45%, neither is 0% (unfair) or 100% (saturated). If primary is 0%, `[PROMPT]` revisit hop count / seed noise before proceeding. If saturated, add a dependent hop or tighten pagination.

`[PROMPT]` Optional: build Task 3b infeasibility variant only if the human approves spending the time.

---

## Stage 6 — Trials, reference run, analysis scaffolding

Build `[AUTO]`:

- `runner/run_trials.py`: run N trials per task (fresh setup() per trial, no state carryover); per trial log every tool call/args/result/reasoning, final score, and per-assertion booleans; persist each trial as a transcript to transcripts/ (gitignored) named <task>_<model>\_trial<N>_<UTC>.json; aggregate pass^k
- `[AUTO]` one reference run with a stronger model (Claude Sonnet or GPT-4o), reference trajectory only, not a benchmark sweep

`[PLACEHOLDER]`: N (default 5), choice of reference model.

Test goals:

- runner emits, per trial, a structured record containing tool calls, per-assertion booleans, and total score
- aggregate report shows score distribution and pass^k across trials
- a forced-failure stub trajectory is logged with the failing assertion identified
- each trial writes a transcript file to transcripts/ with the documented name pattern, containing instruction, ordered tool calls/args/results, final score, and the 3 per-assertion booleans

Human gate (SESSION 3 BOUNDARY): `[PROMPT]` human reads trajectories, confirms the failure pattern matches a hypothesis in Section G (context drop at the cross-service hop is the lead hypothesis), then writes the loss analysis and client update. The agent may draft `[PROMPT]`, but the human finalizes wording (no AI slop, no em dashes).

---

## Cross-stage rules

- Never edit `env/` to make a task pass. If a task needs something the env cannot express, stop and raise it as a `changelog.md` proposal.
- Any deviation from these docs: append to `changelog.md` (date, stage, change, reason, approved by) and get human approval before continuing.
- After every stage, run that stage's auditor checklist and paste the evidence (grep output, test names) into the build report.
