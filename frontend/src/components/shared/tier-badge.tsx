import * as React from "react";
import { Eye, ShieldCheck, User } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type { Tier } from "@/lib/types";
import { cn } from "@/lib/utils";

const TIERS = {
  high: { variant: "emerald" as const, icon: ShieldCheck, label: "High", hint: "auto-executes" },
  medium: { variant: "amber" as const, icon: Eye, label: "Medium", hint: "audit re-performs" },
  low: { variant: "rose" as const, icon: User, label: "Low", hint: "human review" },
};

/** Derive a tier from a confidence value using the architecture thresholds. */
export function tierFromConfidence(confidence: number | null | undefined): Tier | null {
  if (confidence === null || confidence === undefined || Number.isNaN(confidence)) return null;
  if (confidence >= 0.85) return "high";
  if (confidence >= 0.6) return "medium";
  return "low";
}

export interface TierBadgeProps extends Omit<React.ComponentProps<"span">, "children"> {
  tier: Tier | string | null | undefined;
  /** Fallback when `tier` is missing: derive from confidence. */
  confidence?: number | null;
  size?: "sm" | "md" | "lg";
  /** Append the consequence ("· auto", "· audit", "· human"). */
  showHint?: boolean;
  /** Icon only. */
  compact?: boolean;
}

/** HIGH ≥ 0.85 executes · MEDIUM 0.60–0.85 audited · LOW < 0.60 human. */
export function TierBadge({ tier, confidence, size = "md", showHint = false, compact = false, className, ...props }: TierBadgeProps) {
  const key = ((tier ?? tierFromConfidence(confidence) ?? "") as string).toLowerCase() as keyof typeof TIERS;
  const t = TIERS[key];
  if (!t) {
    return (
      <Badge variant="outline" size={size} className={className} {...props}>
        —
      </Badge>
    );
  }
  const Icon = t.icon;
  return (
    <Badge
      variant={t.variant}
      size={size}
      className={cn("uppercase tracking-wide", className)}
      title={`${t.label} confidence · ${t.hint}`}
      aria-label={compact ? `${t.label} confidence` : undefined}
      {...props}
    >
      <Icon aria-hidden />
      {compact ? null : t.label}
      {showHint && !compact ? <span className="font-normal normal-case tracking-normal opacity-80">· {t.hint.split(" ")[0]}</span> : null}
    </Badge>
  );
}
