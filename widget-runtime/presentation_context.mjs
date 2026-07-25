const LOCALE_PATTERN = /^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$/;


export function normalizePresentationContext(value) {
  const context =
    typeof value === "object" && value !== null ? value : {};
  const theme =
    typeof context.theme === "object" && context.theme !== null
      ? context.theme
      : {};
  const preference = ["system", "light", "dark"].includes(theme.preference)
    ? theme.preference
    : "system";
  const effective = ["light", "dark"].includes(theme.effective)
    ? theme.effective
    : "dark";
  const locale =
    typeof context.locale === "string"
      && context.locale.length <= 35
      && LOCALE_PATTERN.test(context.locale)
      ? context.locale
      : "en-US";
  return {
    theme: { preference, effective },
    locale,
    reduced_motion:
      typeof context.reduced_motion === "boolean"
        ? context.reduced_motion
        : false,
  };
}


export function presentationMedia(context) {
  const normalized = normalizePresentationContext(context);
  return {
    colorScheme: normalized.theme.effective,
    reducedMotion: normalized.reduced_motion ? "reduce" : "no-preference",
  };
}
