import { expect, test } from "@playwright/test";

import { ask, openTrace, signIn, turns } from "./support";

test.describe("hub", () => {
  test.beforeEach(async ({ page }) => {
    await signIn(page, "operator");
  });

  test("answers deterministically: rule route, trace, provenance", async ({ page }) => {
    const turn = await ask(page, "How many shipments are late?");
    // the numbers come from the tool; its provenance heads the result table, by label then id
    const result = turn.getByRole("region", { name: /^Source: .+ \(late_shipments\)$/ });
    await expect(result).toBeVisible();
    await expect(turn.getByText(/^\d+ rows? · as of \d{4}-\d{2}-\d{2} \d{2}:\d{2} · scope: .+/)).toBeVisible();
    // an answer in words above the rows: its wording is the server's, so only its place is checked
    await expect(turn.getByRole("paragraph").first()).not.toBeEmpty();
    await expect(result.getByRole("row").nth(1)).toBeVisible(); // a header row, then data

    const steps = await openTrace(turn);
    await expect(steps.getByText("Matched by keywords → late_shipments")).toBeVisible();
    await expect(steps.getByTitle("Keyword rule arm").first()).toHaveText(/^rule/);
    await expect(steps.getByText(/^Ran deterministic tool \(\d+ rows?\)$/)).toBeVisible();
  });

  test("asks when the report can't apply the question's filter, then answers unfiltered", async ({ page }) => {
    const question = "low stock items except packaging";
    // the explanation is the server's prose; what the console owns is the shape of the choice
    const clarify = await ask(page, question);
    const choice = clarify.getByRole("button", { name: /^Show all categories/ });
    await expect(choice).toContainText("Unfiltered");
    await expect(choice).toContainText("(low_stock)"); // the choice in words, then the report it runs
    await expect(choice).toHaveAccessibleDescription(
      "Runs without the filter you asked for. The answer says it is unfiltered.",
    );
    // it asked instead of answering: no result yet
    await expect(clarify.getByRole("region", { name: /^Source:/ })).toHaveCount(0);

    await choice.click();
    const answer = turns(page).nth(1);
    await expect(answer).toContainText(question);
    await expect(answer).toContainText("Run as low_stock");
    await expect(answer.getByRole("region", { name: /^Source: .+ \(low_stock\)$/ })).toBeVisible();
    // the provenance says the result ignores the exclusion it was asked for
    await expect(answer.getByText(/^\d+ rows? · as of .+ · scope: .*\b(?:not filtered|unfiltered)\b/i)).toBeVisible();

    const steps = await openTrace(answer);
    await expect(steps.getByText("Ran low_stock as selected")).toBeVisible();
    await expect(steps.getByText(/^Scope check: ran without the filter you named \(not packaging\), as you chose$/i)).toBeVisible();
    // the choice is offered once: an earlier turn's chips stay inert
    await expect(choice).toBeDisabled();
  });

  test("says no model is configured: in the header, and on the narration of an answer", async ({ page }) => {
    const pill = page.getByRole("banner").getByRole("button", { name: "No model" });
    await expect(pill).toBeVisible();
    await expect(pill).toHaveAccessibleDescription(
      "No model API key is configured. Routing uses rules and keyword search, and answers use templates.",
    );
    await pill.hover();
    await expect(page.getByRole("tooltip")).toContainText("No model API key is configured.");

    const turn = await ask(page, "Which SKUs are low on stock?");
    const steps = await openTrace(turn);
    await expect(steps.getByText("Template narration (no model configured)")).toBeVisible();
    await expect(steps.getByText("Narrated by the language model")).toHaveCount(0);
  });

  test("falls back to chat when no report fits, and says no model is configured", async ({ page }) => {
    const turn = await ask(page, "write a haiku about the sea");
    await expect(turn.getByText(/^No language model is configured, so I can only run the reports this console knows about\./)).toBeVisible();
    await expect(turn.getByRole("region", { name: /^Source:/ })).toHaveCount(0);

    const steps = await openTrace(turn);
    await expect(steps.getByText("No report matches; answering as general chat")).toBeVisible();
    await expect(steps.getByTitle("Chat fallback: no report matched").first()).toHaveText(/^fallback/);
  });
});
