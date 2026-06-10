# Apollo RL Environment

Harbor-compatible RL env: two simulated services (Slack-like + task manager), unsaturated tasks targeting Gemma 4 26B, state-diff verifiers. Python 3.11+. Solo build, staged, human gate between stages.

## Commands
- Install: `uv sync`
- Test: `uv run pytest -q`
- Run one task: `uv run python -m runner.run_trials --task <id> --trials 1`

## Layout and the one hard rule
- `env/` services + state + seeding. STABLE. Never edit `env/` to make a task pass.
- `tasks/` one file per task (`TASK_ID`, `INSTRUCTION`, `ALLOWED_CHANGES`, `seed`, `verify`). Adding a task = adding one file.
- `verifiers/utils.py` shared + stable. `harbor/` adapter + trajectory, stable.
- `env/` never imports `tasks/`. `tasks/` import `env/` and `verifiers/`.

## Invariants (hold every turn)
- Determinism: no `datetime.now()`, no `random`, no `uuid`, no `time` in `env/` or seeds. Two builds of a seed must be deep-equal.
- IDs: counter-based `prefix + zero-padded int` (M001, C001, T001, U001) via `next_id`.
- Clock: FrozenClock only; `ts` is the monotonic tick int.
- Tools: every tool returns a dict with `ok`. Domain failure is `{"ok": False, "error": <code>}` from the closed code set. Never raise for domain errors.
- Verifier signature is fixed: `verify(state_before, state_after, trajectory) -> float` in `[0,1]`. Reads state directly, not via tools. `episode_start = state_before.clock.current()`.
- Isolation: the agent gets only the Slack/Task tools. Never expose the verifier, `ALLOWED_CHANGES`, ground-truth/expected state, `episode_start`, the before-snapshot, the trajectory, or the score to the agent (not as a tool, a tool return, or a field on `WorkspaceState`). Instruction text never names target IDs.

## Where details live (read on demand, do NOT preload all)
Open only the doc for the current stage, then rely on the code:
- schema + signatures: `docs/system_design.md`
- task specs + assertions: `docs/tasks_spec.md`
- verifier utils + normalization + exploit desk-check: `docs/verifier_design.md`
- build order + gates + test goals: `docs/dev_stages.md`

## Workflow
- Follow `docs/dev_stages.md` stage by stage. Commit at each stage boundary.
- After each stage, run that stage's auditor checklist and paste evidence (grep output + test names) into the report.
- Do not deviate from the docs. If a doc is wrong or blocking, stop and propose a `docs/changelog.md` entry (date, stage, change, reason, approved by) and wait for approval.
- Ask the stage's listed clarification questions before starting it if anything is ambiguous.

## Git
- NEVER run git add, git commit, git push, or any git write operation. No exceptions. Instead, prompt the user to run the command themselves.

## Style
- Concise. No em dashes anywhere (code comments, commit messages, prose).
- Write tests as verifiable conditions, not prose.
