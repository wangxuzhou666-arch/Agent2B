import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { TraceStep } from "@/lib/api";

import { renderWithIntl } from "../../../test/render";
import { TraceList } from "./trace-list";

const steps: TraceStep[] = [
  { step: "candidates", status: "ok", source: null, facts: { count: 7 }, duration_ms: 0.4 },
  {
    step: "understand",
    status: "ok",
    source: "rule",
    facts: { kind: "tool", target: "late_shipments", reason: "keyword match: late" },
    duration_ms: 1.8,
  },
  { step: "gate", status: "ok", source: null, facts: { target: "late_shipments", confirmation: "not_required" }, duration_ms: 5.2 },
  { step: "execute", status: "ok", source: null, facts: { row_count: 12 }, duration_ms: 3.9 },
  { step: "narrate", status: "ok", source: null, facts: { source: "model", stripped: 1 }, duration_ms: 0 },
];

describe("TraceList", () => {
  it("collapses a finished trace to 'Thought for …' and expands to every step", () => {
    renderWithIntl(<TraceList steps={steps} elapsedMs={412} />);
    const summary = screen.getByText("Thought for 412 ms");
    const details = summary.closest("details");
    expect(details).not.toHaveAttribute("open");
    const list = within(details!).getByRole("list", { name: "Reasoning trace" });
    expect(within(list).getByText("Matched by keywords → late_shipments")).toBeInTheDocument();
    expect(within(list).getByText(/keyword match: late/)).toBeInTheDocument();
    expect(within(list).getByText("Permission and precondition check passed")).toBeInTheDocument();
    expect(within(list).getByText("Ran deterministic tool (12 rows)")).toBeInTheDocument();
    expect(within(list).getByText("Numeric lock removed 1 sentence")).toBeInTheDocument();
    // route-source tag, verbatim, with its meaning for assistive tech
    expect(list.querySelector('[data-route-source="rule"]')).toHaveTextContent("rule (Keyword rule arm)");
    // measured durations only; the narrate step had no timing and shows none
    expect(within(list).getByText("2 ms")).toBeInTheDocument();
    expect(within(list).getByText("5 ms")).toBeInTheDocument();
    expect(within(list).getByText("4 ms")).toBeInTheDocument();
    expect(within(list).queryByText("0 ms")).not.toBeInTheDocument();
  });

  it("says when the narration is template text because no model is configured, in both languages", () => {
    const narrated: TraceStep[] = [
      ...steps.slice(0, 4),
      { step: "narrate", status: "ok", source: null, facts: { source: "model", stripped: 0, provider: "mock", mock: true }, duration_ms: 0 },
    ];
    const view = renderWithIntl(<TraceList steps={narrated} elapsedMs={412} />);
    const list = screen.getByRole("list", { name: "Reasoning trace" });
    expect(within(list).getByText("Template narration (no model configured)")).toBeInTheDocument();
    expect(within(list).queryByText("Narrated by the language model")).not.toBeInTheDocument();
    view.unmount();

    renderWithIntl(<TraceList steps={narrated} elapsedMs={412} />, { locale: "zh" });
    expect(within(screen.getByRole("list", { name: "推理轨迹" })).getByText("模板叙述（未配置模型）")).toBeInTheDocument();
  });

  it("shows arriving steps and the thinking line while live", () => {
    renderWithIntl(<TraceList steps={steps.slice(0, 2)} live startedAt={0} />);
    const list = screen.getByRole("list", { name: "Reasoning trace" });
    expect(within(list).getByText("7 tools available to your role")).toBeInTheDocument();
    expect(within(list).getByText("Matched by keywords → late_shipments")).toBeInTheDocument();
    // the active line names what runs next (screen readers get the full text at once)
    expect(within(list).getByText("Running late_shipments")).toBeInTheDocument();
    expect(screen.queryByText(/Thought for/)).not.toBeInTheDocument();
  });

  it("folds old steps into an 'earlier steps' line while live", () => {
    const many = Array.from({ length: 8 }, (_, i) => ({ ...steps[0]!, facts: { count: i } }));
    renderWithIntl(<TraceList steps={many} live startedAt={0} />);
    expect(screen.getByText("…3 earlier steps")).toBeInTheDocument();
  });

  it("says how a stopped or failed request ended", () => {
    const { rerender } = renderWithIntl(<TraceList steps={steps} elapsedMs={1500} outcome="stopped" />);
    expect(screen.getByText("Stopped after 1.5 s")).toBeInTheDocument();
    rerender(<TraceList steps={steps} elapsedMs={2000} outcome="failed" />);
    expect(screen.getByText("Failed after 2.0 s")).toBeInTheDocument();
  });

  it("renders in Chinese", () => {
    renderWithIntl(<TraceList steps={steps} elapsedMs={400} />, { locale: "zh" });
    expect(screen.getByText("思考了 400 ms")).toBeInTheDocument();
    expect(screen.getByText("关键词命中 → late_shipments")).toBeInTheDocument();
  });

  it("names excluded scope items in the reader's language", () => {
    const scoped: TraceStep[] = [
      {
        step: "scope",
        status: "ok",
        source: "explicit",
        facts: {
          verdict: "unfiltered",
          acknowledged: true,
          mentioned: [{ key: "category:sku", value: "packaging", negated: true, text: "packaging" }],
          unbound: [{ key: "category:sku", value: "packaging", negated: true }],
        },
        duration_ms: 0.2,
      },
    ];
    const en = renderWithIntl(<TraceList steps={scoped} elapsedMs={10} defaultOpen />);
    expect(screen.getByText("Scope check: ran without the filter you named (not packaging), as you chose")).toBeInTheDocument();
    en.unmount();
    renderWithIntl(<TraceList steps={scoped} elapsedMs={10} defaultOpen />, { locale: "zh" });
    expect(screen.getByText("范围检查：按你的选择，未按你提到的条件筛选（非packaging）")).toBeInTheDocument();
  });
});
