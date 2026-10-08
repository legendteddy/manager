# Security, identity, credentials, and network trust

This document describes the reference security boundaries added for production-oriented embeddings of Manager. They are framework controls and integration points, not a deployment-specific production certification.

## Boundary model

Manager keeps four questions separate:

1. **Authentication**: who or what presented an identity, and was the identity cryptographically validated?
2. **Authorization**: may that principal use a specific capability for this action, resource, environment, and side-effect class under the current policy revision?
3. **Approval**: did an authorized human approve the exact consequential action that was reviewed?
4. **Execution**: immediately before the side effect, are the current identity, authorization, target checks, approval, and trusted tool definition still valid?

Authentication never implies authorization. Approval never substitutes for authorization. Model output, MCP discovery metadata, and tool output never become authority inputs.

## Authenticated identity

`manager_runtime.security.HS256JWTValidator` is a zero-dependency reference validator used to make the token-validation boundary executable. It validates:

- an explicitly allowed algorithm;
- signature;
- issuer;
- audience;
- subject;
- expiry;
- not-before;
- issued-at when present;
- token identifier when required;
- scope/capability claim shape;
- an optional application-owned revocation checker.

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

For compatibility, callers that supply neither security envelope continue to use the earlier coarse authorization flags. Production embeddings can set `require_security_context=true` to fail closed when the strict envelope is absent.

## Approval and restart safety

When strict authorization is active, consequential approval packets place a digest of the current authorization decision in the existing `approval.extensions.security` namespace.

On execution the runtime re-evaluates the current security envelope and compares it with that binding. A changed principal, capability set, token lifetime/identifier, environment, policy revision, resource, action, or side-effect class makes the old approval stale.

Durable checkpoints do not need to serialize tokens or secret material. The existing durable loop persists the approval packet but its authorization-context snapshot intentionally retains only coarse non-secret booleans. A resumed consequential operation must receive fresh current security context from the embedding application; the tool runtime then revalidates the approval binding before execution.

This provides fail-closed restart behavior without turning durable state into an identity or credential store.

## Credential providers

`SecretProvider` is a provider-neutral acquisition interface. The reference implementations are:

- `EnvironmentSecretProvider` for externally injected environment values;
- `MountedFileSecretProvider` for mounted secret volumes with path traversal protection;
- `ChainedSecretProvider` for explicit application-owned fallback order.

Every acquisition returns a `SecretLease`. A lease redacts its string representation, rejects serialization, and can carry an expiry. Providers are queried on demand, so rotation does not require a credential value to become canonical Manager state.

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

The current official MCP SDK bridge already has tested cross-origin redirect rejection. Custom CA, mTLS, proxy, and enterprise egress configuration still depend on the concrete transport client selected by the embedding application. `NetworkSecurityPolicy.ssl_context()` is an integration point, not proof that a third-party SDK used it.

## Error handling

Security boundary errors expose stable codes and, where useful, exception class names. Secret-bearing exception messages are not promoted into governed results. A best-effort text redactor exists for application logging surfaces, but avoiding raw sensitive messages remains preferable to trying to scrub them after capture.

## Deployment requirements

A production deployment still needs environment-specific decisions and evidence for:

- identity issuer and key distribution;
- asymmetric/workload identity verifier selection where appropriate;
- revocation source, availability, and fail-closed behavior;
- policy administration and revision lifecycle;
- secure secret-provider implementation and access controls;
- credential rotation and emergency revocation;
- TLS roots, mTLS identities, egress restrictions, and proxy topology;
- storage encryption/access controls for durable state;
- logging, monitoring, incident response, and retention;
- independent security review.

These controls reduce framework-level ambiguity. They do not make arbitrary deployments production-ready by themselves.
