"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ArrowRight, CheckCheck, GitBranch, ListFilter, RefreshCw, Search, ShieldCheck, X } from "lucide-react";
import { ExceptionPanel } from "@/components/exceptions/exception-panel";
import { useHealth } from "@/components/layout/health-provider";
import { ConfidenceBadge, EmptyState, Money, PageHeader, StatusPill } from "@/components/shared";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input, inputClassName } from "@/components/ui/input";
import { errorMessage, getExceptions, getPeriods } from "@/lib/api";
import type { ExceptionView, PeriodView, ResolveResponse } from "@/lib/types";
import { cn, humanize, periodMonth } from "@/lib/utils";

const STATUSES = ["needs_human", "open", "pending_audit", "resolved", "dismissed"];

export function ExceptionWorkspace() {
  const params = useSearchParams();
  const router = useRouter();
  const { online } = useHealth();
  const period = params.get("period_id") ?? "";
  const status = params.get("status") ?? "";
  const selectedId = params.get("id");
  const [search, setSearch] = useState("");
  const [revision, setRevision] = useState(0);
  const [result, setResult] = useState<ResolveResponse | null>(null);
  const [state, setState] = useState<{
    items: ExceptionView[]; periods: PeriodView[]; key: string; loading: boolean; error: string | null;
  }>({ items: [], periods: [], key: "", loading: true, error: null });
  const filterKey = `${period}|${status}`;

  useEffect(() => {
    const controller = new AbortController();
    let pending = false;
    async function load() {
      if (pending) return;
      pending = true;
      const [items, periods] = await Promise.allSettled([
        getExceptions({ period_id: period, status }, controller.signal),
        getPeriods(controller.signal),
      ]);
      pending = false;
      if (controller.signal.aborted) return;
      setState((previous) => ({
        items: items.status === "fulfilled" ? items.value : previous.key === filterKey ? previous.items : [],
        periods: periods.status === "fulfilled" ? periods.value : previous.periods,
        key: filterKey,
        loading: false,
        error: items.status === "rejected" ? errorMessage(items.reason) : null,
      }));
    }
    void load();
    const interval = window.setInterval(() => {
      if (document.visibilityState === "visible") void load();
    }, 8000);
    return () => { controller.abort(); window.clearInterval(interval); };
  }, [period, status, filterKey, revision, online]);

  function href(updates: Record<string, string | null>) {
    const next = new URLSearchParams(params.toString());
    for (const [key, value] of Object.entries(updates)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    return `/exceptions${next.size ? `?${next}` : ""}`;
  }

  const loading = state.loading || state.key !== filterKey;
  const items = state.key === filterKey ? state.items : [];
  const query = search.trim().toLowerCase();
  const visible = items.filter((item) =>
    [item.id, item.title, item.counterparty_name, item.entity_id, item.category].some((value) => value?.toLowerCase().includes(query)),
  );
  const periodOptions = [...new Map([
    ...state.periods.map((p) => [p.id, p.name] as const),
    ...items.map((item) => [item.period_id, periodMonth(item.period_id)] as const),
    ...(period ? [[period, periodMonth(period)] as const] : []),
  ]).entries()].sort(([a], [b]) => a.localeCompare(b));

  return (
    <div className="space-y-6">
      <PageHeader eyebrow="Human judgment · lasting memory" title="Exception desk"
        description="Investigate the evidence. Teach the pattern. Let the next close remember."
        actions={<Button variant="secondary" size="sm" onClick={() => setRevision((value) => value + 1)}><RefreshCw aria-hidden /> Refresh</Button>} />

      <div className="grid gap-3 sm:grid-cols-3">
        {[
          { title: "Awaiting judgment", value: items.filter((i) => i.status === "needs_human" || i.status === "open").length, icon: ListFilter, tone: "text-amber-300" },
          { title: "Under audit", value: items.filter((i) => i.status === "pending_audit").length, icon: ShieldCheck, tone: "text-sky-300" },
          { title: "Resolved", value: items.filter((i) => i.status === "resolved").length, icon: CheckCheck, tone: "text-emerald-300" },
        ].map(({ title, value, icon: Icon, tone }) => (
          <Card key={title} className="flex items-center gap-4 px-5 py-4">
            <Icon className={cn("size-5", tone)} aria-hidden />
            <div><p className="text-xs text-muted">{title}</p><p className="mt-1 font-mono text-2xl">{loading || state.error && !items.length ? "—" : value}</p></div>
            <span className="ml-auto text-[10px] text-subtle">in this view</span>
          </Card>
        ))}
      </div>

      {result && <section role="status" className="relative overflow-hidden rounded-xl border border-emerald-400/30 bg-emerald-500/10 p-5 motion-safe:animate-fade-up">
        <Button variant="ghost" size="icon-xs" className="absolute right-3 top-3" aria-label="Dismiss resolution confirmation" onClick={() => setResult(null)}><X aria-hidden /></Button>
        <div className="flex gap-4 pr-8">
          <GitBranch className="mt-1 size-6 shrink-0 text-emerald-300" aria-hidden />
          <div className="min-w-0 space-y-2">
            <h2 className="font-semibold text-emerald-100">{result.rule_id ? "One correction. A smarter close." : "Review recorded."}</h2>
            <p className="text-sm text-emerald-100/80">{result.message || `Exception ${humanize(result.status).toLowerCase()}.`}
              {" "}{result.propagated.length > 0 ? `${result.propagated.length} other exception${result.propagated.length === 1 ? "" : "s"} resolved automatically.` : "No sibling exceptions were auto-resolved."}</p>
            <div className="flex flex-wrap gap-2">
              {result.rule_id && <Link className={buttonVariants({ size: "sm" })} href={`/memory?focus=${encodeURIComponent(result.rule_id)}`}>Explore learned rule <ArrowRight aria-hidden /></Link>}
              {result.propagated.map((id) => <Link key={id} className="break-all rounded border border-emerald-400/20 px-2 py-1 font-mono text-xs text-emerald-200 hover:bg-emerald-400/10" href={`/exceptions?id=${encodeURIComponent(id)}`}>{id}</Link>)}
            </div>
          </div>
        </div>
      </section>}

      <div className="flex flex-col gap-3 rounded-xl border border-border bg-surface p-3 lg:flex-row lg:items-end">
        <label className="min-w-0 flex-1 space-y-1.5 text-xs text-muted">
          <span>Search exceptions</span><span className="relative block"><Search className="absolute left-3 top-2.5 size-4" aria-hidden />
            <Input className="pl-9" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Counterparty, reference, or category" />
          </span>
        </label>
        <label className="space-y-1.5 text-xs text-muted lg:w-44"><span>Period</span>
          <select className={inputClassName} value={period} onChange={(event) => router.replace(href({ period_id: event.target.value }), { scroll: false })}>
            <option value="">All periods</option>{periodOptions.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
          </select>
        </label>
        <label className="space-y-1.5 text-xs text-muted lg:w-44"><span>Status</span>
          <select className={inputClassName} value={status} onChange={(event) => router.replace(href({ status: event.target.value }), { scroll: false })}>
            <option value="">All statuses</option>{[...new Set([...STATUSES, ...(status ? [status] : [])])].map((value) => <option key={value} value={value}>{humanize(value)}</option>)}
          </select>
        </label>
      </div>

      {state.error && <div role="alert" className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-100">
        {state.error}. {items.length ? "Showing the last available queue." : "The queue will reconnect automatically when the backend is available."}
      </div>}

      <div className={cn("grid items-start gap-5", selectedId && "xl:grid-cols-[minmax(300px,0.85fr)_minmax(0,1.4fr)]")}>
        <Card className="overflow-hidden">
          <div className="flex items-center justify-between border-b border-border px-4 py-3">
            <h2 className="text-sm font-medium">Review queue</h2><span className="font-mono text-xs text-muted">{loading ? "Loading…" : `${visible.length} items`}</span>
          </div>
          {loading ? <div className="p-5 text-sm text-muted" role="status">Loading exceptions…</div> : visible.length ? (
            <ul className="max-h-[850px] divide-y divide-border overflow-y-auto">
              {visible.map((item) => <li key={item.id}>
                <Link href={href({ id: item.id })} scroll={false} aria-current={item.id === selectedId ? "true" : undefined}
                  className={cn("group block border-l-2 border-transparent p-4 transition-colors hover:bg-surface-2", item.id === selectedId && "border-l-emerald-400 bg-emerald-500/5")}>
                  <div className="flex items-center justify-between gap-3"><span className="truncate text-sm font-medium">{item.counterparty_name || item.counterparty_id || humanize(item.entity_type)}</span><Money value={item.amount} className="text-sm" /></div>
                  <p className="mt-1 text-sm text-muted">{item.title}</p>
                  <div className="mt-3 flex flex-wrap items-center gap-2"><StatusPill status={item.status} size="sm" /><ConfidenceBadge confidence={item.confidence} size="sm" />
                    <span className="ml-auto font-mono text-[10px] text-subtle">{item.period_id}</span></div>
                  <p className="mt-2 break-all font-mono text-[10px] text-subtle">{item.entity_id}</p>
                </Link>
              </li>)}
            </ul>
          ) : <div className="p-4"><EmptyState title={state.error ? "Queue unavailable" : query || status || period ? "No matching exceptions" : "A clear desk"}
            description={state.error ? "Start the backend, then refresh. No sample financial data is shown." : query || status || period ? "Try another period, status, or search term." : "Run a period close from the dashboard to surface items that need review."}
            action={<Link href="/" className={buttonVariants({ variant: "secondary", size: "sm" })}>Open close dashboard <ArrowRight aria-hidden /></Link>} /></div>}
        </Card>
        {selectedId && <ExceptionPanel key={selectedId} id={selectedId} revision={revision} onClose={() => router.replace(href({ id: null }), { scroll: false })}
          onResolved={(response) => { setResult(response); setRevision((value) => value + 1); }} />}
      </div>
    </div>
  );
}
