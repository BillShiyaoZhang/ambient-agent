import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { SystemDialog, SystemIconButton, SystemPopover } from "../../frontend/src/components/system/SystemUI";

describe("System UI primitives", () => {
  it("gives icon-only controls a visible tooltip contract", () => {
    render(<SystemIconButton label="Open settings"><span>⚙</span></SystemIconButton>);
    const button = screen.getByRole("button", { name: "Open settings" });
    expect(button.getAttribute("data-tooltip")).toBe("Open settings");
    expect(button.hasAttribute("aria-pressed")).toBe(false);
  });

  it("keeps only one system popover open across independent owners", () => {
    const closeFirst = vi.fn();
    const closeSecond = vi.fn();
    const firstTrigger = React.createRef<HTMLButtonElement>();
    const secondTrigger = React.createRef<HTMLButtonElement>();
    render(<>
      <button ref={firstTrigger}>First</button>
      <SystemPopover open onClose={closeFirst} triggerRef={firstTrigger} label="First menu">First menu</SystemPopover>
      <button ref={secondTrigger}>Second</button>
      <SystemPopover open onClose={closeSecond} triggerRef={secondTrigger} label="Second menu">Second menu</SystemPopover>
    </>);
    expect(closeFirst).toHaveBeenCalledTimes(1);
    expect(closeSecond).not.toHaveBeenCalled();
  });

  it("does not dismiss a blocking approval dialog through Escape or its scrim", () => {
    const close = vi.fn();
    render(<SystemDialog open blocking title="Permission" onClose={close}>
      <button>Approve</button>
    </SystemDialog>);
    fireEvent.keyDown(document, { key: "Escape" });
    fireEvent.click(screen.getByTestId("system-dialog-scrim"));
    expect(close).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog").getAttribute("aria-modal")).toBe("true");
  });

  it("dismisses a non-blocking dialog with Escape", () => {
    const close = vi.fn();
    render(<SystemDialog open title="Details" onClose={close}>Details</SystemDialog>);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(close).toHaveBeenCalledTimes(1);
  });
});
