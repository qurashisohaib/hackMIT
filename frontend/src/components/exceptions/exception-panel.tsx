"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowUpRight, CheckCircle2, CircleHelp, RefreshCw, X, XCircle } from "lucide-react";
import { ResolveForm } from "@/components/exceptions/resolve-form";
import { useHealth } from "@/components/layout/health-provider";
import { AgentChip, ConfidenceBadge, EmptyState, Money, StatusPill, TierBadge } from "@/components/shared";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { errorMessage, getException } from "@/lib/api";
import type { ExceptionDetail, ResolveResponse } from "@/lib/types";
import { formatDateTime, humanize } from "@/lib/utils";

export function ExceptionPanel({ id, revision, onClose, onResolved }: {
  id: string; revision: number; onClose: () => void; onResolved: (result: ResolveResponse) => void;
}) {
  const { online } = useHealth();
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<{ detail: ExceptionDetail | null; error: string | null; loading: boolean }>({ detail: null, error: null, loading: true });

  useEffect(() => {
    const controller = new AbortController();
    let pending = false;
    async function load() {
      if (pending) return;
      pending = true;
      try {
        const detail = await getException(id, controller.signal);
        if (!controller.signal.aborted) setState({ detail, error: null, loading: false });
      } catch (error) {
        if (!controller.signal.aborted) setState((previous) => ({ ...previous, error: errorMessage(error), loading: false }));
      } finally { pending = false; }
    }
    void load();
    const interval = window.setInterval(() => { if (document.visibilityState === "visible") void load(); }, 8000);
    return () => { controller.abort(); window.clearInterval(interval); };
  }, [id, revision, attempt, online]);

  const detail = state.detail;
  const exception = detail?.exception;
  const editable = exception && ["open", "needs_human", "pending_audit"].includes(exception.status);

  return (
    <Card className="min-w-0 overflow-hidden" aria-label="Exception detail">
      <div className="flex items-center justify-between gap-2 border-b border-border px-5 py-3">
        <h2 className="min-w-0 break-all font-mono text-xs text-muted">{id}</h2>
        <Button variant="ghost" size="icon-sm" onClick={onClose} aria-label="Close exception detail"><X aria-hidden /></Button>
      </div>
      {state.loading ? <div role="status" className="p-6 text-sm text-muted">Loading the evidence trail…</div> : null}
      {state.error && <div role="alert" className="m-4 space-y-2 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-100">
        <p>{state.error}. {detail ? "This detail may be outdated; refresh before submitting a review." : "The exception could not be loaded. Check the reference or reconnect the backend."}</p>
        <Button size="sm" variant="secondary" onClick={() => setAttempt((value) => value + 1)}><RefreshCw aria-hidden /> Retry</Button>
      </div>}
      {detail && exception && <div className="space-y-6 p-5">
        <section className="space-y-3">
          <div className="flex flex-wrap items-center gap-2"><StatusPill status={exception.status} /><AgentChip agent={exception.agent} /><span className="ml-auto font-mono text-xs text-muted">{exception.period_id}</span></div>
          <h3 className="text-xl font-semibold tracking-tight">{exception.title}</h3>
          <p className="text-sm leading-relaxed text-muted">{exception.description}</p>
          <div className="flex flex-wrap items-center gap-3"><ConfidenceBadge confidence={exception.confidence} /><TierBadge tier={exception.tier} confidence={exception.confidence} showHint /><span className="text-xs text-muted">{exception.counterparty_name || exception.counterparty_id || "Counterparty not identified"}</span></div>
          <div className="grid grid-cols-3 gap-2 rounded-lg border border-border bg-surface-2 p-3">
            {[["Actual", exception.amount], ["Expected", exception.expected_amount], ["Difference", exception.difference]].map(([label, value]) => <div key={String(label)} className="min-w-0">
              <p className="mb-1 text-[10px] text-muted">{label}</p><Money value={typeof value === "number" ? value : null} className="text-xs sm:text-sm" />
            </div>)}
          </div>
          <p className="break-all font-mono text-[10px] text-subtle">{exception.entity_id} · {formatDateTime(exception.created_at)}</p>
          <Link href={`/memory?focus=${encodeURIComponent(exception.id)}`} className={buttonVariants({ variant: "outline", size: "sm" })}>View memory & provenance <ArrowUpRight aria-hidden /></Link>
        </section>

        <section className="space-y-3" aria-label="Hypotheses and evidence">
          <div className="flex items-center justify-between"><h3 className="text-sm font-semibold">Hypotheses & evidence</h3><span className="font-mono text-xs text-muted">{detail.hypotheses.length} considered</span></div>
          {!detail.hypotheses.length && <EmptyState compact icon={CircleHelp} title="No hypotheses recorded" description="Review the source evidence or provide a manual resolution." />}
          {[...detail.hypotheses].sort((a, b) => b.confidence - a.confidence).map((hypothesis, index) => <details key={hypothesis.id} open={index === 0} className="group rounded-lg border border-border-strong bg-surface-2">
            <summary className="cursor-pointer p-3 text-sm">
              <span className="ml-1 inline-flex max-w-[90%] items-center gap-2 align-middle"><span className="min-w-0 flex-1">{humanize(hypothesis.kind)}</span><ConfidenceBadge confidence={hypothesis.confidence} size="sm" /></span>
              <span className="mt-2 block text-xs leading-relaxed text-muted">{hypothesis.description}</span>
            </summary>
            <div className="space-y-3 border-t border-border p-3">
              <div className="flex flex-wrap gap-2 text-[10px] text-muted"><span>{hypothesis.tested ? hypothesis.passed ? "Test passed" : "Test did not pass" : "Not tested"}</span><span>· {humanize(hypothesis.generated_by)}</span><span>· {hypothesis.steps} steps</span></div>
              {hypothesis.candidate_ids.length > 0 && <p className="break-all font-mono text-[10px] text-muted">Candidates: {hypothesis.candidate_ids.join(", ")}</p>}
              <ul className="space-y-2">{hypothesis.evidence.map((evidence, evidenceIndex) => <li key={`${evidence.kind}-${evidenceIndex}`} className="flex gap-2 rounded-md bg-background/50 p-2.5">
                {evidence.supports ? <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-emerald-400" aria-hidden /> : <XCircle className="mt-0.5 size-3.5 shrink-0 text-rose-300" aria-hidden />}
                <div className="min-w-0"><p className="text-xs leading-relaxed">{evidence.description}</p><p className="mt-1 text-[10px] text-muted">{evidence.supports ? "Supports" : "Contradicts"} · {humanize(evidence.kind)} · weight {evidence.weight}</p></div>
              </li>)}</ul>
              {!hypothesis.evidence.length && <p className="text-xs text-muted">No evidence tests attached.</p>}
              {hypothesis.rule_id && <Link className="text-xs text-emerald-300 hover:underline" href={`/memory?focus=${encodeURIComponent(hypothesis.rule_id)}`}>Inspect supporting rule →</Link>}
              {Object.keys(hypothesis.params).length > 0 && <details><summary className="cursor-pointer text-xs text-muted">Calculation parameters</summary><pre className="mt-2 overflow-auto whitespace-pre-wrap break-all text-[10px] text-muted">{JSON.stringify(hypothesis.params, null, 2)}</pre></details>}
            </div>
          </details>)}
        </section>

        {detail.decision && <section className="space-y-3 rounded-lg border border-border p-4">
          <div className="flex flex-wrap items-center gap-2"><h3 className="mr-auto text-sm font-semibold">Recorded decision</h3><StatusPill status={detail.decision.status} /><ConfidenceBadge confidence={detail.decision.confidence} size="sm" /><TierBadge tier={detail.decision.tier} size="sm" /></div>
          <p className="text-sm text-muted">{detail.decision.explanation}</p>
          <p className="break-all font-mono text-xs text-subtle">{humanize(detail.decision.action)}{detail.decision.matched_ids.length ? ` · ${detail.decision.matched_ids.join(", ")}` : ""}</p>
          <Link href={`/memory?focus=${encodeURIComponent(detail.decision.id)}`} className="text-xs text-emerald-300 hover:underline">Trace decision provenance →</Link>
        </section>}

        <section className="space-y-3">
          <h3 className="text-sm font-semibold">Independent audit</h3>
          {!detail.audit.length && <p className="text-xs text-muted">No audit findings recorded for this exception.</p>}
          {detail.audit.map((finding) => <div key={finding.id} className="space-y-3 rounded-lg border border-sky-500/20 bg-sky-500/5 p-4">
            <StatusPill status={finding.verdict} />
            <dl className="space-y-2 text-xs"><dt className="font-medium text-sky-200">Challenge</dt><dd className="text-muted">{finding.challenge}</dd><dt className="font-medium text-sky-200">Response</dt><dd className="text-muted">{finding.response}</dd></dl>
            <p className="text-xs leading-relaxed">{finding.reasoning}</p>
            <ul className="space-y-1">{finding.checks.map((check, index) => <li key={`${check.name}-${index}`} className="text-xs text-muted">{check.passed ? "Passed" : "Failed"}: {check.name} — {check.detail}</li>)}</ul>
          </div>)}
        </section>

        {detail.rules_used.length > 0 && <section className="space-y-2"><h3 className="text-sm font-semibold">Rules used</h3>{detail.rules_used.map((rule) => <Link key={rule.id} href={`/memory?focus=${encodeURIComponent(rule.id)}`} className="block rounded-lg border border-border p-3 text-xs hover:bg-surface-2"><span className="text-emerald-300">{rule.description}</span><span className="mt-1 block text-muted">Version {rule.version} · {humanize(rule.status)} · {rule.trust.times_confirmed} confirmations</span></Link>)}</section>}

        {detail.corrections.length > 0 && <section className="space-y-3"><h3 className="text-sm font-semibold">Human review history</h3>{detail.corrections.map((correction) => <div key={correction.id} className="border-l-2 border-emerald-500/40 pl-3"><p className="text-xs">{correction.explanation}</p><p className="mt-1 text-[10px] text-muted">{humanize(correction.action)} · {correction.by} · {formatDateTime(correction.created_at)}</p></div>)}</section>}

        {Object.keys(detail.entity).length > 0 && <details className="rounded-lg border border-border p-3"><summary className="cursor-pointer text-xs text-muted">Source transaction / invoice</summary><pre className="mt-3 max-h-64 overflow-auto whitespace-pre-wrap break-all font-mono text-[10px] text-muted">{JSON.stringify(detail.entity, null, 2)}</pre></details>}
        {detail.related.length > 0 && <details className="rounded-lg border border-border p-3"><summary className="cursor-pointer text-xs text-muted">Related candidate evidence · {detail.related.length} records</summary>
          <ul className="mt-3 max-h-80 space-y-3 overflow-auto">{detail.related.map((row, index) => <li key={typeof row.id === "string" ? row.id : index} className="rounded border border-border bg-surface-2 p-3">
            <pre className="whitespace-pre-wrap break-all font-mono text-[10px] text-muted">{JSON.stringify(row, null, 2)}</pre>
          </li>)}</ul>
        </details>}
        {editable && !state.error && <ResolveForm detail={detail} onResolved={onResolved} />}
        {!editable && <p className="rounded-lg border border-border bg-surface-2 p-3 text-xs text-muted">This exception is {humanize(exception.status).toLowerCase()}. Its evidence and review history remain available above.</p>}
      </div>}
    </Card>
  );
}
