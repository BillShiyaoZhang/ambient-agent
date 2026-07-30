import React, { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SlashCommandInput } from "../../frontend/src/components/SlashCommandInput";
import {
  FALLBACK_SLASH_COMMAND_CATALOG,
  applySlashSuggestion,
  slashSuggestionContext,
  type SlashCommandCatalog,
} from "../../frontend/src/services/slashCommands";

function catalogWithIds(): SlashCommandCatalog {
  return {
    ...FALLBACK_SLASH_COMMAND_CATALOG,
    commands: FALLBACK_SLASH_COMMAND_CATALOG.commands.map((command) => ({
      ...command,
      arguments: command.arguments.map((argument) => {
        if (argument.option_source === "apps") {
          return {
            ...argument,
            options: [
              { value: "planner", label: "Planner", description: "Plans the day" },
              { value: "calendar", label: "Calendar", description: "Shows events" },
            ],
          };
        }
        if (argument.option_source === "skills") {
          return {
            ...argument,
            options: [
              { value: "agent-skill:daily", label: "Daily planning" },
              {
                value: "agent-skill:review",
                label: "Review notes",
                disabled: true,
              },
            ],
          };
        }
        return argument;
      }),
    })),
  };
}

describe("slash command suggestions", () => {
  it("shows every command for slash and narrows by typed name", () => {
    const all = slashSuggestionContext(
      "/",
      1,
      FALLBACK_SLASH_COMMAND_CATALOG,
      "en",
    );
    const precise = slashSuggestionContext(
      "/ap",
      3,
      FALLBACK_SLASH_COMMAND_CATALOG,
      "en",
    );

    expect(all?.suggestions.map((item) => item.value)).toEqual([
      "ask",
      "app",
      "create",
      "query",
      "mutate",
      "skill",
    ]);
    expect(precise?.suggestions.map((item) => item.value)).toEqual(["app"]);
  });

  it("shows every ID for an empty choice and filters across ID metadata", () => {
    const catalog = catalogWithIds();
    const allApps = slashSuggestionContext("/app ", 5, catalog, "en");
    const filtered = slashSuggestionContext("/app cal", 8, catalog, "en");

    expect(allApps?.suggestions.map((item) => item.value)).toEqual([
      "planner",
      "calendar",
    ]);
    expect(filtered?.suggestions.map((item) => item.value)).toEqual(["calendar"]);
  });

  it("replaces only the active command in a multi-command message", () => {
    const value = "/app planner improve colors /qu";
    const context = slashSuggestionContext(
      value,
      value.length,
      FALLBACK_SLASH_COMMAND_CATALOG,
      "en",
    );
    const query = context?.suggestions.find((item) => item.value === "query");
    expect(query).toBeDefined();
    expect(applySlashSuggestion(
      value,
      query!,
    ).value).toBe("/app planner improve colors /query ");
  });
});

describe("SlashCommandInput", () => {
  function Harness({ onSubmit = vi.fn() }: { onSubmit?: () => void }) {
    const [value, setValue] = useState("");
    return (
      <SlashCommandInput
        value={value}
        onChange={setValue}
        onSubmit={onSubmit}
        placeholder="Message Ambient"
        language="en"
        catalog={catalogWithIds()}
      />
    );
  }

  it("inserts a command, then exposes all current App IDs", () => {
    render(<Harness />);
    const input = screen.getByPlaceholderText("Message Ambient") as HTMLTextAreaElement;

    fireEvent.change(input, { target: { value: "/", selectionStart: 1 } });
    expect(screen.getByRole("option", { name: /\/app/ })).toBeDefined();
    fireEvent.click(screen.getByRole("option", { name: /\/app/ }));

    expect(input.value).toBe("/app ");
    expect(screen.getByRole("option", { name: /Planner/ })).toBeDefined();
    expect(screen.getByRole("option", { name: /Calendar/ })).toBeDefined();
    fireEvent.click(screen.getByRole("option", { name: /Planner/ }));
    expect(input.value).toBe("/app planner ");
  });

  it("uses Enter to choose a suggestion before submitting", () => {
    const submit = vi.fn();
    render(<Harness onSubmit={submit} />);
    const input = screen.getByPlaceholderText("Message Ambient") as HTMLTextAreaElement;

    fireEvent.change(input, { target: { value: "/ap", selectionStart: 3 } });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(input.value).toBe("/app ");
    expect(submit).not.toHaveBeenCalled();
  });
});
