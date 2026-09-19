"use client";

import Link from "next/link";
import { useState } from "react";
import { ArrowUpRight, BookOpen, ShieldCheck } from "lucide-react";
import { EmptyState, StatusPill } from "@/components/shared";
import { Card } from "@/components/ui/card";
import { Input, inputClassName } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import type { RuleView } from "@/lib/types";
import { formatPercent, humanize, periodMonth } from "@/lib/utils";

export function RuleLibrary({ rules, loading, error }: { rules: RuleView[]; loading: boolean; error: string | null }) {
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("active");
  const visible = rules.filter((rule) => (!status || rule.status === status) && [rule.description, rule.scope_name, rule.scope_id, rule.pattern_type, rule.id].some((value) => value?.toLowerCase().includes(search.trim().toLowerCase())));

  return (
    <section className="space-y-4" aria-labelledby="rule-library-title">
      <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-end">
        <div><p className="mb-1 font-mono text-[10px] uppercase tracking-[0.16em] text-muted">Institutional knowledge</p><h2 id="rule-library-title" className="text-lg font-semibold">Rule library <span className="ml-2 font-mono text-sm text-muted">{rules.length}</span></h2><p className="mt-1 text-xs text-muted">Across all periods. Each version preserves where it came from and how it earned trust.</p></div>
        <div className="flex gap-2"><Input aria-label="Search learned rules" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search rules…" className="sm:w-52" /><select aria-label="Rule status" className={`${inputClassName} w-36`} value={status} onChange={(event) => setStatus(event.target.value)}><option value="active">Active</option><option value="superseded">Superseded</option><option value="">All versions</option></select></div>
      </div>
      {error && <p role="alert" className="rounded-lg border border-amber-400/20 bg-amber-500/5 p-3 text-xs text-amber-200">{error}. Rule records could not be refreshed.</p>}
      {loading ? <p role="status" className="text-sm text-muted">Loading learned rules…</p> : !visible.length ? <EmptyState icon={BookOpen} title={error ? "Rule library unavailable" : rules.length ? "No rules match these filters" : "Your next correction starts the library"}
        description="Teach an exception to create a rule. Its source, version, and confirmations will appear here."
        action={<Link href="/exceptions" className="text-sm text-emerald-300 hover:underline">Open exception desk →</Link>} /> : (
        <div className="grid gap-3 lg:grid-cols-2 2xl:grid-cols-3">{visible.map((rule) => <Card key={rule.id} className="flex flex-col gap-4 p-4">
          <div className="flex flex-wrap items-center gap-2"><StatusPill status={rule.status} size="sm" /><span className="rounded border border-border-strong px-1.5 py-0.5 font-mono text-[10px] text-muted">v{rule.version}</span><span className="ml-auto text-[10px] text-subtle">{rule.learned_in_period ? `${periodMonth(rule.learned_in_period)} · ${rule.learned_in_period}` : "Period not recorded"}</span></div>
          <Link href={`/memory?focus=${encodeURIComponent(rule.id)}`} className="group flex items-start gap-3"><h3 className="text-sm leading-relaxed group-hover:text-emerald-200">{rule.description || humanize(rule.pattern_type)}</h3><ArrowUpRight className="ml-auto mt-0.5 size-4 shrink-0 text-muted" aria-hidden /></Link>
          <p className="text-xs text-muted">{humanize(rule.pattern_type)} · {rule.scope_name || rule.scope_id || humanize(rule.scope_type)}</p>
          <div className="mt-auto space-y-2"><div className="flex justify-between text-xs"><span className="text-muted">Trust score</span><span className="font-mono text-emerald-200">{formatPercent(rule.trust_score)}</span></div><Progress value={rule.trust_score * 100} label={`Trust for ${rule.description}`} /></div>
          <div className="grid grid-cols-3 gap-2 border-t border-border pt-3 text-center">{[["Applied", rule.trust.times_applied], ["Confirmed", rule.trust.times_confirmed], ["Refuted", rule.trust.times_refuted]].map(([label, value]) => <div key={label}><p className="font-mono text-sm">{value}</p><p className="mt-0.5 text-[10px] text-muted">{label}</p></div>)}</div>
          <div className="flex flex-wrap gap-3 text-[10px] text-muted">{rule.trust.human_verified && <span className="inline-flex items-center gap-1 text-violet-300"><ShieldCheck className="size-3" aria-hidden />Human verified</span>}{rule.source_id && <Link className="hover:text-foreground hover:underline" href={`/memory?focus=${encodeURIComponent(rule.source_id)}`}>Original correction →</Link>}{rule.supersedes && <Link className="hover:text-foreground hover:underline" href={`/memory?focus=${encodeURIComponent(rule.supersedes)}`}>Previous version →</Link>}</div>
        </Card>)}</div>
      )}
    </section>
  );
}
