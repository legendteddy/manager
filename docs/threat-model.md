# Threat model

This threat model defines the security assumptions of Manager's public reference architecture. It is a design and review aid, not a security certification.

## Security objectives

Manager aims to preserve:

- **authority integrity**: untrusted content, models, tools, and remote servers cannot silently expand their authority;
- **approval integrity**: consequential actions execute only against the exact approved action, target, arguments, and current authority state;
- **state integrity**: durable state cannot silently resurrect stale or uncertain work;
- **public/private separation**: private operational data is not promoted into the public framework by default;
- **execution integrity**: tool and MCP calls remain behind application-owned policy, verification, and recovery controls;
- **evidence integrity**: maturity and safety claims remain proportional to what was actually tested;
- **release integrity**: distributed artifacts must be traceable to an explicitly approved source revision and release process.

## Trust boundaries

### Human principal / maintainer

Human instructions may authorize actions within the surrounding product's authority model. Human approval can still be mistaken, stale, socially engineered, or bound to incomplete information, so exact action binding and revalidation remain necessary.

### Model provider

Model output is untrusted proposal content. A model may propose text or tool actions but does not own side-effect class, authorization, approval, verification, state ownership, or recovery decisions.

### Retrieved and external content

Webpages, files, messages, repository content, tool results, model output, and other retrieved material are data, not instructions. Prompt injection from these sources must not redefine governance or permissions.

### Native tools and external APIs

Tool implementations may fail, return misleading data, partially execute, or produce an uncertain outcome. Consequential effects require application-owned classification, authorization, approval, and verification.

### MCP servers and transports

MCP discovery metadata and results are untrusted. Remote descriptions and annotations do not define Manager policy. Server identity, exact tool presence, and reviewed schema are revalidated before execution in the reference adapter.

### Durable state store

Persisted state may be stale, malformed, corrupted, or restored from an unexpected point. Loaded state is validated before it can drive execution. The SQLite reference adapter is not an encryption or distributed-consensus boundary.

### Repository and CI

Repository history, pull requests, workflows, dependencies, runners, and build tooling are part of the software-supply-chain boundary. Branch rules and CI reduce accidental or unauthorized change but do not make a compromised maintainer, dependency, GitHub account, or hosted runner harmless.

### Distribution channel

Future package indexes, release artifacts, containers, or other distribution mechanisms introduce an additional trust boundary. Manager currently has no public package publication workflow and therefore makes no artifact-provenance claim beyond repository commits and CI evidence.

## Threats and current mitigations

| Threat | Current mitigation | Residual risk |
| --- | --- | --- |
| Prompt injection changes authority | External content is treated as data; policy fields are application-owned; deterministic gates enforce critical constraints. | A privileged application can still pass unsafe authority or expose excessive tools. |
| Model fabricates or widens tool permission | Trusted `ToolRegistry` owns side-effect metadata, allowed tools, and verification requirements. | Incorrect registry configuration remains trusted application error. |
| Stale approval is replayed | Approval fingerprints bind action, target, arguments, and tool definition; resume revalidates current authority. | Human approval may still be based on incomplete or misleading context. |
| Process crashes during consequential execution | Durable state records execution intent; uncertain outcomes enter `recovery_required` rather than automatic retry. | Exactly-once external effects are not guaranteed. |
| Persisted state is corrupted or incompatible | State shape, legal transitions, revisions, and checkpoint versions are validated; future unknown versions fail closed. | Storage confidentiality, availability, and host compromise are outside the SQLite reference guarantee. |
| MCP tool changes after registration | Remote server identity, exact tool presence, and schema fingerprint are rechecked before execution. | A malicious server can preserve schema while changing semantics. Independent verification is still needed for consequential effects. |
| Cross-origin HTTP redirect leaks trust context | Tested Streamable HTTP path rejects cross-origin redirects and normalizes the failure at Manager's boundary. | Production proxy, TLS, certificate, OAuth, and enterprise egress policies are not established. |
| Malformed or hostile remote response | SDK/protocol failures are normalized and tested malformed responses fail closed. | Transport fuzzing and hostile-wire robustness are not comprehensive. |
| Sensitive tool output reaches model or trace | Sensitive outputs can be withheld from continuation; public traces must minimize sensitive content. | Correct sensitivity classification remains an application responsibility. |
| Secret or private data is committed publicly | Public/private policy plus repository-integrity scanning for high-confidence secret, risky filename, email, contract, and fixture patterns. | Pattern scanning is incomplete and cannot prove absence of secrets or proprietary context. |
| Unauthorized default-branch mutation | Active repository rules require pull requests and the `public-safety` check; deletion and non-fast-forward changes are blocked with no bypass actor. | Maintainer-account compromise and weaknesses in the required check remain material risks. |
| CI token is abused | Workflow requests `contents: read` only and uses shell/git rather than broad write permissions. | Hosted runner and dependency-install compromise remain outside this control. |
| Dependency update silently changes behavior | Version ranges are bounded by major versions and transport behavior is tested in CI. | CI is not fully locked or hashed, so an allowed dependency release can change the environment. |
| Published artifact differs from reviewed source | No publication workflow exists, so no release is claimed. Release policy requires exact commit/artifact binding before future publication. | Artifact provenance, signing, SBOM, and trusted publishing remain future work. |
| Denial of service or unbounded loop | Agent loops have explicit model/tool/result budgets; MCP operations can use timeouts. | Host-level CPU, memory, network exhaustion, and adversarial workload limits are not comprehensively modeled. |

## Consequential assumptions

The reference runtime assumes the embedding application correctly supplies:

- authenticated human identity where identity matters;
- current authorization scope;
- verified target information when required;
- trusted local tool metadata and implementations;
- an appropriate verifier for consequential effects;
- secure credential and secret storage outside Manager contracts;
- appropriate filesystem/database protection for durable state;
- network, TLS, proxy, OAuth, and certificate policy for production transports;
- operational monitoring and incident response appropriate to the deployment.

Manager cannot recover guarantees that the embedding application does not provide.

## Explicit non-goals and unproven areas

The current evidence does not prove:

- resistance to a compromised host, CI runner, maintainer account, dependency, or model provider;
- production OAuth, credential, TLS/mTLS, proxy, or certificate safety;
- operating-system or container sandbox isolation;
- arbitrary MCP-server trustworthiness;
- semantic equivalence when a remote server keeps the same schema but changes behavior;
- distributed locking, high availability, or consensus;
- exactly-once provider calls or external side effects;
- complete secret detection;
- comprehensive protocol fuzzing;
- supply-chain provenance for published packages;
- production readiness.

## Review triggers

Revisit this threat model when a change introduces or materially changes:

- a new external transport or provider;
- provider-managed tool execution;
- authentication or credential storage;
- public package/container publishing;
- a new durable-state backend;
- distributed execution or locking;
- automatic retries of consequential effects;
- expanded network access;
- sandbox/process isolation claims;
- security-sensitive authority or approval semantics.

Security-relevant behavior changes should add deterministic tests or evals where practical and must not weaken an existing gate merely to make an implementation pass.