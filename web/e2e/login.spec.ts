import { expect, test } from "@playwright/test";

import { apiPost, primaryNav, signIn, signInForToken, type DemoRole } from "./support";

const ROLES: { role: DemoRole; canImport: boolean }[] = [
  { role: "admin", canImport: true },
  { role: "operator", canImport: true },
  { role: "viewer", canImport: false },
];

test.describe("demo login", () => {
  for (const { role, canImport } of ROLES) {
    test(`signs in as ${role}`, async ({ page }) => {
      await signIn(page, role);
      // named by what it shows (user, role) and then "Account" (WCAG 2.5.3 Label in Name)
      const account = page.getByRole("button", { name: /Account$/ });
      await expect(account).toContainText(`demo-${role}`);
      await expect(account).toHaveAccessibleName(new RegExp(`^demo-${role}\\b`));
      await expect(page.getByRole("textbox", { name: "Ask a question" })).toBeVisible();

      const importLink = primaryNav(page).getByRole("link", { name: "Import" });
      if (canImport) {
        await importLink.click();
        await expect(page).toHaveURL(/\/import$/);
        await expect(page.getByRole("combobox", { name: "Import into" })).toBeVisible();
        return;
      }
      // the viewer has no Import window: not in the navigation, and not by URL either
      await expect(importLink).toHaveCount(0);
      await page.goto("/import");
      await expect(page.getByText("No access", { exact: true })).toBeVisible();
      await expect(page.getByText("Your role can't open this window.")).toBeVisible();
      await expect(page.getByRole("combobox", { name: "Import into" })).toHaveCount(0);
    });
  }
});

test("sign-in follows next= back to a deep link in this console, and never off it", async ({ page, baseURL }) => {
  const origin = new URL(baseURL!).origin;
  // nothing may reach the probe host; were the redirect open, the browser would try
  const escaped: string[] = [];
  await page.context().route(/^[a-z]+:\/\/evil\.example/i, async (route) => {
    escaped.push(route.request().url());
    await route.abort();
  });

  // signed out, a deep link goes through /login and comes back with its query
  await page.goto("/audit?status=ok");
  await expect(page).toHaveURL(/\/login\?next=/);
  await page.getByRole("button", { name: "Sign in as Operator" }).click();
  await expect(page).toHaveURL(`${origin}/audit?status=ok`);

  // signed in, /login sends you on at once: query and hash survive...
  await page.goto(`/login?next=${encodeURIComponent("/audit?status=error#log")}`);
  await expect(page).toHaveURL(`${origin}/audit?status=error#log`);

  // ...and a next= that leaves the origin lands on Hub instead
  for (const next of [
    "/%09/evil.example", // TAB, dropped by the URL parser: "//evil.example"
    "/%0A/evil.example",
    "/%0D/evil.example",
    "//evil.example",
    "/%5Cevil.example", // "/\evil.example"
    "/.//evil.example", // its path parses to "//evil.example"
    "https://evil.example/hub",
  ]) {
    await page.goto(`/login?next=${next}`);
    await expect(page, next).toHaveURL(`${origin}/hub`);
  }
  expect(escaped).toEqual([]);
});

test("signing out revokes the token on the server: the API refuses it from then on", async ({ page, request }) => {
  const { api, token } = await signInForToken(page, "operator");
  const me = () => request.get(`${api}/api/auth/me`, { headers: { Authorization: `Bearer ${token}` } });
  expect((await me()).status()).toBe(200); // the token works while signed in

  const revoked = apiPost(page, "/api/auth/logout");
  await page.getByRole("button", { name: /Account$/ }).click();
  await page.getByRole("menuitem", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login/);
  expect((await revoked).status()).toBe(200);

  // the old token, as anyone holding a copy would send it
  const refused = await me();
  expect(refused.status()).toBe(401);
  expect(await refused.json()).toMatchObject({ error: { code: "token_revoked" } });
  // and the tab keeps nothing of the session
  expect(await page.evaluate(() => window.sessionStorage.getItem("twotrack.session"))).toBeNull();
});
