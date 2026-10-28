import { act, fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { AnchorHTMLAttributes } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, type ApprovalOutcome, type AuditEntryOut } from "@/lib/api";
import type { ApprovalClient, Deadline } from "@/lib/approvals";

import { approvalsValue } from "../../../test/approvals";
import { deferred } from "../../../test/deferred";
import { renderWithIntl } from "../../../test/render";
import { ApprovalCard, type ApprovalCardProps } from "./approval-card";

vi.mock("next/link", () => ({
  default: ({ href, ...props }: AnchorHTMLAttributes<HTMLAnchorElement> & { href: string }) => <a href={href} {...props} />,
}));
const approvals = vi.hoisted(() => ({ value: null as unknown as ReturnType<typeof approvalsValue> }));
vi.mock("@/components/providers/approvals-provider", () => ({ useApprovals: () => approvals.value }));

const OUTCOME: ApprovalOutcome = {
  grant_id: "g-123",
  kind: "query",
  target: "create_po",
  status: "ok",
  problem: null,
  answer: "Created draft purchase order PO-20031.",
  answer_source: "template",
  narrative_stripped: 0,
  columns: ["po_id", "qty"],
  rows: [{ po_id: "PO-20031", qty: 200 }],
  aggregates: {},
  provenance: { tool: "create_po", label: "Create purchase order", row_count: 1, as_of: "2026-10-26T08:00:00Z", scope: "" },
  audit_ids: [41, 42],
  import_result: null,
  trace: [{ step: "execute", status: "ok", source: null, facts: { row_count: 1 }, duration_ms: 3 }],
};

const lost = () => new ApiError({ status: 0, code: "network_error", message: "The server could not be reached.", kind: "network" });
const httpError = (status: number, code: string) => new ApiError({ status, code, message: code, kind: "http" });

const client = {
  approve: vi.fn<ApprovalClient["approve"]>(),
  reject: vi.fn<ApprovalClient["reject"]>(),
  records: vi.fn<ApprovalClient["records"]>(),
};

const deadlineIn = (ms: number): Deadline => ({ endsAt: performance.now() + ms, windowMs: ms });

function renderCard(over: Partial<ApprovalCardProps> = {}) {
  const props: ApprovalCardProps = {
    grantId: "g-123",
    summary: "Create a draft purchase order (sku_id=SKU-10042, qty=200)",
    title: "Approval required",
    why: "This request changes data, so it runs only after you approve it.",
    deadline: deadlineIn(15 * 60_000),
    userId: "operator",
    ...over,
  };
  return renderWithIntl(<ApprovalCard {...props} />);
}

beforeEach(() => {
  for (const fn of Object.values(client)) fn.mockReset();
  approvals.value = approvalsValue({}, { client });
});
afterEach(() => vi.useRealTimers());

describe("ApprovalCard", () => {
  it("shows what will run and approves inline, once, however often it is pressed", async () => {
    const user = userEvent.setup();
    const reply = deferred<ApprovalOutcome>();
    client.approve.mockReturnValue(reply.promise);
    renderCard();
    const card = screen.getByRole("region", { name: "Approval required" });
    expect(within(card).getByText("Create a draft purchase order (sku_id=SKU-10042, qty=200)")).toBeInTheDocument();
    expect(await within(card).findByText(/Expires in 1[45]:\d\d/)).toBeInTheDocument();

    const approve = within(card).getByRole("button", { name: "Approve" });
    await user.dblClick(approve);
    // in flight: the pressed button keeps focus, but presses go nowhere
    const approving = within(card).getByRole("button", { name: /Approving/ });
    expect(approving).toHaveAttribute("aria-disabled", "true");
    expect(approving).toHaveFocus();
    await user.click(within(card).getByRole("button", { name: "Reject" }));
    expect(client.approve).toHaveBeenCalledTimes(1);
    expect(client.reject).not.toHaveBeenCalled();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();

    await act(async () => reply.resolve({ ...OUTCOME }));
    expect(screen.getByRole("heading", { name: /^Approved/ })).toBeInTheDocument();
    expect(client.approve).toHaveBeenCalledTimes(1);
  });

  it("takes a held Enter as one press", () => {
    renderCard();
    const approve = screen.getByRole("button", { name: "Approve" });
    expect(fireEvent.keyDown(approve, { key: "Enter" })).toBe(true);
    expect(fireEvent.keyDown(approve, { key: "Enter", repeat: true })).toBe(false); // default (activation) prevented
  });

  it("puts the receipt first once resolved, with the run's result inside the card as report text", async () => {
    const user = userEvent.setup();
    client.approve.mockResolvedValue(OUTCOME);
    renderCard();
    await user.click(screen.getByRole("button", { name: "Approve" }));

    const heading = await screen.findByRole("heading", { name: /^Approved by operator · \d\d:\d\d$/ });
    const card = screen.getByRole("region", { name: /^Approved/ });
    // the pressed button is gone: focus lands on the heading that says how it ended, and it is announced
    expect(heading).toHaveFocus();
    expect(within(card).getByRole("status")).toHaveTextContent("Approved. The write ran.");

    const answer = within(card).getByText("Created draft purchase order PO-20031.");
    expect(answer.closest(".report-prose")).not.toBeNull();
    expect(answer.closest(".answer-prose")).toBeNull(); // no chat-style answer inside a card
    expect(within(card).getByText("The write ran.")).toBeInTheDocument();
    expect(within(card).getByRole("cell", { name: "PO-20031" })).toBeInTheDocument();
    const trace = within(card).getByText(/^Ran after approval in /).closest("details");
    expect(trace).not.toHaveAttribute("open"); // collapsed by default
    expect(within(card).getByRole("link", { name: "View audit record #42" })).toHaveAttribute("href", "/audit?id=42");
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
  });

  it("never disables Approve on the local clock; time up only asks the server", async () => {
    vi.useFakeTimers();
    renderCard({ deadline: deadlineIn(3_000) });
    await act(() => vi.advanceTimersByTimeAsync(1));
    expect(screen.getByText("Expires in 0:03")).toBeInTheDocument();
    const refresh = approvals.value.refresh as ReturnType<typeof vi.fn>;

    await act(() => vi.advanceTimersByTimeAsync(3_100));
    expect(screen.getByText("Expires in 0:00")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeEnabled();
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("lets the server's 410 expire it", async () => {
    const user = userEvent.setup();
    client.approve.mockRejectedValue(httpError(410, "approval_expired"));
    renderCard();
    await user.click(screen.getByRole("button", { name: "Approve" }));
    expect(await screen.findByRole("heading", { name: "Expired" })).toHaveFocus();
    expect(screen.getByText("This confirmation expired. Ask again to get a new one.")).toBeInTheDocument();
    expect(client.records).not.toHaveBeenCalled();
  });

  it("offers a retry only for a reply lost in transit, then reads what really happened from the audit log", async () => {
    const user = userEvent.setup();
    client.approve.mockRejectedValueOnce(lost()).mockRejectedValueOnce(httpError(409, "grant_used"));
    const recorded: AuditEntryOut = {
      id: 42,
      ts: "2026-10-26T08:00:05Z",
      user_id: "operator",
      roles: ["operator"],
      target: "create_po",
      effect: "write",
      status: "ok",
      route_source: "rule",
      params_digest: "d1",
      params: {},
      row_count: 1,
      grant_id: "g-123",
      problem: null,
      request_id: "r1",
      parent_id: 40,
      duration_ms: 4,
    };
    const lookup = deferred<AuditEntryOut[]>();
    client.records.mockReturnValue(lookup.promise);
    renderCard();

    await user.click(screen.getByRole("button", { name: "Approve" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The connection failed before the server answered. Try again: a confirmation runs at most once.",
    );
    const retry = screen.getByRole("button", { name: "Approve" });
    expect(retry).toBeEnabled();

    // the first one went through after all: the retry finds the grant used, so the log decides
    await user.click(retry);
    expect(await screen.findByRole("button", { name: /Checking/ })).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByRole("status")).toHaveTextContent("the audit log is being checked for what happened");
    expect(client.records).toHaveBeenCalledWith("g-123");

    await act(async () => lookup.resolve([recorded]));
    const card = screen.getByRole("region", { name: /^Approved by operator/ });
    expect(within(card).getByText("The write ran.")).toBeInTheDocument();
    expect(within(card).getByText("From the audit log: the reply to the approval was lost in transit.")).toBeInTheDocument();
    expect(within(card).getByRole("link", { name: "View audit record #42" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("takes a 409 without a lost reply before it as the verdict, and offers no retry", async () => {
    const user = userEvent.setup();
    client.approve.mockRejectedValue(httpError(409, "grant_used"));
    renderCard();
    await user.click(screen.getByRole("button", { name: "Approve" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This confirmation was already used or no longer matches the request.");
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Reject" })).toBeDisabled();
    expect(client.records).not.toHaveBeenCalled();
  });

  it("keeps an older card static, and says where to act on it", () => {
    renderCard({ interactive: false, footnote: "Only the latest pending confirmation can be approved here." });
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.getByText("Only the latest pending confirmation can be approved here.")).toBeInTheDocument();
  });

  it("shows a confirmation resolved in another window by its receipt, even as an older card", () => {
    approvals.value.store.depart("g-123", false);
    renderCard({ interactive: false });
    expect(screen.getByRole("heading", { name: "Resolved elsewhere" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(""); // it was so before it came on screen: nothing to announce
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
