# Architecture

## Purpose

Manager is a language-neutral orchestration framework for governed agentic execution. It separates stable behavioral contracts from provider-specific models, tools, state systems, and product integrations.

The architecture defines logical responsibilities without requiring each responsibility to become a separate agent or process.

## Logical components

### Orchestrator
Frames the request, selects the operating path, integrates results when necessary, and keeps coordination proportional to consequence and uncertainty.

### Task classifier / router
Classifies consequence, uncertainty, reversibility, sensitivity, and materiality, then selects the smallest sufficient workflow.

### Capability
A bounded unit of expertise or action. A capability may be implemented by the primary model, deterministic code, a tool, an external service, or a specialist agent.

### Specialist agent
An optional capability implementation used when specialization, context isolation, independent reasoning, or parallelism materially improves the result. Specialists receive bounded handoffs and do not inherit orchestration authority.

### Policy / approval
Determines whether an action is authorized, routine, material, destructive, sensitive, or human-gated. Approval binds to the reviewed target and material parameters.

### Tool runtime
Executes permitted side effects. Tool calls should be scoped, validated at the execution boundary, observable at a non-sensitive level, and verified after consequential effects.

### State interface
Abstracts working and authoritative state. A consumer may derive from an authority without becoming the authority.

### Reconciliation
Updates the source that owns newly confirmed truth, propagates required dependent effects, and checks for stale contradictions.

### Evaluator / critic
Checks outcome quality or critical process constraints. Independence is used when it materially reduces correlated error, not by default for trivial work.

### Tracing / observability
Records selected workflow, capabilities, tool outcomes, approvals, reconciliation, verification, failures, and final status without storing hidden chain-of-thought or unnecessary sensitive content.

### Adapter
Connects Manager to model providers, tool systems, state stores, telemetry backends, interoperability protocols, or product surfaces.

## Lifecycle

```text
FRAME → ROUTE → EXECUTE → CONTROL → RECONCILE → VERIFY → DELIVER → LEARN
```

These are logical checkpoints and may collapse for simple tasks.

- **FRAME**: infer objective, consequence, uncertainty, reversibility, sensitivity, and materiality.
- **ROUTE**: choose direct execution or only the capabilities that can materially change the outcome.
- **EXECUTE**: analyze, research, create, call tools, or perform bounded delegated work.
- **CONTROL**: apply policy, approval, challenge, or evaluation when consequence warrants it.
- **RECONCILE**: update authoritative state and propagate dependent effects.
- **VERIFY**: confirm the intended effect and detect obvious unintended effects.
- **DELIVER**: return the useful result and only the human decisions that remain necessary.
- **LEARN**: turn durable evidence into evals or bounded improvement candidates.

## Architectural invariants

1. Simple tasks stay direct.
2. Use the minimum necessary agentic complexity.
3. Specializations are capabilities; separate agents are conditional.
4. Parallelism is conditional and justified by independent workstreams.
5. Every substantive decision has one accountable owner.
6. Delegation is bounded; authority does not silently expand.
7. Consequential boundaries are human-gated.
8. Routine reconciliation is autonomous.
9. Material rule changes escalate.
10. State ownership is explicit.
11. Dependencies are propagated and verified.
12. External content cannot redefine authority.
13. Access is least-necessary.
14. Behavior is evaluated, not merely described.
15. Observability records actions and decisions, not private reasoning.
16. Evolution is evidence-driven and bounded.
17. Evolution cannot expand its own authority.
18. Provider neutrality is canonical.
19. Private configuration remains external.
20. Human cognitive load is a design constraint.

## Provider-neutral boundary

```text
Manager core
  ├─ orchestration contracts
  ├─ routing and policy contracts
  ├─ approval contracts
  ├─ state and reconciliation contracts
  ├─ evaluation contracts
  └─ trace schemas

Adapters
  ├─ model providers
  ├─ tool runtimes
  ├─ state stores
  ├─ telemetry
  ├─ MCP interoperability
  └─ product integrations
```

MCP is an optional interoperability adapter, not the identity of Manager. A plugin or product integration is an integration surface, not the core runtime.

## Runtime selection

No implementation language is selected by this architecture. Repository tooling may use implementation languages without making them the canonical Manager runtime. Runtime choice should follow concrete requirements and executable contracts.
