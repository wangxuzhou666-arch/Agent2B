import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { AnchorHTMLAttributes } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  type ApprovalItem,
  type ApprovalOutcome,
  type EntityList,
  type ImportCommitOut,
  type ImportPreviewOut,
} from "@/lib/api";

import { approvalsValue } from "../../../test/approvals";
import { deferred } from "../../../test/deferred";
import { ENTITIES } from "../../../test/fixtures";
import { IntlProviders, renderWithIntl } from "../../../test/render";
import { ImportWizard } from "./import-wizard";

const refresh = vi.hoisted(() => vi.fn());
const approvals = vi.hoisted(() => ({ value: null as unknown as ReturnType<typeof approvalsValue> }));
vi.mock("@/components/providers/approvals-provider", () => ({ useApprovals: () => approvals.value }));
vi.mock("@/components/providers/auth-provider", () => ({
  useAuth: () => ({ session: { userId: "demo-operator", roles: ["operator"] } }),
}));
vi.mock("next/navigation", () => ({ useSearchParams: () => new URLSearchParams("entity=chilled") }));
vi.mock("next/link", () => ({
  default: ({ href, ...props }: AnchorHTMLAttributes<HTMLAnchorElement> & { href: string }) => <a href={href} {...props} />,
}));

const apiMock = vi.hoisted(() => ({
  listEntities: vi.fn(),
  previewImport: vi.fn(),
  commitImport: vi.fn(),
  approve: vi.fn(),
  reject: vi.fn(),
  listAudit: vi.fn(),
}));
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, ...apiMock } };
});

const preview: ImportPreviewOut = {
  entity: "chilled",
  file_type: "csv",
  encoding: "utf-8",
  mapping: [
    { column: 0, header: "Item ID", field_id: "item_id", label: "Item ID" },
    { column: 1, header: "Name", field_id: "name", label: "Name" },
  ],
  unmapped: [],
  rows: [
    { row: 2, values: { item_id: "I-1", name: "Ice Pack" }, errors: [], field_errors: [], action: "new" },
    {
      row: 3,
      values: { item_id: "I-2", name: null },
      errors: ["Name: required"],
      field_errors: [{ field_id: "name", message: "required" }],
      action: null,
    },
  ],
  valid_count: 1,
  error_count: 1,
  digest: "digest-1",
  grant: {
    grant_id: "g1",
    target: "import:chilled",
    summary: "Import 1 row(s) into Chilled Item from chilled.csv",
    label: "Import Chilled Item",
    expires_at: new Date(Date.now() + 9e5).toISOString(),
    expires_in_s: 900,
  },
};

const csv = (name = "chilled.csv", body = "Item ID,Name\nI-1,Ice Pack\n") => new File([body], name, { type: "text/csv" });

beforeEach(() => {
  refresh.mockReset();
  approvals.value = approvalsValue({ refresh });
  for (const fn of Object.values(apiMock)) fn.mockReset();
  apiMock.listEntities.mockResolvedValue({ items: ENTITIES });
  apiMock.previewImport.mockResolvedValue(preview);
  apiMock.commitImport.mockResolvedValue({
    status: "needs_confirmation",
    approval: {
      grant_id: "g1",
      target: "import:chilled",
      summary: "Import 1 row(s) into Chilled Item from chilled.csv",
      label: "Import Chilled Item",
      expires_at: new Date(Date.now() + 9e5).toISOString(),
      expires_in_s: 900,
    },
    result: null,
  });
});

afterEach(() => vi.useRealTimers());

const stepState = () =>
  within(screen.getByRole("list", { name: "Import steps" }))
    .getAllByRole("listitem")
    .map((li) => `${li.textContent}${li.getAttribute("aria-current") ? "*" : ""}`);

async function upload(user: ReturnType<typeof userEvent.setup>, file = csv()) {
  await user.upload(screen.getByLabelText("Choose file", { selector: "input" }), file);
}

describe("ImportWizard", () => {
  it("says it is loading to screen readers while the entities load, in both languages", () => {
    apiMock.listEntities.mockReturnValue(deferred<EntityList>().promise);
    const view = renderWithIntl(<ImportWizard />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading…");
    view.unmount();
    renderWithIntl(<ImportWizard />, { locale: "zh" });
    expect(screen.getByRole("status")).toHaveTextContent("加载中…");
  });

  it("walks Upload → Preview → Confirm → Done through the one-time approval", async () => {
    // nothing here waits on a clock: each server answer is handed over, then the screen is read once
    vi.useFakeTimers();
    const entities = deferred<EntityList>();
    const previewed = deferred<ImportPreviewOut>();
    const committed = deferred<ImportCommitOut>();
    const approved = deferred<ApprovalOutcome>();
    apiMock.listEntities.mockReturnValue(entities.promise);
    apiMock.previewImport.mockReturnValue(previewed.promise);
    apiMock.commitImport.mockReturnValue(committed.promise);
    apiMock.approve.mockReturnValue(approved.promise);
    renderWithIntl(<ImportWizard />);

    await act(async () => entities.resolve({ items: ENTITIES }));
    // the target from the link is preselected; categories sit under their root
    const target = screen.getByRole("combobox", { name: "Import into" });
    expect(target).toHaveValue("chilled");
    expect([...target.querySelectorAll("option")].map((o) => o.textContent)).toEqual([
      "Item",
      "Item › Part",
      "Item › Chilled Item",
      "Site",
    ]);
    expect(stepState()).toEqual(["1Upload*", "2Preview", "3Confirm", "4Done"]);

    fireEvent.change(screen.getByLabelText("Choose file", { selector: "input" }), { target: { files: [csv()] } });
    expect(apiMock.previewImport).toHaveBeenCalledWith("chilled", expect.any(File), expect.anything());
    await act(async () => previewed.resolve(preview));
    expect(screen.getByRole("heading", { name: "Check the preview" })).toHaveFocus();
    expect(stepState()).toEqual(["Upload (completed)", "2Preview*", "3Confirm", "4Done"]);

    fireEvent.click(screen.getByRole("button", { name: "Import 1 row" }));
    expect(apiMock.commitImport).toHaveBeenCalledWith("chilled", { digest: "digest-1" });
    await act(async () =>
      committed.resolve({
        status: "needs_confirmation",
        approval: {
          grant_id: "g1",
          target: "import:chilled",
          summary: "Import 1 row(s) into Chilled Item from chilled.csv",
          label: "Import Chilled Item",
          expires_at: new Date(Date.now() + 9e5).toISOString(),
          expires_in_s: 900,
        },
        result: null,
      }),
    );
    const card = screen.getByRole("region", { name: "Approval required · Import Chilled Item" });
    expect(within(card).getByText("Import 1 row(s) into Chilled Item from chilled.csv")).toBeInTheDocument();
    expect(refresh).toHaveBeenCalled(); // the sidebar badge learns about the new approval

    fireEvent.click(within(card).getByRole("button", { name: "Approve" }));
    expect(apiMock.approve).toHaveBeenCalledWith("g1");
    await act(async () =>
      approved.resolve({
        grant_id: "g1",
        kind: "import",
        target: "import:chilled",
        status: "ok",
        problem: null,
        answer: "Imported 1 row(s); 1 skipped.",
        answer_source: "system",
        narrative_stripped: 0,
        columns: [],
        rows: [],
        aggregates: {},
        provenance: null,
        audit_ids: [12],
        import_result: { entity: "chilled", written: 1, skipped: 1, digest: "digest-1", audit_id: 12 },
        trace: [],
      }),
    );
    expect(screen.getByText("1 row imported into Item › Chilled Item")).toBeInTheDocument();
    expect(screen.getByText("1 row with errors was skipped.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View in Data" })).toHaveAttribute("href", "/data?entity=chilled");
    expect(screen.getByRole("link", { name: "View in Audit" })).toHaveAttribute("href", "/audit?id=12");
    expect(stepState()).toEqual(["Upload (completed)", "Preview (completed)", "Confirm (completed)", "Done (completed)"]);

    fireEvent.click(screen.getByRole("button", { name: "Import another file" }));
    expect(screen.getByRole("heading", { name: "Choose a target and a file" })).toBeInTheDocument();
  });

  it("rejecting returns to Upload and says nothing was written", async () => {
    const user = userEvent.setup();
    apiMock.reject.mockResolvedValue({ grant_id: "g1", status: "rejected" });
    renderWithIntl(<ImportWizard />);
    await screen.findByRole("combobox", { name: "Import into" });
    await upload(user);
    await user.click(await screen.findByRole("button", { name: "Import 1 row" }));
    await user.click(within(await screen.findByRole("region", { name: /^Approval required/ })).getByRole("button", { name: "Reject" }));
    expect(await screen.findByText("Import cancelled. Nothing was written.")).toBeInTheDocument();
    expect(apiMock.reject).toHaveBeenCalledWith("g1");
  });

  it("checks the file in the browser before uploading", async () => {
    const user = userEvent.setup({ applyAccept: false });
    renderWithIntl(<ImportWizard />);
    await screen.findByRole("combobox", { name: "Import into" });
    await upload(user, csv("notes.txt"));
    expect(screen.getByRole("alert")).toHaveTextContent("This isn't a CSV or XLSX file the server can read.");
    expect(apiMock.previewImport).not.toHaveBeenCalled();
  });

  it.each([
    [new ApiError({ status: 413, code: "too_large", message: "the file exceeds 2097152 bytes", kind: "http" }), "The file is larger than 2 MB."],
    [new ApiError({ status: 403, code: "forbidden", message: "your role may not import data", kind: "http" }), "Your role may not import data."],
    [
      new ApiError({ status: 400, code: "missing_columns", message: "required column(s) not found: ['Item ID']", kind: "http" }),
      "Required columns are missing.required column(s) not found: ['Item ID']",
    ],
  ])("maps upload error %#", async (error, text) => {
    const user = userEvent.setup();
    apiMock.previewImport.mockRejectedValue(error);
    renderWithIntl(<ImportWizard />);
    await screen.findByRole("combobox", { name: "Import into" });
    await upload(user);
    expect(await screen.findByRole("alert")).toHaveTextContent(text);
    expect(screen.getByRole("heading", { name: "Choose a target and a file" })).toBeInTheDocument();
  });

  it("starts over with the reason when the confirmation can no longer be used", async () => {
    const user = userEvent.setup();
    apiMock.approve.mockRejectedValue(
      new ApiError({ status: 409, code: "digest_mismatch", message: "the file or its validation changed since the preview", kind: "http" }),
    );
    renderWithIntl(<ImportWizard />);
    await screen.findByRole("combobox", { name: "Import into" });
    await upload(user);
    await user.click(await screen.findByRole("button", { name: "Import 1 row" }));
    await user.click(within(await screen.findByRole("region", { name: /^Approval required/ })).getByRole("button", { name: "Approve" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The file or its validation changed since the preview. Upload it again.");
    expect(screen.getByRole("heading", { name: "Choose a target and a file" })).toBeInTheDocument();
  });

  it("starts over with the reason when a reject finds the confirmation already settled elsewhere", async () => {
    const user = userEvent.setup();
    apiMock.reject.mockRejectedValue(
      new ApiError({ status: 404, code: "not_found", message: "This confirmation was not found.", kind: "http" }),
    );
    renderWithIntl(<ImportWizard />);
    await screen.findByRole("combobox", { name: "Import into" });
    await upload(user);
    await user.click(await screen.findByRole("button", { name: "Import 1 row" }));
    await user.click(within(await screen.findByRole("region", { name: /^Approval required/ })).getByRole("button", { name: "Reject" }));
    expect(apiMock.reject).toHaveBeenCalledWith("g1");
    expect(await screen.findByRole("alert")).toHaveTextContent("The preview is no longer available. Upload the file again.");
    expect(screen.getByRole("heading", { name: "Choose a target and a file" })).toBeInTheDocument();
    expect(refresh).toHaveBeenCalled();
  });

  it("keeps the confirmation open to retry when a reject fails in transit", async () => {
    const user = userEvent.setup();
    apiMock.reject.mockRejectedValue(
      new ApiError({ status: 0, code: "network_error", message: "The server could not be reached.", kind: "network" }),
    );
    renderWithIntl(<ImportWizard />);
    await screen.findByRole("combobox", { name: "Import into" });
    await upload(user);
    await user.click(await screen.findByRole("button", { name: "Import 1 row" }));
    const card = await screen.findByRole("region", { name: /^Approval required/ });
    await user.click(within(card).getByRole("button", { name: "Reject" }));
    expect(await within(card).findByRole("alert")).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Reject" })).toBeEnabled();
    expect(screen.getByRole("heading", { name: "Confirm the write" })).toBeInTheDocument();
  });

  it("finishes from the audit log when the approval's reply was lost and a retry finds it used", async () => {
    const user = userEvent.setup();
    apiMock.approve
      .mockRejectedValueOnce(new ApiError({ status: 0, code: "network_error", message: "The server could not be reached.", kind: "network" }))
      .mockRejectedValueOnce(new ApiError({ status: 409, code: "grant_invalid", message: "the confirmation is invalid, expired or used", kind: "http" }));
    apiMock.listAudit.mockResolvedValue({
      items: [
        {
          id: 12,
          ts: "2026-10-26T08:00:05Z",
          user_id: "demo-operator",
          roles: ["operator"],
          target: "import:chilled",
          effect: "write",
          status: "ok",
          route_source: "explicit",
          params_digest: "digest-1",
          params: { filename: "chilled.csv", rows: 2, valid: 1, errors: 1 },
          row_count: 1,
          grant_id: "g1",
          problem: null,
          request_id: "r1",
          parent_id: 11,
          duration_ms: 3,
        },
      ],
      total: 1,
      page: 1,
      page_size: 50,
    });
    renderWithIntl(<ImportWizard />);
    await screen.findByRole("combobox", { name: "Import into" });
    await upload(user);
    await user.click(await screen.findByRole("button", { name: "Import 1 row" }));
    const card = await screen.findByRole("region", { name: /^Approval required/ });
    await user.click(within(card).getByRole("button", { name: "Approve" }));
    expect(await within(card).findByRole("alert")).toHaveTextContent("The connection failed before the server answered.");

    await user.click(within(card).getByRole("button", { name: "Approve" }));
    expect(await screen.findByText("1 row imported into Item › Chilled Item")).toBeInTheDocument();
    expect(apiMock.listAudit).toHaveBeenCalledWith({ grant_id: "g1", page_size: 50 });
    expect(screen.getByText("1 row with errors was skipped.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View in Audit" })).toHaveAttribute("href", "/audit?id=12");
  });

  it("counts the confirmation down from the time left on the server's clock, not from its expires_at", async () => {
    const user = userEvent.setup();
    apiMock.commitImport.mockResolvedValue({
      status: "needs_confirmation",
      // an `expires_at` long past on this computer's clock: only the server's 42 s count
      approval: { grant_id: "g1", target: "import:chilled", summary: "Import 1 row(s)", label: "Import Chilled Item", expires_at: "2000-01-01T00:00:00Z", expires_in_s: 42 },
      result: null,
    });
    renderWithIntl(<ImportWizard />);
    await screen.findByRole("combobox", { name: "Import into" });
    await upload(user);
    await user.click(await screen.findByRole("button", { name: "Import 1 row" }));
    const card = await screen.findByRole("region", { name: "Approval required · Import Chilled Item" });
    expect(await within(card).findByText(/^Expires in 0:4[12]$/)).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Approve" })).toBeEnabled();
  });

  it("previews the open file again, and fetches the targets again, when the console switches language", async () => {
    const user = userEvent.setup();
    const reworded: ImportPreviewOut = {
      ...preview,
      mapping: preview.mapping.map((m) => ({ ...m, label: m.field_id === "name" ? "名称" : "物料编号" })),
      rows: [preview.rows[0]!, { ...preview.rows[1]!, errors: ["名称：必填"] }],
    };
    apiMock.previewImport.mockResolvedValueOnce(preview).mockResolvedValueOnce(reworded);
    const view = render(
      <IntlProviders locale="en">
        <ImportWizard />
      </IntlProviders>,
    );
    await screen.findByRole("combobox", { name: "Import into" });
    const file = csv();
    await upload(user, file);
    expect(await screen.findByText("Name: required")).toBeInTheDocument();
    expect(apiMock.listEntities).toHaveBeenCalledTimes(1);

    view.rerender(
      <IntlProviders locale="zh">
        <ImportWizard />
      </IntlProviders>,
    );
    // the same bytes, worded again; still on Preview, nothing committed
    expect(await screen.findByText("名称：必填")).toBeInTheDocument();
    expect(apiMock.previewImport).toHaveBeenCalledTimes(2);
    expect(apiMock.previewImport.mock.calls[1]![1]).toBe(file);
    expect(apiMock.listEntities).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("heading", { name: "检查预览" })).toBeInTheDocument();
    expect(apiMock.commitImport).not.toHaveBeenCalled();
  });

  it("keeps the preview on screen when previewing it again fails", async () => {
    const user = userEvent.setup();
    apiMock.previewImport
      .mockResolvedValueOnce(preview)
      .mockRejectedValueOnce(new ApiError({ status: 0, code: "network_error", message: "The server could not be reached.", kind: "network" }));
    const view = render(
      <IntlProviders locale="en">
        <ImportWizard />
      </IntlProviders>,
    );
    await screen.findByRole("combobox", { name: "Import into" });
    await upload(user);
    await screen.findByText("Name: required");

    view.rerender(
      <IntlProviders locale="zh">
        <ImportWizard />
      </IntlProviders>,
    );
    await act(async () => undefined);
    expect(apiMock.previewImport).toHaveBeenCalledTimes(2);
    expect(screen.getByText("Name: required")).toBeInTheDocument(); // same verdicts, only in the old words
    expect(screen.getByRole("heading", { name: "检查预览" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("does not preview again once in Confirm, and words the card as the approvals list does after a switch", async () => {
    const user = userEvent.setup();
    const view = render(
      <IntlProviders locale="en">
        <ImportWizard />
      </IntlProviders>,
    );
    await screen.findByRole("combobox", { name: "Import into" });
    await upload(user);
    await user.click(await screen.findByRole("button", { name: "Import 1 row" }));
    await screen.findByRole("region", { name: "Approval required · Import Chilled Item" });

    // the provider fetched the list again in the new language: the same confirmation, worded for the reader
    const listed: ApprovalItem = {
      grant_id: "g1",
      kind: "import",
      user_id: "demo-operator",
      target: "import:chilled",
      label: "导入冷藏物品",
      summary: "从 chilled.csv 向冷藏物品导入 1 行",
      route_source: "explicit",
      created_at: new Date(Date.now() - 60_000).toISOString(),
      expires_at: new Date(Date.now() + 840_000).toISOString(),
      expires_in_s: 840,
      mine: true,
    };
    approvals.value = { ...approvals.value, items: [listed], count: 1 };
    view.rerender(
      <IntlProviders locale="zh">
        <ImportWizard />
      </IntlProviders>,
    );
    const card = await screen.findByRole("region", { name: "需要审批 · 导入冷藏物品" });
    expect(within(card).getByText("从 chilled.csv 向冷藏物品导入 1 行")).toBeInTheDocument();
    expect(apiMock.previewImport).toHaveBeenCalledTimes(1);
  });
});
