import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { DashboardCanvas, Widget } from "../../frontend/src/components/DashboardCanvas";
import { SandboxWidget } from "../../frontend/src/components/SandboxWidget";
import React from "react";

describe("SandboxWidget Rendering & Containment", () => {
  it("should render widget HTML correctly", () => {
    const mockWidget: Widget = {
      id: "test-w1",
      title: "Test Widget",
      html: '<h3 data-testid="widget-title">Inside Widget</h3>',
      css: ".widget-root-content { color: purple; }",
      js: "",
    };

    render(
      <DashboardCanvas
        widgets={[mockWidget]}
        onRemoveWidget={() => {}}
        renderWidgetContent={(w) => <SandboxWidget widget={w} />}
      />
    );

    const titleEl = screen.getByTestId("widget-title");
    expect(titleEl).toBeDefined();
    expect(titleEl.textContent).toBe("Inside Widget");
  });

  it("should scope CSS styles inside the widget container", () => {
    const mockWidget: Widget = {
      id: "test-w2",
      title: "CSS Test",
      html: '<div class="test-class">Styled text</div>',
      css: "[data-widget-scope='widget-test-w2'] .test-class { color: rgb(255, 0, 0); }",
      js: "",
    };

    render(
      <DashboardCanvas
        widgets={[mockWidget]}
        onRemoveWidget={() => {}}
        renderWidgetContent={(w) => <SandboxWidget widget={w} />}
      />
    );

    const sandbox = screen.getByTestId("sandbox-test-w2");
    const styleEl = sandbox.querySelector("style");
    expect(styleEl).toBeDefined();
    expect(styleEl?.textContent).toContain("[data-widget-scope='widget-test-w2']");
  });

  it("should execute JS safely and bind variables to the scoped root element", () => {
    const mockWidget: Widget = {
      id: "test-w3",
      title: "JS Test",
      html: '<button id="btn" data-testid="test-btn">Click me</button><div id="output" data-testid="test-out">Init</div>',
      css: "",
      js: `
        const btn = root.querySelector("#btn");
        const out = root.querySelector("#output");
        btn.addEventListener("click", () => {
          out.textContent = "Clicked!";
        });
      `,
    };

    render(
      <DashboardCanvas
        widgets={[mockWidget]}
        onRemoveWidget={() => {}}
        renderWidgetContent={(w) => <SandboxWidget widget={w} />}
      />
    );

    const btn = screen.getByTestId("test-btn");
    const out = screen.getByTestId("test-out");
    expect(out.textContent).toBe("Init");

    btn.click();
    expect(out.textContent).toBe("Clicked!");
  });

  it("should isolate JS execution context so local variables do not bleed to global window object", () => {
    const mockWidget: Widget = {
      id: "test-w4",
      title: "Isolation Test",
      html: "<div></div>",
      css: "",
      js: "var mySecretVariableBleed = 'secret-value'; window.mySecretVariablePublic = 'public-value';",
    };

    render(
      <DashboardCanvas
        widgets={[mockWidget]}
        onRemoveWidget={() => {}}
        renderWidgetContent={(w) => <SandboxWidget widget={w} />}
      />
    );

    // var inside new Function executes in local scope and does not attach to window (unlike global scripts)
    expect((window as any).mySecretVariableBleed).toBeUndefined();
    // explicit window writing will bleed (which is normal), but let's confirm var does not.
    expect((window as any).mySecretVariablePublic).toBe("public-value");
    
    // Clean up
    delete (window as any).mySecretVariablePublic;
  });
});
