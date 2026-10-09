# RFC: Agent Platform Foundations

**Status:** Direction approved; implementation contracts proposed  
**Owner approval:** 2026-10-09  
**Scope:** Manager target architecture for identity, policy enforcement, isolation, governed registries, persistent agents, and multi-model routing  
**Decision owner:** Manager technical architecture  

## 1. Decision summary

Manager will evolve toward a provider-neutral governed agent platform with six ordered foundations:

1. first-class execution identity and bounded delegation;
2. a central policy gateway for cross-boundary agent interactions;
3. sandbox/process/network isolation;
4. a governed registry substrate for agents, skills, tools, MCP endpoints, models, and related capability metadata;
5. optional persistent agents alongside ephemeral workers;
6. evaluation-driven, cost-aware multi-model routing with hard execution budgets.

This RFC records the approved target direction. It does **not** make any model provider, cloud identity product, protocol, registry vendor, or sandbox implementation canonical.

The existing Manager invariants remain binding:

- model or agent output is proposal content, not authority;
- human final authority remains at material boundaries;
- approvals remain action-specific and become stale when reviewed material parameters change;
- external content and remote metadata are evidence, not authority;
- MCP and future agent protocols remain adapters, not Manager's identity;
- provider neutrality remains canonical;
- least-necessary access, explicit state ownership, reconciliation, verification, and recovery remain mandatory;
- evolution cannot expand its own authority or weaken protected surfaces.

Any later implementation that changes a protected rule beyond the direction approved here still requires the normal material-change process.

## 2. Why now

Manager already separates orchestration, model adapters, tool execution, policy, approvals, durable state, recovery, verification, reconciliation, and observability. The production-hardening work in flight is also making service identity, network boundaries, MCP isolation, idempotency, and durable execution materially stronger.

Recent public agent-platform designs provide independent evidence that production agent systems are converging on several related primitives: per-agent identity, centralized policy enforcement, governed registries, sandboxing, persistent and temporary agent identities, multi-model routing, and explicit cost controls.

That external convergence is evidence, not authority. Manager should adopt only the parts that strengthen its existing governance model.

## 3. Adopt, adapt, reject

### Adopt

Manager should adopt these concepts as first-class architecture:

- execution principals with stable identity;
- explicit delegation chains and non-amplifying authority;
- centralized policy evaluation for agent-to-agent, agent-to-tool, agent-to-MCP, and agent-to-API interactions;
- isolated execution with deny-by-default resource and egress profiles;
- a common governed registry envelope with typed resources;
- persistent agents as an optional runtime primitive;
- empirical model capability profiles;
- hard run budgets spanning model, tool, time, external API, and delegation costs.

### Adapt

Manager should support, but not become coupled to:

- SPIFFE/SVID or workload identity as one identity-provider adapter;
- A2A or similar agent interoperability protocols as adapters;
- provider-specific agent runtimes;
- external cloud registries;
- organization-specific IAM engines;
- provider-native memory systems;
- provider-native model routers.

### Reject

Manager should reject these architecture patterns:

- model identity as execution authority;
- agent self-asserted scopes or risk classes;
- remote registry/MCP descriptions as trusted policy metadata;
- implicit inheritance of human authority by an agent;
- reusable blanket approval for future consequential actions;
- provider-managed tool execution that bypasses Manager's enforceable policy boundary;
- mandatory agent proliferation for every capability;
- one cloud vendor's identity/IAM assumptions in Manager core;
- memory as canonical authority merely because it is persistent;
- routing optimizers that can lower safety controls to reduce latency or cost.

## 4. Target control flow

The target execution path is:

```text
proposal / objective
        ↓
execution principal
        ↓
delegation + current authority
        ↓
policy gateway
        ↓
registry / capability resolution
        ↓
execution environment
        ↓
side effect / interaction
        ↓
verification
        ↓
durable evidence + state
```

No layer below the policy gateway may silently create authority that did not exist above it.

## 5. Phase 1: execution identity and delegation

### 5.1 Separate ingress identity from Manager execution identity

Manager must distinguish at least these concepts:

- **caller identity**: who or what invoked the service/API;
- **execution principal**: the actor currently responsible for a Manager action;
- **delegator**: the principal that granted bounded authority to another principal;
- **human approver**: a human principal whose explicit decision satisfies an approval gate;
- **credential binding**: environment-owned mechanism used to authenticate to an external system.

Authentication is not authorization. Authorization is not approval. Approval is not delegation.

The open service-identity work must remain compatible with this distinction: a validated API caller may be allowed to submit a run without receiving tool scope, approval status, model-visible authority, or downstream credentials.

### 5.2 Proposed core primitive

A future provider-neutral contract should be able to represent an execution principal similar to:

```text
ExecutionPrincipal
├── principal_id
├── principal_type
│   ├── human
│   ├── service
│   ├── persistent_agent
│   ├── ephemeral_worker
│   └── system
├── issuer / identity_provider_ref
├── parent_principal_id?      # delegator lineage
├── delegated_scope
├── capability_grants
├── constraints
├── created_at
├── expires_at?
├── revision / identity_version
└── provenance
```

This is a logical contract, not a commitment to a specific schema or credential format.

### 5.3 Delegation invariants

Delegation must satisfy all of the following:

1. a child principal cannot receive authority the parent does not currently hold;
2. delegated scope is explicit and bounded by objective, target/capability scope, time, and resource budgets where applicable;
3. delegation does not transfer human approval;
4. approval remains bound to the reviewed action and current principal context where principal identity is material;
5. revocation or authority-version change invalidates stale delegation before consequential execution;
6. delegation lineage is auditable without exposing secrets or hidden reasoning;
7. a receiving agent cannot rewrite its own principal, parent, trust class, scopes, or expiry;
8. a protocol adapter may authenticate an external agent but cannot make it trusted merely because authentication succeeded.

### 5.4 Identity-provider boundary

Manager core should consume verified principal assertions through an adapter boundary. Candidate deployment adapters may include OIDC/JWT, workload identity, SPIFFE/SVID, mTLS identities, service accounts, or organization-specific identity providers.

Manager core should not mint vendor-specific cloud credentials or define organization IAM policy.

### 5.5 Phase 1 acceptance criteria

Before Phase 1 is considered complete:

- caller identity and execution-principal identity are explicitly separated;
- tests prove authentication cannot become tool authorization or approval;
- a delegated principal cannot amplify scope;
- expiry/revocation/version changes fail closed for consequential work;
- durable checkpoints bind enough principal/delegation identity to reject stale resumption;
- identity data reaching models/traces is minimized and explicitly classified;
- existing approval and recovery behavior remains intact.

## 6. Phase 2: policy gateway

### 6.1 Purpose

Manager currently has strong tool-policy boundaries. The target is to generalize this into one logical enforcement plane for every privileged cross-boundary interaction.

The gateway should govern:

- user/service → agent;
- agent → agent;
- agent → tool;
- agent → MCP server/tool;
- agent → API/application endpoint;
- agent → model where provider/data policy matters;
- future agent-protocol traffic.

This does not require one network proxy process. "Policy gateway" is a logical enforcement contract that may be embedded in-process or deployed as a service.

### 6.2 Policy inputs

A gateway decision may depend on application-owned facts such as:

```text
principal
current delegated authority
requested capability/action
target identity
tool/resource registry definition
side-effect class
materiality / risk class
approval state
budget state
network / sandbox profile
provider/data-transfer policy
current policy revision
verification requirement
```

Model output, remote descriptions, remote MCP annotations, remote agent claims, and prompt text are never trusted policy inputs unless independently mapped to application-owned policy data.

### 6.3 Default-deny rule

For privileged egress or side effects, lack of an applicable allow rule should mean deny.

The gateway must not use "reachable" or "registered" as synonyms for "authorized".

### 6.4 Gateway bypass prevention

Internal calls must not become a back door. A direct library call, local agent handoff, retry path, resumed durable action, or alternate protocol must preserve the same material policy checks as the normal path.

Implementation should prefer a small number of enforceable choke points instead of duplicating policy logic across adapters.

### 6.5 Phase 2 acceptance criteria

- agent-to-agent and agent-to-resource interactions use a common authorization decision contract;
- tests prove alternate transports and resumed runs cannot bypass the gate;
- policy revision is part of stale-state analysis where material;
- gateway decisions remain deterministic for protected constraints;
- denial does not rely on model compliance;
- approval semantics from current Manager are preserved rather than replaced by generic IAM allow rules.

## 7. Phase 3: sandbox and resource isolation

### 7.1 Purpose

Authorization answers whether an action may be attempted. Isolation limits damage when authorized code, tools, agents, or integrations are buggy or compromised.

Manager should treat sandboxing as a separate control plane rather than assuming policy correctness prevents all runtime compromise.

### 7.2 Target isolation profile

A sandbox profile should eventually be able to constrain:

```text
CPU / wall-clock
memory
process count
filesystem mounts and write paths
network destinations / ports / protocols
localhost / metadata-service access
DNS behavior where enforceable
secret and credential exposure
syscalls / capabilities where supported
child-process execution
temporary storage
artifact export
```

The implementation may vary by deployment: OS process isolation, containers, microVMs, browser sandboxes, remote execution services, or other mechanisms.

### 7.3 Deny-by-default expectations

Untrusted or generated code should not automatically receive:

- host filesystem access;
- host-local service access;
- cloud metadata access;
- broad internet egress;
- deployment credentials;
- other agents' durable state;
- arbitrary subprocess creation;
- production-network access.

Exceptions must be application-owned and explicit.

### 7.4 Sandbox is not authorization

A sandboxed action still requires normal identity, policy, approval, and verification. Conversely, an authorized action should still be sandboxed when execution risk warrants it.

### 7.5 Phase 3 acceptance criteria

- at least one reference isolation backend has executable tests;
- network/localhost/metadata-service boundaries are tested adversarially;
- resource exhaustion has explicit ceilings;
- secrets are not injected by default;
- sandbox escape is treated as residual risk rather than claimed impossible;
- failure/timeout cleanup is verified;
- sandbox policy cannot be lowered by model or remote tool output.

## 8. Phase 4: governed registry substrate

### 8.1 Common envelope, typed resources

Manager should converge its resource inventories on a common governed envelope while preserving type-specific contracts.

A future registry may expose resource types such as:

```text
agent
skill
tool
mcp_server
endpoint
model
sandbox_profile
policy_bundle
```

A common envelope may include:

```text
resource_id
resource_type
version
owner
publisher / provenance
trust_class
capabilities
policy bindings
authority requirements
health / lifecycle state
cost profile
sensitivity / data-transfer class
created_at / updated_at
```

Type-specific fields remain separately validated. A single loose metadata bag is explicitly not the goal.

### 8.2 Trusted metadata rule

For security-sensitive fields, the registry must be application-owned.

Remote discovery may supply evidence such as a name, schema, endpoint, or advertised capability, but Manager must map that evidence to a reviewed local resource definition before it can influence:

- side-effect class;
- authorization;
- approval requirements;
- verifier selection;
- sensitive-output policy;
- model-facing descriptions where prompt injection is relevant;
- sandbox profile;
- credential binding;
- allowed destinations.

This extends the current ToolRegistry/MCP trust model rather than replacing it.

### 8.3 Skills

A skill should be modeled as a reusable method or workflow, not execution authority.

A future skill contract should be able to declare:

```text
objective / applicability
required capabilities
workflow or instructions
input/output contract
constraints
authority prerequisites
verification contract
version
provenance
eval evidence
```

Installing or selecting a skill must never grant the underlying tools or scopes automatically.

### 8.4 Phase 4 acceptance criteria

- registry resources are versioned and provenance-bearing;
- sensitive policy metadata is locally authoritative;
- registration does not equal authorization;
- version drift invalidates stale consequential assumptions where relevant;
- duplicate/ambiguous identities fail closed;
- skill selection cannot create tool authority;
- registry compromise is included in the threat model and verification strategy.

## 9. Phase 5: persistent agents and ephemeral workers

### 9.1 Two different lifetime models

Manager should explicitly support two agent lifetime classes:

**Ephemeral worker**

- lifetime bounded to a task/run/work package;
- receives narrow delegated scope;
- minimal retained state;
- identity expires with the work or lease;
- ideal for parallel research, coding, evaluation, or specialized execution.

**Persistent agent**

- stable principal identity across runs;
- versioned role/capability contract;
- governed state namespace;
- explicit owner and lifecycle;
- revocable/suspendable;
- suitable for durable organizational roles where persistence creates real value.

### 9.2 Capabilities are not automatically persistent agents

Manager must preserve its existing minimum-complexity principle. A named capability, Council role, evaluator function, or skill does not require a permanently running agent.

Persistent agents should exist only when stable identity, durable responsibility, asynchronous continuity, or role-specific state materially improves the system.

### 9.3 Persistent-state authority

Persistent agent memory is not automatically canonical truth.

Persisted knowledge should carry, where useful:

- source/provenance;
- freshness;
- confidence;
- sensitivity;
- owner;
- authority level;
- expiry/retention policy.

Repository governance, application systems of record, explicit approvals, and other authoritative sources keep their existing precedence.

### 9.4 Lifecycle controls

Persistent agents need explicit lifecycle states such as provisioned, active, suspended, revoked, retired, or equivalent.

Suspension/revocation must stop new consequential work and invalidate stale delegated authority. Resumption must revalidate the current role version, policy, registry dependencies, and required credentials.

### 9.5 Phase 5 acceptance criteria

- ephemeral and persistent identities are distinguishable in contracts and traces;
- persistent state cannot silently become authority;
- role/version change invalidates stale execution assumptions;
- suspend/revoke behavior is executable and tested;
- no standing human approval is inherited by a persistent agent;
- multi-agent fan-out has finite depth/concurrency/budget controls;
- handoffs preserve one accountable decision owner and bounded authority.

## 10. Phase 6: multi-model routing and economic governance

### 10.1 Model choice remains an adapter concern plus Manager policy

Manager should choose models from empirical capability and deployment constraints rather than vendor hierarchy.

A routing decision may consider:

```text
task capability requirements
consequence / materiality
uncertainty
context size
latency target
cost
privacy / data residency
provider availability
tool/function support
historical eval performance
failure rate
```

### 10.2 ModelCapabilityProfile

Manager may eventually maintain versioned evidence-backed profiles for candidate model/provider combinations. Scores should come from Manager's own evals and operational evidence, not vendor marketing claims.

A cheaper or faster model may be selected only when protected constraints remain satisfied.

### 10.3 Budgets

Run budgets should eventually support at least:

```text
model turns
tool calls
wall-clock time
token / model spend
tool / external API spend
delegation count / fan-out
concurrent workers
result size
human-interruption budget where a product chooses to model it
```

Delegation must not reset budgets. Restart must not reset budgets. Switching models must not reset budgets.

### 10.4 Safety floors

The router must not lower mandatory control quality because of cost optimization.

Examples:

- a security verifier requiring a certain evaluated capability floor cannot be downgraded solely to save cost;
- data-classification rules can prohibit providers regardless of model score;
- material work may require independent evaluation even if one model appears sufficient;
- provider failover must preserve the same authority and tool-policy boundary.

### 10.5 Phase 6 acceptance criteria

- routing is reproducible enough to explain which policy/evidence factors selected a model;
- provider adapters remain unable to own authority;
- budget exhaustion produces an explicit blocked/terminal condition rather than silent degradation;
- model switching does not widen tool exposure or data access;
- empirical eval evidence supports production routing claims;
- cost optimization cannot disable protected verification or approval requirements.

## 11. Interoperability boundary

MCP remains one interoperability adapter. Future protocols such as A2A should follow the same rule:

```text
Manager core
  └─ interoperability adapters
      ├─ MCP
      ├─ A2A
      ├─ REST/HTTP
      ├─ events/webhooks
      └─ future protocols
```

Protocol authentication may establish a remote identity claim. It does not establish Manager authorization, approval, trust class, or safe semantics by itself.

## 12. Interaction with active production work

This RFC intentionally avoids editing current implementation surfaces owned by active production branches.

### PR #21: security identity and credential architecture

The service-caller identity work should be treated as a foundation, not replaced.

Required compatibility:

- retain the separation between API authentication and Manager tool authority;
- use future `ExecutionPrincipal` concepts beneath or alongside service-caller identity, not by copying untrusted caller claims into model/tool authority;
- keep deployment identity-provider choice outside canonical core;
- preserve credential rotation and secret minimization.

### PR #46: production service/API gateway

The production gateway remains the network service boundary. This RFC's policy gateway is broader and logical: it governs cross-boundary interactions inside and outside the HTTP service.

Required compatibility:

- clients cannot submit trusted registry, provider, MCP, authorization, or policy objects;
- accepted work remains durable and idempotent according to service contracts;
- future agent identity must not weaken subject isolation;
- API cancellation/recovery remains subordinate to durable execution semantics.

### PR #45: MCP localhost/SSRF hardening

The MCP network policy work is a direct precursor to Phase 3 isolation.

Required compatibility:

- deny-by-default egress remains preferred;
- localhost/loopback exceptions remain explicit and application-owned;
- remote MCP metadata cannot select its own network or sandbox policy.

The RFC branch should be reconciled with these PRs if they land before this RFC merges. Their implementation truth takes precedence over stale wording in this document.

## 13. Red-team review

The architecture was challenged against likely failure modes before implementation.

| Attack / failure | Required architectural response |
| --- | --- |
| Agent delegates more authority than it owns | monotonic scope reduction, parent-authority revalidation, fail closed |
| API caller identity is copied into tool authority | hard separation of ingress authn/API authorization from Manager execution authority |
| Agent self-registers a high-trust tool or destination | registry writes require separate application-owned authority; registration does not grant use |
| Remote MCP/agent description declares itself read-only/safe | remote metadata remains evidence only; local trusted definition controls risk class |
| Internal library path bypasses network policy gateway | policy enforcement contract applies in-process as well as over network boundaries |
| Persistent agent keeps authority after role removal | versioned identity/role plus currentness/revocation check before consequential execution |
| Approval is replayed by another principal | bind principal/delegation identity when material; stale on material identity/authority change |
| Delegation creates infinite sub-agents | bounded fan-out, depth, concurrency, time, and cost; child budgets consume parent budget |
| Router chooses a cheaper but unsafe model | protected capability/data/safety floors precede cost optimization |
| Model/provider switch expands tools | allowed capability/tool set remains Manager-owned and invariant across routing unless separately authorized |
| Sandbox can access host-local services | explicit network namespace/egress controls; loopback/metadata access denied unless reviewed |
| Secrets leak into generated code sandbox | secrets absent by default; scoped broker/credential binding where needed |
| Registry compromise poisons policy metadata | registry integrity, versioning, provenance, audit, and independent verification for consequential resources |
| Policy gateway becomes a single availability bottleneck | fail-safe deployment design, explicit health/readiness, no bypass-on-failure mode |
| Policy change races a durable resume | policy/authority revision participates in stale checkpoint revalidation where material |
| A2A peer is authenticated but malicious | authentication proves identity only; local policy, registry, sandbox, budgets, and verification still apply |
| Memory poisoning changes a protected rule | memory carries provenance/authority metadata; protected/canonical sources retain precedence |
| Persistent agent accumulates excessive context | least-necessary context namespaces, retention limits, sensitivity controls, explicit sharing |

Residual risk remains for compromised hosts, kernel/container escapes, compromised maintainers/identity providers, malicious trusted application configuration, semantic tool drift that preserves schemas, and external effects that cannot be made exactly-once. This RFC does not claim to eliminate those classes.

## 14. Implementation sequence and dependency gates

Implementation should proceed in this order because each later layer depends on the previous one:

```text
Phase 1  Identity + delegation
   ↓
Phase 2  Policy gateway
   ↓
Phase 3  Sandbox/isolation
   ↓
Phase 4  Governed registry substrate
   ↓
Phase 5  Persistent agents
   ↓
Phase 6  Multi-model economic routing
```

Registry groundwork may be prototyped earlier where needed, but persistent-agent authority should not ship before identity, policy, and isolation contracts are credible.

Each implementation phase should include:

1. contract/schema proposal;
2. threat-model delta;
3. deterministic policy tests where applicable;
4. adversarial regression tests;
5. durable-state/recovery impact analysis;
6. public/private boundary review;
7. documentation update;
8. explicit residual-risk statement;
9. CI evidence before readiness claims.

## 15. Migration principles

Manager is pre-stable, but migration should still avoid unnecessary breakage.

- Existing `AuthorizationContext`/tool policy should evolve incrementally rather than being discarded.
- ToolRegistry remains authoritative until a broader registry demonstrably subsumes it without weakening security semantics.
- Existing MCP bindings remain local-trust definitions.
- Existing durable approval fingerprints and `recovery_required` semantics remain mandatory.
- Current model adapters should continue working with explicit model selection while routing is added above them.
- Existing direct/simple workflows must remain possible. Adding platform primitives must not force every task through multiple agents.

## 16. Non-goals

This RFC does not select:

- a production identity provider;
- a canonical cloud IAM system;
- a canonical sandbox vendor/runtime;
- a production database or distributed lock service;
- a service mesh;
- a secret manager;
- a default model provider;
- a default persistent-agent roster;
- an organizational permission taxonomy;
- a memory database;
- A2A as a required protocol;
- exactly-once external side effects;
- universal production-readiness status.

## 17. Success criteria for the architecture program

The program is successful when Manager can demonstrate, with executable evidence, that:

- every consequential action is attributable to a current execution principal;
- delegation cannot silently amplify authority;
- every privileged cross-boundary interaction encounters enforceable policy;
- high-risk execution is isolated with bounded resources and egress;
- registered resources have trusted provenance/version/policy metadata;
- persistent agents can be revoked and cannot convert memory into authority;
- ephemeral delegation remains bounded and budgeted;
- model routing is provider-neutral, evidence-backed, and cost-aware without weakening controls;
- protocols and providers remain replaceable adapters;
- current approval, durable-state, recovery, verification, and reconciliation guarantees remain intact.

## 18. External evidence considered

These public sources informed the comparison that triggered this RFC. They are evidence only and do not override Manager governance:

- Google Cloud, "Welcome to Gemini at Work 2026: Introducing the Gemini agent" (2026-10-08): https://cloud.google.com/blog/products/ai-machine-learning/welcome-to-gemini-at-work-2026
- Google Cloud, "Agent Identity overview": https://docs.cloud.google.com/gemini-enterprise-agent-platform/govern/agent-identity-overview
- Google Cloud, "Agent Gateway overview": https://docs.cloud.google.com/gemini-enterprise-agent-platform/govern/gateways/agent-gateway-overview
- Google Cloud, "Agent Registry": https://docs.cloud.google.com/gemini-enterprise-agent-platform/govern/agent-registry

The reusable architectural lesson is the convergence around identity, enforcement, isolation, inventory, persistent execution, and multi-model economics. Manager's implementation remains provider-neutral and governed by its own evidence and security contracts.
