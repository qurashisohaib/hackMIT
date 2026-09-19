"use client";

import * as React from "react";
import { Cpu, RefreshCw, Sparkles } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Tooltip } from "@/components/ui/tooltip";
import { LiveDot } from "@/components/shared/live-dot";
import { isLlmBrain, useHealth } from "@/components/layout/health-provider";
import { cn } from "@/lib/utils";

export const DEFAULT_COMPANY = "Office of the CFO";

/** "OpenAI Agents" vs "Deterministic" brain indicator, from GET /api/health. */
export function BrainBadge({ size = "md", className }: { size?: "sm" | "md" | "lg"; className?: string }) {
  const { health, online } = useHealth();
  if (online === null) {
    return (
      <Badge variant="outline" size={size} className={cn("text-muted", className)}>
        <Cpu aria-hidden /> Brain…
      </Badge>
    );
  }
  if (!online || !health) {
    return (
      <Badge variant="zinc" size={size} className={className} title="Backend offline">
        <Cpu aria-hidden /> Brain unknown
      </Badge>
    );
  }
  const llm = isLlmBrain(health);
  return (
    <Badge
      variant={llm ? "violet" : "zinc"}
      size={size}
      className={className}
      title={llm ? `LLM brain: ${health.brain}` : `Deterministic brain: ${health.brain}`}
    >
      {llm ? <Sparkles aria-hidden /> : <Cpu aria-hidden />}
      {llm ? "OpenAI Agents" : "Deterministic"}
    </Badge>
  );
}

/** Backend liveness pill. */
export function HealthPill({ className }: { className?: string }) {
  const { online, checking, refresh, error, lastChecked } = useHealth();
  const label = online === null ? "Checking backend" : online ? "Backend online" : "Backend offline";
  const color = online === null ? "zinc" : online ? "emerald" : "rose";
  return (
    <Tooltip
      content={
        <span className="block space-y-0.5">
          <span className="block font-medium">{label}</span>
          {error ? <span className="block text-muted">{error}</span> : null}
          {lastChecked ? <span className="block font-mono text-[10px] text-muted">checked {new Date(lastChecked).toLocaleTimeString()}</span> : null}
        </span>
      }
    >
      <button
        type="button"
        onClick={() => void refresh()}
        aria-label={`${label}. Click to re-check.`}
        className={cn(
          "inline-flex h-7 items-center gap-2 rounded-md border border-border-strong bg-surface-2 px-2.5 text-xs text-muted transition-colors hover:text-foreground",
          className,
        )}
      >
        <LiveDot color={color} pulse={online === true} size="xs" />
        <span className="hidden sm:inline">{online === null ? "Checking…" : online ? "Online" : "Offline"}</span>
        <RefreshCw className={cn("size-3", checking && "animate-spin")} aria-hidden />
      </button>
    </Tooltip>
  );
}

export function Topbar() {
  const { health, online } = useHealth();
  const company = health?.company?.trim() || DEFAULT_COMPANY;
  return (
    <header
      data-slot="topbar"
      className="sticky top-0 z-30 flex h-14 items-center justify-between gap-3 border-b border-border bg-background/80 px-4 backdrop-blur sm:px-6 lg:px-8"
    >
      <div className="flex min-w-0 items-center gap-3">
        <div className="min-w-0 leading-tight">
          <p className="truncate text-sm font-semibold tracking-tight">{company}</p>
          <p className="hidden truncate font-mono text-[10px] tracking-[0.16em] text-muted uppercase sm:block">
            {online ? "AI Office of the CFO · period close" : "AI Office of the CFO"}
          </p>
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {health?.version ? (
          <span className="hidden font-mono text-[11px] text-subtle md:inline" title="Backend version">
            v{health.version}
          </span>
        ) : null}
        <BrainBadge />
        <HealthPill />
      </div>
    </header>
  );
}
