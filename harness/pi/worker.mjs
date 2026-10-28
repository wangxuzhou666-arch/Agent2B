// twotrack exploration worker: one Pi agent-core run per process.
//
// The model may call only the tools the host declares in `start`. Every call it makes is sent to
// the host as a `tool_call` line and answered by the host's controlled pipeline (authorize,
// validate, grant, audit); the worker runs no business logic. Calls Pi refuses on its own (an
// unknown tool, arguments that fail the schema, a spent budget) never reach the host as calls;
// the worker reports each one as a `tool_blocked` event so the host can audit it.
//
// stdout carries protocol lines only (UTF-8 JSON, one object per line); stderr is diagnostics.
// The key, if any, arrives only through the environment variable `start.model.api_key_env` names,
// and is redacted from every line this process writes: as it is, JSON-escaped and URL-quoted
// (secretForms). Contract: README.md.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";
import { Agent } from "@earendil-works/pi-agent-core";
import { createModels, createProvider } from "@earendil-works/pi-ai";
import { openAICompletionsApi } from "@earendil-works/pi-ai/api/openai-completions.lazy";

const PROTOCOL = 1;
const HERE = dirname(fileURLToPath(import.meta.url));
const MAX_LINE = 1024 * 1024; // bytes per protocol line, either direction (the host checks too)
const MAX_ARGS = 256 * 1024; // serialized arguments of one tool call
const MAX_TEXT = 64 * 1024; // characters of the final answer
const MAX_DETAIL = 300; // characters of any diagnostic string
// A key shorter than this is not redacted, here or on the host (MIN_SECRET_CHARS in
// core/explore/pi.py): replacing a string that short would rewrite unrelated text wherever it
// occurs, and a value that short is a placeholder some endpoints accept ("EMPTY", "ollama"), not
// a credential.
const MIN_SECRET_CHARS = 8;
// The SDK under pi-ai announces the OS, the runtime and its own version on every request.
const FINGERPRINT = Object.fromEntries(
  ["arch", "lang", "os", "package-version", "runtime", "runtime-version", "retry-count", "timeout"]
    .map((h) => [`x-stainless-${h}`, null]),
);

let secrets = []; // every form of the key (secretForms), once `start` names its variable
let agent = null;
let started = false;
let stopOverride = null; // why the run ended, when the worker or the host ended it
const waiting = new Map(); // protocol id -> settle(reply), for the one forwarded call in flight

// Every way a key can appear in text, the same forms the host redacts (secret_forms in
// core/explore/pi.py): as it is; JSON-escaped, with non-ASCII as it is (JSON.stringify) or as
// \uXXXX (Python's json); and URL-quoted, as encodeURIComponent writes it or with !'()* escaped
// too (Python's quote). Longest first, so that a form that contains another is replaced whole.
// None for a key shorter than MIN_SECRET_CHARS.
function secretForms(secret) {
  if (typeof secret !== "string" || [...secret].length < MIN_SECRET_CHARS) return [];
  const json = JSON.stringify(secret).slice(1, -1);
  const ascii = json.replace(/[\u007f-￿]/g,
    (c) => `\\u${c.charCodeAt(0).toString(16).padStart(4, "0")}`);
  let uri = null;
  try {
    uri = encodeURIComponent(secret);
  } catch {
    // a lone surrogate, which no encoder writes: there is no URL form
  }
  const strict = uri?.replace(/[!'()*]/g, (c) => `%${c.charCodeAt(0).toString(16).toUpperCase()}`);
  return [...new Set([secret, json, ascii, uri, strict].filter(Boolean))]
    .sort((a, b) => b.length - a.length);
}

function redact(text) {
  let out = String(text ?? "");
  for (const secret of secrets) out = out.split(secret).join("[redacted]");
  return out;
}

function clip(text, max = MAX_DETAIL) {
  const out = redact(text);
  return out.length > max ? `${out.slice(0, max)}...` : out;
}

function line(msg) {
  // every string first, then the line: escaping a string that still held a form of the key
  // would write a form (escaped twice) that no longer matches
  const out = redact(JSON.stringify(msg, (_, v) => (typeof v === "string" ? redact(v) : v)));
  if (Buffer.byteLength(out) <= MAX_LINE) return out;
  return JSON.stringify({ type: "event", name: "dropped", what: String(msg.type) });
}

const send = (msg) => process.stdout.write(`${line(msg)}\n`);
const finish = (msg, code) => process.stdout.write(`${line(msg)}\n`, () => process.exit(code));
const diag = (text) => process.stderr.write(`${clip(text, 2000)}\n`);

function fatal(reason) {
  finish({ type: "final", text: "", stop_reason: "error", error: reason, http_status: null,
           usage: null, turns: 0, tool_calls: 0, forwarded: 0 }, 1);
}

// ---------------------------------------------------------------- versions

function installedVersion(name) {
  // the package.json of the copy Node resolves for this worker, i.e. the one it imports
  let dir;
  try {
    dir = dirname(fileURLToPath(import.meta.resolve(name)));
  } catch {
    dir = join(HERE, "node_modules", name);
  }
  for (;;) {
    try {
      const pkg = JSON.parse(readFileSync(join(dir, "package.json"), "utf8"));
      if (pkg.name === name) return pkg.version;
    } catch {
      // not this directory; keep walking up
    }
    const up = dirname(dir);
    if (up === dir) return null;
    dir = up;
  }
}

function hello() {
  const pins = JSON.parse(readFileSync(join(HERE, "package.json"), "utf8")).overrides ?? {};
  const versions = Object.fromEntries(Object.keys(pins).map((n) => [n, installedVersion(n)]));
  send({ type: "hello", protocol: PROTOCOL, versions, node: process.versions.node });
  const drift = Object.keys(pins).filter((n) => versions[n] !== pins[n]);
  if (drift.length > 0) {
    fatal(`installed versions differ from the pins: ${drift.join(", ")}`);
    return false;
  }
  return true;
}

// ---------------------------------------------------------------- model

function buildModel(m) {
  const model = {
    id: m.id, name: m.id, api: "openai-completions", provider: "twotrack", baseUrl: m.base_url,
    reasoning: false, input: ["text"], cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: m.context_window ?? 32768, maxTokens: m.max_tokens ?? 2048,
    compat: { supportsDeveloperRole: false, supportsReasoningEffort: false, supportsStore: false,
              ...(m.compat ?? {}) },
    headers: { "User-Agent": "twotrack-explorer", ...FINGERPRINT, ...(m.headers ?? {}) },
  };
  // pi-ai 1.2.0 refuses a provider without a key, so a keyless endpoint gets a placeholder
  const apiKey = (m.api_key_env && process.env[m.api_key_env]) || "unused";
  const models = createModels();
  models.setProvider(createProvider({
    id: "twotrack", name: "twotrack", baseUrl: m.base_url, models: [model],
    api: openAICompletionsApi(),
    auth: { apiKey: { name: "twotrack", resolve: async () => ({ auth: { apiKey } }) } },
  }));
  return { models, model };
}

function httpStatus(message) {
  const match = /^(\d{3})\b/.exec(String(message ?? ""));
  const status = match ? Number(match[1]) : null;
  return status !== null && status >= 100 && status <= 599 ? status : null;
}

// ---------------------------------------------------------------- run

async function run(start) {
  const b = start.budget;
  const m = start.model;
  const key = m.api_key_env ? process.env[m.api_key_env] : undefined;
  secrets = secretForms(key);
  const { models, model } = buildModel(m);
  const declared = new Set(start.tools.map((t) => t.name));
  let turn = 1; // the turn in progress, 1-based
  let attempts = 0; // every call the model asked for, refused ones included
  let forwarded = 0; // calls sent to the host
  let lastStop = null; // stop reason of the latest assistant message
  let current = null; // the call in progress; Pi runs them one at a time (sequential)

  function hostTool(spec) {
    return {
      name: spec.name, label: spec.name, description: spec.description,
      parameters: spec.parameters, executionMode: "sequential",
      async execute(_toolCallId, args, signal) {
        const call = current;
        const id = `t${call.seq}`;
        if (JSON.stringify(args ?? {}).length > MAX_ARGS) {
          call.reported = true;
          send({ type: "event", name: "tool_blocked", id, tool: spec.name, reason: "invalid_args",
                 detail: "arguments too large" });
          return { content: [{ type: "text", text: "invalid: arguments too large" }],
                   details: { ok: false, code: "invalid" }, isError: true };
        }
        call.forwarded = true;
        forwarded += 1;
        const reply = await new Promise((resolve) => {
          let timer = null;
          const settle = (r) => { clearTimeout(timer); waiting.delete(id); resolve(r); };
          if (signal?.aborted) return settle({ ok: false, code: "aborted", error: "run aborted" });
          signal?.addEventListener("abort",
            () => settle({ ok: false, code: "aborted", error: "run aborted" }), { once: true });
          timer = setTimeout(() => {
            send({ type: "event", name: "tool_timeout", id, tool: spec.name });
            settle({ ok: false, code: "timeout", error: `no answer in ${b.tool_timeout_ms} ms` });
          }, b.tool_timeout_ms);
          waiting.set(id, settle);
          send({ type: "tool_call", id, name: spec.name, args: args ?? {} });
        });
        const text = reply.ok === true ? String(reply.content ?? "")
          : `${reply.code ?? "error"}: ${reply.error ?? ""}`;
        return {
          content: [{ type: "text", text }],
          details: { ok: reply.ok === true, code: reply.code ?? null },
          ...(reply.ok === true ? {} : { isError: true }),
          ...(reply.terminate === true ? { terminate: true } : {}),
        };
      },
    };
  }

  agent = new Agent({
    initialState: { systemPrompt: start.system, model, thinkingLevel: "off",
                    tools: start.tools.map(hostTool) },
    // Agent forwards no request options: set the timeout and turn retries off here. Temperature
    // is left out on purpose (the endpoint's own default applies).
    streamFn: (mdl, ctx, opts) => models.streamSimple(mdl, ctx,
      { ...opts, timeoutMs: b.request_timeout_ms, maxRetries: 0 }),
    toolExecution: "sequential",
    // Fail closed: never forward a call whose result no later turn could read, nor one past the
    // call budget. A blocked call terminates the batch, so the run ends after this turn.
    beforeToolCall: async () => {
      const reason = turn >= b.max_turns ? "max_turns"
        : attempts > b.max_tool_calls ? "max_tool_calls" : null;
      if (reason === null) return undefined;
      current.budget = reason;
      stopOverride ??= reason;
      return { block: true, reason: `budget: ${reason.replaceAll("_", " ")} reached`,
               terminate: true };
    },
    finishTurn: async ({ toolResults }) => {
      const done = turn;
      turn += 1;
      if (attempts > b.max_tool_calls) {
        stopOverride ??= "max_tool_calls";
        return { action: "end" };
      }
      if (done >= b.max_turns && toolResults.length > 0) {
        stopOverride ??= "max_turns";
        return { action: "end" };
      }
      return undefined;
    },
  });

  agent.subscribe((e) => {
    if (e.type === "message_end" && e.message.role === "assistant") {
      lastStop = e.message.stopReason;
      const calls = e.message.content.filter((c) => c.type === "toolCall").map((c) => clip(c.name, 200));
      send({ type: "event", name: "assistant", turn, stop: lastStop, calls });
    } else if (e.type === "tool_execution_start") {
      attempts += 1;
      current = { seq: attempts, args: e.args, forwarded: false, reported: false, budget: null };
    } else if (e.type === "tool_execution_end" && current && !current.forwarded && !current.reported) {
      // Pi refused this call itself; it never reached the host
      const reason = current.budget ?? (stopOverride === "host_abort" || stopOverride === "host_gone"
        ? "aborted" : lastStop === "length" ? "truncated"
          : declared.has(e.toolName) ? "invalid_args" : "unknown_tool");
      const detail = (e.result?.content ?? []).filter((c) => c.type === "text").map((c) => c.text)
        .join(" ").split("\n\nReceived arguments")[0];
      const args = JSON.stringify(current.args ?? {}).length <= MAX_ARGS ? current.args : undefined;
      send({ type: "event", name: "tool_blocked", id: `t${current.seq}`, tool: clip(e.toolName, 200),
             reason, detail: clip(detail), ...(args === undefined ? {} : { args }) });
    }
  });

  const wall = setTimeout(() => { stopOverride ??= "wall_clock"; agent.abort(); }, b.wall_ms);
  try {
    await agent.prompt(start.task);
  } finally {
    clearTimeout(wall);
  }
  const assistant = agent.state.messages.filter((msg) => msg.role === "assistant");
  const last = assistant.at(-1);
  const usage = assistant.reduce((u, msg) => ({ input: u.input + (msg.usage?.input ?? 0),
                                                output: u.output + (msg.usage?.output ?? 0) }),
                                 { input: 0, output: 0 });
  const text = (last?.content ?? []).filter((c) => c.type === "text").map((c) => c.text).join("");
  const error = last?.stopReason === "error" ? last.errorMessage ?? "model call failed" : null;
  // a run that ends right after a tool batch was ended by a `terminate` answer
  const piStop = last?.stopReason === "toolUse" ? "terminated" : last?.stopReason;
  finish({ type: "final", text: clip(text, MAX_TEXT), stop_reason: stopOverride ?? piStop ?? "error",
           error: error === null ? null : clip(error), http_status: httpStatus(error), usage,
           turns: turn - 1, tool_calls: attempts, forwarded }, 0);
}

// ---------------------------------------------------------------- protocol

process.on("uncaughtException", (err) => { diag(err?.stack ?? err); fatal(clip(err?.message ?? err)); });
process.on("unhandledRejection", (err) => { diag(err?.stack ?? err); fatal(clip(err?.message ?? err)); });

if (hello()) {
  createInterface({ input: process.stdin, crlfDelay: Infinity }).on("line", (text) => {
    if (Buffer.byteLength(text) > MAX_LINE) return fatal("protocol: line too long");
    let msg;
    try {
      msg = JSON.parse(text);
    } catch {
      return fatal("protocol: a line is not JSON");
    }
    if (msg?.type === "start" && !started) {
      started = true;
      run(msg).catch((err) => { diag(err?.stack ?? err); fatal(clip(err?.message ?? err)); });
    } else if (msg?.type === "tool_result") {
      waiting.get(msg.id)?.(msg); // unknown or late ids are ignored
    } else if (msg?.type === "abort") {
      stopOverride ??= "host_abort";
      agent?.abort();
    } else {
      fatal(`protocol: unexpected ${String(msg?.type).slice(0, 40)} message`);
    }
  }).on("close", () => {
    if (!started) process.exit(0); // a version check, or a host that changed its mind
    stopOverride ??= "host_gone";
    agent?.abort();
  });
}
