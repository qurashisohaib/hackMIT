"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Activity, Pause, Play, Radio, RefreshCw, RotateCcw, ShieldCheck, SkipForward } from "lucide-react";
import { useHealth } from "@/components/layout/health-provider";
import { AgentTimeline } from "@/components/trace/agent-timeline";
import { EventEvidence, EventRow } from "@/components/trace/event-evidence";
import { auditThreads, mergeEvents } from "@/components/trace/trace-data";
import { AgentChip } from "@/components/shared/agent-chip";
import { EmptyState } from "@/components/shared/empty-state";
import { PageHeader } from "@/components/shared/page-header";
import { StatusPill } from "@/components/shared/status-pill";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { errorMessage, getRunTrace, getRuns } from "@/lib/api";
import { AGENT_ORDER, getAgent } from "@/lib/agents";
import { applyEventToLiveState, EMPTY_LIVE_STATE, useRunEvents } from "@/lib/sse";
import type { AgentEvent, RunView } from "@/lib/types";
import { formatDateTime, formatNumber, humanize } from "@/lib/utils";

export function TraceWorkspace() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const { online } = useHealth();
  const [tick, setTick] = useState(0);
  const [state, setState] = useState<{ runs: RunView[] | null; error: string | null }>({ runs: null, error: null });
  const requestedId = params.get("run") ?? params.get("run_id");

  useEffect(() => {
    const controller = new AbortController();
    const load = () => getRuns(controller.signal).then((runs) => {
      if (!controller.signal.aborted) setState({ runs: [...runs].sort((a, b) => (b.started_at ?? "").localeCompare(a.started_at ?? "")), error: null });
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setState((previous) => ({ ...previous, error: errorMessage(error) }));
    });
    void load();
    const timer = window.setInterval(() => { if (document.visibilityState === "visible") void load(); }, 5000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [online, tick]);

  const run = state.runs?.find((item) => item.run_id === requestedId)
    ?? (!requestedId ? state.runs?.find((item) => ["running", "queued"].includes(item.status)) ?? state.runs?.[0] : undefined);
  const selectRun = (id: string) => {
    const query = new URLSearchParams(params.toString());
    query.set("run", id);
    query.delete("run_id");
    router.replace(`${pathname}?${query}`, { scroll: false });
  };
  return (
    <div className="space-y-6">
      <PageHeader eyebrow="Agent observability" title="Every decision leaves a trace" description="Follow the agents live, replay a completed close, and inspect the evidence behind each decision." actions={
        <>
          <Select aria-label="Select run" value={run?.run_id ?? requestedId ?? ""} onChange={(event) => selectRun(event.target.value)} wrapperClassName="sm:w-80" disabled={!state.runs?.length}>
            {!run ? <option value={requestedId ?? ""}>{requestedId ? "Requested run unavailable" : "Select a run"}</option> : null}
            {state.runs?.map((item) => <option key={item.run_id} value={item.run_id}>{item.period_id} · {humanize(item.status)} · {formatDateTime(item.started_at)} · {item.run_id}</option>)}
          </Select>
          <Button variant="outline" size="icon" aria-label="Refresh runs" onClick={() => setTick((value) => value + 1)}><RefreshCw /></Button>
        </>
      } />
      {state.error ? <div role="alert" className="rounded-lg border border-amber-400/25 bg-amber-400/5 px-4 py-3 text-sm text-amber-200">{state.error}. {state.runs ? "Showing the last loaded run list." : "Start the backend, then retry."}</div> : null}
      {!state.runs && !state.error ? <Skeleton className="h-96" aria-label="Loading runs" /> : run ? (
        <RunTrace key={run.run_id} run={run} refreshKey={tick} />
      ) : (
        <EmptyState icon={Activity} title={requestedId ? "Run not found" : "No agent runs yet"} description={requestedId ? "This run may have been removed when the demo was reset. Select another run or open the dashboard." : "Start a period close on the dashboard. Its plan, tools, hypotheses, and audit conversation will appear here."} action={<Link href="/" className={buttonVariants({ variant: "secondary", size: "sm" })}>Open dashboard</Link>} />
      )}
    </div>
  );
}

function RunTrace({ run, refreshKey }: { run: RunView; refreshKey: number }) {
  const { online } = useHealth();
  const [streamRun] = useState(["running", "queued"].includes(run.status) ? run.run_id : null);
  const live = useRunEvents(streamRun);
  const [snapshot, setSnapshot] = useState<{ events: AgentEvent[] | null; error: string | null }>({ events: null, error: null });
  const [cursor, setCursor] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [agent, setAgent] = useState("all");
  const [kind, setKind] = useState("all");
  const [query, setQuery] = useState("");
  const [limit, setLimit] = useState(100);
  const [auditLimit, setAuditLimit] = useState(10);
  const [tab, setTab] = useState<"events" | "audit">("events");
  const active = ["running", "queued"].includes(run.status);

  useEffect(() => {
    const controller = new AbortController();
    const load = () => getRunTrace(run.run_id, controller.signal).then((events) => {
      if (!controller.signal.aborted) setSnapshot({ events, error: null });
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setSnapshot((previous) => ({ ...previous, error: errorMessage(error) }));
    });
    void load();
    const timer = active ? window.setInterval(() => { if (document.visibilityState === "visible") void load(); }, 6000) : null;
    return () => { controller.abort(); if (timer) window.clearInterval(timer); };
  }, [run.run_id, active, online, refreshKey, live.runStatus]);

  const events = useMemo(() => mergeEvents(snapshot.events ?? [], live.events), [snapshot.events, live.events]);
  const visibleCount = cursor === null ? events.length : Math.min(cursor, events.length);
  const visible = useMemo(() => events.slice(0, visibleCount), [events, visibleCount]);
  const derived = useMemo(() => visible.reduce(applyEventToLiveState, EMPTY_LIVE_STATE), [visible]);
  const filtered = useMemo(() => visible.filter((event) => {
    const matchAgent = agent === "all" || getAgent(event.agent).id === agent;
    const matchKind = kind === "all" || event.kind === kind || event.kind.startsWith(`${kind}.`);
    const matchQuery = !query.trim() || `${event.title} ${event.detail} ${JSON.stringify(event.data)}`.toLowerCase().includes(query.trim().toLowerCase());
    return matchAgent && matchKind && matchQuery;
  }), [visible, agent, kind, query]);
  const threads = useMemo(() => auditThreads(visible), [visible]);

  useEffect(() => {
    if (!playing || cursor === null || cursor >= events.length) return;
    const timer = window.setInterval(() => setCursor((value) => Math.min((value ?? 0) + 1, events.length)), 500 / speed);
    return () => window.clearInterval(timer);
  }, [playing, cursor, events.length, speed]);

  const replay = () => { setCursor(0); setPlaying(true); };
  const isPlaying = playing && cursor !== null && cursor < events.length;
  const status = cursor === null ? (active ? live.runStatus ?? run.status : run.status) : derived.runStatus ?? "queued";
  const displayedRows = cursor === null ? filtered.slice(-limit) : filtered.slice(0, limit);
  return (
    <div className="space-y-5">
      <Card className="overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border bg-grid px-5 py-4">
          <div className="flex flex-wrap items-center gap-3">
            <StatusPill status={status} kind="run" />
            <span className="font-mono text-xs text-muted">{run.period_id}</span>
            <span className="break-all font-mono text-[10px] text-subtle">{run.run_id}</span>
          </div>
          <span className="inline-flex items-center gap-2 font-mono text-[10px] uppercase text-emerald-300"><Radio className="size-3" aria-hidden />{cursor !== null ? "Persisted replay" : active ? `Live · ${live.status}` : "Persisted trace"}</span>
        </div>
        <div className="flex flex-wrap gap-x-8 gap-y-3 px-5 py-4">
          {[
            ["Events", visibleCount],
            ["Precedent hits", derived.precedentHits],
            ["Exceptions raised", derived.exceptionsRaised],
            ["Audit challenges", visible.filter((event) => event.kind === "audit.challenge").length],
          ].map(([label, value]) => <div key={label}><p className="font-mono text-[10px] text-muted uppercase">{label}</p><p className="mt-1 font-mono text-xl">{formatNumber(Number(value))}</p></div>)}
          <div className="min-w-40 flex-1"><p className="font-mono text-[10px] text-muted uppercase">Current step</p><p className="mt-1 text-sm">{derived.currentStep ?? (cursor === null ? run.current_step : null) ?? "Awaiting events"}</p></div>
        </div>
        <div className="flex flex-wrap items-center gap-2 border-t border-border px-4 py-3">
          <Button size="sm" variant="secondary" onClick={replay} disabled={!events.length}><RotateCcw />Replay</Button>
          <Button size="sm" variant="outline" disabled={!events.length} onClick={() => {
            if (cursor === null || cursor >= events.length) replay();
            else setPlaying(!isPlaying);
          }}>{isPlaying ? <Pause /> : <Play />}{isPlaying ? "Pause" : "Play"}</Button>
          <Button size="icon-sm" variant="ghost" aria-label="Step one event" disabled={!events.length || (cursor !== null && cursor >= events.length)} onClick={() => { setPlaying(false); setCursor(Math.min((cursor ?? 0) + 1, events.length)); }}><SkipForward /></Button>
          <Select aria-label="Replay speed" value={speed} onChange={(event) => setSpeed(Number(event.target.value))} wrapperClassName="w-20">
            {[1, 2, 5, 10].map((value) => <option key={value} value={value}>{value}×</option>)}
          </Select>
          <input aria-label="Replay position" type="range" min={0} max={events.length} value={visibleCount} disabled={!events.length} onChange={(event) => { setPlaying(false); setCursor(Number(event.target.value)); }} className="min-w-24 flex-1 accent-emerald-400" />
          <span className="font-mono text-[10px] text-muted">{visibleCount}/{events.length}</span>
          <Button variant="ghost" size="sm" onClick={() => { setCursor(null); setPlaying(false); }}>{active ? "Follow live" : "Show all"}</Button>
        </div>
      </Card>
      {snapshot.error || (active && live.status === "reconnecting") ? <p role="alert" className="rounded-lg border border-amber-400/25 px-4 py-3 text-sm text-amber-200">{snapshot.error ?? "Live stream reconnecting"}. Available events remain visible; the trace retries while the run is active.</p> : null}
      {run.error || derived.error ? <p role="alert" className="rounded-lg border border-rose-400/25 px-4 py-3 text-sm text-rose-200">{derived.error ?? run.error}</p> : null}
      {!snapshot.events && !events.length && !snapshot.error ? <Skeleton className="h-80" aria-label="Loading trace" /> : !events.length ? (
        <EmptyState icon={Activity} title="No trace events available" description={active ? "Waiting for this run to publish its first event." : "This run has no persisted events. Start a new close to capture a trace."} />
      ) : (
        <>
          <AgentTimeline events={visible} selected={agent} onSelect={(value) => { setAgent(value); setLimit(100); setTab("events"); }} />
          <div className="flex flex-wrap gap-2" role="group" aria-label="Trace view">
            <Button variant={tab === "events" ? "secondary" : "ghost"} aria-pressed={tab === "events"} onClick={() => setTab("events")}><Activity />Event log</Button>
            <Button variant={tab === "audit" ? "secondary" : "ghost"} aria-pressed={tab === "audit"} onClick={() => setTab("audit")}><ShieldCheck />Audit conversation <span className="font-mono text-xs text-muted">{threads.length}</span></Button>
          </div>
          {tab === "events" ? (
            <Card>
              <CardHeader>
                <CardTitle>Reasoning &amp; tool log</CardTitle>
                <CardDescription>Expand a step for tool arguments, tested hypotheses, and the supporting or contradicting evidence.</CardDescription>
              </CardHeader>
              <div className="flex flex-wrap gap-2 border-b border-border px-4 pb-4">
                <Input aria-label="Search trace events" placeholder="Search an entity, rule, tool, or explanation…" value={query} onChange={(event) => { setQuery(event.target.value); setLimit(100); }} className="min-w-48 flex-1" />
                <Select aria-label="Filter agent" value={agent} onChange={(event) => { setAgent(event.target.value); setLimit(100); }} wrapperClassName="w-36">
                  <option value="all">All agents</option>{AGENT_ORDER.map((id) => <option key={id} value={id}>{getAgent(id).name}</option>)}
                </Select>
                <Select aria-label="Filter event type" value={kind} onChange={(event) => { setKind(event.target.value); setLimit(100); }} wrapperClassName="w-40">
                  <option value="all">All event types</option>{["run", "agent", "tool", "hypothesis", "decision", "exception", "memory", "audit", "human", "handoff", "propagation", "forecast", "report", "close", "brain"].map((value) => <option key={value} value={value}>{humanize(value)}</option>)}
                </Select>
              </div>
              {filtered.length ? <div>{displayedRows.map((event) => <EventRow key={event.id} event={event} />)}</div> : <EmptyState compact title={visibleCount ? "No matching events" : "Replay ready"} description={visibleCount ? "Try another agent, event type, or search." : "Press Play or step forward to reveal the recorded decisions."} />}
              <div className="flex items-center justify-between gap-3 border-t border-border px-4 py-3 text-xs text-muted">
                <span>{displayedRows.length} of {filtered.length} matching events{cursor === null && filtered.length > limit ? " · showing most recent" : ""}</span>
                {filtered.length > limit ? <Button size="sm" variant="ghost" onClick={() => setLimit((value) => value + 100)}>Load 100 more</Button> : null}
              </div>
            </Card>
          ) : threads.length ? (
            <div className="space-y-4">
              {threads.slice(0, auditLimit).map((thread) => (
                <Card key={thread.id}>
                  <CardHeader><CardTitle className="break-all font-mono text-xs">Review · {thread.id}</CardTitle></CardHeader>
                  <ol className="space-y-4 px-5 pb-5">
                    {thread.events.map((event) => (
                      <li key={event.id} className="border-l-2 border-amber-400/30 pl-4">
                        <div className="mb-2 flex flex-wrap items-center gap-2"><AgentChip agent={event.agent} size="xs" /><span className="font-mono text-[10px] text-amber-300">{humanize(event.kind.replace("audit.", ""))}</span><span className="font-mono text-[10px] text-subtle">#{event.seq}</span></div>
                        <p className="mb-2 text-sm font-medium">{event.title}</p>
                        <EventEvidence event={event} />
                      </li>
                    ))}
                  </ol>
                </Card>
              ))}
              {threads.length > auditLimit ? <Button variant="secondary" onClick={() => setAuditLimit((value) => value + 10)}>Load more audit conversations</Button> : null}
            </div>
          ) : <EmptyState icon={ShieldCheck} title="No audit conversation at this point" description="Challenges, independent responses, and verdicts appear when the audit agent reviews a decision. Advance the replay to see later events." />}
        </>
      )}
    </div>
  );
}
