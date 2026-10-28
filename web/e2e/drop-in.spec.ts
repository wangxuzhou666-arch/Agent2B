import { expect, test } from "@playwright/test";

import { primaryNav, signIn } from "./support";

// examples/plugins/cold_chain.py: a category added without touching core or console code
test("a drop-in category shows up as a Data tab and as an Import target", async ({ page }) => {
  await signIn(page, "operator");

  await primaryNav(page).getByRole("link", { name: "Data" }).click();
  await page.getByRole("button", { name: /^SKU/ }).click();
  const tabs = page.getByRole("tablist", { name: "SKU categories" });
  await tabs.getByRole("tab", { name: "Cold-Chain Item" }).click();
  await expect(page).toHaveURL(/category=cold_chain_item/);
  await expect(tabs.getByRole("tab", { name: "Cold-Chain Item" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByText("A temperature-controlled item with a limited shelf life.")).toBeVisible();
  // its own attributes become columns, units in the header
  const records = page.getByRole("table", { name: "Cold-Chain Item records" });
  await expect(records.getByRole("columnheader", { name: /Storage Temp \(C\)/ })).toBeVisible();
  await expect(records.getByRole("columnheader", { name: /Shelf Life \(days\)/ })).toBeVisible();

  await primaryNav(page).getByRole("link", { name: "Import" }).click();
  const target = page.getByRole("combobox", { name: "Import into" });
  await target.selectOption({ label: "SKU › Cold-Chain Item" });
  await expect(target).toHaveValue("cold_chain_item");
  // the columns this target reads include the plugin's fields
  await expect(page.getByRole("listitem").filter({ hasText: /^Shelf Life \(days\)/ })).toBeVisible();
});
