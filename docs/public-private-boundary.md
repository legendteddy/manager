# Public / Private Boundary

## Invariant

`legendteddy/manager` is a public repository.

Assume every committed design, document, example, test, fixture, trace, configuration fragment, path, commit message, and history entry can be read, indexed, forked, quoted, cached, or reused by outsiders.

Public safety is a migration and release gate.

## Classification before migration

Every artifact or concept considered for migration must be classified:

| Classification | Public handling |
| --- | --- |
| Generic framework architecture | Eligible |
| Generic behavioral or eval concept | Sanitize, then migrate |
| Maintainer-specific configuration | Exclude |
| Business/domain-specific knowledge | Exclude unless deliberately public and independently safe |
| Credentials, sensitive operational state, or non-public personal information | Never migrate |

When classification is uncertain, exclude the material until publication safety is verified.

## Private reference boundary

Manager may be informed by private reference systems, but public files must not disclose their exact repository identity, URL, repository mappings, operational state, private configuration, or internal estate topology.

Public lineage should remain generic, for example: "an earlier private agent-governance architecture."

## Public repository contents

The public repository may contain orchestration contracts, routing concepts, policy and approval interfaces, capability contracts, state interfaces, reconciliation logic, evaluation frameworks, trace schemas, adapters, synthetic examples, templates, and generic tests/fixtures.

## Private contents

The public repository must not contain private human/operator configuration, proprietary business context, customer or employee information, private repository mappings, confidential financial information, real operational state, credentials/tokens/keys, sensitive connected-source contents, private incident details, sensitive prompts/traces, non-public personal information, or local filesystem paths that expose sensitive identifiers.

## Interface pattern

Private deployment-specific configuration should connect through explicit interfaces rather than being embedded into the framework.

```text
public Manager core
        │
        ├── configuration interface ──> private policy/configuration
        ├── state interface ─────────> private operational state
        ├── tool adapter ────────────> private integration
        └── secret reference ────────> external secret store
```

The interface contract may be public. Private values and operational contents are not.

## Examples and evals

Use synthetic organizations, repositories, identifiers, datasets, actions, and traces. Do not disguise a private real-world incident merely by changing a name or number; abstract the behavioral property into a genuinely generic case.

## Trace hygiene

Public traces, if added later, should record behavior at the minimum useful level such as workflow selected, capability activation, tool class/outcome, approval state, reconciliation result, and verification result.

They must not contain hidden reasoning, credentials, private source text, sensitive identifiers, or unnecessary private context.

## Commit metadata

Commit history is part of the public surface. Prefer public-safe no-reply identity metadata unless a contributor intentionally publishes another address.

## Migration rule

Migration is concept extraction plus public-safety classification, not repository duplication.
