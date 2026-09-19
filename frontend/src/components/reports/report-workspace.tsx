"use client";

import Link from "next/link";
import { ArrowUpRight, Check, FileText, Printer, TriangleAlert } from "lucide-react";
import { PeriodControls } from "@/components/forecast/period-controls";
import { usePeriodResource, usePeriodSelection } from "@/components/forecast/use-period-resource";
import { EvidenceLinks } from "@/components/trace/event-evidence";
import { textValue } from "@/components/trace/trace-data";
import { EmptyState } from "@/components/shared/empty-state";
import { PageHeader } from "@/components/shared/page-header";
import { StatusPill } from "@/components/shared/status-pill";
import { Button, buttonVariants } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { getReport } from "@/lib/api";
import type { CloseReport, JsonObject, PeriodView } from "@/lib/types";
import { cn, formatDate, formatDateTime, formatMoney, formatNumber, formatPercent, humanize } from "@/lib/utils";
import styles from "./report.module.css";

export function ReportWorkspace() {
  const selection = usePeriodSelection();
  const resource = usePeriodResource(selection.periodId, getReport, selection.revision);
  const error = resource.error ?? selection.error;
  return (
    <div className={cn(styles.page, "space-y-6")}>
      <div className={styles.controls}>
        <PageHeader eyebrow="Controller reporting" title="The close, on the record" description="A reviewable close document with checklist results, unresolved items, and evidence from financial memory." actions={
          <>
            <PeriodControls periods={selection.periods} periodId={selection.periodId} onChange={selection.selectPeriod} onRefresh={selection.refresh} />
            <Button variant="secondary" onClick={() => window.print()} disabled={!resource.data || !!error}><Printer />Print report</Button>
          </>
        } />
      </div>
      {error ? <div role="alert" className={cn(styles.screenOnly, "rounded-lg border border-amber-400/25 bg-amber-400/5 px-4 py-3 text-sm text-amber-200")}>{error}. {resource.data ? "Showing a previously loaded report. Refresh successfully before printing." : "Report data is unavailable. Check the backend and retry."}</div> : null}
      {selection.loading || resource.loading ? (
        <div role="status" aria-label="Loading close report" className="mx-auto max-w-[980px] space-y-4"><Skeleton className="h-40" /><Skeleton className="h-96" /></div>
      ) : resource.data ? (
        <ReportDocument report={resource.data} period={selection.period} stale={!!error} />
      ) : (
        <EmptyState icon={FileText} title={error ? "Report unavailable" : !selection.periodId ? "No periods available" : "No close report yet"} description={error ? "No report content is substituted while the backend is unavailable." : "Run the selected period close to generate a report, checklist, and linked evidence."} action={<Link href="/" className={buttonVariants({ variant: "secondary", size: "sm" })}>Open dashboard</Link>} />
      )}
    </div>
  );
}

function Narrative({ text }: { text: string }) {
  return (
    <div className="space-y-3 text-sm leading-7 text-muted">
      {text.split(/\n\s*\n/).filter(Boolean).map((paragraph, index) => <p key={index} className="whitespace-pre-wrap break-words">{paragraph}</p>)}
    </div>
  );
}

function ReportDocument({ report, period, stale }: { report: CloseReport; period: PeriodView | null; stale: boolean }) {
  const done = report.checklist.filter((item) => item.status === "done").length;
  const flagged = report.checklist.filter((item) => item.status !== "done").length;
  return (
    <article className={cn(styles.document, "space-y-8 rounded-xl border border-border bg-surface p-5 sm:p-9")} aria-label={`Close report for ${period?.name ?? report.period_id}`}>
      <header className={cn(styles.keepTogether, "space-y-5 border-b border-border pb-7")}>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <span className="font-mono text-[10px] tracking-[0.2em] text-muted uppercase">AI Office of the CFO / Close report</span>
          <StatusPill status={report.status} kind="period" />
        </div>
        <div>
          <h2 className="text-2xl font-semibold tracking-tight sm:text-3xl">{period?.name ?? report.period_id}</h2>
          {period ? <p className="mt-2 text-sm text-muted">{formatDate(period.start_date)} – {formatDate(period.end_date)}</p> : null}
        </div>
        <dl className="flex flex-wrap gap-x-8 gap-y-3 text-xs">
          <div><dt className="font-mono text-[10px] text-subtle uppercase">Generated</dt><dd className="mt-1">{formatDateTime(report.generated_at)}</dd></div>
          <div><dt className="font-mono text-[10px] text-subtle uppercase">Period</dt><dd className="mt-1 font-mono">{report.period_id}</dd></div>
          <div className="min-w-0"><dt className="font-mono text-[10px] text-subtle uppercase">Run</dt><dd className="mt-1 break-all font-mono">{report.run_id ? <Link href={`/trace?run=${encodeURIComponent(report.run_id)}`} className="text-emerald-300 hover:underline">{report.run_id}</Link> : "Not linked"}</dd></div>
        </dl>
        {stale ? <p className="rounded border border-amber-400/30 p-3 text-sm text-amber-200">Previously loaded report. The backend could not confirm whether this document is current.</p> : null}
      </header>
      <section className={styles.section} aria-labelledby="report-summary">
        <h3 id="report-summary" className="mb-3 text-base font-semibold">Executive summary</h3>
        {report.summary ? <Narrative text={report.summary} /> : <p className="text-sm text-muted">No executive summary was supplied.</p>}
      </section>
      {report.metrics ? (
        <section className={cn(styles.section, styles.keepTogether)} aria-labelledby="report-metrics">
          <h3 id="report-metrics" className="mb-4 text-base font-semibold">Close metrics</h3>
          <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-3">
            {[
              ["Items evaluated", formatNumber(report.metrics.total_items)],
              ["Automatically resolved", formatNumber(report.metrics.auto_resolved)],
              ["Human reviews", formatNumber(report.metrics.human_reviews)],
              ["Precedent hits", formatNumber(report.metrics.precedent_hits)],
              ["Accuracy", formatPercent(report.metrics.accuracy, 1)],
              ["Coverage", formatPercent(report.metrics.coverage, 1)],
            ].map(([label, value]) => <div key={label} className="bg-surface-2 px-4 py-3"><dt className="font-mono text-[10px] text-muted uppercase">{label}</dt><dd className="mt-1 font-mono text-lg">{value}</dd></div>)}
          </dl>
          <p className="mt-2 text-[11px] leading-relaxed text-subtle">Metrics describe the recorded run. Accuracy covers decided items; coverage is the share of total items decided. A dash indicates an unavailable value.</p>
        </section>
      ) : null}
      <section className={styles.section} aria-labelledby="report-checklist">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-2"><h3 id="report-checklist" className="text-base font-semibold">Close checklist</h3><span className="font-mono text-[10px] text-muted">{done}/{report.checklist.length} done · {flagged} requiring attention</span></div>
        {report.checklist.length ? <ul className="divide-y divide-border rounded-lg border border-border">
          {report.checklist.map((item, index) => <li key={`${item.name}-${index}`} className="flex gap-3 p-4">
            {item.status === "done" ? <Check className="mt-0.5 size-4 shrink-0 text-emerald-300" aria-hidden /> : <TriangleAlert className="mt-0.5 size-4 shrink-0 text-amber-300" aria-hidden />}
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center justify-between gap-2"><h4 className="text-sm font-medium">{item.name}</h4><StatusPill status={item.status} kind="checklist" /></div>
              {item.detail ? <p className="mt-1 whitespace-pre-wrap break-words text-xs leading-relaxed text-muted">{item.detail}</p> : null}
              {item.count !== null ? <p className="mt-2 font-mono text-[10px] text-subtle">Count: {formatNumber(item.count)}</p> : null}
            </div>
          </li>)}
        </ul> : <p className="text-sm text-muted">No checklist was included in this report.</p>}
      </section>
      {report.sections.map((section, index) => (
        <section key={`${section.title}-${index}`} className={cn(styles.section, "border-t border-border pt-6")} aria-labelledby={`report-section-${index}`}>
          <h3 id={`report-section-${index}`} className="mb-3 text-base font-semibold">{section.title}</h3>
          {section.body ? <Narrative text={section.body} /> : <p className="text-sm text-muted">No narrative supplied for this section.</p>}
          {section.evidence_ids.length ? (
            <div className="mt-4 rounded-lg border border-border bg-surface-2/40 p-3">
              <h4 className="mb-2 font-mono text-[10px] text-subtle uppercase">Supporting evidence</h4>
              <ul className="flex flex-wrap gap-x-4 gap-y-2">
                {section.evidence_ids.map((id, evidenceIndex) => <li key={`${id}-${evidenceIndex}`} className="min-w-0">
                  <Link href={`/memory?focus=${encodeURIComponent(id)}`} className="inline-flex max-w-full items-center gap-1 break-all font-mono text-[11px] text-emerald-300 hover:underline">{id}<ArrowUpRight className="size-3 shrink-0" aria-hidden /></Link>
                </li>)}
              </ul>
            </div>
          ) : null}
          {Object.keys(section.data).length ? <details className={cn(styles.screenOnly, "mt-3 rounded-lg border border-border")}><summary className="cursor-pointer px-3 py-2 text-xs text-muted">Supporting structured data</summary><pre className="max-h-72 overflow-auto border-t border-border p-3 text-[11px] text-muted">{JSON.stringify(section.data, null, 2)}</pre></details> : null}
        </section>
      ))}
      <section className={cn(styles.section, "border-t border-border pt-6")} aria-labelledby="report-open-items">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-2"><h3 id="report-open-items" className="text-base font-semibold">Open items &amp; controller actions</h3><span className="font-mono text-xs text-muted">{report.open_items.length} items</span></div>
        {report.open_items.length ? <ol className="space-y-3">{report.open_items.map((item, index) => <OpenItem key={textValue(item.exception_id) ?? textValue(item.id) ?? index} item={item} index={index} />)}</ol> : <p className="text-sm leading-relaxed text-muted">No open items are recorded in this report. Review the checklist and period status above before final sign-off.</p>}
        <Link href={`/exceptions?period_id=${encodeURIComponent(report.period_id)}`} className={cn(styles.screenOnly, "mt-4 inline-flex items-center gap-1 text-xs text-emerald-300 hover:underline")}>Open exception queue<ArrowUpRight className="size-3" aria-hidden /></Link>
      </section>
      <footer className="border-t border-border pt-5 text-[10px] leading-relaxed text-subtle">
        Generated from the recorded period close and financial memory. Evidence IDs link to their provenance; run metrics reflect the recorded run, and unresolved items remain subject to controller review.
      </footer>
    </article>
  );
}

function OpenItem({ item, index }: { item: JsonObject; index: number }) {
  const id = textValue(item.exception_id) ?? textValue(item.id);
  const title = textValue(item.title) ?? textValue(item.description) ?? textValue(item.name) ?? `Open item ${index + 1}`;
  const status = textValue(item.status);
  const description = textValue(item.description) ?? textValue(item.detail);
  const known = new Set(["id", "exception_id", "title", "description", "detail", "name", "status", "amount"]);
  const extra = Object.entries(item).filter(([key]) => !known.has(key));
  return (
    <li className="rounded-lg border border-amber-400/20 bg-amber-400/5 p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <h4 className="min-w-0 break-words text-sm font-medium">{id ? <Link href={`/exceptions?id=${encodeURIComponent(id)}`} className="text-amber-100 hover:underline">{title}</Link> : title}</h4>
        {status ? <StatusPill status={status} kind="exception" /> : null}
      </div>
      {description && description !== title ? <p className="mt-2 whitespace-pre-wrap break-words text-xs leading-relaxed text-muted">{description}</p> : null}
      {typeof item.amount === "number" ? <p className="mt-2 font-mono text-sm">{formatMoney(item.amount)}</p> : null}
      {id ? <p className="mt-2 break-all font-mono text-[10px] text-subtle">{id}</p> : null}
      {extra.length ? <dl className="mt-3 space-y-1 text-xs text-muted">{extra.map(([key, value]) => <div key={key} className="flex flex-wrap gap-x-2"><dt className="font-medium">{humanize(key)}:</dt><dd className="min-w-0 whitespace-pre-wrap break-words">{typeof value === "string" ? value : JSON.stringify(value)}</dd></div>)}</dl> : null}
      <div className="mt-3"><EvidenceLinks data={item} /></div>
    </li>
  );
}
