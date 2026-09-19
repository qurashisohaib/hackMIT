"use client";

import * as React from "react";
import Link from "next/link";
import { ArrowRight, Play, RotateCw, Sparkles } from "lucide-react";
import { LiveCounters, PipelineSteps } from "@/components/dashboard/run-progress";
import { AgentChip } from "@/components/shared/agent-chip";
import { LiveDot } from "@/components/shared/live-dot";
import { StatusPill } from "@/components/shared/status-pill";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { UseEventsResult } from "@/lib/sse";
import type { PeriodStats, PeriodView, RunMetrics } from "@/lib/types";
import { cn, formatDateShort, formatNumber, formatPercent, periodMonth } from "@/lib/utils";

/** Read a stat under any of several candidate keys (backend key names may vary). */
export function statValue(stats: PeriodStats | undefined, keys: string[]): number | null {
  if (!stats) return null;
  for (const k of keys) {
    const v = stats[k];
    if (typeof v === "number") return v;
  }
  return null;
}

const STAT_KEYS = {
  bank: ["bank_transactions", "bank_txns", "bank_txn_count", "transactions", "bank"],
  ap: ["ap_invoices", "ap_invoice_count", "ap"],
  ar: ["ar_invoices", "ar_invoice_count", "ar"],
  unreconciled: ["unreconciled", "unreconciled_txns", "unreconciled_bank_txns", "open_bank_txns", "unmatched"],
  exceptions: ["open_exceptions", "exceptions_open", "exceptions"],
};

function Stat({ label, value, tone }: { label: string; value: number | null; tone?: string }) {
  return (
    <div className="min-w-0">
      <p className="truncate text-[11px] text-muted">{label}</p>
      <p className={cn("font-mono text-base leading-tight tabular-nums", tone ?? "text-foreground")}>{value === null ? "—" : formatNumber(value)}</p>
    </div>
  );
}

export interface PeriodCardProps {
  period: PeriodView;
  /** Highlight as the suggested next period to run. */
  suggested?: boolean;
  /** Backend offline or another run in flight. */
  disabled?: boolean;
  /** POST /api/runs in flight for this period. */
  starting?: boolean;
  /** Live SSE state when this period owns the active run. */
  live?: UseEventsResult | null;
  /** Latest metrics for this period (from /api/metrics). */
  metrics?: RunMetrics | null;
  onRun: (periodId: string) => void;
}

/** One period: status, stats, and the Run button that flips into a live progress block. */
export function PeriodCard({ period, suggested = false, disabled = false, starting = false, live, metrics, onRun }: PeriodCardProps) {
  const month = periodMonth(period);
  const stats = period.stats;
  const unreconciled = statValue(stats, STAT_KEYS.unreconciled);
  const isRunning = !!live && live.runStatus !== "completed" && live.runStatus !== "failed";
  const ended = !!live && (live.runStatus === "completed" || live.runStatus === "failed");
  const closed = period.status === "closed";
  const finalMetrics = live?.metrics ?? metrics ?? null;

  return (
    <Card
      data-period={period.id}
      className={cn(
        "relative flex flex-col gap-4 p-5 transition-[border-color,box-shadow] duration-300",
        suggested && !live && "border-emerald-400/40 shadow-[0_0_0_1px_rgba(52,211,153,0.15),0_20px_50px_-30px_rgba(16,185,129,0.6)]",
        isRunning && "border-sky-400/40",
        ended && live?.runStatus === "completed" && "border-emerald-400/40",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="text-lg font-semibold tracking-tight">{month}</h3>
            {suggested && !live && !closed ? (
              <span className="inline-flex items-center gap-1 font-mono text-[10px] tracking-wide text-emerald-300 uppercase">
                <Sparkles className="size-3" aria-hidden /> next
              </span>
            ) : null}
          </div>
          <p className="font-mono text-[11px] text-muted">
            {formatDateShort(period.start_date)} – {formatDateShort(period.end_date)} · <span className="text-subtle">{period.id}</span>
          </p>
        </div>
        <StatusPill status={isRunning ? "running" : period.status} kind="period" size="sm" />
      </div>

      <div className="grid grid-cols-4 gap-3 rounded-lg border border-border bg-surface-2/50 px-3.5 py-3">
        <Stat label="Bank txns" value={statValue(stats, STAT_KEYS.bank)} />
        <Stat label="AP" value={statValue(stats, STAT_KEYS.ap)} />
        <Stat label="AR" value={statValue(stats, STAT_KEYS.ar)} />
        <Stat
          label="Unreconciled"
          value={unreconciled}
          tone={unreconciled === null ? undefined : unreconciled > 0 ? "text-amber-200" : "text-emerald-200"}
        />
      </div>

      {live ? (
        <div className="space-y-3 rounded-lg border border-border bg-surface-2/40 p-3.5 animate-fade-in">
          <div className="flex items-center gap-2 text-xs">
            <LiveDot color={ended ? (live.runStatus === "failed" ? "rose" : "emerald") : "sky"} pulse={!ended} size="xs" />
            <span className="font-medium">
              {ended ? (live.runStatus === "failed" ? "Failed" : "Complete") : live.runStatus ? "Running" : "Connecting"}
            </span>
            {!ended && live.currentAgent ? <AgentChip agent={live.currentAgent} size="xs" /> : null}
            <span className="min-w-0 flex-1 truncate text-muted">{!ended ? live.currentStep : null}</span>
          </div>
          <PipelineSteps live={live} compact />
          {ended && live.runStatus === "completed" ? (
            <div className="flex items-center justify-between gap-3 text-xs">
              <span className="text-muted">
                Accuracy <span className="font-mono text-emerald-200">{formatPercent(finalMetrics?.accuracy)}</span> · Human{" "}
                <span className="font-mono text-rose-200">{finalMetrics ? formatNumber(finalMetrics.human_reviews) : "—"}</span>
              </span>
              <Link href={`/exceptions?period_id=${encodeURIComponent(period.id)}`} className="inline-flex items-center gap-1 font-medium text-emerald-300 hover:underline">
                Exceptions <ArrowRight className="size-3.5" aria-hidden />
              </Link>
            </div>
          ) : (
            <LiveCounters live={live} compact />
          )}
        </div>
      ) : (
        <div className="mt-auto flex items-center gap-2">
          <Button
            className="flex-1"
            variant={closed ? "secondary" : "default"}
            onClick={() => onRun(period.id)}
            disabled={disabled}
            loading={starting}
            title={disabled ? "Backend offline or a run is already in progress" : `Run the ${month} close`}
          >
            {starting ? null : closed ? <RotateCw aria-hidden /> : <Play aria-hidden />}
            {starting ? "Starting…" : closed ? `Re-run ${month} close` : `Run ${month} Close`}
          </Button>
          {period.last_run_id ? (
            <Link
              href={`/trace?run_id=${encodeURIComponent(period.last_run_id)}`}
              className={cn(buttonVariants({ variant: "ghost", size: "sm" }), "shrink-0")}
              title="Open the last run in the trace viewer"
            >
              Trace
            </Link>
          ) : null}
        </div>
      )}

      {!live && metrics ? (
        <p className="font-mono text-[10px] text-subtle">
          last run · accuracy {formatPercent(metrics.accuracy)} · {formatNumber(metrics.human_reviews)} human · {formatNumber(metrics.precedent_hits)} precedent
        </p>
      ) : null}
    </Card>
  );
}

export function PeriodCardSkeleton() {
  return (
    <Card className="flex flex-col gap-4 p-5">
      <div className="flex items-start justify-between">
        <div className="space-y-2">
          <Skeleton className="h-6 w-28" />
          <Skeleton className="h-3 w-40" />
        </div>
        <Skeleton className="h-5 w-16" />
      </div>
      <Skeleton className="h-14 w-full" />
      <Skeleton className="h-9 w-full" />
    </Card>
  );
}
