"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore, type FormEvent } from "react";
import { ArrowRight, GitBranch, Network, Pause, Play, RefreshCw, Search, SkipForward, X } from "lucide-react";
import { DEFAULT_LABELS, EMPTY_GRAPH, graphCaption, NODE_COLORS, nodeName, provenanceElements } from "@/components/memory/graph-data";
import { NodePanel } from "@/components/memory/node-panel";
import { RuleLibrary } from "@/components/memory/rule-library";
import { useHealth } from "@/components/layout/health-provider";
import { EmptyState, PageHeader } from "@/components/shared";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input, inputClassName } from "@/components/ui/input";
import { errorMessage, getMemoryGraph, getMemoryStats, getPeriods, getProvenance, getRules } from "@/lib/api";
import type { CytoscapeElements, GraphSubgraph, MemoryStats, PeriodView, RuleView } from "@/lib/types";
import { cn, formatNumber, periodMonth } from "@/lib/utils";

const GraphCanvas = dynamic(() => import("./graph-canvas"), {
  ssr: false,
  loading: () => <div role="status" className="flex h-[480px] items-center justify-center text-sm text-muted sm:h-[560px]">Preparing the graph canvas…</div>,
});

const MOTION_QUERY = "(prefers-reduced-motion: reduce)";
function subscribeMotion(callback: () => void) {
  const query = window.matchMedia(MOTION_QUERY);
  query.addEventListener("change", callback);
  return () => query.removeEventListener("change", callback);
}
function motionSnapshot() { return window.matchMedia(MOTION_QUERY).matches; }
function serverMotionSnapshot() { return true; }

interface MemoryData {
  graph: CytoscapeElements;
  rules: RuleView[];
  stats: MemoryStats | null;
  periods: PeriodView[];
  key: string;
  loading: boolean;
  error: string | null;
  ruleError: string | null;
}

interface Traversal {
  root: string;
  chain: GraphSubgraph | null;
  order: string[];
  count: number;
  playing: boolean;
  loading: boolean;
  error: string | null;
}

export function MemoryWorkspace() {
  const params = useSearchParams();
  const router = useRouter();
  const { online } = useHealth();
  const focus = params.get("focus") || null;
  const period = params.get("period_id") ?? "";
  const labelParam = params.get("labels") || DEFAULT_LABELS.join(",");
  const limitParam = Number(params.get("limit") || 80);
  const limit = [40, 80, 160, 300].includes(limitParam) ? limitParam : 80;
  const labels = useMemo(() => labelParam.split(",").filter(Boolean), [labelParam]);
  const viewKey = `${focus ?? ""}|${period}|${labelParam}|${limit}`;
  const [selection, setSelection] = useState<{ scope: string; id: string | null } | null>(null);
  const selectedId = selection?.scope === viewKey ? selection.id : focus;
  const [revision, setRevision] = useState(0);
  const [showLabels, setShowLabels] = useState(true);
  const [nodeSearch, setNodeSearch] = useState("");
  const [traversal, setTraversal] = useState<Traversal | null>(null);
  const traceRequest = useRef<AbortController | null>(null);
  const reducedMotion = useSyncExternalStore(subscribeMotion, motionSnapshot, serverMotionSnapshot);
  const [state, setState] = useState<MemoryData>({
    graph: EMPTY_GRAPH, rules: [], stats: null, periods: [], key: "", loading: true, error: null, ruleError: null,
  });

  useEffect(() => {
    const controller = new AbortController();
    Promise.allSettled([
      getMemoryGraph({ labels, period_id: period, focus, limit }, controller.signal),
      getRules(controller.signal), getMemoryStats(controller.signal), getPeriods(controller.signal),
    ]).then(([graph, rules, stats, periods]) => {
      if (controller.signal.aborted) return;
      setState((previous) => ({
        graph: graph.status === "fulfilled" ? graph.value : previous.key === viewKey ? previous.graph : EMPTY_GRAPH,
        rules: rules.status === "fulfilled" ? rules.value : previous.rules,
        stats: stats.status === "fulfilled" ? stats.value : previous.stats,
        periods: periods.status === "fulfilled" ? periods.value : previous.periods,
        key: viewKey,
        loading: false,
        error: graph.status === "rejected" ? errorMessage(graph.reason) : null,
        ruleError: rules.status === "rejected" ? errorMessage(rules.reason) : null,
      }));
    });
    return () => controller.abort();
  }, [labels, period, focus, limit, viewKey, revision, online]);

  useEffect(() => () => traceRequest.current?.abort(), [selectedId, viewKey]);

  const currentTrace = traversal?.root === selectedId ? traversal : null;
  const tracing = !!currentTrace?.chain;
  const tracePlaying = !!currentTrace?.playing && !reducedMotion;
  const traceCount = currentTrace ? reducedMotion && currentTrace.playing ? currentTrace.order.length : currentTrace.count : 0;

  useEffect(() => {
    if (!tracePlaying) return;
    const timer = window.setInterval(() => setTraversal((previous) => {
      if (!previous || previous.root !== selectedId || !previous.playing) return previous;
      const count = Math.min(previous.count + 1, previous.order.length);
      return { ...previous, count, playing: count < previous.order.length };
    }), 800);
    return () => window.clearInterval(timer);
  }, [tracePlaying, selectedId]);

  const selectNode = useCallback((id: string) => setSelection({ scope: viewKey, id }), [viewKey]);
  const updateQuery = (updates: Record<string, string | null>) => {
    traceRequest.current?.abort();
    const next = new URLSearchParams(params.toString());
    for (const [key, value] of Object.entries(updates)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    setTraversal(null);
    router.replace(`/memory${next.size ? `?${next}` : ""}`, { scroll: false });
  };

  async function trace(id: string) {
    traceRequest.current?.abort();
    const controller = new AbortController();
    traceRequest.current = controller;
    setTraversal({ root: id, chain: null, order: [], count: 0, playing: false, loading: true, error: null });
    try {
      const chain = await getProvenance(id, controller.signal);
      if (controller.signal.aborted) return;
      const ids = new Set(chain.nodes.map((node) => node.id));
      const order = [...new Set(chain.order.length ? chain.order : chain.nodes.map((node) => node.id))].filter((nodeId) => ids.has(nodeId));
      setTraversal({ root: id, chain, order, count: reducedMotion ? order.length : Math.min(1, order.length), playing: !reducedMotion && order.length > 1, loading: false, error: null });
    } catch (error) {
      if (!controller.signal.aborted) setTraversal({ root: id, chain: null, order: [], count: 0, playing: false, loading: false, error: errorMessage(error) });
    }
  }

  function focusNode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const id = new FormData(event.currentTarget).get("focus");
    if (typeof id === "string" && id.trim()) updateQuery({ focus: id.trim(), period_id: null });
  }

  const loading = state.loading || state.key !== viewKey;
  const baseGraph = state.key === viewKey ? state.graph : EMPTY_GRAPH;
  const chain = currentTrace?.chain ?? null;
  const graph = useMemo(() => chain ? provenanceElements(chain) : baseGraph, [chain, baseGraph]);
  const highlightedIds = useMemo(() => currentTrace?.order.slice(0, traceCount) ?? [], [currentTrace?.order, traceCount]);
  const directory = graph.nodes.filter(({ data }) => `${data.id} ${data.label} ${graphCaption(data)}`.toLowerCase().includes(nodeSearch.trim().toLowerCase()));
  const allLabels = [...new Set([...Object.keys(NODE_COLORS), ...Object.keys(state.stats?.by_label ?? {}), ...labels])];
  const periodOptions = [...new Map([...state.periods.map((p) => [p.id, p.name] as const), ...(period ? [[period, periodMonth(period)] as const] : [])]).entries()].sort(([a], [b]) => a.localeCompare(b));

  return (
    <div className="space-y-6">
      <PageHeader eyebrow="Every decision has a lineage" title="Financial memory"
        description="Follow a decision back to the evidence, the rule, and the person who taught it."
        actions={<Button variant="secondary" size="sm" onClick={() => { traceRequest.current?.abort(); setTraversal(null); setRevision((value) => value + 1); }}><RefreshCw aria-hidden /> Refresh memory</Button>} />

      <div className="grid gap-3 sm:grid-cols-3">
        {[["Memory nodes", state.stats?.nodes], ["Evidence relationships", state.stats?.edges], ["Learned rules", state.stats?.by_label.Rule ?? (state.loading || state.ruleError ? null : state.rules.length)]].map(([label, value]) => <Card key={label} className="flex items-center justify-between px-5 py-4"><span className="text-xs text-muted">{label}</span><span className="font-mono text-xl">{formatNumber(typeof value === "number" ? value : null)}</span></Card>)}
      </div>

      <Card className="space-y-4 p-4">
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-[1fr_180px_140px_auto]">
          <form key={focus ?? "unfocused"} onSubmit={focusNode} className="flex items-end gap-2"><label className="min-w-0 flex-1 space-y-1.5 text-xs text-muted"><span>Focus on a node</span><Input name="focus" defaultValue={focus ?? ""} placeholder="Paste a rule, decision, or exception ID" required /></label><Button size="icon" variant="secondary" type="submit" aria-label="Focus graph on node"><Search aria-hidden /></Button></form>
          <label className="space-y-1.5 text-xs text-muted"><span>Period</span><select className={inputClassName} disabled={!!focus || tracing} value={period} onChange={(event) => updateQuery({ period_id: event.target.value, focus: null })}><option value="">All periods</option>{periodOptions.map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></label>
          <label className="space-y-1.5 text-xs text-muted"><span>Node limit</span><select className={inputClassName} value={limit} disabled={tracing} onChange={(event) => updateQuery({ limit: event.target.value })}>{[40, 80, 160, 300].map((count) => <option key={count} value={count}>{count} nodes</option>)}</select></label>
          <label className="flex h-9 items-center gap-2 self-end text-xs text-muted"><input type="checkbox" className="accent-emerald-400" checked={showLabels} onChange={(event) => setShowLabels(event.target.checked)} />Show labels</label>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="mr-1 font-mono text-[10px] uppercase tracking-wider text-subtle">Node types / legend</span>
          {allLabels.slice(0, 8).map((label) => <LabelFilter key={label} label={label} checked={labels.includes(label)} disabled={!!focus || tracing || labels.length === 1 && labels.includes(label)} onChange={() => updateQuery({ labels: (labels.includes(label) ? labels.filter((item) => item !== label) : [...labels, label]).join(",") })} />)}
          <details className="w-full pt-1"><summary className="cursor-pointer text-xs text-muted">More node types</summary><div className="mt-2 flex flex-wrap gap-2">{allLabels.slice(8).map((label) => <LabelFilter key={label} label={label} checked={labels.includes(label)} disabled={!!focus || tracing || labels.length === 1 && labels.includes(label)} onChange={() => updateQuery({ labels: (labels.includes(label) ? labels.filter((item) => item !== label) : [...labels, label]).join(",") })} />)}</div></details>
        </div>
        <p className="text-[11px] leading-relaxed text-muted">{focus || tracing ? "Focused and provenance views include connected node types across periods. Clear the focus to filter the overview." : `Showing a curated knowledge layer, up to ${limit} nodes. Select a node for evidence or narrow the view by period.`}</p>
        {focus && <div className="flex items-center gap-2 rounded-md border border-emerald-500/20 bg-emerald-500/5 px-3 py-2"><span className="min-w-0 flex-1 break-all font-mono text-xs text-emerald-200">Focus: {focus}</span><Button variant="ghost" size="xs" onClick={() => updateQuery({ focus: null })}><X aria-hidden />Clear focus</Button></div>}
      </Card>

      {state.error && <div role="alert" className="rounded-lg border border-amber-400/20 bg-amber-500/5 p-3 text-sm text-amber-100">{state.error}. {baseGraph.nodes.length ? "Showing the last available graph." : "Reconnect the backend to explore saved memory. No sample nodes are shown."}</div>}
      {currentTrace?.error && <p role="alert" className="rounded-lg border border-rose-400/20 bg-rose-500/5 p-3 text-sm text-rose-200">Provenance unavailable: {currentTrace.error}. Try tracing this node again.</p>}

      <div className={cn("grid items-start gap-4", selectedId && "xl:grid-cols-[minmax(0,1fr)_340px]")}>
        <div className="min-w-0 space-y-4">
          <Card className="overflow-hidden">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-3">
              <h2 className="inline-flex items-center gap-2 text-sm font-medium"><Network className="size-4 text-emerald-300" aria-hidden />{tracing ? "Provenance graph" : focus ? "Focused neighborhood" : "Memory overview"}</h2>
              <span className="font-mono text-[10px] text-muted">{loading && !tracing ? "Loading…" : `${graph.nodes.length} nodes · ${graph.edges.length} relationships`}</span>
            </div>
            {loading && !tracing ? <div role="status" className="flex h-[480px] items-center justify-center text-sm text-muted">Loading financial memory…</div> : graph.nodes.length ? (
              <GraphCanvas elements={graph} selectedId={selectedId} onSelect={selectNode} showLabels={showLabels} highlightedIds={highlightedIds} tracing={tracing} />
            ) : <div className="p-6"><EmptyState icon={Network} title={state.error ? "Memory is unavailable" : focus ? "No nodes found around this reference" : "No memory in this view"}
              description={focus ? "Check the ID or clear the focus. A demo reset removes learned rules and decisions." : "Run a close and teach an exception to create connected decisions, rules, and corrections. You can also broaden the node filters."}
              action={<Link href="/exceptions" className={buttonVariants({ variant: "secondary", size: "sm" })}>Open exception desk <ArrowRight aria-hidden /></Link>} /></div>}
            <p className="border-t border-border px-4 py-2.5 text-[10px] text-muted">Drag to pan · scroll to zoom · select a node to inspect · arrows show relationship direction</p>
          </Card>

          {currentTrace?.chain && <Card className="space-y-3 p-4" aria-label="Provenance traversal">
            <div className="flex flex-wrap items-center justify-between gap-3"><h3 className="inline-flex items-center gap-2 text-sm font-semibold"><GitBranch className="size-4 text-emerald-300" aria-hidden />Back to the source</h3><Button variant="ghost" size="xs" onClick={() => setTraversal(null)}><X aria-hidden />Exit trace</Button></div>
            <p className="text-xs text-muted">Walk the recorded provenance in traversal order. Highlighted arrows connect the visited evidence.</p>
            <div className="flex flex-wrap items-center gap-2">
              <Button size="sm" variant="secondary" disabled={!currentTrace.order.length} onClick={() => setTraversal((previous) => previous ? {
                ...previous, count: reducedMotion ? previous.order.length : previous.count >= previous.order.length ? 1 : previous.count,
                playing: reducedMotion ? false : !tracePlaying,
              } : previous)}>{tracePlaying ? <Pause aria-hidden /> : <Play aria-hidden />}{reducedMotion ? "Show complete path" : tracePlaying ? "Pause" : traceCount >= currentTrace.order.length ? "Replay" : "Play"}</Button>
              <Button size="sm" variant="outline" disabled={traceCount >= currentTrace.order.length} onClick={() => setTraversal((previous) => previous ? { ...previous, playing: false, count: Math.min(traceCount + 1, previous.order.length) } : previous)}><SkipForward aria-hidden />Step</Button>
              <span role="status" aria-live="polite" className="text-xs text-muted">{traceCount} / {currentTrace.order.length} nodes{reducedMotion ? " · reduced motion" : tracePlaying ? " · tracing" : ""}</span>
            </div>
            {!currentTrace.order.length && <p className="text-xs text-muted">No provenance links are recorded for this node.</p>}
            <ol className="max-h-60 space-y-1 overflow-y-auto">{currentTrace.order.map((id, index) => {
              const node = currentTrace.chain?.nodes.find((item) => item.id === id);
              return <li key={id}><button onClick={() => setTraversal((previous) => previous ? { ...previous, count: index + 1, playing: false } : previous)}
                aria-current={index === traceCount - 1 ? "step" : undefined} className={cn("flex w-full items-start gap-3 rounded-lg border border-transparent p-2 text-left text-xs", index < traceCount ? "bg-emerald-500/5 text-emerald-100" : "text-muted", index === traceCount - 1 && "border-emerald-400/30")}>
                <span className="pt-0.5 font-mono text-[10px] text-muted">{String(index + 1).padStart(2, "0")}</span><span className="min-w-0"><span className="block text-[10px] text-muted">{node?.label ?? "Node"}{typeof node?.props.period_id === "string" ? ` · ${node.props.period_id}` : ""}</span><span className="mt-0.5 block break-words">{node ? nodeName(node) : id}</span></span>
              </button></li>;
            })}</ol>
          </Card>}

          {graph.nodes.length > 0 && <details className="rounded-xl border border-border bg-surface p-4"><summary className="cursor-pointer text-xs text-muted">Node directory · keyboard-accessible graph navigation</summary>
            <Input aria-label="Search visible graph nodes" className="my-3" value={nodeSearch} onChange={(event) => setNodeSearch(event.target.value)} placeholder="Search visible nodes…" />
            <ul className="grid max-h-72 gap-1 overflow-y-auto sm:grid-cols-2">{directory.map(({ data }) => <li key={data.id}><button aria-pressed={selectedId === data.id} className={cn("w-full rounded border border-border p-2 text-left text-xs hover:bg-surface-2", selectedId === data.id && "border-emerald-400/40")} onClick={() => selectNode(data.id)}><span className="text-[10px] text-muted">{data.label}</span><span className="mt-1 block break-words">{graphCaption(data)}</span><span className="mt-1 block break-all font-mono text-[9px] text-subtle">{data.id}</span></button></li>)}</ul>
            {!directory.length && <p className="text-xs text-muted">No visible nodes match this search.</p>}
          </details>}
        </div>
        {selectedId ? <NodePanel key={selectedId} id={selectedId} rules={state.rules} onSelect={selectNode} onClose={() => { setSelection({ scope: viewKey, id: null }); setTraversal(null); }} onTrace={trace} tracing={!!currentTrace?.loading} revision={revision} /> : null}
      </div>
      <RuleLibrary rules={state.rules} loading={state.loading} error={state.ruleError} />
    </div>
  );
}

function LabelFilter({ label, checked, disabled, onChange }: { label: string; checked: boolean; disabled: boolean; onChange: () => void }) {
  return <label className={cn("inline-flex items-center gap-1.5 rounded-md border px-2 py-1.5 text-[10px] focus-within:ring-2 focus-within:ring-ring", checked ? "border-border-strong bg-surface-2 text-foreground" : "border-border text-muted", disabled && "opacity-60")}>
    <input type="checkbox" checked={checked} disabled={disabled} onChange={onChange} className="sr-only" />
    <span className="size-2 rounded-full" style={{ background: NODE_COLORS[label] ?? "#a1a1aa", opacity: checked ? 1 : 0.35 }} /><span>{label}</span><span className="sr-only">{checked ? "shown" : "hidden"}</span>
  </label>;
}
