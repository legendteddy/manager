# Threat model

This threat model defines the security assumptions of Manager's public reference architecture. It is a design and review aid, not a security certification.

## Security objectives

Manager aims to preserve:

- **authority integrity**: untrusted content, models, tools, and remote servers cannot silently expand their authority;
- **approval integrity**: consequential actions execute only against the exact approved action, target, arguments, and current authority state;
- **identity integrity**: authentication facts are validated explicitly and cannot silently become authorization;
- **credential integrity**: secret material remains externally injected and outside canonical contracts and durable state;
- **state integrity**: durable state cannot silently resurrect stale or uncertain work;
- **public/private separation**: private operational data is not promoted into the public framework by default;
- **execution integrity**: tool and MCP calls remain behind application-owned policy, verification, and recovery controls;
- **evidence integrity**: maturity and safety claims remain proportional to what was actually tested;
- **release integrity**: distributed artifacts must be traceable to an explicitly approved source revision and release process.

## Trust boundaries

### Human principal / maintainer

Human instructions may authorize actions within the surrounding product's authority model. Human approval can still be mistaken, stale, socially engineered, or bound to incomplete information, so exact action binding and revalidation remain necessary. Approval is not authorization.

### API, workload, and service identity

Authenticated identity is application-owned security context. The reference security module includes a strict JWT validation boundary and a deny-by-default capability policy evaluator. Production deployments may use another identity mechanism, but must validate equivalent issuer, audience, lifetime, subject, algorithm/signature, capability, and revocation properties before constructing current authorization context.

### Model provider

Model output is untrusted proposal content. A model may propose text or tool actions but does not own side-effect class, authorization, approval, verification, state ownership, recovery decisions, identity, or credential material.

### Retrieved and external content

Webpages, files, messages, repository content, tool results, model output, and other retrieved material are data, not instructions. Prompt injection from these sources must not redefine governance or permissions.

### Native tools and external APIs

Tool implementations may fail, return misleading data, partially execute, or produce an uncertain outcome. Consequential effects require application-owned classification, authorization, approval, and verification.

### MCP servers and transports

MCP discovery metadata and results are untrusted. Remote descriptions and annotations do not define Manager policy. Server identity, exact tool presence, and reviewed schema are revalidated before execution in the reference adapter. URL-based official MCP clients can additionally opt into Manager's network policy gate before the SDK connects.

### Credential and secret providers

Secret acquisition is an external-injection boundary. Manager's reference `SecretProvider` interface returns ephemeral `SecretLease` values that redact representation and reject serialization. Environment and mounted-file implementations are examples, not a claim that those sources are sufficient for every deployment. External secret managers and workload identity remain application integrations.

### Network and trusted proxy boundary

Transport security is configuration owned by the embedding application and its concrete HTTP/MCP/provider clients. The reference `NetworkSecurityPolicy` makes production TLS verification, redirect, credential-forwarding, CA/mTLS configuration, and trusted-proxy expectations explicit. The official MCP URL bridge applies URL-level policy at creation and again before operations, but a policy object still does not prove a third-party SDK consumed a supplied SSL context or enterprise egress policy.

### Durable state store

Persisted state may be stale, malformed, corrupted, or restored from an unexpected point. Loaded state is validated before it can drive execution. The SQLite reference adapter is not an encryption or distributed-consensus boundary. Raw credentials and bearer tokens must not be treated as durable run state.

### Repository and CI

Repository history, pull requests, workflows, dependencies, runners, and build tooling are part of the software-supply-chain boundary. Branch rules and CI reduce accidental or unauthorized change but do not make a compromised maintainer, dependency, GitHub account, or hosted runner harmless.

### Distribution channel

Future package indexes, release artifacts, containers, or other distribution mechanisms introduce an additional trust boundary. Manager currently has no public package publication workflow and therefore makes no artifact-provenance claim beyond repository commits and CI evidence.

## Threats and current mitigations

| Threat | Implemented / tested protection | Deployment assumption / residual risk |
| --- | --- | --- |
| Prompt injection changes authority | External content is treated as data; policy fields are application-owned; deterministic gates enforce critical constraints. | A privileged application can still pass unsafe authority or expose excessive tools. |
| Authentication is mistaken for authorization | Strict security envelopes evaluate principal + capability + action + resource + environment + side-effect class + current policy. Authentication alone grants nothing. Tests cover authenticated-but-denied and wrong principal type. | The embedding application must only pass validated identity facts as trusted security context. |
| Forged or malformed bearer identity | Reference JWT validator uses strict base64url/JSON parsing, rejects duplicate members, verifies an allowed algorithm, minimum-strength HMAC key, signature, issuer, audience, subject, expiry, not-before, optional issued-at/JTI, capability shape, and optional revocation. Tests cover signature tampering, malformed/ambiguous claims, weak signing keys, expiry, issuer, audience, algorithm, and revocation. | The built-in zero-dependency validator is HS256 only. Production asymmetric/workload identity verification and key distribution are deployment integrations. |
| Revoked identity remains usable after initial authentication | Strict authorization may call an application-owned `principal_revalidator` on every evaluation, including the final check immediately before a tool side effect. Tests revoke the principal between approval validation and execution and require zero adapter calls. Revalidation backend failures fail closed with redacted messages. | The deployment owns revocation freshness, backend availability, emergency revocation semantics, and whether every security-sensitive entry point supplies a revalidator. |
| Model fabricates or widens tool permission | Trusted `ToolRegistry` owns side-effect metadata, allowed tools, and verification requirements; authorization context is application-owned. | Incorrect registry or policy configuration remains trusted application error. |
| Stale approval is replayed | Approval action fingerprints bind target/arguments/tool identity. With strict authorization, `approval.extensions.security` also binds current principal facts, capability, environment, policy revision, resource, and side-effect class. Execution re-evaluates authorization immediately before the side effect. Tests cover changed policy, changed principal, expired identity, denied capability, and live principal revocation after approval. | Human approval may still be based on incomplete or misleading context. Legacy callers that do not opt into a strict security envelope retain coarse authorization semantics. |
| Restart expands authority or resurrects credentials | Durable checkpoints persist approval bindings but intentionally exclude strict identity/policy objects and raw tokens. Consequential resume requires fresh current authorization before durable execution intent and revalidates the approval binding again before the side effect. Tests cover fresh resume, policy/principal drift, capability loss, expired identity, and credential-marker non-persistence. | The embedding application must provide fresh current identity/policy context on resume. Durable storage confidentiality remains deployment-specific. |
| Credential value enters logs/checkpoints/contracts | Secret acquisition is externalized; `SecretLease` redacts representation and rejects serialization; secret-provider, revocation, provider, tool and MCP failures expose bounded classifications rather than raw exception messages. Chained providers do not downgrade past access-denied failures. Tests cover serialization, rotation, provider failure redaction, chain behavior, and hostile secret-bearing errors. | Application logging outside Manager can still leak data if it logs raw SDK objects, headers, payloads, or secret-store responses. Secret-store access controls and rotation are deployment responsibilities. |
| Process crashes during consequential execution | Durable state records execution intent; uncertain outcomes enter `recovery_required` rather than automatic retry. | Exactly-once external effects are not guaranteed. |
| Persisted state is corrupted or incompatible | State shape, legal transitions, revisions, and checkpoint versions are validated; future unknown versions fail closed. | Storage confidentiality, availability, and host compromise are outside the SQLite reference guarantee. |
| MCP tool changes after registration | Remote server identity, exact tool presence, and schema fingerprint are rechecked before execution. | A malicious server can preserve schema while changing semantics. Independent verification is still needed for consequential effects. |
| MCP target is cleartext, credential-bearing, or changed after setup | With an explicit production `NetworkSecurityPolicy`, the official URL-based MCP bridge rejects cleartext and URL-embedded credentials at construction and rechecks the target immediately before discovery/call operations. Tests also cover post-construction target mutation. | Custom transport objects and SDK-internal CA/mTLS/OAuth behavior remain application/SDK concerns. Production callers must actually opt into the policy. |
| Cross-origin redirect leaks credentials | Existing Streamable HTTP conformance tests reject cross-origin redirects. `NetworkSecurityPolicy` rejects cross-origin redirects by default and strips Authorization, proxy authorization, cookies, API keys, and Host if an embedding explicitly allows an origin change. | Concrete SDK redirect behavior must still be verified. OAuth token forwarding rules and enterprise proxies remain deployment-specific. |
| TLS validation is disabled in production | `NetworkSecurityPolicy(production=True)` rejects `verify_tls=False`; HTTPS is the production URL expectation; generated SSL contexts require hostname/certificate verification and TLS 1.2+. | Concrete transports must actually consume the intended policy/SSL context. CA roots, mTLS identities, certificate lifecycle, and enterprise egress need deployment evidence. |
| Proxy headers spoof caller identity | Forwarded client IP is ignored from untrusted peers. For trusted proxy chains, X-Forwarded-For is evaluated from the immediate peer outward so attacker-prepended leftmost values do not win. Tests cover untrusted peers and multi-hop chains. | Trusted proxy CIDRs/topology must be configured correctly. Other proxy headers require equivalent handling where used. |
| Malformed or hostile remote response | SDK/protocol failures are normalized and tested malformed responses fail closed. | Transport fuzzing and hostile-wire robustness are not comprehensive. |
| Sensitive tool output reaches model or trace | Sensitive outputs can be withheld from continuation; public traces must minimize sensitive content. | Correct sensitivity classification remains an application responsibility. |
| Secret or private data is committed publicly | Public/private policy plus repository-integrity scanning for high-confidence secret, risky filename, email, contract, and fixture patterns. | Pattern scanning is incomplete and cannot prove absence of secrets or proprietary context. |
| Unauthorized default-branch mutation | Active repository rules require pull requests and the `public-safety` check; deletion and non-fast-forward changes are blocked with no bypass actor. | Maintainer-account compromise and weaknesses in the required check remain material risks. |
| CI token is abused | Workflow requests `contents: read` only and uses shell/git rather than broad write permissions. | Hosted runner and dependency-install compromise remain outside this control. |
| Dependency update silently changes behavior | Version ranges are bounded by major versions and transport behavior is tested in CI. | CI is not fully locked or hashed, so an allowed dependency release can change the environment. |
| Published artifact differs from reviewed source | No publication workflow exists, so no release is claimed. Release policy requires exact commit/artifact binding before future publication. | Artifact provenance, signing, SBOM, and trusted publishing remain future work. |
| Denial of service or unbounded loop | Agent loops have explicit model/tool/result budgets; MCP operations can use timeouts; the reference JWT validator bounds token size. | Host-level CPU, memory, network exhaustion, and adversarial workload limits are not comprehensively modeled. |

## Consequential assumptions

The reference runtime assumes the embedding application correctly supplies:

- an authenticated principal where identity matters;
- a current security policy and authorization context for strict production authorization;
- fresh identity/policy context again when resuming consequential durable work;
- live principal/revocation revalidation where the deployment requires immediate revocation;
- verified target information when required;
- trusted local tool metadata and implementations;
- an appropriate verifier for consequential effects;
- secure credential/secret injection and rotation outside canonical Manager contracts;
- an appropriate asymmetric/workload identity verifier when HS256 is not suitable;
- revocation data whose freshness, availability and failure behavior match deployment policy;
- appropriate filesystem/database protection for durable state;
- concrete network, TLS, proxy, OAuth, CA, mTLS, redirect, and certificate policy for production transports;
- explicit use of `NetworkSecurityPolicy` or an equivalent control on relevant network clients;
- operational monitoring and incident response appropriate to the deployment.

Manager cannot recover guarantees that the embedding application does not provide.

## Explicit non-goals and unproven areas

The current evidence does not prove:

- resistance to a compromised host, CI runner, maintainer account, dependency, or model provider;
- production safety of any particular OAuth/OIDC provider, asymmetric JWT/JWKS integration, workload identity system, or revocation backend;
- that every third-party provider/MCP SDK consumes `NetworkSecurityPolicy`, its CA configuration, or its SSL context;
- correctness of a deployment's mTLS certificate issuance/rotation, trusted proxy topology, or enterprise egress controls;
- operating-system or container sandbox isolation;
- arbitrary MCP-server trustworthiness;
- semantic equivalence when a remote server keeps the same schema but changes behavior;
- distributed locking, high availability, or consensus;
- exactly-once provider calls or external side effects;
- complete secret detection or complete log redaction;
- comprehensive protocol fuzzing;
- supply-chain provenance for published packages;
- production readiness.

## Review triggers

Revisit this threat model when a change introduces or materially changes:

- a new external transport or provider;
- provider-managed tool execution;
- authentication, authorization, identity, revocation, or credential storage;
- public package/container publishing;
- a new durable-state backend;
- distributed execution or locking;
- automatic retries of consequential effects;
- expanded network access;
- proxy/TLS/mTLS/CA assumptions;
- sandbox/process isolation claims;
- security-sensitive authority or approval semantics.

Security-relevant behavior changes should add deterministic tests or evals where practical and must not weaken an existing gate merely to make an implementation pass. See `docs/security-identity.md` for the reference security boundary and its deployment requirements.
