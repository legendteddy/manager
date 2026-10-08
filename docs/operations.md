# Production operations and SRE controls

Manager's operational layer is provider-neutral and deliberately separate from policy and authorization. Telemetry may describe an action, but it never grants authority, satisfies approval, changes a durable decision, or supplies trusted tool metadata.

## Runtime components

The Python reference runtime exposes:

- `SafeTelemetry`: structured events, metrics and spans with recursive redaction and bounded labels;
- `InMemoryTelemetrySink`: a bounded test/development buffer that drops oldest records at capacity;
- `JsonLoggingSink`: compact JSON structured logging through the Python standard library;
- `CapacityLimits` / `CapacityManager`: non-blocking concurrency gates, a bounded run queue, and checkpoint-size limits;
- `OperationalRuntime`: shared telemetry plus capacity control for run, model, tool, MCP and state operations;
- `ObservedModelAdapter`: provider-neutral model instrumentation and model concurrency protection;
- `ObservedRunStore`: state latency/error instrumentation plus checkpoint-size enforcement.

An embedding service should create one long-lived `OperationalRuntime` per process and reuse it across requests. Creating one runtime per request defeats process-level concurrency accounting.

## Privacy and redaction

Telemetry is intentionally lossy. Do not use it as a payload dump channel.

By default the sanitizer redacts fields whose names indicate credentials, tokens, authorization, cookies, sessions, prompts, model input, tool arguments, headers, private context, environment data or generic payload/context blobs. Bearer-style and common secret-like token strings are also redacted from otherwise ordinary strings. Arbitrary objects become their type name instead of invoking custom serialization.

Correlation identifiers that are not restricted identifier strings are replaced by a stable SHA-256-derived short hash. This keeps correlation useful without copying private text into telemetry.

Metric labels are allowlisted to low-cardinality dimensions. High-cardinality identifiers such as run IDs, tool names, request IDs, user IDs and arbitrary targets belong in correlation fields or sanitized events, not metric labels.

## Failure semantics

Telemetry sink failure is fail-safe and non-authoritative. `SafeTelemetry` catches sink exceptions and increments an in-process failure count. It does not retry recursively, block execution, alter authorization or mutate durable state.

Capacity failure is different. Concurrency gates use non-blocking acquisition. When capacity is exhausted, new work receives `OverloadedError` immediately rather than waiting in hidden queues. The bounded run queue raises the same error when full.

A service boundary should translate `OverloadedError` into its explicit overload response (for example HTTP 429/503 according to the service contract) and should not silently retry inside the same saturated process.

## Default capacity assumptions

Reference defaults are conservative placeholders, not production SLOs:

| Limit | Default |
| --- | ---: |
| Active runs | 64 |
| Queued runs | 256 |
| Model calls | 16 |
| Tool calls | 32 |
| MCP operations | 16 |
| State operations | 32 |
| Telemetry buffer records | 2048 |
| Serialized checkpoint | 2,000,000 bytes |

Deployments must tune these values from measured provider quotas, memory/CPU profiles, state-store capacity and latency objectives. Increasing a limit without load evidence can move overload from a clean rejection to process, provider, or datastore exhaustion.

## Metrics

The operational layer emits or reserves these provider-neutral concepts. Backends may rename them at export time, but should preserve meaning and bounded dimensions.

| Metric | Type | Meaning |
| --- | --- | --- |
| `manager_active_runs` | gauge | in-process active run slots |
| `manager_active_model_calls` | gauge | model concurrency |
| `manager_active_tool_calls` | gauge | tool concurrency |
| `manager_active_mcp_calls` | gauge | MCP concurrency |
| `manager_active_state_calls` | gauge | state backend concurrency |
| `manager_run_latency_seconds` | histogram | run execution latency |
| `manager_provider_latency_seconds` | histogram | provider latency |
| `manager_tool_latency_seconds` | histogram | tool latency |
| `manager_mcp_latency_seconds` | histogram | MCP latency |
| `manager_state_latency_seconds` | histogram | state operation latency |
| `manager_overload_rejections_total` | counter | deterministic capacity rejections |

Service integrations should additionally count completed/failed runs, queue depth, approval waits, recovery-required runs, retries, duplicate suppression, policy denials, timeouts, cancellations and budget exhaustion from the authoritative runtime outcomes. Do not infer those states from log text when durable state can report them directly.

## Suggested alert conditions

These are generic conditions, not organization-specific thresholds:

- sustained active-work or queue-depth saturation near configured limits;
- any sustained non-zero overload rejection rate;
- queue depth that rises while completion throughput falls;
- approval-wait population with age above the product's expected human response window;
- any growth in `recovery_required` that is not being reconciled;
- state latency/error increase correlated with run stalls or CAS conflicts;
- provider latency/error/timeout increase isolated to a provider;
- MCP latency/error/timeout increase isolated to MCP transport;
- telemetry sink failures above zero for a sustained interval;
- repeated budget exhaustion, duplicate suppression or retry amplification;
- checkpoint-size rejection, which indicates state growth approaching the configured envelope.

Exact paging thresholds, retention periods and SLO/error budgets require maintainer/operator policy and are intentionally not invented by the reference runtime.

## Diagnostic workflow

1. Determine whether the service is saturated: active slots, queue depth, overload rejections.
2. Correlate the request ID to run ID, then to model/tool/MCP/state operations without inspecting sensitive payloads.
3. Identify the slow or failing boundary from latency and status telemetry.
4. For approval waits, inspect the authoritative approval state and age, not model output.
5. For `recovery_required`, follow the recovery procedure and reconcile real-world outcome before retrying.
6. If state is degraded, reduce admission/concurrency before increasing retry pressure.
7. If a provider or MCP backend is degraded, avoid retry storms; use bounded retries only where the governing call semantics allow them.
8. If telemetry itself is failing, use health diagnostics and local process counters. Do not weaken authorization or dump secrets to compensate.

## Synthetic stress coverage

The reference tests exercise thousands of telemetry records against a bounded buffer, sink failure, model concurrency saturation, bounded queue rejection, checkpoint-size rejection, and high-cardinality label filtering without real providers. Service-specific load tests should add slow provider/tool/state doubles, cancellation/timeout storms, retry storms and recovery backlogs at the service layer where request queues and cancellation semantics exist.
