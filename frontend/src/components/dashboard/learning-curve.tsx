"use client";

import * as React from "react";
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis, type TooltipContentProps } from "recharts";
import { ChartLine } from "lucide-react";
import { EmptyState } from "@/components/shared/empty-state";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { MetricsResponse, RunMetrics } from "@/lib/types";
import { CHART_CHROME, CHART_SERIES, cn, formatNumber, formatPercent, formatPoints, periodMonthShort } from "@/lib/utils";

/*
 * Dataviz notes: two measures of different scale (accuracy %, human reviews) → two
 * small multiples on their own axes, never a dual-axis chart. One series per panel,
 * so no legend box; the title names it. Palette validated for the dark surface
 * (CHART_SERIES). Values are also rendered as a table row beneath each panel.
 */

export interface CurvePoint {
  period_id: string;
  label: string;
  accuracy: number | null; // percent 0..100
  human: number;
  precedent: number;
  index: number;
}

export function toCurvePoints(periods: RunMetrics[] | undefined | null): CurvePoint[] {
  if (!periods?.length) return [];
  return [...periods]
    .sort((a, b) => a.period_id.localeCompare(b.period_id))
    .map((m, i) => ({
      period_id: m.period_id,
      label: periodMonthShort(m.period_id) || m.period_id,
      accuracy: m.accuracy === null || m.accuracy === undefined ? null : Math.round(m.accuracy * 1000) / 10,
      human: m.human_reviews,
      precedent: m.precedent_hits,
      index: i,
    }));
}

const AXIS_TICK = { fill: CHART_CHROME.muted, fontSize: 11, fontFamily: "var(--font-mono)" } as const;

function ChartTip({ active, payload, label, format }: TooltipContentProps & { format: (v: number) => string }) {
  if (!active || !payload?.length) return null;
  const entry = payload[0];
  const raw: unknown = entry.value;
  const value = typeof raw === "number" ? raw : null;
  return (
    <div className="rounded-md border border-border-strong bg-surface-3 px-2.5 py-1.5 text-xs shadow-lg">
      <p className="font-semibold text-foreground">{value === null ? "—" : format(value)}</p>
      <p className="mt-0.5 flex items-center gap-1.5 text-muted">
        <span className="inline-block h-0.5 w-3 rounded-full" style={{ background: entry.color ?? CHART_SERIES[0] }} aria-hidden />
        {String(label)}
      </p>
    </div>
  );
}

interface PanelProps {
  title: string;
  description: string;
  points: CurvePoint[];
  valueKey: "accuracy" | "human";
  color: string;
  format: (v: number) => string;
  /** Delta between consecutive points, formatted. */
  delta: (curr: number, prev: number) => string;
  /** Whether an increase is good (colours the delta). */
  upIsGood: boolean;
  kind: "line" | "bar";
}

function Panel({ title, description, points, valueKey, color, format, delta, upIsGood, kind }: PanelProps) {
  const latest = points.length ? points[points.length - 1] : null;
  const prev = points.length > 1 ? points[points.length - 2] : null;
  const latestValue = latest ? latest[valueKey] : null;
  const prevValue = prev ? prev[valueKey] : null;
  const change = latestValue !== null && prevValue !== null ? latestValue - prevValue : null;
  const changeTone = change === null || change === 0 ? "text-muted" : (change > 0) === upIsGood ? "text-emerald-300" : "text-rose-300";

  const tooltipContent = React.useCallback((props: TooltipContentProps) => <ChartTip {...props} format={format} />, [format]);

  return (
    <div className="min-w-0">
      <div className="flex items-baseline justify-between gap-3">
        <div>
          <p className="text-xs font-medium text-foreground">{title}</p>
          <p className="text-[11px] text-muted">{description}</p>
        </div>
        <div className="text-right">
          <p className="text-2xl leading-none font-semibold tracking-tight">{latestValue === null ? "—" : format(latestValue)}</p>
          <p className={cn("mt-1 font-mono text-[11px]", changeTone)}>
            {change === null ? (latest ? latest.label : "no runs yet") : `${delta(latestValue as number, prevValue as number)} vs ${prev?.label}`}
          </p>
        </div>
      </div>

      <div className="mt-3 h-[190px] w-full" role="img" aria-label={`${title} by period`}>
        <ResponsiveContainer width="100%" height="100%">
          {kind === "line" ? (
            <LineChart data={points} margin={{ top: 12, right: 12, bottom: 4, left: -14 }}>
              <CartesianGrid vertical={false} stroke={CHART_CHROME.grid} strokeDasharray="0" />
              <XAxis dataKey="label" tick={AXIS_TICK} axisLine={false} tickLine={false} tickMargin={8} />
              <YAxis domain={[0, 100]} ticks={[0, 50, 100]} tick={AXIS_TICK} axisLine={false} tickLine={false} tickFormatter={(v: number) => `${v}%`} width={44} />
              <Tooltip content={tooltipContent} cursor={{ stroke: CHART_CHROME.axis, strokeWidth: 1 }} isAnimationActive={false} />
              <Line
                type="monotone"
                dataKey="accuracy"
                stroke={color}
                strokeWidth={2}
                strokeLinecap="round"
                strokeLinejoin="round"
                connectNulls
                dot={{ r: 4, fill: color, stroke: CHART_CHROME.surface, strokeWidth: 2 }}
                activeDot={{ r: 6, fill: color, stroke: CHART_CHROME.surface, strokeWidth: 2 }}
                isAnimationActive
                animationDuration={600}
              />
            </LineChart>
          ) : (
            <BarChart data={points} margin={{ top: 12, right: 12, bottom: 4, left: -14 }} barCategoryGap="35%">
              <CartesianGrid vertical={false} stroke={CHART_CHROME.grid} strokeDasharray="0" />
              <XAxis dataKey="label" tick={AXIS_TICK} axisLine={false} tickLine={false} tickMargin={8} />
              <YAxis allowDecimals={false} tick={AXIS_TICK} axisLine={false} tickLine={false} width={44} />
              <Tooltip content={tooltipContent} cursor={{ fill: "rgba(255,255,255,0.04)" }} isAnimationActive={false} />
              <Bar dataKey="human" fill={color} radius={[4, 4, 0, 0]} maxBarSize={24} isAnimationActive animationDuration={600} />
            </BarChart>
          )}
        </ResponsiveContainer>
      </div>

      {/* Table twin: every value readable without hover, with period-over-period deltas. */}
      <ol className="mt-2 flex items-stretch divide-x divide-border rounded-md border border-border bg-surface-2/40 text-center">
        {points.map((p, i) => {
          const v = p[valueKey];
          const pv = i > 0 ? points[i - 1][valueKey] : null;
          const d = v !== null && pv !== null ? v - pv : null;
          const tone = d === null || d === 0 ? "text-subtle" : (d > 0) === upIsGood ? "text-emerald-300" : "text-rose-300";
          return (
            <li key={p.period_id} className="flex flex-1 flex-col items-center px-2 py-1.5">
              <span className="font-mono text-[10px] text-muted uppercase">{p.label}</span>
              <span className="font-mono text-sm tabular-nums">{v === null ? "—" : format(v)}</span>
              <span className={cn("font-mono text-[10px] tabular-nums", tone)}>{d === null ? " " : delta(v as number, pv as number)}</span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

export interface LearningCurveProps {
  metrics: MetricsResponse | null;
  loading: boolean;
  className?: string;
}

/** Accuracy % and human reviews per period, Jan → Feb → Mar, with deltas. */
export function LearningCurve({ metrics, loading, className }: LearningCurveProps) {
  const points = React.useMemo(() => toCurvePoints(metrics?.periods), [metrics]);

  return (
    <Card className={cn("flex flex-col", className)}>
      <CardHeader className="flex-row items-start justify-between gap-4 pb-4">
        <div>
          <CardTitle className="flex items-center gap-2">
            <ChartLine className="size-4 text-emerald-300" aria-hidden />
            Learning curve
          </CardTitle>
          <CardDescription>
            Accuracy is scored against hidden ground truth. Human reviews should fall as rules are learned and trusted.
          </CardDescription>
        </div>
        {points.length ? (
          <span className="shrink-0 font-mono text-[10px] tracking-wide text-subtle uppercase">
            {points.length} period{points.length === 1 ? "" : "s"}
          </span>
        ) : null}
      </CardHeader>
      <div className="px-5 pb-5">
        {loading && !metrics ? (
          <div className="grid gap-6 md:grid-cols-2">
            <Skeleton className="h-[260px] w-full" />
            <Skeleton className="h-[260px] w-full" />
          </div>
        ) : points.length === 0 ? (
          <EmptyState
            icon={ChartLine}
            title="No runs yet"
            description="Run the January close to plot the first point. Teach a rule, then run February to watch the curve bend."
            compact
          />
        ) : (
          <div className="grid gap-8 md:grid-cols-2">
            <Panel
              title="Accuracy"
              description="Decided items correct, vs ground truth"
              points={points}
              valueKey="accuracy"
              color={CHART_SERIES[0]}
              format={(v) => formatPercent(v / 100, v % 1 === 0 ? 0 : 1)}
              delta={(c, p) => formatPoints(c - p, 1)}
              upIsGood
              kind="line"
            />
            <Panel
              title="Human reviews"
              description="Exceptions escalated to the controller"
              points={points}
              valueKey="human"
              color={CHART_SERIES[1]}
              format={(v) => formatNumber(v)}
              delta={(c, p) => formatNumber(c - p, { signed: true })}
              upIsGood={false}
              kind="bar"
            />
          </div>
        )}
      </div>
    </Card>
  );
}
