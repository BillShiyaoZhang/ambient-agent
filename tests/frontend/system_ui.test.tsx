import React, { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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

  it("restores drawer trigger focus by default", async () => {
    function Harness() {
      const [open, setOpen] = useState(false);
      return <>
        <button type="button" onClick={() => setOpen(true)}>Open drawer</button>
        <SystemDrawer open={open} label="Details" onClose={() => setOpen(false)}>
          <button type="button" onClick={() => setOpen(false)}>Close drawer</button>
        </SystemDrawer>
      </>;
    }

    render(<Harness />);
    const trigger = screen.getByRole("button", { name: "Open drawer" });
    trigger.focus();
    fireEvent.click(trigger);
    await waitFor(() => expect(document.activeElement).toBe(
      screen.getByRole("button", { name: "Close drawer" })
    ));

    fireEvent.click(screen.getByRole("button", { name: "Close drawer" }));
    await waitFor(() => expect(document.activeElement).toBe(trigger));
  });

  it("keeps a closed drawer out of keyboard interaction until it opens", async () => {
    function Harness() {
      const [open, setOpen] = useState(false);
      return <>
        <button type="button" onClick={() => setOpen(true)}>Open private drawer</button>
        <SystemDrawer open={open} label="Private details" onClose={() => setOpen(false)}>
          <button type="button">Private action</button>
        </SystemDrawer>
      </>;
    }

    render(<Harness />);
    const dialog = screen.getByRole("dialog", { name: "Private details", hidden: true });
    const layer = dialog.parentElement;
    if (!layer) throw new Error("Drawer layer was not rendered.");

    expect(layer.getAttribute("aria-hidden")).toBe("true");
    expect(layer.hasAttribute("inert")).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "Open private drawer" }));

    await waitFor(() => {
      expect(layer.getAttribute("aria-hidden")).toBe("false");
      expect(layer.hasAttribute("inert")).toBe(false);
    });
  });

  it("can delegate drawer focus restoration to its owner", async () => {
    function Harness() {
      const [open, setOpen] = useState(false);
      return <>
        <button type="button" onClick={() => setOpen(true)}>Open managed drawer</button>
        <SystemDrawer
          open={open}
          label="Managed details"
          onClose={() => setOpen(false)}
          restoreFocusOnClose={false}
        >
          <button type="button" onClick={() => setOpen(false)}>Close managed drawer</button>
        </SystemDrawer>
      </>;
    }

    render(<Harness />);
    const trigger = screen.getByRole("button", { name: "Open managed drawer" });
    trigger.focus();
    fireEvent.click(trigger);
    const close = screen.getByRole("button", { name: "Close managed drawer" });
    await waitFor(() => expect(document.activeElement).not.toBe(trigger));

    fireEvent.click(close);
    await new Promise((resolve) => requestAnimationFrame(resolve));
    expect(document.activeElement).not.toBe(trigger);
  });
});
