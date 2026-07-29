import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  DeferredGraphWorkbench,
  GraphLoadingStatus,
} from "../../frontend/src/components/graph/DeferredGraph";

describe("deferred graph surfaces", () => {
  it("announces graph loading accessibly", () => {
    render(<GraphLoadingStatus label="Execution graph loading" />);

    const status = screen.getByRole("status", { name: "Execution graph loading" });
    expect(status.getAttribute("aria-live")).toBe("polite");
    expect(status.getAttribute("aria-busy")).toBe("true");
    expect(status.textContent).toContain("Loading interactive graph");
  });

  it("does not render or request the workbench surface while closed", () => {
    const { container } = render(
      <DeferredGraphWorkbench open={false} language="en" onClose={() => {}} />,
    );

    expect(container.childElementCount).toBe(0);
    expect(screen.queryByRole("status")).toBeNull();
  });
});
