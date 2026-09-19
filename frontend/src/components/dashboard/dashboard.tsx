"use client";

import * as React from "react";
import { CalendarRange, TriangleAlert } from "lucide-react";
import { DashboardHero } from "@/components/dashboard/hero";
import { LearningCurve } from "@/components/dashboard/learning-curve";
import { MemoryTile } from "@/components/dashboard/memory-tile";
import { MetricTiles } from "@/components/dashboard/metric-tiles";
import { PeriodCard, PeriodCardSkeleton } from "@/components/dashboard/period-card";
import { LiveRunPanel } from "@/components/dashboard/run-progress";
import { useDashboardData } from "@/components/dashboard/use-dashboard-data";
import { useHealth } from "@/components/layout/health-provider";
import { EmptyState } from "@/components/shared/empty-state";
import { Button } from "@/components/ui/button";
import { createRun, errorMessage, getRun } from "@/lib/api";
import { useRunEvents } from "@/lib/sse";
import type { RunMetrics, RunView } from "@/lib/types";

interface ActiveRun {
  runId: string;
  periodId: string;
}

const POLL_ACTIVE_MS = 4000;

export function Dashboard() {
  const { online } = useHealth();

  // Run the user started in this session.
  const [startedRun, setStartedRun] = React.useState<ActiveRun | null>(null);
  const [starting, setStarting] = React.useState<string | null>(null);
  const [runError, setRunError] = React.useState<string | null>(null);
  const [dismissedRunId, setDismissedRunId] = React.useState<string | null>(null);
  const [completedView, setCompletedView] = React.useState<RunView | null>(null);

  // Cheap first pass: we need `runs` to adopt an in-flight run after a reload,
  // so the poll interval is decided after the data hook below returns.
  const [pollMs, setPollMs] = React.useState<number | null>(null);
  const data = useDashboardData({ pollMs, refreshKey: online });

  // Adopt a run already in progress on the backend (e.g. page reload mid-close).
  const adopted = React.useMemo<ActiveRun | null>(() => {
    const r = data.runs?.find((x) => x.status === "running" || x.status === "queued");
    return r ? { runId: r.run_id, periodId: r.period_id } : null;
  }, [data.runs]);

  const activeRun = startedRun ?? adopted;
  const visibleRun = activeRun && activeRun.runId !== dismissedRunId ? activeRun : null;
  const live = useRunEvents(visibleRun?.runId ?? null);
  const ended = live.runStatus === "completed" || live.runStatus === "failed";
  const running = !!visibleRun && !ended;

  // Poll every 4s while a run is active.
  React.useEffect(() => {
    const next = running ? POLL_ACTIVE_MS : null;
    const id = window.setTimeout(() => setPollMs(next), 0);
    return () => window.clearTimeout(id);
  }, [running]);

  // On completion: fetch the final RunView (metrics) and refresh the dashboard data.
  const refresh = data.refresh;
  React.useEffect(() => {
    if (!visibleRun || !ended) return;
    const ctrl = new AbortController();
    const runId = visibleRun.runId;
    getRun(runId, ctrl.signal)
      .then((view) => {
        if (!ctrl.signal.aborted) setCompletedView(view);
      })
      .catch(() => {});
    const t = window.setTimeout(refresh, 300);
    return () => {
      ctrl.abort();
      window.clearTimeout(t);
    };
  }, [visibleRun, ended, refresh]);

  const periods = React.useMemo(() => [...(data.periods ?? [])].sort((a, b) => a.id.localeCompare(b.id)), [data.periods]);
  const metricsResponse = data.metrics;
  const metricsByPeriod = React.useMemo(() => {
    const map = new Map<string, RunMetrics>();
    for (const m of metricsResponse?.periods ?? []) map.set(m.period_id, m);
    return map;
  }, [metricsResponse]);
  const suggestedId = periods.find((p) => p.status !== "closed")?.id ?? null;

  const startRun = async (periodId: string) => {
    setRunError(null);
    setStarting(periodId);
    try {
      const { run_id } = await createRun(periodId);
      setCompletedView(null);
      setDismissedRunId(null);
      setStartedRun({ runId: run_id, periodId });
    } catch (err) {
      setRunError(errorMessage(err, "Could not start the run"));
    } finally {
      setStarting(null);
    }
  };

  const onReset = () => {
    setStartedRun(null);
    setCompletedView(null);
    setDismissedRunId(null);
    setRunError(null);
    refresh();
  };

  const activePeriod = visibleRun ? periods.find((p) => p.id === visibleRun.periodId) ?? null : null;

  return (
    <div className="flex flex-col gap-6">
      <DashboardHero periods={data.periods} metrics={data.metrics} memoryStats={data.memoryStats} loading={data.loading} onReset={onReset} />

      {runError ? (
        <div role="alert" className="flex items-center gap-3 rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-2.5 text-sm text-rose-100 animate-fade-in">
          <TriangleAlert className="size-4 shrink-0 text-rose-300" aria-hidden />
          <span className="min-w-0 flex-1">{runError}</span>
          <Button size="xs" variant="ghost" className="text-rose-100 hover:bg-rose-500/20" onClick={() => setRunError(null)}>
            Dismiss
          </Button>
        </div>
      ) : null}

      <section aria-labelledby="periods-title" className="space-y-3">
        <div className="flex items-end justify-between gap-3">
          <div>
            <h2 id="periods-title" className="text-sm font-semibold tracking-tight">
              Period close
            </h2>
            <p className="text-xs text-muted">Run each month in order. Teach a rule after January and watch February use it.</p>
          </div>
          {data.lastUpdated ? (
            <span className="hidden font-mono text-[10px] text-subtle sm:inline">
              {running ? "polling every 4s" : `updated ${new Date(data.lastUpdated).toLocaleTimeString()}`}
            </span>
          ) : null}
        </div>

        {data.loading && !data.periods ? (
          <div className="grid gap-4 md:grid-cols-3">
            <PeriodCardSkeleton />
            <PeriodCardSkeleton />
            <PeriodCardSkeleton />
          </div>
        ) : periods.length === 0 ? (
          <EmptyState
            icon={CalendarRange}
            title={online === false ? "Backend offline" : "No periods found"}
            description={
              online === false
                ? "Start the API with `make backend` — the dashboard reconnects automatically."
                : "Seed the synthetic company with `make seed`, or press Reset demo to reseed from the API."
            }
          />
        ) : (
          <div className="grid gap-4 md:grid-cols-3">
            {periods.map((p) => (
              <PeriodCard
                key={p.id}
                period={p}
                suggested={p.id === suggestedId}
                disabled={online === false || running || starting !== null}
                starting={starting === p.id}
                live={visibleRun?.periodId === p.id ? live : null}
                metrics={metricsByPeriod.get(p.id) ?? null}
                onRun={(id) => void startRun(id)}
              />
            ))}
          </div>
        )}
      </section>

      {visibleRun ? (
        <LiveRunPanel
          runId={visibleRun.runId}
          periodId={visibleRun.periodId}
          period={activePeriod}
          live={live}
          runView={completedView?.run_id === visibleRun.runId ? completedView : null}
          onDismiss={ended ? () => setDismissedRunId(visibleRun.runId) : undefined}
        />
      ) : null}

      <section aria-label="Learning metrics" className="grid gap-4 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <LearningCurve metrics={data.metrics} loading={data.loading} />
        <div className="flex flex-col gap-4">
          <MetricTiles metrics={data.metrics} loading={data.loading} />
          <MemoryTile stats={data.memoryStats} loading={data.loading} className="flex-1" />
        </div>
      </section>
    </div>
  );
}
