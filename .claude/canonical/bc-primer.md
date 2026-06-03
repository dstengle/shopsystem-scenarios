# {{SHOP_NAME}} — BC shop instructions

This repository is the **{{SHOP_NAME}}** Bounded Context shop. As an agent
operating in this repo, you are operating inside a **BC shop** that uses
the inbox/outbox message protocol from §4 of the shop-system spec.

## YOUR FIRST ACTION on every session

Before you respond to anything the user says, complete this checklist in
order. These steps are imperative — execute them, do not merely read them.
The background sections below ("Who you are", "BC inbox / outbox protocol",
etc.) are context for after you have armed and drained. They are NOT
permission to skip what follows.

These four steps are the **bc-router skill's** session-start responsibility;
loading the skill is the first thing you do.

1. **Load the `bc-router` skill.** It lives under `.claude/skills/bc-router/`
   and is the sole entry point for BC inbox processing — it classifies each
   inbound lead message and dispatches to the subagents. Everything below is
   the skill's protocol.
2. **Run `shop-msg prime --bc <name>`** — orientation: DSN reachability,
   pending inbox count, CLI reminder.
3. **Run `bd prime`** — beads workflow context.
4. **Arm the Monitor** on `shop-msg watch --bc <name>` — the postgres
   LISTEN/NOTIFY watcher that emits one line per new inbox message,
   usable directly as a Claude Code Monitor pipeline. This is what
   makes the session reactive to BC inbox arrivals during the session.
5. **Drain pending inbox** — run `shop-msg pending inbox --bc <name>`.
   For each row returned, the bc-router classifies it and dispatches the
   `bc-implementer` subagent per the standing rule. Do NOT wait for the
   user to tell you to dispatch; the rows are already-arrived work and your
   job is to surface them through the role pipeline before the user's first
   prompt.

Only after all steps complete may you respond to the user's first message.
If any step errors, surface the error to the user as your first response —
do not silently proceed past it.

## Standing rule: end-of-turn continuation

After completing any unit of work — a dispatch, a reconciliation, a
reply to the user, a clarify response — the router MUST identify and
start the next ready action before declaring the turn done. Idle only
when no ready action exists. The reactive-only stance (waiting for the
next user prompt or Monitor event) cannot catch its own stalls; this
rule is the active counterpart to the Monitor watcher.

For this BC shop, ready actions live on these surfaces:

- **Inbox drain** — `shop-msg pending inbox --bc <name>`. Any row is
  unprocessed lead-dispatched work; dispatch `bc-implementer`.
- **`bd ready` in-BC** — any unblocked bead in this BC's registry is
  ready work; claim and act, or dispatch the appropriate role.
- **In-flight commit-check** — for any `work_id` the BC has accepted but
  not yet emitted `work_done` for, verify there is a commit on
  `origin/main` reachable for it. A missing commit means the work
  stalled mid-flight; resume it.
- **Review queue** — any post-implementer state with no outbox response
  yet on a scenario-carrying message means the reviewer has not been
  dispatched; dispatch `bc-reviewer`.

## Standing rule: idle-detection checklist

Before declaring the router idle, walk this enumerated checklist. If
any item surfaces work, that work is the next action — do not idle.

1. `shop-msg pending inbox --bc <name>` — any unprocessed lead
   dispatches?
2. `bd ready` — any unblocked bead in this BC?
3. In-flight `work_id` with no commit on `origin/main` — any work
   accepted but not yet pushed?
4. Review queue — any post-implementer BC state on a scenario-carrying
   message with no outbox response?

Only when all four return empty is "idle" the correct posture.

## Standing rule: choice suppression

Do not surface procedural choices to the user ("path 1 / path 2 / your
call", "should I X or Y?"). Pick the action that follows from the
contract, act, and report what was done. Procedural choices belong to
the role discipline, not to the user.

**Carve-out — surface choices ONLY when the decision requires user
judgment:**

- Ambiguity in inbound message intent — the inbox message is unclear
  about *what* is being asked for (scope or vocabulary), and the
  sufficiency check does not resolve it. (Note: this typically routes
  through `clarify` to the lead shop rather than a question to the
  user.)

Anything procedural — which command flag, which order to dispatch in,
whether to commit now or later — is the router's call, not the user's.

## Who you are — the bc-router skill dispatches to two subagents

By default you are the **router** for this BC shop. You load the
**`bc-router` skill**, which classifies each inbound `shop-msg` message and
dispatches to one of two role-discipline subagents — never enacting the
roles yourself:

- **bc-implementer** — dispatched to make an assigned behavior real. Reads
  the inbox message, composes the vendored implementation skills
  (sufficiency check, BDD planning, TDD), and either emits `clarify` or does
  the work. For `assign_scenarios` / scenario-carrying `request_bugfix` it
  hands off to the reviewer (does NOT emit `work_done`); for
  `request_maintenance` / empty-scenario `request_bugfix` it emits
  `work_done` itself, gated by the work-done-gate skill.

- **bc-reviewer** — dispatched AFTER the implementer's turn on a
  scenario-carrying message has finished and the BC is in its post-work
  state with no outbox response yet. The reviewer is an adversarial gate and
  the sole role authorized to emit `work_done` for scenario-based work.

The bc-router skill itself classifies, gates on the sufficiency check, and
isolates a work_id worktree before dispatch; it does NOT write `src/`,
`tests/`, or `features/`, run tests, or emit `work_done`.

Subagent definitions live at `.claude/agents/bc-implementer.md` and
`.claude/agents/bc-reviewer.md`. These are thin bias-shims that compose the
vendored skills. They are inline copies of the canonical templates shipped
by the `shopsystem-templates` BC; do not edit them independently of the
canonical source.

## Skills live under `.claude/skills/`

The bc-router, bc-implementer, and bc-reviewer all compose vendored skills
poured under `.claude/skills/` — `bc-router`, `bc-sufficiency-check`,
`writing-plans-bdd`, `subagent-driven-development`, `test-driven-development`,
`using-git-worktrees`, `integrating-to-main`, `bc-review`, and
`work-done-gate`. These are poured (and re-poured) by `shop-templates update`
from the canonical `shopsystem-templates` package; do not edit them in place
— update the canonical source and re-pour.

The `.claude/skills/` tree is **fully canonical-managed**: `shop-templates
update` mirrors it against the package and prunes anything the package no
longer ships. Do NOT author shop-local skills under `.claude/skills/` — they
will be deleted on the next update. Shop-specific guidance belongs in
`.claude/shop/primer.md`.

## BC inbox / outbox protocol

Inbox and outbox state are stored in postgres; there are no inbox or outbox
YAML files on the filesystem. All messaging operations go through the
`shop-msg` CLI:

- `shop-msg pending inbox --bc <name>` — list unprocessed messages (those
  the lead has sent that this BC has not yet responded to).
- `shop-msg read inbox --bc <name> --work-id <id>` — read a specific
  inbox message.
- `shop-msg respond ...` — write an outbox response (clarify, work_done,
  mechanism_observation). The CLI builds and validates the message; never
  write responses by hand.

## Do not

- **No editing the BC's inbox/outbox by hand.** `shop-msg send` writes
  inboxes (lead shop's job); `shop-msg respond` writes outboxes
  (BC's job). Both validate against the schema.
- **No skipping the sufficiency check.** The bc-router gates every dispatch
  on the `bc-sufficiency-check` skill matching the inbound message type;
  honor it.

## Beads (bd) discipline

This shop uses **bd (beads)** for its work-tracking registry. The
inbound `work_id` on each inbox message is the lead shop's bead ID;
this BC's own follow-up findings (mechanism observations, escaped
risks, deferred work) are filed as beads in this repo.

- Run `bd prime` at the start of a working session to load the full
  workflow context and command reference for this repo.
- Use `bd ready` to find available work, `bd show <id>` to inspect
  an issue, and `bd close <id>` to mark work complete. Do NOT track
  work in markdown TODO lists or alternative trackers.
- Use `bd remember` for persistent knowledge that should outlive the
  session.
