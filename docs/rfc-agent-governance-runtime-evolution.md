# RFC: Governed Agent Runtime Evolution

- **Status:** Accepted direction; staged implementation pending per-phase design and verification
- **Decision authority:** Owner approval received 2026-10-09
- **Scope:** Target architecture and implementation sequence only
- **Production behavior changed by this RFC:** No

## Decision summary

Manager will evolve toward a stronger provider-neutral control plane for long-lived and delegated agents without weakening its existing authority, approval, tool, MCP, durable-state, or verification boundaries.

The approved direction is to add, in dependency order:

1. first-class execution principals and authority delegation;
2. a generalized policy gateway across agent, tool, MCP, API, and agent-to-agent traffic;
3. sandbox/process/network/resource isolation for risky execution paths;
4. a unified governed resource registry for models, agents, tools, skills, MCP servers, and endpoints;
5. explicit persistent-agent versus ephemeral-worker lifecycles with provenance-aware governed memory;
6. evaluation-driven, cost-aware multi-model routing and run budgets.

A2A and cloud-specific identity mechanisms may be supported through adapters. They are not architectural identity.

## Why this RFC exists

Manager already has strong local controls:

- proposal is separated from execution authority;
- application-owned `ToolRegistry` metadata controls side-effect class, schemas, versions, verification requirements, and sensitive-output policy;
- consequential approval is action-specific and becomes stale when policy-relevant facts change;
- strict identity and authorization can be revalidated immediately before consequential execution;
- durable execution preserves budgets and stale-approval semantics across interruption;
- uncertain side effects enter `recovery_required` instead of being blindly replayed;
- MCP discovery is untrusted and explicit local bindings are revalidated immediately before remote execution;
- provider-managed tools and MCP execution remain outside the trusted reference path when they would bypass Manager policy;
- external content remains evidence, not authority.

Those controls should be generalized, not replaced.

The remaining architectural gap is that Manager's strongest controls are currently concentrated around tool execution and request-scoped identity. Long-lived agents, delegated workers, generalized agent-to-agent calls, generated code, richer registries, governed memory, and automatic model selection need the same authority discipline as the tool runtime.

## Current baseline and overlap

This RFC is intentionally based on the current `main` architecture plus active production-hardening work.

Two open workstreams overlap with later implementation phases and must be reconciled before code from this RFC lands:

- **PR #45 / `prod/06-tool-mcp-isolation`** hardens MCP network defaults and documents residual OS/container/firewall/service-mesh requirements. This complements, but does not replace, a general execution sandbox.
- **PR #46 / `prod/03-service-runtime`** builds a durable authenticated service/API gateway with application-owned backend construction and explicit exclusions for raw MCP serving and streaming. This should be treated as a service boundary that the generalized policy gateway must preserve rather than bypass.

The first implementation PR after this RFC should rebase on the final disposition of those branches and reconcile any changed security, service, MCP, or readiness contracts.

## Architectural invariants preserved

This RFC does not weaken the existing Manager invariants. In particular:

1. simple tasks stay direct;
2. specializations remain capabilities, and separate agents remain conditional;
3. delegation never silently expands authority;
4. consequential boundaries remain human-gated;
5. material rule changes still require human approval;
6. external content, model output, MCP metadata, tool output, and memory cannot redefine authority;
7. provider neutrality remains canonical;
8. private configuration and credentials remain external;
9. behavior is verified and evaluated rather than merely described;
10. evolution cannot expand its own authority, lower approval thresholds, weaken protected surfaces, or weaken the evals that judge it.

## Adopt / adapt / defer / reject

| Concept | Decision | Manager treatment |
| --- | --- | --- |
| First-class agent/workload identity | Adopt | Provider-neutral `ExecutionPrincipal` plus adapter-supplied authentication evidence. |
| Persistent and ephemeral agents | Adopt | Explicit lifecycle classes with different state, authority, and expiry rules. |
| Central agent policy gateway | Adopt | Generalize Manager's existing execution policy boundary to all governed outbound/inbound agent interactions. |
| Unified agent/tool/skill/MCP registry | Adopt | Single governed resource abstraction with type-specific contracts. |
| Secure code/tool sandbox | Adopt | Explicit containment profiles for code, shell, browser, untrusted MCP subprocesses, plugins, and generated executables. |
| Cost-aware multi-model routing | Adopt | Deterministic policy + eval evidence + hard run budgets; no vendor marketing scores as authority. |
| SPIFFE identity | Adapt | Supported identity adapter/format, never mandatory Manager core identity. |
| Agent-to-Agent protocol | Adapt | Optional interoperability adapter subject to Manager identity, policy, budget, verification, and audit. |
| Vendor memory taxonomy | Adapt | Useful categories may inform implementation, but Manager adds provenance, authority, freshness, sensitivity, and ownership. |
| Vendor-specific Workspace/product identity | Defer | Product adapter concern after the control plane is stable. |
| Agent-per-capability design | Reject | Violates minimum-necessary agentic complexity. |
| Remote metadata defining risk or permission | Reject | Local/app-owned governance remains authoritative. |
| Provider-managed execution bypassing Manager policy | Reject | Equivalent enforceable policy boundary is required before support. |
| Memory as authority | Reject | Memory is evidence/state unless explicitly owned by an authoritative source. |

## Target architecture

```text
Human / Service Authority Root
              |
              v
       Manager Control Plane
              |
      +-------+--------+
      |                |
      v                v
Execution Principal   Run Budget
      |                |
      +-------+--------+
              v
        Delegation Check
              v
          Policy Gateway
              |
   +----------+-----------+------------------+
   |          |           |                  |
   v          v           v                  v
Direct     Persistent   Ephemeral          External
Execution   Agent        Worker            Agent/API
   |          |           |                  |
   +----------+-----------+------------------+
              v
        Capability Router
              |
    +---------+----------+---------+
    |                    |         |
    v                    v         v
 Models                Skills     Tools
                                   |
                          +--------+--------+
                          |                 |
                          v                 v
                         MCP              APIs
                          |                 |
                          +--------+--------+
                                   v
                            Isolation Profile
                                   v
                           External Systems

Cross-cutting: durable state, governed memory, evidence, audit,
reconciliation, evaluator, verification, recovery, LEARN.
```

The key rule is that no model, worker, persistent agent, tool, protocol adapter, or memory record gains authority merely because it exists or was discovered.

## 1. ExecutionPrincipal

Manager should represent every actor that can originate or inherit executable authority with an explicit principal.

Minimum conceptual fields:

```text
ExecutionPrincipal
- principal_id
- principal_type
- parent_principal_id
- authenticated_identity
- delegated_authority
- capability_grants
- resource_scopes
- credential_binding_refs
- policy_revision
- provenance
- created_at
- expires_at
```

Initial `principal_type` values should include:

- `human`
- `service`
- `manager`
- `persistent_agent`
- `ephemeral_worker`

Additional types require reviewed semantics rather than free-form labels.

### Delegation rule

Authority may only attenuate through delegation.

```text
Owner / authorized human
    -> Manager
        -> persistent role agent
            -> ephemeral worker
                -> governed action
```

A child principal must never obtain a capability, resource scope, side-effect class, credential binding, lifetime, or approval privilege broader than the intersection permitted by its parent and current policy.

A model may propose delegation. It may not mint the delegated authority object that authorizes itself.

### Approval binding

Where a consequential action uses delegated execution, approval should eventually bind at least:

- acting principal;
- delegating principal/chain digest;
- action/tool/resource identity;
- target;
- material arguments;
- trusted definition/version;
- policy revision and policy digest;
- required side-effect class;
- approval identity;
- expiry/currentness evidence;
- canonical action fingerprint.

Changing any bound authority-relevant field makes prior approval stale.

## 2. Generalized Policy Gateway

The current tool runtime is already a policy boundary. The next step is to generalize the same discipline across additional traffic classes.

The gateway should mediate at least:

- agent -> agent;
- agent -> model;
- agent -> skill;
- agent -> native tool;
- agent -> MCP tool;
- agent -> HTTP/API endpoint;
- agent -> sandbox/code execution;
- agent -> persistent state or governed memory when access is sensitive or authority-bearing.

Canonical decision flow:

```text
proposal
 -> authenticated/current principal
 -> delegation validation
 -> local resource resolution
 -> authorization policy
 -> consequence/materiality check
 -> budget/admission check
 -> exact approval check when required
 -> isolated execution when applicable
 -> post-effect verification
 -> evidence/audit/reconciliation
```

The gateway is a logical enforcement boundary, not necessarily one network proxy or process.

### Default-deny rule

When strict policy mode is active, an unknown principal, unknown governed destination, stale policy binding, expired delegation, missing required approval, or unresolved resource identity fails closed.

Compatibility paths may remain temporarily for existing callers, but production-capable profiles should converge toward explicit strict mode rather than silently inheriting legacy coarse authorization.

## 3. Isolation and sandboxing

Manager currently has bounded tool/MCP resource controls, network-policy integration points, non-root/read-only-root deployment evidence, and hostile MCP tests. That is not equivalent to a general code/process sandbox.

A Manager isolation profile should be able to bound, where supported by the embedding environment:

- wall-clock time;
- CPU time/quota;
- memory;
- process count;
- open files/file descriptors;
- filesystem visibility and write paths;
- working-directory scope;
- network egress destinations;
- DNS behavior;
- subprocess spawning;
- environment-variable exposure;
- secret mounting/injection;
- device access;
- IPC/host namespace access;
- output/stdout/stderr size;
- artifact size and count.

High-risk execution paths should default to a restrictive profile. Examples include:

- generated Python or shell;
- untrusted plugin code;
- browser/computer-control helpers;
- arbitrary MCP subprocess servers;
- package hooks/build scripts;
- externally supplied executable adapters.

Manager core should define the isolation contract and required evidence. Concrete isolation may be provided by containers, microVMs, OS sandboxing, remote execution services, or another adapter.

No adapter may claim `sandboxed` merely because it runs in a different process.

## 4. Unified Governed Resource Registry

Manager should generalize `ToolRegistry` into a resource model without weakening the tool-specific trusted metadata boundary.

Conceptual resource contract:

```text
GovernedResource
- resource_id
- resource_type
- version
- owner
- provenance
- trust_class
- capabilities
- authority_requirements
- side_effect_class / effect profile when applicable
- verification_contract
- endpoint or adapter reference
- health/currentness
- cost_profile
- policy_bindings
- sensitivity
```

Initial resource types:

- `model`
- `agent`
- `tool`
- `skill`
- `mcp_server`
- `endpoint`

Type-specific schemas remain necessary. The unified registry is an inventory/governance plane, not an excuse to flatten every resource into the same executable interface.

### Discovery rule

Discovery creates candidates, not trust.

Remote registries, MCP discovery, provider catalogs, plugin manifests, A2A descriptors, package metadata, and model-generated resource suggestions remain untrusted until matched to application-owned policy and reviewed local definitions.

## 5. Skill contract

A skill should be a governed reusable workflow/capability description rather than an arbitrary prompt blob.

Conceptual fields:

```text
Skill
- skill_id
- version
- objective
- applicability
- required_capabilities
- workflow
- constraints
- authority_requirements
- resource_dependencies
- verification_contract
- provenance
- eval_history
```

A skill cannot grant permissions that its caller lacks.

The `LEARN` phase may produce a candidate skill, routing rule, or procedure, but promotion remains subject to existing bounded-evolution rules. Any candidate touching a protected surface or material rule still requires human approval.

## 6. Persistent agents and ephemeral workers

Manager should distinguish lifecycle, identity, memory, and authority.

### Ephemeral worker

- lifetime: bounded to a run/task/subtask;
- authority: delegated subset only;
- memory: minimal task-scoped state;
- credentials: least necessary and preferably short-lived;
- default: preferred implementation for temporary specialization and parallel work.

### Persistent agent

- lifetime: organizational role or long-lived service;
- stable principal identity;
- explicit owner and role purpose;
- durable state/history with retention rules;
- role-scoped capability grants;
- explicit revocation/disable path;
- currentness and health checks;
- no automatic inheritance of human authority.

Persistent agents are justified only when durable role identity materially improves the system. A permanent capability definition does not require a permanently running agent.

## Governed memory

If persistent-agent memory is introduced, every record should carry enough metadata to prevent memory from silently becoming authority:

- provenance/source;
- owner/source-of-truth class;
- confidence where relevant;
- freshness/observed-at time;
- sensitivity;
- authority level;
- retention/expiry;
- relationship to canonical state;
- reconciliation status when derived from another authority.

A practical taxonomy may include:

- working memory;
- episodic memory;
- semantic memory;
- procedural memory;
- authoritative references.

`Authoritative reference` does not mean a model-created memory is authoritative. It means the record points to, or is derived under explicit rules from, an independently owned source of truth.

## 7. Multi-model routing and run budgets

Manager should eventually select among approved models using deterministic constraints plus Manager-owned evaluation evidence.

Routing dimensions may include:

- required capabilities;
- difficulty/uncertainty;
- consequence/materiality;
- latency objective;
- cost;
- context size;
- privacy/data-transfer constraints;
- tool/function-calling support;
- historical eval score for the relevant task class;
- current health/availability;
- provider concentration/failover policy.

A cheaper model should not be selected when it would violate a capability, risk, privacy, or evaluation threshold. A more capable model should not receive data or tools outside the authorized workflow merely because it scores higher.

### RunBudget

Conceptual budget fields:

```text
RunBudget
- max_model_turns
- max_tool_calls
- max_external_calls
- max_tokens / provider units
- max_cost
- max_wall_clock
- max_sandbox_cpu
- max_sandbox_memory
- max_artifact_bytes
- max_human_attention_events
```

Existing loop budgets should be reused rather than replaced.

Budgets are ceilings, not targets.

## A2A and other interoperability protocols

Agent-to-Agent protocols should be implemented as adapters, like MCP.

An external agent descriptor may advertise identity, capabilities, schemas, endpoints, or skills, but it cannot define Manager's local trust class, approval requirements, delegated authority, verification policy, or sensitivity rules.

Before a consequential cross-agent action, Manager should retain a locally governed identity/resource binding and revalidate current destination identity and contract similarly to the existing MCP drift checks where practical.

## Threat analysis

New threats introduced by this direction include:

- compromised persistent agents retaining stale authority;
- privilege amplification across delegation chains;
- confused-deputy behavior between agents;
- registry poisoning;
- stale agent or skill versions;
- sandbox escape or overclaimed containment;
- memory poisoning and authority laundering through memory;
- cost exhaustion and denial of wallet;
- model-router downgrade attacks;
- cross-agent prompt injection;
- credential overexposure to child workers;
- policy bypass through direct protocol or network access;
- split-brain authority between service gateway, policy gateway, registry, and durable state.

Required mitigations include attenuation-only delegation, current-principal revalidation, default deny in strict mode, local registry authority, exact version/currentness checks, explicit egress controls, secret minimization, hard budgets, independent verification for consequential effects, durable recovery semantics, and tests proving direct bypass paths fail closed.

## Compatibility and migration

This direction should be introduced additively before any legacy path is removed.

Principles:

1. existing provider-neutral model and tool contracts remain valid until a reviewed migration says otherwise;
2. `ToolRegistry` semantics remain authoritative for tools even if the implementation later sits inside a unified registry;
3. existing durable checkpoints must fail closed rather than being best-effort interpreted after breaking contract changes;
4. strict principal/gateway mode should be opt-in for compatibility first, then become required for production-capable profiles only after migration evidence exists;
5. no persisted identity token, bearer secret, private key, or raw credential becomes canonical state;
6. new public schemas require contract tests and explicit migration/versioning rules.

## Implementation sequence

### Prerequisite alignment

Before Phase 1 code lands:

- reconcile this RFC against the final state of PR #45 and PR #46;
- refresh the threat model and readiness docs where their claims differ from the integrated runtime;
- identify all existing identity, authorization, tool-registry, service-gateway, MCP, durable-state, and budget contracts that the new work must preserve.

### Phase 1: Execution principal and delegation

Deliverables:

- canonical principal/delegation contract;
- attenuation checks;
- strict currentness/revocation integration;
- approval binding to acting/delegating authority where applicable;
- deterministic tests for privilege amplification, expiry, stale delegation, parent revocation, and restart behavior.

Exit gate: no existing tool or approval authority can be widened by principal/delegation introduction.

### Phase 2: Generalized policy gateway

Deliverables:

- one policy decision path for governed agent/resource interactions;
- explicit bypass prevention tests;
- service/API integration preserving application-owned backend authority;
- audit/evidence events without sensitive payload leakage.

Exit gate: direct agent-to-resource paths cannot silently bypass the same authority and approval rules enforced by the tool runtime.

### Phase 3: Isolation profiles

Deliverables:

- provider-neutral sandbox/isolation contract;
- at least one executable reference adapter;
- hostile tests for CPU, memory, process, filesystem, output, timeout, and network escape controls supported by that adapter;
- clear non-claims for host/kernel compromise and unsupported platforms.

Exit gate: risky executable workloads can be constrained by a tested profile, and unsupported isolation cannot be mislabeled as sandboxed.

### Phase 4: Unified registry and skills

Deliverables:

- typed governed-resource registry;
- compatibility layer preserving existing `ToolRegistry` trust semantics;
- skill schema and evaluation history;
- discovery-candidate versus trusted-registration boundary;
- version/currentness drift tests.

Exit gate: remote discovery cannot promote itself into trusted local authority.

### Phase 5: Persistent agents and governed memory

Deliverables:

- persistent/ephemeral lifecycle contracts;
- enable/disable/revoke semantics;
- retention and ownership rules;
- memory provenance/authority metadata;
- poisoning, staleness, deletion, and reconciliation tests.

Exit gate: persistent state cannot mint authority or survive explicit revocation as executable permission.

### Phase 6: Cost-aware multi-model routing

Deliverables:

- approved-model registry integration;
- evaluation-backed routing policy;
- `RunBudget` extensions;
- downgrade/fallback rules;
- deterministic budget exhaustion and provider failure behavior;
- observability for routing decisions without exposing private reasoning.

Exit gate: model choice cannot widen data access, tool access, authority, or consequence tolerance, and hard budgets remain enforceable under retries/fallback.

## Per-phase verification requirements

Every implementation phase must include, as applicable:

- unit tests;
- contract/schema conformance;
- hostile/adversarial tests;
- regression tests for prior authority and approval invariants;
- durable restart/recovery tests when persisted state is touched;
- public-safety checks;
- Python compatibility matrix;
- documentation updates matching executable behavior;
- explicit residual-risk and non-claim review.

A green branch does not inherit `main` readiness claims until the exact revision passes all required repository gates.

## Decisions already approved

The Owner has approved the architectural direction to:

- add first-class execution-principal/delegation semantics;
- distinguish persistent agents from ephemeral workers;
- generalize policy enforcement into a gateway boundary;
- add sandbox/process/network/resource isolation;
- build a unified governed resource registry and skills model;
- add governed memory only with provenance/authority metadata;
- add evaluation-driven, cost-aware multi-model routing and run budgets;
- keep SPIFFE, A2A, cloud products, and similar technologies as adapters rather than Manager identity;
- reject external metadata, memory, providers, or discovered agents as authority sources.

## Decisions not implied by this approval

This RFC does not pre-approve:

- a specific public schema shape for every new contract;
- a breaking compatibility migration;
- a specific cloud, identity provider, sandbox vendor, datastore, or model vendor;
- production deployment;
- public package publication;
- weakening an existing protected surface;
- automatic promotion of material or security-sensitive changes;
- provider-managed tool execution without an equivalent proven policy boundary.

Those decisions follow Manager's normal material-work rules when they become concrete.

## External evidence reviewed

External vendor architecture is evidence, not authority. The following public Google Cloud documentation was reviewed as comparative evidence on 2026-10-09:

- Agent Gateway overview: https://docs.cloud.google.com/gemini-enterprise-agent-platform/govern/gateways/agent-gateway-overview
- Agent Identity overview: https://docs.cloud.google.com/gemini-enterprise-agent-platform/govern/agent-identity-overview
- Agent Registry: https://docs.cloud.google.com/gemini-enterprise-agent-platform/govern/agent-registry
- Agent Platform overview: https://docs.cloud.google.com/gemini-enterprise-agent-platform/overview

The accepted Manager design is intentionally provider-neutral and is grounded first in Manager's own governance, security, architecture, runtime contracts, tests, and observed gaps.
