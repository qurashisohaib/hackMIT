"use client";

import * as React from "react";
import { errorMessage, getHealth } from "@/lib/api";
import type { HealthResponse } from "@/lib/types";

export interface HealthState {
  /** Last successful /api/health payload. */
  health: HealthResponse | null;
  /** null while the first check is in flight. */
  online: boolean | null;
  checking: boolean;
  lastChecked: number | null;
  error: string | null;
  /** Force a re-check now. */
  refresh: () => Promise<void>;
}

const HealthContext = React.createContext<HealthState | null>(null);

const ONLINE_INTERVAL_MS = 10_000;
const OFFLINE_INTERVAL_MS = 3_000;

/**
 * Polls GET /api/health and shares the result app-wide (topbar badge,
 * offline banner, dashboard hero). Polls faster while the backend is down so
 * the UI recovers within seconds of `make backend`.
 */
export function HealthProvider({ children }: { children: React.ReactNode }) {
  const [health, setHealth] = React.useState<HealthResponse | null>(null);
  const [online, setOnline] = React.useState<boolean | null>(null);
  const [checking, setChecking] = React.useState(false);
  const [lastChecked, setLastChecked] = React.useState<number | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const inFlight = React.useRef<AbortController | null>(null);

  const refresh = React.useCallback(async () => {
    inFlight.current?.abort();
    const controller = new AbortController();
    inFlight.current = controller;
    setChecking(true);
    try {
      const h = await getHealth(controller.signal);
      if (controller.signal.aborted) return;
      setHealth(h);
      setOnline(true);
      setError(null);
    } catch (err) {
      if (controller.signal.aborted) return;
      setOnline(false);
      setError(errorMessage(err, "Backend unreachable"));
    } finally {
      if (!controller.signal.aborted) {
        setChecking(false);
        setLastChecked(Date.now());
      }
    }
  }, []);

  React.useEffect(() => {
    // Kick off the first check on the next tick so the effect body itself stays side-effect free.
    const first = window.setTimeout(() => void refresh(), 0);
    return () => {
      window.clearTimeout(first);
      inFlight.current?.abort();
    };
  }, [refresh]);

  React.useEffect(() => {
    const interval = online === false ? OFFLINE_INTERVAL_MS : ONLINE_INTERVAL_MS;
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") void refresh();
    }, interval);
    return () => window.clearInterval(id);
  }, [online, refresh]);

  const value = React.useMemo<HealthState>(
    () => ({ health, online, checking, lastChecked, error, refresh }),
    [health, online, checking, lastChecked, error, refresh],
  );

  return <HealthContext.Provider value={value}>{children}</HealthContext.Provider>;
}

/** Read backend health. Safe outside the provider (returns an "unknown" state). */
export function useHealth(): HealthState {
  const ctx = React.useContext(HealthContext);
  return (
    ctx ?? {
      health: null,
      online: null,
      checking: false,
      lastChecked: null,
      error: null,
      refresh: async () => {},
    }
  );
}

/** True when the backend reports an LLM-backed brain. */
export function isLlmBrain(health: HealthResponse | null | undefined): boolean {
  if (!health) return false;
  const b = (health.brain ?? "").toLowerCase();
  return health.llm_available || b.includes("openai") || b.includes("llm") || b.includes("agents");
}
