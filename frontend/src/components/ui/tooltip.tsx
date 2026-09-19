"use client";

import * as React from "react";
import { createPortal } from "react-dom";
import { cn } from "@/lib/utils";

export interface TooltipProps {
  /** Tooltip body. Strings and nodes both work. */
  content: React.ReactNode;
  children: React.ReactElement<Record<string, unknown>>;
  side?: "top" | "bottom" | "left" | "right";
  /** Delay before showing, ms. Default 150. */
  delay?: number;
  className?: string;
  /** Disable the tooltip entirely (renders children only). */
  disabled?: boolean;
}

type Handler<E> = ((e: E) => void) | undefined;

/**
 * Lightweight tooltip: shows on hover and keyboard focus, portals to <body>
 * so it is never clipped by overflow containers. The child must be a single
 * element that accepts mouse/focus handlers and `aria-describedby`.
 */
export function Tooltip({ content, children, side = "top", delay = 150, className, disabled = false }: TooltipProps) {
  const id = React.useId();
  const [open, setOpen] = React.useState(false);
  const [pos, setPos] = React.useState<{ x: number; y: number } | null>(null);
  const anchor = React.useRef<HTMLElement | null>(null);
  const timer = React.useRef<number | null>(null);

  const measure = React.useCallback(() => {
    const el = anchor.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const gap = 8;
    let x = r.left + r.width / 2;
    let y = r.top - gap;
    if (side === "bottom") y = r.bottom + gap;
    if (side === "left") {
      x = r.left - gap;
      y = r.top + r.height / 2;
    }
    if (side === "right") {
      x = r.right + gap;
      y = r.top + r.height / 2;
    }
    setPos({ x, y });
  }, [side]);

  const show = React.useCallback(
    (target: EventTarget | null) => {
      if (disabled) return;
      if (target instanceof HTMLElement) anchor.current = target;
      if (timer.current) window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => {
        measure();
        setOpen(true);
      }, delay);
    },
    [delay, disabled, measure],
  );

  const hide = React.useCallback(() => {
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = null;
    setOpen(false);
  }, []);

  React.useEffect(() => {
    if (!open) return;
    const onScroll = () => measure();
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onScroll);
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && hide();
    document.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onScroll);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, measure, hide]);

  React.useEffect(
    () => () => {
      if (timer.current) window.clearTimeout(timer.current);
    },
    [],
  );

  const childProps = children.props;
  const child = React.cloneElement(children, {
    "aria-describedby": open ? id : (childProps["aria-describedby"] as string | undefined),
    onMouseEnter: (e: React.MouseEvent) => {
      (childProps.onMouseEnter as Handler<React.MouseEvent>)?.(e);
      show(e.currentTarget);
    },
    onMouseLeave: (e: React.MouseEvent) => {
      (childProps.onMouseLeave as Handler<React.MouseEvent>)?.(e);
      hide();
    },
    onFocus: (e: React.FocusEvent) => {
      (childProps.onFocus as Handler<React.FocusEvent>)?.(e);
      show(e.currentTarget);
    },
    onBlur: (e: React.FocusEvent) => {
      (childProps.onBlur as Handler<React.FocusEvent>)?.(e);
      hide();
    },
  });

  const transform =
    side === "top"
      ? "translate(-50%, -100%)"
      : side === "bottom"
        ? "translate(-50%, 0)"
        : side === "left"
          ? "translate(-100%, -50%)"
          : "translate(0, -50%)";

  return (
    <>
      {child}
      {open && pos && typeof document !== "undefined"
        ? createPortal(
            <div
              role="tooltip"
              id={id}
              style={{ position: "fixed", left: pos.x, top: pos.y, transform }}
              className={cn(
                "pointer-events-none z-[60] max-w-xs rounded-md border border-border-strong bg-surface-3 px-2.5 py-1.5 text-xs leading-snug text-foreground shadow-lg animate-fade-in",
                className,
              )}
            >
              {content}
            </div>,
            document.body,
          )
        : null}
    </>
  );
}
