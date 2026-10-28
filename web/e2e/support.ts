import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, type Locator, type Page, type Response } from "@playwright/test";

/** Sample files shipped with the repo (examples/data). */
export const EXAMPLES = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "examples", "data");

export type DemoRole = "admin" | "operator" | "viewer";

const ROLE_LABEL: Record<DemoRole, string> = { admin: "Admin", operator: "Operator", viewer: "Viewer" };

/** Passwordless demo sign-in from /login; lands on Hub. */
export async function signIn(page: Page, role: DemoRole): Promise<void> {
  await page.goto("/login");
  await page.getByRole("button", { name: `Sign in as ${ROLE_LABEL[role]}` }).click();
  await expect(page).toHaveURL(/\/hub$/);
}

/** A response to the console's own `POST <path>` on the API (not its CORS preflight). */
export function apiPost(page: Page, path: string): Promise<Response> {
  return page.waitForResponse((r) => new URL(r.url()).pathname === path && r.request().method() === "POST");
}

/**
 * `signIn`, and what the API issued for it: its origin (where the console's API lives) and the
 * bearer token, so a test can ask the API itself what that token is still good for.
 */
export async function signInForToken(page: Page, role: DemoRole): Promise<{ api: string; token: string }> {
  const issued = apiPost(page, "/api/auth/demo");
  await signIn(page, role);
  const response = await issued;
  const { access_token: token } = (await response.json()) as { access_token: string };
  return { api: new URL(response.url()).origin, token };
}

/** The console's primary navigation (sidebar). */
export function primaryNav(page: Page): Locator {
  return page.getByRole("navigation", { name: "Primary" });
}

/** Hub turns, oldest first (one `<article>` per question). */
export function turns(page: Page): Locator {
  return page.getByRole("article");
}

/** Ask a question in Hub and return its turn (a locator pinned to its position). */
export async function ask(page: Page, question: string): Promise<Locator> {
  const index = await turns(page).count();
  const box = page.getByRole("textbox", { name: "Ask a question" });
  await box.fill(question);
  await box.press("Enter");
  const turn = turns(page).nth(index);
  await expect(turn).toContainText(question);
  return turn;
}

/** Open a finished turn's collapsed trace ("Thought for …") and return its step list. */
export async function openTrace(turn: Locator): Promise<Locator> {
  await turn.getByText(/^Thought for /).click();
  const steps = turn.getByRole("list", { name: "Reasoning trace" });
  await expect(steps).toBeVisible();
  return steps;
}
