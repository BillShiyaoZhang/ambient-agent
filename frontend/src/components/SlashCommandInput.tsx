import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  applySlashSuggestion,
  FALLBACK_SLASH_COMMAND_CATALOG,
  loadSlashCommandCatalog,
  slashSuggestionContext,
  type SlashCommandCatalog,
  type SlashSuggestion,
} from "../services/slashCommands";

interface SlashCommandInputProps {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  placeholder: string;
  disabled?: boolean;
  language: "zh" | "en";
  apiBase?: string;
  catalog?: SlashCommandCatalog;
}

function firstEnabledIndex(suggestions: SlashSuggestion[]): number {
  const index = suggestions.findIndex((suggestion) => !suggestion.disabled);
  return index >= 0 ? index : 0;
}

function moveSelection(
  suggestions: SlashSuggestion[],
  current: number,
  delta: number,
): number {
  if (!suggestions.length) return 0;
  let candidate = current;
  for (let count = 0; count < suggestions.length; count += 1) {
    candidate = (candidate + delta + suggestions.length) % suggestions.length;
    if (!suggestions[candidate].disabled) return candidate;
  }
  return current;
}

export const SlashCommandInput: React.FC<SlashCommandInputProps> = ({
  value,
  onChange,
  onSubmit,
  placeholder,
  disabled = false,
  language,
  apiBase,
  catalog: catalogOverride,
}) => {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [catalog, setCatalog] = useState(
    () => catalogOverride ?? FALLBACK_SLASH_COMMAND_CATALOG,
  );
  const [cursor, setCursor] = useState(value.length);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [dismissedKey, setDismissedKey] = useState<string | null>(null);
  const refreshKeyRef = useRef<string | null>(null);

  useEffect(() => {
    if (catalogOverride) setCatalog(catalogOverride);
  }, [catalogOverride]);

  const context = useMemo(
    () => slashSuggestionContext(value, cursor, catalog, language),
    [catalog, cursor, language, value],
  );
  const visibleContext = context?.key === dismissedKey ? null : context;
  const activationKey = visibleContext?.activationKey ?? null;

  useEffect(() => {
    if (!visibleContext) {
      return;
    }
    setSelectedIndex(firstEnabledIndex(visibleContext.suggestions));
    setDismissedKey(null);
  }, [visibleContext]);

  useEffect(() => {
    if (!activationKey) {
      refreshKeyRef.current = null;
      return;
    }
    if (!apiBase || catalogOverride || refreshKeyRef.current === activationKey) return;
    refreshKeyRef.current = activationKey;
    let cancelled = false;
    void loadSlashCommandCatalog(apiBase)
      .then((loaded) => {
        if (!cancelled) setCatalog(loaded);
      })
      .catch(() => {
        // The static catalog still exposes every command; dynamic ID choices
        // become available again on the next activation.
      });
    return () => {
      cancelled = true;
    };
  }, [activationKey, apiBase, catalogOverride]);

  useEffect(() => {
    setCursor((current) => Math.min(current, value.length));
  }, [value.length]);

  const chooseSuggestion = (suggestion: SlashSuggestion) => {
    if (suggestion.disabled) return;
    const next = applySlashSuggestion(value, suggestion);
    onChange(next.value);
    setCursor(next.cursor);
    setDismissedKey(null);
    requestAnimationFrame(() => {
      textareaRef.current?.focus();
      textareaRef.current?.setSelectionRange(next.cursor, next.cursor);
    });
  };

  const activeSuggestion = visibleContext?.suggestions[selectedIndex];
  const menuId = "agent-slash-command-menu";

  return (
    <div className="slash-command-input">
      {visibleContext ? (
        <div
          id={menuId}
          className="slash-command-menu"
          role="listbox"
          aria-label={language === "zh" ? "斜杠命令建议" : "Slash command suggestions"}
        >
          <div className="slash-command-menu-header">
            <strong>
              {visibleContext.kind === "command"
                ? (language === "zh" ? "命令" : "Commands")
                : (language === "zh" ? "选择完整 ID" : "Choose a complete ID")}
            </strong>
            <span>
              {visibleContext.kind === "command"
                ? (language === "zh"
                  ? `单条消息最多 ${catalog.max_commands} 个`
                  : `Up to ${catalog.max_commands} per message`)
                : `/${visibleContext.commandName}`}
            </span>
          </div>
          <div className="slash-command-options">
            {visibleContext.suggestions.length ? visibleContext.suggestions.map((suggestion, index) => (
              <button
                id={suggestion.id}
                key={suggestion.id}
                type="button"
                role="option"
                aria-selected={index === selectedIndex}
                aria-disabled={suggestion.disabled}
                disabled={suggestion.disabled}
                className={index === selectedIndex ? "is-selected" : ""}
                onMouseDown={(event) => event.preventDefault()}
                onMouseEnter={() => {
                  if (!suggestion.disabled) setSelectedIndex(index);
                }}
                onClick={() => chooseSuggestion(suggestion)}
              >
                <span className="slash-command-option-main">
                  <strong>{suggestion.title}</strong>
                  <code>{suggestion.meta}</code>
                </span>
                {suggestion.description ? <small>{suggestion.description}</small> : null}
              </button>
            )) : (
              <div className="slash-command-empty">
                {language === "zh" ? "没有匹配项" : "No matching choices"}
              </div>
            )}
          </div>
          <div className="slash-command-menu-footer">
            <span>↑↓ {language === "zh" ? "选择" : "select"}</span>
            <span>↵ {language === "zh" ? "插入" : "insert"}</span>
            <span>Esc {language === "zh" ? "关闭" : "close"}</span>
          </div>
        </div>
      ) : null}
      <textarea
        ref={textareaRef}
        value={value}
        onChange={(event) => {
          onChange(event.target.value);
          setCursor(event.target.selectionStart);
          setDismissedKey(null);
        }}
        onClick={(event) => setCursor(event.currentTarget.selectionStart)}
        onSelect={(event) => setCursor(event.currentTarget.selectionStart)}
        onKeyUp={(event) => {
          if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
            setCursor(event.currentTarget.selectionStart);
          }
        }}
        onKeyDown={(event) => {
          if (visibleContext?.suggestions.length && event.key === "ArrowDown") {
            event.preventDefault();
            setSelectedIndex((index) => moveSelection(visibleContext.suggestions, index, 1));
            return;
          }
          if (visibleContext?.suggestions.length && event.key === "ArrowUp") {
            event.preventDefault();
            setSelectedIndex((index) => moveSelection(visibleContext.suggestions, index, -1));
            return;
          }
          if (
            activeSuggestion
            && !activeSuggestion.disabled
            && (event.key === "Tab" || (event.key === "Enter" && !event.shiftKey))
          ) {
            event.preventDefault();
            chooseSuggestion(activeSuggestion);
            return;
          }
          if (visibleContext && event.key === "Escape") {
            event.preventDefault();
            setDismissedKey(visibleContext.key);
            return;
          }
          if (
            event.key === "Enter"
            && !event.shiftKey
            && !event.nativeEvent.isComposing
          ) {
            event.preventDefault();
            onSubmit();
          }
        }}
        placeholder={placeholder}
        rows={1}
        disabled={disabled}
        aria-autocomplete="list"
        aria-expanded={Boolean(visibleContext)}
        aria-controls={visibleContext ? menuId : undefined}
        aria-activedescendant={activeSuggestion?.id}
      />
    </div>
  );
};
