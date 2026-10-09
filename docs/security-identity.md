# Security, identity, credentials, and network trust

This document describes the reference security boundaries added for production-oriented embeddings of Manager. They are framework controls and integration points, not a deployment-specific production certification.

## Boundary model

Manager keeps four questions separate:

1. **Authentication**: who or what presented an identity, and was the identity cryptographically validated?
2. **Authorization**: may that principal use a specific capability for this action, resource, environment, and side-effect class under the current policy revision?
3. **Approval**: did an authorized human approve the exact consequential action that was reviewed?
4. **Execution**: immediately before the side effect, are the current identity, authorization, target checks, approval, and trusted tool definition still valid?

Authentication never implies authorization. Approval never substitutes for authorization. Model output, MCP discovery metadata, and tool output never become authority inputs.

## Authentication boundary ownership

Manager deliberately does not collapse every dependency into one bearer-token model. The embedding application owns authentication at each external boundary and passes only validated, non-secret facts into Manager policy.

| Boundary | Trusted authentication fact | Manager enforcement / integration |
| --- | --- | --- |
| Human or API caller | validated subject, issuer, audience, validity window and capabilities | strict security context can require a current authenticated principal before tool authorization |
| Manager service / worker | workload or service principal with an explicit `principal_type` | policy can restrict principal type, capability, environment, resource and action |
| Model provider | application-selected adapter/provider identity and configured client | provider output is untrusted proposal content and cannot mint Manager authority |
| MCP server | application-selected server identity plus same-session tool/schema revalidation | MCP metadata cannot redefine side-effect class, policy, approval or authorization |
| Tool implementation | application-owned `ToolRegistry` entry and adapter | trusted registry owns tool identity, side-effect class and verification requirements |
| Persistence service | application-owned store instance and storage access policy | persisted run state is validated before use; credentials and raw bearer identity are not durable authority |

A deployment may use OIDC/JWT, workload identity, mTLS identities, SPIFFE/SVIDs, a service mesh, or another mechanism at these boundaries. Manager's reference code does not make any one vendor or cloud canonical.

## Authenticated identity

`manager_runtime.security.HS256JWTValidator` is a zero-dependency reference validator used to make the token-validation boundary executable. It validates:

- an explicitly allowed algorithm;
- a strict base64url/JSON token shape with duplicate JSON members rejected;
- signature using an HMAC key of at least 32 bytes;
- issuer;
- audience;
- subject;
- expiry;
- not-before;
- issued-at when present;
- token identifier when required;
- scope/capability claim shape;
- an optional application-owned revocation checker whose failures are fail-closed and message-redacted.

The validator intentionally supports only HS256. That is not a recommendation to use shared-secret JWTs for every deployment. Production applications using asymmetric JWTs, workload identity, SPIFFE/SVIDs, cloud identity, mTLS identities, or another mechanism should provide an equivalent verifier at the embedding boundary and pass only validated, non-secret principal facts into Manager authorization.

Raw bearer tokens are not part of Manager's canonical contracts and should not enter task objects, model requests, durable checkpoints, traces, or tool arguments.

## Authorization envelope

When `security_context` and `security_policy` are supplied to the governed tool runtime, Manager switches to deny-by-default authorization for that request. Both must be supplied together.

The authorization decision binds:

- principal subject;
- issuer and audience;
- principal type;
- current capabilities;
- identity validity times and token identifier;
- environment;
- current policy revision;
- matched capability;
- exact tool action;
- resource/target;
- trusted side-effect class.

Policy rules are application-owned data. A rule grants a named capability only when action, resource, environment, and side-effect class all match. An approval cannot override a denied capability.

For consequential approvals, the tool runtime strengthens the base authorization decision with a canonical digest of the actual security-policy fields consumed by authorization. This includes the policy rules themselves, not only the application-managed revision label. Therefore a same-revision policy mutation that still permits the current action nevertheless makes an existing approval stale. This is intentionally conservative: human approval remains bound to the authority state that was actually reviewed rather than trusting revision bookkeeping as the sole change detector.

For compatibility, callers that supply neither security envelope continue to use the earlier coarse authorization flags. Production embeddings can set `require_security_context=true` to fail closed when the strict envelope is absent. Removing the legacy compatibility path for every caller would change the public trust/compatibility model and requires an explicit maintainer decision rather than an implicit security-worker policy change.

### Current-principal revalidation

A strict `security_context` may include an application-owned `principal_revalidator` callable. Manager invokes it on each authorization evaluation, including the final check immediately before a consequential tool side effect. This provides a provider-neutral integration point for live revocation, disabled workload identities, terminated sessions, or other current-principal policy.

The callback receives only normalized, non-secret principal facts. Callback failures fail closed and do not expose the backend exception message. The deployment still owns the revocation source, freshness, availability, and emergency-revocation policy.

Consequential approval binding records whether live principal revalidation was active. Removing the callback therefore invalidates an existing approval. Embeddings may also provide a non-empty `principal_revalidator_revision` string alongside the callback. That stable, application-owned label is included in the approval binding so changing the revocation/currentness backend or its security semantics can explicitly stale prior approvals. A revalidator revision without a revalidator is rejected as an invalid security context. If an embedding replaces a callback without changing this optional revision, Manager cannot infer that the implementation changed; deployments should advance the revision whenever the revalidation authority or semantics change.

## Approval and restart safety

When strict authorization is active, consequential approval packets place a digest of the current authorization state in the existing `approval.extensions.security` namespace.

On execution the runtime re-evaluates the current security envelope and compares it with that binding. A changed principal, capability set, token lifetime/identifier, environment, policy revision, policy rules/authorization constraints, resource, action, side-effect class, principal-revalidation presence, or supplied principal-revalidator revision makes the old approval stale.

Durable checkpoints do not serialize tokens, security-policy objects, revocation callbacks, or secret material. The existing durable loop persists the approval binding but its authorization-context snapshot intentionally retains only coarse non-secret booleans. A resumed consequential operation must receive fresh current security context from the embedding application before durable execution intent is recorded. The tool runtime then revalidates that current context and the approval binding again immediately before execution.

This provides fail-closed restart behavior without turning durable state into an identity or credential store. An approval can survive as historical evidence, but it cannot recreate expired or revoked authority.

## Credential providers

`SecretProvider` is a provider-neutral acquisition interface. The reference implementations are:

- `EnvironmentSecretProvider` for externally injected environment values;
- `MountedFileSecretProvider` for mounted secret volumes with path traversal protection;
- `ChainedSecretProvider` for explicit application-owned fallback order.

Every acquisition returns a `SecretLease`. A lease redacts its string representation, rejects serialization, and can carry an expiry. Providers are queried on demand, so rotation does not require a credential value to become canonical Manager state.

`ChainedSecretProvider` falls back only when a provider reports that a credential is unavailable. Authorization/access-denied and other security failures do not silently fall through to a weaker source. Unexpected provider exceptions are converted to structured errors containing the exception type but not its potentially secret-bearing message.

External secret managers and workload-identity systems should implement the same acquisition boundary rather than placing provider-specific secret formats into public Manager contracts.

## Network trust policy

`NetworkSecurityPolicy` centralizes provider-neutral transport requirements that adapters can apply:

- production mode forbids disabling TLS verification;
- HTTPS is the production URL expectation;
- cleartext HTTP is rejected except for explicitly enabled non-production loopback use;
- URL-embedded credentials are rejected;
- TLS contexts require certificate and hostname verification and TLS 1.2 or newer;
- custom CA roots are an external configuration input;
- mTLS certificate/key configuration must be paired;
- HTTPS-to-cleartext redirect downgrade is rejected;
- cross-origin redirects are rejected by default;
- when a caller deliberately permits a cross-origin redirect, sensitive headers including authorization, cookies, API keys, proxy authorization, and Host are stripped;
- forwarded client identity is accepted only through configured trusted proxy networks, and `X-Forwarded-For` is evaluated from the immediate peer outward rather than trusting attacker-prepended leftmost values.

The official MCP SDK bridge accepts an optional `NetworkSecurityPolicy` for URL targets and validates the target both when the client is created and immediately before each operation. This rejects production cleartext endpoints, embedded URL credentials, and later target mutation before the SDK opens that connection. Existing Streamable HTTP conformance tests separately cover cross-origin redirect rejection.

Custom CA, mTLS, proxy, OAuth, and enterprise egress configuration still depend on the concrete transport client selected by the embedding application. `NetworkSecurityPolicy.ssl_context()` is an integration point, not proof that a third-party SDK consumed that SSL context.

## Error handling

Security boundary errors expose stable codes and, where useful, exception class names. Secret-bearing exception messages are not promoted into governed results. Provider, MCP, tool, credential-provider, revocation-backend, and TLS-configuration failures use bounded error information at Manager's public boundary.

A best-effort text redactor exists for application logging surfaces, but avoiding raw sensitive messages remains preferable to trying to scrub them after capture. Applications must not log raw authorization headers, tokens, SDK request objects, secret leases, or sensitive payloads merely because Manager itself redacts governed errors.

## Deployment requirements

A production deployment still needs environment-specific decisions and evidence for:

- identity issuer and key distribution;
- asymmetric/workload identity verifier selection where appropriate;
- revocation source, availability, freshness, and fail-closed behavior;
- policy administration and revision lifecycle;
- secure secret-provider implementation and access controls;
- credential rotation and emergency revocation;
- TLS roots, mTLS identities, certificate lifecycle, egress restrictions, and proxy topology;
- proof that concrete provider/MCP/HTTP SDKs use the intended CA, mTLS, redirect and proxy configuration;
- storage encryption/access controls for durable state;
- logging, monitoring, incident response, and retention;
- independent security review.

These controls reduce framework-level ambiguity. They do not make arbitrary deployments production-ready by themselves.
