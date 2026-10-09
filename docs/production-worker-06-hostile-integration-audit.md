# Production Worker 06: hostile MCP and tool-runtime audit

## Scope

This audit attacks Manager's tool and MCP boundary on top of the production-integrated `main` runtime. External tools and MCP servers are treated as untrusted, potentially malicious peers. The goal is to preserve Manager-owned authority and keep failures bounded rather than to make arbitrary external processes or networks trustworthy.

## Fresh attack added

### Loopback SSRF default

The integrated runtime already blocked private, link-local, multicast, unspecified, unusual-scheme, credential-bearing, and metadata targets by policy, but the default `MCPNetworkPolicy` still allowed loopback and plaintext loopback HTTP. That made localhost an implicit trust exception even though loopback services can expose admin panels, cloud sidecars, developer daemons, container bridges, or other host-local capabilities.

The default policy now denies loopback too. Local MCP development and conformance must opt in explicitly with both `allow_loopback=True` and, for plaintext local HTTP, `allow_plain_http_loopback=True`.

This is intentionally configurable rather than a blanket prohibition. Legitimate local deployments can still declare the exception, while production callers no longer receive it accidentally.

## Existing hostile coverage re-audited

The current production-integrated runtime already contains deterministic defenses and hostile regressions for:

- remote descriptions and annotations attempting to redefine authority;
- trusted registry immutability and locally owned tool classification;
- discovery-count, pagination, cursor, name, description, and schema limits;
- deep and oversized schema rejection;
- same-session schema fingerprint revalidation before consequential execution;
- disappearance, restart, crash, timeout, cancellation, and reconnect behavior;
- request and result byte/item/depth limits;
- finite operation timeouts and concurrent-operation admission limits;
- stdio command/argument/environment/cwd validation;
- shell-wrapper rejection by default;
- bounded, continuously drained subprocess stderr;
- custom transport opt-in;
- SSRF checks for private/link-local/non-global address classes and metadata hostnames;
- URL credential and unusual-scheme rejection;
- same-origin redirect conformance and cross-origin redirect rejection;
- cross-origin credential-capture attack coverage;
- malformed JSON, unexpected content type, and mid-response connection close;
- redacted transport/SDK exception surfaces;
- MCP error-result normalization;
- model-visible result bounding and sensitive-output withholding;
- application-owned verification for consequential MCP tools.

The hostile HTTP and normal Streamable HTTP fixtures now declare their loopback exception explicitly, so test infrastructure does not weaken the runtime default.

## Authority boundary

Remote MCP metadata remains capability evidence only. It cannot assign or lower:

- side-effect class;
- authorization requirements;
- human approval requirements;
- sensitivity classification;
- application-owned tool version;
- trusted model-facing description;
- verification requirements or verifier identity.

Consequential MCP actions remain behind Manager's normal policy, authorization, approval, trusted binding, same-session schema revalidation, and application-owned verification boundary.

## Residual platform requirements

Manager's Python library controls are defense in depth, not a replacement for deployment isolation. Production deployments still need platform controls for the following:

- **OS/container sandboxing:** a malicious stdio child can consume CPU, memory, file descriptors, filesystem access, or spawn descendants unless the host/container constrains it. Use non-root identities, seccomp/AppArmor/SELinux where appropriate, cgroups/quotas, read-only filesystems, and process-tree containment.
- **Network egress enforcement:** userspace DNS/address validation cannot eliminate DNS rebinding, compromised resolver behavior, proxy rewriting, or host compromise. Enforce destination policy with a firewall, egress proxy, service mesh, VPC/network policy, and explicit metadata-service blocking.
- **TLS/mTLS and credential policy:** certificate trust, client certificates, OAuth/token issuance and rotation, enterprise proxies, and secret storage are deployment concerns and must remain outside public Manager bindings.
- **Raw wire-size and streaming limits:** Manager bounds normalized MCP results before they enter Manager state or model continuation, but an SDK/HTTP stack may allocate or parse transport bytes before Manager sees the normalized result. Reverse proxies, transport configuration, and container memory limits should enforce raw response/stream budgets.
- **Semantic drift:** a malicious server can preserve the reviewed schema while changing behavior. Consequential effects therefore still require an independent application-owned verifier.
- **Exactly-once effects:** transport cancellation or process failure cannot prove whether a remote side effect occurred. Durable recovery and external reconciliation remain required for uncertain outcomes.

## Verification contract

Required repository CI must pass the full Python compatibility matrix, normal unit tests/evals/contracts/release checks, ordinary MCP stdio and Streamable HTTP conformance, and all three hostile MCP suites:

- `test_mcp_hostile_boundaries.py`
- `test_mcp_hostile_stdio_transport.py`
- `test_mcp_hostile_http_transport.py`

No production deployment, package publication, tag, release, credential use, or real external side effect is part of this worker.
