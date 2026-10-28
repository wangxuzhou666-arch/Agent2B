import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ApprovalOutcome, TraceStep } from "@/lib/api";

import { renderWithIntl } from "../../../test/render";
import { OutcomeView } from "./approval-outcome";

const narrated: ApprovalOutcome = {
  grant_id: "g-123",
  kind: "query",
  target: "create_po",
  status: "ok",
  problem: null,
  answer: "Created draft purchase order PO-20031.",
  answer_source: "model",
  narrative_stripped: 0,
  columns: [],
  rows: [],
  aggregates: {},
  provenance: null,
  audit_ids: [42],
  import_result: null,
  trace: [],
  model_errors: [],
};

const failed = { call: "narrate", error: "BadRequestError", http_status: 400 } as const;
const steps: TraceStep[] = [
  { step: "execute", status: "ok", source: null, facts: { row_count: 1 }, duration_ms: 3 },
  {
    step: "narrate",
    status: "ok",
    source: null,
    facts: { source: "template", stripped: 0, provider: "openai_compat", mock: false, model_error: failed },
    duration_ms: 0,
  },
  { step: "respond", status: "ok", source: null, facts: { answer_source: "template", rows: 1, model_errors: [failed] }, duration_ms: 0 },
];
const templated: ApprovalOutcome = { ...narrated, answer_source: "template", trace: steps, model_errors: [failed] };

describe("OutcomeView", () => {
  it("says under the run's narration that its model call failed and a fixed template stood in, in both languages", () => {
    const view = renderWithIntl(<OutcomeView outcome={templated} elapsedMs={12} source="reply" />);
    const note = screen.getByText("Model call failed (narration); this answer was narrated from a fixed template.");
    expect(note).toHaveClass("text-xs", "text-warning");
    // inside the verdict's inset, after the narration
    const answer = screen.getByText("Created draft purchase order PO-20031.");
    expect(answer.compareDocumentPosition(note) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(note.parentElement).toBe(screen.getByText("The write ran.").parentElement);
    // the run's trace names the failure: its class and HTTP status
    const trace = screen.getByRole("list", { name: "Reasoning trace" });
    expect(within(trace).getByText("Model call failed (BadRequestError, HTTP 400); narrated from a fixed template")).toBeInTheDocument();
    expect(within(trace).getByText("Model call failed: narration (BadRequestError, HTTP 400)")).toBeInTheDocument();

    view.unmount();
    renderWithIntl(<OutcomeView outcome={templated} elapsedMs={12} source="reply" />, { locale: "zh" });
    expect(screen.getByText("模型调用失败（叙述），本回答改由固定模板叙述。")).toBeInTheDocument();
  });

  it("adds no line when the model narrated, or for an outcome rebuilt from the audit log (no field at all)", () => {
    const view = renderWithIntl(<OutcomeView outcome={narrated} elapsedMs={12} source="reply" />);
    expect(screen.getByText("Created draft purchase order PO-20031.")).toBeInTheDocument();
    expect(screen.queryByText(/^Model calls? failed/)).not.toBeInTheDocument();
    view.unmount();
    // what lib/approvals rebuilds after a lost reply: no answer, no trace, no model_errors
    const replayed: ApprovalOutcome = { ...narrated, answer: "", answer_source: "system", audit_ids: [42] };
    delete replayed.model_errors;
    renderWithIntl(<OutcomeView outcome={replayed} elapsedMs={null} source="audit" />);
    expect(screen.getByText("From the audit log: the reply to the approval was lost in transit.")).toBeInTheDocument();
    expect(screen.queryByText(/^Model calls? failed/)).not.toBeInTheDocument();
  });
});
