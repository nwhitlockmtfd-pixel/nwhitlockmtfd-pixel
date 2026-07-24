# 01 — Hello Swarm

The five-minute tour: a planner, an engineer, and a reviewer collaborate on a
tiny goal, with a human ship-gate at the end and a full audit trail.

```bash
cd backend && pip install -e .          # from the repo root
mkdir /tmp/hello-swarm && cd /tmp/hello-swarm
ghost init .

# Offline first (no API keys — scripted provider, proves the pipeline):
ghost run "create hello.txt containing 'hi'" -w feature-dev-mini --offline --yes

# The real thing (needs ANTHROPIC_API_KEY):
ghost run "create hello.txt containing 'hi'" -w feature-dev-mini --budget 2
```

During a live run you'll be prompted at the ship gate (and whenever a worker
escalates). Afterwards, inspect exactly what happened:

```bash
ghost events                       # every decision, in order
ghost events --kind 'model.*' --full   # the exact prompts and outputs
ghost events --kind 'worker.deliberation' --full
```

What to notice:

- The reviewer re-runs verification itself; if it returns
  `verdict: request_changes`, the engineer gets the structured reasons on
  attempt 2 — watch for a second `task.assigned` on the implement step.
- Kill the run and re-check `ghost events`: everything already emitted is
  durably in `.ghostframe/ghost.db`.
- Budgets are enforced *before* each model call — try `--budget 0.01` and
  watch the run park for human approval instead of overspending.
