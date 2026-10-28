import { expect, test } from "@playwright/test";

import { ask, openTrace, primaryNav, signIn } from "./support";

test("create_po waits for approval, runs once approved, and the audit log replays it", async ({ page }) => {
  // a quantity of its own, so this run's request is the one found in Approvals and Audit
  const qty = 1_000 + Math.floor(Math.random() * 90_000);
  await signIn(page, "operator");

  // 1. Hub routes the write and holds it for a one-time confirmation
  const turn = await ask(page, `Create a purchase order for ${qty} units of SKU-10042`);
  // what waits, by name: its label, then the tool's id
  const held = turn.getByRole("region", { name: /^Approval required · .+ \(create_po\)$/ });
  await expect(held).toContainText(`Quantity: ${qty};`);
  await expect(held.getByRole("button", { name: "Approve" })).toBeEnabled();
  const steps = await openTrace(turn);
  await expect(steps.getByText("Write held until you approve it")).toBeVisible();

  // 2. Approvals: approve it there in one step (approving is the confirmation), and it runs
  await primaryNav(page).getByRole("link", { name: /^Approvals/ }).click();
  await expect(page).toHaveURL(/\/approvals$/);
  // named by its label, then the tool's id; once resolved, the receipt heads that name
  const card = page.getByRole("region", { name: /Write request · .+ \(create_po\)$/ }).filter({ hasText: `Quantity: ${qty};` });
  await card.getByRole("button", { name: "Approve" }).click();
  await expect(card.getByText("The write ran.", { exact: true })).toBeVisible();
  await expect(page.getByRole("alertdialog")).toHaveCount(0);
  // the pressed button is gone; focus is on the receipt that heads the card
  await expect(card.getByRole("heading", { name: /^Approved by demo-operator/ })).toBeFocused();
  await expect(card).toContainText(/Draft PO-\d+ created for SKU-10042/);

  // 3. Audit: the record the run left, with its trace replayed and its request chain
  await card.getByRole("link", { name: /^View audit record #\d+$/ }).click();
  await expect(page).toHaveURL(/\/audit\?id=\d+$/);
  const entryId = new URL(page.url()).searchParams.get("id");
  const drawer = page.getByRole("dialog", { name: `Entry #${entryId}` });
  const replay = drawer.getByRole("list", { name: "Reasoning trace" });
  await expect(replay.getByText("Permission check passed; your approval was used")).toBeVisible();
  await expect(replay.getByText("Ran deterministic tool (1 row)")).toBeVisible();
  const chain = drawer.getByRole("list", { name: "Request chain" });
  await expect(chain.getByRole("listitem")).toHaveText([/^Needs approval/, /^Started/, /^OK/]);

  // 4. The log narrowed to that request: intent, start and outcome, newest first
  await drawer.getByRole("button", { name: "Close" }).click();
  await expect(drawer).toBeHidden();
  const log = page.getByRole("table", { name: "Audit log" });
  const row = log.getByRole("row").filter({ has: page.getByRole("cell", { name: `#${entryId}`, exact: true }) });
  await row.getByRole("button", { name: /^Show every record of request / }).click();
  await expect(page).toHaveURL(/request_id=/);
  const outcomes = log.getByRole("row").filter({ hasText: "create_po" });
  await expect(outcomes).toHaveCount(3);
  await expect(outcomes.nth(0)).toContainText("OK");
  await expect(outcomes.nth(1)).toContainText("Started");
  await expect(outcomes.nth(2)).toContainText("Needs approval");

  // 5. Back in Hub, the card reads as resolved, the same as in Approvals: no live buttons
  await primaryNav(page).getByRole("link", { name: "Hub" }).click();
  await expect(page).toHaveURL(/\/hub$/);
  const resolved = turn.getByRole("region", { name: /^Approved by demo-operator/ });
  await expect(resolved).toContainText("The write ran.");
  await expect(turn.getByRole("button", { name: "Approve" })).toHaveCount(0);
});
