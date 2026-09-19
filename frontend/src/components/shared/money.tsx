import * as React from "react";
import { cn, formatMoney, type MoneyOptions } from "@/lib/utils";

export interface MoneyProps extends Omit<React.ComponentProps<"span">, "children">, MoneyOptions {
  value: number | null | undefined;
  /** Tint negatives rose and (when `signed`) positives emerald. Default false. */
  colorize?: boolean;
  /** Dim the cents. Default true when not compact/whole. */
  dimCents?: boolean;
}

/** Monospace, tabular currency figure. */
export function Money({ value, whole, compact, signed, colorize = false, dimCents, className, ...props }: MoneyProps) {
  const text = formatMoney(value, { whole, compact, signed });
  const tone =
    colorize && value !== null && value !== undefined
      ? value < 0
        ? "text-rose-300"
        : signed && value > 0
          ? "text-emerald-300"
          : ""
      : "";
  const shouldDim = dimCents ?? (!compact && !whole);
  const dot = text.lastIndexOf(".");
  const main = shouldDim && dot > 0 ? text.slice(0, dot) : text;
  const cents = shouldDim && dot > 0 ? text.slice(dot) : "";
  return (
    <span data-slot="money" className={cn("font-mono tabular-nums whitespace-nowrap", tone, className)} {...props}>
      {main}
      {cents ? <span className="opacity-60">{cents}</span> : null}
    </span>
  );
}
