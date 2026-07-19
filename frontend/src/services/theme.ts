export type ThemePreference = "system" | "light" | "dark";
export type EffectiveTheme = "light" | "dark";

export interface ThemeSnapshot {
  preference: ThemePreference;
  effective: EffectiveTheme;
}

const STORAGE_KEY = "ambient_theme_preference";

export interface ThemeController {
  snapshot: () => ThemeSnapshot;
  setPreference: (preference: ThemePreference) => void;
  subscribe: (listener: (snapshot: ThemeSnapshot) => void) => () => void;
  destroy: () => void;
}

export function createThemeController(): ThemeController {
  const media = typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-color-scheme: dark)")
    : ({ matches: false, addEventListener: () => {}, removeEventListener: () => {} } as unknown as MediaQueryList);
  const stored = localStorage.getItem(STORAGE_KEY);
  let preference: ThemePreference = stored === "light" || stored === "dark" || stored === "system" ? stored : "system";
  let systemTheme: EffectiveTheme = media.matches ? "dark" : "light";
  const listeners = new Set<(snapshot: ThemeSnapshot) => void>();

  const snapshot = (): ThemeSnapshot => ({
    preference,
    effective: preference === "system" ? systemTheme : preference,
  });
  const apply = () => {
    const current = snapshot();
    document.documentElement.dataset.theme = current.effective;
    document.documentElement.dataset.themePreference = current.preference;
    document.documentElement.style.colorScheme = current.effective;
    listeners.forEach((listener) => listener(current));
  };
  const handleSystemChange = (event: MediaQueryListEvent) => {
    systemTheme = event.matches ? "dark" : "light";
    if (preference === "system") apply();
  };
  media.addEventListener("change", handleSystemChange);
  apply();

  return {
    snapshot,
    setPreference(nextPreference) {
      preference = nextPreference;
      localStorage.setItem(STORAGE_KEY, nextPreference);
      apply();
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    destroy() {
      media.removeEventListener("change", handleSystemChange);
      listeners.clear();
    },
  };
}
