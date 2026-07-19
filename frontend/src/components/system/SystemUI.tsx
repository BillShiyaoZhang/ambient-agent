import React, { useEffect, useId, useRef } from "react";
import "./SystemUI.css";

type IconTone = "neutral" | "accent" | "danger";

export interface SystemIconButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  label: string;
  tone?: IconTone;
  selected?: boolean;
}

export const SystemIconButton = React.forwardRef<HTMLButtonElement, SystemIconButtonProps>(function SystemIconButton({
  label,
  tone = "neutral",
  selected,
  className = "",
  children,
  type = "button",
  ...props
}, ref) {
  return (
    <button
      {...props}
      ref={ref}
      type={type}
      aria-label={label}
      aria-pressed={props["aria-expanded"] === undefined ? selected : undefined}
      data-tooltip={label}
      data-tone={tone}
      className={`system-icon-button ${selected ? "is-selected" : ""} ${className}`.trim()}
    >
      {children}
    </button>
  );
});

export interface SystemPopoverProps {
  open: boolean;
  onClose: () => void;
  triggerRef: React.RefObject<HTMLElement | null>;
  label: string;
  className?: string;
  children: React.ReactNode;
}

let activeSystemPopover: { id: symbol; close: () => void } | null = null;

export function SystemPopover({ open, onClose, triggerRef, label, className = "", children }: SystemPopoverProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const popoverIdRef = useRef(Symbol("system-popover"));
  const onCloseRef = useRef(onClose);
  useEffect(() => { onCloseRef.current = onClose; }, [onClose]);

  useEffect(() => {
    if (!open) return;
    const popoverId = popoverIdRef.current;
    if (activeSystemPopover?.id !== popoverId) activeSystemPopover?.close();
    activeSystemPopover = { id: popoverId, close: () => onCloseRef.current() };
    const dismiss = (deferFocus = false) => {
      onCloseRef.current();
      if (deferFocus) requestAnimationFrame(() => triggerRef.current?.focus());
      else triggerRef.current?.focus();
    };
    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!panelRef.current?.contains(target) && !triggerRef.current?.contains(target)) dismiss(true);
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      dismiss();
    };
    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
      if (activeSystemPopover?.id === popoverId) activeSystemPopover = null;
    };
  }, [open, triggerRef]);

  if (!open) return null;
  return <div ref={panelRef} className={`system-popover ${className}`.trim()} role="menu" aria-label={label}>{children}</div>;
}

const FOCUSABLE = "button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])";

function useDialogFocus(open: boolean, containerRef: React.RefObject<HTMLElement | null>, blocking: boolean, onClose?: () => void) {
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const onCloseRef = useRef(onClose);
  useEffect(() => { onCloseRef.current = onClose; }, [onClose]);

  useEffect(() => {
    if (!open) return;
    returnFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const frame = requestAnimationFrame(() => {
      const focusable = containerRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE);
      (focusable?.[0] ?? containerRef.current)?.focus();
    });
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        if (!blocking) {
          onCloseRef.current?.();
        }
        return;
      }
      if (event.key !== "Tab" || !containerRef.current) return;
      const focusable = [...containerRef.current.querySelectorAll<HTMLElement>(FOCUSABLE)];
      if (focusable.length === 0) {
        event.preventDefault();
        containerRef.current.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener("keydown", handleKeyDown);
      requestAnimationFrame(() => returnFocusRef.current?.focus());
    };
  }, [blocking, containerRef, open]);
}

export interface SystemDialogProps {
  open: boolean;
  title: string;
  description?: string;
  onClose?: () => void;
  blocking?: boolean;
  size?: "compact" | "medium" | "large";
  className?: string;
  children: React.ReactNode;
}

export function SystemDialog({ open, title, description, onClose, blocking = false, size = "medium", className = "", children }: SystemDialogProps) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const descriptionId = useId();
  useDialogFocus(open, dialogRef, blocking, onClose);
  if (!open) return null;
  return (
    <div className="system-layer system-dialog-layer">
      <div
        className="system-scrim"
        data-testid="system-dialog-scrim"
        aria-hidden="true"
        onClick={() => { if (!blocking) onClose?.(); }}
      />
      <div
        ref={dialogRef}
        className={`system-dialog system-dialog-${size} ${className}`.trim()}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
        tabIndex={-1}
      >
        <header className="system-dialog-header">
          <div><h2 id={titleId}>{title}</h2>{description ? <p id={descriptionId}>{description}</p> : null}</div>
        </header>
        {children}
      </div>
    </div>
  );
}

export interface SystemDrawerProps {
  open: boolean;
  label: string;
  onClose: () => void;
  side?: "right" | "left";
  className?: string;
  children: React.ReactNode;
}

export function SystemDrawer({ open, label, onClose, side = "right", className = "", children }: SystemDrawerProps) {
  const drawerRef = useRef<HTMLElement>(null);
  useDialogFocus(open, drawerRef, false, onClose);
  return (
    <div className={`system-layer system-drawer-layer ${open ? "is-open" : ""}`} aria-hidden={!open}>
      <button type="button" className="system-scrim" onClick={onClose} tabIndex={open ? 0 : -1} aria-label={`Close ${label}`} />
      <aside ref={drawerRef} className={`system-drawer is-${side} ${className}`.trim()} role="dialog" aria-modal="true" aria-label={label} tabIndex={-1}>
        {children}
      </aside>
    </div>
  );
}

export interface SystemToastProps extends React.HTMLAttributes<HTMLDivElement> {
  tone?: "success" | "warning" | "danger" | "info";
}

export function SystemToast({ tone = "info", className = "", children, ...props }: SystemToastProps) {
  return <div {...props} role="status" data-tone={tone} className={`system-toast ${className}`.trim()}>{children}</div>;
}
