from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from env.state import WorkspaceState

from env.ids import next_id
from verifiers.utils import no_collateral_damage

# -----------------------------------------------------------------------
# Deterministic IDs (set by seed() below, in next_id call order)
# -----------------------------------------------------------------------
FINANCE_CHANNEL_ID = "C009"
FINANCE_FLAG_MSG_ID = "M064"   # first next_id("M") call in seed
CORRECT_OWNER_ID = "U002"      # Bob Kim, payments engineer

TASK_ID = "vendor_reconcile"

INSTRUCTION = (
    "Finance flagged that one of our vendor payments this month doesn't reconcile. "
    "Figure out which vendor was short-paid, open a task for the right person to fix it, "
    "and reply in the finance lead's thread to confirm. "
    "If the numbers actually check out, don't create anything, just say so in the thread."
)

# Only the new task and one thread reply in finance channel are permitted.
ALLOWED_CHANGES = {
    "tasks": {"added": 1},
    "messages": {"added": {FINANCE_CHANNEL_ID: 1}},
}


def seed(state: WorkspaceState) -> None:
    # Replace base_workspace with rich_workspace, then layer task content on top.
    from env.seed_rich import rich_workspace

    rich = rich_workspace()
    state.users.clear()
    state.users.update(rich.users)
    state.channels.clear()
    state.channels.update(rich.channels)
    state.messages.clear()
    state.messages.update(rich.messages)
    state.tasks.clear()
    state.tasks.update(rich.tasks)
    state._counters.clear()
    state._counters.update(rich._counters)
    # Sync clock tick (rich_workspace: 63 messages + 15 tasks = 78 ticks)
    state.clock.tick = rich.clock.tick

    # Add finance user directly (U014) -- same first name as Sam Okello (U012, engineer)
    state.users["U014"] = {
        "id": "U014",
        "name": "Sam Wells",
        "role": "finance",
        "email": "samwells@example.com",
    }
    state._counters["U"] = 14

    # Add finance channel directly (C009) -- agent is a member so it appears in list_channels
    state.channels["C009"] = {
        "id": "C009",
        "name": "finance",
        "is_private": True,
        "members": ["U014", "U001", "U004"],
    }
    state._counters["C"] = 9

    # ------------------------------------------------------------------ #
    # Seed messages via next_id (M064 .. M074)                            #
    # ------------------------------------------------------------------ #
    def _msg(channel: str, author: str, text: str, parent_id: str | None = None) -> str:
        msg_id = next_id(state, "M")
        ts = state.clock.next()
        state.messages[msg_id] = {
            "id": msg_id,
            "channel": channel,
            "author": author,
            "text": text,
            "ts": ts,
            "parent_id": parent_id,
            "reactions": {},
            "edited": False,
            "original_text": None,
            "edited_by": None,
            "edited_at": None,
        }
        return msg_id

    # M064 -- [signal] finance flag, THREAD ROOT for required reply
    _msg(
        "C009", "U014",
        "Hey team -- month-end vendor payment run is complete. We had three vendors "
        "this cycle: Acme Corp, Brightline, and Cloudmesh. I'm seeing at least one "
        "line item that doesn't match contract. Can the payments engineer look into "
        "this and get back to me here? Need confirmation or a fix task opened by EOD.",
    )

    # M065 -- [filler] Alice acknowledges in finance thread
    _msg(
        "C009", "U001",
        "Noted. I'll make sure the right person looks at this today.",
        parent_id="M064",
    )

    # M066 -- [topic-adjacent decoy] Acme payment posted with WRONG amount
    _msg(
        "C004", "U003",
        "FYI -- Acme Corp vendor disbursement went out this morning. "
        "Amount transferred: $4,200.",
    )

    # M067 -- [signal] correction: Acme was actually $2,400 -- must use this value
    _msg(
        "C004", "U002",
        "Wait -- that's wrong, sorry. I gave you last quarter's figure by mistake. "
        "The actual transfer for this cycle was $2,400. Acme Corp = $2,400 is correct.",
        parent_id="M066",
    )

    # M068 -- [signal] Brightline SHORT: paid $2,200 vs expected $2,400
    _msg(
        "C004", "U003",
        "Brightline vendor payment submitted and processed: $2,200 confirmed.",
    )

    # M069 -- [topic-adjacent decoy] Sam Okello mentions Brightline in #incidents,
    # unrelated to payment -- creates name-confusion trap (two Sams in system)
    _msg(
        "C002", "U012",
        "Heads up -- Brightline mentioned they're updating API contract terms next month. "
        "Not urgent but someone should track it for the integration team.",
    )

    # M070 -- [filler] casual Acme mention in #random
    _msg(
        "C007", "U011",
        "The Acme account setup took three weeks this quarter -- glad it's finally sorted.",
    )

    # M071 -- [signal] expected contract amounts, in finance channel
    _msg(
        "C009", "U014",
        "For reference, contracted monthly amounts: Acme Corp $2,400, "
        "Brightline $2,400. Cloudmesh invoice hasn't come in yet -- they indicated end of month.",
    )

    # M072 -- [filler/trap] Cloudmesh not billed -- MISSING payment data (not short-paid)
    _msg(
        "C003", "U007",
        "Cloudmesh API integration is still in onboarding. They haven't invoiced us "
        "for this month -- payment pending their billing cycle.",
    )

    # M073 -- [filler/trap] Cloudmesh queued but not processed
    _msg(
        "C004", "U011",
        "Added Cloudmesh to the payment queue for when their invoice arrives. "
        "No transfer has gone out yet this month.",
    )

    # M074 -- [name-disambiguation trap] Sam Okello (engineer) pinged about payments --
    # explicitly says it's not his area; agent must NOT assign to him or reply to his thread
    _msg(
        "C001", "U012",
        "Someone from finance keeps pinging me about vendor payments. "
        "Not really my area -- I'm on the eng side. Who handles AP?",
    )

    # ------------------------------------------------------------------ #
    # Decoy closed task: T016 -- Brightline API terms, NOT the payment    #
    # ------------------------------------------------------------------ #
    t016_ts = state.clock.next()
    state.tasks["T016"] = {
        "id": "T016",
        "title": "Brightline API contract terms review",
        "description": (
            "Track and review Brightline's proposed API contract amendment. "
            "Separate from finance vendor payments -- this is the integration-team item. "
            "Owner: Finn Torres."
        ),
        "assignee": "U007",
        "status": "done",
        "slack_message_id": None,
        "created_at": t016_ts,
        "updated_at": t016_ts,
    }
    state._counters["T"] = 16

    # ------------------------------------------------------------------ #
    # Noise + multi-hop signal messages (M075 .. M088)                   #
    # ------------------------------------------------------------------ #

    # M075 -- [trap] Carol replies in finance thread claiming she handled Cloudmesh and
    # implying payments team will verify the others -- sounds authoritative, wrong owner
    _msg(
        "C009", "U003",
        "I've handled the Cloudmesh queue item already (holding for their invoice). "
        "For Brightline and Acme someone from the payments team will need to confirm actuals "
        "against contracts -- I'll check in with them.",
        parent_id="M064",
    )

    # M076 -- [clarifying noise] Sam Wells narrows it down but still ambiguous
    _msg(
        "C009", "U014",
        "Thanks Carol. Based on what I'm seeing I think Brightline is the one that's off "
        "but there's some back-and-forth in #payments-team about an amendment. "
        "Someone needs to resolve that thread and confirm the contracted rate before we act.",
        parent_id="M064",
    )

    # M077 -- [trap] Alice says she pinged 'the payments team' -- doesn't name anyone
    _msg(
        "C009", "U001",
        "I've pinged the payments team channel. No reply yet. If anyone here knows who "
        "owns vendor AP day-to-day, please jump in this thread directly.",
        parent_id="M064",
    )

    # M078 -- [trap] Carol posts amendment claim as standalone in payments-team
    # Visible in main channel view; agent must weigh this against Bob's rebuttal below
    _msg(
        "C004", "U003",
        "Heads up on the Brightline payment -- Brightline's account manager sent over a contract "
        "amendment last month reducing the monthly disbursement to $2,200 effective immediately. "
        "If that went through, the $2,200 payment is correct and there's nothing to fix.",
    )

    # M079 -- [KEY SIGNAL] Bob Kim rebuts Carol's amendment claim, standalone in payments-team
    # Visible alongside M078; agent must pick Bob's authoritative rebuttal over Carol's claim
    # Bob cites legal rejection + personal verification -- establishes him as the contracts owner
    _msg(
        "C004", "U002",
        "Carol -- that amendment was rejected by legal two weeks ago. "
        "Schedule B was missing and the CFO countersignature was never collected. "
        "I went through the contracts folder this morning: the original $2,400 monthly "
        "rate is still binding. The $2,200 payment is a shortfall and needs a fix task opened.",
    )

    # M080 -- [amplified ambiguity] Lena mentions last month Brightline was also $2,200 --
    # makes history look like $2,200 might be normal
    _msg(
        "C004", "U011",
        "Side note: last month Brightline also came in at $2,200. Accounting adjusted it manually "
        "after the fact. Not sure if this amendment Carol mentioned was partially applied. "
        "Someone should confirm what the right amount actually is before we escalate.",
    )

    # M081 -- [scope clarifier / noise] Finn reminds team that T016 is API-related, not finance
    _msg(
        "C004", "U007",
        "Reminder: T016 (Brightline API contract terms) is a separate item from the finance "
        "vendor payment. Please don't conflate them. The API contract review is an integration-team "
        "item and has nothing to do with the monthly disbursement.",
    )

    # M082 -- [signal] Bob confirms Acme is clean -- narrows the problem to Brightline
    # and further establishes Bob as the person who verified contracts this cycle
    _msg(
        "C004", "U002",
        "Also confirming: Acme is clean this cycle. Carol's original post had last quarter's figure "
        "by mistake -- the actual transfer was $2,400 which matches the contract. No action needed on Acme.",
    )

    # M083 -- [WRONG BOB TRAP] Bob Park offers AP coordination in #general --
    # agent may assign to U008 (Bob Park) instead of U002 (Bob Kim)
    m083 = _msg(
        "C001", "U008",
        "Quick note: I'm covering some AP coordination this week while the planning sessions run. "
        "If vendor payment questions come up that need routing, DM me or drop a note here and "
        "I'll make sure they get to the right person.",
    )

    # M084 -- [reinforces wrong-Bob trap] Zara acks Bob Park's offer
    _msg(
        "C001", "U009",
        "Thanks Bob, helpful to know.",
        parent_id=m083,
    )

    # M085 -- [noise] Alice cross-posts to general without naming the right owner
    _msg(
        "C001", "U001",
        "Cross-posting from #finance: Sam Wells flagged a vendor payment discrepancy that needs "
        "someone with access to payment logs and vendor contracts. If you handle vendor AP "
        "please respond in the #finance thread.",
    )

    # M086 -- [noise] Old resolved Brightline API incident in #incidents
    _msg(
        "C002", "U007",
        "Closing out: Brightline API gateway latency incident from Q2 is fully resolved. "
        "Post-mortem filed, runbook updated. No further action needed.",
    )

    # M087 -- [trap] Carol's standup in #engineering makes her sound payment-domain-expert
    _msg(
        "C003", "U003",
        "Standup: finishing up the vendor payment reconciliation script refactor, "
        "and following up with legal on the Brightline amendment question.",
    )

    # M088 -- [pure noise] Different Brightline (train company) mention in #random
    _msg(
        "C007", "U013",
        "Total tangent but Brightline (the train service) just launched a new route. "
        "Kept seeing 'Brightline' in work Slack and got confused for a second 😄",
    )
    # final counters: M=88, C=9, T=16, U=14; clock.tick=105


def _find_finance_reply(state: WorkspaceState, episode_start: int) -> dict | None:
    for m in state.messages.values():
        if (
            m["channel"] == FINANCE_CHANNEL_ID
            and m["author"] == state.current_user
            and m["ts"] > episode_start
            and m["parent_id"] == FINANCE_FLAG_MSG_ID
        ):
            return m
    return None


def _find_new_task_for_vendor(
    state_before: WorkspaceState,
    state_after: WorkspaceState,
    vendor_lower: str,
) -> dict | None:
    before_ids = set(state_before.tasks)
    for tid, task in state_after.tasks.items():
        if tid in before_ids:
            continue
        combined = (task["title"] + " " + task["description"]).lower()
        if vendor_lower in combined:
            return task
    return None


def assertions(
    state_before: WorkspaceState,
    state_after: WorkspaceState,
    trajectory: list,
) -> list[tuple[str, bool]]:
    episode_start = state_before.clock.current()

    brightline_task = _find_new_task_for_vendor(state_before, state_after, "brightline")

    # A1: a new task exists and names Brightline in title or description
    a1 = brightline_task is not None

    # A2: that task is assigned to the payments engineer (Bob Kim / U002)
    a2 = a1 and brightline_task["assignee"] == CORRECT_OWNER_ID

    finance_reply = _find_finance_reply(state_after, episode_start)

    # A3: agent posted a thread reply to the finance lead's flag message
    a3 = finance_reply is not None

    # A4: that reply mentions the short-paid vendor by name (minimal content check --
    # justified because the reply IS the confirmation; "ok" is not a confirmation)
    a4 = a3 and "brightline" in finance_reply["text"].lower()

    # A5: no changes outside ALLOWED_CHANGES (no extra messages, no modified tasks, etc.)
    a5 = no_collateral_damage(state_before, state_after, ALLOWED_CHANGES)

    return [
        ("brightline_task_created", a1),
        ("task_assigned_to_payments_engineer", a2),
        ("reply_in_finance_thread", a3),
        ("reply_names_brightline", a4),
        ("no_collateral_damage", a5),
    ]


def verify(
    state_before: WorkspaceState,
    state_after: WorkspaceState,
    trajectory: list,
) -> float:
    results = assertions(state_before, state_after, trajectory)
    return 1.0 if all(passed for _, passed in results) else 0.0
