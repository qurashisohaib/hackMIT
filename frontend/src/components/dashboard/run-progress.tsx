"use client";

import * as React from "react";
import Link from "next/link";
import {
  ArrowRight,
  Brain,
  Check,
  CircleCheck,
  Link2,
  ShieldCheck,
  Timer,
  TriangleAlert,
  User,
  X,
  type LucideIcon,
} from "lucide-react";
import { AgentChip } from "@/components/shared/agent-chip";
import { LiveDot } from "@/components/shared/live-dot";
import { StatusPill } from "@/components/shared/status-pill";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { AGENTS, PIPELINE_STEPS } from "@/lib/agents";
import type { RunLiveState, StreamStatus, UseEventsResult } from "@/lib/sse";
import type { AgentEvent, PeriodView, RunMetrics, RunView } from "@/lib/types";
import { cn, formatDecimal, formatMs, formatNumber, formatPercent, formatTime, periodMonth, shortId } from "@/lib/utils";

// ---------------------------------------------------------------- animated numbers

/** Tween an integer toward `target` (ease-out cubic) for satisfying live counters. */
export function useAnimatedNumber(target: number, duration = 500): number {
  const [display, setDisplay] = React.useState(target);
  const displayRef = React.useRef(target);

  React.useEffect(() => {
    const from = displayRef.current;
    if (from === target) return;
    if (typeof requestAnimationFrame !== "function") return;
    const start = performance.now();
    let raf = 0;
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      const v = Math.round(from + (target - from) * eased);
      displayRef.current = v;
      setDisplay(v);
      if (t < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, duration]);

  return display;
}

// ---------------------------------------------------------------- pipeline steps

export type StepState = "pending" | "active" | "done" | "failed";

/** Map live run state onto the 7 pipeline steps. */
export function deriveStepStates(live: Pick<RunLiveState, "runStatus" | "currentAgent" | "finishedAgents">): StepState[] {
  if (live.runStatus === "completed") return PIPELINE_STEPS.map(() => "done");
  const finished = new Set(live.finishedAgents);
  let idx = live.currentAgent ? PIPELINE_STEPS.findIndex((s) => s.agent === live.currentAgent) : -1;
  // The CFO both opens (plan) and closes (metrics) the run: once others have finished, it is wrapping up.
  if (live.currentAgent === "cfo" && finished.size > 0) idx = PIPELINE_STEPS.length;
  return PIPELINE_STEPS.map((s, i) => {
    if (live.runStatus === "failed") {
      if (i === idx) return "failed";
      return i < idx || finished.has(s.agent) ? "done" : "pending";
    }
    if (i < idx) return "done";
    if (i === idx) return "active";
    return finished.has(s.agent) ? "done" : "pending";
  });
}

export interface PipelineStepsProps {
  live: Pick<RunLiveState, "runStatus" | "currentAgent" | "finishedAgents">;
  /** Dots only (for cards). */
  compact?: boolean;
  className?: string;
}

/** CFO plan → AP/AR → Recon → Audit → Close → Forecast → Report */
export function PipelineSteps({ live, compact = false, className }: PipelineStepsProps) {
  const states = deriveStepStates(live);
  return (
    <ol
      aria-label="Pipeline progress"
      className={cn("flex items-center", compact ? "gap-1" : "gap-1.5 sm:gap-2", className)}
    >
      {PIPELINE_STEPS.map((step, i) => {
        const state = states[i];
        const meta = AGENTS[step.agent];
        const Icon = meta.icon;
        const tone =
          state === "done"
            ? cn("border-transparent", meta.dot, "text-black/80")
            : state === "active"
              ? cn("bg-surface-2", meta.border, meta.text, "shadow-[0_0_0_3px_color-mix(in_oklab,currentColor_25%,transparent)]")
              : state === "failed"
                ? "border-rose-400/60 bg-rose-500/15 text-rose-200"
                : "border-border-strong bg-surface-2 text-subtle";
        if (compact) {
          return (
            <li key={step.key} className="flex flex-1 items-center" aria-label={`${step.label}: ${state}`} title={`${step.label} · ${state}`}>
              <span
                className={cn(
                  "h-1.5 w-full rounded-full transition-colors",
                  state === "done" ? meta.dot : state === "active" ? cn(meta.dot, "animate-pulse") : state === "failed" ? "bg-rose-400" : "bg-border-strong",
                )}
              />
            </li>
          );
        }
        return (
          <li key={step.key} className="flex min-w-0 flex-1 items-center gap-1.5 sm:gap-2" aria-current={state === "active" ? "step" : undefined}>
            <div className="flex min-w-0 flex-col items-center gap-1.5">
              <span
                className={cn("inline-flex size-7 items-center justify-center rounded-full border transition-all duration-300", tone)}
                title={`${step.label} · ${state}`}
              >
                {state === "done" ? <Check className="size-3.5" aria-hidden /> : state === "failed" ? <X className="size-3.5" aria-hidden /> : <Icon className="size-3.5" aria-hidden />}
              </span>
              <span
                className={cn(
                  "max-w-full truncate font-mono text-[10px] tracking-wide uppercase",
                  state === "active" ? meta.text : state === "done" ? "text-foreground/80" : "text-subtle",
                )}
              >
                {step.label}
              </span>
            </div>
            {i < PIPELINE_STEPS.length - 1 ? (
              <span className={cn("mb-5 h-px flex-1 rounded-full", state === "done" ? "bg-border-strong" : "bg-border")} aria-hidden />
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}

// ---------------------------------------------------------------- counters

interface CounterSpec {
  key: "reconciled" | "matched" | "exceptions" | "human" | "precedent_hits";
  label: string;
  icon: LucideIcon;
  tone: string;
  iconTone: string;
}

const COUNTERS: CounterSpec[] = [
  { key: "reconciled", label: "Reconciled", icon: CircleCheck, tone: "border-emerald-400/25", iconTone: "text-emerald-300 bg-emerald-500/12" },
  { key: "matched", label: "Matched", icon: Link2, tone: "border-sky-400/25", iconTone: "text-sky-300 bg-sky-500/12" },
  { key: "exceptions", label: "Exceptions", icon: TriangleAlert, tone: "border-amber-400/25", iconTone: "text-amber-300 bg-amber-500/12" },
  { key: "human", label: "Human review", icon: User, tone: "border-rose-400/25", iconTone: "text-rose-300 bg-rose-500/12" },
  { key: "precedent_hits", label: "Precedent hits", icon: Brain, tone: "border-violet-400/25", iconTone: "text-violet-300 bg-violet-500/12" },
];

/** Resolve a counter from run.progress counts with event-derived fallbacks. */
export function counterValue(live: Pick<RunLiveState, "counts" | "exceptionsRaised" | "precedentHits">, key: CounterSpec["key"]): number {
  const v = live.counts[key];
  if (typeof v === "number") return v;
  if (key === "exceptions") return live.exceptionsRaised;
  if (key === "precedent_hits") return live.precedentHits;
  return 0;
}

function Counter({ spec, value, compact }: { spec: CounterSpec; value: number; compact?: boolean }) {
  const shown = useAnimatedNumber(value);
  const Icon = spec.icon;
  if (compact) {
    return (
      <div className="flex items-center gap-1.5" title={spec.label}>
        <span className={cn("inline-flex size-5 items-center justify-center rounded", spec.iconTone)}>
          <Icon className="size-3" aria-hidden />
        </span>
        <span className="font-mono text-sm tabular-nums">{formatNumber(shown)}</span>
        <span className="sr-only">{spec.label}</span>
      </div>
    );
  }
  return (
    <div className={cn("flex items-center gap-3 rounded-lg border bg-surface-2/60 px-3.5 py-3", spec.tone)}>
      <span className={cn("inline-flex size-8 shrink-0 items-center justify-center rounded-md", spec.iconTone)}>
        <Icon className="size-4" aria-hidden />
      </span>
      <div className="min-w-0">
        <p className="text-2xl leading-none font-semibold tracking-tight text-foreground">{formatNumber(shown)}</p>
        <p className="mt-1 truncate text-[11px] text-muted">{spec.label}</p>
      </div>
    </div>
  );
}

export interface LiveCountersProps {
  live: Pick<RunLiveState, "counts" | "exceptionsRaised" | "precedentHits">;
  compact?: boolean;
  className?: string;
}

/** reconciled ✓ · matched ✓ · exceptions ⚠ · human 👤 · precedent hits 🧠 */
export function LiveCounters({ live, compact = false, className }: LiveCountersProps) {
  return (
    <div className={cn(compact ? "flex flex-wrap items-center gap-x-4 gap-y-1.5" : "grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-5", className)}>
      {COUNTERS.map((c) => (
        <Counter key={c.key} spec={c} value={counterValue(live, c.key)} compact={compact} />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- ticker

const KIND_TONE: Record<string, string> = {
  "exception.raised": "text-amber-300",
  "human.review_requested": "text-rose-300",
  "memory.precedent_hit": "text-violet-300",
  "memory.write": "text-violet-300",
  "audit.challenge": "text-amber-300",
  "audit.verdict": "text-amber-300",
  "decision.executed": "text-emerald-300",
  "exception.resolved": "text-emerald-300",
  "run.completed": "text-emerald-300",
  "run.failed": "text-rose-300",
  "brain.fallback": "text-rose-300",
  propagation: "text-violet-300",
};

export interface AgentTickerProps {
  events: AgentEvent[];
  /** Number of rows. Default 8. */
  limit?: number;
  status?: StreamStatus;
  className?: string;
}

/** Last N agent events, newest first, with agent chips. */
export function AgentTicker({ events, limit = 8, status, className }: AgentTickerProps) {
  const rows = React.useMemo(() => {
    const out: AgentEvent[] = [];
    for (let i = events.length - 1; i >= 0 && out.length < limit; i--) {
      const ev = events[i];
      if (ev.kind === "run.progress") continue;
      out.push(ev);
    }
    return out;
  }, [events, limit]);

  return (
    <div className={cn("flex min-h-0 flex-col", className)}>
      <div className="mb-2 flex items-center justify-between">
        <p className="font-mono text-[10px] tracking-[0.16em] text-muted uppercase">Agent activity</p>
        <span className="inline-flex items-center gap-1.5 font-mono text-[10px] text-subtle">
          <LiveDot color={status === "open" ? "emerald" : status === "reconnecting" || status === "connecting" ? "amber" : "zinc"} pulse={status === "open"} size="xs" />
          {status === "open" ? "live" : status === "closed" ? "ended" : (status ?? "idle")}
        </span>
      </div>
      <ol className="flex flex-col gap-1" aria-live="polite" aria-relevant="additions">
        {rows.length === 0 ? (
          <li className="flex items-center gap-2 rounded-md border border-dashed border-border-strong px-3 py-4 text-xs text-muted">
            <LiveDot color="amber" pulse size="xs" /> Waiting for the first agent event…
          </li>
        ) : (
          rows.map((ev) => (
            <li
              key={ev.id}
              className="flex items-center gap-2 rounded-md border border-border bg-surface-2/50 px-2.5 py-1.5 text-xs animate-fade-up"
            >
              <span className="hidden w-[62px] shrink-0 font-mono text-[10px] text-subtle tabular-nums sm:inline">{formatTime(ev.ts)}</span>
              <AgentChip agent={ev.agent} size="xs" />
              <span className={cn("hidden shrink-0 font-mono text-[10px] md:inline", KIND_TONE[ev.kind] ?? "text-subtle")}>{ev.kind}</span>
              <span className="min-w-0 flex-1 truncate text-foreground/90" title={ev.detail || ev.title}>
                {ev.title || ev.detail || ev.kind}
              </span>
            </li>
          ))
        )}
      </ol>
    </div>
  );
}

// ---------------------------------------------------------------- summary

function SummaryStat({ label, value, sub, tone }: { label: string; value: React.ReactNode; sub?: React.ReactNode; tone?: string }) {
  return (
    <div className="rounded-lg border border-border bg-surface-2/60 px-3.5 py-3">
      <p className="text-[11px] text-muted">{label}</p>
      <p className={cn("mt-1 text-xl leading-none font-semibold tracking-tight", tone ?? "text-foreground")}>{value}</p>
      {sub ? <p className="mt-1.5 font-mono text-[10px] text-subtle">{sub}</p> : null}
    </div>
  );
}

export interface RunSummaryProps {
  metrics: RunMetrics | null;
  periodId: string;
  runId: string;
  failed?: boolean;
  error?: string | null;
}

/** Metrics summary after run.completed, with links onward. */
export function RunSummary({ metrics, periodId, runId, failed = false, error }: RunSummaryProps) {
  const auditRate = metrics && metrics.audit_challenges > 0 ? metrics.audit_passed / metrics.audit_challenges : null;
  return (
    <div className="space-y-4">
      {failed ? (
        <p role="alert" className="rounded-md border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-200">
          Run failed{error ? `: ${error}` : "."} Check the backend log or open the trace.
        </p>
      ) : null}
      {metrics ? (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6">
          <SummaryStat label="Accuracy vs truth" value={formatPercent(metrics.accuracy)} tone="text-emerald-200" sub={`coverage ${formatPercent(metrics.coverage)}`} />
          <SummaryStat label="Auto-resolved" value={formatNumber(metrics.auto_resolved)} sub={`of ${formatNumber(metrics.total_items)} items`} />
          <SummaryStat label="Precedent hits" value={formatNumber(metrics.precedent_hits)} tone="text-violet-200" sub={`${formatNumber(metrics.rules_used)} rules used`} />
          <SummaryStat label="Human reviews" value={formatNumber(metrics.human_reviews)} tone={metrics.human_reviews > 0 ? "text-rose-200" : "text-foreground"} sub={`${formatNumber(metrics.exceptions_raised)} exceptions`} />
          <SummaryStat label="Audit pass rate" value={formatPercent(auditRate)} tone="text-amber-200" sub={`${metrics.audit_passed}/${metrics.audit_challenges} challenges`} />
          <SummaryStat label="Wall time" value={formatMs(metrics.wall_ms)} sub={`${formatDecimal(metrics.avg_steps_per_exception)} steps/exc`} />
        </div>
      ) : (
        <p className="text-xs text-muted">{failed ? "No metrics were produced." : "Fetching final metrics…"}</p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Link href={`/exceptions?period_id=${encodeURIComponent(periodId)}`} className={cn(buttonVariants({ size: "sm" }))}>
          Review exceptions <ArrowRight aria-hidden />
        </Link>
        <Link href={`/trace?run_id=${encodeURIComponent(runId)}`} className={cn(buttonVariants({ variant: "secondary", size: "sm" }))}>
          <Timer aria-hidden /> Open trace
        </Link>
        <Link href={`/reports?period_id=${encodeURIComponent(periodId)}`} className={cn(buttonVariants({ variant: "ghost", size: "sm" }))}>
          <ShieldCheck aria-hidden /> Close report
        </Link>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- panel

export interface LiveRunPanelProps {
  runId: string;
  periodId: string;
  period?: PeriodView | null;
  live: UseEventsResult;
  /** RunView fetched after completion (for metrics when the SSE payload lacks them). */
  runView?: RunView | null;
  onDismiss?: () => void;
  className?: string;
}

/** Full-width live run panel: pipeline steps, animated counters, ticker, completion summary. */
export function LiveRunPanel({ runId, periodId, period, live, runView, onDismiss, className }: LiveRunPanelProps) {
  const ended = live.runStatus === "completed" || live.runStatus === "failed";
  const runStatus = live.runStatus ?? (live.status === "connecting" || live.status === "reconnecting" ? "queued" : "running");
  const month = periodMonth(period ?? periodId);
  const currentMeta = live.currentAgent ? AGENTS[live.currentAgent as keyof typeof AGENTS] : null;
  const metrics = runView?.metrics ?? live.metrics;

  return (
    <Card className={cn("overflow-hidden animate-fade-up", className)} aria-live="polite">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-border px-5 py-3">
        <LiveDot color={ended ? (live.runStatus === "failed" ? "rose" : "emerald") : "sky"} pulse={!ended} />
        <h2 className="text-sm font-semibold tracking-tight">
          {ended ? (live.runStatus === "failed" ? "Run failed" : "Close complete") : "Closing"} · {month}
        </h2>
        <StatusPill status={runStatus} kind="run" size="sm" />
        <span className="font-mono text-[11px] text-subtle" title={runId}>
          {shortId(runId, 14)}
        </span>
        {!ended && live.currentStep ? (
          <span className="hidden min-w-0 items-center gap-2 truncate text-xs text-muted sm:inline-flex">
            <span aria-hidden>·</span>
            {currentMeta ? <AgentChip agent={currentMeta.id} size="xs" /> : null}
            <span className="truncate">{live.currentStep}</span>
          </span>
        ) : null}
        <div className="ml-auto flex items-center gap-2">
          {onDismiss ? (
            <Button variant="ghost" size="icon-xs" onClick={onDismiss} aria-label="Dismiss run panel">
              <X />
            </Button>
          ) : null}
        </div>
      </div>

      <div className="grid gap-6 p-5 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="min-w-0 space-y-5">
          <PipelineSteps live={live} />
          {ended ? (
            <RunSummary metrics={metrics} periodId={periodId} runId={runId} failed={live.runStatus === "failed"} error={live.error ?? runView?.error ?? null} />
          ) : (
            <LiveCounters live={live} />
          )}
        </div>
        <AgentTicker events={live.events} status={live.status} className="lg:border-l lg:border-border lg:pl-6" />
      </div>
    </Card>
  );
}
