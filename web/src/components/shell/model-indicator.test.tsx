import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { HealthOut, ProviderInfo } from "@/lib/api";

import { renderWithIntl } from "../../../test/render";
import { ConsoleModelIndicator, ModelIndicator } from "./model-indicator";

const health = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, health } };
});

const MOCK: ProviderInfo = { name: "mock", model: null, mock: true };
const REAL: ProviderInfo = { name: "openai_compat", model: "a-model", mock: false };
const HINT = "No model API key is configured. Routing uses rules and keyword search, and answers use templates.";

const healthy = (provider: ProviderInfo): HealthOut => ({
  status: "ok",
  version: "0.1.0",
  demo_mode: true,
  provider,
  limits: { max_upload_bytes: 2_097_152, max_rows: 2_000 },
});

// a block, not an arrow's value: a function returned from beforeEach runs as its teardown
beforeEach(() => {
  health.mockReset();
});

describe("ModelIndicator", () => {
  it("says no model is configured when the server runs its mock provider, and what that means", async () => {
    const user = userEvent.setup();
    renderWithIntl(<ModelIndicator provider={MOCK} />);
    const pill = screen.getByRole("button", { name: "No model" });
    // for screen readers whether or not the tooltip is open
    expect(pill).toHaveAccessibleDescription(HINT);
    await user.hover(pill);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(HINT);
  });

  it("says it in Chinese too", () => {
    renderWithIntl(<ModelIndicator provider={MOCK} />, { locale: "zh" });
    expect(screen.getByRole("button", { name: "未配置模型" })).toHaveAccessibleDescription(
      "未配置模型 API 密钥。路由使用规则与关键词检索，回答使用模板。",
    );
  });

  it("shows nothing with a real model, or before the server has said which", () => {
    const view = renderWithIntl(<ModelIndicator provider={REAL} />);
    expect(view.container).toBeEmptyDOMElement();
    view.rerender(<ModelIndicator provider={null} />);
    expect(view.container).toBeEmptyDOMElement();
  });
});

describe("ConsoleModelIndicator", () => {
  it("asks the server's health which model answers, once", async () => {
    health.mockResolvedValue(healthy(MOCK));
    renderWithIntl(<ConsoleModelIndicator />);
    expect(await screen.findByRole("button", { name: "No model" })).toBeInTheDocument();
    expect(health).toHaveBeenCalledTimes(1);
  });

  it("stays out of the way when the health check fails", async () => {
    health.mockRejectedValue(new Error("unreachable"));
    const view = renderWithIntl(<ConsoleModelIndicator />);
    await waitFor(() => expect(health).toHaveBeenCalled());
    expect(view.container).toBeEmptyDOMElement();
  });
});
