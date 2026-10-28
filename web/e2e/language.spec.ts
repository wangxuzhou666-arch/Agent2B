import { expect, test } from "@playwright/test";

import { primaryNav, signIn } from "./support";

// The browser itself asks for English (playwright.config.ts: `locale: "en-US"`), so Chinese words
// from the API can only come from the console asking for them, in its own Accept-Language.
test("in Chinese, Data shows the entities' Chinese labels: the console asks the API in its language", async ({ page }) => {
  await signIn(page, "operator");
  await primaryNav(page).getByRole("link", { name: "Data" }).click();
  await expect(page).toHaveURL(/\/data/);
  await expect(page.getByRole("navigation", { name: "Entities" }).getByRole("button", { name: /^SKU/ })).toBeVisible();

  // switching the console to Chinese fetches the entities again, asking for Chinese
  const asked = page.waitForRequest(
    (r) => new URL(r.url()).pathname === "/api/entities" && r.method() === "GET" && r.headers()["accept-language"] === "zh-CN",
  );
  await page.getByRole("button", { name: "切换到中文" }).click();
  await asked;
  const entities = page.getByRole("navigation", { name: "实体" });
  await expect(entities.getByRole("button", { name: /^物料（SKU）/ })).toBeVisible();
  await expect(entities.getByRole("button", { name: /^库存/ })).toBeVisible();
  await entities.getByRole("button", { name: /^物料（SKU）/ }).click();
  await expect(page.getByRole("tab", { name: "冷链物料" })).toBeVisible();

  // a fresh load in Chinese asks in Chinese from its first request on
  await page.reload();
  await expect(page.getByRole("navigation", { name: "实体" }).getByRole("button", { name: /^物料（SKU）/ })).toBeVisible();
  await expect(page.getByRole("tab", { name: "冷链物料" })).toBeVisible();
});
