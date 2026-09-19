import type { AgentEvent, JsonObject } from "@/lib/types";

export function mergeEvents(...batches: AgentEvent[][]): AgentEvent[] {
  const events = new Map<string, AgentEvent>();
  for (const batch of batches) {
    for (const event of batch) events.set(event.id, event);
  }
  return [...events.values()].sort((a, b) => a.seq - b.seq || a.ts.localeCompare(b.ts));
}

export function objectValue(value: unknown): JsonObject | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as JsonObject
    : null;
}

export function textValue(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

export interface EvidenceLink {
  id: string;
  href: string;
  kind: "exception" | "memory";
}

const NODE_KEYS = new Set([
  "node_id", "rule_id", "decision_id", "hypothesis_id", "correction_id", "finding_id",
  "observation_id", "source_id", "evidence_ids", "hypothesis_ids", "rule_ids", "node_ids",
]);
const NODE_OBJECTS = new Set(["rule", "rules", "decision", "hypothesis", "hypotheses", "best", "correction", "node", "observation"]);

export function evidenceLinks(data: JsonObject): EvidenceLink[] {
  const links = new Map<string, EvidenceLink>();
  const add = (value: unknown, kind: EvidenceLink["kind"]) => {
    if (typeof value !== "string" || !value) return;
    const href = kind === "exception"
      ? `/exceptions?id=${encodeURIComponent(value)}`
      : `/memory?focus=${encodeURIComponent(value)}`;
    links.set(href, { id: value, href, kind });
  };
  const visit = (value: unknown, context = "", depth = 0) => {
    if (depth > 8) return;
    if (Array.isArray(value)) {
      for (const entry of value) visit(entry, context, depth + 1);
      return;
    }
    const object = objectValue(value);
    if (!object) {
      if (NODE_KEYS.has(context)) add(value, "memory");
      if (context === "exception_id" || context === "exception_ids") add(value, "exception");
      return;
    }
    for (const [key, entry] of Object.entries(object)) {
      if (key === "id" && context === "exception") add(entry, "exception");
      else if (key === "id" && NODE_OBJECTS.has(context)) add(entry, "memory");
      else visit(entry, key, depth + 1);
    }
  };
  visit(data);
  return [...links.values()];
}

export function auditThreadKey(event: AgentEvent): string | null {
  const decision = objectValue(event.data.decision);
  return textValue(event.data.decision_id)
    ?? textValue(decision?.id)
    ?? textValue(event.data.exception_id)
    ?? null;
}

export function auditThreads(events: AgentEvent[]): { id: string; events: AgentEvent[] }[] {
  const threads = new Map<string, AgentEvent[]>();
  let latest = "";
  for (const event of events) {
    if (!event.kind.startsWith("audit.")) continue;
    const reference = auditThreadKey(event);
    if (reference) latest = reference;
    else if (event.kind === "audit.challenge" || !latest) latest = event.id;
    const id = reference ?? latest;
    threads.set(id, [...(threads.get(id) ?? []), event]);
  }
  return [...threads.entries()].map(([id, thread]) => ({ id, events: thread }));
}
