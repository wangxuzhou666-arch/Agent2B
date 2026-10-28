import { createTranslator } from "next-intl";
import { describe, expect, it } from "vitest";

import en from "../../messages/en.json";
import zh from "../../messages/zh.json";
import type { TraceStep } from "./api";
import { activeLabel, describeStep, describeTrace, exploreStop, modelFailure, routeSourceOf, type TraceLine } from "./trace";

const step = (s: Partial<TraceStep> & Pick<TraceStep, "step">): TraceStep => ({
  status: "ok",
  source: null,
  facts: {},
  duration_ms: 0,
  ...s,
});

const CATALOGS = { en, zh } as const;

/** The lines as a reader sees them, in either language. */
function read(locale: keyof typeof CATALOGS, lines: readonly TraceLine[]): string[] {
  const t = createTranslator({ locale, messages: CATALOGS[locale], namespace: "trace.step" });
  return lines.map((line) => t(line.key, line.values));
}

describe("describeStep", () => {
  it("labels each routing arm from the understand step and keeps its source", () => {
    const rule = describeStep(
      step({ step: "understand", source: "rule", facts: { kind: "tool", target: "late_shipments", reason: "keyword match: late" }, duration_ms: 1.2 }),
      1,
    );
    expect(rule).toEqual([
      {
        id: "1",
        key: "routeRule",
        values: { target: "late_shipments" },
        tone: "ok",
        source: "rule",
        durationMs: 1.2,
        note: "keyword match: late",
      },
    ]);
    expect(describeStep(step({ step: "understand", source: "semantic", facts: { kind: "tool", target: "low_stock" } }), 0)[0]?.key).toBe("routeSemantic");
    expect(describeStep(step({ step: "understand", source: "llm", facts: { kind: "tool", target: "po_status" } }), 0)[0]?.key).toBe("routeLlm");
    expect(describeStep(step({ step: "understand", source: "explicit", facts: { kind: "tool", target: "po_status" } }), 0)[0]?.key).toBe("routeExplicit");
    expect(describeStep(step({ step: "understand", source: "semantic", facts: { kind: "chat", target: null } }), 0)[0]?.key).toBe("routeChat");
  });

  it("describes clarify decisions by their options", () => {
    const many = describeStep(step({ step: "understand", source: "rule", facts: { kind: "clarify", options: ["a", "b"] } }), 0)[0];
    expect(many).toMatchObject({ key: "routeClarify", values: { count: 2 }, tone: "warn" });
    const one = describeStep(step({ step: "understand", source: "llm_confirm", facts: { kind: "clarify", options: ["create_po"] } }), 0)[0];
    expect(one).toMatchObject({ key: "routeLlmConfirm", values: { target: "create_po" } });
  });

  it("explains the gate: passed, held for approval, blocked by role", () => {
    expect(describeStep(step({ step: "gate", facts: { target: "x", confirmation: "not_required" } }), 0)[0]?.key).toBe("gateOk");
    expect(describeStep(step({ step: "gate", facts: { confirmation: "consumed" } }), 0)[0]?.key).toBe("gateApprovalUsed");
    expect(describeStep(step({ step: "gate", status: "blocked", facts: { stage: "confirmation", grant_id: "g" } }), 0)[0]).toMatchObject({ key: "gateNeedsApproval", tone: "warn" });
    expect(describeStep(step({ step: "gate", status: "blocked", facts: { stage: "authz" } }), 0)[0]?.key).toBe("gateAuthz");
    expect(describeStep(step({ step: "gate", status: "blocked", facts: { stage: "precondition", unmet: ["has_suppliers"] } }), 0)[0]).toMatchObject({ key: "gatePrecondition", values: { unmet: "has_suppliers" } });
  });

  it("counts rows, and marks an empty result as a result rather than an error", () => {
    expect(describeStep(step({ step: "execute", facts: { row_count: 12 }, duration_ms: 4 }), 0)[0]).toMatchObject({ key: "executeRows", values: { count: 12 }, tone: "ok", durationMs: 4 });
    expect(describeStep(step({ step: "execute", facts: { row_count: 0 } }), 0)[0]?.tone).toBe("empty");
    expect(describeStep(step({ step: "execute", status: "error", facts: { error: "ValueError", rolled_back: true } }), 0)[0]?.key).toBe("executeRolledBack");
  });

  it("reports the numeric lock only where it ran (model narration)", () => {
    expect(describeStep(step({ step: "narrate", facts: { source: "template", stripped: 0 } }), 3).map((l) => l.key)).toEqual(["narrateTemplate"]);
    const model = describeStep(step({ step: "narrate", facts: { source: "model", stripped: 2 } }), 3);
    expect(model.map((l) => l.key)).toEqual(["narrateModel", "numericLockStripped"]);
    expect(model[1]).toMatchObject({ id: "3-lock", values: { count: 2 }, tone: "warn", durationMs: null });
  });

  it("labels narration by the zero-key mock provider as template text, whichever path it took", () => {
    // what the server sends with no model key: the mock answers on the model's path
    const mock = describeStep(step({ step: "narrate", facts: { source: "model", stripped: 0, provider: "mock", mock: true } }), 3);
    expect(mock.map((l) => l.key)).toEqual(["narrateMock", "numericLockClean"]);
    const template = describeStep(step({ step: "narrate", facts: { source: "template", stripped: 0, provider: "mock", mock: true } }), 3);
    expect(template.map((l) => l.key)).toEqual(["narrateMock"]);
    // a real model says so; `mock` must be exactly true to count
    const real = describeStep(step({ step: "narrate", facts: { source: "model", stripped: 0, provider: "openai_compat", mock: false } }), 3);
    expect(real.map((l) => l.key)).toEqual(["narrateModel", "numericLockClean"]);
    expect(describeStep(step({ step: "narrate", facts: { source: "model", mock: "true" } }), 3)[0]?.key).toBe("narrateModel");
  });

  it("surfaces egress redactions on respond and keeps unknown steps verbatim", () => {
    expect(describeStep(step({ step: "respond", facts: { rows: 3, redacted: 2 } }), 0).map((l) => l.key)).toEqual(["respondOk", "egressRedacted"]);
    expect(describeStep(step({ step: "record", status: "skipped" }), 0)[0]).toMatchObject({ key: "unknown", values: { step: "record" }, tone: "neutral" });
  });
});

describe("narration: three states a reader can tell apart", () => {
  const failed = { call: "narrate", error: "BadRequestError", http_status: 400 };
  const narrate = (facts: Record<string, unknown>) => describeStep(step({ step: "narrate", facts }), 3);

  it("no model configured: the zero-key mock's template, never a failing model", () => {
    const mock = narrate({ source: "model", stripped: 0, provider: "mock", mock: true });
    expect(read("en", mock)).toEqual(["Template narration (no model configured)", "Numeric lock: every number checked against the result"]);
    expect(read("zh", mock)[0]).toBe("模板叙述（未配置模型）");
    // no model was configured to fail; the respond step lists any failed call (a failed scrub)
    expect(narrate({ source: "template", stripped: 0, provider: "mock", mock: true, model_error: failed }).map((l) => l.key)).toEqual(["narrateMock"]);
  });

  it("a configured model whose call failed: the error's class and HTTP status, and the template that stood in", () => {
    const lines = narrate({ source: "template", stripped: 0, provider: "openai_compat", mock: false, model_error: failed });
    // one line, amber; no numeric-lock line: a template cites no unverified number
    expect(lines).toEqual([
      { id: "3", key: "narrateFailed", values: { error: "BadRequestError", status: "400" }, tone: "warn", source: null, durationMs: null },
    ]);
    expect(read("en", lines)).toEqual(["Model call failed (BadRequestError, HTTP 400); narrated from a fixed template"]);
    expect(read("zh", lines)).toEqual(["模型调用失败（BadRequestError，HTTP 400），改由固定模板叙述"]);
    // a timeout or an unparseable reply has no HTTP status
    const timeout = narrate({ source: "template", mock: false, model_error: { call: "narrate", error: "APITimeoutError", http_status: null } });
    expect(read("en", timeout)).toEqual(["Model call failed (APITimeoutError); narrated from a fixed template"]);
    expect(read("zh", timeout)).toEqual(["模型调用失败（APITimeoutError），改由固定模板叙述"]);
  });

  it("the model narrated: its own label and the numeric lock, whatever else the facts hold", () => {
    const model = narrate({ source: "model", stripped: 0, provider: "openai_compat", mock: false });
    expect(read("en", model)).toEqual(["Narrated by the language model", "Numeric lock: every number checked against the result"]);
    expect(read("zh", model)[0]).toBe("由语言模型叙述");
    // the server records a failure only under template text: model text stays the model's
    expect(narrate({ source: "model", stripped: 0, mock: false, model_error: failed }).map((l) => l.key)).toEqual(["narrateModel", "numericLockClean"]);
    // and template text with no failure behind it (the lock stripped every sentence) stays a plain template
    expect(narrate({ source: "template", stripped: 2, mock: false }).map((l) => l.key)).toEqual(["narrateTemplate", "numericLockStripped"]);
  });

  it("lists every failed model call on the respond step, routing included, one line each", () => {
    const model_errors = [
      { call: "route", error: "BadRequestError", http_status: 400 },
      { call: "narrate", error: "APITimeoutError", http_status: null },
    ];
    const lines = describeStep(step({ step: "respond", facts: { answer_source: "template", rows: 19, redacted: 1, model_errors } }), 5);
    expect(lines.map((l) => [l.id, l.key, l.tone, l.durationMs])).toEqual([
      ["5", "respondOk", "ok", null],
      ["5-model-0", "modelCallFailed", "warn", null],
      ["5-model-1", "modelCallFailed", "warn", null],
      ["5-egress", "egressRedacted", "neutral", null],
    ]);
    expect(read("en", lines).slice(1, 3)).toEqual(["Model call failed: routing (BadRequestError, HTTP 400)", "Model call failed: narration (APITimeoutError)"]);
    expect(read("zh", lines).slice(1, 3)).toEqual(["模型调用失败：路由（BadRequestError，HTTP 400）", "模型调用失败：叙述（APITimeoutError）"]);
    // a run that finished with an error lists them too; none failed, no line
    const agent = [{ call: "agent_step", error: "APIConnectionError" }];
    expect(read("en", describeStep(step({ step: "respond", status: "error", facts: { model_errors: agent } }), 0))).toEqual([
      "Finished with an error",
      "Model call failed: agent step (APIConnectionError)",
    ]);
    expect(describeStep(step({ step: "respond", facts: { rows: 3, model_errors: [] } }), 0).map((l) => l.key)).toEqual(["respondOk"]);
  });

  it("shows names and status codes only, never an error's text", () => {
    const leaky = { call: "narrate", error: "temperature 0.2 is below 1 for 'which SKUs are low'", http_status: 4000 };
    const lines = narrate({ source: "template", mock: false, model_error: leaky });
    expect(lines[0]).toMatchObject({ key: "narrateFailed", values: { error: "?", status: "none" }, tone: "warn" });
    expect(read("en", lines)).toEqual(["Model call failed (?); narrated from a fixed template"]);
    // a call this build does not know reads verbatim; a value that is not a failure record is skipped
    const odd = [{ call: "rerank", error: "ServiceUnavailableError", http_status: 503 }, "BadRequestError", null];
    expect(read("en", describeStep(step({ step: "respond", facts: { rows: 0, model_errors: odd } }), 0))).toEqual([
      "Answer assembled (0 rows)",
      "Model call failed: rerank (ServiceUnavailableError, HTTP 503)",
    ]);
    expect(narrate({ source: "template", mock: false, model_error: "BadRequestError" }).map((l) => l.key)).toEqual(["narrateTemplate"]);
  });
});

describe("exploration: the hand-over, the run's start and a refusal", () => {
  const evidence = (semantic: Record<string, unknown>) => ({
    reason: "intent_not_covered",
    to: "open_task",
    evidence: { rule: null, llm: null, arms_silent: true, semantic, candidates: 4 },
  });
  const handover = (facts: Record<string, unknown>) => describeStep(step({ step: "handover", source: "agent", facts }), 2);

  it("labels the router's hand-over, with the closest semantic match when the evidence names one", () => {
    const none = handover(evidence({ best: null, score: 0, threshold: 0.5, below: true }));
    expect(none).toEqual([{ id: "2", key: "handover", values: {}, tone: "ok", source: "agent", durationMs: null }]);
    expect(read("en", none)).toEqual(["Handed over to exploration: no report covers the question"]);
    expect(read("zh", none)).toEqual(["移交探索：没有报表能回答这个问题"]);

    const closest = handover(evidence({ best: "low_stock", score: 0.312, threshold: 0.5, below: true }));
    expect(closest[0]).toMatchObject({ key: "handoverClosest", values: { target: "low_stock", score: 0.312, threshold: 0.5 } });
    expect(read("en", closest)).toEqual([
      "Handed over to exploration: no report covers the question (closest semantic match low_stock: 0.312, threshold 0.5)",
    ]);
    expect(read("zh", closest)).toEqual(["移交探索：没有报表能回答这个问题（最接近的语义匹配 low_stock：0.312，阈值 0.5）"]);

    // a reason this build does not know reads verbatim; evidence it cannot read is left out
    expect(read("en", handover({ reason: "operator_choice" }))).toEqual(["Handed over to exploration (operator_choice)"]);
    expect(handover({ reason: "intent_not_covered", evidence: "semantic" })[0]?.key).toBe("handover");
  });

  it("labels the start of a run: its runtime and every tool it may call", () => {
    const start = describeStep(step({ step: "execute", facts: { loop: "start", runtime: "pi", exposed: ["low_stock", "po_status"] } }), 3);
    expect(start[0]).toMatchObject({ key: "executeAgentStart", values: { runtime: "pi", count: 2, tools: "low_stock, po_status" }, tone: "ok" });
    expect(read("en", start)).toEqual(["Exploration started (Pi) with 2 tools: low_stock, po_status"]);
    expect(read("zh", start)).toEqual(["开始探索（Pi），可调用 2 个工具：low_stock, po_status"]);

    const bare = describeStep(step({ step: "execute", facts: { loop: "start", runtime: "builtin", exposed: [] } }), 3);
    expect(read("en", bare)).toEqual(["Exploration started (built-in loop) with no tools"]);
    expect(read("zh", bare)).toEqual(["开始探索（内置循环），没有可调用的工具"]);
    expect(read("en", describeStep(step({ step: "execute", facts: { loop: "start", runtime: "builtin", exposed: ["low_stock"] } }), 3))).toEqual([
      "Exploration started (built-in loop) with 1 tool: low_stock",
    ]);
  });

  it("labels a refused hand-over by why the guard refused, not as a failed agent turn", () => {
    const refused = (facts: Record<string, unknown>) =>
      describeStep(
        step({
          step: "execute",
          status: "error",
          source: "agent",
          facts: { loop: "refused", runtime: "builtin", stop_reason: "handover_refused", error: "HandoverRefused", ...facts },
        }),
        4,
      );
    const earlier = refused({ refused: "earlier_outcome", outcome: "denied" });
    expect(earlier[0]).toMatchObject({ key: "executeAgentRefused", values: { refused: "earlier_outcome", outcome: "denied" }, tone: "error", source: "agent" });
    expect(read("en", earlier)).toEqual(["Exploration refused: the request already ended on its fixed path (denied)"]);
    expect(read("zh", earlier)).toEqual(["已拒绝探索：该请求已在固定流程中结束（已拒绝）"]);

    expect(read("en", refused({ refused: "explicit_target", outcome: null }))).toEqual(["Exploration refused: the request names a specific report"]);
    expect(read("zh", refused({ refused: "no_handover", outcome: null }))).toEqual(["已拒绝探索：该请求未被移交给探索"]);
    expect(read("en", refused({ refused: "earlier_outcome", outcome: "needs_confirmation" }))).toEqual([
      "Exploration refused: the request already ended on its fixed path (waiting for approval)",
    ]);
  });

  it("names what runs next: planning after the hand-over, the answer after the run ends", () => {
    const at = (last: TraceStep) => activeLabel([step({ step: "candidates", facts: { count: 4 } }), last]).key;
    expect(at(step({ step: "handover", source: "agent", facts: evidence({ best: null }) }))).toBe("planning");
    expect(at(step({ step: "execute", facts: { loop: "start", runtime: "pi", exposed: [] } }))).toBe("planning");
    expect(at(step({ step: "execute", facts: { loop: "turn", turn: 1, requested: ["low_stock"] } }))).toBe("planning");
    expect(at(step({ step: "execute", facts: { loop: "answer", turn: 2, stop_reason: "answered" } }))).toBe("writing");
    expect(at(step({ step: "execute", status: "error", facts: { loop: "refused", stop_reason: "handover_refused" } }))).toBe("writing");
    expect(at(step({ step: "execute", status: "blocked", facts: { loop: "stopped", stop_reason: "needs_confirmation" } }))).toBe("finishing");
  });
});

describe("exploreStop", () => {
  const end = (status: TraceStep["status"], facts: Record<string, unknown>) =>
    step({ step: "execute", status, facts: { runtime: "pi", model: "mock", turns: 2, calls: [], ...facts } });
  const trace = (last: TraceStep) => [
    step({ step: "understand", source: "agent", facts: { kind: "open_task" } }),
    step({ step: "handover", source: "agent", facts: { reason: "intent_not_covered" } }),
    step({ step: "execute", facts: { loop: "start", runtime: "pi", exposed: ["low_stock"] } }),
    last,
    step({ step: "narrate", facts: { source: "template" } }),
  ];
  const hub = (locale: keyof typeof CATALOGS) => createTranslator({ locale, messages: CATALOGS[locale], namespace: "hub" });

  it("is null for a turn that did not explore, and for a run that answered or waits for an approval", () => {
    expect(exploreStop([])).toBeNull();
    expect(exploreStop([step({ step: "execute", facts: { row_count: 3 } })])).toBeNull();
    expect(exploreStop(trace(end("ok", { loop: "answer", turn: 2, stop_reason: "answered" })))).toBeNull();
    expect(exploreStop(trace(end("blocked", { loop: "stopped", reason: "needs_confirmation", stop_reason: "needs_confirmation" })))).toBeNull();
  });

  it("is a warning for a spent budget and an error for anything else, a refusal included", () => {
    for (const reason of ["max_turns", "max_tool_calls"]) {
      expect(exploreStop(trace(end("blocked", { loop: "stopped", reason, stop_reason: reason })))).toEqual({ reason, detail: "none", tone: "warn" });
    }
    for (const reason of ["timeout", "worker_crashed", "protocol_error", "model_error", "audit_failed", "runtime_error"]) {
      expect(exploreStop(trace(end("error", { loop: "turn", turn: 1, error: "X", stop_reason: reason })))).toEqual({ reason, detail: "none", tone: "error" });
    }
    const refused = step({ step: "execute", status: "error", facts: { loop: "refused", stop_reason: "handover_refused", refused: "no_handover" } });
    expect(exploreStop([refused])).toEqual({ reason: "handover_refused", detail: "none", tone: "error" });
    const unavailable = end("error", { loop: "turn", turn: 1, error: "ExplorerUnavailable", stop_reason: "worker_unavailable", detail: "startup_timeout" });
    expect(exploreStop(trace(unavailable))).toEqual({ reason: "worker_unavailable", detail: "startup_timeout", tone: "error" });
    // a reason this build does not know is still a failure; text that is not a name reads "?"
    expect(exploreStop(trace(end("error", { loop: "turn", stop_reason: "gremlins" })))).toMatchObject({ reason: "gremlins", tone: "error" });
    expect(exploreStop(trace(end("error", { loop: "turn", stop_reason: "it broke: see the log" })))).toMatchObject({ reason: "?", tone: "error" });
  });

  it("names every stop reason in both languages, the worker's start-up detail included", () => {
    const reasons = [
      "max_turns",
      "max_tool_calls",
      "handover_refused",
      "timeout",
      "worker_crashed",
      "protocol_error",
      "worker_unavailable",
      "model_error",
      "audit_failed",
      "runtime_error",
    ];
    for (const locale of ["en", "zh"] as const) {
      const t = hub(locale);
      const texts = reasons.map((reason) => t("stopReason", { reason, detail: "none" }));
      // each has its own words: none falls through to the raw reason, no two read the same
      expect(texts.filter((text, i) => text === reasons[i])).toEqual([]);
      expect(new Set(texts).size).toBe(reasons.length);
    }
    expect(hub("en")("stopReason", { reason: "max_turns", detail: "none" })).toBe("turn limit reached");
    expect(hub("en")("stopReason", { reason: "worker_unavailable", detail: "startup_timeout" })).toBe(
      "the worker was unavailable (it did not start in time)",
    );
    expect(hub("zh")("stopReason", { reason: "worker_unavailable", detail: "version_drift" })).toBe("工作进程不可用（版本不是固定的版本）");
    expect(hub("en")("stopReason", { reason: "gremlins", detail: "none" })).toBe("gremlins");
  });
});

describe("modelFailure", () => {
  const route = { call: "route", error: "BadRequestError", http_status: 400 } as const;
  const narrate = { call: "narrate", error: "BadRequestError", http_status: 400 } as const;

  it("names each failed call once, in the order it failed, and what stood in for it", () => {
    expect(modelFailure([route, narrate], "template")).toEqual({ calls: ["route", "narrate"], fallback: "template" });
    expect(modelFailure([narrate, route, narrate], "template")).toEqual({ calls: ["narrate", "route"], fallback: "template" });
    // routing alone failed: the rules and keyword search routed, whoever narrated
    expect(modelFailure([route], "model")).toEqual({ calls: ["route"], fallback: "rules" });
    expect(modelFailure([route], "system")).toEqual({ calls: ["route"], fallback: "rules" });
    // a template no failed text call caused is not blamed on the failure
    expect(modelFailure([route], "template")).toEqual({ calls: ["route"], fallback: "rules" });
    expect(modelFailure([{ call: "chat", error: "APITimeoutError" }], "system")).toEqual({ calls: ["chat"], fallback: "none" });
  });

  it("is null when no call failed: the zero-key mock, a model that answered, a server before the field", () => {
    expect(modelFailure([], "model")).toBeNull();
    expect(modelFailure(undefined, "template")).toBeNull();
  });
});

describe("trace helpers", () => {
  const steps = [
    step({ step: "candidates", facts: { count: 7 } }),
    step({ step: "understand", source: "rule", facts: { kind: "tool", target: "late_shipments" } }),
  ];

  it("flattens steps to lines with stable ids", () => {
    expect(describeTrace(steps).map((l) => l.id)).toEqual(["0", "1"]);
    expect(routeSourceOf(steps)).toBe("rule");
  });

  it("names what runs next from the last step received", () => {
    expect(activeLabel([])).toEqual({ key: "understanding", values: {} });
    expect(activeLabel(steps)).toEqual({ key: "running", values: { target: "late_shipments" } });
    expect(activeLabel([...steps, step({ step: "execute", facts: { row_count: 1 } })]).key).toBe("writing");
  });

  it("after a passed scope check the gate and the run still follow", () => {
    const scoped = [...steps, step({ step: "scope", facts: { target: "late_shipments", verdict: "pass" } })];
    expect(activeLabel(scoped)).toEqual({ key: "running", values: { target: "late_shipments" } });
    const blocked = [...steps, step({ step: "scope", status: "blocked", facts: { target: "late_shipments", verdict: "gap" } })];
    expect(activeLabel(blocked)).toEqual({ key: "options", values: {} });
    expect(activeLabel([...steps, step({ step: "scope", status: "error" })]).key).toBe("finishing");
  });
});
