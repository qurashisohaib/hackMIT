"use client";

import { useCallback, useEffect, useState } from "react";
import { errorMessage, getMemoryStats, getMetrics, getPeriods, getRuns } from "@/lib/api";
import type { MemoryStats, MetricsResponse, PeriodView, RunView } from "@/lib/types";

export interface DashboardData {
  periods: PeriodView[] | null;
  metrics: MetricsResponse | null;
  memoryStats: MemoryStats | null;
  runs: RunView[] | null;
  /** True until the first fetch settles. Later refetches keep the previous data (no skeleton flash). */
  loading: boolean;
  /** Error from the periods fetch (the load-bearing one); null when healthy. */
  error: string | null;
  lastUpdated: number | null;
  /** Trigger a refetch now. Safe to call from event handlers and effects. */
  refresh: () => void;
}

export interface UseDashboardDataOptions {
  /** Poll interval in ms, or null to fetch only on mount / refresh(). */
  pollMs: number | null;
  /** Any value; when it changes a refetch happens (e.g. backend online flag). */
  refreshKey?: unknown;
}

/**
 * Fetches periods, metrics, memory stats and runs together. Each endpoint fails
 * independently — a 500 on /metrics never blanks the period cards.
 */
export function useDashboardData({ pollMs, refreshKey }: UseDashboardDataOptions): DashboardData {
  const [tick, setTick] = useState(0);
  const [state, setState] = useState<Omit<DashboardData, "refresh">>({
    periods: null,
    metrics: null,
    memoryStats: null,
    runs: null,
    loading: true,
    error: null,
    lastUpdated: null,
  });

  const refresh = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    const ctrl = new AbortController();
    void Promise.allSettled([
      getPeriods(ctrl.signal),
      getMetrics(ctrl.signal),
      getMemoryStats(ctrl.signal),
      getRuns(ctrl.signal),
    ]).then(([p, m, s, r]) => {
      if (ctrl.signal.aborted) return;
      setState((prev) => ({
        periods: p.status === "fulfilled" ? p.value : prev.periods,
        metrics: m.status === "fulfilled" ? m.value : prev.metrics,
        memoryStats: s.status === "fulfilled" ? s.value : prev.memoryStats,
        runs: r.status === "fulfilled" ? r.value : prev.runs,
        loading: false,
        error: p.status === "rejected" ? errorMessage(p.reason) : null,
        lastUpdated: Date.now(),
      }));
    });
    return () => ctrl.abort();
  }, [tick, refreshKey]);

  useEffect(() => {
    if (!pollMs) return;
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") setTick((t) => t + 1);
    }, pollMs);
    return () => window.clearInterval(id);
  }, [pollMs]);

  return { ...state, refresh };
}
