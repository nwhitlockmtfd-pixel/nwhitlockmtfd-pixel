# Plugin SDK

Everything domain-specific in GhostFrame is a plugin — including the built-in
team (`core-team`) and the built-in providers. The kernel ships with
batteries included but nothing hardwired: if our own features don't use the
public SDK, the SDK is broken.

## 1. What a plugin can provide

| Extension point | Protocol | Examples |
|---|---|---|
| Workers | `WorkerDefinition` (data) + optional prompt files | a `DataEngineer`, a `LegalReviewer` |
| Tools | `Tool` | Jira, Playwright browser, Terraform |
| Memory providers | `MemoryProvider` | Qdrant, Chroma, S3 archival |
| Model providers | `ModelProvider` | Bedrock, Gemini, vLLM cluster |
| Workflow step types | `StepExecutor` | canary-deploy step, A/B judge step |
| Approval systems | `ApprovalPolicy` + notifier | Slack approvals, PagerDuty gate |
| Event sinks | `EventSink` | Datadog, OpenTelemetry, S3 archiver |
| Retry policies | `RetryPolicy` | domain-specific failure handling |
| Secrets backends | `SecretsBackend` | Vault, AWS Secrets Manager |

## 2. Anatomy

A plugin is a normal Python package with an entry point and a manifest:

```toml
# pyproject.toml
[project.entry-points."ghostframe.plugins"]
jira = "ghostframe_jira:plugin"
```

```python
# ghostframe_jira/__init__.py
from ghostframe.sdk import Plugin, hookimpl

plugin = Plugin(
    name="jira",
    version="1.2.0",
    requires_ghostframe=">=0.5,<2.0",       # checked at load
    permissions=["network:atlassian.net"],  # declared, shown to user at install
)

@plugin.tool
class CreateIssue:
    """Create a Jira issue."""
    name = "jira.create_issue"
    side_effects = ["network"]

    class Args(BaseModel):
        project: str
        title: str
        body: str

    async def run(self, ctx: ToolContext, args: Args) -> ToolResult:
        token = await ctx.secrets.get("JIRA_TOKEN")     # never raw env access
        ...
        ctx.log.info("issue created", key=key)          # → events, auditable
        return ToolResult(ok=True, data={"key": key})
```

`ToolContext` is the capability boundary: secrets, scoped fs, scoped http
client, logger, memory access — all mediated, all audited. Plugins never
receive kernel internals.

## 3. Loading, trust, isolation

- **Discovery**: entry points; `ghost plugins add ghostframe-jira` is
  `uv pip install` + registry refresh + permission review prompt.
- **Trust model (honest)**: Python plugins are in-process — a malicious
  plugin is malicious code, and we say so loudly. Mitigations, not theater:
  declared-permission review at install, scoped `ToolContext` capabilities
  (the easy path is the safe path), and container-sandboxed execution for
  tools that request `shell`/`fs` side effects. True isolation
  (subprocess/WASM plugin host) is on the roadmap (v2.0) — see design review.
- **MCP as the arm's-length option**: third-party integrations you don't
  trust in-process should run as MCP servers (separate process, protocol
  boundary) mounted through the MCP adapter tool.

## 4. Compatibility & quality

- Plugin API is semver'd separately from the app; `requires_ghostframe`
  enforced at load; deprecations get one minor version with warnings.
- The SDK ships a test harness — `ghostframe.sdk.testing` provides a
  `FakeSwarm`, tool-invocation fixtures, and golden-event assertions:

```python
async def test_create_issue(fake_swarm):
    result = await fake_swarm.invoke_tool("jira.create_issue",
                                          project="GF", title="t", body="b")
    assert result.ok
    fake_swarm.assert_event("tool.completed", tool="jira.create_issue")
```

- `ghost plugins verify <pkg>` runs the plugin's declared test suite against
  the installed kernel version.
- Community registry: a static index repo (like vim-plug ecosystems, not a
  hosted service) listing vetted plugins + their permission manifests.
