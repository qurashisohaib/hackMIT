/**
 * Typed HTTP client for the CFO Office backend (ARCHITECTURE §7).
 *
 * One function per endpoint. Every call goes through `request()`, which adds a
 * timeout, JSON handling and a structured `ApiError`. Use `isOffline(err)` to
 * decide whether to show the "backend offline" banner.
 *
 * Base URL: same origin, or `process.env.NEXT_PUBLIC_API_URL`.
 */
import type {
  CloseReport,
  CreateRunResponse,
  CytoscapeElements,
  DataTable,
  ExceptionDetail,
  ExceptionView,
  ForecastView,
  GraphSubgraph,
  HealthResponse,
  JsonObject,
  MemoryStats,
  MetricsResponse,
  NodeDetail,
  PeriodView,
  ResetResponse,
  ResolveRequest,
  ResolveResponse,
  RuleView,
  RunView,
  AgentEvent,
} from "./types";

/** Origin of the backend, without trailing slash (e.g. http://localhost:8000). */
export const API_BASE = (process.env.NEXT_PUBLIC_API_URL ?? "").replace(/\/+$/, "");

/** `${API_BASE}/api` — prefix for every REST + SSE route. */
export const API_URL = `${API_BASE}/api`;

/** Default request timeout in ms. */
export const DEFAULT_TIMEOUT_MS = 10_000;

export type ApiErrorKind = "http" | "network" | "timeout" | "parse" | "aborted";

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number;
  readonly url: string;
  readonly body: unknown;

  constructor(message: string, opts: { kind: ApiErrorKind; status?: number; url: string; body?: unknown }) {
    super(message);
    this.name = "ApiError";
    this.kind = opts.kind;
    this.status = opts.status ?? 0;
    this.url = opts.url;
    this.body = opts.body;
  }

  /** True when the backend is unreachable (connection refused, DNS, timeout). */
  get offline(): boolean {
    return this.kind === "network" || this.kind === "timeout";
  }
}

/** Narrow helper: is this error a "backend is down" condition? */
export function isOffline(err: unknown): boolean {
  return err instanceof ApiError && err.offline;
}

/** Extract a readable message from any thrown value. */
export function errorMessage(err: unknown, fallback = "Something went wrong"): string {
  if (err instanceof ApiError) {
    if (err.offline) return "Backend offline";
    const detail = (err.body as { detail?: unknown } | null)?.detail;
    if (typeof detail === "string") return detail;
    return err.message;
  }
  if (err instanceof Error) return err.message || fallback;
  return fallback;
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  /** Override the default timeout. */
  timeoutMs?: number;
  /** External abort signal (e.g. from a React effect cleanup). */
  signal?: AbortSignal;
  /** Query-string parameters; null/undefined values are dropped. */
  query?: Record<string, string | number | boolean | null | undefined>;
}

function buildUrl(path: string, query?: RequestOptions["query"]): string {
  const url = path.startsWith("http") ? path : `${API_URL}${path.startsWith("/") ? path : `/${path}`}`;
  if (!query) return url;
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v === null || v === undefined || v === "") continue;
    params.set(k, String(v));
  }
  const qs = params.toString();
  return qs ? `${url}?${qs}` : url;
}

/**
 * Low-level fetch wrapper. Resolves with parsed JSON (or `undefined` for 204),
 * rejects with `ApiError`.
 */
export async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const url = buildUrl(path, opts.query);
  const controller = new AbortController();
  const timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);

  const onExternalAbort = () => controller.abort();
  if (opts.signal) {
    if (opts.signal.aborted) controller.abort();
    else opts.signal.addEventListener("abort", onExternalAbort, { once: true });
  }

  try {
    const res = await fetch(url, {
      method: opts.method ?? "GET",
      headers: opts.body !== undefined ? { "Content-Type": "application/json", Accept: "application/json" } : { Accept: "application/json" },
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      signal: controller.signal,
      cache: "no-store",
    });

    const text = await res.text();
    let data: unknown = undefined;
    if (text) {
      try {
        data = JSON.parse(text);
      } catch {
        if (res.ok) {
          throw new ApiError(`Invalid JSON from ${url}`, { kind: "parse", status: res.status, url, body: text });
        }
        data = text;
      }
    }

    if (!res.ok) {
      const detail = (data as { detail?: unknown } | null)?.detail;
      const msg = typeof detail === "string" ? detail : `${res.status} ${res.statusText || "Request failed"}`;
      throw new ApiError(msg, { kind: "http", status: res.status, url, body: data });
    }
    return data as T;
  } catch (err) {
    if (err instanceof ApiError) throw err;
    if (err instanceof DOMException && err.name === "AbortError") {
      if (timedOut) throw new ApiError(`Request timed out after ${timeoutMs} ms`, { kind: "timeout", url });
      throw new ApiError("Request aborted", { kind: "aborted", url });
    }
    // fetch throws TypeError on network failure / CORS / connection refused
    throw new ApiError(err instanceof Error ? err.message : "Network error", { kind: "network", url });
  } finally {
    clearTimeout(timer);
    opts.signal?.removeEventListener("abort", onExternalAbort);
  }
}

// ---------------------------------------------------------------- endpoints

/** GET /api/health — backend liveness, brain type, company name. Short timeout. */
export function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return request<HealthResponse>("/health", { signal, timeoutMs: 4_000 });
}

/** GET /api/periods */
export function getPeriods(signal?: AbortSignal): Promise<PeriodView[]> {
  return request<PeriodView[]>("/periods", { signal });
}

/** POST /api/runs {period_id} → {run_id}. Starts a background period close. */
export function createRun(periodId: string, signal?: AbortSignal): Promise<CreateRunResponse> {
  return request<CreateRunResponse>("/runs", { method: "POST", body: { period_id: periodId }, signal });
}

/** GET /api/runs */
export function getRuns(signal?: AbortSignal): Promise<RunView[]> {
  return request<RunView[]>("/runs", { signal });
}

/** GET /api/runs/{run_id} */
export function getRun(runId: string, signal?: AbortSignal): Promise<RunView> {
  return request<RunView>(`/runs/${encodeURIComponent(runId)}`, { signal });
}

/** GET /api/runs/{run_id}/trace — the full event list (non-streaming). */
export function getRunTrace(runId: string, signal?: AbortSignal): Promise<AgentEvent[]> {
  return request<AgentEvent[]>(`/runs/${encodeURIComponent(runId)}/trace`, { signal, timeoutMs: 20_000 });
}

/** SSE URL for one run (replay + live). Use with `EventSource` or `useRunEvents`. */
export function runEventsUrl(runId: string): string {
  return `${API_URL}/runs/${encodeURIComponent(runId)}/events`;
}

/** SSE URL for all runs. */
export function globalEventsUrl(): string {
  return `${API_URL}/events/stream`;
}

export interface ExceptionFilters {
  period_id?: string | null;
  status?: string | null;
}

/** GET /api/exceptions?period_id&status */
export function getExceptions(filters: ExceptionFilters = {}, signal?: AbortSignal): Promise<ExceptionView[]> {
  return request<ExceptionView[]>("/exceptions", {
    query: { period_id: filters.period_id, status: filters.status },
    signal,
  });
}

/** GET /api/exceptions/{id} */
export function getException(id: string, signal?: AbortSignal): Promise<ExceptionDetail> {
  return request<ExceptionDetail>(`/exceptions/${encodeURIComponent(id)}`, { signal });
}

/** POST /api/exceptions/{id}/resolve — approve / teach / manual match / dismiss. Propagates to siblings. */
export function resolveException(id: string, body: ResolveRequest, signal?: AbortSignal): Promise<ResolveResponse> {
  return request<ResolveResponse>(`/exceptions/${encodeURIComponent(id)}/resolve`, {
    method: "POST",
    body,
    signal,
    timeoutMs: 60_000,
  });
}

export interface MemoryGraphQuery {
  /** Comma-separated node labels, e.g. "Rule,Exception". */
  labels?: string[] | string | null;
  period_id?: string | null;
  limit?: number | null;
  /** Node id to centre the export on. */
  focus?: string | null;
}

/** GET /api/memory/graph → cytoscape elements. */
export function getMemoryGraph(q: MemoryGraphQuery = {}, signal?: AbortSignal): Promise<CytoscapeElements> {
  const labels = Array.isArray(q.labels) ? q.labels.join(",") : q.labels;
  return request<CytoscapeElements>("/memory/graph", {
    query: { labels, period_id: q.period_id, limit: q.limit, focus: q.focus },
    signal,
    timeoutMs: 20_000,
  });
}

/** GET /api/memory/nodes/{id} → {node, edges, neighbors} */
export function getMemoryNode(id: string, signal?: AbortSignal): Promise<NodeDetail> {
  return request<NodeDetail>(`/memory/nodes/${encodeURIComponent(id)}`, { signal });
}

/** GET /api/memory/provenance/{id} → GraphSubgraph (ordered for highlighting). */
export function getProvenance(id: string, signal?: AbortSignal): Promise<GraphSubgraph> {
  return request<GraphSubgraph>(`/memory/provenance/${encodeURIComponent(id)}`, { signal });
}

/** GET /api/memory/rules */
export function getRules(signal?: AbortSignal): Promise<RuleView[]> {
  return request<RuleView[]>("/memory/rules", { signal });
}

/** GET /api/memory/stats */
export function getMemoryStats(signal?: AbortSignal): Promise<MemoryStats> {
  return request<MemoryStats>("/memory/stats", { signal });
}

/** GET /api/metrics → latest RunMetrics per period + deltas. */
export function getMetrics(signal?: AbortSignal): Promise<MetricsResponse> {
  return request<MetricsResponse>("/metrics", { signal });
}

/** GET /api/forecast?period_id */
export function getForecast(periodId?: string | null, signal?: AbortSignal): Promise<ForecastView> {
  return request<ForecastView>("/forecast", { query: { period_id: periodId }, signal });
}

/** GET /api/reports/{period_id} */
export function getReport(periodId: string, signal?: AbortSignal): Promise<CloseReport> {
  return request<CloseReport>(`/reports/${encodeURIComponent(periodId)}`, { signal });
}

/** GET /api/data/{table}?period_id → raw rows. */
export function getData<T extends JsonObject = JsonObject>(
  table: DataTable,
  periodId?: string | null,
  signal?: AbortSignal,
): Promise<T[]> {
  return request<T[]>(`/data/${table}`, { query: { period_id: periodId }, signal, timeoutMs: 20_000 });
}

/** POST /api/reset — reseed financial truth and clear memory. */
export function resetDemo(signal?: AbortSignal): Promise<ResetResponse> {
  return request<ResetResponse>("/reset", { method: "POST", signal, timeoutMs: 60_000 });
}

/** Namespace export for `import { api } from "@/lib/api"` style. */
export const api = {
  getHealth,
  getPeriods,
  createRun,
  getRuns,
  getRun,
  getRunTrace,
  runEventsUrl,
  globalEventsUrl,
  getExceptions,
  getException,
  resolveException,
  getMemoryGraph,
  getMemoryNode,
  getProvenance,
  getRules,
  getMemoryStats,
  getMetrics,
  getForecast,
  getReport,
  getData,
  resetDemo,
};

export default api;
