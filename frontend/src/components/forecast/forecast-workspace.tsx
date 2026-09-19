"use client";

import Link from "next/link";
import { ArrowUpRight, BookOpen, CircleHelp, TrendingUp } from "lucide-react";
import { CashChart } from "@/components/forecast/cash-chart";
import { PeriodControls } from "@/components/forecast/period-controls";
import { usePeriodResource, usePeriodSelection } from "@/components/forecast/use-period-resource";
import { EmptyState } from "@/components/shared/empty-state";
import { PageHeader } from "@/components/shared/page-header";
import { buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { getForecast } from "@/lib/api";
import type { ForecastView, JsonObject } from "@/lib/types";
import { cn, formatDate, formatMoney, humanize } from "@/lib/utils";

function displayValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

function comparisonValue(key: string, value: unknown): string {
  if (typeof value !== "number") return displayValue(value);
  if (key.endsWith("_pct")) return `${value.toFixed(2)}%`;
  if (/(cash|inflow|outflow|amount|forecast|actual|net|variance)/.test(key)) return formatMoney(value);
  return value.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

export function ForecastWorkspace() {
  const selection = usePeriodSelection();
  const resource = usePeriodResource(selection.periodId, getForecast, selection.revision);
  const error = resource.error ?? selection.error;
  return (
    <div className="space-y-6">
      <PageHeader eyebrow="Treasury intelligence" title="Cash, with context" description="A 13-week view of cash requirements, informed by open receivables, payables, payroll, and financial memory." actions={
        <PeriodControls periods={selection.periods} periodId={selection.periodId} onChange={selection.selectPeriod} onRefresh={selection.refresh} />
      } />
      {error ? <div role="alert" className="rounded-lg border border-amber-400/25 bg-amber-400/5 px-4 py-3 text-sm text-amber-200">{error}. {resource.data ? "Showing the last loaded forecast; refresh when the backend is available." : "Forecast data is unavailable. Check the backend and retry."}</div> : null}
      {selection.loading || resource.loading ? (
        <div className="space-y-4" role="status" aria-label="Loading forecast"><div className="grid grid-cols-2 gap-4 lg:grid-cols-4">{[0, 1, 2, 3].map((key) => <Skeleton key={key} className="h-24" />)}</div><Skeleton className="h-96" /></div>
      ) : resource.data ? <ForecastDocument forecast={resource.data} /> : (
        <EmptyState icon={TrendingUp} title={error ? "Forecast unavailable" : !selection.periodId ? "No periods available" : "No forecast for this period"} description={error ? "No forecast values are substituted while the backend is unavailable." : "Run a period close to generate the cash forecast and its source assumptions."} action={<Link href="/" className={buttonVariants({ variant: "secondary", size: "sm" })}>Open dashboard</Link>} />
      )}
    </div>
  );
}

function ForecastDocument({ forecast }: { forecast: ForecastView }) {
  const weeks = forecast.weeks;
  const ending = weeks[weeks.length - 1]?.ending_cash;
  const minimum = weeks.length ? Math.min(forecast.opening_cash, ...weeks.map((week) => week.ending_cash)) : null;
  const memoryCount = forecast.assumptions.filter((item) => item.source === "rule" || item.source === "observation").length;
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-2 font-mono text-xs text-muted">
        <span>Period {forecast.period_id} · as of {formatDate(forecast.as_of)}</span>
        <span>{weeks.length} weeks returned · all amounts USD</span>
      </div>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {[
          { label: "Opening cash", value: forecast.opening_cash, hint: "At forecast start" },
          { label: "Ending cash", value: ending, hint: weeks.length ? `After ${weeks.length} weeks` : "Not yet available" },
          { label: "Minimum cash", value: minimum, hint: "Opening and weekly balances" },
          { label: "Memory adjustment", value: forecast.memory_adjustment_total, hint: `${memoryCount} sourced assumptions` },
        ].map((item) => (
          <Card key={item.label} className="p-4">
            <p className="font-mono text-[10px] tracking-wide text-muted uppercase">{item.label}</p>
            <p className={cn("mt-2 break-words font-mono text-xl tabular-nums sm:text-2xl", typeof item.value === "number" && item.value < 0 ? "text-rose-300" : "text-foreground")}>{formatMoney(item.value, { whole: true, signed: item.label === "Memory adjustment" })}</p>
            <p className="mt-2 text-[11px] text-subtle">{item.hint}</p>
          </Card>
        ))}
      </div>
      {weeks.length && weeks.length !== 13 ? <p role="status" className="rounded-lg border border-amber-400/25 px-4 py-3 text-sm text-amber-200">The backend returned {weeks.length} of the expected 13 weeks. Only available weeks are shown.</p> : null}
      {minimum !== null && minimum < 0 ? <p role="status" className="rounded-lg border border-rose-400/25 px-4 py-3 text-sm text-rose-200">Projected cash falls below zero. Review upcoming payment obligations and the assumptions below.</p> : null}
      <Card>
        <CardHeader><CardTitle>Cash movement &amp; runway</CardTitle><CardDescription>Inflows stack above zero, outflows below zero. The line shows the projected closing balance on the right axis.</CardDescription></CardHeader>
        <CardContent>{weeks.length ? <CashChart weeks={weeks} /> : <EmptyState compact icon={TrendingUp} title="No weekly projection available" description="The forecast contains no weekly cash values yet." />}</CardContent>
      </Card>
      <div className="grid items-start gap-6 xl:grid-cols-[1.5fr_1fr]">
        <Card className="min-w-0">
          <CardHeader><CardTitle>Weekly cash schedule</CardTitle><CardDescription>Exact backend projections. Expand a week to inspect its component amounts.</CardDescription></CardHeader>
          {weeks.length ? <div className="overflow-x-auto px-5 pb-5">
            <table className="w-full text-left text-xs">
              <caption className="sr-only">Weekly forecast in US dollars</caption>
              <thead className="border-b border-border font-mono text-[10px] text-muted uppercase"><tr><th className="py-3 pr-4">Week of</th><th className="px-2 py-3 text-right">Inflows</th><th className="px-2 py-3 text-right">Outflows</th><th className="px-2 py-3 text-right">Net</th><th className="py-3 pl-2 text-right">Ending cash</th></tr></thead>
              <tbody>
                {weeks.map((week, index) => <tr key={week.week_start} className="border-b border-border last:border-0">
                  <th scope="row" className="min-w-32 py-3 pr-4 font-normal">
                    <details>
                      <summary className="cursor-pointer whitespace-nowrap text-foreground"><span className="mr-2 font-mono text-[10px] text-subtle">W{index + 1}</span>{formatDate(`${week.week_start}T12:00:00`)}</summary>
                      <dl className="mt-3 space-y-1 text-[10px] text-muted">{Object.entries(week.detail).length ? Object.entries(week.detail).map(([key, value]) => <div key={key} className="flex justify-between gap-3"><dt>{humanize(key)}</dt><dd className="font-mono">{formatMoney(value)}</dd></div>) : <div>No component detail supplied.</div>}</dl>
                    </details>
                  </th>
                  <td className="whitespace-nowrap px-2 py-3 text-right font-mono text-emerald-300">{formatMoney(week.inflows)}</td>
                  <td className="whitespace-nowrap px-2 py-3 text-right font-mono text-violet-300">{formatMoney(week.outflows)}</td>
                  <td className={cn("whitespace-nowrap px-2 py-3 text-right font-mono", week.net < 0 && "text-rose-300")}>{formatMoney(week.net, { signed: true })}</td>
                  <td className="whitespace-nowrap py-3 pl-2 text-right font-mono">{formatMoney(week.ending_cash)}</td>
                </tr>)}
              </tbody>
            </table>
          </div> : <div className="px-5 pb-5 text-sm text-muted">No weekly values returned.</div>}
        </Card>
        <Card>
          <CardHeader><CardTitle className="flex items-center gap-2"><BookOpen className="size-4 text-cyan-300" aria-hidden />Assumptions &amp; memory</CardTitle><CardDescription>Each assumption retains its source. Defaults are identified explicitly.</CardDescription></CardHeader>
          <CardContent>
            {forecast.assumptions.length ? <ol className="space-y-3">{forecast.assumptions.map((assumption, index) => (
              <li key={`${assumption.node_id ?? assumption.source}-${index}`} className="rounded-lg border border-border bg-surface-2/50 p-3">
                <span className={cn("font-mono text-[10px] uppercase", assumption.source === "default" ? "text-amber-300" : "text-cyan-300")}>{humanize(assumption.source)}</span>
                <p className="mt-2 text-xs leading-relaxed">{assumption.description}</p>
                {assumption.value !== undefined ? <pre className="mt-2 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] text-muted">{displayValue(assumption.value)}</pre> : null}
                {assumption.node_id ? <Link href={`/memory?focus=${encodeURIComponent(assumption.node_id)}`} className="mt-3 inline-flex max-w-full items-center gap-1 font-mono text-[10px] text-emerald-300 hover:underline"><span className="truncate">{assumption.node_id}</span><ArrowUpRight className="size-3 shrink-0" aria-hidden /></Link> : <p className="mt-3 text-[10px] text-subtle">No memory node linked by the backend.</p>}
              </li>
            ))}</ol> : <EmptyState compact icon={CircleHelp} title="No assumptions supplied" description="No source assumptions were included in this forecast." />}
          </CardContent>
        </Card>
      </div>
      <Card>
        <CardHeader><CardTitle>Actuals vs. forecast</CardTitle><CardDescription>{forecast.error_pct === null ? "Forecast error is not available until comparable actuals exist." : `Reported forecast error: ${forecast.error_pct.toFixed(2)}%. Values below are supplied by the backend.`}</CardDescription></CardHeader>
        <CardContent>{forecast.actual_vs_forecast.length ? <ActualComparison rows={forecast.actual_vs_forecast} /> : <EmptyState compact icon={TrendingUp} title="No comparable actuals yet" description="This section will show recorded comparisons when actual cash movements become available. No estimated actuals are substituted." />}</CardContent>
      </Card>
    </div>
  );
}

function ActualComparison({ rows }: { rows: JsonObject[] }) {
  const columns = [...new Set(rows.flatMap((row) => Object.keys(row)))];
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs">
        <caption className="sr-only">Actual and forecast comparison returned by the backend</caption>
        <thead className="border-b border-border font-mono text-[10px] text-muted uppercase"><tr>{columns.map((column) => <th key={column} className="whitespace-nowrap px-3 py-3">{humanize(column)}</th>)}</tr></thead>
        <tbody>{rows.map((row, index) => <tr key={index} className="border-b border-border last:border-0">{columns.map((column) => <td key={column} className="max-w-sm whitespace-pre-wrap break-words px-3 py-3 font-mono">{comparisonValue(column, row[column])}</td>)}</tr>)}</tbody>
      </table>
    </div>
  );
}
