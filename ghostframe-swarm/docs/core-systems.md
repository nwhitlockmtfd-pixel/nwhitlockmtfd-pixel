# Core Systems

Reference for every kernel subsystem. Each section states: purpose, design,
and the interface other components (and plugins) depend on.

## Workflow Engine

Executes **workflows**: typed directed graphs of steps, defined in YAML or
registered by plugins. Step types are a closed set the engine understands;
everything domain-specific lives in the data.

```yaml
# workflows/feature-dev.yaml
id: feature-dev
version: 3
steps:
  plan:       {type: agent, worker: planner, output: task_plan}
  spec_gate:  {type: human_gate, policy: approve_plan, after: plan}
  implement:  {type: fan_out, over: task_plan.items, worker_from: item.role, after: spec_gate}
  review:     {type: agent, worker: security_reviewer, for_each: implement, after: implement}
  qa:         {type: agent, worker: qa_engineer, after: review}
  ship_gate:  {type: human_gate, policy: code_change_approval, after: qa}
  finalize:   {type: agent, worker: documentation_writer, after: ship_gate}
edges_on_failure:
  review.rejected: implement       # loop with feedback (max_loops: 3)
  qa.failed: implement
```

- Each running workflow is a persisted state machine; transitions are events,
  so a kernel crash resumes exactly where it stopped.
- `fan_out`/`join` give parallelism; `human_gate` parks without burning tokens;
  `max_loops` bounds review cycles; loop exhaustion escalates to a human.
- Workflow definitions are versioned; running instances pin the version they
  started with, so upgrading a workflow never corrupts in-flight work.

## Task Scheduler

Decides *which* ready task runs *where*, *now*.

- Priority queue (Redis sorted sets) with: explicit priority, deadline
  pressure, dependency readiness, and per-worker concurrency limits.
- **Leases, not ownership**: a worker holds a task lease with heartbeats;
  missed heartbeats → lease expires → task requeued with attempt history
  intact. This is the crash-safety story.
- Fairness: per-workflow token-rate budgets prevent one giant workflow from
  starving the queue.
- Stall detection: steps declare an SLA; silent workers get poked with a
  STATUS request, then rescheduled.

## Agent Registry

The directory of worker definitions and live worker instances.

- Definitions (from YAML/plugins) validated at load; live instances register
  with capabilities + health; the scheduler matches `role`/capability
  requirements to instances.
- Also the enforcement source for permissions: Tool Runner and Model Router
  query the registry, never the worker's own claims.

## Event Bus

Redis Streams transport, Postgres persistence, described in
[architecture.md §4](architecture.md). Public interface is deliberately tiny:

```python
await bus.publish(event)                       # append + fan out
bus.subscribe(kinds=["task.*"], group="dash")  # consumer group, at-least-once
```

Everything else — audit, replay, costs, dashboard, metrics, plugin hooks — is
a consumer. Adding an integration never touches the kernel.

## Messaging Layer

Worker-to-worker and human-to-worker communication.

- Per-worker durable inbox (Redis stream) + threaded conversations
  (`thread_id` groups a delegation or review exchange).
- Messages are typed envelopes (the seven verbs in workers.md §3) with
  free-text bodies inside.
- Dead-letter queue for undeliverable messages; DLQ arrivals raise dashboard
  diagnostics, they never vanish.

## Tool Runner

The only way workers touch the world.

1. **Validate** args against the tool's Pydantic schema (bad args bounce back
   to the worker with the validation error — cheap self-correction).
2. **Authorize** against registry permissions + resource scopes (fs globs,
   network allowlists, repo branch rules).
3. **Execute** in the right sandbox: in-process (pure tools), subprocess with
   rlimits (shell/tests), or container (untrusted/plugin tools). Timeouts and
   output caps always.
4. **Record** `tool.invoked` / `tool.completed` events with args, result
   digest, duration, and side-effect declarations.

Tools declare side effects (`reads_fs`, `writes_fs`, `network`, `spends_money`)
— approval policies can gate on these declarations (e.g. "any tool with
`spends_money` requires human approval in this project").

**MCP adapter**: any Model Context Protocol server can be mounted as a tool
namespace (`mcp.github.*`), inheriting the same permission and audit
machinery. This is how GhostFrame gets a large tool ecosystem on day one.

## Model Router

One interface over all providers; the sole model-call path.

```python
class ModelProvider(Protocol):
    async def complete(self, req: CompletionRequest) -> CompletionResponse: ...
    async def stream(self, req: CompletionRequest) -> AsyncIterator[Delta]: ...
    def capabilities(self) -> ModelCapabilities   # ctx window, tools, vision, cost/token
```

- Built-in providers: Anthropic, OpenAI, Ollama, OpenAI-compatible local
  servers (vLLM, LM Studio). Others are plugins.
- **Selection policy** per worker/step: preferred → fallback chains, tier
  abstraction (`frontier` / `fast` / `cheap`) so configs survive model
  renames, per-call overrides for cheap operations.
- Router responsibilities: capability matching (context size, tool support),
  provider health/circuit breaking, rate-limit-aware queuing, token counting,
  cost precomputation for the budget gate, and full request/response event
  emission (with secrets redacted, content stored by reference for large
  payloads).

## Retry Engine

Policy-driven reaction to failure, distinct per failure class
(workers.md §4). A policy is data:

```yaml
retry_policies:
  default:
    transient:  {strategy: backoff, base_s: 2, max_attempts: 5}
    capability: {strategy: reroute, escalate_model_tier: true, max_attempts: 2}
    spec:       {strategy: bounce_to, worker: product_manager}
    budget:     {strategy: park_for_human}
```

Attempt history is first-class state — every retry decision and its inputs
are events, so "why did this run four times?" is answerable from the timeline.

## Approval Engine

Human-in-the-loop as a scheduling primitive.

- **Policies** decide which actions need approval: by side-effect class, by
  path (`deploy/**`), by cost threshold, by confidence, by workflow gate.
- Requests carry the worker's decision pack (workers.md §5), land in a queue
  visible in dashboard/CLI/webhook (Slack plugin), and support approve /
  deny / approve-with-edits (the edit becomes context for the worker).
- Timeouts are explicit policy: escalate to another approver, auto-deny, or
  keep parked. Nothing silently auto-approves.

## Cost & Token Tracking

A projector over `model.call` and `tool.*` events — not instrumentation
sprinkled through code.

- Every event carries a `CostDelta`; rollups by task, worker, workflow,
  model, provider, day.
- Budgets are enforced *pre-spend*: router computes an estimate, budget gate
  reserves it, actuals reconcile after the call.
- Dashboards get live burn rate; the API exposes `GET /costs` with the same
  groupings.

## Logging, Audit Trail, Replay

Three views over one log:

- **Logging**: structured (JSON) operational logs, correlation-id linked to
  events. For operators.
- **Audit trail**: the event log itself — who/what/when/why for every
  decision, exportable, tamper-evident (hash-chained event batches).
- **Replay**: reconstruct any workflow run event-by-event in the dashboard
  (time-travel debugging). Two modes:
  - *Projection replay* (always available): rebuild state/UI from events.
  - *Re-execution replay* (debugging): re-run a workflow feeding recorded
    model responses and tool results back instead of live calls —
    deterministic reproduction of any bug in orchestration logic.

## Context Manager

Builds each worker's prompt from parts — never by string concatenation in
worker code:

```
context_pack = system_prompt (role file)
             + task frame (goal, acceptance criteria, budget state)
             + retrieved memory (per memory.md strategies, token-budgeted)
             + thread history (compressed beyond N turns)
             + tool schemas (only permitted tools)
```

The pack is itself recorded in the deliberation event, so "what did the model
actually see?" is always answerable. Token budget per section, with pruning
priority: tool schemas > task frame > retrieved memory > thread history.

## Configuration Loader, Secrets, Versioning

- **Config**: layered Pydantic Settings (`defaults < ghostframe.yaml < env <
  CLI`); all swarm behavior (workers, workflows, budgets, policies) is
  reviewable config in the user's repo.
- **Secrets**: reference-based resolution (env / file / HashiCorp Vault
  backend via plugin); redaction at the event-emission boundary — the log
  physically cannot contain resolved secrets.
- **Versioning**: schema_version on events (with upcasters), version pinning
  on workflow instances, semver on the plugin API. The compatibility promise:
  *an event log written by any 1.x can be replayed by any later 1.x.*
