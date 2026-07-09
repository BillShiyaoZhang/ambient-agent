import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { AuditLogPanel, AuditLogEntry } from "../../frontend/src/components/AuditLogPanel";
import React from "react";

// Mock fetch globally
const mockLogs: AuditLogEntry[] = [
  {
    id: 1,
    timestamp: "2026-07-07T12:00:00.000Z",
    provider: "ollama",
    model: "llama3",
    prompt: "Show me weather",
    response: "Beijing weather widget XML"
  }
];

describe("AuditLogPanel Component", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation(() =>
      Promise.resolve({
        ok: true,
        json: () => Promise.resolve(mockLogs),
      })
    ));
  });

  it("should fetch and render audit logs correctly", async () => {
    render(<AuditLogPanel isOpen={true} onClose={() => {}} />);
    
    // Expect loading state or title
    expect(screen.getByText("Data Transmission Audit Log")).toBeDefined();
    
    // Wait for the mock log entry to be displayed
    await waitFor(() => {
      expect(screen.getByText(/llama3/)).toBeDefined();
      expect(screen.getByText(/Show me weather/)).toBeDefined();
    });
  });
});
