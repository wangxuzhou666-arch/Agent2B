// @vitest-environment node
import { describe, expect, it } from "vitest";

import { ApiError, toFailure, type ApprovalOutcome, type ApprovalRef, type ImportPreviewOut } from "./api";
import type { ApprovalState } from "./approvals";
import { afterApproval, cellErrors, checkFile, initialWizard, uploadIssue, wizardReducer, type WizardState } from "./imports";

const httpError = (status: number, code: string, message = "server says so") =>
  new ApiError({ status, code, message, kind: "http" });

const preview = (over: Partial<ImportPreviewOut> = {}): ImportPreviewOut => ({
  entity: "part",
  file_type: "csv",
  encoding: "utf-8",
  mapping: [],
  unmapped: [],
  rows: [],
  valid_count: 3,
  error_count: 1,
  digest: "d1",
  grant: null,
  ...over,
});

describe("uploadIssue", () => {
  it("maps the server's stable upload codes", () => {
    expect(uploadIssue(httpError(413, "too_large")).key).toBe("tooLarge");
    expect(uploadIssue(httpError(413, "payload_too_large")).key).toBe("tooLarge");
    expect(uploadIssue(httpError(400, "file_type")).key).toBe("fileType");
    expect(uploadIssue(httpError(400, "zip_bomb")).key).toBe("zipBomb");
    expect(uploadIssue(httpError(403, "forbidden")).key).toBe("forbidden");
    expect(uploadIssue(httpError(409, "digest_mismatch")).key).toBe("digestMismatch");
    expect(uploadIssue(httpError(409, "grant_invalid")).key).toBe("grantInvalid");
    expect(uploadIssue(httpError(410, "approval_expired")).key).toBe("expired");
    expect(uploadIssue(httpError(404, "not_found")).key).toBe("notFound");
  });

  it("keeps the server's sentence where it names columns or limits", () => {
    const issue = uploadIssue(httpError(400, "missing_columns", "required column(s) not found: ['Item ID']"));
    expect(issue).toEqual({ key: "missingColumns", detail: "required column(s) not found: ['Item ID']", code: "missing_columns" });
    expect(uploadIssue(httpError(409, "digest_mismatch")).detail).toBeNull();
  });

  it("falls back on the status, then on a generic message", () => {
    expect(uploadIssue(httpError(413, "something_new")).key).toBe("tooLarge");
    expect(uploadIssue(httpError(403, "role_gate")).key).toBe("forbidden");
    expect(uploadIssue(httpError(400, "brand_new_code", "odd file"))).toMatchObject({ key: "generic", detail: "odd file" });
    expect(uploadIssue(new ApiError({ status: 0, code: "network_error", message: "x", kind: "network" })).key).toBe("network");
    // a gateway that could not reach the API is the network too, not the server's verdict
    expect(uploadIssue(httpError(502, "http_502", "Bad Gateway"))).toEqual({ key: "network", detail: null, code: "http_502" });
    expect(uploadIssue(httpError(500, "internal_error", "boom"))).toMatchObject({ key: "generic", detail: "boom" });
  });
});

describe("checkFile", () => {
  const MB = 1024 * 1024;
  it("fails fast on type, emptiness and size, case-insensitively", () => {
    expect(checkFile({ name: "notes.txt", size: 10 }, MB, [".csv", ".xlsx"])?.key).toBe("fileType");
    expect(checkFile({ name: "rows.CSV", size: 0 }, MB, [".csv", ".xlsx"])?.key).toBe("empty");
    expect(checkFile({ name: "rows.xlsx", size: MB + 1 }, MB, [".csv", ".xlsx"])?.key).toBe("tooLarge");
    expect(checkFile({ name: "rows.csv", size: MB }, MB, [".csv", ".xlsx"])).toBeNull();
  });
});

describe("cellErrors", () => {
  const fields = [
    { id: "unit", label: "Unit" },
    { id: "unit_cost", label: "Unit Cost" },
    { id: "name", label: "Name" },
  ];
  it("attributes '<label>: problem' to the longest matching label, the rest to the row", () => {
    const result = cellErrors(
      { errors: ["Unit Cost: must be a number", "Name: required", "duplicate key; first seen in row 2"] },
      fields,
    );
    expect(result.byField).toEqual({ unit_cost: ["Unit Cost: must be a number"], name: ["Name: required"] });
    expect(result.row).toEqual(["duplicate key; first seen in row 2"]);
  });
});

describe("wizardReducer", () => {
  const file = { name: "parts.csv", size: 1200 };
  const approval: ApprovalRef = {
    grant_id: "g1",
    target: "import:part",
    summary: "Import 3 row(s) into Part",
    label: "Import Part",
    expires_at: "2026-10-26T09:00:00Z",
    expires_in_s: 900,
  };
  const deadline = { endsAt: 900_000, windowMs: 900_000 };

  it("walks Upload → Preview → Confirm → Done", () => {
    let state: WizardState = initialWizard(null);
    state = wizardReducer(state, { type: "uploadStart" });
    expect(state).toMatchObject({ step: "upload", busy: false }); // no target yet: ignored
    state = wizardReducer(state, { type: "selectEntity", entityId: "part" });
    state = wizardReducer(state, { type: "uploadStart" });
    expect(state).toMatchObject({ step: "upload", busy: true, entityId: "part" });
    state = wizardReducer(state, { type: "uploadOk", file, preview: preview() });
    expect(state).toMatchObject({ step: "preview", committing: false });
    state = wizardReducer(state, { type: "commitStart" });
    expect(state).toMatchObject({ step: "preview", committing: true });
    state = wizardReducer(state, { type: "needsConfirmation", approval, deadline });
    expect(state).toMatchObject({ step: "confirm", approval, deadline });
    state = wizardReducer(state, {
      type: "done",
      result: { entity: "part", written: 3, skipped: 1, digest: "d1", audit_id: 42 },
    });
    expect(state).toMatchObject({ step: "done", result: { written: 3, audit_id: 42 }, file });
  });

  it("takes the same file previewed again (in another language) only over the preview of those very bytes", () => {
    const shown = wizardReducer(wizardReducer(initialWizard("part"), { type: "uploadStart" }), {
      type: "uploadOk",
      file,
      preview: preview(),
    });
    const reworded = preview({
      mapping: [{ column: 0, header: "物料编码", field_id: "sku_id", label: "物料编码" }],
      rows: [{ row: 2, values: {}, errors: ["数量：必须是数字"], field_errors: [], action: null }],
    });
    expect(wizardReducer(shown, { type: "previewAgain", preview: reworded })).toEqual({ ...shown, preview: reworded });
    // other bytes, another target, a commit under way, another step: the preview on screen stands
    expect(wizardReducer(shown, { type: "previewAgain", preview: preview({ digest: "d2" }) })).toBe(shown);
    expect(wizardReducer(shown, { type: "previewAgain", preview: preview({ entity: "chilled" }) })).toBe(shown);
    const committing = wizardReducer(shown, { type: "commitStart" });
    expect(wizardReducer(committing, { type: "previewAgain", preview: reworded })).toBe(committing);
    const confirm = wizardReducer(committing, { type: "needsConfirmation", approval, deadline });
    expect(wizardReducer(confirm, { type: "previewAgain", preview: reworded })).toBe(confirm);
  });

  it("keeps the user on Upload with the reason when the upload fails", () => {
    let state = wizardReducer(initialWizard("part"), { type: "uploadStart" });
    state = wizardReducer(state, { type: "uploadFail", issue: { key: "tooLarge", detail: null, code: "too_large" } });
    expect(state).toMatchObject({ step: "upload", busy: false, issue: { key: "tooLarge" } });
  });

  it("cannot commit a preview with no valid rows, and ignores out-of-step actions", () => {
    const start = wizardReducer(wizardReducer(initialWizard("part"), { type: "uploadStart" }), {
      type: "uploadOk",
      file,
      preview: preview({ valid_count: 0, error_count: 4 }),
    });
    expect(wizardReducer(start, { type: "commitStart" })).toBe(start);
    expect(wizardReducer(initialWizard("part"), { type: "needsConfirmation", approval, deadline }).step).toBe("upload");
  });

  it("restarts from any step, keeping the target and saying why", () => {
    const preview1 = wizardReducer(wizardReducer(initialWizard("part"), { type: "uploadStart" }), {
      type: "uploadOk",
      file,
      preview: preview(),
    });
    const confirm = wizardReducer(preview1, { type: "needsConfirmation", approval, deadline });
    expect(wizardReducer(confirm, { type: "restart", notice: "rejected" })).toEqual({
      step: "upload",
      entityId: "part",
      busy: false,
      issue: null,
      notice: "rejected",
    });
  });
});

describe("afterApproval", () => {
  const outcome = (over: Partial<ApprovalOutcome> = {}): ApprovalOutcome => ({
    grant_id: "g1",
    kind: "import",
    target: "import:part",
    status: "ok",
    problem: null,
    answer: "",
    answer_source: "system",
    narrative_stripped: 0,
    columns: [],
    rows: [],
    aggregates: {},
    provenance: null,
    audit_ids: [42],
    import_result: { entity: "part", written: 3, skipped: 1, digest: "d1", audit_id: 42 },
    trace: [],
    ...over,
  });
  const approved = (over: Partial<ApprovalOutcome> = {}): ApprovalState => ({
    phase: "approved",
    outcome: outcome(over),
    at: 0,
    by: null,
    elapsedMs: 5,
    source: "reply",
  });
  const failed = (error: ApiError, inDoubt = false): ApprovalState => ({
    phase: "failed",
    action: "approve",
    error: toFailure(error),
    at: 0,
    inDoubt,
  });

  it("goes to Done with the import's result", () => {
    expect(afterApproval(approved())).toEqual({ type: "done", result: outcome().import_result });
  });

  it("starts over, saying why, when the confirmation can no longer be used", () => {
    expect(afterApproval(approved({ status: "blocked", problem: "the file changed", import_result: null }))).toEqual({
      type: "restart",
      issue: { key: "generic", detail: "the file changed", code: "blocked" },
    });
    expect(afterApproval({ phase: "rejected", at: 0, by: null, source: "reply" })).toEqual({ type: "restart", notice: "rejected" });
    expect(afterApproval({ phase: "expired", at: 0, source: "reply" })).toEqual({
      type: "restart",
      issue: { key: "expired", detail: null, code: "approval_expired" },
    });
    expect(afterApproval(failed(httpError(409, "digest_mismatch")))).toEqual({
      type: "restart",
      issue: { key: "digestMismatch", detail: null, code: "digest_mismatch" },
    });
  });

  it("starts over on an expired approve the same way whether the reply or the audit log reported it", () => {
    // `audit`: the approve's reply was lost, and the audit log holds the server's 410 to it
    const expiredOnApprove = { type: "restart", issue: { key: "expired", detail: null, code: "approval_expired" } };
    expect(afterApproval({ phase: "expired", at: 0, source: "audit" })).toEqual(expiredOnApprove);
    expect(afterApproval({ phase: "expired", at: 0, source: "reply" })).toEqual(expiredOnApprove);
  });

  it("stays while it is open, lost in transit, in doubt, or resolved in a way the card explains", () => {
    const stays: ApprovalState[] = [
      { phase: "pending" },
      { phase: "approving", attempt: 1, inDoubt: false },
      { phase: "checking", attempt: 2, action: "approve" },
      failed(new ApiError({ status: 0, code: "network_error", message: "x", kind: "network" }), true),
      failed(httpError(500, "internal_error"), true),
      { phase: "expired", at: 0, source: "list" },
      { phase: "elsewhere", at: 0 },
      { phase: "unconfirmed", started: true, at: 0 },
    ];
    for (const state of stays) expect(afterApproval(state)).toBeNull();
  });
});
