import * as React from "react";
import { cn } from "@/lib/utils";

const fills = {
  emerald: "bg-emerald-400",
  amber: "bg-amber-400",
  rose: "bg-rose-400",
  sky: "bg-sky-400",
  violet: "bg-violet-400",
  neutral: "bg-zinc-400",
} as const;

const tracks = {
  emerald: "bg-emerald-500/15",
  amber: "bg-amber-500/15",
  rose: "bg-rose-500/15",
  sky: "bg-sky-500/15",
  violet: "bg-violet-500/15",
  neutral: "bg-zinc-500/15",
} as const;

export type ProgressColor = keyof typeof fills;

export interface ProgressProps extends React.ComponentProps<"div"> {
  /** 0..100. Omit for an indeterminate bar. */
  value?: number | null;
  color?: ProgressColor;
  /** Height class; default "h-1.5". */
  size?: "xs" | "sm" | "md";
  /** Animated stripes (for active work). */
  striped?: boolean;
  label?: string;
}

/** Meter-style progress: the track is a lighter step of the same hue so state reads across the bar. */
export function Progress({ value, color = "emerald", size = "sm", striped = false, label, className, ...props }: ProgressProps) {
  const indeterminate = value === null || value === undefined || Number.isNaN(value);
  const pct = indeterminate ? 0 : Math.min(100, Math.max(0, value));
  const height = size === "xs" ? "h-1" : size === "md" ? "h-2.5" : "h-1.5";
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={indeterminate ? undefined : Math.round(pct)}
      data-slot="progress"
      className={cn("relative w-full overflow-hidden rounded-full", height, tracks[color], className)}
      {...props}
    >
      <div
        className={cn(
          "h-full rounded-full transition-[width] duration-500 ease-out",
          fills[color],
          striped && "progress-stripes",
          indeterminate && "w-1/3 animate-[indeterminate_1.4s_ease-in-out_infinite]",
        )}
        style={indeterminate ? undefined : { width: `${pct}%` }}
      />
      {indeterminate ? (
        <style>{`@keyframes indeterminate{0%{transform:translateX(-120%)}100%{transform:translateX(320%)}}`}</style>
      ) : null}
    </div>
  );
}
