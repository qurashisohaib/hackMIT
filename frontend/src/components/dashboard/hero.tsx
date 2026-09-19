"use client";

import * as React from "react";
import { BrainBadge } from "@/components/layout/topbar";
import { useHealth } from "@/components/layout/health-provider";
import { ResetDemoButton } from "@/components/dashboard/reset-demo-button";
import { Skeleton } from "@/components/ui/skeleton";
import type { MemoryStats, MetricsResponse, PeriodView } from "@/lib/types";
import { formatNumber, formatPercent } from "@/lib/utils";

export interface DashboardHeroProps {
  periods: PeriodView[] | null;
  metrics: MetricsResponse | null;
  memoryStats: MemoryStats | null;
  loading: boolean;
  onReset: () => void;
}

function Fact({ label, value, loading }: { label: string; value: React.ReactNode; loading?: boolean }) {
  return (
    <div className="min-w-0">
      <p className="font-mono text-[10px] tracking-[0.16em] text-muted uppercase">{label}</p>
      {loading ? <Skeleton className="mt-1.5 h-5 w-14" /> : <p className="mt-0.5 text-lg font-semibold tracking-tight text-foreground">{value}</p>}
    </div>
  );
}

/** Company + "Office of the CFO" + brain badge + headline facts + reset. */
export function DashboardHero({ periods, metrics, memoryStats, loading, onReset }: DashboardHeroProps) {
  const { health, online } = useHealth();
  const company = health?.company?.trim() || "Office of the CFO";

  const closed = periods?.filter((p) => p.status === "closed").length ?? 0;
  const total = periods?.length ?? 0;
  const latest = metrics?.periods?.length ? metrics.periods[metrics.periods.length - 1] : null;
  const rules =
    (typeof memoryStats?.rules === "number" ? memoryStats.rules : memoryStats?.by_label?.Rule) ?? (memoryStats ? 0 : null);

  return (
    <section
      aria-labelledby="hero-title"
      className="relative overflow-hidden rounded-2xl border border-border bg-surface p-6 sm:p-8"
    >
      <div className="bg-grid pointer-events-none absolute inset-0 opacity-40 [mask-image:radial-gradient(60%_80%_at_20%_0%,black,transparent)]" aria-hidden />
      <div
        className="pointer-events-none absolute -top-24 -left-24 size-72 rounded-full bg-emerald-500/15 blur-3xl"
        aria-hidden
      />
      <div className="relative flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
        <div className="min-w-0 space-y-3">
          <p className="font-mono text-[11px] tracking-[0.2em] text-emerald-300/90 uppercase">AI Office of the CFO</p>
          <div className="flex flex-wrap items-center gap-3">
            <h1 id="hero-title" className="text-3xl font-semibold tracking-tight text-foreground sm:text-4xl">
              {loading && online === null ? <Skeleton className="h-9 w-64" /> : company}
            </h1>
            <BrainBadge size="lg" />
          </div>
          <p className="max-w-2xl text-sm leading-relaxed text-muted">
            Seven specialised finance agents run the period close under a CFO orchestrator and share one{" "}
            <span className="text-foreground">Financial Memory Graph</span>. Every hypothesis, decision and human correction
            becomes precedent — so what you teach in January runs autonomously by March.
          </p>
        </div>

        <div className="flex shrink-0 flex-col gap-4 sm:flex-row sm:items-end lg:flex-col lg:items-end">
          <div className="grid grid-cols-3 gap-6">
            <Fact label="Periods closed" value={`${closed} / ${total || 3}`} loading={loading} />
            <Fact label="Rules learned" value={rules === null ? "—" : formatNumber(rules)} loading={loading} />
            <Fact label="Latest accuracy" value={latest ? formatPercent(latest.accuracy) : "—"} loading={loading} />
          </div>
          <ResetDemoButton onReset={onReset} disabled={online === false} />
        </div>
      </div>
    </section>
  );
}
