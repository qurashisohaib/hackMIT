import * as React from "react";
import { cn } from "@/lib/utils";

const colors = {
  emerald: "bg-emerald-400",
  amber: "bg-amber-400",
  rose: "bg-rose-400",
  sky: "bg-sky-400",
  violet: "bg-violet-400",
  zinc: "bg-zinc-500",
} as const;

export type LiveDotColor = keyof typeof colors;

export interface LiveDotProps extends React.ComponentProps<"span"> {
  color?: LiveDotColor;
  /** Animated ping ring (for "live"/"running" states). */
  pulse?: boolean;
  size?: "xs" | "sm" | "md";
}

/** Status dot with an optional ping animation. */
export function LiveDot({ color = "emerald", pulse = false, size = "sm", className, ...props }: LiveDotProps) {
  const dim = size === "xs" ? "size-1.5" : size === "md" ? "size-2.5" : "size-2";
  return (
    <span data-slot="live-dot" className={cn("relative inline-flex shrink-0", dim, className)} aria-hidden {...props}>
      {pulse ? <span className={cn("absolute inset-0 rounded-full opacity-75 animate-live-ping", colors[color])} /> : null}
      <span className={cn("relative inline-flex h-full w-full rounded-full", colors[color])} />
    </span>
  );
}
