# Python reference runtime

This directory contains Manager's first reference runtime.

The runtime remains intentionally narrow. It implements a deterministic **control plane** for routing, approval, bounded delegation, public-safety filtering, and routine reconciliation, plus a provider-neutral text-generation adapter boundary. Python is the first reference implementation language; the canonical Manager contracts remain language- and provider-neutral.

## Scope

Implemented:

- direct routing for low-risk routine tasks;
- material-action approval blocking;
- stale-approval rejection;
- authority-preserving specialist handoffs;
- routine authority-first reconciliation;
- prompt-injection resistance at the control layer;
- public/private exclusion decisions for modeled public writes;
- contract-shaped trace, result, approval, handoff, reconciliation, model request, and model response artifacts;
- deterministic execution of the public eval fixtures;
- provider-neutral model adapter protocol;
- OpenAI Responses API reference adapter;
- model-backed execution for eligible direct public tasks;
- no-provider-call guarantees for blocked/material work;
- non-public provider input withheld by default.

Not implemented:

- provider-side or Manager-side tool calling;
- arbitrary external side effects;
- durable state stores;
- external approval persistence;
- multimodal model input;
- streaming;
- durable provider conversation/session state;
- provider failover or automatic model selection;
- full JSON Schema validation;
- production readiness or private-reference parity.

## Run deterministic evals

From the repository root:

```bash
PYTHONPATH=runtime/python python3 -m manager_runtime.evals evals/cases
```

Run unit tests:

```bash
PYTHONPATH=runtime/python python3 -m unittest discover -s runtime/python/tests -v
```

## OpenAI reference adapter

Install the optional provider dependency:

```bash
python3 -m pip install -e 'runtime/python[openai]'
```

The adapter uses the official Python SDK's Responses API. Credentials are supplied through the SDK's normal external configuration, such as `OPENAI_API_KEY`; never commit credentials to this repository.

Model selection is explicit. Manager deliberately does not hard-code a default model.

Example:

```python
from manager_runtime import run_with_model
from manager_runtime.providers import OpenAIResponsesAdapter

payload = {
    "task": {
        "task_id": "example-1",
        "objective": "Write one concise public sentence.",
        "classification": {
            "materiality": "routine",
            "consequence": "low",
            "uncertainty": "low",
            "reversibility": "reversible",
            "sensitivity": "public",
        },
    },
    "model_input": "Explain bounded delegation in one sentence.",
}

output = run_with_model(
    payload,
    OpenAIResponsesAdapter(),
    model="YOUR_MODEL_ID",
)
print(output["result"]["finding"])
```

## Governance boundary

`run_with_model` always executes the deterministic control plane first. If the task is material or otherwise blocked, the provider is not called. Only the direct workflow is model-backed in this stage.

Non-public input is not sent to a provider by default. The embedding application must make an explicit opt-in decision before doing so, and remains responsible for its own provider/data-processing requirements.

A green test or eval run demonstrates only the behavior actually encoded and tested. It does not establish general reasoning quality, provider uptime, security completeness, private-reference parity, deployment readiness, or production suitability.
