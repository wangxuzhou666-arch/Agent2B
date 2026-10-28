import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { AnchorHTMLAttributes } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { QueryOut, TraceStep } from "@/lib/api";

import { approvalsValue } from "../../../test/approvals";
import { renderWithIntl } from "../../../test/render";
import type { Turn } from "./conversation-state";
import { TurnView } from "./turn-view";

const conversation = {
  send: vi.fn(),
  retry: vi.fn(),
};
const approvals = vi.hoisted(() => ({ value: null as unknown as ReturnType<typeof approvalsValue> }));

vi.mock("./conversation-provider", () => ({ useConversation: () => conversation }));
vi.mock("@/components/providers/approvals-provider", () => ({ useApprovals: () => approvals.value }));
vi.mock("next/link", () => ({
  default: ({ href, ...props }: AnchorHTMLAttributes<HTMLAnchorElement> & { href: string }) => (
    <a href={href} {...props} />
  ),
}));
vi.mock("@/components/providers/auth-provider", () => ({
  useAuth: () => ({ session: { userId: "demo-operator", roles: ["operator"] } }),
}));

const baseResponse: QueryOut = {
  conversation_id: "c1",
  query: "Which SKUs are low on stock?",
  decision: null,
  answer: "19 SKUs are below their reorder point.",
  answer_source: "model",
  columns: [],
  rows: [],
  aggregates: {},
  provenance: { tool: "low_stock", label: "Low stock", row_count: 19, as_of: "2026-10-26T08:00:00Z", scope: "all warehouses" },
  narrative_stripped: 0,
  approval: null,
  clarify_options: [],
  trace: [],
  error: null,
  audit_ids: [],
};

const turn = (over: Partial<Turn> = {}): Turn => ({
  id: "t1",
  query: "Which SKUs are low on stock?",
  explicitTarget: null,
  explicitParams: null,
  status: "done",
  startedAt: 0,
  steps: [],
  response: baseResponse,
  error: null,
  elapsedMs: 420,
  approvalDeadline: null,
  ...over,
});

beforeEach(() => {
  for (const fn of Object.values(conversation)) fn.mockReset();
  approvals.value = approvalsValue();
});

describe("TurnView", () => {
  it("shows the answer with its provenance line when there is no table: the tool's label, then its id", () => {
    renderWithIntl(<TurnView turn={turn()} latest busy={false} />);
    expect(screen.getByText("19 SKUs are below their reorder point.")).toBeInTheDocument();
    expect(
      screen.getByText(/^Source: Low stock \(low_stock\) · 19 rows · as of \d{4}-\d{2}-\d{2} \d{2}:\d{2} · scope: all warehouses$/),
    ).toBeInTheDocument();
  });

  it("tells screen readers who asked, punctuated per language", () => {
    const view = renderWithIntl(<TurnView turn={turn()} latest busy={false} />);
    expect(screen.getByText("You:", { selector: ".sr-only" }).textContent).toBe("You: ");
    view.unmount();
    renderWithIntl(<TurnView turn={turn()} latest busy={false} />, { locale: "zh" });
    expect(screen.getByText("你：", { selector: ".sr-only" })).toBeInTheDocument();
  });

  it("discloses the numeric lock under the answer", () => {
    renderWithIntl(
      <TurnView turn={turn({ response: { ...baseResponse, narrative_stripped: 2 } })} latest busy={false} />,
    );
    expect(
      screen.getByText(
        "Numeric lock removed 2 sentences with unverified numbers. Table values are unaffected.",
      ),
    ).toBeInTheDocument();
  });

  describe("who narrated: three states a reader can tell apart", () => {
    const narrate = (facts: TraceStep["facts"]): TraceStep => ({ step: "narrate", status: "ok", source: null, facts, duration_ms: 1 });
    const respond = (facts: TraceStep["facts"]): TraceStep => ({ step: "respond", status: "ok", source: null, facts, duration_ms: 0 });
    const failure = /^Model calls? failed/;

    it("no model configured: the trace says so, and no failure line follows the answer", () => {
      const steps = [narrate({ source: "model", stripped: 0, provider: "mock", mock: true }), respond({ rows: 19 })];
      renderWithIntl(<TurnView turn={turn({ steps, response: { ...baseResponse, trace: steps, model_errors: [] } })} latest busy={false} />);
      expect(screen.getByText("Template narration (no model configured)")).toBeInTheDocument();
      expect(screen.queryByText(failure)).not.toBeInTheDocument();
    });

    it("a configured model whose calls failed: one warning line under the answer, in both languages", () => {
      const model_errors = [
        { call: "route", error: "BadRequestError", http_status: 400 },
        { call: "narrate", error: "BadRequestError", http_status: 400 },
      ] as const;
      const steps = [
        narrate({ source: "template", stripped: 0, provider: "openai_compat", mock: false, model_error: model_errors[1] }),
        respond({ answer_source: "template", rows: 19, model_errors: [...model_errors] }),
      ];
      const response: QueryOut = { ...baseResponse, answer_source: "template", trace: steps, model_errors: [...model_errors] };
      const view = renderWithIntl(<TurnView turn={turn({ steps, response })} latest busy={false} />);
      const answer = screen.getByText("19 SKUs are below their reorder point.");
      const note = screen.getByText("Model calls failed (routing, narration); this answer was narrated from a fixed template.");
      expect(note).toHaveClass("text-xs", "text-warning");
      expect(answer.compareDocumentPosition(note) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      // the trace names each failure: its error class and HTTP status, never its message
      const trace = screen.getByRole("list", { name: "Reasoning trace" });
      expect(within(trace).getByText("Model call failed (BadRequestError, HTTP 400); narrated from a fixed template")).toBeInTheDocument();
      expect(within(trace).getByText("Model call failed: routing (BadRequestError, HTTP 400)")).toBeInTheDocument();
      expect(within(trace).queryByText("Narrated from a fixed template")).not.toBeInTheDocument();

      view.unmount();
      renderWithIntl(<TurnView turn={turn({ steps, response })} latest busy={false} />, { locale: "zh" });
      expect(screen.getByText("模型调用失败（路由、叙述），本回答改由固定模板叙述。")).toHaveClass("text-warning");
      expect(screen.getByText("模型调用失败（BadRequestError，HTTP 400），改由固定模板叙述")).toBeInTheDocument();
    });

    it("the model narrated: its label in the trace, and no failure line", () => {
      const steps = [narrate({ source: "model", stripped: 0, provider: "openai_compat", mock: false }), respond({ rows: 19 })];
      renderWithIntl(<TurnView turn={turn({ steps, response: { ...baseResponse, trace: steps, model_errors: [] } })} latest busy={false} />);
      expect(screen.getByText("Narrated by the language model")).toBeInTheDocument();
      expect(screen.queryByText(failure)).not.toBeInTheDocument();
    });

    it("a failed routing call alone says routing fell back, and leaves the narration to the model", () => {
      const response: QueryOut = { ...baseResponse, model_errors: [{ call: "route", error: "APITimeoutError", http_status: null }] };
      const view = renderWithIntl(<TurnView turn={turn({ response })} latest busy={false} />);
      expect(screen.getByText("Model call failed (routing); routing used rules and keyword search.")).toBeInTheDocument();
      view.unmount();
      renderWithIntl(<TurnView turn={turn({ response })} latest busy={false} />, { locale: "zh" });
      expect(screen.getByText("模型调用失败（路由），路由改用规则与关键词检索。")).toBeInTheDocument();
    });
  });

  describe("an exploration's stop reason: one labelled line under the answer", () => {
    const execute = (status: TraceStep["status"], facts: TraceStep["facts"]): TraceStep => ({ step: "execute", status, source: null, facts, duration_ms: 1 });
    const explored = (end: TraceStep): TraceStep[] => [
      { step: "understand", status: "ok", source: "agent", facts: { kind: "open_task" }, duration_ms: 1 },
      { step: "handover", status: "ok", source: "agent", facts: { reason: "intent_not_covered", to: "open_task" }, duration_ms: 0 },
      execute("ok", { loop: "start", runtime: "pi", exposed: ["low_stock"] }),
      end,
    ];
    const show = (steps: TraceStep[], answer: string, locale: "en" | "zh" = "en") => {
      // the server leaves `error` empty for these stops: the answer and the trace say why
      const response: QueryOut = { ...baseResponse, answer, answer_source: "template", provenance: null, trace: steps };
      return renderWithIntl(<TurnView turn={turn({ steps, response })} latest busy={false} />, { locale });
    };
    const stopLine = () => screen.queryByText(/^(Stop reason|停止原因)/)?.closest("p") ?? null;

    it("amber for a spent budget, after the answer that leads with it", () => {
      const answer = "Exploration stopped early: it reached its turn limit before finishing.";
      show(explored(execute("blocked", { loop: "stopped", reason: "max_turns", turns: 4, tool_calls: 3, stop_reason: "max_turns" })), answer);
      const line = stopLine();
      expect(line).toHaveTextContent("Stop reason: turn limit reached");
      expect(line).toHaveClass("text-xs", "text-warning");
      expect(line).not.toHaveClass("text-destructive");
      expect(screen.getByText(answer).compareDocumentPosition(line!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });

    it("red for a failure or a refusal, in both languages", () => {
      const unavailable = explored(
        execute("error", { loop: "turn", turn: 1, error: "ExplorerUnavailable", stop_reason: "worker_unavailable", detail: "startup_timeout" }),
      );
      const view = show(unavailable, "Exploration stopped: the exploration runtime failed.");
      expect(stopLine()).toHaveTextContent("Stop reason: the worker was unavailable (it did not start in time)");
      expect(stopLine()).toHaveClass("text-xs", "text-destructive");
      view.unmount();
      const zh = show(unavailable, "探索已停止：探索运行时出错。", "zh");
      expect(stopLine()).toHaveTextContent("停止原因：工作进程不可用（未能按时启动）");
      expect(stopLine()).toHaveClass("text-destructive");
      zh.unmount();

      // the guard refused: nothing ran, and the trace says why instead of a failed agent turn
      const refused = execute("error", { loop: "refused", runtime: "builtin", stop_reason: "handover_refused", error: "HandoverRefused", refused: "explicit_target", outcome: null });
      show([refused], "Exploration was refused: this request names a specific report, so it runs only on its fixed path.");
      expect(stopLine()).toHaveTextContent("Stop reason: exploration was refused");
      expect(stopLine()).toHaveClass("text-destructive");
      const trace = screen.getByRole("list", { name: "Reasoning trace" });
      expect(within(trace).getByText("Exploration refused: the request names a specific report")).toBeInTheDocument();
    });

    it("says nothing when the run answered or waits for an approval; the trace labels the hand-over and the start", () => {
      const view = show(explored(execute("ok", { loop: "answer", turn: 2, stop_reason: "answered" })), "Here is where things stand.");
      expect(stopLine()).toBeNull();
      const trace = screen.getByRole("list", { name: "Reasoning trace" });
      expect(within(trace).getByText("Handed over to exploration: no report covers the question")).toBeInTheDocument();
      expect(within(trace).getByText("Exploration started (Pi) with 1 tool: low_stock")).toBeInTheDocument();
      view.unmount();

      show(
        explored(execute("blocked", { loop: "stopped", reason: "needs_confirmation", turns: 1, tool_calls: 1, stop_reason: "needs_confirmation" })),
        "Confirmation needed: Create a draft purchase order",
      );
      expect(stopLine()).toBeNull();
    });
  });

  it("puts provenance in the table header instead of repeating it", () => {
    const response = { ...baseResponse, columns: ["sku_id", "available"], rows: [{ sku_id: "SKU-1", available: 4 }] };
    renderWithIntl(<TurnView turn={turn({ response })} latest busy={false} />);
    expect(screen.getByRole("region", { name: "Source: Low stock (low_stock)" })).toBeInTheDocument();
    expect(screen.queryByText(/^Source: Low stock \(low_stock\) ·/)).not.toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "4" })).toHaveClass("text-right");
  });

  it("re-sends the question with explicit_target when a clarify chip is picked", async () => {
    const user = userEvent.setup();
    const response = {
      ...baseResponse,
      answer: "Several reports could answer this. Which one did you mean?",
      answer_source: "system" as const,
      provenance: null,
      clarify_options: [
        { name: "low_stock", description: "Shortages", effect: "read", unfiltered: false },
        { name: "reorder_suggestion", description: "Order quantities", effect: "read", unfiltered: false },
      ],
    };
    renderWithIntl(<TurnView turn={turn({ response })} latest busy={false} />);
    await user.click(screen.getByRole("button", { name: /reorder_suggestion/ }));
    expect(conversation.send).toHaveBeenCalledWith("Which SKUs are low on stock?", {
      explicitTarget: "reorder_suggestion",
      explicitParams: null,
    });
  });

  it("sends a scope choice's explicit_params exactly as received, under its label", async () => {
    const user = userEvent.setup();
    const params = { _scope_ack: ["category:sku=packaging"], warehouse: "WH-EAST" };
    const response = {
      ...baseResponse,
      answer: "`low_stock` can't filter by category other than *Packaging*.",
      answer_source: "system" as const,
      provenance: null,
      clarify_options: [
        {
          name: "low_stock",
          description: "Shortages",
          effect: "read",
          label: "Show all categories",
          explicit_params: params,
          unfiltered: true,
        },
        { name: "inventory_lookup", description: "Stock by item", effect: "read", unfiltered: false },
      ],
    };
    renderWithIntl(<TurnView turn={turn({ query: "low stock items except packaging", response })} latest busy={false} />);
    const chip = screen.getByRole("button", { name: /Show all categories/ });
    // the choice in words first, then the report it runs (DESIGN 10.4: "Label (tool_id)")
    expect(chip).toHaveTextContent(/^Show all categories \(low_stock\)/);
    expect(within(chip).getByText("(low_stock)")).toHaveClass("font-mono");
    await user.click(chip);
    expect(conversation.send).toHaveBeenCalledTimes(1);
    const [query, options] = conversation.send.mock.calls[0]!;
    expect(query).toBe("low stock items except packaging");
    expect(options).toEqual({ explicitTarget: "low_stock", explicitParams: params });
    expect(options.explicitParams).toBe(params); // untouched: the one-shot `_scope_ack` rides along

    // an option without a label shows its report id
    await user.click(screen.getByRole("button", { name: "inventory_lookup" }));
    expect(conversation.send).toHaveBeenLastCalledWith("low stock items except packaging", {
      explicitTarget: "inventory_lookup",
      explicitParams: null,
    });
  });

  it("tags an unfiltered choice and says what it means, in both languages", async () => {
    const user = userEvent.setup();
    const response = {
      ...baseResponse,
      answer_source: "system" as const,
      provenance: null,
      clarify_options: [
        {
          name: "low_stock",
          description: "Shortages",
          effect: "read",
          label: "Show all categories",
          explicit_params: { _scope_ack: ["category:sku=packaging"] },
          unfiltered: true,
        },
        { name: "inventory_lookup", description: "Stock by item", effect: "read", unfiltered: false },
      ],
    };
    const view = renderWithIntl(<TurnView turn={turn({ response })} latest busy={false} />);
    const chip = screen.getByRole("button", { name: /Show all categories/ });
    expect(chip).toHaveTextContent("Show all categories (low_stock)Unfiltered");
    expect(chip).toHaveAccessibleDescription("Runs without the filter you asked for. The answer says it is unfiltered.");
    expect(chip).not.toHaveAttribute("title");
    const other = screen.getByRole("button", { name: "inventory_lookup" });
    expect(other).not.toHaveTextContent("Unfiltered");
    expect(other).toHaveAttribute("title", "Stock by item");

    await user.hover(chip);
    expect(await screen.findByRole("tooltip")).toHaveTextContent("Runs without the filter you asked for.");

    view.unmount();
    renderWithIntl(<TurnView turn={turn({ response })} latest busy={false} />, { locale: "zh" });
    const zh = screen.getByRole("button", { name: /Show all categories/ });
    expect(zh).toHaveTextContent("Show all categories（low_stock）未筛选"); // full-width parentheses in Chinese
    expect(zh).toHaveTextContent("未筛选");
    expect(zh).toHaveAccessibleDescription("不按你提出的条件筛选，回答中会注明未筛选。");
  });

  it("keeps chips of older turns static", () => {
    const response = {
      ...baseResponse,
      clarify_options: [{ name: "low_stock", description: "Shortages", effect: "read", unfiltered: false }],
    };
    renderWithIntl(<TurnView turn={turn({ response })} latest={false} busy={false} />);
    expect(screen.getByRole("button", { name: /low_stock/ })).toBeDisabled();
  });

  it("offers Retry on a transport failure of the latest turn", async () => {
    const user = userEvent.setup();
    renderWithIntl(
      <TurnView
        turn={turn({
          status: "error",
          response: null,
          error: { code: "network_error", message: "x", kind: "network", status: 0, retryable: true },
        })}
        latest
        busy={false}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Can't reach the server.");
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(conversation.retry).toHaveBeenCalledWith("t1");
  });

  it("offers no Retry when the server answered with a failure (a verdict, not a lost answer)", () => {
    const response = { ...baseResponse, answer: "", provenance: null, error: "The report failed while running." };
    const view = renderWithIntl(<TurnView turn={turn({ response })} latest busy={false} />);
    expect(screen.getByRole("alert")).toHaveTextContent("The request failed: The report failed while running.");
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();

    // nor for a failed turn whose error frame was the server's verdict
    view.unmount();
    renderWithIntl(
      <TurnView
        turn={turn({
          status: "error",
          response: null,
          error: { code: "query_failed", message: "The request failed while running.", kind: "stream", status: 200, retryable: false },
        })}
        latest
        busy={false}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("The request failed: The request failed while running.");
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
  });

  it("lets the approval card stand in for the system's confirmation text", () => {
    const response = {
      ...baseResponse,
      answer: "Confirmation needed: Create a draft purchase order (sku_id=SKU-10042, qty=200)",
      answer_source: "system" as const,
      provenance: null,
      approval: {
        grant_id: "g1",
        target: "create_po",
        summary: "Create a draft purchase order (sku_id=SKU-10042, qty=200)",
        label: "Create purchase order",
        expires_at: new Date(Date.now() + 60_000).toISOString(),
        expires_in_s: 60,
      },
    };
    renderWithIntl(
      <TurnView turn={turn({ response, approvalDeadline: { endsAt: 60_000, windowMs: 60_000 } })} latest busy={false} />,
    );
    expect(screen.getByRole("region", { name: "Approval required · Create purchase order (create_po)" })).toBeInTheDocument();
    expect(screen.queryByText(/^Confirmation needed/)).not.toBeInTheDocument();
  });

  it("titles the approval card by what waits, its label then the id of its target", () => {
    const decision: NonNullable<QueryOut["decision"]> = {
      kind: "tool",
      target: "create_po",
      params: {},
      confidence: 1,
      source: "rule",
      clarify_options: [],
      reason: "",
    };
    const response = {
      ...baseResponse,
      answer: "",
      answer_source: "system" as const,
      provenance: null,
      decision,
      approval: {
        grant_id: "g1",
        target: "create_po",
        summary: "Create a draft purchase order",
        label: "新建采购单",
        expires_at: new Date(Date.now() + 60_000).toISOString(),
        expires_in_s: 60,
      },
    };
    renderWithIntl(<TurnView turn={turn({ response, approvalDeadline: { endsAt: 60_000, windowMs: 60_000 } })} latest busy={false} />, {
      locale: "zh",
    });
    const card = screen.getByRole("region", { name: "需要审批 · 新建采购单（create_po）" });
    expect(within(card).getByText("（create_po）")).toHaveClass("font-mono");
  });

  describe("the id in the approval card's title: the approval's own target first, the routed one if it names none", () => {
    const routed = (target: string | null): NonNullable<QueryOut["decision"]> => ({
      kind: target ? "tool" : "open_task",
      target,
      params: {},
      confidence: target ? 1 : 0,
      source: target ? "rule" : "agent",
      clarify_options: [],
      reason: "",
    });
    const held = (approvalTarget: string | null, decisionTarget: string | null) => {
      const response: QueryOut = {
        ...baseResponse,
        answer: "",
        answer_source: "system",
        provenance: null,
        decision: routed(decisionTarget),
        approval: {
          grant_id: "g1",
          target: approvalTarget,
          summary: "Create a draft purchase order",
          label: "Create purchase order",
          expires_at: new Date(Date.now() + 60_000).toISOString(),
          expires_in_s: 60,
        },
      };
      renderWithIntl(<TurnView turn={turn({ response, approvalDeadline: { endsAt: 60_000, windowMs: 60_000 } })} latest busy={false} />);
    };

    it("names a write the exploration proposed, which routed to no target", () => {
      held("create_po", null);
      const card = screen.getByRole("region", { name: "Approval required · Create purchase order (create_po)" });
      expect(within(card).getByText("(create_po)")).toHaveClass("font-mono");
    });

    it("falls back to the routed target when the approval names none", () => {
      held(null, "create_po");
      expect(screen.getByRole("region", { name: "Approval required · Create purchase order (create_po)" })).toBeInTheDocument();
    });

    it("prefers the approval's target to the routed one: the grant is bound to it", () => {
      held("create_po", "reorder_plan");
      expect(screen.getByRole("region", { name: "Approval required · Create purchase order (create_po)" })).toBeInTheDocument();
    });

    it("shows the label alone when nothing names the target", () => {
      held(null, null);
      expect(screen.getByRole("region", { name: "Approval required · Create purchase order" })).toBeInTheDocument();
    });
  });

  it("shows a confirmation that left the pending list untouched as resolved elsewhere, with no live buttons", () => {
    const response = {
      ...baseResponse,
      answer: "",
      answer_source: "system" as const,
      provenance: null,
      approval: {
        grant_id: "g1",
        target: "create_po",
        summary: "Create a draft purchase order",
        label: "Create purchase order",
        expires_at: new Date(Date.now() + 60_000).toISOString(),
        expires_in_s: 60,
      },
    };
    approvals.value.store.depart("g1", false); // what the provider does when the list drops it
    renderWithIntl(<TurnView turn={turn({ response, approvalDeadline: { endsAt: 60_000, windowMs: 60_000 } })} latest busy={false} />);
    const card = screen.getByRole("region", { name: /Resolved elsewhere/ });
    expect(card).toHaveTextContent("Create a draft purchase order");
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View in Audit" })).toHaveAttribute("href", "/audit?grant_id=g1");
  });
});
