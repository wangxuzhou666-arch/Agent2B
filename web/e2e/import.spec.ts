import path from "node:path";

import { expect, test } from "@playwright/test";

import { EXAMPLES, primaryNav, signIn } from "./support";

test("imports a sample CSV: preview, confirm, approve, then the rows are in Data", async ({ page }) => {
  await signIn(page, "operator");
  await primaryNav(page).getByRole("link", { name: "Import" }).click();

  // 1. Upload: target and file
  await page.getByRole("combobox", { name: "Import into" }).selectOption({ label: "SKU › Packaging" });
  await page.getByLabel("Choose file", { exact: true }).setInputFiles(path.join(EXAMPLES, "packaging_sample.csv"));

  // 2. Preview: every row valid, the Chinese header mapped like the English ones; nothing written yet
  await expect(page.getByRole("heading", { name: "Check the preview" })).toBeVisible();
  await expect(page.getByText("packaging_sample.csv", { exact: true })).toBeVisible();
  await expect(page.getByText(/^20 rows · 20 valid$/)).toBeVisible();
  const mapping = page.getByRole("list", { name: "Column mapping" });
  await expect(mapping.getByRole("listitem").filter({ hasText: /^物料编码/ })).toContainText("sku_id");
  await expect(page.getByRole("cell", { name: "SKU-25001", exact: true })).toBeVisible();

  // 3. Confirm: the one-time approval bound to this file
  await page.getByRole("button", { name: "Import 20 rows" }).click();
  await expect(page.getByRole("heading", { name: "Confirm the write" })).toBeVisible();
  const card = page.getByRole("region", { name: "Approval required" });
  await expect(card).toContainText("Import 20 row(s) into Packaging from packaging_sample.csv");
  await card.getByRole("button", { name: "Approve" }).click();

  // 4. Done, then the rows in Data
  await expect(page.getByRole("heading", { name: "Imported" })).toBeVisible();
  await expect(page.getByText("20 rows imported into SKU › Packaging")).toBeVisible();
  await page.getByRole("link", { name: "View in Data" }).click();
  await expect(page).toHaveURL(/\/data\?entity=packaging/);
  await expect(page.getByRole("tab", { name: "Packaging", selected: true })).toBeVisible();

  const filters = page.getByRole("form", { name: "Filter records" });
  await filters.getByRole("combobox", { name: "Field" }).selectOption({ label: "SKU ID" });
  await filters.getByRole("textbox", { name: "Value for SKU ID" }).fill("SKU-25001");
  await filters.getByRole("button", { name: "Add filter" }).click();
  await expect(page).toHaveURL(/f\.sku_id=SKU-25001/);
  const records = page.getByRole("table", { name: "Packaging records" });
  await expect(records.getByRole("button", { name: "Open record SKU-25001" })).toBeVisible();
  await expect(records.getByRole("cell", { name: "Corrugated Box Large" })).toBeVisible();
  await expect(records.getByRole("row")).toHaveCount(2); // header + the one imported row
});
