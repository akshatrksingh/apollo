from __future__ import annotations

from env.clock import FrozenClock
from env.state import WorkspaceState


def rich_workspace() -> WorkspaceState:
    clock = FrozenClock()

    # 12 human users + 1 bot (U004). Deliberate near-duplicates:
    #   Bob Kim (U002) vs Bob Park (U008) -- same first name, different surname
    #   Maya Patel (U006) vs Maya Rao (U010) -- same first name, different surname
    users = {
        "U001": {"id": "U001", "name": "Alice Chen",   "role": "manager",  "email": "alice@example.com"},
        "U002": {"id": "U002", "name": "Bob Kim",      "role": "engineer", "email": "bobk@example.com"},
        "U003": {"id": "U003", "name": "Carol Diaz",   "role": "engineer", "email": "carol@example.com"},
        "U004": {"id": "U004", "name": "Agent",        "role": "bot",      "email": "agent@example.com"},
        "U005": {"id": "U005", "name": "Dave Park",    "role": "manager",  "email": "dave@example.com"},
        "U006": {"id": "U006", "name": "Maya Patel",   "role": "designer", "email": "mayap@example.com"},
        "U007": {"id": "U007", "name": "Finn Torres",  "role": "engineer", "email": "finn@example.com"},
        "U008": {"id": "U008", "name": "Bob Park",     "role": "engineer", "email": "bobpa@example.com"},
        "U009": {"id": "U009", "name": "Zara Osei",    "role": "manager",  "email": "zara@example.com"},
        "U010": {"id": "U010", "name": "Maya Rao",     "role": "designer", "email": "mayar@example.com"},
        "U011": {"id": "U011", "name": "Lena Vogel",   "role": "engineer", "email": "lena@example.com"},
        "U012": {"id": "U012", "name": "Sam Okello",   "role": "engineer", "email": "sam@example.com"},
        "U013": {"id": "U013", "name": "Priya Nair",   "role": "designer", "email": "priya@example.com"},
    }

    # 8 channels. Agent (U004) is NOT in C005 (leadership) or C008 (design-private).
    channels = {
        "C001": {"id": "C001", "name": "general",       "is_private": False,
                 "members": ["U001","U002","U003","U004","U005","U006","U007","U008","U009","U010","U011","U012","U013"]},
        "C002": {"id": "C002", "name": "incidents",     "is_private": False,
                 "members": ["U001","U002","U003","U004","U005","U007","U008","U011","U012"]},
        "C003": {"id": "C003", "name": "engineering",   "is_private": False,
                 "members": ["U001","U002","U003","U004","U007","U008","U011","U012"]},
        "C004": {"id": "C004", "name": "payments-team", "is_private": True,
                 "members": ["U002","U003","U004","U007","U011"]},
        "C005": {"id": "C005", "name": "leadership",    "is_private": True,
                 "members": ["U001","U005","U009"]},
        "C006": {"id": "C006", "name": "on-call",       "is_private": False,
                 "members": ["U001","U002","U003","U004","U007","U008","U011","U012"]},
        "C007": {"id": "C007", "name": "random",        "is_private": False,
                 "members": ["U001","U002","U003","U004","U005","U006","U007","U008","U010","U011","U012","U013"]},
        "C008": {"id": "C008", "name": "design",        "is_private": True,
                 "members": ["U006","U010","U013","U001"]},
    }

    messages: dict = {}
    tasks: dict = {}

    def _msg(msg_id: str, channel: str, author: str, text: str, parent_id=None, reactions=None) -> None:
        ts = clock.next()
        messages[msg_id] = {
            "id": msg_id,
            "channel": channel,
            "author": author,
            "text": text,
            "ts": ts,
            "parent_id": parent_id,
            "reactions": reactions or {},
            "edited": False,
            "original_text": None,
            "edited_by": None,
            "edited_at": None,
        }

    # ------------------------------------------------------------------ #
    # C001 #general  -- mix of signal, decoys, filler                     #
    # ------------------------------------------------------------------ #

    # [filler] week-ago casual chatter (ticks 1-5)
    _msg("M001", "C001", "U006", "Happy Friday everyone! Big plans for the weekend?")
    _msg("M002", "C001", "U012", "Anyone up for lunch around noon?")
    _msg("M003", "C001", "U008", "Out of office Monday, back Tuesday.")
    _msg("M004", "C001", "U013", "standup reminder: 9:30am in #engineering")
    _msg("M005", "C001", "U011", "Can we sync later about the deploy pipeline?")

    # [filler] mid-week chatter (ticks 6-9)
    _msg("M006", "C001", "U003", "Just merged the onboarding doc updates.")
    _msg("M007", "C001", "U007", "Thanks Carol! I was waiting for that.")
    _msg("M008", "C001", "U005", "Reminder: Q3 planning doc due EOW.")
    _msg("M009", "C001", "U002", "Will get to it after the incident call.")

    # [signal] new joiner announcement -- recent (tick 10)
    _msg("M010", "C001", "U001", "Welcome Priya and Lena -- both joining the design and eng teams respectively!", reactions={"wave": {"U002","U003","U005","U006","U007","U010"}})

    # ------------------------------------------------------------------ #
    # C002 #incidents                                                       #
    # ------------------------------------------------------------------ #

    # [topic-adjacent decoy] OLD resolved payments latency incident (ticks 11-15)
    _msg("M011", "C002", "U001", "INC-007 (resolved): Payment gateway latency spike last Tuesday -- p99 hit 4s. Root cause was misconfigured connection pool. Fixed and closed.")
    _msg("M012", "C002", "U003", "INC-007: Post-mortem filed. Action item: add connection pool size to runbook.", parent_id="M011")
    _msg("M013", "C002", "U002", "INC-007: Runbook updated.", parent_id="M011")

    # [filler] unrelated noise
    _msg("M014", "C002", "U008", "Anyone else seeing flaky tests in CI this morning?")
    _msg("M015", "C002", "U011", "Yeah the test_payment_reconcile suite has been flaky for a week. Not blocking anything though.")

    # [signal] ACTIVE payments latency incident -- NEW (ticks 16-21)
    _msg("M016", "C002", "U007", "INC-012 ACTIVE: Checkout payments latency spiking again -- p99 now 6s, error rate 2%. Started ~30m ago.")
    _msg("M017", "C002", "U002", "INC-012: On it. Checking DB query times.", parent_id="M016")
    _msg("M018", "C002", "U011", "INC-012: I see the same in the metrics. Looks like the payments-svc index query.", parent_id="M016")
    _msg("M019", "C002", "U007", "INC-012: Confirmed -- missing index on transactions table after last migration.", parent_id="M016")
    _msg("M020", "C002", "U001", "INC-012: Who can own the fix? Need a task created and index added ASAP.", parent_id="M016", reactions={"eyes": {"U002","U011","U012"}})
    _msg("M021", "C002", "U004", "INC-012: Acknowledged, monitoring metrics.", parent_id="M016")

    # [topic-adjacent decoy] different latency issue in auth (ticks 22-24)
    _msg("M022", "C002", "U003", "Heads up: auth service latency up slightly (p50 ~120ms vs normal 80ms). Not incident-level, just watching it.")
    _msg("M023", "C002", "U012", "Probably the token validation cache miss rate. I'll dig in.", parent_id="M022")
    _msg("M024", "C002", "U003", "Resolved -- cache was cold after last restart. Back to normal.", parent_id="M022")

    # ------------------------------------------------------------------ #
    # C003 #engineering                                                     #
    # ------------------------------------------------------------------ #

    # [filler] PR chatter (ticks 25-28)
    _msg("M025", "C003", "U008", "PR #88 up: refactor retry logic to use exponential backoff.")
    _msg("M026", "C003", "U011", "Left a couple nits on PR #88, looks solid overall.", parent_id="M025")
    _msg("M027", "C003", "U002", "Approved PR #88. Merging after CI.", parent_id="M025")
    _msg("M028", "C003", "U008", "PR #88 merged.", parent_id="M025")

    # [signal] deploy freeze announcement (ticks 29-30)
    _msg("M029", "C003", "U001", "Deploy freeze starts Thursday EOD through Sunday for the mobile release. Non-critical changes should wait.")
    _msg("M030", "C003", "U007", "Got it. Will hold my payments-svc index migration PR until Monday.", parent_id="M029")

    # [topic-adjacent decoy] old 'migration' mention -- resolved (tick 31)
    _msg("M031", "C003", "U012", "The user-schema migration from last sprint landed cleanly, all green.")

    # [signal] on-call rotation reminder (tick 32)
    _msg("M032", "C003", "U009", "On-call rotation: Bob Kim is primary this week, Finn is secondary.")

    # [filler] standup (ticks 33-35)
    _msg("M033", "C003", "U003", "Standup: finished auth rate-limit refactor. Starting on notification service next.")
    _msg("M034", "C003", "U011", "Standup: reviewing PRs today, then back on the reconciliation alerting task.")
    _msg("M035", "C003", "U007", "Standup: INC-012 investigation, then payments-svc index PR.")

    # ------------------------------------------------------------------ #
    # C004 #payments-team                                                  #
    # ------------------------------------------------------------------ #

    # [topic-adjacent decoy] old Stripe webhook issue -- resolved (ticks 36-38)
    _msg("M036", "C004", "U002", "Stripe webhook idempotency issue from last month: fully resolved. New dedup key logic is live.")
    _msg("M037", "C004", "U011", "Confirmed, no duplicate charge events in the last 7 days.", parent_id="M036")
    _msg("M038", "C004", "U003", "Closing that item off the backlog.", parent_id="M036")

    # [signal] active payments index discussion (ticks 39-42)
    _msg("M039", "C004", "U007", "Created PR #91 for the missing index on transactions.checkout_at. Needs review before merge.")
    _msg("M040", "C004", "U002", "On it -- will review PR #91 this afternoon.", parent_id="M039", reactions={"thumbsup": {"U011","U003"}})
    _msg("M041", "C004", "U011", "I can add the create-index migration script if needed.", parent_id="M039")
    _msg("M042", "C004", "U003", "Do it -- migration + index in one PR is cleaner.", parent_id="M039")

    # [filler] reconciliation chatter (ticks 43-44)
    _msg("M043", "C004", "U003", "Reconciliation job ran clean last night. No mismatches.")
    _msg("M044", "C004", "U002", "Good. Let's keep the alerting task on the board for this week anyway.")

    # ------------------------------------------------------------------ #
    # C005 #leadership  (agent NOT a member)                              #
    # ------------------------------------------------------------------ #

    # [filler/signal leadership] (ticks 45-48)
    _msg("M045", "C005", "U001", "Q3 budget locked. Headcount +2 eng, +1 design.")
    _msg("M046", "C005", "U005", "I will draft the offer letters by EOW.", parent_id="M045")
    _msg("M047", "C005", "U009", "Roadmap v2 deck ready for board review Thursday.")
    _msg("M048", "C005", "U001", "Good. Let's do a dry run Wednesday 4pm.")

    # ------------------------------------------------------------------ #
    # C006 #on-call                                                        #
    # ------------------------------------------------------------------ #

    # [signal] active alert requiring acknowledgment (ticks 49-52)
    _msg("M049", "C006", "U001", "PagerDuty alert: payments-svc error rate exceeded 1% threshold. Incident INC-012 opened.")
    _msg("M050", "C006", "U002", "Acknowledged. Working the issue with Finn.", parent_id="M049", reactions={"white_check_mark": {"U001","U007"}})
    _msg("M051", "C006", "U007", "Runbook link: https://internal/runbooks/payments-latency", parent_id="M049")
    _msg("M052", "C006", "U011", "Disk usage on prod-db-01 at 78%. Keeping watch -- not a page yet.", reactions={"eyes": {"U002","U001"}})

    # [topic-adjacent decoy] old resolved alert (ticks 53-54)
    _msg("M053", "C006", "U012", "Last week's disk alert on prod-db-02 was resolved. Added automated cleanup job.")
    _msg("M054", "C006", "U008", "Confirmed -- disk now at 55% and stable.", parent_id="M053")

    # ------------------------------------------------------------------ #
    # C007 #random                                                         #
    # ------------------------------------------------------------------ #

    # [filler] pure casual (ticks 55-60)
    _msg("M055", "C007", "U006", "Anyone else watching the Euros? Wild match last night.")
    _msg("M056", "C007", "U013", "Yes!! That last-minute goal was insane.")
    _msg("M057", "C007", "U008", "Off topic but: best coffee spot near the office?", reactions={"coffee": {"U003","U007","U012"}})
    _msg("M058", "C007", "U003", "The place on 3rd St. is great, opens at 7am.")
    _msg("M059", "C007", "U011", "Has anyone tried the new standing desk setup in room 4B?")
    _msg("M060", "C007", "U007", "Just booked it for Friday afternoon.")

    # [filler] half-finished thread (ticks 61-63)
    _msg("M061", "C007", "U002", "Anyone know if we have a VPN policy doc?")
    _msg("M062", "C007", "U012", "I think it's in the Notion wiki but I can't find the link.", parent_id="M061")
    _msg("M063", "C007", "U001", "I'll find it and post here -- might take a day.", parent_id="M061")

    # max message number used = 63

    def _task(task_id: str, title: str, description: str, assignee: str, status: str, slack_message_id=None) -> None:
        ts = clock.next()
        tasks[task_id] = {
            "id": task_id,
            "title": title,
            "description": description,
            "assignee": assignee,
            "status": status,
            "slack_message_id": slack_message_id,
            "created_at": ts,
            "updated_at": ts,
        }

    # [signal] active incident work
    _task("T001", "Add index on transactions.checkout_at", "INC-012: Missing index causing p99 latency spike. Add index in a migration. Blocks T002.", "U007", "in_progress", slack_message_id="M039")
    _task("T002", "Deploy payments-svc index migration", "Deploy PR #91 to production once reviewed. Depends on T001 completing first.", "U002", "blocked", slack_message_id="M040")
    _task("T003", "Resolve INC-012 and file post-mortem", "Write post-mortem for the checkout latency spike. Depends on T002 resolution.", "U001", "todo", slack_message_id="M020")

    # [signal] on-call and alerting
    _task("T004", "Add reconciliation job failure alerting", "Wire payment reconciliation failures to PagerDuty. Continuing from prior sprint.", "U011", "in_progress", slack_message_id="M044")
    _task("T005", "Add disk usage alert for prod-db-01", "Disk at 78%. Add alert threshold at 80% in monitoring. Similar to last sprint's prod-db-02 fix.", "U012", "todo", slack_message_id="M052")

    # [topic-adjacent decoy tasks -- keyword overlap but different scope]
    _task("T006", "Stripe webhook idempotency audit", "Audit all inbound Stripe webhooks for dedup key correctness. Follow-up from resolved incident, not INC-012.", "U003", "done", slack_message_id="M037")
    _task("T007", "Auth rate-limit config refactor", "Move auth rate limit values to config service. Follow-up from old INC. Not related to current latency.", "U003", "in_progress")
    _task("T008", "Payment reconciliation runbook", "Document the reconciliation job manually triggering steps. Separate from alerting task T004.", "U002", "todo")

    # [signal] deploy freeze and PR work
    _task("T009", "Review and merge PR #91 (index migration)", "Review Finn's index PR before deploy freeze Thursday. Linked to INC-012 work.", "U002", "in_progress", slack_message_id="M039")
    _task("T010", "Hold non-critical deploys until Monday", "Enforce deploy freeze Thu EOD through Sunday. See #engineering announcement.", "U001", "todo", slack_message_id="M029")

    # [filler tasks -- unrelated to active incidents]
    _task("T011", "Update onboarding documentation", "Add new tooling section to the onboarding guide. Low priority.", "U006", "todo")
    _task("T012", "Design system component audit", "Catalog all button/form variants across the app for the design system refresh.", "U010", "in_progress")
    _task("T013", "VPN policy documentation", "Write and publish the company VPN usage policy. Requested in #random.", "U001", "todo", slack_message_id="M063")
    _task("T014", "Exponential backoff retry refactor", "Complete retry logic refactor. PR #88 merged; close this task.", "U008", "done", slack_message_id="M028")
    _task("T015", "Token validation cache warm-up", "Add cache pre-warm on auth service restart to avoid cold-cache latency. From #incidents thread.", "U003", "todo", slack_message_id="M024")

    # max task number used = 15

    state = WorkspaceState(
        users=users,
        channels=channels,
        messages=messages,
        tasks=tasks,
        clock=clock,
        current_user="U004",
        counters={"M": 63, "C": 8, "T": 15, "U": 13},
    )

    return state
