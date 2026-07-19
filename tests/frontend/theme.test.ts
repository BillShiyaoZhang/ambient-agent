import { afterEach, describe, expect, it, vi } from "vitest";
import { createThemeController } from "../../frontend/src/services/theme";

describe("Workspace theme controller", () => {
  afterEach(() => localStorage.clear());

  it("follows system theme until a manual preference is stored", () => {
    const listeners = new Set<(event: MediaQueryListEvent) => void>();
    const media = {
      matches: true,
      addEventListener: (_: string, listener: (event: MediaQueryListEvent) => void) => listeners.add(listener),
      removeEventListener: (_: string, listener: (event: MediaQueryListEvent) => void) => listeners.delete(listener),
    } as unknown as MediaQueryList;
    vi.stubGlobal("matchMedia", vi.fn(() => media));

    const controller = createThemeController();
    expect(controller.snapshot()).toEqual({ preference: "system", effective: "dark" });
    controller.setPreference("light");
    expect(controller.snapshot()).toEqual({ preference: "light", effective: "light" });
    expect(localStorage.getItem("ambient_theme_preference")).toBe("light");
    controller.destroy();
  });
});
