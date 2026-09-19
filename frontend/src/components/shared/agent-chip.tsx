import * as React from "react";
import { getAgent } from "@/lib/agents";
import { cn } from "@/lib/utils";

export interface AgentChipProps extends Omit<React.ComponentProps<"span">, "children"> {
  /** Agent id (cfo, ap_ar, recon, audit, close, forecast, report, human, system) or any backend string. */
  agent: string | null | undefined;
  size?: "xs" | "sm" | "md";
  /** Show the lucide icon. Default true. */
  showIcon?: boolean;
  /** Use the long name ("Reconciliation") instead of the short one ("Recon"). */
  long?: boolean;
  /** Icon only, with an accessible label. */
  iconOnly?: boolean;
}

/** Colour-coded agent identity chip. Colour is fixed per agent across the whole app. */
export function AgentChip({ agent, size = "sm", showIcon = true, long = false, iconOnly = false, className, ...props }: AgentChipProps) {
  const meta = getAgent(agent);
  const Icon = meta.icon;
  const label = long ? meta.name : meta.short;
  const dims = size === "xs" ? "h-5 px-1.5 text-[10.5px] gap-1 [&_svg]:size-3" : size === "md" ? "h-7 px-2.5 text-sm gap-1.5 [&_svg]:size-4" : "h-6 px-2 text-xs gap-1.5 [&_svg]:size-3.5";
  return (
    <span
      data-slot="agent-chip"
      data-agent={meta.id}
      title={`${meta.name} — ${meta.role}`}
      aria-label={iconOnly ? meta.name : undefined}
      className={cn(
        "inline-flex shrink-0 items-center rounded-md border font-medium whitespace-nowrap leading-none",
        meta.chip,
        dims,
        iconOnly && "px-0 justify-center aspect-square",
        className,
      )}
      {...props}
    >
      {showIcon || iconOnly ? <Icon aria-hidden /> : null}
      {iconOnly ? null : label}
    </span>
  );
}
