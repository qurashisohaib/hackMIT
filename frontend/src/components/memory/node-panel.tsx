"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowUpRight, GitBranch, RefreshCw, X } from "lucide-react";
import { NODE_COLORS, nodeName } from "@/components/memory/graph-data";
import { useHealth } from "@/components/layout/health-provider";
import { StatusPill } from "@/components/shared";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { errorMessage, getMemoryNode } from "@/lib/api";
import type { NodeDetail, RuleView } from "@/lib/types";
import { formatPercent, humanize } from "@/lib/utils";

function propertyText(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

export function NodePanel({ id, rules, onSelect, onClose, onTrace, tracing, revision }: {
  id: string; rules: RuleView[]; onSelect: (id: string) => void; onClose: () => void; onTrace: (id: string) => void; tracing: boolean; revision: number;
}) {
  const { online } = useHealth();
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<{ detail: NodeDetail | null; loading: boolean; error: string | null }>({ detail: null, loading: true, error: null });
  useEffect(() => {
    const controller = new AbortController();
    getMemoryNode(id, controller.signal).then(
      (detail) => { if (!controller.signal.aborted) setState({ detail, loading: false, error: null }); },
      (error: unknown) => { if (!controller.signal.aborted) setState({ detail: null, loading: false, error: errorMessage(error) }); },
    );
    return () => controller.abort();
  }, [id, attempt, revision, online]);
  const detail = state.detail;
  const rule = rules.find((item) => item.id === id);
  const exceptionId = detail?.node.label === "Exception" ? id : typeof detail?.node.props.exception_id === "string" ? detail.node.props.exception_id : null;
  const properties = detail ? Object.entries(detail.node.props).filter(([key]) => key !== "embedding") : [];

  return (
    <Card className="min-w-0 overflow-hidden" aria-label="Selected memory node">
      <div className="flex items-center justify-between border-b border-border p-4"><h2 className="text-sm font-medium">Node inspector</h2><Button size="icon-xs" variant="ghost" onClick={onClose} aria-label="Close node inspector"><X aria-hidden /></Button></div>
      {state.loading && <p role="status" className="p-4 text-sm text-muted">Loading node evidence…</p>}
      {state.error && <div role="alert" className="space-y-3 p-4"><p className="text-sm text-amber-200">{state.error}. This node may no longer exist after a demo reset.</p><Button variant="secondary" size="sm" onClick={() => setAttempt((value) => value + 1)}><RefreshCw aria-hidden /> Retry</Button></div>}
      {detail && <div className="space-y-5 p-4">
        <section className="space-y-3">
          <span className="inline-flex items-center gap-2 font-mono text-[10px] uppercase tracking-wider"><span className="size-2 rounded-full" style={{ background: NODE_COLORS[detail.node.label] ?? "#a1a1aa" }} />{detail.node.label}</span>
          <h3 className="break-words text-base font-semibold leading-relaxed">{nodeName(detail.node)}</h3>
          <p className="break-all font-mono text-[10px] text-muted">{id}</p>
          {typeof detail.node.props.status === "string" && <StatusPill status={detail.node.props.status} />}
          <Button className="w-full" onClick={() => onTrace(id)} loading={tracing}><GitBranch aria-hidden />Trace provenance</Button>
          <div className="flex flex-wrap gap-2">
            <Link href={`/memory?focus=${encodeURIComponent(id)}`} className={buttonVariants({ variant: "outline", size: "xs" })}>Focus / share node <ArrowUpRight aria-hidden /></Link>
            {exceptionId && <Link href={`/exceptions?id=${encodeURIComponent(exceptionId)}`} className={buttonVariants({ variant: "outline", size: "xs" })}>Open exception <ArrowUpRight aria-hidden /></Link>}
          </div>
        </section>
        {rule && <section className="space-y-3 rounded-lg border border-emerald-400/20 bg-emerald-500/5 p-3">
          <div className="flex items-center justify-between"><h3 className="text-xs font-medium">Rule version {rule.version}</h3><span className="font-mono text-xs text-emerald-200">{formatPercent(rule.trust_score)} trust</span></div>
          <Progress value={rule.trust_score * 100} label="Rule trust score" />
          <p className="text-xs text-muted">{rule.trust.times_applied} applications · {rule.trust.times_confirmed} confirmations · {rule.trust.times_refuted} refutations</p>
          <p className="text-xs text-muted">{rule.trust.human_verified ? "Human verified" : "Not human verified"} · Learned {rule.learned_in_period ?? "in an unrecorded period"}</p>
          {rule.supersedes && <button className="text-xs text-emerald-300 hover:underline" onClick={() => onSelect(rule.supersedes!)}>Inspect superseded version →</button>}
          {rule.source_id && <button className="block text-xs text-emerald-300 hover:underline" onClick={() => onSelect(rule.source_id!)}>Inspect learning source →</button>}
        </section>}
        <section className="space-y-2">
          <h3 className="text-xs font-semibold">Connected evidence <span className="font-mono text-muted">({detail.neighbors.length})</span></h3>
          {!detail.neighbors.length && <p className="text-xs text-muted">No neighboring nodes recorded.</p>}
          <ul className="max-h-72 space-y-1 overflow-y-auto">{detail.neighbors.map((neighbor) => <li key={neighbor.id}><button className="w-full rounded-lg border border-border p-2.5 text-left hover:bg-surface-2" onClick={() => onSelect(neighbor.id)}>
            <span className="block text-[10px] text-muted">{neighbor.label}</span><span className="mt-1 block break-words text-xs">{nodeName(neighbor)}</span>
            <span className="mt-1 block break-all font-mono text-[9px] text-subtle">{detail.edges.filter((edge) => edge.src === neighbor.id || edge.dst === neighbor.id).map((edge) => `${edge.src === id ? "→" : "←"} ${edge.rel}`).join(" · ")}</span>
          </button></li>)}</ul>
        </section>
        <details className="rounded-lg border border-border p-3"><summary className="cursor-pointer text-xs text-muted">Node properties</summary><dl className="mt-3 space-y-3">{properties.map(([key, value]) => <div key={key}><dt className="text-[10px] font-medium text-muted">{humanize(key)}</dt><dd className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-all font-mono text-[10px]">{propertyText(value)}</dd></div>)}</dl></details>
      </div>}
    </Card>
  );
}
