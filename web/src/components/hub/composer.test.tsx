import { fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { renderWithIntl } from "../../../test/render";
import { Composer } from "./composer";

function setup(busy = false) {
  const onSend = vi.fn();
  const onStop = vi.fn();
  renderWithIntl(<Composer busy={busy} onSend={onSend} onStop={onStop} />);
  return { onSend, onStop, input: screen.getByLabelText("Ask a question") };
}

describe("Composer", () => {
  it("sends on Enter and clears the input", async () => {
    const user = userEvent.setup();
    const { onSend, input } = setup();
    await user.type(input, "Which SKUs are low on stock?{Enter}");
    expect(onSend).toHaveBeenCalledWith("Which SKUs are low on stock?");
    expect(input).toHaveValue("");
  });

  it("adds a new line on Shift+Enter without sending", async () => {
    const user = userEvent.setup();
    const { onSend, input } = setup();
    await user.type(input, "first{Shift>}{Enter}{/Shift}second");
    expect(onSend).not.toHaveBeenCalled();
    expect(input).toHaveValue("first\nsecond");
  });

  it("does not send the Enter that confirms an IME candidate", () => {
    const { onSend, input } = setup();
    fireEvent.change(input, { target: { value: "库存不足的物料有哪些" } });
    fireEvent.keyDown(input, { key: "Enter", isComposing: true });
    fireEvent.keyDown(input, { key: "Enter", keyCode: 229 });
    expect(onSend).not.toHaveBeenCalled();
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSend).toHaveBeenCalledWith("库存不足的物料有哪些");
  });

  it("ignores blank input", async () => {
    const user = userEvent.setup();
    const { onSend, input } = setup();
    await user.type(input, "   {Enter}");
    expect(onSend).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
  });

  it("turns Send into Stop while busy and never sends", async () => {
    const user = userEvent.setup();
    const { onSend, onStop, input } = setup(true);
    await user.type(input, "next question{Enter}");
    expect(onSend).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Stop" }));
    expect(onStop).toHaveBeenCalledTimes(1);
  });
});
