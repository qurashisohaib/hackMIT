"use client";

import * as React from "react";
import Link from "next/link";
import { ArrowRight, Waypoints } from "lucide-react";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { MemoryStats } from "@/lib/types";
import { cn, formatNumber } from "@/lib/utils";

function pick(stats: MemoryStats | null, direct: "rules" | "corrections", label: string): number | null {
  if (!stats) return null;
  const d = stats[direct];
  if (typeof d === "number") return d;
  const v = stats.by_label?.[label];
  return typeof v === "number" ? v : 0;
}

const LABEL_ORDER = ["Rule", "HumanCorrection", "Exception", "Hypothesis", "Decision", "AuditFinding", "Observation"];

export interface MemoryTileProps {
  stats: MemoryStats | null;
  loading: boolean;
  className?: string;
}

/** Financial Memory Graph counters (nodes, edges, rules, corrections) linking to /memory. */
export function MemoryTile({ stats, loading, className }: MemoryTileProps) {
  const nodes = stats?.nodes ?? null;
  const edges = stats?.edges ?? null;
  const rules = pick(stats, "rules", "Rule");
  const corrections = pick(stats, "corrections", "HumanCorrection");

  const byLabel = React.useMemo(() => {
    const src = stats?.by_label ?? {};
    const rows = LABEL_ORDER.filter((l) => typeof src[l] === "number" && src[l] > 0).map((l) => ({ label: l, count: src[l] }));
    const max = rows.reduce((m, r) => Math.max(m, r.count), 0);
    return { rows, max };
  }, [stats]);

  return (
    <Card className={cn("flex flex-col", className)}>
      <CardHeader className="flex-row items-start justify-between gap-3 pb-3">
        <div>
          <CardTitle className="flex items-center gap-2">
            <Waypoints className="size-4 text-violet-300" aria-hidden />
            Financial Memory Graph
          </CardTitle>
          <CardDescription>Exceptions, hypotheses, decisions, corrections and rules with provenance edges.</CardDescription>
        </div>
        <Link href="/memory" className="inline-flex shrink-0 items-center gap-1 text-xs font-medium text-emerald-300 hover:underline">
          Open <ArrowRight className="size-3.5" aria-hidden />
        </Link>
      </CardHeader>
      <div className="px-5 pb-5">
        {loading && !stats ? (
          <div className="grid grid-cols-4 gap-3">
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        ) : (
          <>
            <dl className="grid grid-cols-4 gap-3">
              {[
                { label: "Nodes", value: nodes },
                { label: "Edges", value: edges },
                { label: "Rules", value: rules, tone: "text-violet-200" },
                { label: "Corrections", value: corrections, tone: "text-rose-200" },
              ].map((s) => (
                <div key={s.label} className="min-w-0">
                  <dt className="text-[11px] text-muted">{s.label}</dt>
                  <dd className={cn("text-xl leading-tight font-semibold tracking-tight", s.tone)}>{s.value === null ? "—" : formatNumber(s.value)}</dd>
                </div>
              ))}
            </dl>
            {byLabel.rows.length ? (
              <ul className="mt-4 space-y-1.5">
                {byLabel.rows.map((r) => (
                  <li key={r.label} className="flex items-center gap-2 text-[11px]">
                    <span className="w-28 shrink-0 truncate font-mono text-muted">{r.label}</span>
                    <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-violet-500/12" aria-hidden>
                      <span className="block h-full rounded-full bg-violet-400/80 transition-[width] duration-500" style={{ width: `${Math.max(3, (r.count / byLabel.max) * 100)}%` }} />
                    </span>
                    <span className="w-10 shrink-0 text-right font-mono tabular-nums">{formatNumber(r.count)}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-4 rounded-md border border-dashed border-border-strong px-3 py-3 text-center text-[11px] text-muted">
                {stats ? "Memory is empty — run a close and teach a rule to populate it." : "Memory stats unavailable."}
              </p>
            )}
          </>
        )}
      </div>
    </Card>
  );
}
