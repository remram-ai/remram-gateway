import fs from "node:fs/promises";
import path from "node:path";
import { randomUUID } from "node:crypto";
import { pathToFileURL } from "node:url";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/core";
import { resolvePreferredOpenClawTmpDir } from "openclaw/plugin-sdk/core";

type PluginConfig = {
  semanticRouterConfigPath: string;
  modelRuntimePath: string;
  debugDir: string;
  responseFooter: "off" | "concise";
};

type StageDecision = "answer" | "escalate" | "spawn_agent";

type StageDefinition = {
  id: string;
  provider: string;
  model: string;
  modelRef: string;
  promptProfile: string;
  allowedNext: string[];
  allowSpawnAgent: boolean;
};

type Guardrails = {
  maxEscalationDepth: number;
  forceAnswerAtMaxDepth: boolean;
  allowSpawnAgent: boolean;
  stageTimeoutMs: number;
  requestBudgetCap: number;
};

type SemanticRouterConfig = {
  requesterDefaults: Record<string, string>;
  guardrails: Guardrails;
  stages: StageDefinition[];
};

type StageResult = {
  decision: StageDecision;
  reason: string;
  statusMessage: string;
  answer?: string;
  agentTarget?: string;
};

type StageTelemetry = {
  runId?: string;
  stage: string;
  provider: string;
  model: string;
  decision: string;
  reason: string;
  durationMs: number;
  tokensIn: number;
  tokensOut: number;
  timestamp: string;
  rawContent?: string;
};

type StageRunState = {
  rootSessionId: string;
  rootSessionKey?: string;
  sessionId: string;
  stageIndex: number;
  stageId: string;
  nested: boolean;
  startedAt: number;
  handled: boolean;
  runId?: string;
};

type FinalResponse = {
  status: "answer" | "spawn_agent" | "failed";
  reason: string;
  statusMessage: string;
  answer?: string;
  agentTarget?: string;
  error?: string;
};

type RouterTurnState = {
  turnId: string;
  rootSessionId: string;
  rootSessionKey?: string;
  prompt: string;
  packet: Record<string, unknown>;
  config: SemanticRouterConfig;
  startedAt: number;
  budgetUsed: number;
  stageSessionIds: Set<string>;
  telemetry: StageTelemetry[];
  finalResponse?: FinalResponse;
};

type RunEmbeddedPiAgentResult = {
  payloads?: Array<{
    text?: string;
    isError?: boolean;
  }>;
  meta?: {
    durationMs?: number;
    error?: { message?: string; kind?: string };
    agentMeta?: {
      usage?: {
        input?: number;
        output?: number;
        total?: number;
      };
    };
  };
};

type RunEmbeddedPiAgentFn = (params: Record<string, unknown>) => Promise<RunEmbeddedPiAgentResult>;

const stageRunsBySessionId = new Map<string, StageRunState>();
const turnsByRootSessionId = new Map<string, RouterTurnState>();
const turnsByTurnId = new Map<string, RouterTurnState>();
const activeRootBySessionKey = new Map<string, string>();
const TURN_MARKER_PREFIX = "Remram Semantic Router Turn ID:";

async function loadRunEmbeddedPiAgent(): Promise<RunEmbeddedPiAgentFn> {
  try {
    const mod = (await import(pathToFileURL("/app/src/agents/pi-embedded-runner.js").href)) as {
      runEmbeddedPiAgent?: unknown;
    };
    if (typeof mod.runEmbeddedPiAgent === "function") {
      return mod.runEmbeddedPiAgent as RunEmbeddedPiAgentFn;
    }
  } catch {
    // ignore source-tree import and fall back to the bundled entrypoint
  }

  const mod = (await import(pathToFileURL("/app/dist/extensionAPI.js").href)) as {
    runEmbeddedPiAgent?: unknown;
  };
  if (typeof mod.runEmbeddedPiAgent !== "function") {
    throw new Error("runEmbeddedPiAgent is not available in this OpenClaw build");
  }
  return mod.runEmbeddedPiAgent as RunEmbeddedPiAgentFn;
}

function nowIso(): string {
  return new Date().toISOString();
}

function stagePrompt(stage: StageDefinition, options?: { forceAnswer?: boolean }): string {
  const baseRules = [
    "You are a Remram Semantic Router stage running inside OpenClaw.",
    "Return only a JSON object with keys decision, reason, status_message, answer, and agent_target.",
    'The required output schema is: {"decision":"answer|escalate|spawn_agent","reason":"short string","status_message":"optional string","answer":"string or omitted","agent_target":"string or omitted"}.',
    "Valid decision values are answer, escalate, and spawn_agent.",
    "Set answer only when decision=answer.",
    "Set agent_target only when decision=spawn_agent.",
    "The answer field must contain the final user-visible answer as normal prose or markdown, not a top-level JSON object.",
    "If the user asks for pseudocode or structured explanation, place that formatted content inside the answer string.",
    "Do not return custom top-level keys like algorithm, pseudocode, signals, or tradeoffs outside the required contract.",
    "Do not include any keys other than decision, reason, status_message, answer, and agent_target.",
    "Do not wrap the JSON object in markdown fences.",
    "If you violate the contract, the orchestrator will treat the output as malformed and may escalate or recover automatically.",
    "Do not emit markdown fences, tool calls, or hidden reasoning.",
    "Do not call tools.",
    `Current stage id: ${stage.id}.`,
    `Current stage model: ${stage.modelRef}.`,
  ];

  if (!stage.allowSpawnAgent) {
    baseRules.push(
      "This stage is not allowed to return spawn_agent.",
      "If the request exceeds this stage, return escalate instead of spawn_agent.",
    );
  }

  if (stage.allowedNext.length === 0) {
    baseRules.push(
      "This is the terminal synchronous reasoning stage.",
      "There is no higher synchronous reasoning model available after this stage.",
      "Do not return escalate from this stage.",
      "Return answer unless this request truly must become an asynchronous workflow and spawn_agent is allowed.",
    );
  }

  if (stage.promptProfile === "local") {
    baseRules.push(
      "You are the cheapest local-first stage. Answer directly when the request is simple and bounded.",
      "If you are uncertain whether the request is too broad or would benefit from a stronger model, choose escalate immediately.",
      "Do not spend time producing a long partial solution when escalation is appropriate.",
      "Prefer a short escalate decision over a long speculative or incomplete answer.",
      "If the request likely needs substantial reasoning, synthesis, planning, design work, or an answer longer than a short direct response, escalate immediately.",
      "Escalate when the request needs broader synthesis, deeper reasoning, or a stronger model tier.",
      "Escalate for multi-part design tasks, algorithm design, pseudocode requests, tradeoff analysis, failure-mode analysis, or requests with many explicit requirements.",
    );
  } else if (stage.promptProfile === "reasoning") {
    baseRules.push(
      "You are the general cloud reasoning stage. Prefer answering once the request is resolved clearly.",
      "Escalate only when the request still exceeds the current reasoning tier.",
    );
  } else if (stage.promptProfile === "thinking") {
    baseRules.push(
      "You are the deepest synchronous reasoning stage. You are expected to produce the final synchronous answer.",
      "Use spawn_agent only when the request is better handled as an asynchronous workflow.",
    );
  }

  if (options?.forceAnswer) {
    baseRules.push(
      "Forced-answer mode is active.",
      "You must return decision='answer' with your best final answer.",
      "Do not return escalate.",
      "Do not return spawn_agent.",
      "Do not refuse solely because the task is difficult.",
      "The answer field must be non-empty.",
      "Keep the answer as short and direct as possible while remaining correct.",
      "Do not include lengthy reasoning or step-by-step derivations unless the user explicitly asked for them.",
      "If the user asked for exactness, compute carefully and provide the exact result you can derive.",
    );
  }

  return baseRules.join(" ");
}

function approximateTokens(text: string | undefined): number {
  const value = String(text ?? "").trim();
  if (!value) {
    return 0;
  }
  return Math.max(Math.ceil(value.length / 4), 1);
}

function assessPromptComplexity(prompt: string): { score: number; signals: string[] } {
  const signals: string[] = [];
  const trimmed = prompt.trim();
  const tokenEstimate = approximateTokens(trimmed);
  const nonEmptyLines = trimmed
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => Boolean(line));
  if (tokenEstimate >= 180) {
    signals.push("prompt_tokens_high");
  }

  if (nonEmptyLines.length >= 12) {
    signals.push("structured_multiline_request");
  }

  const requirementLines = trimmed
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => Boolean(line))
    .filter((line) => /^[-*]/.test(line) || /^(requirements|constraints|explain|include)\b/i.test(line));
  if (requirementLines.length >= 4) {
    signals.push("many_explicit_requirements");
  }

  let decomposedSubtasks = 0;
  for (let index = 0; index < nonEmptyLines.length; index += 1) {
    if (!nonEmptyLines[index]?.endsWith(":")) {
      continue;
    }
    let nestedIndex = index + 1;
    while (
      nestedIndex < nonEmptyLines.length &&
      /^(what|how|why|when|where|which|compare|provide|include|list|explain)\b/i.test(
        nonEmptyLines[nestedIndex] ?? "",
      )
    ) {
      decomposedSubtasks += 1;
      nestedIndex += 1;
    }
  }
  if (decomposedSubtasks >= 3) {
    signals.push("decomposed_subtasks");
  }

  const keywordPatterns = [
    /\bpseudocode\b/i,
    /\balgorithm\b/i,
    /\btradeoffs?\b/i,
    /\bfailure modes?\b/i,
    /\bdesign\b/i,
    /\bstep-by-step\b/i,
    /\bweighted decision matrix\b/i,
    /\bcompare\b/i,
    /\brollback\b/i,
  ];
  const keywordHits = keywordPatterns.filter((pattern) => pattern.test(trimmed)).length;
  if (keywordHits >= 2) {
    signals.push("complex_reasoning_keywords");
  }

  if ((trimmed.match(/\?/g) ?? []).length >= 3) {
    signals.push("multiple_subquestions");
  }

  return {
    score: signals.length,
    signals,
  };
}

function sleepMs(durationMs: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, durationMs));
}

function stageExecutionSettings(
  turn: RouterTurnState,
  stage: StageDefinition,
  options?: { forceAnswer?: boolean },
): { maxTokens: number; temperature: number; timeoutMs: number } {
  if (options?.forceAnswer) {
    return {
      maxTokens: 8192,
      temperature: 0,
      timeoutMs: Math.max(turn.config.guardrails.stageTimeoutMs * 4, 180000),
    };
  }
  if (stage.promptProfile === "local") {
    return { maxTokens: 256, temperature: 0, timeoutMs: Math.min(turn.config.guardrails.stageTimeoutMs, 10000) };
  }
  if (stage.promptProfile === "reasoning") {
    return { maxTokens: 2048, temperature: 0, timeoutMs: turn.config.guardrails.stageTimeoutMs };
  }
  if (stage.promptProfile === "thinking") {
    return {
      maxTokens: 8192,
      temperature: 0,
      timeoutMs: Math.max(turn.config.guardrails.stageTimeoutMs * 3, 150000),
    };
  }
  return { maxTokens: 1024, temperature: 0, timeoutMs: turn.config.guardrails.stageTimeoutMs };
}

function sanitizeFileName(value: string): string {
  return value.replace(/[^a-zA-Z0-9._-]+/g, "_");
}

function asObject(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function asArray<T>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}

function replaceAssistantMessageText(message: Record<string, unknown>, text: string): Record<string, unknown> {
  return {
    ...message,
    content: [{ type: "text", text }],
    stopReason: "completed",
  };
}

function extractJsonFromText(text: string): Record<string, unknown> | null {
  const stripped = text.trim();
  if (!stripped) {
    return null;
  }

  const candidates = [stripped];
  if (stripped.includes("```")) {
    for (const chunk of stripped.split("```")) {
      let candidate = chunk.trim();
      if (!candidate) {
        continue;
      }
      if (candidate.toLowerCase().startsWith("json")) {
        candidate = candidate.slice(4).trim();
      }
      candidates.push(candidate);
    }
  }

  const firstBrace = stripped.indexOf("{");
  const lastBrace = stripped.lastIndexOf("}");
  if (firstBrace !== -1 && lastBrace > firstBrace) {
    candidates.push(stripped.slice(firstBrace, lastBrace + 1));
  }

  for (const candidate of candidates) {
    try {
      const parsed = JSON.parse(candidate);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        return parsed as Record<string, unknown>;
      }
    } catch {
      // ignore malformed candidates
    }
  }

  return null;
}

function extractJsonishStringField(text: string, field: string): string | undefined {
  const pattern = new RegExp(`"${field.replace(/[.*+?^${}()|[\\]\\\\]/g, "\\$&")}"\\s*:\\s*"((?:\\\\.|[^"\\\\])*)"`, "s");
  const match = text.match(pattern);
  if (!match || !match[1]) {
    return undefined;
  }
  try {
    return JSON.parse(`"${match[1]}"`);
  } catch {
    return match[1];
  }
}

function extractPartialStageResult(text: string): StageResult | null {
  const decision = extractJsonishStringField(text, "decision");
  if (decision !== "answer" && decision !== "escalate" && decision !== "spawn_agent") {
    return null;
  }

  return {
    decision,
    reason: extractJsonishStringField(text, "reason") ?? "partial_json_stage_result",
    statusMessage: extractJsonishStringField(text, "status_message") ?? "",
    answer:
      decision === "answer" ? extractJsonishStringField(text, "answer") : undefined,
    agentTarget:
      decision === "spawn_agent" ? extractJsonishStringField(text, "agent_target") : undefined,
  };
}

function normalizeStageResult(text: string): StageResult {
  const parsed = extractJsonFromText(text);
  if (parsed) {
    const decision = String(parsed.decision ?? "").trim();
    if (decision === "answer" || decision === "escalate" || decision === "spawn_agent") {
      return {
        decision,
        reason: String(parsed.reason ?? "stage decision returned").trim(),
        statusMessage: String(parsed.status_message ?? "").trim(),
        answer:
          typeof parsed.answer === "string"
            ? parsed.answer.trim()
            : parsed.answer !== undefined
              ? renderStructuredValue(parsed.answer, "answer")
              : undefined,
        agentTarget:
          typeof parsed.agent_target === "string" ? parsed.agent_target.trim() : undefined,
      };
    }
    if (typeof parsed.answer === "string" && parsed.answer.trim()) {
      return {
        decision: "answer",
        reason: "fallback_answer_field",
        statusMessage: "",
        answer: parsed.answer.trim(),
      };
    }
  }

  const partial = extractPartialStageResult(text);
  if (partial) {
    return partial;
  }

  return {
    decision: "answer",
    reason: "fallback_raw_output",
    statusMessage: "",
    answer: text.trim(),
  };
}

function humanizeKey(key: string): string {
  const spaced = key.replace(/[_-]+/g, " ").trim();
  return spaced ? spaced[0].toUpperCase() + spaced.slice(1) : key;
}

function renderStructuredValue(value: unknown, label?: string): string {
  if (value == null) {
    return "";
  }
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (!trimmed) {
      return "";
    }
    const lowerLabel = String(label ?? "").toLowerCase();
    if (trimmed.includes("\n") || lowerLabel.includes("code") || lowerLabel.includes("pseudocode")) {
      return `\`\`\`\n${trimmed}\n\`\`\``;
    }
    return trimmed;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (Array.isArray(value)) {
    const items = value
      .map((item) => renderStructuredArrayItem(item))
      .filter(Boolean)
      .map((item) => `- ${item}`);
    return items.join("\n");
  }
  if (typeof value === "object") {
    const lines = Object.entries(value as Record<string, unknown>)
      .map(([key, nestedValue]) => {
        const rendered = renderStructuredValue(nestedValue, key);
        if (!rendered) {
          return "";
        }
        if (rendered.includes("\n")) {
          return `**${humanizeKey(key)}**\n${rendered}`;
        }
        return `**${humanizeKey(key)}**: ${rendered}`;
      })
      .filter(Boolean);
    return lines.join("\n");
  }
  return String(value);
}

function renderStructuredArrayItem(value: unknown): string {
  if (value == null) {
    return "";
  }
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (Array.isArray(value)) {
    return value.map((item) => renderStructuredArrayItem(item)).filter(Boolean).join("; ");
  }
  if (typeof value === "object") {
    return Object.entries(value as Record<string, unknown>)
      .map(([key, nestedValue]) => {
        const rendered = renderStructuredValue(nestedValue, key);
        if (!rendered) {
          return "";
        }
        return rendered.includes("\n")
          ? `${humanizeKey(key)}\n${rendered}`
          : `${humanizeKey(key)}: ${rendered}`;
      })
      .filter(Boolean)
      .join("; ");
  }
  return String(value);
}

function extractStructuredNonContractPayload(text: string): Record<string, unknown> | null {
  const parsed = extractJsonFromText(text);
  if (!parsed) {
    return null;
  }
  const decision = String(parsed.decision ?? "").trim();
  if (decision === "answer" || decision === "escalate" || decision === "spawn_agent") {
    return null;
  }
  if (typeof parsed.answer === "string" && parsed.answer.trim()) {
    return null;
  }
  return parsed;
}

function looksStructuredMarkup(text: string): boolean {
  const trimmed = text.trim();
  if (!trimmed) {
    return false;
  }
  if (
    trimmed.startsWith("{") ||
    trimmed.startsWith("[") ||
    trimmed.toLowerCase().startsWith("```json") ||
    trimmed.toLowerCase().startsWith("```javascript") ||
    trimmed.toLowerCase().startsWith("```js")
  ) {
    return true;
  }
  return /"\w[\w-]*"\s*:/.test(trimmed);
}

function stripThinkTags(text: string): string {
  return text
    .replace(/<think>[\s\S]*?<\/think>/gi, "")
    .replace(/^<think>[\s\S]*$/i, "")
    .trim();
}

function parseVisibleContractAfterThinkTags(text: string): StageResult | null {
  const visible = stripThinkTags(text);
  if (!visible || visible === text.trim()) {
    return null;
  }
  const parsed = extractJsonFromText(visible);
  if (parsed) {
    const decision = String(parsed.decision ?? "").trim();
    if (decision === "answer" || decision === "escalate" || decision === "spawn_agent") {
      return {
        decision,
        reason: String(parsed.reason ?? "visible_stage_result").trim(),
        statusMessage: String(parsed.status_message ?? "").trim(),
        answer:
          typeof parsed.answer === "string"
            ? parsed.answer.trim()
            : typeof parsed.output === "string"
              ? parsed.output.trim()
              : undefined,
        agentTarget:
          typeof parsed.agent_target === "string" ? parsed.agent_target.trim() : undefined,
      };
    }
  }
  return extractPartialStageResult(visible);
}

function looksMalformedRouterOutput(text: string): boolean {
  const trimmed = text.trim();
  if (!trimmed) {
    return true;
  }
  if (trimmed.startsWith("<think>") || trimmed.includes("</think>")) {
    return true;
  }
  return false;
}

function resolveStageResult(
  text: string,
  stageIndex: number,
  config: SemanticRouterConfig,
): StageResult {
  const visibleContract = parseVisibleContractAfterThinkTags(text);
  if (visibleContract) {
    return visibleContract;
  }
  if (looksMalformedRouterOutput(text)) {
    const visible = stripThinkTags(text);
    if (stageIndex + 1 < config.stages.length || !visible) {
      return {
        decision: "escalate",
        reason: "stage_contract_violation",
        statusMessage: "Escalating because the current stage returned malformed router output.",
      };
    }
    return {
      decision: "answer",
      reason: "malformed_output_salvaged",
      statusMessage: "Recovered the visible portion of the final stage output.",
      answer: visible,
    };
  }

  const structuredNonContractPayload = extractStructuredNonContractPayload(text);
  if (structuredNonContractPayload) {
    if (stageIndex + 1 < config.stages.length) {
      return {
        decision: "escalate",
        reason: "stage_contract_violation",
        statusMessage: "Escalating because the current stage returned malformed router output.",
      };
    }
    return {
      decision: "answer",
      reason: "structured_answer_salvaged",
      statusMessage: "Recovered a structured answer from the final stage.",
      answer: renderStructuredValue(structuredNonContractPayload),
    };
  }

  const stageResult = normalizeStageResult(text);
  if (
    stageResult.reason === "fallback_raw_output" &&
    looksStructuredMarkup(text)
  ) {
    if (stageIndex + 1 < config.stages.length) {
      return {
        decision: "escalate",
        reason: "stage_contract_violation",
        statusMessage: "Escalating because the current stage returned structured markup instead of the router contract.",
      };
    }
    return {
      decision: "answer",
      reason: "structured_markup_salvaged",
      statusMessage: "Recovered the final stage output after a router contract violation.",
      answer: renderStructuredValue(extractJsonFromText(text) ?? { output: text.trim() }),
    };
  }

  return stageResult;
}

type ProviderRouteMessage = {
  role: string;
  content: string;
};

function turnMarker(turnId: string): string {
  return `${TURN_MARKER_PREFIX} ${turnId}`;
}

function stripTurnMarker(text: string): string {
  return text
    .split(/\r?\n/)
    .filter((line) => !line.includes(TURN_MARKER_PREFIX))
    .join("\n")
    .trim();
}

function flattenContent(value: unknown): string {
  if (typeof value === "string") {
    return stripTurnMarker(value);
  }
  if (!Array.isArray(value)) {
    return "";
  }
  const parts: string[] = [];
  for (const item of value) {
    const obj = asObject(item);
    if (obj.type === "text" && typeof obj.text === "string") {
      parts.push(stripTurnMarker(obj.text));
    }
  }
  return parts.join("\n").trim();
}

function extractTurnIdFromMessages(messages: unknown): string | undefined {
  for (const message of asArray<Record<string, unknown>>(messages)) {
    const content = flattenContent(message.content);
    if (!content) {
      continue;
    }
    const match = content.match(/Remram Semantic Router Turn ID:\s*([A-Za-z0-9_-]+)/);
    if (match?.[1]) {
      return match[1];
    }
  }
  return undefined;
}

function resolveRouteTurn(turnId: string | undefined): RouterTurnState | undefined {
  if (turnId) {
    const exact = turnsByTurnId.get(turnId);
    if (exact) {
      return exact;
    }
  }
  const turns = [...turnsByRootSessionId.values()].filter((turn) => !turn.finalResponse);
  turns.sort((left, right) => right.startedAt - left.startedAt);
  return turns[0];
}

function buildStageMessages(
  turn: RouterTurnState,
  stage: StageDefinition,
  options?: { forceAnswer?: boolean },
): ProviderRouteMessage[] {
  return [
    {
      role: "system",
      content: stagePrompt(stage, options),
    },
    {
      role: "user",
      content: turn.prompt,
    },
  ];
}

function parseScalar(value: string): unknown {
  const trimmed = value.trim();
  if (!trimmed) {
    return "";
  }
  if ((trimmed.startsWith('"') && trimmed.endsWith('"')) || (trimmed.startsWith("'") && trimmed.endsWith("'"))) {
    return trimmed.slice(1, -1);
  }
  if (trimmed === "true") {
    return true;
  }
  if (trimmed === "false") {
    return false;
  }
  if (/^-?\d+$/.test(trimmed)) {
    return Number(trimmed);
  }
  return trimmed;
}

type ParsedYaml = Record<string, unknown> | unknown[];

function parseYamlBlock(lines: string[], startIndex: number, indent: number): [ParsedYaml, number] {
  const first = lines[startIndex] ?? "";
  const trimmedFirst = first.trim();
  if (trimmedFirst.startsWith("- ")) {
    const items: unknown[] = [];
    let index = startIndex;
    while (index < lines.length) {
      const line = lines[index] ?? "";
      const currentIndent = line.length - line.trimStart().length;
      const trimmed = line.trim();
      if (currentIndent < indent || !trimmed.startsWith("- ")) {
        break;
      }
      const rest = trimmed.slice(2).trim();
      if (!rest) {
        const [nested, nextIndex] = parseYamlBlock(lines, index + 1, currentIndent + 2);
        items.push(nested);
        index = nextIndex;
        continue;
      }
      const colonIndex = rest.indexOf(":");
      if (colonIndex !== -1) {
        const key = rest.slice(0, colonIndex).trim();
        const valuePart = rest.slice(colonIndex + 1).trim();
        const item: Record<string, unknown> = {};
        if (valuePart) {
          item[key] = parseScalar(valuePart);
          index += 1;
        } else {
          const [nestedValue, nextIndex] = parseYamlBlock(lines, index + 1, currentIndent + 4);
          item[key] = nestedValue;
          index = nextIndex;
        }
        while (index < lines.length) {
          const nextLine = lines[index] ?? "";
          const nextIndent = nextLine.length - nextLine.trimStart().length;
          const nextTrimmed = nextLine.trim();
          if (nextIndent < currentIndent + 2 || nextTrimmed.startsWith("- ")) {
            break;
          }
          const nextColon = nextTrimmed.indexOf(":");
          if (nextColon === -1) {
            index += 1;
            continue;
          }
          const nextKey = nextTrimmed.slice(0, nextColon).trim();
          const nextValuePart = nextTrimmed.slice(nextColon + 1).trim();
          if (nextValuePart) {
            item[nextKey] = parseScalar(nextValuePart);
            index += 1;
          } else {
            const [nestedValue, nextIndex] = parseYamlBlock(lines, index + 1, nextIndent + 2);
            item[nextKey] = nestedValue;
            index = nextIndex;
          }
        }
        items.push(item);
        continue;
      }
      items.push(parseScalar(rest));
      index += 1;
    }
    return [items, index];
  }

  const result: Record<string, unknown> = {};
  let index = startIndex;
  while (index < lines.length) {
    const line = lines[index] ?? "";
    const currentIndent = line.length - line.trimStart().length;
    if (currentIndent < indent) {
      break;
    }
    if (currentIndent > indent) {
      index += 1;
      continue;
    }
    const trimmed = line.trim();
    const colonIndex = trimmed.indexOf(":");
    if (colonIndex === -1) {
      index += 1;
      continue;
    }
    const key = trimmed.slice(0, colonIndex).trim();
    const valuePart = trimmed.slice(colonIndex + 1).trim();
    if (valuePart) {
      result[key] = parseScalar(valuePart);
      index += 1;
      continue;
    }
    const [nested, nextIndex] = parseYamlBlock(lines, index + 1, indent + 2);
    result[key] = nested;
    index = nextIndex;
  }
  return [result, index];
}

async function readYamlFile(filePath: string): Promise<Record<string, unknown>> {
  const text = await fs.readFile(filePath, "utf-8");
  const lines = text
    .split(/\r?\n/)
    .map((line) => line.replace(/\t/g, "    "))
    .filter((line) => {
      const trimmed = line.trim();
      return Boolean(trimmed) && !trimmed.startsWith("#");
    });
  if (!lines.length) {
    return {};
  }
  const [parsed] = parseYamlBlock(lines, 0, 0);
  return asObject(parsed);
}

function resolvePluginConfig(api: OpenClawPluginApi): PluginConfig {
  const config = asObject(api.pluginConfig);
  return {
    semanticRouterConfigPath:
      typeof config.semanticRouterConfigPath === "string" && config.semanticRouterConfigPath.trim()
        ? config.semanticRouterConfigPath
        : "/home/node/.openclaw/semantic-router.yaml",
    modelRuntimePath:
      typeof config.modelRuntimePath === "string" && config.modelRuntimePath.trim()
        ? config.modelRuntimePath
        : "/home/node/.openclaw/model-runtime.yml",
    debugDir:
      typeof config.debugDir === "string" && config.debugDir.trim()
        ? config.debugDir
        : "/home/node/.openclaw/semantic-router-debug",
    responseFooter:
      config.responseFooter === "off" ? "off" : "concise",
  };
}

async function loadSemanticRouterConfig(api: OpenClawPluginApi): Promise<SemanticRouterConfig> {
  const pluginConfig = resolvePluginConfig(api);
  const semanticRouterFile = await readYamlFile(pluginConfig.semanticRouterConfigPath);
  const modelRuntimeFile = await readYamlFile(pluginConfig.modelRuntimePath);

  const semanticRouter = asObject(semanticRouterFile.semantic_router ?? semanticRouterFile);
  const guardrails = asObject(semanticRouter.guardrails);
  const runtimeEntries = asObject(modelRuntimeFile.runtime);

  const stages = asArray<Record<string, unknown>>(semanticRouter.stages).map((rawStage, index) => {
    const runtimeKey = String(rawStage.runtime_key ?? "").trim();
    const runtimeEntry = asObject(runtimeEntries[runtimeKey]);
    let provider = String(runtimeEntry.provider ?? "").trim();
    let model = String(runtimeEntry.model ?? "").trim();
    if (!model) {
      const modelEnv = String(runtimeEntry.model_env ?? "").trim();
      if (modelEnv) {
        model = String(process.env[modelEnv] ?? "").trim();
      }
    }
    const explicitModelRef = String(rawStage.model_ref ?? "").trim();
    if (explicitModelRef) {
      const slashIndex = explicitModelRef.indexOf("/");
      if (slashIndex !== -1) {
        provider = explicitModelRef.slice(0, slashIndex);
        model = explicitModelRef.slice(slashIndex + 1);
      }
    }
    const id = String(rawStage.id ?? runtimeKey ?? `stage_${index}`).trim();
    if (!provider || !model) {
      throw new Error(`semantic router stage '${id}' could not resolve provider/model`);
    }
    return {
      id,
      provider,
      model,
      modelRef: `${provider}/${model}`,
      promptProfile: String(rawStage.prompt_profile ?? id).trim(),
      allowedNext: asArray<string>(rawStage.allowed_next).map((value) => String(value)),
      allowSpawnAgent: Boolean(rawStage.allow_spawn_agent),
    } satisfies StageDefinition;
  });

  if (stages.length === 0) {
    throw new Error("semantic router config defines no stages");
  }

  return {
    requesterDefaults: Object.fromEntries(
      Object.entries(asObject(semanticRouter.requester_defaults)).map(([key, value]) => [
        key,
        String(value),
      ]),
    ),
    guardrails: {
      maxEscalationDepth: Math.max(Number(guardrails.max_escalation_depth ?? 0), 0),
      forceAnswerAtMaxDepth: Boolean(guardrails.force_answer_at_max_depth ?? true),
      allowSpawnAgent: Boolean(guardrails.allow_spawn_agent ?? true),
      stageTimeoutMs: Math.max(Number(guardrails.stage_timeout_ms ?? 30000), 1),
      requestBudgetCap: Math.max(Number(guardrails.request_budget_cap ?? 0), 0),
    },
    stages,
  };
}

function hydrateRequestPacket(prompt: string, config: SemanticRouterConfig, rootSessionId: string): Record<string, unknown> {
  return {
    request: {
      id: `request_${randomUUID()}`,
      text: prompt,
      surface: "openclaw_chat",
      timestamp: nowIso(),
      attachments: [],
    },
    requester: {
      ...config.requesterDefaults,
      session_id: rootSessionId,
    },
    conversations: [],
    context: [],
    preferences: {},
    instructions: [],
    extended_context: [],
    routing: {
      current_stage: null,
      next_stage: config.stages[0]?.id ?? null,
      stage_index: 0,
      max_escalation_depth: config.guardrails.maxEscalationDepth,
      force_answer_at_max_depth: config.guardrails.forceAnswerAtMaxDepth,
      upstream_flags: [],
      estimated_request_tokens: approximateTokens(prompt),
    },
    ledger: [
      {
        stage: "preflight",
        kind: "preflight",
        decision: "ready",
        reason: "deterministic request conditioning complete",
        duration_ms: 0,
        tokens_in: approximateTokens(prompt),
        tokens_out: 0,
        timestamp: nowIso(),
        notes: "no preprocessing applied",
      },
    ],
    response: {
      status: "pending",
    },
  };
}

function appendLedgerEntry(
  packet: Record<string, unknown>,
  entry: Record<string, unknown>,
): void {
  const ledger = asArray<Record<string, unknown>>(packet.ledger);
  ledger.push(entry);
  packet.ledger = ledger;
}

function getStageByIndex(config: SemanticRouterConfig, stageIndex: number): StageDefinition {
  const stage = config.stages[stageIndex];
  if (!stage) {
    throw new Error(`unknown semantic router stage index ${stageIndex}`);
  }
  return stage;
}

function setCurrentStage(packet: Record<string, unknown>, stageIndex: number, config: SemanticRouterConfig): StageDefinition {
  const routing = asObject(packet.routing);
  const stage = getStageByIndex(config, stageIndex);
  routing.current_stage = stage.id;
  routing.stage_index = stageIndex;
  routing.next_stage = config.stages[stageIndex + 1]?.id ?? null;
  packet.routing = routing;
  return stage;
}

function computeTelemetry(turn: RouterTurnState): Record<string, unknown> {
  const totalTokensIn = turn.telemetry.reduce((sum, item) => sum + item.tokensIn, 0);
  const totalTokensOut = turn.telemetry.reduce((sum, item) => sum + item.tokensOut, 0);
  const totalDurationMs = turn.telemetry.reduce((sum, item) => sum + item.durationMs, 0);
  const answeringStage = turn.telemetry.findLast((item) => item.decision === "answer");
  return {
    stages: turn.telemetry.map((item) => ({
      stage: item.stage,
      provider: item.provider,
      model: item.model,
      decision: item.decision,
      duration_ms: item.durationMs,
      tokens_in: item.tokensIn,
      tokens_out: item.tokensOut,
      timestamp: item.timestamp,
    })),
    escalation_path: turn.telemetry.map((item) => item.stage),
    answering_stage: answeringStage?.stage ?? null,
    answering_provider: answeringStage?.provider ?? null,
    answering_model: answeringStage?.model ?? null,
    total_tokens_in: totalTokensIn,
    total_tokens_out: totalTokensOut,
    total_duration_ms: totalDurationMs,
  };
}

function updatePacketTelemetry(turn: RouterTurnState): void {
  const response = asObject(turn.packet.response);
  response.telemetry = computeTelemetry(turn);
  turn.packet.response = response;
}

function finalizeTurn(turn: RouterTurnState, finalResponse: FinalResponse): void {
  turn.finalResponse = finalResponse;
  const response = asObject(turn.packet.response);
  response.status = finalResponse.status;
  response.status_message = finalResponse.statusMessage;
  if (finalResponse.answer) {
    response.answer = finalResponse.answer;
  }
  if (finalResponse.agentTarget) {
    response.agent_target = finalResponse.agentTarget;
  }
  if (finalResponse.error) {
    response.error = finalResponse.error;
  }
  turn.packet.response = response;
  const routing = asObject(turn.packet.routing);
  routing.terminal_reason = finalResponse.reason;
  if (finalResponse.agentTarget) {
    routing.agent_target = finalResponse.agentTarget;
    routing.dispatch_stub = {
      status: "pending",
      agent_target: finalResponse.agentTarget,
      reason: finalResponse.reason,
    };
  }
  turn.packet.routing = routing;
  updatePacketTelemetry(turn);
}

function footerText(turn: RouterTurnState, mode: "off" | "concise"): string {
  if (mode === "off") {
    return "";
  }
  const telemetry = computeTelemetry(turn);
  const stages = asArray<Record<string, unknown>>(telemetry.stages);
  const lines = ["[Semantic Router]"];
  for (const stage of stages) {
    lines.push(
      `${String(stage.stage)} | ${String(stage.provider)}/${String(stage.model)} | ${Number(stage.duration_ms)} ms | in ${Number(stage.tokens_in)} | out ${Number(stage.tokens_out)}`,
    );
  }
  lines.push(`total | ${Number(telemetry.total_duration_ms)} ms`);
  return lines.join("\n");
}

function finalVisibleText(turn: RouterTurnState, mode: "off" | "concise"): string {
  const finalResponse = turn.finalResponse;
  if (!finalResponse) {
    return "";
  }
  const base =
    finalResponse.status === "answer"
      ? String(finalResponse.answer ?? "")
      : finalResponse.status === "spawn_agent"
        ? finalResponse.statusMessage ||
          `Dispatching workflow for ${finalResponse.agentTarget ?? "agent_router_pending"}.`
        : finalResponse.statusMessage || finalResponse.error || "Semantic Router failed.";
  const footer = footerText(turn, mode);
  return footer ? `${base}\n\n${footer}` : base;
}

async function callOllamaStage(
  turn: RouterTurnState,
  stage: StageDefinition,
  options?: { forceAnswer?: boolean },
): Promise<{ text: string; tokensIn: number; tokensOut: number; durationMs: number }> {
  const startedAt = Date.now();
  const messages = buildStageMessages(turn, stage, options);
  const execution = stageExecutionSettings(turn, stage, options);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), execution.timeoutMs);
  try {
    const response = await fetch("http://ollama:11434/api/chat", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        model: stage.model,
        messages,
        stream: false,
        options: {
          num_predict: execution.maxTokens,
          temperature: execution.temperature,
        },
      }),
      signal: controller.signal,
    });
    if (!response.ok) {
      throw new Error(`ollama_stage_failed:${response.status}`);
    }
    const payload = asObject(await response.json());
    const message = asObject(payload.message);
    const text = typeof message.content === "string" ? message.content.trim() : "";
    return {
      text,
      tokensIn: Number(payload.prompt_eval_count ?? approximateTokens(turn.prompt)),
      tokensOut: Number(payload.eval_count ?? approximateTokens(text)),
      durationMs: Math.max(Date.now() - startedAt, 0),
    };
  } finally {
    clearTimeout(timer);
  }
}

async function callTogetherStage(
  turn: RouterTurnState,
  stage: StageDefinition,
  options?: { forceAnswer?: boolean },
): Promise<{ text: string; tokensIn: number; tokensOut: number; durationMs: number }> {
  const apiKey = String(process.env.TOGETHER_API_KEY ?? "").trim();
  if (!apiKey) {
    throw new Error("together_api_key_missing");
  }
  const startedAt = Date.now();
  const execution = stageExecutionSettings(turn, stage, options);
  const deadline = startedAt + execution.timeoutMs;
  let attempt = 0;
  let lastError: Error | null = null;

  while (attempt < 3) {
    attempt += 1;
    const remainingMs = Math.max(deadline - Date.now(), 1);
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), remainingMs);
    try {
      const response = await fetch("https://api.together.xyz/v1/chat/completions", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${apiKey}`,
        },
        body: JSON.stringify({
          model: stage.model,
          messages: buildStageMessages(turn, stage, options),
          stream: false,
          max_tokens: execution.maxTokens,
          temperature: execution.temperature,
        }),
        signal: controller.signal,
      });
      if (!response.ok) {
        const errorText = await response.text();
        const retryable = response.status === 429 || response.status >= 500;
        lastError = new Error(`together_stage_failed:${response.status}:${errorText}`);
        const backoffMs = attempt * 750;
        if (retryable && attempt < 3 && Date.now() + backoffMs < deadline) {
          await sleepMs(backoffMs);
          continue;
        }
        throw lastError;
      }
      const payload = asObject(await response.json());
      const choices = asArray<Record<string, unknown>>(payload.choices);
      const firstChoice = asObject(choices[0]);
      const message = asObject(firstChoice.message);
      const text = flattenContent(message.content);
      const usage = asObject(payload.usage);
      return {
        text,
        tokensIn: Number(usage.prompt_tokens ?? approximateTokens(turn.prompt)),
        tokensOut: Number(usage.completion_tokens ?? approximateTokens(text)),
        durationMs: Math.max(Date.now() - startedAt, 0),
      };
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      lastError = error instanceof Error ? error : new Error(message);
      const retryableNetworkError =
        message.includes("fetch failed") || message.includes("ECONNRESET") || message.includes("ENOTFOUND");
      const retryableAbort = message.includes("AbortError");
      const backoffMs = attempt * 750;
      if ((retryableNetworkError || retryableAbort) && attempt < 3 && Date.now() + backoffMs < deadline) {
        await sleepMs(backoffMs);
        continue;
      }
      throw lastError;
    } finally {
      clearTimeout(timer);
    }
  }

  throw lastError ?? new Error("together_stage_failed:retry_exhausted");
}

async function callStageModel(
  turn: RouterTurnState,
  stage: StageDefinition,
  options?: { forceAnswer?: boolean },
): Promise<{ text: string; tokensIn: number; tokensOut: number; durationMs: number }> {
  if (stage.provider === "ollama") {
    return callOllamaStage(turn, stage, options);
  }
  if (stage.provider === "together") {
    return callTogetherStage(turn, stage, options);
  }
  throw new Error(`unsupported_semantic_router_provider:${stage.provider}`);
}

function canAdvanceStage(turn: RouterTurnState, stageIndex: number, stage: StageDefinition): {
  nextStageId: string | null;
  allowedTransition: boolean;
  depthExhausted: boolean;
  overBudget: boolean;
} {
  const nextStageId = turn.config.stages[stageIndex + 1]?.id ?? null;
  return {
    nextStageId,
    allowedTransition: Boolean(nextStageId && stage.allowedNext.includes(nextStageId)),
    depthExhausted:
      stageIndex >= Math.min(turn.config.guardrails.maxEscalationDepth, turn.config.stages.length - 1) ||
      stageIndex + 1 >= turn.config.stages.length,
    overBudget:
      turn.config.guardrails.requestBudgetCap > 0 &&
      turn.budgetUsed >= turn.config.guardrails.requestBudgetCap,
  };
}

function recordStageTelemetry(
  turn: RouterTurnState,
  stage: StageDefinition,
  payload: {
    decision: string;
    reason: string;
    durationMs: number;
    tokensIn: number;
    tokensOut: number;
    rawContent?: string;
    error?: string;
    provider?: string;
    model?: string;
    runId?: string;
  },
): void {
  const timestamp = nowIso();
  turn.telemetry.push({
    runId: payload.runId,
    stage: stage.id,
    provider: payload.provider ?? stage.provider,
    model: payload.model ?? stage.model,
    decision: payload.decision,
    reason: payload.reason,
    durationMs: payload.durationMs,
    tokensIn: payload.tokensIn,
    tokensOut: payload.tokensOut,
    timestamp,
    rawContent: payload.rawContent,
  });
  appendLedgerEntry(turn.packet, {
    stage: stage.id,
    kind: "semantic_stage",
    provider: payload.provider ?? stage.provider,
    model: payload.model ?? stage.model,
    decision: payload.decision,
    reason: payload.reason,
    duration_ms: payload.durationMs,
    tokens_in: payload.tokensIn,
    tokens_out: payload.tokensOut,
    timestamp,
    error: payload.error,
  });
  updatePacketTelemetry(turn);
}

function coerceForcedAnswer(text: string, stageResult: StageResult): StageResult {
  if (stageResult.decision === "answer" && stageResult.answer?.trim()) {
    return stageResult;
  }
  const parsed = extractJsonFromText(text);
  const rendered =
    stageResult.answer?.trim() ||
    renderStructuredValue(parsed ?? { output: stripTurnMarker(text).trim() }, "answer");
  return {
    decision: "answer",
    reason: "forced_answer_at_terminal_stage",
    statusMessage: "Returning the best available response from the final reasoning stage.",
    answer: rendered.trim(),
  };
}

function shouldSkipForcedAnswerRetry(stageResult: StageResult): boolean {
  const agentTarget = String(stageResult.agentTarget ?? "").trim().toLowerCase();
  const combinedReason = `${stageResult.reason} ${stageResult.statusMessage}`.toLowerCase();
  if (
    agentTarget.includes("code") ||
    agentTarget.includes("python") ||
    agentTarget.includes("calculator")
  ) {
    return true;
  }
  return (
    combinedReason.includes("guarantee correctness") ||
    combinedReason.includes("deterministic") ||
    combinedReason.includes("exact simulation") ||
    combinedReason.includes("code execution")
  );
}

async function attemptForcedAnswerAtTerminalStage(
  turn: RouterTurnState,
  stage: StageDefinition,
  stageIndex: number,
): Promise<StageResult | null> {
  appendLedgerEntry(turn.packet, {
    stage: stage.id,
    kind: "semantic_stage_policy",
    decision: "answer",
    reason: "force_answer_retry",
    duration_ms: 0,
    tokens_in: 0,
    tokens_out: 0,
    timestamp: nowIso(),
  });
  try {
    const forcedCall = await callStageModel(turn, stage, { forceAnswer: true });
    const forcedStageResult = coerceForcedAnswer(
      forcedCall.text,
      resolveStageResult(forcedCall.text, stageIndex, turn.config),
    );
    recordStageTelemetry(turn, stage, {
      decision: forcedStageResult.decision,
      reason: forcedStageResult.reason,
      durationMs: forcedCall.durationMs,
      tokensIn: forcedCall.tokensIn,
      tokensOut: forcedCall.tokensOut,
      rawContent: forcedCall.text,
    });
    turn.budgetUsed += forcedCall.tokensIn + forcedCall.tokensOut;
    return forcedStageResult;
  } catch (error) {
    const errorMessage = error instanceof Error ? error.message : String(error);
    recordStageTelemetry(turn, stage, {
      decision: "failed",
      reason: "force_answer_retry_failed",
      durationMs: 0,
      tokensIn: 0,
      tokensOut: 0,
      rawContent: errorMessage,
      error: errorMessage,
    });
    return null;
  }
}

async function executeProviderRouteTurn(api: OpenClawPluginApi, turn: RouterTurnState): Promise<void> {
  if (turn.finalResponse) {
    return;
  }

  for (let stageIndex = 0; stageIndex < turn.config.stages.length; stageIndex += 1) {
    const stage = setCurrentStage(turn.packet, stageIndex, turn.config);
    if (stageIndex === 0) {
      const complexity = assessPromptComplexity(turn.prompt);
      if (complexity.score >= 2) {
        const { nextStageId } = canAdvanceStage(turn, stageIndex, stage);
        appendLedgerEntry(turn.packet, {
          stage: stage.id,
          kind: "semantic_stage_policy",
          decision: "escalate",
          reason: "local_complexity_bypass",
          override_signals: complexity.signals,
          override_score: complexity.score,
          duration_ms: 0,
          tokens_in: 0,
          tokens_out: 0,
          timestamp: nowIso(),
        });
        const routing = asObject(turn.packet.routing);
        routing.current_stage = stage.id;
        routing.next_stage = nextStageId;
        routing.stage_index = stageIndex + 1;
        turn.packet.routing = routing;
        continue;
      }
    }
    const stageStartedAt = Date.now();
    let stageCall;
    try {
      stageCall = await callStageModel(turn, stage);
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : String(error);
      const durationMs = Math.max(Date.now() - stageStartedAt, 0);
      recordStageTelemetry(turn, stage, {
        decision: "failed",
        reason: "stage_execution_failed",
        durationMs,
        tokensIn: 0,
        tokensOut: 0,
        rawContent: errorMessage,
        error: errorMessage,
      });
      const { nextStageId, allowedTransition, depthExhausted, overBudget } = canAdvanceStage(
        turn,
        stageIndex,
        stage,
      );
      if (turn.config.guardrails.forceAnswerAtMaxDepth && stage.allowedNext.length === 0) {
        const forcedStageResult = await attemptForcedAnswerAtTerminalStage(turn, stage, stageIndex);
        if (forcedStageResult?.answer?.trim()) {
          finalizeTurn(turn, {
            status: "answer",
            reason: forcedStageResult.reason,
            statusMessage: forcedStageResult.statusMessage,
            answer: forcedStageResult.answer,
          });
          await writeDebugArtifact(api, turn, Math.max(Date.now() - turn.startedAt, 0));
          return;
        }
      }
      if (allowedTransition && !depthExhausted && !overBudget) {
        appendLedgerEntry(turn.packet, {
          stage: stage.id,
          kind: "semantic_stage_recovery",
          decision: "escalate",
          reason: "stage_execution_failed_recovered",
          next_stage: nextStageId,
          duration_ms: 0,
          tokens_in: 0,
          tokens_out: 0,
          timestamp: nowIso(),
        });
        const routing = asObject(turn.packet.routing);
        routing.current_stage = stage.id;
        routing.next_stage = nextStageId;
        routing.stage_index = stageIndex + 1;
        turn.packet.routing = routing;
        continue;
      }
      finalizeTurn(turn, {
        status: "failed",
        reason: "stage_execution_failed",
        statusMessage: "A Semantic Router stage failed during provider execution and no further escalation path was available.",
        error: errorMessage,
      });
      await writeDebugArtifact(api, turn, Math.max(Date.now() - turn.startedAt, 0));
      return;
    }

    const stageResult = resolveStageResult(stageCall.text, stageIndex, turn.config);
    if (stageIndex === 0 && stageResult.decision === "answer") {
      const complexity = assessPromptComplexity(turn.prompt);
      const answerTokens = approximateTokens(stageResult.answer);
      if (complexity.score >= 2 && answerTokens >= 180) {
        appendLedgerEntry(turn.packet, {
          stage: stage.id,
          kind: "semantic_stage_policy",
          decision: "escalate",
          reason: "local_complexity_override",
          override_signals: complexity.signals,
          override_score: complexity.score,
          duration_ms: 0,
          tokens_in: 0,
          tokens_out: 0,
          timestamp: nowIso(),
        });
        stageResult.decision = "escalate";
        stageResult.reason = "local_complexity_override";
        stageResult.statusMessage =
          "Escalating because the request exceeds the local-first reasoning budget.";
        delete stageResult.answer;
      }
    }
    recordStageTelemetry(turn, stage, {
      decision: stageResult.decision,
      reason: stageResult.reason,
      durationMs: stageCall.durationMs,
      tokensIn: stageCall.tokensIn,
      tokensOut: stageCall.tokensOut,
      rawContent: stageCall.text,
    });
    turn.budgetUsed += stageCall.tokensIn + stageCall.tokensOut;
    const { nextStageId, allowedTransition, depthExhausted, overBudget } = canAdvanceStage(
      turn,
      stageIndex,
      stage,
    );

    if (stageResult.decision === "answer") {
      finalizeTurn(turn, {
        status: "answer",
        reason: stageResult.reason,
        statusMessage: stageResult.statusMessage,
        answer: stageResult.answer ?? "",
      });
      break;
    }

    if (stageResult.decision === "spawn_agent") {
      if (
        turn.config.guardrails.forceAnswerAtMaxDepth &&
        stage.allowedNext.length === 0 &&
        !shouldSkipForcedAnswerRetry(stageResult)
      ) {
        const forcedStageResult = await attemptForcedAnswerAtTerminalStage(turn, stage, stageIndex);
        if (forcedStageResult?.answer?.trim()) {
          finalizeTurn(turn, {
            status: "answer",
            reason: forcedStageResult.reason,
            statusMessage: forcedStageResult.statusMessage,
            answer: forcedStageResult.answer,
          });
          break;
        }
      }
      if (!(turn.config.guardrails.allowSpawnAgent && stage.allowSpawnAgent)) {
        finalizeTurn(turn, {
          status: "failed",
          reason: "spawn_agent_disallowed",
          statusMessage: "Workflow dispatch is not permitted for this request.",
          error: "spawn_agent_disallowed",
        });
      } else {
        finalizeTurn(turn, {
          status: "spawn_agent",
          reason: stageResult.reason,
          statusMessage: stageResult.statusMessage || "Dispatching workflow.",
          agentTarget: stageResult.agentTarget ?? "agent_router_pending",
        });
      }
      break;
    }

    if (stageResult.decision !== "escalate") {
      finalizeTurn(turn, {
        status: "failed",
        reason: "invalid_stage_decision",
        statusMessage: "Semantic Router returned an invalid stage decision.",
        error: "invalid_stage_decision",
      });
      break;
    }

    if (!allowedTransition || overBudget || depthExhausted) {
      if (turn.config.guardrails.forceAnswerAtMaxDepth) {
        if (!overBudget && depthExhausted) {
          const forcedStageResult = await attemptForcedAnswerAtTerminalStage(turn, stage, stageIndex);
          if (forcedStageResult?.answer?.trim()) {
            finalizeTurn(turn, {
              status: "answer",
              reason: forcedStageResult.reason,
              statusMessage: forcedStageResult.statusMessage,
              answer: forcedStageResult.answer,
            });
            break;
          }
        }
        finalizeTurn(turn, {
          status: "answer",
          reason: overBudget ? "budget_exhausted" : "escalation_limit_reached",
          statusMessage: "Returning the best available response within the configured reasoning budget.",
          answer:
            stageResult.answer?.trim() ||
            `I could not escalate further within the configured semantic router guardrails. (${overBudget ? "budget_exhausted" : "escalation_limit_reached"})`,
        });
      } else {
        finalizeTurn(turn, {
          status: "failed",
          reason: "escalation_blocked",
          statusMessage: "Escalation was blocked by router guardrails.",
          error: "escalation_blocked",
        });
      }
      break;
    }

    const routing = asObject(turn.packet.routing);
    routing.current_stage = stage.id;
    routing.next_stage = nextStageId;
    routing.stage_index = stageIndex + 1;
    turn.packet.routing = routing;
  }

  if (!turn.finalResponse) {
    finalizeTurn(turn, {
      status: "failed",
      reason: "semantic_router_missing_terminal_state",
      statusMessage: "Semantic Router did not reach a terminal state.",
      error: "semantic_router_missing_terminal_state",
    });
  }
  await writeDebugArtifact(api, turn, Math.max(Date.now() - turn.startedAt, 0));
}

async function executeNestedStage(
  api: OpenClawPluginApi,
  turn: RouterTurnState,
  stageIndex: number,
): Promise<void> {
  const config = turn.config;
  const stage = setCurrentStage(turn.packet, stageIndex, config);
  const syntheticSessionId = `${turn.rootSessionId}__${sanitizeFileName(stage.id)}__${randomUUID()}`;
  const syntheticRun: StageRunState = {
    rootSessionId: turn.rootSessionId,
    rootSessionKey: turn.rootSessionKey,
    sessionId: syntheticSessionId,
    stageIndex,
    stageId: stage.id,
    nested: true,
    startedAt: Date.now(),
    handled: false,
  };
  stageRunsBySessionId.set(syntheticSessionId, syntheticRun);
  turn.stageSessionIds.add(syntheticSessionId);

  const tmpRoot = path.join(resolvePreferredOpenClawTmpDir(), "openclaw-remram-runtime");
  await fs.mkdir(tmpRoot, { recursive: true });
  const sessionFile = path.join(tmpRoot, `${sanitizeFileName(syntheticSessionId)}.json`);
  const workspaceDir =
    typeof api.config?.agents?.defaults?.workspace === "string"
      ? api.config.agents.defaults.workspace
      : process.cwd();

  const runEmbeddedPiAgent = await loadRunEmbeddedPiAgent();
  const result = await runEmbeddedPiAgent({
    sessionId: syntheticSessionId,
    sessionFile,
    workspaceDir,
    config: api.config,
    prompt: turn.prompt,
    timeoutMs: config.guardrails.stageTimeoutMs,
    runId: `semantic-router-${randomUUID()}`,
    provider: stage.provider,
    model: stage.model,
    disableTools: true,
  });

  const currentTurn = turnsByRootSessionId.get(turn.rootSessionId);
  if (currentTurn?.finalResponse) {
    return;
  }

  const text = (result.payloads ?? [])
    .filter((payload) => !payload.isError && typeof payload.text === "string")
    .map((payload) => payload.text ?? "")
    .join("\n")
    .trim();
  if (!text) {
    finalizeTurn(turn, {
      status: "failed",
      reason: "nested_stage_empty_output",
      statusMessage: "A semantic router stage returned no output.",
      error: result.meta?.error?.message ?? "nested_stage_empty_output",
    });
    return;
  }

  const fallbackStageResult = resolveStageResult(text, stageIndex, config);
  if (!currentTurn?.telemetry.find((item) => item.stage === stage.id)) {
    recordStageTelemetry(turn, stage, {
      decision: fallbackStageResult.decision,
      reason: fallbackStageResult.reason,
      durationMs: Number(result.meta?.durationMs ?? 0),
      tokensIn: Number(result.meta?.agentMeta?.usage?.input ?? approximateTokens(turn.prompt)),
      tokensOut: Number(result.meta?.agentMeta?.usage?.output ?? approximateTokens(text)),
      rawContent: text,
    });
    turn.budgetUsed += Number(result.meta?.agentMeta?.usage?.total ?? 0);
  }

  if (!turn.finalResponse) {
    await handleStageDecision(api, turn, syntheticRun, fallbackStageResult);
  }
}

async function handleStageDecision(
  api: OpenClawPluginApi,
  turn: RouterTurnState,
  stageRun: StageRunState,
  stageResult: StageResult,
): Promise<void> {
  const config = turn.config;
  const stage = getStageByIndex(config, stageRun.stageIndex);
  const nextStageId = config.stages[stageRun.stageIndex + 1]?.id ?? null;
  const allowedTransition = Boolean(nextStageId && stage.allowedNext.includes(nextStageId));
  const depthExhausted =
    stageRun.stageIndex >= Math.min(config.guardrails.maxEscalationDepth, config.stages.length - 1) ||
    stageRun.stageIndex + 1 >= config.stages.length;
  const overBudget =
    config.guardrails.requestBudgetCap > 0 && turn.budgetUsed >= config.guardrails.requestBudgetCap;

  if (stageResult.decision === "answer") {
    finalizeTurn(turn, {
      status: "answer",
      reason: stageResult.reason,
      statusMessage: stageResult.statusMessage,
      answer: stageResult.answer ?? "",
    });
    return;
  }

  if (stageResult.decision === "spawn_agent") {
    if (!(config.guardrails.allowSpawnAgent && stage.allowSpawnAgent)) {
      finalizeTurn(turn, {
        status: "failed",
        reason: "spawn_agent_disallowed",
        statusMessage: "Workflow dispatch is not permitted for this request.",
        error: "spawn_agent_disallowed",
      });
      return;
    }
    finalizeTurn(turn, {
      status: "spawn_agent",
      reason: stageResult.reason,
      statusMessage: stageResult.statusMessage || "Dispatching workflow.",
      agentTarget: stageResult.agentTarget ?? "agent_router_pending",
    });
    return;
  }

  if (stageResult.decision !== "escalate") {
    finalizeTurn(turn, {
      status: "failed",
      reason: "invalid_stage_decision",
      statusMessage: "Semantic Router returned an invalid stage decision.",
      error: "invalid_stage_decision",
    });
    return;
  }

  if (!allowedTransition || overBudget || depthExhausted) {
    if (config.guardrails.forceAnswerAtMaxDepth) {
      finalizeTurn(turn, {
        status: "answer",
        reason: overBudget ? "budget_exhausted" : "escalation_limit_reached",
        statusMessage: "Returning the best available response within the configured reasoning budget.",
        answer: `I could not escalate further within the configured semantic router guardrails. (${overBudget ? "budget_exhausted" : "escalation_limit_reached"})`,
      });
      return;
    }
    finalizeTurn(turn, {
      status: "failed",
      reason: "escalation_blocked",
      statusMessage: "Escalation was blocked by router guardrails.",
      error: "escalation_blocked",
    });
    return;
  }

  await executeNestedStage(api, turn, stageRun.stageIndex + 1);
}

async function writeDebugArtifact(api: OpenClawPluginApi, turn: RouterTurnState, durationMs?: number): Promise<void> {
  const pluginConfig = resolvePluginConfig(api);
  await fs.mkdir(pluginConfig.debugDir, { recursive: true });
  const telemetry = asObject(computeTelemetry(turn));
  telemetry.total_duration_ms = Number(durationMs ?? telemetry.total_duration_ms ?? 0);
  const payload = {
    turn_id: turn.turnId,
    root_session_id: turn.rootSessionId,
    root_session_key: turn.rootSessionKey ?? null,
    prompt: turn.prompt,
    packet: turn.packet,
    telemetry,
    stages: turn.telemetry,
    final_response: turn.finalResponse ?? null,
    written_at: nowIso(),
  };
  const latestPath = path.join(pluginConfig.debugDir, `${sanitizeFileName(turn.rootSessionId)}.json`);
  const turnPath = path.join(
    pluginConfig.debugDir,
    `${sanitizeFileName(turn.rootSessionId)}__${sanitizeFileName(turn.turnId)}.json`,
  );
  const serialized = JSON.stringify(payload, null, 2) + "\n";
  await Promise.all([
    fs.writeFile(latestPath, serialized, "utf-8"),
    fs.writeFile(turnPath, serialized, "utf-8"),
  ]);
}

export function createSemanticRouterPlugin(api: OpenClawPluginApi) {
  api.registerHttpRoute({
    path: "/plugins/remram-runtime/router/v1/chat/completions",
    auth: "plugin",
    handler: async (req, res) => {
      const chunks: Buffer[] = [];
      for await (const chunk of req) {
        chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
      }
      const rawBody = Buffer.concat(chunks).toString("utf-8");
      api.logger.info(`[semantic-router] provider-route request: ${rawBody.length} bytes`);
      const parsedBody = asObject(JSON.parse(rawBody || "{}"));
      const turnId = extractTurnIdFromMessages(parsedBody.messages);
      const turn = resolveRouteTurn(turnId);
      if (!turn) {
        res.statusCode = 404;
        res.setHeader("Content-Type", "application/json; charset=utf-8");
        res.end(JSON.stringify({ error: { message: "semantic_router_turn_not_found" } }));
        return true;
      }
      await executeProviderRouteTurn(api, turn);
      res.statusCode = 200;
      res.setHeader("Content-Type", "text/event-stream; charset=utf-8");
      res.setHeader("Cache-Control", "no-cache");
      res.setHeader("Connection", "keep-alive");
      const completionId = `chatcmpl-${randomUUID()}`;
      const created = Math.floor(Date.now() / 1000);
      const responseText = finalVisibleText(turn, resolvePluginConfig(api).responseFooter);
      const responseTokens = approximateTokens(responseText);
      const writeChunk = (payload: Record<string, unknown> | "[DONE]") => {
        if (payload === "[DONE]") {
          res.write("data: [DONE]\n\n");
          return;
        }
        res.write(`data: ${JSON.stringify(payload)}\n\n`);
      };
      writeChunk({
        id: completionId,
        object: "chat.completion.chunk",
        created,
        model: "router",
        choices: [
          {
            index: 0,
            delta: {
              role: "assistant",
              content: responseText,
            },
            finish_reason: null,
          },
        ],
        usage: {
          prompt_tokens: Number(computeTelemetry(turn).total_tokens_in ?? 0),
          completion_tokens: responseTokens,
          total_tokens: Number(computeTelemetry(turn).total_tokens_in ?? 0) + responseTokens,
        },
      });
      writeChunk({
        id: completionId,
        object: "chat.completion.chunk",
        created,
        model: "router",
        choices: [
          {
            index: 0,
            delta: {},
            finish_reason: "stop",
          },
        ],
      });
      writeChunk("[DONE]");
      res.end();
      return true;
    },
  });

  api.on("before_model_resolve", async (event, ctx) => {
    const config = await loadSemanticRouterConfig(api);
    const sessionId = String(ctx.sessionId ?? "").trim();
    if (!sessionId) {
      return;
    }
    const turnId = randomUUID();
    const turn: RouterTurnState = {
      turnId,
      rootSessionId: sessionId,
      rootSessionKey: ctx.sessionKey,
      prompt: event.prompt,
      packet: hydrateRequestPacket(event.prompt, config, sessionId),
      config,
      startedAt: Date.now(),
      budgetUsed: 0,
      stageSessionIds: new Set([sessionId]),
      telemetry: [],
    };
    turnsByRootSessionId.set(sessionId, turn);
    turnsByTurnId.set(turnId, turn);
    if (ctx.sessionKey) {
      activeRootBySessionKey.set(ctx.sessionKey, sessionId);
    }
    setCurrentStage(turn.packet, 0, config);
    return {
      providerOverride: "remram-router",
      modelOverride: "router",
    };
  }, { priority: 100 });

  api.on("before_prompt_build", async (_event, ctx) => {
    const sessionId = String(ctx.sessionId ?? "").trim();
    if (!sessionId) {
      return;
    }
    const turn = turnsByRootSessionId.get(sessionId);
    if (!turn) {
      return;
    }
    return {
      prependSystemContext: `${turnMarker(turn.turnId)}\nDo not mention or reproduce this marker.`,
    };
  }, { priority: 100 });

  api.on("before_tool_call", (_event, ctx) => {
    const sessionId = String(ctx.sessionId ?? "").trim();
    if (!sessionId) {
      return;
    }
    if (stageRunsBySessionId.has(sessionId)) {
      return {
        block: true,
        blockReason: "Semantic Router stage runs do not allow tools.",
      };
    }
  }, { priority: 100 });

  api.on("llm_output", async (event, ctx) => {
    const sessionId = String(ctx.sessionId ?? "").trim();
    if (!sessionId) {
      return;
    }
    const stageRun = stageRunsBySessionId.get(sessionId);
    if (!stageRun || stageRun.handled) {
      return;
    }
    stageRun.handled = true;
    stageRun.runId = event.runId;

    const turn = turnsByRootSessionId.get(stageRun.rootSessionId);
    if (!turn) {
      return;
    }

    const text = event.assistantTexts.join("\n").trim();
    const stageResult = resolveStageResult(text, stageRun.stageIndex, turn.config);
    const durationMs = Math.max(Date.now() - stageRun.startedAt, 0);
    const tokensIn = Number(event.usage?.input ?? approximateTokens(turn.prompt));
    const tokensOut = Number(event.usage?.output ?? approximateTokens(text));
    const stage = getStageByIndex(turn.config, stageRun.stageIndex);

    recordStageTelemetry(turn, stage, {
      runId: event.runId,
      provider: event.provider,
      model: event.model,
      decision: stageResult.decision,
      reason: stageResult.reason,
      durationMs,
      tokensIn,
      tokensOut,
      rawContent: text,
    });
    turn.budgetUsed += Number(event.usage?.total ?? tokensIn + tokensOut);
    await handleStageDecision(api, turn, stageRun, stageResult);
  }, { priority: 100 });

  api.on("before_message_write", (event, ctx) => {
    const message = asObject(event.message);
    if (message.role !== "assistant") {
      return;
    }
    const sessionKey = String(ctx.sessionKey ?? "").trim();
    if (!sessionKey) {
      return;
    }
    const rootSessionId = activeRootBySessionKey.get(sessionKey);
    if (!rootSessionId) {
      return;
    }
    const turn = turnsByRootSessionId.get(rootSessionId);
    if (!turn?.finalResponse) {
      return;
    }
    const text = finalVisibleText(turn, resolvePluginConfig(api).responseFooter);
    if (!text) {
      return;
    }
    return {
      message: replaceAssistantMessageText(message, text),
    };
  }, { priority: 100 });

  api.on("agent_end", async (event, ctx) => {
    const sessionId = String(ctx.sessionId ?? "").trim();
    if (!sessionId) {
      return;
    }
    const turn = turnsByRootSessionId.get(sessionId);
    if (!turn) {
      return;
    }
    const response = asObject(turn.packet.response);
    const telemetry = asObject(response.telemetry);
    telemetry.total_duration_ms = Number(event.durationMs ?? telemetry.total_duration_ms ?? 0);
    response.telemetry = telemetry;
    turn.packet.response = response;
    await writeDebugArtifact(api, turn, Number(event.durationMs ?? 0));

    for (const ownedSessionId of turn.stageSessionIds) {
      stageRunsBySessionId.delete(ownedSessionId);
    }
    turnsByTurnId.delete(turn.turnId);
    turnsByRootSessionId.delete(turn.rootSessionId);
    if (turn.rootSessionKey) {
      activeRootBySessionKey.delete(turn.rootSessionKey);
    }
  }, { priority: 100 });
}
