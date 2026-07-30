import React from "react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import {
  SystemDialog,
  SystemDrawer,
  SystemIconButton,
  SystemPopover,
} from "../../frontend/src/components/system/SystemUI";

describe("System UI primitives", () => {
  it("gives icon-only controls a visible tooltip contract", () => {
    render(<SystemIconButton label="Open settings"><span>⚙</span></SystemIconButton>);
    const button = screen.getByRole("button", { name: "Open settings" });
    expect(button.getAttribute("data-tooltip")).toBe("Open settings");
    expect(button.hasAttribute("aria-pressed")).toBe(false);
  });

  it("uses a light tooltip surface in light mode", () => {
    const stylesheet = readFileSync(resolve(process.cwd(), "src/index.css"), "utf8");
    expect(stylesheet).toMatch(
      /:root\[data-theme="light"\]\s*\{[\s\S]*?--surface-tooltip:\s*rgba\(252,252,254,.98\)/,
    );
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

  it("unmounts a closed drawer so hidden interactive content cannot leak into navigation", () => {
    const view = render(
      <SystemDrawer open label="Tasks" onClose={() => {}}>
        <button>Hidden graph node</button>
      </SystemDrawer>,
    );
    expect(screen.getByRole("button", { name: "Hidden graph node" })).toBeDefined();

    view.rerender(
      <SystemDrawer open={false} label="Tasks" onClose={() => {}}>
        <button>Hidden graph node</button>
      </SystemDrawer>,
    );
    expect(screen.queryByRole("button", { name: "Hidden graph node" })).toBeNull();
    expect(document.body.textContent).not.toContain("Hidden graph node");
  });
});
