"use client";

/**
 * Server-Sent Events helpers for the agent event bus (ARCHITECTURE §6).
 *
 * - `useRunEvents(runId)`  — replay + live events for one run, with derived live
 *   state (counts, run status, current agent, metrics on completion).
 * - `useGlobalEvents()`    — every event from every run (`/api/events/stream`).
 * - `subscribeRun(runId, cb)` — non-hook subscription; returns an unsubscribe fn.
 *
 * All three share `openEventStream`, which:
 *   • listens on the default `message` channel AND on every named event kind
 *     (works whether the backend sets `event:` or not),
 *   • de-duplicates by `seq` so replays after a reconnect are harmless,
 *   • reconnects with exponential backoff when the stream drops.
 *
 * Events are buffered to at most `MAX_BUFFERED_EVENTS` (oldest dropped) and
 * flushed to React once per animation frame so a 500-event replay does not
 * cause 500 renders.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { globalEventsUrl, runEventsUrl } from "./api";
import type { AgentEvent, AgentId, RunCounts, RunMetrics, RunStatus } from "./types";

export const MAX_BUFFERED_EVENTS = 2000;

export type StreamStatus = "idle" | "connecting" | "open" | "reconnecting" | "closed" | "error";

/** Every event kind the backend emits (§6). Used to register named listeners. */
export const EVENT_KINDS = [
  "run.started",
  "run.plan",
  "run.progress",
  "run.completed",
  "run.failed",
  "agent.started",
  "agent.finished",
  "agent.step",
  "tool.call",
  "tool.result",
  "hypothesis.generated",
  "hypothesis.tested",
  "decision.made",
  "decision.executed",
  "exception.raised",
  "exception.resolved",
  "memory.read",
  "memory.write",
  "memory.precedent_hit",
  "handoff",
  "audit.challenge",
  "audit.response",
  "audit.verdict",
  "human.review_requested",
  "human.correction",
  "propagation",
  "forecast.updated",
  "report.ready",
  "brain.fallback",
  "close.checklist",
  "event",
] as const;

// ---------------------------------------------------------------- parsing

/**
 * Parse one SSE `data:` payload into an AgentEvent. Accepts a bare AgentEvent,
 * `{event: AgentEvent}` or `{data: AgentEvent}` wrappers. Returns null for
 * heartbeats / unparseable payloads.
 */
export function parseAgentEvent(raw: string): AgentEvent | null {
  if (!raw || raw === "ping" || raw === "keepalive") return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== "object") return null;
  const obj = parsed as Record<string, unknown>;
  const inner =
    typeof obj.kind === "string"
      ? obj
      : obj.event && typeof obj.event === "object"
        ? (obj.event as Record<string, unknown>)
        : obj.data && typeof obj.data === "object" && typeof (obj.data as Record<string, unknown>).kind === "string"
          ? (obj.data as Record<string, unknown>)
          : null;
  if (!inner || typeof inner.kind !== "string") return null;
  return {
    id: typeof inner.id === "string" ? inner.id : `EV-${Math.random().toString(36).slice(2, 12)}`,
    run_id: typeof inner.run_id === "string" ? inner.run_id : null,
    seq: typeof inner.seq === "number" ? inner.seq : 0,
    ts: typeof inner.ts === "string" ? inner.ts : new Date().toISOString(),
    agent: typeof inner.agent === "string" ? inner.agent : "system",
    kind: inner.kind,
    title: typeof inner.title === "string" ? inner.title : "",
    detail: typeof inner.detail === "string" ? inner.detail : "",
    data: inner.data && typeof inner.data === "object" ? (inner.data as Record<string, unknown>) : {},
  };
}

// ---------------------------------------------------------------- core stream

export interface StreamHandlers {
  onEvent: (event: AgentEvent) => void;
  onStatus?: (status: StreamStatus) => void;
}

export interface StreamOptions {
  /** Close (and stop reconnecting) once `run.completed` / `run.failed` arrives. Default false. */
  closeOnRunEnd?: boolean;
  /** Initial reconnect delay in ms (doubles up to `maxReconnectMs`). Default 1000. */
  reconnectMs?: number;
  /** Cap for the backoff. Default 15000. */
  maxReconnectMs?: number;
}

/**
 * Open an EventSource with reconnect + de-duplication. Returns a function that
 * closes the stream and cancels any pending reconnect.
 */
export function openEventStream(url: string, handlers: StreamHandlers, opts: StreamOptions = {}): () => void {
  if (typeof window === "undefined" || typeof EventSource === "undefined") {
    return () => {};
  }
  const baseDelay = opts.reconnectMs ?? 1000;
  const maxDelay = opts.maxReconnectMs ?? 15_000;

  let es: EventSource | null = null;
  let closed = false;
  let attempt = 0;
  let maxSeq = 0;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  const setStatus = (s: StreamStatus) => handlers.onStatus?.(s);

  const handleMessage = (msg: MessageEvent) => {
    const ev = parseAgentEvent(typeof msg.data === "string" ? msg.data : "");
    if (!ev) return;
    if (ev.seq > 0) {
      if (ev.seq <= maxSeq) return; // replayed after reconnect
      maxSeq = ev.seq;
    }
    handlers.onEvent(ev);
    if (opts.closeOnRunEnd && (ev.kind === "run.completed" || ev.kind === "run.failed")) {
      close("closed");
    }
  };

  const scheduleReconnect = () => {
    if (closed) return;
    const delay = Math.min(maxDelay, baseDelay * 2 ** attempt);
    attempt += 1;
    setStatus("reconnecting");
    reconnectTimer = setTimeout(connect, delay);
  };

  const connect = () => {
    if (closed) return;
    reconnectTimer = null;
    setStatus(attempt === 0 ? "connecting" : "reconnecting");
    try {
      es = new EventSource(url);
    } catch {
      scheduleReconnect();
      return;
    }
    es.onopen = () => {
      attempt = 0;
      setStatus("open");
    };
    es.onmessage = handleMessage;
    for (const kind of EVENT_KINDS) es.addEventListener(kind, handleMessage as EventListener);
    es.onerror = () => {
      if (closed) return;
      // The browser retries on its own while CONNECTING; only take over once it gave up.
      if (es && es.readyState === EventSource.CLOSED) {
        es.close();
        es = null;
        scheduleReconnect();
      } else {
        setStatus("reconnecting");
      }
    };
  };

  const close = (finalStatus: StreamStatus = "closed") => {
    if (closed) return;
    closed = true;
    if (reconnectTimer) clearTimeout(reconnectTimer);
    reconnectTimer = null;
    if (es) {
      es.close();
      es = null;
    }
    setStatus(finalStatus);
  };

  connect();
  return () => close("closed");
}

/**
 * Subscribe to one run's events (replay + live) without React. The callback
 * receives every de-duplicated event. Returns an unsubscribe function.
 */
export function subscribeRun(
  runId: string,
  cb: (event: AgentEvent) => void,
  opts: StreamOptions & { onStatus?: (s: StreamStatus) => void } = {},
): () => void {
  return openEventStream(runEventsUrl(runId), { onEvent: cb, onStatus: opts.onStatus }, { closeOnRunEnd: true, ...opts });
}

/** Subscribe to every run's events without React. */
export function subscribeGlobal(
  cb: (event: AgentEvent) => void,
  opts: StreamOptions & { onStatus?: (s: StreamStatus) => void } = {},
): () => void {
  return openEventStream(globalEventsUrl(), { onEvent: cb, onStatus: opts.onStatus }, opts);
}

// ---------------------------------------------------------------- derived run state

export interface RunLiveState {
  /** Derived from run.started / run.completed / run.failed. */
  runStatus: RunStatus | null;
  /** Live counters from the latest run.progress (reconciled, matched, exceptions, human, precedent_hits). */
  counts: RunCounts;
  /** Final metrics from run.completed (if the backend attaches them). */
  metrics: RunMetrics | null;
  /** Agent currently working (from agent.started / handoff). */
  currentAgent: AgentId | string | null;
  /** Human-readable current step (from agent.started / agent.step / run.plan). */
  currentStep: string | null;
  /** Agents that have emitted agent.finished, in order. */
  finishedAgents: string[];
  /** Plan steps announced by run.plan (if provided as a string list). */
  plan: string[] | null;
  /** Error text from run.failed. */
  error: string | null;
  /** Ids of exceptions raised so far. */
  exceptionsRaised: number;
  /** Precedent hits counted from memory.precedent_hit events (fallback if progress lacks it). */
  precedentHits: number;
}

export const EMPTY_LIVE_STATE: RunLiveState = {
  runStatus: null,
  counts: {},
  metrics: null,
  currentAgent: null,
  currentStep: null,
  finishedAgents: [],
  plan: null,
  error: null,
  exceptionsRaised: 0,
  precedentHits: 0,
};

const COUNT_KEYS = ["reconciled", "matched", "exceptions", "human", "precedent_hits"] as const;

function pickCounts(data: Record<string, unknown>): RunCounts | null {
  const src = (data.counts && typeof data.counts === "object" ? data.counts : data) as Record<string, unknown>;
  const out: RunCounts = {};
  let any = false;
  for (const [k, v] of Object.entries(src)) {
    if (typeof v === "number") {
      out[k] = v;
      any = true;
    }
  }
  return any ? out : null;
}

/** Canonical counter keys carried on run.progress, in display order. */
export const RUN_COUNT_KEYS = COUNT_KEYS;

function isRunMetrics(v: unknown): v is RunMetrics {
  return !!v && typeof v === "object" && "period_id" in (v as object) && "total_items" in (v as object);
}

/** Pure reducer: fold one event into the derived live state. Exported for /trace replays. */
export function applyEventToLiveState(state: RunLiveState, ev: AgentEvent): RunLiveState {
  const data = ev.data ?? {};
  switch (ev.kind) {
    case "run.started":
      return { ...state, runStatus: "running", error: null, currentAgent: "cfo", currentStep: ev.title || "Starting run" };
    case "run.plan": {
      const raw = (data.plan ?? data.steps) as unknown;
      const plan = Array.isArray(raw) ? raw.map((s) => (typeof s === "string" ? s : JSON.stringify(s))) : state.plan;
      return { ...state, plan, currentAgent: "cfo", currentStep: ev.title || "Planning" };
    }
    case "run.progress": {
      const counts = pickCounts(data);
      return counts ? { ...state, counts: { ...state.counts, ...counts } } : state;
    }
    case "run.completed": {
      const metrics = isRunMetrics(data.metrics) ? data.metrics : isRunMetrics(data) ? (data as unknown as RunMetrics) : state.metrics;
      const counts = pickCounts(data);
      return {
        ...state,
        runStatus: "completed",
        metrics,
        counts: counts ? { ...state.counts, ...counts } : state.counts,
        currentAgent: null,
        currentStep: "Completed",
      };
    }
    case "run.failed":
      return {
        ...state,
        runStatus: "failed",
        error: (typeof data.error === "string" && data.error) || ev.detail || ev.title || "Run failed",
        currentAgent: null,
        currentStep: "Failed",
      };
    case "agent.started":
      return {
        ...state,
        currentAgent: ev.agent,
        currentStep: (typeof data.step === "string" && data.step) || ev.title || null,
      };
    case "agent.step":
      return { ...state, currentAgent: ev.agent, currentStep: ev.title || state.currentStep };
    case "agent.finished":
      return {
        ...state,
        finishedAgents: state.finishedAgents.includes(ev.agent) ? state.finishedAgents : [...state.finishedAgents, ev.agent],
      };
    case "handoff": {
      const to = typeof data.to === "string" ? data.to : null;
      return to ? { ...state, currentAgent: to, currentStep: ev.title || state.currentStep } : state;
    }
    case "exception.raised":
      return { ...state, exceptionsRaised: state.exceptionsRaised + 1 };
    case "memory.precedent_hit":
      return { ...state, precedentHits: state.precedentHits + 1 };
    default:
      return state;
  }
}

// ---------------------------------------------------------------- hooks

export interface UseEventsResult extends RunLiveState {
  /** Buffered events (oldest → newest), at most MAX_BUFFERED_EVENTS. */
  events: AgentEvent[];
  /** The most recent event, or null. */
  latest: AgentEvent | null;
  /** Connection status of the EventSource. */
  status: StreamStatus;
  /** Drop buffered events and reset derived state (keeps the connection). */
  clear: () => void;
}

interface StreamState {
  /** The URL this state belongs to; state from a previous URL is ignored at render time. */
  url: string | null;
  events: AgentEvent[];
  live: RunLiveState;
  status: StreamStatus;
}

const IDLE_STATE: StreamState = { url: null, events: [], live: EMPTY_LIVE_STATE, status: "idle" };

function freshState(url: string | null, status: StreamStatus): StreamState {
  return { url, events: [], live: EMPTY_LIVE_STATE, status };
}

function useEventStream(url: string | null, opts: StreamOptions): UseEventsResult {
  const [state, setState] = useState<StreamState>(IDLE_STATE);
  const pending = useRef<AgentEvent[]>([]);
  const frame = useRef<number | null>(null);

  const clear = useCallback(() => {
    pending.current = [];
    setState((prev) => ({ ...prev, events: [], live: EMPTY_LIVE_STATE }));
  }, []);

  // Stable option values so the effect does not re-run on object identity changes.
  const closeOnRunEnd = opts.closeOnRunEnd ?? false;
  const reconnectMs = opts.reconnectMs;
  const maxReconnectMs = opts.maxReconnectMs;

  useEffect(() => {
    if (!url) return;
    const tag = url;
    pending.current = [];

    const flush = () => {
      frame.current = null;
      const batch = pending.current;
      if (batch.length === 0) return;
      pending.current = [];
      setState((prev) => {
        const base = prev.url === tag ? prev : freshState(tag, prev.status);
        let live = base.live;
        for (const ev of batch) live = applyEventToLiveState(live, ev);
        const merged =
          base.events.length + batch.length > MAX_BUFFERED_EVENTS
            ? [...base.events, ...batch].slice(-MAX_BUFFERED_EVENTS)
            : [...base.events, ...batch];
        return { ...base, events: merged, live };
      });
    };

    const stop = openEventStream(
      tag,
      {
        onEvent: (ev) => {
          pending.current.push(ev);
          if (frame.current === null) {
            frame.current =
              typeof requestAnimationFrame === "function"
                ? requestAnimationFrame(flush)
                : (setTimeout(flush, 16) as unknown as number);
          }
        },
        onStatus: (status) => {
          setState((prev) => (prev.url === tag ? { ...prev, status } : freshState(tag, status)));
        },
      },
      { closeOnRunEnd, reconnectMs, maxReconnectMs },
    );
    return () => {
      stop();
      if (frame.current !== null && typeof cancelAnimationFrame === "function") cancelAnimationFrame(frame.current);
      frame.current = null;
      pending.current = [];
    };
  }, [url, closeOnRunEnd, reconnectMs, maxReconnectMs]);

  // Ignore state that belongs to a previous URL (avoids a reset-in-effect and any stale flash).
  const current = state.url === url ? state : freshState(url, url ? "connecting" : "idle");
  const latest = current.events.length ? current.events[current.events.length - 1] : null;

  return useMemo(
    () => ({ ...current.live, events: current.events, latest, status: current.status, clear }),
    [current, latest, clear],
  );
}

/**
 * Subscribe to one run's SSE stream. Pass `null` to stay idle (no connection).
 * Replays history on connect, then streams live events; closes itself once the
 * run completes or fails.
 */
export function useRunEvents(runId: string | null | undefined): UseEventsResult {
  const url = runId ? runEventsUrl(runId) : null;
  return useEventStream(url, { closeOnRunEnd: true });
}

/**
 * Subscribe to every run's events (`/api/events/stream`). Stays open for the
 * component's lifetime; pass `enabled=false` to pause.
 */
export function useGlobalEvents(enabled = true): UseEventsResult {
  const url = enabled ? globalEventsUrl() : null;
  return useEventStream(url, { closeOnRunEnd: false });
}
