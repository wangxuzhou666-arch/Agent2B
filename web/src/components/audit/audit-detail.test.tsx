import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AuditQuery, TraceStep } from "@/lib/api";

import { auditDetail, auditEntry } from "../../../test/fixtures";
import { renderWithIntl } from "../../../test/render";
import { AuditDetailSheet } from "./audit-detail";

const apiMock = vi.hoisted(() => ({ getAudit: vi.fn(), listAudit: vi.fn() }));
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, ...apiMock } };
});

const step = (s: Partial<TraceStep> & Pick<TraceStep, "step">): TraceStep =>
  ({ status: "ok", source: null, facts: {}, duration_ms: 0, ...s }) as TraceStep;

const trace: TraceStep[] = [
  step({ step: "candidates", facts: { count: 4 } }),
  step({ step: "understand", source: "rule", facts: { kind: "tool", target: "make_order" }, duration_ms: 2 }),
  step({ step: "scope", status: "blocked", facts: { mentioned: ["I-1", "WH-9"], bound: ["I-1"], unbound: ["WH-9"] } }),
  step({ step: "gate", status: "ok", facts: { confirmation: "consumed" } }),
  step({ step: "warp_drive", status: "ok", facts: { engaged: true } }),
  step({ step: "execute", status: "ok", facts: { row_count: 1 }, duration_ms: 3 }),
];

const current = auditDetail({
  id: 6,
  ts: "2026-10-26T08:00:05Z",
  status: "ok",
  request_id: "req-1",
  grant_id: "g1",
  parent_id: 3,
  row_count: 1,
  params: { item_id: "I-1", qty: 200, cost_basis: "***", supplier_id: null },
  trace,
});

beforeEach(() => {
  apiMock.getAudit.mockReset();
  apiMock.listAudit.mockReset();
  apiMock.getAudit.mockImplementation(async (id: number) =>
    id === 6 ? current : auditDetail({ id, status: "needs_confirmation", ts: "2026-10-26T08:00:00Z" }),
  );
  apiMock.listAudit.mockImplementation(async (query: AuditQuery) => ({
    items:
      query.request_id === "req-1"
        ? [
            auditEntry({ id: 6, ts: "2026-10-26T08:00:05Z", status: "ok", parent_id: 3 }),
            auditEntry({ id: 5, ts: "2026-10-26T08:00:05Z", status: "started", parent_id: 3 }),
            auditEntry({ id: 3, ts: "2026-10-26T08:00:00Z", status: "needs_confirmation" }),
          ]
        : [],
    total: 3,
    page: 1,
    page_size: 200,
  }));
});

describe("AuditDetailSheet", () => {
  it("replays every recorded step, including scope and steps this build does not know", async () => {
    renderWithIntl(<AuditDetailSheet entryId={6} onClose={() => undefined} onOpen={() => undefined} />);
    const replay = await screen.findByRole("list", { name: "Reasoning trace" });
    const lines = within(replay)
      .getAllByRole("listitem")
      .map((li) => li.textContent);
    expect(lines).toEqual([
      expect.stringContaining("4 tools available"),
      expect.stringMatching(/Matched by keywords → make_order.*rule/),
      "Scope check: 1 named item is not covered by the parameters (WH-9)",
      "Permission check passed; your approval was used",
      "warp_drive (ok) · engaged=true",
      expect.stringContaining("Ran deterministic tool (1 row)"),
    ]);
  });

  it("shows parameters as recorded, with masked values called out", async () => {
    renderWithIntl(<AuditDetailSheet entryId={6} onClose={() => undefined} onOpen={() => undefined} />);
    const params = await screen.findByRole("region", { name: "Parameters" });
    expect(within(params).getByText("cost_basis").nextSibling).toHaveTextContent("masked");
    expect(within(params).getByText("200")).toBeInTheDocument();
    expect(within(params).queryByText("***")).not.toBeInTheDocument();
  });

  it("draws the request chain oldest first and opens a sibling", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    renderWithIntl(<AuditDetailSheet entryId={6} onClose={() => undefined} onOpen={onOpen} />);
    const chain = await screen.findByRole("list", { name: "Request chain" });
    const steps = within(chain).getAllByRole("listitem");
    expect(steps.map((li) => li.querySelector("button, span.font-semibold")?.textContent)).toEqual([
      "Needs approval",
      "Started",
      "OK",
    ]);
    expect(steps[2]).toHaveAttribute("aria-current", "step");
    expect(apiMock.listAudit).toHaveBeenCalledWith({ request_id: "req-1", page_size: 200 }, expect.anything());
    expect(apiMock.listAudit).toHaveBeenCalledWith({ grant_id: "g1", page_size: 200 }, expect.anything());
    await user.click(within(chain).getByRole("button", { name: "Needs approval" }));
    expect(onOpen).toHaveBeenCalledWith(3);
  });
});
