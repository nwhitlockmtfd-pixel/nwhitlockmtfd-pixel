# Worker Architecture

A **worker** is a long-lived, permissioned, budgeted AI actor with a role.
Workers do not call each other's code; they exchange messages and the
workflow engine arbitrates. This document defines the worker model and the
built-in team.

## 1. Anatomy of a worker

Workers are **data, not subclasses**. One `Worker` runtime (the agent loop in
[architecture.md §5](architecture.md)) executes any `WorkerDefinition`:

```yaml
# agents/backend_engineer.yaml
id: backend_engineer
role: >
  Implements server-side features. Writes production-quality Python.
  Never merges own work; submits for review.
system_prompt: prompts/backend_engineer.md      # plain file, inspectable, PR-reviewable
model_policy:
  preferred: {provider: anthropic, tier: frontier}
  fallback:  {provider: openai, tier: frontier}
  cheap_ops: {provider: ollama, model: qwen2.5-coder}   # e.g. commit messages
permissions:
  tools: [repo.read, repo.edit, shell.run, tests.run, search.docs]
  fs_scope: ["src/**", "tests/**"]              # cannot touch .github/, deploy/
  network: [pypi.org, docs.python.org]
  repo: {branches: ["ghost/*"], push: true, merge: false}
budgets:
  per_task_tokens: 400_000
  per_task_usd: 4.00
confidence:
  ask_below: 0.55          # ask clarifying question instead of guessing
  escalate_below: 0.30     # hand to human
memory:
  writes: [task, project]
  reads:  [task, project, team, tool]
review:
  can_review: [code]
  reviewed_by: [security_reviewer, qa_engineer]
delegation:
  may_delegate_to: [research_agent, documentation_writer]
escalation:
  after_failed_attempts: 3
  on_permission_denied: ask_human
```

Every field above corresponds to a runtime enforcement point — this file is a
contract, not documentation.

### Required capabilities (all workers)

| Capability | How it works |
|---|---|
| **Role** | System prompt file + structured metadata; registered in Agent Registry |
| **Memory** | Declared read/write access to memory layers; hydrated per loop iteration |
| **Permissions** | Tool allowlist + resource scopes; enforced in Tool Runner, never trusted to the prompt |
| **Tools** | Typed tools (Pydantic schemas); includes MCP-adapted tools |
| **Communication** | Inbox/outbox on the messaging layer; threaded conversations |
| **Confidence score** | Model self-reports per action (0–1) + platform adjusts using calibration history (see §5) |
| **Review capability** | Can be assigned review steps; produces structured `ReviewVerdict` |
| **Delegation** | Emits `Delegate` action → engine creates subtask with budget carved from parent |
| **Human escalation** | Emits `Escalate` action → approval queue with full context pack |

## 2. The built-in team

Ships as the `core-team` plugin — replaceable, but the reference standard.

| Worker | Purpose | Signature tools | Typical reviewers |
|---|---|---|---|
| **Planner** | Decomposes goals into a task DAG with acceptance criteria and budget split | memory.search, estimate | Product Manager, human |
| **Product Manager** | Owns requirements; turns vague asks into specs; arbitrates scope disputes | memory, docs.read, ask_human | human |
| **Architect** | System design, interface contracts, ADRs; reviews structural changes | repo.read, docs.write, diagram | Security Reviewer |
| **Research Agent** | Investigates libraries, APIs, prior art; produces cited briefs | web.search, web.fetch, memory.write | Architect |
| **Backend Engineer** | Server code, migrations, tests | repo.*, shell, tests.run | QA, Security |
| **Frontend Engineer** | UI code, components, styles | repo.*, browser.screenshot, tests.run | QA |
| **DevOps Engineer** | CI/CD, containers, deploy configs | repo.*, shell, ci.status | Security, human gate on deploy/** |
| **Security Reviewer** | Threat-models diffs; blocks dangerous changes; audits tool usage | repo.read, deps.audit, secrets.scan | — (reviews others) |
| **QA Engineer** | Writes/runs tests, reproduces bugs, verifies acceptance criteria | tests.*, shell, browser | — (reviews others) |
| **Documentation Writer** | Docs, changelogs, ADR polish | repo.read, docs.write | Product Manager |

Team composition is a workflow-level choice: a `feature-dev` workflow might
use six of these; a `research-brief` workflow uses two.

## 3. Collaboration verbs

All inter-worker behavior reduces to seven structured actions. Each is a
typed message + an event; free-text chat between workers exists only *inside*
these envelopes (the `body` field), so conversations stay auditable and
parseable.

```
DELEGATE      "You do this piece."     → engine spawns subtask (budget carved
                                         from parent; depth-limited, default 3)
SUBMIT        "My work is done."       → artifacts + self-assessment attached
REVIEW        "Verdict on your work."  → approve | request_changes(reasons[])
                                         | reject(reasons[]); structured reasons
                                         feed the retry engine and memory
ASK           "I need clarification."  → routed to the requester (worker or
                                         human); task parks, budget frozen
REJECT_TASK   "I shouldn't do this."   → wrong role / missing permission /
                                         conflicts with project memory; engine
                                         reroutes or escalates
ESCALATE      "A human must decide."   → approval queue with context pack:
                                         what, why, options considered, cost so far
STATUS        "Here's my progress."    → checkpoint events; feeds dashboard +
                                         stall detection (no checkpoint within
                                         step SLA → scheduler intervenes)
```

**Rejecting bad work** is deliberately cheap and normal. A `request_changes`
verdict loops the original worker with the reviewer's structured reasons
prepended to its context; a `reject` verdict sends the decision to the retry
engine, which may reroute to a different worker or trigger re-planning. All
verdicts accumulate in team memory, which is how the swarm "learns" reviewer
standards over time.

## 4. Retrying intelligently

The retry engine (see core-systems.md) distinguishes failure classes, and
workers are required to classify their failures in the `SUBMIT`/error payload:

- **Transient** (rate limit, network): backoff and retry, same everything.
- **Capability** (model produced garbage twice): reroute to stronger model
  tier, or different worker.
- **Specification** (reviewer keeps rejecting on requirements): don't retry
  the engineer — bounce to Product Manager or human, because the *spec* is
  the defect.
- **Permission/budget**: never silently retried; parks for human decision.

Attempt history is part of the retry context, so attempt 3 always knows what
attempts 1–2 tried and why they failed.

## 5. Confidence, calibration, escalation

Raw model self-confidence is unreliable, so GhostFrame treats it as a signal
to *calibrate*, not to trust:

1. Worker reports confidence per action (structured output field).
2. Platform records outcome (review verdict, test pass, human override).
3. A per-worker calibration curve (reported vs. actual success, updated in
   team memory) rescales future reports.
4. Gates use **calibrated** confidence: `ask_below` → ask a clarifying
   question; `escalate_below` → human queue.

Escalation to a human is never a stack trace. It's a **decision pack**:
the question, options the worker considered with tradeoffs, relevant memory
excerpts, cost spent so far, and cost estimates per option — designed so a
human can decide in under a minute from the dashboard or CLI.

## 6. What workers can never do

Enforced by the kernel regardless of prompts, plugins, or model output:

- Call a model except through the router (so every call is logged and budgeted).
- Use a tool outside their allowlist or resource scope.
- Exceed a budget (checked before spend, not after).
- Approve their own work.
- Modify their own permissions, budgets, or definition.
- Suppress events. There is no "quiet mode."
