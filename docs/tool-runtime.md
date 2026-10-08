# Governed tool runtime

## Purpose

Manager separates **tool proposal** from **tool execution**.

A model, primary agent, human, or system may propose a tool name and arguments. The proposal does not carry execution authority. Manager resolves the tool against a trusted registry, reads the registry-owned side-effect class, evaluates authorization and approval requirements, executes only when permitted, and verifies consequential effects when required.

## Trust boundary

```text
model / agent / human proposal
        ↓
ToolRequest: name + arguments + target
        ↓
trusted ToolRegistry
  - side-effect class
  - input schema
  - verification requirement
        ↓
deterministic policy
        ↓
execute / block / require approval
        ↓
ToolResult + trace event
```

The proposer cannot lower a tool's side-effect class, disable verification, grant itself authorization, or satisfy a human approval requirement.

## Side-effect classes

Manager uses the security classes defined in `SECURITY.md`:

- `analysis`: local/no external side effect;
- `read`: retrieves data without modification;
- `reversible_write`: recoverable mutation that requires routine authorization and verification;
- `external_commitment`: communication or action visible to third parties and requiring explicit human intent plus target verification;
- `sensitive_destructive`: destructive, irreversible, security-sensitive, production-impacting, financial/legal, or comparable action requiring exact human approval.

The Python reference runtime implements a conservative policy for these classes. Other runtimes may implement stricter policy, but must not silently weaken protected approval or authority boundaries.

## Authorization context

Trusted authorization is supplied to the runtime separately from the proposal. The Stage 5 reference context supports:

- `scope_authorized`: the embedding application confirms the requested tool use is within the task's granted scope;
- `human_intent_confirmed`: explicit human intent exists for an external commitment;
- `target_verified`: the external target has been checked;
- `approval`: an approval packet for actions that require human approval.

Model output is never trusted as authorization context.

## Sensitive/destructive approval

For `sensitive_destructive` tools, the runtime binds approval to a stable fingerprint of:

- tool name;
- target;
- arguments.

If those change after approval, the approval is stale and execution is blocked until a fresh approval is issued.

## Verification

Tools marked `requires_verification` do not receive a successful verified result merely because execution returned. The adapter must provide a verification method and that verification must pass. A failed or missing verification produces a failed/unverified outcome rather than an invented success claim.

## Model-provider integration

Stage 5 exposes only **custom function proposals** to model providers. The model may request a function call; Manager's application layer owns whether that request executes.

Provider-executed built-in tools and provider-managed MCP tools are intentionally outside the Stage 5 reference path because they can execute inside the provider before Manager's local tool policy evaluates the request. Future support must preserve the same policy and approval boundary.

## Stage 5 limits

The reference runtime does not yet provide:

- durable approval storage;
- distributed tool registries;
- sandbox/process isolation;
- transaction rollback orchestration;
- provider failover;
- parallel tool execution;
- automatic retry of side effects;
- a model continuation loop after tool results;
- production credentials or production tools.

All examples and tests use synthetic adapters.
