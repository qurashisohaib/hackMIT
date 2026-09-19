"use client";

import * as React from "react";
import { Brain, Footprints, ShieldCheck, Timer, type LucideIcon } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { MetricsResponse, RunMetrics } from "@/lib/types";
import { cn, formatDecimal, formatMs, formatNumber, formatPercent, periodMonthShort } from "@/lib/utils";

interface TileSpec {
  key: string;
  label: string;
  icon: LucideIcon;
  iconTone: string;
  value: (m: RunMetrics) => number | null;
  format: (v: number) => string;
  /** Format a delta between two values. */
  delta: (curr: number, prev: number) => string;
  upIsGood: boolean;
}

const TILES: TileSpec[] = [
  {
    key: "precedent_hits",
    label: "Precedent hits",
    icon: Brain,
    iconTone: "text-violet-300 bg-violet-500/12",
    value: (m) => m.precedent_hits,
    format: (v) => formatNumber(v),
    delta: (c, p) => formatNumber(c - p, { signed: true }),
    upIsGood: true,
  },
  {
    key: "avg_steps",
    label: "Avg steps / exception",
    icon: Footprints,
    iconTone: "text-sky-300 bg-sky-500/12",
    value: (m) => m.avg_steps_per_exception,
    format: (v) => formatDecimal(v, 1),
    delta: (c, p) => `${c - p > 0 ? "+" : c - p < 0 ? "−" : ""}${Math.abs(c - p).toFixed(1)}`,
    upIsGood: false,
  },
  {
    key: "avg_ms",
    label: "Avg time / exception",
    icon: Timer,
    iconTone: "text-cyan-300 bg-cyan-500/12",
    value: (m) => m.avg_ms_per_exception,
    format: (v) => formatMs(v),
    delta: (c, p) => `${c - p > 0 ? "+" : c - p < 0 ? "−" : ""}${formatMs(Math.abs(c - p))}`,
    upIsGood: false,
  },
  {
    key: "audit_pass",
    label: "Audit pass rate",
    icon: ShieldCheck,
    iconTone: "text-amber-300 bg-amber-500/12",
    value: (m) => (m.audit_challenges > 0 ? m.audit_passed / m.audit_challenges : null),
    format: (v) => formatPercent(v),
    delta: (c, p) => {
      const pp = (c - p) * 100;
      return `${pp > 0 ? "+" : pp < 0 ? "−" : ""}${Math.abs(pp).toFixed(0)} pp`;
    },
    upIsGood: true,
  },
];

function Tile({ spec, latest, prev }: { spec: TileSpec; latest: RunMetrics | null; prev: RunMetrics | null }) {
  const Icon = spec.icon;
  const v = latest ? spec.value(latest) : null;
  const pv = prev ? spec.value(prev) : null;
  const d = v !== null && pv !== null ? v - pv : null;
  const tone = d === null || d === 0 ? "text-subtle" : (d > 0) === spec.upIsGood ? "text-emerald-300" : "text-rose-300";
  return (
    <Card className="flex flex-col gap-3 p-4">
      <div className="flex items-center justify-between gap-2">
        <p className="text-[11px] text-muted">{spec.label}</p>
        <span className={cn("inline-flex size-6 items-center justify-center rounded-md", spec.iconTone)}>
          <Icon className="size-3.5" aria-hidden />
        </span>
      </div>
      <p className="text-2xl leading-none font-semibold tracking-tight">{v === null ? "—" : spec.format(v)}</p>
      <p className={cn("font-mono text-[11px] tabular-nums", tone)}>
        {d === null ? (latest ? periodMonthShort(latest.period_id) : "no runs yet") : `${spec.delta(v as number, pv as number)} vs ${periodMonthShort(prev?.period_id)}`}
      </p>
    </Card>
  );
}

export interface MetricTilesProps {
  metrics: MetricsResponse | null;
  loading: boolean;
  className?: string;
}

/** Four KPI tiles from the latest period, with deltas vs the previous one. */
export function MetricTiles({ metrics, loading, className }: MetricTilesProps) {
  const sorted = React.useMemo(() => [...(metrics?.periods ?? [])].sort((a, b) => a.period_id.localeCompare(b.period_id)), [metrics]);
  const latest = sorted.length ? sorted[sorted.length - 1] : null;
  const prev = sorted.length > 1 ? sorted[sorted.length - 2] : null;

  if (loading && !metrics) {
    return (
      <div className={cn("grid grid-cols-2 gap-3", className)}>
        {TILES.map((t) => (
          <Skeleton key={t.key} className="h-[108px] w-full" />
        ))}
      </div>
    );
  }

  return (
    <div className={cn("grid grid-cols-2 gap-3", className)}>
      {TILES.map((t) => (
        <Tile key={t.key} spec={t} latest={latest} prev={prev} />
      ))}
    </div>
  );
}
