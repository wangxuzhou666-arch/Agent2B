import { act, fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { AnchorHTMLAttributes } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, type ApprovalItem, type ApprovalOutcome } from "@/lib/api";

import { approvalsValue } from "../../../test/approvals";
import { renderWithIntl } from "../../../test/render";
import { ApprovalsView, EXPIRED_LINGER_MS, orderApprovals } from "./approvals-view";

const approvals = vi.hoisted(() => ({ value: null as unknown as ReturnType<typeof approvalsValue> }));
const session = vi.hoisted(() => ({ userId: "demo-operator", roles: ["operator"] as string[] }));
const apiMock = vi.hoisted(() => ({ approve: vi.fn(), reject: vi.fn(), listAudit: vi.fn() }));

vi.mock("@/components/providers/approvals-provider", () => ({ useApprovals: () => approvals.value }));
vi.mock("@/components/providers/auth-provider", () => ({ useAuth: () => ({ session }) }));
vi.mock("next/link", () => ({
  default: ({ href, ...props }: AnchorHTMLAttributes<HTMLAnchorElement> & { href: string }) => <a href={href} {...props} />,
}));
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, ...apiMock } };
});

const NOW = Date.parse("2026-10-26T08:00:00Z");
const item = (over: Partial<ApprovalItem> & Pick<ApprovalItem, "grant_id">): ApprovalItem => ({
  kind: "query",
  user_id: "demo-operator",
  target: "make_order",
  label: "Make order",
  summary: "Create a draft order (item_id=I-1, qty=200)",
  route_source: "rule",
  created_at: new Date(Date.now() - 60_000).toISOString(),
  expires_at: new Date(Date.now() + 14 * 60_000).toISOString(),
  expires_in_s: 14 * 60,
  mine: true,
  ...over,
});

const OUTCOME: ApprovalOutcome = {
  grant_id: "g1",
  kind: "query",
  target: "make_order",
  status: "ok",
  problem: null,
  answer: "Draft order D-7 created.",
  answer_source: "template",
  narrative_stripped: 0,
  columns: [],
  rows: [],
  aggregates: {},
  provenance: null,
  audit_ids: [5],
  import_result: null,
  trace: [],
};

/** The provider's list changes to `items`: same store, same refresh spy, a new arrival. */
function listed(items: ApprovalItem[]) {
  approvals.value = { ...approvals.value, items, count: items.length, arrival: { mono: performance.now() } };
}
const refresh = () => approvals.value.refresh as ReturnType<typeof vi.fn>;

beforeEach(() => {
  approvals.value = approvalsValue();
  for (const fn of Object.values(apiMock)) fn.mockReset();
  session.roles = ["operator"];
});
afterEach(() => vi.useRealTimers());

describe("orderApprovals", () => {
  it("puts mine first, then the soonest to expire", () => {
    const later = item({ grant_id: "a", expires_at: new Date(NOW + 9e5).toISOString() });
    const sooner = item({ grant_id: "b", expires_at: new Date(NOW + 1e5).toISOString() });
    const other = item({ grant_id: "c", mine: false, expires_at: new Date(NOW + 1e4).toISOString() });
    expect(orderApprovals([other, later, sooner]).map((i) => i.grant_id)).toEqual(["b", "a", "c"]);
  });
});

describe("ApprovalsView", () => {
  it("explains why approvals exist when nothing is pending", () => {
    renderWithIntl(<ApprovalsView />);
    expect(screen.getByText("Nothing is waiting for approval")).toBeInTheDocument();
    expect(
      screen.getByText(/Writes never run without an explicit, one-time confirmation bound to the exact parameters/),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Import a file" })).toHaveAttribute("href", "/import");
    expect(refresh()).toHaveBeenCalled(); // a fresh list on open
  });

  it("lists mine first and marks other users' requests as reject-only", () => {
    session.roles = ["admin"];
    listed([
      item({ grant_id: "other1", mine: false, user_id: "demo-viewer2", route_source: "teleport_arm" }),
      item({ grant_id: "mine1", kind: "import", target: "import:part", label: "Import Part", summary: "Import 3 row(s) into Part" }),
    ]);
    renderWithIntl(<ApprovalsView />);
    const sections = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(sections).toEqual(["Waiting for you", "Other users' requests"]);

    // named as the reader's language does, then by the id the audit log records
    const mine = screen.getByRole("region", { name: "Import Part (part)" });
    expect(within(mine).getByRole("button", { name: "Approve" })).toBeInTheDocument();
    expect(within(mine).getByText("demo-operator (you)")).toBeInTheDocument();

    const other = screen.getByRole("region", { name: "Write request · Make order (make_order)" });
    expect(within(other).getByText("Requested by demo-viewer2")).toBeInTheDocument();
    expect(within(other).queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(within(other).getByRole("button", { name: "Reject" })).toBeInTheDocument();
    // a route source this build does not know is still shown, verbatim
    expect(within(other).getByText("teleport_arm")).toBeInTheDocument();
  });

  it("counts down from the time left on the server's clock, whatever this computer's clock says", async () => {
    // a computer clock an hour fast reads `expires_at` as long gone: the server says 90 s are left
    listed([
      item({
        grant_id: "g1",
        created_at: new Date(Date.now() - 3_600_000 - 810_000).toISOString(),
        expires_at: new Date(Date.now() - 3_600_000 + 90_000).toISOString(),
        expires_in_s: 90,
      }),
    ]);
    renderWithIntl(<ApprovalsView />);
    const card = screen.getByRole("region", { name: /make_order/ });
    expect(await within(card).findByText(/^Expires in 1:(30|29)$/)).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Approve" })).toBeEnabled();
  });

  it("names a card by its id alone when the server gave no label", () => {
    listed([item({ grant_id: "g1", label: "" }), item({ grant_id: "g2", kind: "import", target: "import:part", label: "" })]);
    renderWithIntl(<ApprovalsView />);
    expect(screen.getByRole("region", { name: "Write request · make_order" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Import · part" })).toBeInTheDocument();
  });

  it("keeps Approve live when the countdown runs out, asks the server, and lets the expired card linger once it is gone", async () => {
    vi.useFakeTimers({ now: NOW });
    listed([
      item({
        grant_id: "g1",
        created_at: new Date(NOW - 898_000).toISOString(),
        expires_at: new Date(NOW + 2_000).toISOString(),
        expires_in_s: 2,
      }),
    ]);
    const view = renderWithIntl(<ApprovalsView />);
    const card = screen.getByRole("region", { name: /make_order/ });
    await act(() => vi.advanceTimersByTimeAsync(1));
    expect(within(card).getByText("Expires in 0:02")).toBeInTheDocument();
    refresh().mockClear();

    // the local clock says time is up: only the server may say it expired
    await act(() => vi.advanceTimersByTimeAsync(2_100));
    expect(within(card).getByText("Expires in 0:00")).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Approve" })).toBeEnabled();
    expect(refresh()).toHaveBeenCalledTimes(1);

    // the refreshed list no longer has it: the provider marks it, and it stays a moment, saying so
    act(() => approvals.value.store.depart("g1", true));
    listed([]);
    view.rerender(<ApprovalsView />);
    const expired = screen.getByRole("region", { name: /make_order/ });
    expect(within(expired).getByText("Expired")).toBeInTheDocument();
    expect(within(expired).getByText("This confirmation expired. It leaves the list on the next refresh.")).toBeInTheDocument();
    expect(within(expired).queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();

    await act(() => vi.advanceTimersByTimeAsync(EXPIRED_LINGER_MS + 10));
    expect(screen.queryByRole("region", { name: /make_order/ })).not.toBeInTheDocument();
    expect(refresh()).toHaveBeenCalledTimes(2);
  });

  it("approves in one step, with no second dialog, and shows the result inside the card", async () => {
    const user = userEvent.setup();
    listed([item({ grant_id: "g1" })]);
    const outcome: ApprovalOutcome = {
      grant_id: "g1",
      kind: "query",
      target: "make_order",
      status: "ok",
      problem: null,
      answer: "Draft order D-7 created.",
      answer_source: "template",
      narrative_stripped: 0,
      columns: ["order_id", "qty"],
      rows: [{ order_id: "D-7", qty: 200 }],
      aggregates: {},
      provenance: { tool: "make_order", label: "Make order", row_count: 1, as_of: "2026-10-26T08:00:00Z", scope: "" },
      audit_ids: [5, 6],
      import_result: null,
      trace: [],
    };
    apiMock.approve.mockResolvedValue(outcome);
    const view = renderWithIntl(<ApprovalsView />);

    await user.click(screen.getByRole("button", { name: "Approve" }));
    expect(apiMock.approve).toHaveBeenCalledWith("g1");
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();

    // the provider drops the resolved item; the card stays with its result, still findable by what it was
    const card = await screen.findByRole("region", { name: /Approved.*make_order/ });
    listed([]);
    view.rerender(<ApprovalsView />);
    expect(within(card).getByText("The write ran.")).toBeInTheDocument();
    expect(within(card).getByText("Draft order D-7 created.")).toBeInTheDocument();
    expect(within(card).getByRole("cell", { name: "D-7" })).toBeInTheDocument();
    expect(within(card).getByRole("link", { name: "View audit record #6" })).toHaveAttribute("href", "/audit?id=6");
    expect(refresh()).toHaveBeenCalled();
  });

  it("rejects in one step, and says nothing was written", async () => {
    const user = userEvent.setup();
    listed([item({ grant_id: "g2" })]);
    apiMock.reject.mockResolvedValue({ grant_id: "g2", status: "rejected" });
    renderWithIntl(<ApprovalsView />);
    await user.click(screen.getByRole("button", { name: "Reject" }));
    expect(apiMock.reject).toHaveBeenCalledWith("g2");
    expect(await screen.findByText("Nothing was written.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View in Audit" })).toHaveAttribute("href", "/audit?grant_id=g2");
  });

  it("lets an approval already on its way finish when the window ends mid-request", async () => {
    vi.useFakeTimers({ now: NOW });
    listed([item({ grant_id: "g1", created_at: new Date(NOW - 897_000).toISOString(), expires_at: new Date(NOW + 3_000).toISOString() })]);
    let finish: (outcome: ApprovalOutcome) => void = () => undefined;
    apiMock.approve.mockReturnValue(new Promise<ApprovalOutcome>((resolve) => (finish = resolve)));
    const view = renderWithIntl(<ApprovalsView />);
    const card = screen.getByRole("region", { name: /make_order/ });
    fireEvent.click(within(card).getByRole("button", { name: "Approve" }));
    expect(apiMock.approve).toHaveBeenCalledWith("g1");

    // the countdown reaches zero while the request is still out: the card keeps waiting for it
    await act(() => vi.advanceTimersByTimeAsync(4_000));
    expect(within(card).queryByText("Expired")).not.toBeInTheDocument();
    expect(within(card).getByRole("button", { name: /Approving/ })).toHaveAttribute("aria-disabled", "true");

    // the server ran it, and the refreshed list no longer has it
    act(() => approvals.value.store.depart("g1", true)); // ignored: our own request is out
    listed([]);
    await act(async () => {
      finish({ ...OUTCOME, grant_id: "g1" });
    });
    view.rerender(<ApprovalsView />);
    expect(screen.getByText("The write ran.")).toBeInTheDocument();
    await act(() => vi.advanceTimersByTimeAsync(EXPIRED_LINGER_MS * 2));
    expect(screen.getByText("The write ran.")).toBeInTheDocument();
  });

  it("keeps a confirmation that expired on approve on screen, saying so, before it leaves", async () => {
    vi.useFakeTimers({ now: NOW });
    listed([item({ grant_id: "g1" })]);
    apiMock.approve.mockRejectedValue(
      new ApiError({ status: 410, code: "approval_expired", message: "This confirmation has expired; ask again.", kind: "http" }),
    );
    const view = renderWithIntl(<ApprovalsView />);
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(refresh()).toHaveBeenCalled(); // after every attempt, which drops it from the list

    listed([]);
    refresh().mockClear();
    view.rerender(<ApprovalsView />);
    const card = screen.getByRole("region", { name: /make_order/ });
    expect(within(card).getByText("Expired")).toBeInTheDocument();
    expect(within(card).getByText("This confirmation expired. It leaves the list on the next refresh.")).toBeInTheDocument();

    await act(() => vi.advanceTimersByTimeAsync(EXPIRED_LINGER_MS - 500));
    expect(screen.getByRole("region", { name: /make_order/ })).toBeInTheDocument();
    await act(() => vi.advanceTimersByTimeAsync(600));
    expect(screen.queryByRole("region", { name: /make_order/ })).not.toBeInTheDocument();
    expect(refresh()).toHaveBeenCalledTimes(1);
  });

  it("drops a card resolved elsewhere without a trace", () => {
    listed([item({ grant_id: "g1" })]);
    const view = renderWithIntl(<ApprovalsView />);
    act(() => approvals.value.store.depart("g1", false));
    listed([]);
    view.rerender(<ApprovalsView />);
    expect(screen.queryByRole("region", { name: /make_order/ })).not.toBeInTheDocument();
    expect(screen.getByText("Nothing is waiting for approval")).toBeInTheDocument();
  });
});
