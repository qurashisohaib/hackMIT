"use client";

import * as React from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

interface DialogContextValue {
  open: boolean;
  setOpen: (open: boolean) => void;
  titleId: string;
  descriptionId: string;
}

const DialogContext = React.createContext<DialogContextValue | null>(null);

const subscribeNoop = () => () => {};

function useDialog(component: string): DialogContextValue {
  const ctx = React.useContext(DialogContext);
  if (!ctx) throw new Error(`<${component}> must be used inside <Dialog>`);
  return ctx;
}

export interface DialogProps {
  open?: boolean;
  defaultOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
  children: React.ReactNode;
}

/** Centered modal dialog. Escape / overlay click close it; focus is trapped inside. */
export function Dialog({ open, defaultOpen = false, onOpenChange, children }: DialogProps) {
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
  return <DialogContext.Provider value={ctx}>{children}</DialogContext.Provider>;
}

export function DialogTrigger({ onClick, ...props }: React.ComponentProps<"button">) {
  const { setOpen } = useDialog("DialogTrigger");
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

export function DialogClose({ onClick, ...props }: React.ComponentProps<"button">) {
  const { setOpen } = useDialog("DialogClose");
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

export interface DialogContentProps extends React.ComponentProps<"div"> {
  hideClose?: boolean;
}

export function DialogContent({ hideClose = false, className, children, ...props }: DialogContentProps) {
  const { open, setOpen, titleId, descriptionId } = useDialog("DialogContent");
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
        '[data-autofocus], input, textarea, select, button:not([data-dialog-close]), [href], [tabindex]:not([tabindex="-1"])',
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
    <div data-slot="dialog-root" className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div
        data-slot="dialog-overlay"
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
        data-slot="dialog-content"
        className={cn(
          "relative flex w-full max-w-md flex-col gap-4 rounded-xl border border-border-strong bg-surface p-6 text-foreground shadow-2xl outline-none animate-zoom-in",
          className,
        )}
        {...props}
      >
        {children}
        {hideClose ? null : (
          <button
            type="button"
            data-dialog-close
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

export function DialogHeader({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="dialog-header" className={cn("flex flex-col gap-1.5 pr-8", className)} {...props} />;
}

export function DialogTitle({ className, ...props }: React.ComponentProps<"h2">) {
  const { titleId } = useDialog("DialogTitle");
  return <h2 id={titleId} data-slot="dialog-title" className={cn("text-base font-semibold tracking-tight", className)} {...props} />;
}

export function DialogDescription({ className, ...props }: React.ComponentProps<"p">) {
  const { descriptionId } = useDialog("DialogDescription");
  return <p id={descriptionId} data-slot="dialog-description" className={cn("text-sm leading-relaxed text-muted", className)} {...props} />;
}

export function DialogFooter({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="dialog-footer" className={cn("flex items-center justify-end gap-2 pt-2", className)} {...props} />;
}
