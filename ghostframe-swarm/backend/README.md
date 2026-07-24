# ghostframe-swarm (backend)

The GhostFrame Swarm kernel: event bus + append-only log, model router with
budget gates, the worker agent loop, tool runner, layered memory, workflow
engine with human approval gates, and the `ghost` CLI.

See [`../README.md`](../README.md) for the project overview and
[`../docs/`](../docs/) for the full architecture.

```bash
pip install -e ".[dev]"
pytest                      # runs entirely offline (scripted provider)
ghost init my-project && cd my-project
ghost run "say hello" -w feature-dev-mini --offline
```
