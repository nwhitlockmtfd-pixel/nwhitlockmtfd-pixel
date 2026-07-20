# Roadmap

Development order inside each version is listed — it doubles as the build
plan. The rule for every release: **cut scope, never cut observability.**

## v0.1 — "The kernel is honest" (foundation)

**Goal**: prove the core loop end-to-end with radical inspectability. One
user (a developer on a laptop), one project, small team of workers.

Build order:
1. Event envelope + bus (in-memory + Redis) + Postgres persistence
2. Model router with Anthropic, OpenAI, Ollama providers + budgets
3. Agent loop + WorkerDefinition + context manager (frame strategy only)
4. Tool runner (in-process + subprocess sandboxes) + 5 builtin tools
   (fs read/write, shell, web fetch, tests.run)
5. Minimal workflow engine (agent step, human gate, linear edges) + scheduler
   with leases
6. Task/project memory layers (no semantic search yet)
7. CLI: `init`, `dev`, `run`, `watch`, `approvals`, `tasks`
8. Dashboard v0: run view with live graph, inspector, approvals queue
9. Workers: Planner, Backend Engineer, Reviewer (3, not 10)
10. Examples 01 + docs: getting started, concepts

**Explicitly not in 0.1**: fan-out, delegation, plugins, semantic memory,
replay UI (the *log* exists from day one; the scrubber UI comes later).

**Technical debt accepted**: SQLite dev-mode divergences; naive scheduler;
no calibration (raw confidence + thresholds); single-process kernel.

**Contributors expected**: 1–3. **Migration**: none, pre-release resets allowed.

## v0.5 — "A real team" (breadth + plugins)

**Goal**: the full built-in team, plugin SDK stable enough for outsiders,
memory that actually retrieves, replay in the UI.

- Full core-team (10 workers), delegation + budget carving, review loops with
  structured verdicts, retry engine with failure classes
- Workflow fan-out/join, `max_loops`, stall detection
- Memory: team + tool layers, pgvector semantic search, compression pass,
  supersede chains
- Plugin SDK v1 (tools, providers, sinks, workers) + testing harness +
  first-party plugins (slack, github, otel) as proof
- MCP adapter (mount MCP servers as tool namespaces)
- Replay UI (timeline scrubber, projection replay); costs view
- API keys w/ scopes; problem+json errors; SDK (`ghostframe` pypi) v1
- Confidence calibration loop (recorded outcomes → curves)

**Debt accepted**: kernel still single-leader; re-execution replay is
CLI-only; dashboard perf on runs >5k events unoptimized.

**Migration**: `ghost migrate` introduced; event upcasters from 0.1.
**Contributors expected**: 5–15; plugin authors become the growth edge.

## v1.0 — "Trustworthy" (hardening + compatibility promise)

**Goal**: production-grade single-node; the compatibility promise begins.

- Stability: chaos tests (kill kernel mid-run, drop Redis, poison messages),
  lease/requeue hardening, DLQ diagnostics UX
- Security: container sandbox default for shell tools; permission review UX
  at plugin install; audit export (hash-chained); secrets backends (vault)
- Performance: event-blob offloading, dashboard virtualization, bus
  benchmarks published; scheduler fairness
- Re-execution replay in UI; determinism suite as required CI gate
- Docs: complete concept/howto coverage; upgrade guide; GOVERNANCE.md with
  2–3 external maintainers on board
- Freeze: event schema v1, plugin API v1, REST v1 — the 1.x promises

**Debt accepted (documented, deliberate)**: single-leader kernel (HA via
fast-restart + leases, not clustering); Python-only plugins.

**Migration**: final pre-1.0 breaking window with automated migrator.
**Contributors expected**: 20–50.

## v2.0 — "Scale and openness" (distribution + isolation)

**Goal**: multi-node, multi-project, arms-length extensions.

- Distributed worker runtimes (N containers, one control plane); kernel HA
  (leader election over Postgres advisory locks or etcd — RFC first)
- Out-of-process plugin host (subprocess or WASM) → true plugin isolation
- Multi-project/org: scoped tokens, per-project isolation guarantees tested
- Federation experiments: swarm-to-swarm delegation (from research track, if
  proven)
- Desktop client (wraps dashboard + local kernel)
- Event store pluggability (Postgres default; Kafka backend for large installs)

**Migration**: 1.x logs replay on 2.x (upcasters); config migrator; plugin
API v2 with one-major deprecation bridge.
**Contributors expected**: 50+, plugin ecosystem self-sustaining.

---

## Open source vs. commercial vs. research

### Open source, free forever

The entire single-team product: kernel, all core systems, all built-in
workers and their prompts, memory engine, dashboard including replay, plugin
SDK, CLI, SDK, MCP adapter, docker deployment. **Test**: if removing it would
make a solo developer's experience worse, it is core and stays free. No
feature moves from open to paid, ever — that promise is in GOVERNANCE.md.

### Commercial (a future "GhostFrame Enterprise" that doesn't rot the core)

Only organizational-scale concerns a solo dev never hits: SSO/SAML/SCIM,
org-wide RBAC and policy packs, hosted control plane (managed cloud),
cross-team cost governance/chargeback, compliance packs (SOC2 evidence
export, retention policies), long-horizon hosted event archival, SLA support.
Architecture rule: enterprise features are plugins/services *on top of*
public extension points — the open core never grows `if enterprise:` branches.

### Research (labs until proven)

- Swarm-to-swarm federation and negotiation protocols
- Learned task routing (predicting best worker/model from team memory)
- Automatic prompt evolution from review outcomes (worker "self-improvement")
  — high memory-poisoning risk; stays behind explicit human gates
- Cooperative multi-agent RL fine-tuning on event logs
- Formal verification of workflow properties (deadlock freedom, budget
  safety) via model checking

Each graduates only with: reproducible benchmark, RFC, and a migration story.
