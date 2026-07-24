# Repository Structure & Engineering Process

Monorepo. One clone gives you the kernel, dashboard, SDK, docs, and examples;
CI paths-filters keep pipelines fast.

## Layout

```
ghostframe-swarm/
├── backend/                      # the kernel (python package: ghostframe)
│   ├── ghostframe/
│   │   ├── kernel/               # workflow engine, scheduler, retry, approvals
│   │   ├── events/               # envelope, bus, persistence, upcasters
│   │   ├── workers/              # agent loop, registry, context manager
│   │   ├── memory/               # engine + postgres/pgvector provider
│   │   ├── models/               # router + anthropic/openai/ollama providers
│   │   ├── tools/                # tool runner, sandboxes, builtin tools, mcp adapter
│   │   ├── api/                  # FastAPI app: REST + WS
│   │   ├── sdk/                  # public interfaces (Protocols), plugin host, testing harness
│   │   ├── config/               # settings, secrets backends, DI container
│   │   └── cli/                  # `ghost` (typer)
│   ├── migrations/               # alembic
│   └── pyproject.toml
├── frontend/                     # dashboard (react+ts+vite)
│   └── src/{views,components,api,replay}/
├── agents/                       # built-in worker definitions (core-team)
│   ├── planner.yaml  ... documentation_writer.yaml
│   └── prompts/*.md              # every system prompt, in the open
├── workflows/                    # built-in workflow definitions (yaml)
├── plugins/                      # first-party plugins, each a real SDK consumer
│   ├── ghostframe-slack/         # approvals via slack
│   ├── ghostframe-github/        # repo tools
│   └── ghostframe-otel/          # event sink → opentelemetry
├── sdk/                          # (docs+stubs) points at backend/ghostframe/sdk
├── examples/
│   ├── 01-hello-swarm/           # two workers, one review, five minutes
│   ├── 02-feature-dev/           # the full team on a toy repo
│   ├── 03-research-brief/
│   └── 04-custom-plugin/
├── tests/                        # cross-cutting: integration, e2e, replay-determinism
├── benchmarks/                   # bus throughput, scheduler latency, memory retrieval
├── deployment/
│   ├── docker-compose.yaml       # the reference single-node stack
│   ├── Dockerfile.{api,worker,dashboard}
│   └── k8s/                      # example manifests (examples, not product)
├── scripts/                      # dev bootstrap, seed data, release tooling
├── docs/                         # this directory; published via mkdocs-material
├── .github/
│   ├── workflows/{ci,release,docs}.yaml
│   ├── ISSUE_TEMPLATE/{bug.yaml,feature.yaml,plugin.yaml,rfc.yaml}
│   └── PULL_REQUEST_TEMPLATE.md
├── CONTRIBUTING.md  CODE_OF_CONDUCT.md  SECURITY.md  GOVERNANCE.md
├── ROADMAP.md  CHANGELOG.md  LICENSE (Apache-2.0)
└── README.md
```

Two structural rules keep the architecture honest:

1. **`sdk/` has no imports from `kernel/`** — interfaces only. Enforced by an
   import-linter contract in CI.
2. **First-party plugins live in `plugins/` and use only the SDK.** If a
   built-in feature can't be written against the SDK, the SDK gets fixed, not
   bypassed.

## Documentation plan

| Doc | Audience | Source |
|---|---|---|
| README | evaluators (30-second pitch → 5-minute demo) | root |
| Getting Started / Tutorials | new users | docs/tutorials/ |
| Concepts (workers, memory, workflows, events) | users | docs/ (this set) |
| How-to guides (add a worker, write a plugin, gate deploys) | users | docs/howto/ |
| API reference | integrators | generated from OpenAPI + docstrings |
| ADRs (`docs/adr/NNNN-*.md`) | contributors | one per irreversible decision |
| RFCs (`docs/rfc/`) | contributors | required for kernel-touching features |

Docs are versioned with releases (mike/mkdocs); every PR that changes
behavior must touch docs or carry a `docs-not-needed` justification label.

## Coding standards

- Python 3.12+, `ruff` (lint+format), `mypy --strict` on `kernel/`, `sdk/`,
  `events/`; typed public APIs everywhere.
- Pydantic models at every boundary (API, events, tools, config); no raw
  dicts crossing module lines.
- Async by default; blocking work behind `anyio.to_thread`.
- Frontend: TS strict, ESLint, Prettier; components colocated with views.
- Conventional Commits (`feat:`, `fix:`, `kernel!:` for breaking) — feeds
  changelog automation.
- Every event kind, config key, and API route documented at the definition
  site; docs generation pulls from source.

## Testing strategy

| Layer | Approach | Gate |
|---|---|---|
| Unit | pure logic; providers/models faked via SDK testing harness | 100% of PRs, <60s |
| Contract | every `Protocol` has a compliance suite plugins run against their implementations (e.g. `MemoryProviderContract`) | PRs touching sdk/ |
| Integration | kernel against real Postgres+Redis (testcontainers); no live LLM calls — recorded model responses via replay fixtures | 100% of PRs |
| E2E | docker-compose stack, scripted runs of examples/, dashboard smoke via Playwright | main + nightly |
| Determinism | replay a recorded run, assert projections byte-identical | 100% of PRs (this guards the core promise) |
| Live-model | tiny nightly suite against real providers, budget-capped | nightly, non-blocking with alert |
| Benchmarks | bus throughput, scheduler latency, retrieval p95; regression vs. baseline | nightly |

The recorded-response fixture system is the same machinery as re-execution
replay — tests and replay share one implementation, so it stays maintained.

## CI/CD

- **ci.yaml**: ruff → mypy → unit+contract (matrix 3.12/3.13) → integration
  (testcontainers) → frontend lint+test+build → e2e (paths-filtered) →
  determinism suite. Required checks; merge queue on main.
- **docs.yaml**: build + link-check on PR; publish on release.
- **release.yaml**: tag `v*` → build sdist/wheels + docker images (ghcr) →
  generate changelog → GitHub release → PyPI (trusted publishing) → docs
  version publish.

## Release strategy

- **Cadence**: minor every ~6 weeks, patches as needed; `main` is always
  releasable (merge queue + determinism gate).
- **Versioning**: SemVer. Pre-1.0: breaking changes allowed in minors,
  loudly, with migration notes + `ghost migrate` where feasible. Post-1.0:
  the compatibility promise is (a) event logs replay across all 1.x, (b)
  plugin API breaks only at majors with one-minor deprecation warnings.
- **Support**: latest minor gets patches; last minor of a major gets security
  fixes for 6 months after next major.

## Contribution process (summary of CONTRIBUTING.md)

- `good-first-issue` curated continuously — plugins, tools, dashboard views,
  and docs are deliberately shallow entry points; the kernel is the deep end.
- RFC required for kernel/event-schema changes; ADR recorded on acceptance.
- Maintainer expectations in GOVERNANCE.md: review SLA target, how commit
  rights are earned, how disagreements resolve (maintainer vote).
- Security reports via SECURITY.md private channel, 90-day coordinated
  disclosure.
