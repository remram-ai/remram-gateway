import { promises as fs } from "node:fs";
import path from "node:path";

const SCHEMA_PATH = "/app/remram-gateway/schemas/remram-request-packet.schema.json";
const RUNTIME_ROOT = "/home/node/.openclaw";
const SESSION_DIR = path.join(RUNTIME_ROOT, "agents", "main", "sessions");
const OPENCLAW_CONFIG_PATH = path.join(RUNTIME_ROOT, "openclaw.json");
const DEFAULT_TOGETHER_BASE_URL = "https://api.together.xyz/v1/chat/completions";
const ESCALATION_SYSTEM_CONTEXT = `## Remram Escalation MVP

For every user request, first produce an internal decision object using the Remram Request Packet response subset:

\`\`\`json
{
  "response": {
    "status": "answer | escalate",
    "answer": "string when answering locally",
    "status_message": "short explanation"
  }
}
\`\`\`

Rules:
- \`response.status\` must be either \`answer\` or \`escalate\`.
- Prefer answering locally when confident.
- If the task is uncertain, deep, or difficult, set \`status\` to \`escalate\`.
- Do not refuse tasks. Escalate instead of refusing.
- After forming the decision, call the tool \`remram_escalate\` exactly once with:
  - \`user_request\`
  - \`decision\`
- Never show the raw decision JSON to the user.
- If the tool returns text content, output that text exactly as returned.
- If the tool returns a \`trace\` object, ensure the user sees the trace information as part of the final answer.
- Do not add any text before or after the tool-provided output.`;

type Decision = {
  response?: {
    status?: unknown;
    answer?: unknown;
    status_message?: unknown;
  };
};

type Telemetry = {
  local_model: string;
  local_input_tokens: number;
  local_output_tokens: number;
  local_duration_ms: number;
  escalated: boolean;
  final_model: string;
  final_input_tokens: number;
  final_output_tokens: number;
  final_latency_ms: number;
};

type ValidationResult = {
  ok: boolean;
  errors: string[];
};

type LocalTelemetry = {
  model: string;
  inputTokens: number;
  outputTokens: number;
  durationMs: number;
};

type ModelAnswer = {
  answer: string;
  model: string;
  inputTokens: number;
  outputTokens: number;
  latencyMs: number;
};

function asObject(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function asNumber(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function normalizeDecision(input: unknown): Decision {
  if (typeof input === "string") {
    return normalizeDecision(JSON.parse(input));
  }

  const raw = asObject(input);
  if (!raw) {
    return {};
  }

  if (asObject(raw.response)) {
    return raw as Decision;
  }

  if ("status" in raw || "answer" in raw || "status_message" in raw) {
    return { response: raw as Decision["response"] };
  }

  return raw as Decision;
}

async function loadResponseSchema() {
  const raw = await fs.readFile(SCHEMA_PATH, "utf8");
  const schema = JSON.parse(raw);
  return schema?.$defs?.response ?? {};
}

function buildManualValidator(schema: any) {
  const properties = asObject(schema?.properties) ?? {};
  const statusEnum = Array.isArray(properties.status?.enum)
    ? properties.status.enum.filter((value: unknown) => typeof value === "string")
    : [];

  return (value: unknown): ValidationResult => {
    const errors: string[] = [];
    const obj = asObject(value);

    if (!obj) {
      return { ok: false, errors: ["response must be an object"] };
    }

    if ("status" in obj) {
      if (typeof obj.status !== "string") {
        errors.push("response.status must be a string");
      } else if (statusEnum.length > 0 && !statusEnum.includes(obj.status)) {
        errors.push(`response.status must be one of: ${statusEnum.join(", ")}`);
      }
    }

    if ("answer" in obj && typeof obj.answer !== "string") {
      errors.push("response.answer must be a string when present");
    }

    if ("status_message" in obj && typeof obj.status_message !== "string") {
      errors.push("response.status_message must be a string when present");
    }

    if ("error" in obj && typeof obj.error !== "string") {
      errors.push("response.error must be a string when present");
    }

    return { ok: errors.length === 0, errors };
  };
}

async function compileResponseValidator() {
  const responseSchema = await loadResponseSchema();

  try {
    const ajvModule = await import("ajv/dist/2020");
    const Ajv2020 = ajvModule.default;
    const ajv = new Ajv2020({ allErrors: true, strict: false });
    const validate = ajv.compile(responseSchema);

    return (value: unknown): ValidationResult => {
      const ok = Boolean(validate(value));
      const errors = Array.isArray(validate.errors)
        ? validate.errors.map((error: any) => {
            const location = error.instancePath || "/response";
            return `${location}: ${error.message ?? "validation failed"}`;
          })
        : [];

      return { ok, errors };
    };
  } catch {
    return buildManualValidator(responseSchema);
  }
}

async function readJsonFile(filePath: string) {
  const raw = await fs.readFile(filePath, "utf8");
  return JSON.parse(raw);
}

async function listFiles(dirPath: string) {
  try {
    return await fs.readdir(dirPath, { withFileTypes: true });
  } catch {
    return [];
  }
}

async function findLikelySessionFile(context: any): Promise<string | null> {
  const sessionCandidates = [
    context?.sessionId,
    context?.session?.id,
    context?.conversationId,
    context?.run?.sessionId,
  ].filter((value: unknown): value is string => typeof value === "string" && value.length > 0);

  for (const sessionId of sessionCandidates) {
    const directPath = path.join(SESSION_DIR, `${sessionId}.jsonl`);
    try {
      await fs.access(directPath);
      return directPath;
    } catch {
      continue;
    }
  }

  const entries = await listFiles(SESSION_DIR);
  const candidates = await Promise.all(
    entries
      .filter((entry) => entry.isFile() && entry.name.endsWith(".jsonl"))
      .map(async (entry) => {
        const filePath = path.join(SESSION_DIR, entry.name);
        const stat = await fs.stat(filePath);
        return { filePath, mtimeMs: stat.mtimeMs };
      }),
  );

  candidates.sort((left, right) => right.mtimeMs - left.mtimeMs);
  return candidates[0]?.filePath ?? null;
}

async function readSessionLines(filePath: string) {
  const raw = await fs.readFile(filePath, "utf8");
  return raw
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      try {
        return JSON.parse(line);
      } catch {
        return null;
      }
    })
    .filter(Boolean);
}

function extractLocalTelemetryFromLine(entry: any): LocalTelemetry | null {
  const message = asObject(entry?.message);
  if (!message || message.role !== "assistant") {
    return null;
  }

  const usage = asObject(message.usage);
  const model = asString(message.model) || asString(entry?.model) || asString(message.provider);
  const durationMs =
    asNumber(message.total_duration) / 1_000_000 ||
    asNumber(message.totalDurationMs) ||
    asNumber(message.duration_ms) ||
    asNumber(entry?.duration_ms) ||
    0;

  if (!model && !usage) {
    return null;
  }

  return {
    model: model || "ollama/unknown",
    inputTokens: asNumber(usage?.input) || asNumber(message.prompt_eval_count),
    outputTokens: asNumber(usage?.output) || asNumber(message.eval_count),
    durationMs,
  };
}

async function extractLocalTelemetry(context: any): Promise<LocalTelemetry> {
  const contextModel =
    asString(context?.model) ||
    asString(context?.run?.model) ||
    asString(context?.assistantModel) ||
    asString(context?.agent?.model);
  const contextUsage = asObject(context?.usage) ?? asObject(context?.run?.usage);
  const contextDurationMs =
    asNumber(context?.total_duration) / 1_000_000 ||
    asNumber(context?.run?.total_duration) / 1_000_000 ||
    asNumber(context?.duration_ms) ||
    asNumber(context?.run?.duration_ms) ||
    0;

  if (contextModel || contextUsage || contextDurationMs) {
    return {
      model: contextModel || "ollama/unknown",
      inputTokens: asNumber(contextUsage?.input) || asNumber(context?.prompt_eval_count),
      outputTokens: asNumber(contextUsage?.output) || asNumber(context?.eval_count),
      durationMs: contextDurationMs,
    };
  }

  const sessionFile = await findLikelySessionFile(context);
  if (!sessionFile) {
    return {
      model: "ollama/unknown",
      inputTokens: 0,
      outputTokens: 0,
      durationMs: 0,
    };
  }

  const entries = await readSessionLines(sessionFile);
  for (let index = entries.length - 1; index >= 0; index -= 1) {
    const telemetry = extractLocalTelemetryFromLine(entries[index]);
    if (telemetry) {
      return telemetry;
    }
  }

  return {
    model: "ollama/unknown",
    inputTokens: 0,
    outputTokens: 0,
    durationMs: 0,
  };
}

async function resolveFallbackModelRef() {
  try {
    const config = await readJsonFile(OPENCLAW_CONFIG_PATH);
    const fallbackRef = config?.agents?.defaults?.model?.fallbacks?.[0];
    if (typeof fallbackRef === "string" && fallbackRef.trim().length > 0) {
      return fallbackRef.trim();
    }
  } catch {
    // Fall through to environment defaults.
  }

  const envModel = process.env.CLOUD_REASONING_MODEL || "deepseek-ai/DeepSeek-R1";
  return `together/${envModel}`;
}

function togetherModelId(modelRef: string) {
  return modelRef.replace(/^together\//, "");
}

function coerceTextContent(content: unknown): string {
  if (typeof content === "string") {
    return content.trim();
  }

  if (Array.isArray(content)) {
    return content
      .map((item) => {
        const obj = asObject(item);
        return obj ? asString(obj.text) : "";
      })
      .join("\n")
      .trim();
  }

  return "";
}

async function callTogetherFallback(userRequest: string, reason: string): Promise<ModelAnswer> {
  const fallbackRef = await resolveFallbackModelRef();
  const apiKey = process.env.TOGETHER_API_KEY || "";
  const startedAt = Date.now();

  if (!apiKey.trim()) {
    throw new Error("TOGETHER_API_KEY is empty");
  }

  const response = await fetch(process.env.TOGETHER_BASE_URL || DEFAULT_TOGETHER_BASE_URL, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${apiKey}`,
    },
    body: JSON.stringify({
      model: togetherModelId(fallbackRef),
      temperature: 0.2,
      messages: [
        {
          role: "system",
          content:
            "You are the Remram escalation reasoning model. Provide the strongest direct answer to the user request. Do not mention escalation, tooling, or internal routing.",
        },
        {
          role: "user",
          content: reason
            ? `Escalation context: ${reason}\n\nUser request: ${userRequest}`
            : userRequest,
        },
      ],
    }),
  });

  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = asString(payload?.error?.message) || response.statusText || "Together API request failed";
    throw new Error(message);
  }

  const answer = coerceTextContent(payload?.choices?.[0]?.message?.content);
  if (!answer) {
    throw new Error("Together API returned an empty answer");
  }

  return {
    answer,
    model: `together/${asString(payload?.model) || togetherModelId(fallbackRef)}`,
    inputTokens: asNumber(payload?.usage?.prompt_tokens),
    outputTokens: asNumber(payload?.usage?.completion_tokens),
    latencyMs: Date.now() - startedAt,
  };
}

async function callBestEffortLocal(userRequest: string): Promise<ModelAnswer> {
  const model = process.env.LOCAL_ROUTING_MODEL || "qwen3-moltbox";
  const ollamaBaseUrl = process.env.OLLAMA_BASE_URL || "http://ollama:11434";
  const startedAt = Date.now();

  const response = await fetch(`${ollamaBaseUrl.replace(/\/$/, "")}/api/chat`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
    },
    body: JSON.stringify({
      model,
      stream: false,
      messages: [
        {
          role: "system",
          content: "Provide the best direct answer you can. Do not mention internal failures or escalation.",
        },
        {
          role: "user",
          content: userRequest,
        },
      ],
    }),
  });

  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = asString(payload?.error) || response.statusText || "Ollama request failed";
    throw new Error(message);
  }

  const answer = asString(payload?.message?.content).trim();
  if (!answer) {
    throw new Error("Ollama returned an empty answer");
  }

  return {
    answer,
    model: `ollama/${asString(payload?.model) || model}`,
    inputTokens: asNumber(payload?.prompt_eval_count),
    outputTokens: asNumber(payload?.eval_count),
    latencyMs: asNumber(payload?.total_duration) / 1_000_000 || Date.now() - startedAt,
  };
}

function buildFooter(telemetry: Telemetry) {
  const lines = [
    "---",
    "Trace",
    `Local model: ${telemetry.local_model}`,
    `Local tokens: ${telemetry.local_input_tokens} -> ${telemetry.local_output_tokens}`,
  ];

  if (telemetry.local_duration_ms > 0) {
    lines.push(`Local duration: ${(telemetry.local_duration_ms / 1000).toFixed(2)}s`);
  }

  lines.push(`Escalation: ${telemetry.escalated ? "yes" : "no"}`);
  lines.push(`Final model: ${telemetry.final_model}`);
  lines.push(`Final tokens: ${telemetry.final_input_tokens} -> ${telemetry.final_output_tokens}`);

  if (telemetry.final_latency_ms > 0) {
    lines.push(`Final latency: ${(telemetry.final_latency_ms / 1000).toFixed(2)}s`);
  }

  lines.push("-----------------------");
  return lines.join("\n");
}

function buildResult(finalAnswer: string, telemetry: Telemetry) {
  const footer = buildFooter(telemetry);
  const text = `${finalAnswer.trim()}\n\n${footer}`;
  const trace = {
    local_model: telemetry.local_model,
    local_tokens: {
      input: telemetry.local_input_tokens,
      output: telemetry.local_output_tokens,
    },
    local_duration_ms: telemetry.local_duration_ms,
    escalated: telemetry.escalated,
    final_model: telemetry.final_model,
    final_tokens: {
      input: telemetry.final_input_tokens,
      output: telemetry.final_output_tokens,
    },
    final_latency_ms: telemetry.final_latency_ms,
  };
  return {
    content: [{ type: "text", text }],
    telemetry,
    trace,
    details: {
      final_answer: finalAnswer.trim(),
      footer,
      trace,
    },
  };
}

function resolveLocalAnswer(decision: Decision) {
  const response = asObject(decision.response);
  const status = asString(response?.status).trim();
  const answer = asString(response?.answer).trim();
  const statusMessage = asString(response?.status_message).trim();

  return { status, answer, statusMessage };
}

function validationFailureReason(errors: string[]) {
  return errors.length > 0 ? errors.join("; ") : "Decision parsing or validation failed.";
}

export default function register(api: any) {
  api.on(
    "before_prompt_build",
    () => ({
      appendSystemContext: ESCALATION_SYSTEM_CONTEXT,
    }),
    { priority: 100 },
  );

  api.registerTool({
    name: "remram_escalate",
    description:
      "Evaluate a Remram response decision, return the local answer when valid, or escalate to the configured fallback reasoning model.",
    parameters: {
      type: "object",
      additionalProperties: false,
      required: ["user_request", "decision"],
      properties: {
        user_request: { type: "string" },
        decision: {},
      },
    },
    async execute(
      _toolCallId: string,
      { user_request, decision }: { user_request: string; decision: unknown },
      context?: any,
    ) {
      const localTelemetry = await extractLocalTelemetry(context);
      const validateResponse = await compileResponseValidator();

      let parsedDecision: Decision = {};
      let validation: ValidationResult = { ok: false, errors: ["Decision not parsed."] };
      let decisionState = {
        status: "",
        answer: "",
        statusMessage: "",
      };

      try {
        parsedDecision = normalizeDecision(decision);
        validation = validateResponse(parsedDecision.response ?? {});
        decisionState = resolveLocalAnswer(parsedDecision);
      } catch (error) {
        validation = {
          ok: false,
          errors: [error instanceof Error ? error.message : "Decision parsing failed."],
        };
      }

      const validLocalAnswer =
        validation.ok && decisionState.status === "answer" && decisionState.answer.length > 0;

      if (validLocalAnswer) {
        return buildResult(decisionState.answer, {
          local_model: localTelemetry.model,
          local_input_tokens: localTelemetry.inputTokens,
          local_output_tokens: localTelemetry.outputTokens,
          local_duration_ms: localTelemetry.durationMs,
          escalated: false,
          final_model: localTelemetry.model,
          final_input_tokens: localTelemetry.inputTokens,
          final_output_tokens: localTelemetry.outputTokens,
          final_latency_ms: 0,
        });
      }

      const escalationReason =
        decisionState.status === "escalate"
          ? decisionState.statusMessage || "Task requires deeper reasoning."
          : decisionState.status === "answer" && !decisionState.answer
            ? "Local model chose answer but returned no answer text."
            : validationFailureReason(validation.errors);

      try {
        const fallback = await callTogetherFallback(user_request, escalationReason);
        return buildResult(fallback.answer, {
          local_model: localTelemetry.model,
          local_input_tokens: localTelemetry.inputTokens,
          local_output_tokens: localTelemetry.outputTokens,
          local_duration_ms: localTelemetry.durationMs,
          escalated: true,
          final_model: fallback.model,
          final_input_tokens: fallback.inputTokens,
          final_output_tokens: fallback.outputTokens,
          final_latency_ms: fallback.latencyMs,
        });
      } catch {
        if (decisionState.answer.length > 0) {
          return buildResult(decisionState.answer, {
            local_model: localTelemetry.model,
            local_input_tokens: localTelemetry.inputTokens,
            local_output_tokens: localTelemetry.outputTokens,
            local_duration_ms: localTelemetry.durationMs,
            escalated: true,
            final_model: localTelemetry.model,
            final_input_tokens: localTelemetry.inputTokens,
            final_output_tokens: localTelemetry.outputTokens,
            final_latency_ms: 0,
          });
        }

        try {
          const localFallback = await callBestEffortLocal(user_request);
          return buildResult(localFallback.answer, {
            local_model: localTelemetry.model,
            local_input_tokens: localTelemetry.inputTokens,
            local_output_tokens: localTelemetry.outputTokens,
            local_duration_ms: localTelemetry.durationMs,
            escalated: true,
            final_model: localFallback.model,
            final_input_tokens: localFallback.inputTokens,
            final_output_tokens: localFallback.outputTokens,
            final_latency_ms: localFallback.latencyMs,
          });
        } catch {
          const finalAnswer =
            decisionState.answer ||
            "I couldn't complete the full escalation path, but the request should be retried because the runtime could not reach either reasoning provider.";
          return buildResult(finalAnswer, {
            local_model: localTelemetry.model,
            local_input_tokens: localTelemetry.inputTokens,
            local_output_tokens: localTelemetry.outputTokens,
            local_duration_ms: localTelemetry.durationMs,
            escalated: true,
            final_model: localTelemetry.model,
            final_input_tokens: localTelemetry.inputTokens,
            final_output_tokens: localTelemetry.outputTokens,
            final_latency_ms: 0,
          });
        }
      }
    },
  });
}
