"use client";

import { useCallback, useEffect, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useHealth } from "@/components/layout/health-provider";
import { ApiError, errorMessage, getPeriods } from "@/lib/api";
import type { PeriodView } from "@/lib/types";

export function usePeriodSelection() {
  const params = useSearchParams();
  const pathname = usePathname();
  const router = useRouter();
  const { online } = useHealth();
  const [revision, setRevision] = useState(0);
  const [state, setState] = useState<{ periods: PeriodView[] | null; error: string | null }>({ periods: null, error: null });
  const requestedId = params.get("period_id") ?? params.get("period");

  useEffect(() => {
    const controller = new AbortController();
    void getPeriods(controller.signal).then((periods) => {
      if (!controller.signal.aborted) setState({ periods: [...periods].sort((a, b) => a.start_date.localeCompare(b.start_date)), error: null });
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setState((previous) => ({ ...previous, error: errorMessage(error) }));
    });
    return () => controller.abort();
  }, [online, revision]);

  const periods = state.periods ?? [];
  const defaultPeriod = [...periods].reverse().find((period) => period.last_run_id) ?? periods[0];
  const periodId = requestedId || defaultPeriod?.id || null;
  const period = periods.find((item) => item.id === periodId) ?? null;
  const selectPeriod = (id: string) => {
    const query = new URLSearchParams(params.toString());
    query.set("period_id", id);
    query.delete("period");
    router.replace(`${pathname}?${query}`, { scroll: false });
  };
  const refresh = useCallback(() => setRevision((value) => value + 1), []);
  return { periods, period, periodId, selectPeriod, refresh, revision, error: state.error, loading: !state.periods && !state.error };
}

interface ResourceState<T> {
  periodId: string;
  data: T | null;
  error: string | null;
  missing: boolean;
}

export function usePeriodResource<T>(periodId: string | null, load: (id: string, signal?: AbortSignal) => Promise<T>, revision: number) {
  const { online } = useHealth();
  const [state, setState] = useState<ResourceState<T> | null>(null);

  useEffect(() => {
    if (!periodId) return;
    const controller = new AbortController();
    let pending = false;
    const fetchData = async () => {
      if (pending) return;
      pending = true;
      try {
        const data = await load(periodId, controller.signal);
        if (!controller.signal.aborted) setState({ periodId, data, error: null, missing: false });
      } catch (error: unknown) {
        if (controller.signal.aborted) return;
        const missing = error instanceof ApiError && error.status === 404;
        setState((previous) => ({
          periodId,
          data: !missing && previous?.periodId === periodId ? previous.data : null,
          error: missing ? null : errorMessage(error),
          missing,
        }));
      } finally {
        pending = false;
      }
    };
    void fetchData();
    const timer = window.setInterval(() => { if (document.visibilityState === "visible") void fetchData(); }, 12000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [periodId, load, revision, online]);

  const current = state?.periodId === periodId ? state : null;
  return {
    data: current?.data ?? null,
    error: current?.error ?? null,
    missing: current?.missing ?? false,
    loading: !!periodId && !current,
  };
}
