"use client";

import * as React from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

interface SheetContextValue {
  open: boolean;
  setOpen: (open: boolean) => void;
  titleId: string;
  descriptionId: string;
}

const SheetContext = React.createContext<SheetContextValue | null>(null);

const subscribeNoop = () => () => {};

function useSheet(component: string): SheetContextValue {
  const ctx = React.useContext(SheetContext);
  if (!ctx) throw new Error(`<${component}> must be used inside <Sheet>`);
  return ctx;
}

export interface SheetProps {
  open?: boolean;
  defaultOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
  children: React.ReactNode;
}

/**
 * Side drawer (shadcn "Sheet"). Controlled via `open`/`onOpenChange`.
 * Escape and overlay click close it; focus moves into the panel on open and
 * returns to the previously focused element on close; body scroll is locked.
 */
export function Sheet({ open, defaultOpen = false, onOpenChange, children }: SheetProps) {
  const [internal, setInternal] = React.useState(defaultOpen);
  const isControlled = open !== undefined;
  const isOpen = isControlled ? open : internal;
  const id = React.useId();
  const setOpen = React.useCallback(
    (v: boolean) => {
      if (!isControlled) setInternal(v);
      onOpenChange?.(v);
    },
    [isControlled, onOpenChange],
  );
  const ctx = React.useMemo(
    () => ({ open: isOpen, setOpen, titleId: `${id}-title`, descriptionId: `${id}-desc` }),
    [isOpen, setOpen, id],
  );
  return <SheetContext.Provider value={ctx}>{children}</SheetContext.Provider>;
}

export function SheetTrigger({ onClick, ...props }: React.ComponentProps<"button">) {
  const { setOpen } = useSheet("SheetTrigger");
  return (
    <button
      type="button"
      onClick={(e) => {
        onClick?.(e);
        if (!e.defaultPrevented) setOpen(true);
      }}
      {...props}
    />
  );
}

export function SheetClose({ onClick, ...props }: React.ComponentProps<"button">) {
  const { setOpen } = useSheet("SheetClose");
  return (
    <button
      type="button"
      onClick={(e) => {
        onClick?.(e);
        if (!e.defaultPrevented) setOpen(false);
      }}
      {...props}
    />
  );
}

const sideClasses = {
  right: "inset-y-0 right-0 h-full w-full max-w-[min(100vw,var(--sheet-width))] border-l animate-slide-in-right",
  left: "inset-y-0 left-0 h-full w-full max-w-[min(100vw,var(--sheet-width))] border-r animate-slide-in-left",
  bottom: "inset-x-0 bottom-0 max-h-[85vh] w-full rounded-t-2xl border-t animate-slide-in-bottom",
} as const;

export interface SheetContentProps extends React.ComponentProps<"div"> {
  side?: keyof typeof sideClasses;
  /** CSS width for left/right sheets. Default "40rem". */
  width?: string;
  /** Hide the built-in close button. */
  hideClose?: boolean;
}

export function SheetContent({ side = "right", width = "40rem", hideClose = false, className, children, style, ...props }: SheetContentProps) {
  const { open, setOpen, titleId, descriptionId } = useSheet("SheetContent");
  const panelRef = React.useRef<HTMLDivElement>(null);
  const mounted = React.useSyncExternalStore(subscribeNoop, () => true, () => false);

  React.useEffect(() => {
    if (!open) return;
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const focusTimer = window.setTimeout(() => {
      const panel = panelRef.current;
      if (!panel) return;
      const focusable = panel.querySelector<HTMLElement>(
        '[data-autofocus], input, textarea, select, button:not([data-sheet-close]), [href], [tabindex]:not([tabindex="-1"])',
      );
      (focusable ?? panel).focus({ preventScroll: true });
    }, 20);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        setOpen(false);
      }
      if (e.key === "Tab" && panelRef.current) {
        const items = Array.from(
          panelRef.current.querySelectorAll<HTMLElement>(
            'a[href], button:not([disabled]), textarea, input, select, [tabindex]:not([tabindex="-1"])',
          ),
        ).filter((el) => !el.hasAttribute("disabled") && el.offsetParent !== null);
        if (items.length === 0) return;
        const first = items[0];
        const last = items[items.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      window.clearTimeout(focusTimer);
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
      previouslyFocused?.focus?.({ preventScroll: true });
    };
  }, [open, setOpen]);

  if (!open || !mounted) return null;

  return createPortal(
    <div data-slot="sheet-root" className="fixed inset-0 z-50">
      <div
        data-slot="sheet-overlay"
        className="absolute inset-0 bg-black/60 backdrop-blur-[2px] animate-fade-in"
        onClick={() => setOpen(false)}
        aria-hidden
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        tabIndex={-1}
        data-slot="sheet-content"
        style={{ ["--sheet-width" as string]: width, ...style }}
        className={cn(
          "absolute flex flex-col border-border bg-surface text-foreground shadow-2xl outline-none",
          sideClasses[side],
          className,
        )}
        {...props}
      >
        {children}
        {hideClose ? null : (
          <button
            type="button"
            data-sheet-close
            onClick={() => setOpen(false)}
            aria-label="Close"
            className="absolute top-4 right-4 inline-flex size-8 items-center justify-center rounded-md text-muted transition-colors hover:bg-surface-2 hover:text-foreground"
          >
            <X className="size-4" />
          </button>
        )}
      </div>
    </div>,
    document.body,
  );
}

export function SheetHeader({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="sheet-header" className={cn("flex flex-col gap-1.5 border-b border-border px-6 py-5 pr-14", className)} {...props} />;
}

export function SheetTitle({ className, ...props }: React.ComponentProps<"h2">) {
  const { titleId } = useSheet("SheetTitle");
  return <h2 id={titleId} data-slot="sheet-title" className={cn("text-base font-semibold tracking-tight", className)} {...props} />;
}

export function SheetDescription({ className, ...props }: React.ComponentProps<"p">) {
  const { descriptionId } = useSheet("SheetDescription");
  return <p id={descriptionId} data-slot="sheet-description" className={cn("text-xs text-muted", className)} {...props} />;
}

export function SheetBody({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="sheet-body" className={cn("min-h-0 flex-1 overflow-y-auto px-6 py-5", className)} {...props} />;
}

export function SheetFooter({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="sheet-footer" className={cn("flex items-center justify-end gap-2 border-t border-border px-6 py-4", className)} {...props} />;
}
