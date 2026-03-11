# Remram Semantic Router

This document defines the canonical design intent for the Remram Semantic Router and its escalation ladder.

## 1. Overview

The Remram Semantic Router is the local-first answer-or-escalate ladder that provides the ChatGPT-style front door for Remram.

It exists to let Remram try the cheapest acceptable answer first, escalate in bounded stages when the current tier is insufficient, and cleanly terminate into either:

- a final synchronous answer
- an asynchronous agent workflow handoff

The Semantic Router is not a general workflow engine. It is the chat-time semantic escalation mechanism for requests entering through chat, voice, or API surfaces.

It is also not a parallel chat endpoint. The router must execute inside the normal OpenClaw reply lifecycle so browser chat, `openclaw agent --agent main`, and any runtime chat harness all traverse the same execution stack.

Its core design principle is:

- each stage attempts to answer
- if it cannot, it proposes escalation
- the orchestrator validates and enforces the next hop

This makes the router:

- local-first
- semantically aware
- bounded by orchestration guardrails
- compatible with later agent systems without requiring redesign of the front-door runtime

## 2. Product Goals

The Semantic Router must satisfy the following goals:

- Prefer local reasoning whenever the local tier can produce an acceptable answer.
- Minimize unnecessary cloud usage and preserve a cost-aware local-first posture.
- Produce predictable escalation behavior through a fixed configured ladder rather than open-ended runtime improvisation.
- Preserve a clear user experience during slower reasoning paths through explicit status messages and bounded latency expectations.
- Integrate cleanly with OpenClaw runtime mechanisms instead of replacing them with a parallel execution system.
- Support future agent workflows by cleanly terminating chat-time escalation with a dispatch outcome instead of absorbing long-running work into the chat ladder.
- Provide a stable contract for telemetry, routing analysis, and future tuning.

## 3. Architectural Boundaries

The Semantic Router sits between deterministic request conditioning and asynchronous workflow dispatch.

It does:

- consume a conditioned request packet
- run a configured sequence of semantic stages
- return `answer`, `escalate`, or `spawn_agent`
- append trace information to the packet ledger
- emit routing and run statistics for observability

It does not:

- perform deterministic request conditioning such as identity hydration, modality checks, or budget prechecks
- own attachment preprocessing as a primary responsibility
- execute long-running workflows
- choose arbitrary next routes outside the configured escalation ladder
- become the durable memory authority
- create a second independent chat execution path beside the normal OpenClaw agent lifecycle

The architectural separation is:

- Request Preflight Pipeline: stage 0, deterministic request conditioning, not part of the ladder
- Semantic escalation ladder: synchronous chat-time answer-or-escalate stages
- Agent workflow dispatch: asynchronous workflow handoff after `spawn_agent`

Synchronous semantic escalation and asynchronous workflows are separate mechanisms and must not be implemented as the same runtime path.

The runtime chat harness remains valid only as a debug and regression surface. It must invoke the same OpenClaw lifecycle path used by the browser and agent CLI, not a custom sidecar executor.

## 4. Stage Model Contract

Every stage in the ladder uses the same contract.

Valid outcomes are:

- `answer`
- `escalate`
- `spawn_agent`

Their meanings are:

- `answer`
  - The current stage can resolve the request synchronously.
  - The orchestrator returns the answer to the user and terminates the ladder.

- `escalate`
  - The current stage believes the request requires the next configured capability tier.
  - The orchestrator validates that escalation is allowed, advances to the next configured stage, and continues the ladder.

- `spawn_agent`
  - The current stage determines that the request should leave the synchronous chat ladder and become a workflow.
  - The orchestrator terminates the ladder and hands control to workflow dispatch.

The stage proposes the outcome.

The orchestrator remains authoritative for:

- whether escalation is admissible
- which next stage is allowed
- whether the ladder must terminate
- whether agent dispatch is permitted

The stage contract is intentionally uniform across all stages so the orchestrator loop stays simple and observable.

## 5. Escalation Ladder

The escalation ladder is an ordered set of configured stages.

The ladder must support an arbitrary number of stages.

Stages are configured, not hard-coded.

Each stage follows the same rule:

- attempt to answer first
- escalate only when needed

Example ladder concepts:

- stage 1: local model
- stage 2: stronger general model
- stage 3: deep reasoning model
- stage 4: specialist or workflow-oriented model

Example configuration shape:

```yaml
semantic_router:
  stages:
    - id: local
      model_ref: ollama/qwen3-moltbox
    - id: general
      model_ref: together/meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8
    - id: thinking
      model_ref: together/Qwen/Qwen3-235B-A22B-Thinking-2507
    - id: coding
      model_ref: together/meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8
```

Another valid configuration could stop at two stages:

```yaml
semantic_router:
  stages:
    - id: local
      model_ref: ollama/qwen3-moltbox
    - id: thinking
      model_ref: together/Qwen/Qwen3-235B-A22B-Thinking-2507
```

The first stage is not a pure classifier. It is typically the same local model used for cheap answers. That is intentional. The first inference is already useful work, so allowing it to answer or escalate does not introduce a separate classification cost.

## 6. Guardrails

The orchestrator must enforce guardrails around the ladder at all times.

Required guardrails include:

- maximum escalation depth
- allowed stage transitions
- timeout limits
- budget limits
- terminal policies such as `force_answer_at_max_depth`

The orchestrator must be able to reject a proposed escalation when:

- the next stage does not exist
- the next stage is disallowed by policy
- the request has reached maximum depth
- runtime budget or provider policy would be violated
- the request should instead terminate or dispatch a workflow

Minimum expected guardrail controls:

- `max_escalation_depth`
- `force_answer_at_max_depth`
- `allow_spawn_agent`
- `stage_timeout_ms`
- `request_budget_cap`

The orchestrator is also responsible for preventing runaway escalation loops, including repeated self-escalation or invalid transitions.

## 7. Packet Interface

The Semantic Router consumes the Remram request packet defined in `docs/schemas/remram-request-packet.schema.json`.

The most important fields are:

- `request`
  - canonical
  - carries the inbound request text, surface metadata, and attachment metadata

- `requester`
  - canonical
  - carries identity information required for preference hydration, permissions, and policy decisions

- `preferences`
  - canonical
  - carries stable requester preferences that may influence style, routing posture, and policy behavior

- `conversations`
  - evolving
  - carries primary and related conversation continuity data

- `context`
  - evolving but structurally important
  - contains bounded context intended to be visible to semantic stages

- `routing`
  - canonical
  - carries stage metadata, escalation limits, and dispatch hints

- `ledger`
  - canonical
  - append-only trace of request handling across preflight, semantic stages, and later workflow dispatch

- `response`
  - canonical
  - captures the current response status and any user-visible status or answer payload

`extended_context` and `instructions` are intentionally present in the packet but remain evolving surfaces. They are useful to the broader runtime, but they are not the primary contract of the Semantic Router v1.

The top-level packet shape should remain stable.

Nested packet details may continue to evolve as preflight, memory, and workflow systems mature.

## 8. Observability

The Semantic Router must produce first-class telemetry.

Minimum observability expectations include:

- per-stage timing
- full escalation path
- append-only ledger entries
- run statistics written to OpenSearch

Every stage should produce ledger data sufficient to answer:

- which stage ran
- which model was used
- what decision was returned
- why the decision was made
- how long the stage took
- optional token usage when available

In addition to ledger persistence, the final response returned through the normal OpenClaw path must expose a structured Semantic Router summary that makes escalation obvious to operators and debuggers.

Minimum final-response metadata should include:

- terminal stage
- terminal provider
- terminal model
- total duration
- ordered per-stage timing entries
- ordered per-stage provider/model selections
- per-stage token usage when available
- terminal decision

The browser surface may render a concise summary, but the underlying response/debug object must retain the full structured metadata.

The OpenSearch run stats layer should preserve:

- request timestamp
- route taken
- stages traversed
- models used
- stage durations
- terminal outcome
- whether the run answered locally, escalated, or dispatched a workflow

The ledger is the runtime trace.

The OpenSearch stats documents are the durable analytics surface.

## 9. Integration with OpenClaw

The Semantic Router must integrate with OpenClaw through the native reply lifecycle rather than through a parallel runtime replacement.

### 9.1 Correct Integration Location

The primary integration point is the OpenClaw plugin hook `before_model_resolve`.

That hook runs inside the real reply pipeline immediately before OpenClaw resolves the provider/model pair for an agent run. Its contract allows a plugin to return:

- `providerOverride`
- `modelOverride`

OpenClaw then resolves the actual model using those overridden values.

Per the upstream hook runner behavior, `before_model_resolve` is a modifying hook:

- handlers run sequentially in priority order
- higher-priority hooks run first
- the first defined `providerOverride` or `modelOverride` wins

This matters because Semantic Router stage selection must be authoritative within its plugin priority band and must not assume it can overwrite an earlier higher-priority override.

This is the correct routing seam because it sits inside the normal chat execution stack rather than beside it.

Companion lifecycle hooks are:

- `before_prompt_build`
  - injects stage-specific system prompt and bounded context after stage selection is known
- `llm_output`
  - captures actual provider/model/usage returned by the live model invocation
- `agent_end`
  - records terminal run status and total duration for the completed user turn

`before_prompt_build` is also a modifying hook. Its prompt fields are merged in execution order rather than replaced wholesale.

Operators may disable prompt mutation for a plugin via `plugins.entries.<id>.hooks.allowPromptInjection: false`.

When that policy is disabled:

- OpenClaw blocks `before_prompt_build`
- OpenClaw ignores prompt-mutating fields returned from legacy `before_agent_start`
- OpenClaw still preserves `modelOverride` and `providerOverride`

This means the Semantic Router must treat provider/model selection as the hard execution contract and treat prompt injection as a policy-controlled capability rather than an unconditional assumption.

The generic internal event hooks such as `command:*`, `message:*`, `agent:bootstrap`, and `gateway:startup` are useful event listeners, but they are not the primary routing seam for per-turn semantic escalation.

### 9.2 Corrected Execution Flow

The corrected flow is:

```text
OpenClaw chat request
-> OpenClaw agent / embedded reply pipeline
-> Request Preflight Pipeline
-> before_model_resolve
-> Semantic Router stage selection
-> provider/model override
-> before_prompt_build
-> provider adapter execution
-> llm_output telemetry capture
-> answer OR escalate OR spawn_agent handling
-> final response returned through the normal OpenClaw path
```

For multi-stage turns, the ladder remains inside the same user-turn execution envelope:

```text
OpenClaw chat request
-> preflight
-> stage 1 resolve + execute
-> stage 1 result
-> orchestrator validates next hop
-> stage 2 resolve + execute
-> ...
-> terminal answer or spawn_agent
-> normal OpenClaw response emission
```

The router therefore becomes part of the real execution stack rather than a second chat surface.

### 9.3 Interaction with the OpenClaw Agent Loop

The Semantic Router does not replace the OpenClaw agent loop. It wraps the model-selection portion of that loop.

The interaction model is:

- OpenClaw receives the turn through its normal chat or agent surface
- preflight prepares the request packet
- `before_model_resolve` selects the current stage and overrides provider/model for that stage run
- `before_prompt_build` injects the stage contract prompt and any bounded routing context
- the model executes through the normal provider adapter
- `llm_output` captures what actually ran and what usage/timing was produced
- the orchestrator interprets the stage result:
  - `answer` returns through the normal reply path
  - `escalate` continues to the next configured stage inside the same turn
  - `spawn_agent` exits the synchronous ladder and hands off to workflow dispatch

Tool use remains part of the normal OpenClaw agent loop. The Semantic Router should not special-case tool execution in v1 beyond preserving the normal agent behavior for whichever stage is currently active.

### 9.4 Relationship to the Runtime Chat Harness

The runtime chat harness is a test and debug surface only.

Its purpose is to:

- exercise the same OpenClaw lifecycle path used by browser chat and `openclaw agent --agent main`
- expose packet, ledger, stage path, and telemetry for debugging
- provide a repeatable regression surface for semantic escalation

It must not:

- bypass the OpenClaw reply lifecycle
- call a separate Semantic Router executor
- become the primary product surface for escalation

The harness and browser chat must therefore share one execution path, differing only in how much debug metadata they expose.

### 9.5 Preferred Hosting Model

The preferred integration model is a `remram-runtime` OpenClaw plugin or runtime wrapper that hosts:

- Request Preflight Pipeline orchestration
- Semantic Router orchestration logic
- packet and ledger helpers
- final-response telemetry shaping
- workflow dispatch adapters

In this design, stage models may propose escalation, but OpenClaw-facing orchestration remains authoritative for route validation and execution.

The orchestrator must validate:

- whether the proposed stage transition is allowed
- whether the target provider/model is available
- whether the runtime can honor the request within configured limits

The Semantic Router should extend OpenClaw behavior, not fork it.

### 9.6 Implementation Research Gate

Before any further integration work resumes, the implementation owner must verify the current OpenClaw lifecycle contract from both:

- the latest official OpenClaw documentation
- the current upstream OpenClaw repository source

This is a required pre-step, not an optional validation pass.

Minimum sources to review are:

- the official Agent Loop documentation
- the official Plugins documentation
- the upstream `src/agents/pi-embedded-runner/run.ts`
- the upstream `src/plugins/types.ts`
- the upstream `src/plugins/hooks.ts`

The purpose of this gate is to confirm:

- the authoritative chat execution path
- the current typed plugin hook signatures
- the exact semantics of `before_model_resolve`
- the exact semantics of `before_prompt_build`
- the available telemetry hooks such as `llm_output` and `agent_end`
- any operator policy constraints such as prompt-injection controls

When documentation and source disagree, the upstream source contract wins.

Design work may rely on documentation for orientation, but implementation must align to the actual upstream source and the installed runtime version used in the appliance.

## 10. Relationship to Agent Workflows

`spawn_agent` is the exit path from the Semantic Router into workflow land.

When a stage returns `spawn_agent`:

- synchronous chat-time escalation ends
- the orchestrator records the terminal ladder state
- control passes to the Agent Router or workflow dispatch system

The Semantic Router does not execute workflows.

Within the OpenClaw lifecycle, `spawn_agent` is interpreted after the current stage result is parsed and before a normal terminal assistant reply is committed. It is a boundary signal, not a second model-selection override.

It does not own:

- workflow planning
- workflow step execution
- long-running job control
- step-level push updates

Its role is only to decide that the request should leave the answer-or-escalate ladder and become an asynchronous workflow concern.

This boundary is critical because chat-time escalation and asynchronous work have different latency, control, and lifecycle expectations.

## 11. Non-Goals

The following are explicitly out of scope for Semantic Router v1:

- full long-term memory architecture
- final design of attachment preprocessing for large documents or images
- workflow internals for coding, planning, research, or document agents
- open-ended dynamic route invention by stage models
- unbounded recursive routing inside the synchronous chat path
- a universal public packet format that external adopters must implement in full
- replacing OpenClaw session or workflow primitives with an entirely custom execution engine

## 12. Open Questions

The following design questions remain open:

- What is the initial stage count at launch, and which configured stages are necessary versus optional?
- Should the first local stage and later general stage share one prompt profile family or use distinct stage prompts?
- Which fields in `conversations`, `context`, and `extended_context` should become canonical first?
- How should large attachments be represented in v1 before a full preprocessing strategy is implemented?
- Should `force_answer_at_max_depth` always produce an answer, or can it still permit graceful failure or workflow handoff?
- How much routing metadata should be emitted by stage models versus synthesized by the orchestrator?
- What is the minimum OpenSearch stats document that still gives useful operational visibility?
- How should the eventual workflow dispatch boundary align with OpenClaw-native spawn/session primitives?
