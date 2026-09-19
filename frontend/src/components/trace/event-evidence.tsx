import Link from "next/link";
import { ArrowUpRight, ChevronRight } from "lucide-react";
import { AgentChip } from "@/components/shared/agent-chip";
import { ConfidenceBadge } from "@/components/shared/confidence-badge";
import { evidenceLinks, objectValue, textValue } from "@/components/trace/trace-data";
import type { AgentEvent, JsonObject } from "@/lib/types";
import { cn, formatTime, humanize } from "@/lib/utils";

export function EvidenceLinks({ data }: { data: JsonObject }) {
  const links = evidenceLinks(data);
  if (!links.length) return null;
  return (
    <div className="flex flex-wrap gap-2">
      {links.map((link) => (
        <Link key={link.href} href={link.href} className="inline-flex max-w-full items-center gap-1 rounded border border-border-strong bg-surface-2 px-2 py-1 font-mono text-[11px] text-emerald-300 hover:border-emerald-400/50">
          <span className="truncate">{link.kind === "exception" ? "Exception" : "Memory"} · {link.id}</span>
          <ArrowUpRight className="size-3 shrink-0" aria-hidden />
        </Link>
      ))}
    </div>
  );
}

export function EventEvidence({ event }: { event: AgentEvent }) {
  const hypothesis = objectValue(event.data.hypothesis) ?? objectValue(event.data.best);
  const rawEvidence = event.data.evidence ?? hypothesis?.evidence;
  const evidence = Array.isArray(rawEvidence) ? rawEvidence.map(objectValue).filter((item) => item !== null) : [];
  const confidence = event.data.confidence ?? hypothesis?.confidence;
  return (
    <div className="space-y-3">
      {event.detail ? <p className="whitespace-pre-wrap break-words text-sm leading-relaxed text-muted">{event.detail}</p> : null}
      {hypothesis ? (
        <div className="rounded-md border border-violet-400/20 bg-violet-500/5 p-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-xs text-violet-200">{humanize(textValue(hypothesis.kind) ?? "Hypothesis")}</span>
            {typeof confidence === "number" ? <ConfidenceBadge confidence={confidence} /> : null}
            {typeof hypothesis.passed === "boolean" ? <span className="text-xs text-muted">{hypothesis.tested === false ? "Untested" : hypothesis.passed ? "Passed" : "Failed"}</span> : null}
          </div>
          {textValue(hypothesis.description) ? <p className="mt-2 text-sm">{String(hypothesis.description)}</p> : null}
        </div>
      ) : typeof confidence === "number" ? <ConfidenceBadge confidence={confidence} /> : null}
      {evidence.length ? (
        <ul className="space-y-2">
          {evidence.map((item, index) => (
            <li key={index} className="rounded-md border border-border bg-surface-2 p-3 text-xs">
              <span className={cn("mr-2 font-mono", item.supports === true ? "text-emerald-300" : item.supports === false ? "text-rose-300" : "text-muted")}>
                {item.supports === true ? "SUPPORTS" : item.supports === false ? "CONTRADICTS" : "EVIDENCE"}
              </span>
              <span className="leading-relaxed">{textValue(item.description) ?? humanize(textValue(item.kind) ?? "Evidence")}</span>
            </li>
          ))}
        </ul>
      ) : null}
      <EvidenceLinks data={event.data} />
      {Object.keys(event.data).length ? (
        <details className="rounded-md border border-border bg-background/60">
          <summary className="cursor-pointer px-3 py-2 font-mono text-xs text-muted">Structured payload · tool arguments, results &amp; provenance</summary>
          <pre className="max-h-80 overflow-auto border-t border-border p-3 font-mono text-[11px] leading-relaxed text-muted">{JSON.stringify(event.data, null, 2)}</pre>
        </details>
      ) : null}
    </div>
  );
}

export function EventRow({ event }: { event: AgentEvent }) {
  return (
    <details id={`event-${event.id}`} className="group border-b border-border last:border-0 open:bg-surface-2/30">
      <summary className="flex cursor-pointer list-none items-start gap-3 px-4 py-3 hover:bg-surface-2/60 [&::-webkit-details-marker]:hidden">
        <ChevronRight className="mt-1 size-3 shrink-0 text-subtle transition-transform group-open:rotate-90" aria-hidden />
        <div className="hidden w-20 shrink-0 font-mono text-[10px] text-subtle sm:block">
          <div>{formatTime(event.ts)}</div>
          <div className="mt-1">#{event.seq}</div>
        </div>
        <div className="min-w-0 flex-1">
          <div className="mb-1 flex flex-wrap items-center gap-2">
            <AgentChip agent={event.agent} size="xs" />
            <span className="font-mono text-[10px] text-muted">{event.kind}</span>
          </div>
          <p className="break-words text-sm">{event.title || humanize(event.kind)}</p>
        </div>
      </summary>
      <div className="px-4 pb-4 sm:pl-32"><EventEvidence event={event} /></div>
    </details>
  );
}
