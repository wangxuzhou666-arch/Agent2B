import { expect, test, type APIRequestContext, type Locator, type Page } from "@playwright/test";

import { ask, openTrace, primaryNav, signInForToken } from "../support";

/**
 * The documented exploration case (docs/EVIDENCE.md) as the console shows it. No fixed flow
 * covers the request, so the router hands it over. Pi runs it; the mock's script supplies the
 * plan. Every call it makes goes through the same gate as a fixed flow, and the one write it
 * ends on waits as an approval card bound to its exact parameters.
 * tests/cases/test_exploration_case.py asserts the same run through the HTTP API.
 *
 * The model is the zero-key mock, which follows a fixed script for this one request
 * (src/twotrack/providers/mock_script.py): three reads, then one create_po proposal. The script
 * does the planning, so nothing here shows how any model plans or explores. What these specs
 * check is what the console makes of the run: the trace, the approval card, the write and its
 * audit records.
 *
 * Run on its own API (playwright.explore.config.ts): open tasks on, the Pi explorer.
 */

// Word for word the request the mock scripts; any other wording gets no script
const EXPLORATION_CASE =
  "Several orders from SUP-001 are slipping, SKU-10053 among them. Work out what that puts at risk and propose what to do.";
// what the script proposes, as the card words it
const PROPOSAL = "SKU: SKU-10053; Quantity: 74; Supplier: SUP-007";
// a Node worker starts for each question, so the first answer is the slow one
const RUN_TIMEOUT = 60_000;

/** The run's trace, line by line: the hand-over, Pi and its tools, three reads, the held write. */
const RUN_TRACE = [
  /^\d+ tools? available to your role/,
  /^No single report fits; planning with the agent loop/,
  /^Handed over to exploration: no report covers the question/,
  // the tools it was given include everything the script calls
  /^Exploration started \(Pi\) with \d+ tools: (?=.*\bcreate_po\b)(?=.*\blate_shipments\b)(?=.*\breorder_suggestion\b)(?=.*\bsupplier_scorecard\b)/,
  /^Agent turn 1: requested late_shipments/,
  /^Permission and precondition check passed/,
  /^Ran deterministic tool \(\d+ rows?\)/,
  /^Agent turn 2: requested reorder_suggestion, supplier_scorecard/,
  /^Permission and precondition check passed/,
  /^Ran deterministic tool \(\d+ rows?\)/,
  /^Permission and precondition check passed/,
  /^Ran deterministic tool \(\d+ rows?\)/,
  /^Agent turn 3: requested create_po/,
  /^Write held until you approve it/,
  /^Agent stopped: needs confirmation/,
  /^Answer assembled/,
];

type Session = Awaited<ReturnType<typeof signInForToken>>;

/** Ask the case and wait for its run to end at the approval card of the write it proposed. */
async function askCase(page: Page): Promise<{ turn: Locator; held: Locator; grantId: string }> {
  const turn = await ask(page, EXPLORATION_CASE);
  // Hub names the write as the fixed flow's card does, label then id. The routed target is null
  // for a write the explorer proposed, so the id comes from the approval itself (its target).
  const held = turn.getByRole("region", { name: /^Approval required · Create purchase order \(create_po\)$/ });
  await expect(held).toBeVisible({ timeout: RUN_TIMEOUT });
  await expect(held).toContainText(PROPOSAL);
  const grantId = await held.getByText(/^[0-9a-f]{16}$/).innerText();
  return { turn, held, grantId };
}

/** Purchase orders as the API reports them: what was written, whatever the console shows. */
async function purchaseOrders(
  request: APIRequestContext,
  { api, token }: Session,
  query: string,
): Promise<{ total: number; items: Record<string, unknown>[] }> {
  const response = await request.get(`${api}/api/entities/purchase_order/records?${query}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  expect(response.status()).toBe(200);
  return (await response.json()) as { total: number; items: Record<string, unknown>[] };
}

const orderCount = async (request: APIRequestContext, session: Session) =>
  (await purchaseOrders(request, session, "page_size=1")).total;

test.describe("exploration case: scripted mock model, Pi worker", () => {
  test("approve: the run holds a write for approval, approving runs it once, the audit chains it", async ({
    page,
    request,
  }) => {
    const session = await signInForToken(page, "operator");
    const before = await orderCount(request, session);

    // 1. Hub: handed over to exploration, run in Pi, stopped at one write waiting for approval
    const { turn, held, grantId } = await askCase(page);
    await expect(held.getByRole("button", { name: "Approve" })).toBeEnabled();
    // the run ended as it should: no error line, no stop-reason label, no failed model call
    await expect(turn.getByRole("alert")).toHaveCount(0);
    await expect(turn.getByText("Stop reason:")).toHaveCount(0);
    await expect(turn.getByText(/Model calls? failed/)).toHaveCount(0);
    const steps = await openTrace(turn);
    await expect(steps.getByRole("listitem")).toHaveText(RUN_TRACE);
    await expect(steps.getByTitle("Agent loop").first()).toHaveText(/^agent/);
    // and the console says there is no model behind it
    await expect(page.getByRole("banner").getByRole("button", { name: "No model" })).toBeVisible();

    // 2. Approvals: the same request, bound to the same parameters; approving is the confirmation
    await primaryNav(page).getByRole("link", { name: /^Approvals/ }).click();
    await expect(page).toHaveURL(/\/approvals$/);
    const card = page
      .getByRole("region", { name: /^Write request · Create purchase order \(create_po\)$/ })
      .filter({ hasText: grantId });
    await expect(card).toContainText(PROPOSAL);
    await expect(card.getByTitle("Agent loop")).toBeVisible();
    await card.getByRole("button", { name: "Approve" }).click();
    // once resolved the card no longer shows its grant: it is the one this window resolved
    const receipt = page.getByRole("region", { name: /^Approved by demo-operator/ });
    await expect(receipt.getByText("The write ran.", { exact: true })).toBeVisible();
    await expect(page.getByRole("alertdialog")).toHaveCount(0);
    await expect(receipt).toContainText(/Draft PO-\d+ created for SKU-10053/);
    const created = receipt.getByRole("row").filter({ hasText: "SKU-10053" });
    for (const cell of ["SUP-007", "74", "draft"]) {
      await expect(created.getByRole("cell", { name: cell, exact: true })).toBeVisible();
    }
    // the API's own view: one more order, the draft the card was bound to
    const poId = /Draft (PO-\d+) created/.exec(await receipt.innerText())?.[1];
    if (poId === undefined) throw new Error("the receipt does not name the draft PO it created");
    expect(await orderCount(request, session)).toBe(before + 1);
    const { items } = await purchaseOrders(request, session, `po_id=${poId}`);
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({ sku_id: "SKU-10053", qty: 74, supplier_id: "SUP-007", status: "draft" });

    // 3. Audit: the record the run left, with the parameters it ran with, its trace replayed and
    // the whole chain of its request: three reads, then the write's request, start and outcome
    await receipt.getByRole("link", { name: /^View audit record #\d+$/ }).click();
    await expect(page).toHaveURL(/\/audit\?id=\d+$/);
    const entryId = new URL(page.url()).searchParams.get("id");
    const drawer = page.getByRole("dialog", { name: `Entry #${entryId}` });
    const params = drawer.getByRole("region", { name: "Parameters" });
    for (const value of ["SKU-10053", "74", "SUP-007"]) {
      await expect(params.getByText(value, { exact: true })).toBeVisible();
    }
    const replay = drawer.getByRole("list", { name: "Reasoning trace" });
    await expect(replay.getByText("Permission check passed; your approval was used")).toBeVisible();
    await expect(replay.getByText("Ran deterministic tool (1 row)")).toBeVisible();
    const chain = drawer.getByRole("list", { name: "Request chain" });
    await expect(chain.getByRole("listitem")).toHaveText([
      /^OKlate_shipments/,
      /^OKreorder_suggestion/,
      /^OKsupplier_scorecard/,
      /^Needs approvalcreate_po/,
      /^Startedcreate_po/,
      /^OKcreate_po/,
    ]);

    // 4. The log narrowed to that request: its six records, every one decided by the agent arm
    await drawer.getByRole("button", { name: "Close" }).click();
    await expect(drawer).toBeHidden();
    const log = page.getByRole("table", { name: "Audit log" });
    const row = log.getByRole("row").filter({ has: page.getByRole("cell", { name: `#${entryId}`, exact: true }) });
    await row.getByRole("button", { name: /^Show every record of request / }).click();
    await expect(page).toHaveURL(/request_id=/);
    await expect(log.getByRole("row")).toHaveCount(1 + 6); // header, then the records
    await expect(log.getByTitle("Agent loop")).toHaveCount(6);

    // 5. A second approval of the same grant is refused (409), nothing runs again, and the audit
    // log says so under the grant
    const again = await request.post(`${session.api}/api/approvals/${grantId}/approve`, {
      headers: { Authorization: `Bearer ${session.token}` },
    });
    expect(again.status()).toBe(409);
    expect(await again.json()).toMatchObject({ error: { code: "grant_used" } });
    expect(await orderCount(request, session)).toBe(before + 1);
    await page.goto(`/audit?grant_id=${grantId}`);
    const trail = page.getByRole("table", { name: "Audit log" }).getByRole("row").filter({ hasText: "create_po" });
    await expect(trail).toContainText(["Blocked", "OK", "Started", "Needs approval"]); // newest first
  });

  test("reject: rejecting the card writes nothing, and the audit shows the rejection", async ({ page, request }) => {
    const session = await signInForToken(page, "operator");
    const before = await orderCount(request, session);

    // 1. Hub: the same run, held at the same card
    const { turn, held, grantId } = await askCase(page);

    // 2. Reject it there: the card closes as rejected and says nothing was written
    await held.getByRole("button", { name: "Reject" }).click();
    const receipt = turn.getByRole("region", { name: /^Rejected by demo-operator/ });
    await expect(receipt).toContainText("Nothing was written.");
    await expect(receipt).toContainText(PROPOSAL);
    await expect(turn.getByRole("button", { name: /^(Approve|Reject)$/ })).toHaveCount(0);
    expect(await orderCount(request, session)).toBe(before);

    // 3. Audit under the grant: the request that asked, then the rejection, and no start or outcome
    await receipt.getByRole("link", { name: "View in Audit" }).click();
    await expect(page).toHaveURL(new RegExp(`/audit\\?grant_id=${grantId}$`));
    const records = page.getByRole("table", { name: "Audit log" }).getByRole("row").filter({ hasText: "create_po" });
    await expect(records).toContainText(["Blocked", "Needs approval"]); // newest first
    await records.first().getByRole("button", { name: /open entry #\d+$/ }).click();
    const drawer = page.getByRole("dialog", { name: /^Entry #\d+$/ });
    await expect(drawer.getByText("rejected", { exact: true })).toBeVisible(); // the problem recorded
    await expect(drawer.getByRole("list", { name: "Request chain" }).getByRole("listitem")).toHaveText([
      /^OKlate_shipments/,
      /^OKreorder_suggestion/,
      /^OKsupplier_scorecard/,
      /^Needs approvalcreate_po/,
      /^Blockedcreate_po/,
    ]);

    // 4. The API agrees: no order was written
    expect(await orderCount(request, session)).toBe(before);
  });
});
