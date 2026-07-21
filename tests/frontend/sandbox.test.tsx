import { describe, it, expect, vi } from "vitest";
import { render, screen, act, waitFor } from "@testing-library/react";
import { DashboardCanvas, Widget } from "../../frontend/src/components/DashboardCanvas";
import { SandboxWidget } from "../../frontend/src/components/SandboxWidget";
import { ErrorBoundary } from "../../frontend/src/components/ErrorBoundary";
import React from "react";

describe("SandboxWidget Rendering & Containment", () => {
  it("should catch rendering errors and render widget fallback UI", () => {
    const CrashingComponent = () => {
      throw new Error("Test Crash");
    };

    const spyConsole = vi.spyOn(console, "error").mockImplementation(() => {});

    render(
      <ErrorBoundary>
        <CrashingComponent />
      </ErrorBoundary>
    );

    expect(screen.getByText("Widget Crashed")).toBeDefined();
    expect(screen.getByText("Test Crash")).toBeDefined();

    spyConsole.mockRestore();
  });

  it("should render unified HTM widgets correctly using ambient.components and ambient.html", async () => {
    const mockWidget: Widget = {
      id: "htm-unified-test",
      title: "HTM Test App",
      js: `
        const { useState } = ambient.react;
        const { Card, Button, Text } = ambient.components;

        export default function HtmWidget() {
          const [clicked, setClicked] = useState(false);
          return ambient.html\`
            <\${Card} title="HTM Test Card">
              <\${Text} data-testid="htm-status" text=\${clicked ? "Clicked" : "Not Clicked"} />
              <\${Button} data-testid="htm-btn" label="Click Me" onClick=\${() => setClicked(true)} />
            <//>
          \`;
        }
      `
    };

    render(<SandboxWidget widget={mockWidget} />);

    await waitFor(() => {
      const status = screen.getByTestId("htm-status");
      expect(status).toBeDefined();
      expect(status.textContent).toBe("Not Clicked");
    });

    const btn = screen.getByTestId("htm-btn");
    await act(async () => {
      btn.click();
    });

    await waitFor(() => {
      const status = screen.getByTestId("htm-status");
      expect(status.textContent).toBe("Clicked");
    });
  });
});
