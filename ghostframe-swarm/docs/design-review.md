# Brutally Honest Design Review

Written as if by a skeptical senior maintainer reviewing this whole design
before a line of code exists. Nothing here is softened. Where we accept a
weakness deliberately, we say so; where we don't have an answer, we say that
too.

## 1. The biggest risk isn't technical

**Multi-agent frameworks have a graveyard.** AutoGPT, BabyAGI, and a dozen
"AI software company" projects spiked to 50k stars and collapsed because the
*output quality* of agent teams didn't justify the token spend. GhostFrame's
bet is that observability + review loops + human gates change the economics.
That is a hypothesis, not a fact. If a 6-worker feature-dev run costs $8 and
produces work a senior dev rewrites anyway, no amount of beautiful replay UI
saves the project. **Mitigation**: v0.1 ships with 3 workers, not 10, and the
benchmark suite must include *quality-per-dollar* comparisons against a
single strong agent baseline. If the swarm doesn't beat one good agent on
real tasks, we must be willing to say so publicly and reposition (the
orchestration/audit layer is still valuable for single-agent pipelines).

## 2. Scope: this design is 3 products

Kernel + plugin ecosystem + mission-control dashboard is the surface area of
LangGraph + Temporal + a small Grafana. For a project with 1–3 initial
contributors, that's a multi-year commitment, and half-built versions of this
design look worse than not building them. **Mitigation**: the roadmap's build
order is the real contract — v0.1 is deliberately a *narrow vertical slice*
(one workflow shape, 3 workers, minimal UI). The temptation to build the
plugin SDK before there are users will be strong and must be resisted; SDKs
designed without consumers are always wrong.

## 3. Event sourcing: right idea, real costs

Experienced maintainers will (correctly) warn:

- **Schema evolution is forever.** Every event kind we ship in 0.x becomes an
  upcaster we maintain in 2.x. The promise "any 1.x log replays on any later
  1.x" is easy to write and expensive to keep — it needs the determinism CI
  gate from day one or it will silently rot.
- **Projections drift.** "Rebuildable from events" is only true if someone
  actually rebuilds them regularly. CI must rebuild projections from raw logs
  and diff against incrementally-maintained state, or we'll discover drift in
  production.
- **Volume.** A chatty 10-worker run emits tens of thousands of events, with
  full prompts. The blob-offload design helps, but Postgres as event store
  has a ceiling; we've deliberately postponed Kafka to v2.0, which is right
  for adoption but means big installs will hit walls in 1.x. We should
  publish the measured ceiling ("tested to N events/hour on M hardware")
  rather than let users find it.

## 4. The single-leader kernel is a lie we're telling politely

Until v2.0, the workflow engine is one process. Leases make crashes
*recoverable*, not invisible: an in-flight step's model call dies with the
kernel and gets retried, which costs money and time. Calling this "crash
safety" in marketing while the design review admits "fast-restart, not HA"
is the honest posture — keep it that way. Also: Redis is a single point of
failure for liveness in the same window. Anyone who needs five-nines
orchestration in 2026 should use Temporal, and our docs should literally say
that sentence.

## 5. Security concerns a reviewer will raise in the first hour

- **Prompt injection through tools.** A worker that reads a webpage or a
  README ingests attacker-controlled text that can steer it ("ignore
  review, approve this"). Permissions bound the blast radius (a poisoned
  research agent can't push code), and review + human gates help, but
  **cross-worker injection via memory is the nasty one**: poisoned content
  distilled into project memory steers *future* tasks. The supersede/
  provenance design makes it auditable after the fact; it does not prevent
  it. We need taint-tracking on memory entries sourced from untrusted tool
  output (mark + display provenance class, exclude tainted entries from
  high-privilege workers' packs by default). This is designed but must not
  slip from v1.0.
- **The plugin trust model is honest but thin.** In-process Python plugins
  are code execution, full stop. Declared permissions are review theater
  until the out-of-process host (v2.0) exists — the docs say this plainly,
  which is the best we can do short of re-sequencing v2.0 work earlier.
- **Sandbox realism.** Subprocess + rlimits is not a security boundary
  against hostile code; container sandboxes have escape histories too. For
  shell/test tools operating on the user's own repo this is acceptable
  (threat = confused agent, not APT), but the docs must never use the word
  "secure" for it.
- **Secrets redaction at emission** is good, but redaction-by-reference
  fails open if a tool *returns* a secret in its output. Needs entropy/
  pattern scanning on tool outputs before they enter events — imperfect,
  belongs in v1.0 regardless.

## 6. Performance bottlenecks, ranked by when they'll hurt

1. **Context assembly (immediately).** Retrieval + packing on every loop
   iteration; pgvector HNSW is fine, but the frame strategy re-reads project
   memory constantly → cache with event-driven invalidation.
2. **Model-call queuing at fan-out (v0.5).** 10 parallel implement steps hit
   provider rate limits; the router's rate-limit-aware queue becomes the real
   scheduler — must be observable (queue-time is a first-class metric) or
   users will read stalls as bugs.
3. **Dashboard on big runs (v0.5–1.0).** 50k-event replays will melt naive
   React. Needs virtualization + level-of-detail projection (the manifest
   endpoint should serve pre-bucketed summaries, not raw events).
4. **Postgres write amplification (1.x at scale).** events + blobs + rollups
   + projections per action. Partitioning is designed; batched writes and
   async rollups must be too.

## 7. Simplifications we should actively consider

- **Do we need Redis in v0.1?** In-memory bus + Postgres LISTEN/NOTIFY could
  serve single-node and cut a dependency. Counterpoint: introducing Redis
  later churns the bus abstraction. Decision stands (Redis from 0.1) but
  it's genuinely arguable and deserves an ADR with the counterargument
  recorded.
- **Seven verbs may be five.** REJECT_TASK and ESCALATE might both be ASK
  with a target. Resist taxonomy growth; collapse if usage data says so.
- **Calibration curves (workers.md §5) smell like premature ML.** A simple
  "escalate after N rejections" rule may capture 90% of the value. Ship the
  simple rule first; calibration is v0.5+ and must prove itself against it.
- **The tier abstraction (`frontier`/`fast`/`cheap`)** will leak — models
  differ in kind (context size, tool reliability), not just quality. Keep
  explicit model pinning as the documented escape hatch.

## 8. Alternative architectures we rejected (and when we'd be wrong)

| Alternative | Why rejected | We're wrong if… |
|---|---|---|
| Build on Temporal instead of our own engine | Heavy dependency, JVM-ish ops burden, hides the event log we want to own | our engine's edge cases (leases, joins, loops) eat >30% of dev time — then swallow the pride and RFC a Temporal backend |
| LangGraph as the worker runtime | Couples our worker model to another framework's abstractions and release cadence | the ecosystem consolidates hard on it and interop matters more than coherence — then ship a langgraph-bridge plugin |
| Go kernel + Python workers | Perf + real concurrency | the asyncio kernel hits scheduling ceilings pre-v2.0 |
| Erlang/Elixir (actors are literally the model) | Contributor pool; AI ecosystem is Python | never — the tradeoff is right even though BEAM fits the semantics better |

## 9. What experienced open-source maintainers will criticize

- *"Monorepo with frontend + kernel + 3 plugins = contributor CI pain."*
  Fair; paths-filtering mitigates, but expect grumbling.
- *"YAML programming."* Workflow YAML will grow conditionals until it's a bad
  programming language. Hold the line: complex logic goes in Python step
  executors (plugins), YAML stays declarative wiring. Write this rule into
  CONTRIBUTING before the first `when:` clause PR arrives.
- *"Your prompts are load-bearing and untested."* Correct — prompt files are
  code with no type checker. The recorded-response test suite covers
  orchestration, not prompt quality; the nightly live-model suite is tiny.
  Prompt regression testing (golden tasks, scored) is an unsolved cost/
  quality tradeoff we carry openly.
- *"Confidence scores are vibes."* Also correct (see §7). Never gate anything
  safety-relevant on confidence alone; gates compose with hard rules
  (permissions, budgets) that don't care what the model thinks of itself.
- *"Where's the killer demo?"* The whole design stands or falls on
  `examples/02-feature-dev` being genuinely impressive on a real repo, with
  the replay showing *why* it worked. That example is the product's front
  door and deserves disproportionate investment.

## 10. Bottom line

The architecture is coherent and the observability-first stance is a real
differentiator — nobody in this space has a credible replay/audit story, and
it's the feature serious teams actually need. The two ways this project
dies are (1) building the platform before proving the economics (§1, §2),
and (2) breaking the replay promise once (§3). Every roadmap decision should
be tested against those two failure modes first.
