# Moltbox (remram-edge) – Design

Moltbox is an OpenClaw appliance.

It is a dedicated machine configured to run OpenClaw locally with a GPU-backed model and the supporting infrastructure required to operate and extend the system. Moltbox is the concrete, physical instance of the Remram runtime.

A machine becomes a Moltbox when it has:

*   Purpose-built hardware sized for AI workloads
*   A stable operating system and GPU stack
*   OpenClaw installed and running natively
*   A local model providing routing and bounded reasoning
*   A local database layer for storage and future retrieval capabilities
*   Supporting services such as container runtime and local Git for extension and versioning
*   A client interface for interaction

The core value of Moltbox is local capability.

It provides a strong local model that does not incur per-token cloud cost. It provides a persistent OpenClaw runtime under direct control. It provides the ability to experiment with workflows, tools, and agents without spinning up paid cloud infrastructure. Cloud cognition can be attached when needed, but the baseline system lives on the box.

Moltbox is the foundation for future phases. Additional layers—memory systems, reflection cycles, sovereign cognition clusters—attach to this appliance. They extend it, but the underlying structure remains the same: hardware plus OpenClaw plus local capability.

This document defines the hardware envelope, host configuration, runtime decisions, model strategy, tooling posture, access patterns, and cloud integration choices that together make a machine a Moltbox.

# 1\. Hardware

Moltbox is a hardware appliance for OpenClaw.

It is a dedicated machine that runs OpenClaw natively as its primary runtime and provides the additional capacity required by the Remram system—namely, a GPU-backed local model and the data services needed to store and retrieve knowledge. The hardware defines the performance ceiling, cost profile, and upgrade path.

This chapter walks through capability tiers to help you scale your Moltbox to your own needs. The final section outlines the reference architecture used for validation and reflects the creator’s cost-versus-performance decisions, referred to as Moltbox Prime.

## 1.1 Hardware Capability Classes

These performance tiers provide target envelopes you can build toward based on your needs.

| Class | CPU Cores | System RAM | GPU (VRAM) | Storage | Power Class |
| --- | --- | --- | --- | --- | --- |
| **Appliance** | 8+ | 32–64GB | Integrated / 8–16GB | 1 NVMe | 100–250W |
| **Solo** | 8–12 | 64–128GB | 16–24GB (Prosumer) | 1–2 NVMe | 200–350W |
| **Family** | 16+ | 128–256GB | 24–32GB (Workstation) | 2–3 NVMe | 350–600W |
| **Sovereign** | 24+ | 256GB+ | 48GB+ / Multi-GPU | Clustered storage | 800W–1200W+ |

Power class reflects approximate sustained draw, not peak load, and should not be used directly for PSU sizing.

## 1.2 CPU

The CPU governs orchestration stability, database responsiveness, indexing throughput, and container concurrency. While inference runs on the GPU, the CPU determines how smoothly the rest of the system operates under sustained load.

**Decision Factors**

**Core Count.** Determines how many background processes and orchestration tasks can run simultaneously without contention.

**Clock Speed.** Influences single-thread responsiveness and scheduling efficiency.

**Generation & Platform.** Newer generations provide better memory controllers, higher ceilings, improved PCIe bandwidth, and stronger power efficiency.

**Guidance by Tier**

*   **Solo (8–12 cores):** 8 cores minimum. Supports routing, database services, and moderate indexing without saturation.
*   **Family (16+ cores):** 16 cores recommended for concurrent users and sustained indexing.
*   **Sovereign (24+ cores):** Supports heavy parallel workloads and multi-model coordination.

In practical terms:

*   More cores → better concurrency
*   Higher clocks → faster short-latency operations
*   Newer generation → stronger memory and PCIe support

## 1.3 System Memory

System memory determines how much active database state, indexing data, embeddings, and session context can remain resident.

**Decision Factors**

**Capacity.** Larger capacity keeps indexes memory-resident and reduces disk I/O.

**Speed & Generation.** Influences bandwidth, though capacity remains dominant.

**Headroom for Growth.** Artifact-heavy workflows expand memory demand quickly.

**Guidance by Tier**

*   **Solo (64–128GB):** 64GB minimum; 128GB preferred.
*   **Family (128–256GB):** 128GB baseline; 256GB improves concurrency stability.
*   **Sovereign (256GB+):** Supports large-scale indexing and heavy workloads.

In practical terms:

*   More RAM → fewer disk stalls
*   Higher bandwidth → smoother indexing
*   More headroom → long-term scalability

## 1.4 GPU

The GPU determines local intelligence capacity, usable context size, and escalation frequency.

**Decision Factors**

**VRAM Capacity.** 16GB is the practical minimum for stable orchestration.

**Performance Tier.** Higher tiers reduce latency and improve throughput.

**Architecture.** Prefer NVIDIA 30-series or newer for modern Tensor Core efficiency.

**GPU Count.** Multi-GPU only when intentionally running specialized workloads.

**Guidance by Tier**

*   **Solo (16–24GB):** 16GB minimum for stable 8B routing.
*   **Family (24–32GB):** Provides stronger sustained performance.
*   **Sovereign (48GB+ / Multi-GPU):** Enables larger reasoning models and specialization.

In practical terms:

*   More VRAM → larger models and more context
*   Higher tier → lower latency
*   Multiple GPUs → specialization

## 1.8 Moltbox Prime (Creator Reference Build)

Moltbox Prime aligns to a minimal Family-tier configuration.

*   16-core CPU
*   128GB RAM
*   16GB Blackwell-class GPU
*   Two 2TB NVMe drives
    *   Drive 1: OS, OpenClaw runtime, local Git
    *   Drive 2: Database and persistent storage

The GPU was intentionally sized to the practical 16GB baseline to reduce initial cost while preserving routing stability.

# 2\. Operating System & Runtime Foundation

Moltbox is a dedicated OpenClaw appliance. This chapter defines the operating system and runtime stack supporting stable GPU-accelerated containerized operation.

Objectives:

*   Stable 24/7 operation
*   Clean service isolation
*   Full NVIDIA acceleration
*   Scriptable deployment via Docker Compose
*   LAN-only management exposure
*   Clear recovery posture

## 2.1 Operating System

Moltbox runs **Ubuntu Server LTS**.

Installation posture:

*   Minimal server install
*   OpenSSH enabled
*   Non-root administrative user created
*   Root login disabled
*   Static IP via DHCP reservation

# 3\. OpenClaw Runtime

OpenClaw is the orchestration core of Moltbox.

It manages:

*   Session lifecycle
*   Tool routing and execution control
*   Sandbox lifecycle
*   Memory interaction with OpenSearch
*   Local model invocation
*   Cloud escalation when required
*   Primary developer interface

# 4\. Cognition Architecture

Cognition in Moltbox is governed infrastructure. The appliance does not distribute intelligence indiscriminately; it allocates reasoning deliberately, under policy, and only when orchestration boundaries require it. Routing authority remains local. Planning and synthesis occur remotely. Execution authority always returns to the control plane.

The result is a system that treats reasoning as a metered utility rather than a reflex.

## 4.1 Cognition Role & Boundary

The local model performs orchestration only. Its responsibilities include intent parsing, tool routing, escalation decisions, schema enforcement, and retrieval bundle assembly. It does not perform deep reasoning. The routing tier is optimized for orchestration stability, not deep local reasoning capacity.

Remote models perform planning, multi-step reasoning, long-context synthesis, strategic arbitration, and complex code generation. When invoked, they return structured plans or outputs. Tool execution always occurs under local control. Deterministic tool invocation bypasses heuristic escalation and is resolved locally.

This separation prevents hybrid drift. Moltbox does not partially reason and partially orchestrate. It either routes or escalates.

## 4.2 Local Routing Model

The local control tier executes primary orchestration on Moltbox hardware. It handles intent extraction, structured output generation, and lightweight arbitration before escalation. These models must balance latency, VRAM headroom, and structured reliability within a 16GB GPU envelope.

| Model | Class | Context | First-Token (sec) | Throughput (tok/sec) |
| --- | --- | --- | --- | --- |
| **Qwen3-4B-Instruct-2507** | 4B | 32k+ | ~0.4-0.8 | 160-260 |
| **Qwen3-8B-Instruct** | 8B | 16-32k | ~0.9-1.8 | 90-160 |
| **Qwen3-14B-Instruct** | 14B | 16k | ~1.8-3.5 | 45-95 |

_Assumptions: instruction-tuned checkpoints (Instruct variants), single-user execution, no concurrent sessions, Q8 for 4B/8B, Q5 for 14B, moderate system prompt and tool definitions loaded, CUDA acceleration via llama.cpp-class runtime. Actual performance varies with prompt length and KV cache pressure. Performance scales non-linearly under concurrent sessions and larger KV cache utilization. Figures represent single-session routing under moderate prompt size._

**Qwen3-4B-Instruct-2507** is optimized for responsiveness and large context headroom. On a 16GB 5060 Ti, it comfortably supports 32k+ context while maintaining low first-token latency. It is best suited for intent parsing, schema-constrained JSON generation, lightweight routing, and UX-sensitive deployments. It trades arbitration depth for speed.

**Qwen3-8B-Instruct** is the recommended balanced default. It maintains 32k context support with manageable latency and significantly stronger ambiguity handling than 4B. It is best suited for general orchestration, tool-heavy workflows, and single-user control plane execution. It represents the strongest overall balance of speed and reasoning on 16GB hardware.

**Qwen3-14B-Instruct** marks the upper dense-model boundary for 16GB GPUs. VRAM headroom constrains practical context to ~16k. It provides stronger multi-step reasoning and reduces unnecessary escalations compared to 8B. It is best suited for arbitration-heavy workflows, complex intent resolution, and minimizing escalation to the deep thinking tier. It incurs higher first-token latency and lower sustained throughput.

Selection within this tier should be guided by sensitivity to first-token delay, desired context window size, tolerance for arbitration limitations, and escalation frequency expectations. On RTX 5060 Ti 16GB hardware: 4B maximizes speed and context, 8B balances speed and reasoning, and 14B maximizes local intelligence within VRAM limits. Beyond 14B, dense models require aggressive quantization or additional GPU headroom.

## 4.3 Escalation Policy

Escalation separates planning from execution. The reasoning model generates plans. The local control plane validates and executes those plans.

Escalation precedence is ordered and deterministic:

1.  Deterministic tool invocation
2.  User override
3.  Heuristic uncertainty (reasoning tier only)
4.  Budget gate
5.  Safety override

Deterministic invocation (coding agents, diffusion agents, think tool) bypasses heuristic uncertainty and is resolved locally.

Heuristic escalation applies only to the primary reasoning model and is triggered when task depth exceeds threshold, ambiguity persists, or irreversible action is detected.

This preserves cost discipline and prevents cognitive tier inflation.

## 4.4 Cloud Cognition Plane

The cloud layer provides scalable reasoning capacity without assuming execution authority. Each escalation includes a structured retrieval bundle, explicit task scope, and a hard token ceiling. Invocation is stateless and fully logged.

The architecture remains provider-agnostic, but a reference provider is defined for implementation clarity.

## 4.5 Cloud Provider Selection

Select a provider that offers clear spend control, hard caps, stable API semantics, and access to a wide range of open-weight models. The reference architecture uses Together AI as the default escalation provider.

Together AI was selected because it offers broad access to Qwen and DeepSeek model families, competitive pricing for 32B- and 70B-class models, OpenAI-compatible API semantics, and practical spend monitoring. The architecture does not depend on this provider specifically; alternatives such as Fireworks AI and similar hosted inference platforms can be substituted without structural changes.

In the reference configuration, the reasoning endpoint is defined as the default escalation target. Specialized models are configured as discrete tools. Budget caps are enforced both locally and at the provider level.

## 4.6 Primary Reasoning Models

The reasoning tier handles most escalations. It generates plans, structured analyses, and multi-step reasoning outputs that return to the control plane for execution. These models must balance latency, cost, and structured reliability.

| Model | Class | Context | Blended $/M Tokens\* | Typical Throughput (tok/sec) |
| --- | --- | --- | --- | --- |
| **MiniMax-M2-32B (2.5)** | 32B | 16k-32k | ~$0.40 | 80-120 |
| **DeepSeek-V2.5-32B** | 32B | 16k-32k | ~$0.35 | 70-110 |
| **Qwen2.5-32B-Instruct** | 32B | 16k-32k | ~$0.45 | 60-100 |

**MiniMax-M2-32B (2.5)** offers strong structured planning performance with competitive latency. It is well suited for default escalation where balanced reasoning depth and responsiveness matter. It performs reliably in multi-step plan generation and structured analysis.

**DeepSeek-V2.5-32B** provides high reasoning depth per dollar and is attractive when escalation frequency is high and cost efficiency is critical. It may trade a small amount of instruction conservatism for better cost scaling.

**Qwen2.5-32B-Instruct** emphasizes instruction stability and schema reliability. It is slightly more conservative but often preferred in tool-heavy workflows where predictable output structure is paramount.

Choice within this tier should be guided by escalation frequency, tolerance for minor schema variability, and sensitivity to cost drift.

## 4.7 Deep Thinking Models

Deep thinking models are invoked explicitly through the think tool or under strategic policy triggers. They are used for long-horizon reasoning, arbitration, and extended synthesis where the reasoning tier is insufficient.

| Model | Class | Context | Blended $/M Tokens\* | Typical Throughput (tok/sec) |
| --- | --- | --- | --- | --- |
| **DeepSeek-R1** | ~70B | 32k+ | ~$0.90 | 40-70 |
| **Qwen-70B** | 70B | 32k+ | ~$1.10 | 35-65 |
| **Frontier reasoning models** | 70B+ | 64k+ | ~$4.00+ | 30-60 |

**DeepSeek-R1** provides strong long-form coherence and structured multi-step reasoning while remaining substantially more economical than frontier proprietary models. It is appropriate for arbitration and complex planning.

**Qwen-70B** offers high instruction stability and robust structured synthesis at scale. It is suitable when deterministic planning quality is more important than raw cost efficiency.

**Frontier reasoning models** provide the highest intelligence ceiling but introduce significant cost multipliers and vendor dependency. They are reserved for rare strategic tasks where maximum reasoning depth is required.

These models are slower and more expensive by design and are not part of heuristic escalation.

## 4.8 Coding Models

Coding models are invoked exclusively through deterministic tool calls. They do not participate in heuristic escalation and do not execute independently of control-plane validation.

| Model | Class | Context | Blended $/M Tokens\* | Typical Throughput (tok/sec) |
| --- | --- | --- | --- | --- |
| **Qwen3-Coder-32B** | 32B | 16k-32k | ~$0.40 | 60-100 |
| **DeepSeek-Coder-V2** | 16-32B | 16k-32k | ~$0.30 | 80-140 |
| **Code Llama-34B-Instruct** | 34B | 16k-32k | ~$0.60 | 40-80 |

**Qwen3-Coder-32B** is well suited for multi-file transformations and structured repository-scale edits. It balances reasoning depth with structured output reliability.

**DeepSeek-Coder-V2** provides strong cost efficiency and high throughput for common development tasks. It is attractive for frequent, bounded code generation.

**Code Llama-34B-Instruct** offers deeper reasoning in complex refactor scenarios but at higher cost and lower throughput. It is appropriate when code reasoning depth outweighs cost sensitivity.

Model selection in this tier should reflect expected code task complexity, frequency, and tolerance for iterative correction cycles.

## 4.9 Token & Cost Governance

Cloud cognition is governed by per-request token ceilings, daily spend caps, and logged escalation thresholds. Escalation is denied when policy ceilings are reached unless overridden by safety constraints.

Cloud spend is expected and acceptable. Hardware acceleration is introduced only when sustained token usage justifies capital investment.

_Blended pricing assumes a 70% input / 30% output token mix at time of writing and will vary by provider and over time._

## 4.10 Cognition Failure & Degradation

If the local routing model fails, the appliance halts. Authority is never delegated to cloud services.

If cloud cognition becomes unavailable, the appliance continues in bounded local-only mode. Strategic reasoning requests are deferred. Stability is prioritized over availability.

## 5\. Interaction & Access Model

Moltbox exposes controlled interaction surfaces. Users interact through bounded chat interfaces, while full system authority remains local. Identity persists across channels, memory remains user-scoped, and administrative power is intentionally centralized. Transport surfaces may evolve, but identity, session discipline, and control boundaries remain constant.

### 5.1 Identity & Session Model

Moltbox adopts OpenClaw’s native session primitives and secure DM isolation model. Sessions are the operational unit of interaction. Each inbound message is routed into a session managed by the OpenClaw gateway.

Moltbox layers a stable internal identity mapping above this model to preserve continuity across channels.

Each human interacting with Moltbox has:

*   A persistent internal User ID
*   A dedicated primary agent instance
*   A dedicated long-term memory namespace
*   One or more bound channel identities

Channel identities (browser session, Signal number, future app credential) map deterministically to an internal User ID. A User may bind multiple channels. A channel may not bind to multiple users.

Session ownership attaches to the internal User ID. Transport does not define identity, and identity does not merge implicitly across channels.

### 5.2 Local Web Chat (Primary Household Surface)

The primary non-privileged interaction surface for Moltbox is the OpenClaw Web Chat interface exposed by the gateway.

This interface:

*   Runs in a standard browser
*   Connects directly to the OpenClaw gateway
*   Operates entirely within the LAN boundary
*   Uses OpenClaw’s native session handling
*   Does not expose configuration controls

This surface is suitable for:

*   Family members
*   Household users
*   Experimentation without console access
*   Local mobile browser access

Users authenticate through the gateway’s configured authentication mechanism. Upon authentication, the browser session binds to a specific internal User ID.

The Web Chat surface does not provide:

*   Configuration mutation
*   Escalation policy overrides
*   Infrastructure control
*   Direct access to container management

It is a conversational surface only.

### 5.3 Signal (Remote Channel)

Signal provides a remote interaction surface operating in outbound polling mode.

Characteristics:

*   Outbound polling only
*   No inbound webhook
*   No public port exposure
*   Secure DM isolation enabled
*   Speaker metadata used for identity mapping

Signal identities map deterministically to internal User IDs. Messages received from Signal are routed into OpenClaw sessions under the mapped user identity.

Signal serves as:

*   A remote personal interface
*   A family collaboration channel
*   A long-term supported transport
*   A convenient mobile-first interaction layer

Signal does not grant elevated authority and does not bypass session isolation rules.

### 5.4 Group Sessions & Multi-User Semantics

Group interactions are structured and identity-aware. Moltbox does not collapse user identity within shared channels and does not merge memory across users.

A group session contains:

*   Multiple participating internal User IDs
*   A shared conversational context
*   Explicit speaker attribution per message

Speaker attribution is derived from channel metadata and mapped to internal User IDs. If speaker identity cannot be resolved deterministically, execution is rejected.

Memory discipline remains strict:

*   Shared conversational context is session-scoped
*   Long-term memory remains user-scoped
*   Each primary agent operates within its own memory namespace
*   No automatic cross-user long-term memory writes occur

Group collaboration is supported without compromising memory isolation.

### 5.5 Administrative Surfaces

Administrative authority remains local and intentional. Moltbox is not a public-facing server and does not operate as a network edge device.

#### Console (Privileged Surface)

The OpenClaw Console is the authoritative control surface of the appliance.

Characteristics:

*   LAN-only access
*   Full orchestration visibility
*   Configuration mutation capability
*   Direct access to tool routing behavior
*   Escalation override authority

The console is not a general user interface. It is the owner’s maintenance hatch and operates directly against the control plane.

#### SSH

SSH provides infrastructure-level access.

Characteristics:

*   Key-based authentication
*   LAN-only exposure
*   Used for maintenance, updates, and container management
*   Not exposed publicly

#### Remote Access Evolution

Baseline posture:

*   LAN-only administrative access
*   No port forwarding
*   No embedded VPN services

Future posture:

*   Remote access provided through a network appliance (router-level VPN)
*   Moltbox does not host VPN services

Conditional future:

*   A narrowly scoped application-layer endpoint may be exposed if a dedicated external application is introduced
*   Such exposure would include explicit authentication boundaries, rate limiting, and logging

At no stage does Moltbox become a general-purpose externally managed server.

Moltbox interaction surfaces are structured, identity-driven, and bounded. Conversational access is broad within the LAN and optionally remote through Signal. Administrative authority remains local. Identity persists across channels, and memory remains isolated per user. Transport surface does not imply privilege elevation.

## 6\. Operational Philosophy

Moltbox is an appliance built around authority, determinism, and local control. It does not attempt to be a distributed platform, a hosted SaaS product, or a framework seeking extensions. It is a single-host system that reasons deliberately and executes locally.

### 6.1 Appliance, Not Framework

Moltbox is designed as a dedicated machine with a single control plane. Orchestration authority resides locally. There is no distributed coordinator, no secondary execution host, and no hidden cloud dependency for baseline function.

Services run in containers for isolation and reproducibility, but authority is singular. The machine is the boundary. Scale increases capability, not authority distribution.

### 6.2 Local-First by Default

Local capability is the baseline. The routing model, memory store, orchestration logic, and execution boundaries all reside on the appliance. Cloud cognition augments reasoning depth but does not own execution.

If cloud services become unavailable, Moltbox continues operating in bounded local mode. Stability is prioritized over feature completeness.

### 6.3 Determinism Over Cleverness

Escalation is policy-governed. Tool invocation is explicit. Memory writes are structured. There are no hidden automation layers mutating state without traceability.

Every irreversible action passes through the local control plane. Logs exist for every escalation and tool invocation. The system favors predictability over opportunistic autonomy.

### 6.4 Upgrade & Evolution Path

Moltbox evolves through controlled substitution, not architectural rewrites.

*   GPU upgrades increase local reasoning headroom without altering control structure.
*   Memory expansion increases retention and indexing capacity without changing session semantics.
*   Additional services attach through container boundaries without redefining authority.

Future layers—reflection engines, sovereign cognition clusters, or external applications—extend the appliance but do not replace its foundation.

Moltbox grows by strengthening the single-host control plane, not by fragmenting it.