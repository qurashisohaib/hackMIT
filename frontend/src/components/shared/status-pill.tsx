import * as React from "react";
import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { LiveDot, type LiveDotColor } from "@/components/shared/live-dot";
import { cn, humanize } from "@/lib/utils";

export type StatusKind = "auto" | "period" | "run" | "exception" | "decision" | "rule" | "checklist";

interface StatusStyle {
  variant: BadgeVariant;
  dot: LiveDotColor;
  label: string;
  pulse?: boolean;
}

const COMMON: Record<string, StatusStyle> = {
  // periods
  closed: { variant: "emerald", dot: "emerald", label: "Closed" },
  pending_review: { variant: "amber", dot: "amber", label: "Pending review" },
  // runs
  queued: { variant: "zinc", dot: "zinc", label: "Queued" },
  running: { variant: "sky", dot: "sky", label: "Running", pulse: true },
  completed: { variant: "emerald", dot: "emerald", label: "Completed" },
  failed: { variant: "rose", dot: "rose", label: "Failed" },
  // exceptions
  open: { variant: "sky", dot: "sky", label: "Open" },
  pending_audit: { variant: "amber", dot: "amber", label: "Pending audit" },
  needs_human: { variant: "rose", dot: "rose", label: "Needs human" },
  resolved: { variant: "emerald", dot: "emerald", label: "Resolved" },
  dismissed: { variant: "zinc", dot: "zinc", label: "Dismissed" },
  // decisions
  proposed: { variant: "zinc", dot: "zinc", label: "Proposed" },
  executed: { variant: "emerald", dot: "emerald", label: "Executed" },
  rejected: { variant: "rose", dot: "rose", label: "Rejected" },
  escalated: { variant: "rose", dot: "rose", label: "Escalated" },
  // rules
  active: { variant: "emerald", dot: "emerald", label: "Active" },
  superseded: { variant: "zinc", dot: "zinc", label: "Superseded" },
  // checklist / audit
  done: { variant: "emerald", dot: "emerald", label: "Done" },
  warning: { variant: "amber", dot: "amber", label: "Warning" },
  blocked: { variant: "rose", dot: "rose", label: "Blocked" },
  approved: { variant: "emerald", dot: "emerald", label: "Approved" },
  ok: { variant: "emerald", dot: "emerald", label: "OK" },
};

const PERIOD_OVERRIDES: Record<string, StatusStyle> = {
  open: { variant: "zinc", dot: "zinc", label: "Open" },
  ready: { variant: "zinc", dot: "zinc", label: "Ready" },
};

export function statusStyle(status: string | null | undefined, kind: StatusKind = "auto"): StatusStyle {
  const key = (status ?? "").toLowerCase();
  if (kind === "period" && key in PERIOD_OVERRIDES) return PERIOD_OVERRIDES[key];
  return COMMON[key] ?? { variant: "outline", dot: "zinc", label: humanize(status) };
}

export interface StatusPillProps extends Omit<React.ComponentProps<"span">, "children"> {
  status: string | null | undefined;
  kind?: StatusKind;
  size?: "sm" | "md" | "lg";
  /** Override the label text. */
  label?: string;
  /** Hide the dot. */
  noDot?: boolean;
}

/** Coloured status badge with a dot; statuses map to a fixed palette across pages. */
export function StatusPill({ status, kind = "auto", size = "md", label, noDot = false, className, ...props }: StatusPillProps) {
  const s = statusStyle(status, kind);
  return (
    <Badge variant={s.variant} size={size} className={cn("gap-1.5", className)} {...props}>
      {noDot ? null : <LiveDot color={s.dot} pulse={s.pulse} size="xs" />}
      {label ?? s.label}
    </Badge>
  );
}
