export type ChromeMode = "desktop" | "compact" | "mobile";

export const resolveChromeMode = (width: number): ChromeMode =>
  width >= 1024 ? "desktop" : width >= 720 ? "compact" : "mobile";
