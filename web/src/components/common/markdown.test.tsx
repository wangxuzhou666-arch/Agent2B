import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Markdown, type NarrationSource } from "./markdown";

const md = (text: string, source: NarrationSource) => render(<Markdown source={source}>{text}</Markdown>);

describe("Markdown", () => {
  it("renders a model's link as text that shows where it points, with nothing to click", () => {
    const { container } = md("Order through the [supplier portal](https://portal.example/orders?id=7) today.", "model");
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(container.querySelector("a")).toBeNull();
    expect(container).toHaveTextContent(/^Order through the supplier portal https:\/\/portal\.example\/orders\?id=7 today\.$/);
  });

  it("shows a model's bare URL or address once, still as text", () => {
    const { container } = md(
      "Details: https://portal.example/x, <https://portal.example/y>, www.portal.example and buyer@portal.example",
      "model",
    );
    expect(container.querySelector("a")).toBeNull();
    expect(container).toHaveTextContent(
      /^Details: https:\/\/portal\.example\/x, https:\/\/portal\.example\/y, www\.portal\.example and buyer@portal\.example$/,
    );
  });

  it("keeps a model's dangerous link inert and visible", () => {
    const { container } = md("[Click to verify](javascript:alert(1))", "model");
    expect(container.querySelector("a")).toBeNull();
    expect(container).toHaveTextContent(/^Click to verify javascript:alert\(1\)$/);
  });

  it("fails closed for an answer source this build does not know", () => {
    const { container } = md("[portal](https://portal.example)", "agent" as NarrationSource);
    expect(container.querySelector("a")).toBeNull();
    expect(container).toHaveTextContent(/^portal https:\/\/portal\.example$/);
  });

  it.each(["template", "system"] as const)("renders a %s link as text too, exactly as a model's", (source) => {
    const text =
      "See the [runbook](https://docs.example/runbook), <https://docs.example/x>, www.docs.example, " +
      "ops@docs.example or [this](javascript:alert(1)).";
    const model = md(text, "model").container;
    const { container } = md(text, source);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(container.querySelector("a, [href], [src]")).toBeNull();
    expect(container).toHaveTextContent(
      /^See the runbook https:\/\/docs\.example\/runbook, https:\/\/docs\.example\/x, www\.docs\.example, ops@docs\.example or this javascript:alert\(1\)\.$/,
    );
    expect(container.innerHTML).toBe(model.innerHTML);
  });

  // A template answer repeats a tool's message and scope, which can carry a param a model
  // proposed or a name from a stored record: the shapes below are answers the API returned.
  it.each([
    [
      "a param a model proposed",
      "Exploration stopped early: it reached its tool-call limit before finishing. Last result before it stopped: " +
        "No rows matched (Supplier ID: [Verify your account](https://phish.example/login)).",
    ],
    [
      "a stored name",
      "Draft PO-20302 created for SKU-10001 with Acme [Verify your account](https://phish.example/login), ETA 2026-11-06.",
    ],
  ])("shows a link from %s inside a template answer as text, with nothing to click", (_, answer) => {
    const { container } = md(answer, "template");
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(container.querySelector("a, [href], [src]")).toBeNull();
    expect(container).toHaveTextContent("Verify your account https://phish.example/login");
  });

  it("never renders raw HTML, whoever wrote it", () => {
    for (const source of ["model", "template", "system"] as const) {
      const { container, unmount } = md('<a href="https://evil.example">hi</a> <img src="x" onerror="alert(1)">', source);
      expect(container.querySelector("a, img")).toBeNull();
      unmount();
    }
  });
});
