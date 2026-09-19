import * as React from "react";
import { tierFromConfidence } from "@/components/shared/tier-badge";
import { cn, formatPercent } from "@/lib/utils";

const TONES = {
  high: { text: "text-emerald-200", bar: "bg-emerald-400", track: "bg-emerald-500/15", border: "border-emerald-400/30 bg-emerald-500/10" },
  medium: { text: "text-amber-200", bar: "bg-amber-400", track: "bg-amber-500/15", border: "border-amber-400/30 bg-amber-500/10" },
  low: { text: "text-rose-200", bar: "bg-rose-400", track: "bg-rose-500/15", border: "border-rose-400/30 bg-rose-500/10" },
  none: { text: "text-muted", bar: "bg-zinc-500", track: "bg-zinc-500/15", border: "border-border-strong bg-surface-2" },
} as const;

export interface ConfidenceBadgeProps extends Omit<React.ComponentProps<"span">, "children"> {
  /** 0..1 */
  confidence: number | null | undefined;
  /** Show a tiny meter under the number. Default true. */
  showBar?: boolean;
  size?: "sm" | "md";
  digits?: number;
}

/** Monospace confidence percentage, coloured by tier, with a small meter. */
export function ConfidenceBadge({ confidence, showBar = true, size = "md", digits = 0, className, ...props }: ConfidenceBadgeProps) {
  const tier = tierFromConfidence(confidence) ?? "none";
  const tone = TONES[tier];
  const pct = confidence === null || confidence === undefined ? 0 : Math.round(Math.min(1, Math.max(0, confidence)) * 100);
  return (
    <span
      data-slot="confidence-badge"
      title={tier === "none" ? "No confidence yet" : `${pct}% confidence · ${tier} tier`}
      className={cn(
        "inline-flex shrink-0 flex-col items-stretch justify-center gap-1 rounded-md border px-2",
        size === "sm" ? "h-6 min-w-12" : "h-8 min-w-14 px-2.5",
        tone.border,
        className,
      )}
      {...props}
    >
      <span className={cn("font-mono leading-none tabular-nums", size === "sm" ? "text-[11px]" : "text-xs", tone.text)}>
        {formatPercent(confidence, digits)}
      </span>
      {showBar ? (
        <span className={cn("h-0.5 w-full overflow-hidden rounded-full", tone.track)} aria-hidden>
          <span className={cn("block h-full rounded-full transition-[width] duration-500", tone.bar)} style={{ width: `${pct}%` }} />
        </span>
      ) : null}
    </span>
  );
}
