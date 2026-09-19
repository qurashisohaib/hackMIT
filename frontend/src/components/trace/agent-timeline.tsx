import { AgentChip } from "@/components/shared/agent-chip";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { AGENT_ORDER, getAgent } from "@/lib/agents";
import type { AgentEvent } from "@/lib/types";
import { cn, formatMs } from "@/lib/utils";

export function AgentTimeline({ events, selected, onSelect }: { events: AgentEvent[]; selected: string; onSelect: (agent: string) => void }) {
  const first = Date.parse(events[0]?.ts ?? "");
  const last = Date.parse(events[events.length - 1]?.ts ?? "");
  const duration = Number.isFinite(last - first) ? Math.max(0, last - first) : 0;
  const lanes = AGENT_ORDER.map((id) => {
    const positions = events.flatMap((event, index) => getAgent(event.agent).id === id ? [index] : []);
    const laneEvents = events.filter((event) => getAgent(event.agent).id === id);
    return { id, positions, last: laneEvents[laneEvents.length - 1] };
  });
  return (
    <Card>
      <CardHeader>
        <CardTitle>Agent lanes</CardTitle>
        <CardDescription>Event order across the close · {formatMs(duration)} observed · select a lane to filter the log.</CardDescription>
      </CardHeader>
      <div className="space-y-1 px-3 pb-4">
        {lanes.map(({ id, positions, last: lastEvent }) => {
          const agent = getAgent(id);
          const start = positions.length ? positions[0] / Math.max(events.length, 1) * 100 : 0;
          const width = positions.length ? Math.max(0.8, ((positions[positions.length - 1] - positions[0] + 1) / Math.max(events.length, 1)) * 100) : 0;
          return (
            <button key={id} type="button" aria-pressed={selected === id} onClick={() => onSelect(selected === id ? "all" : id)} className={cn("flex w-full items-center gap-3 rounded-md px-2 py-2 text-left hover:bg-surface-2", selected === id && "bg-surface-3 ring-1 ring-border-strong")}>
              <span className="w-24 shrink-0"><AgentChip agent={id} size="xs" /></span>
              <span className="relative h-5 min-w-0 flex-1 overflow-hidden rounded bg-background" aria-hidden>
                {positions.length ? <span className="absolute inset-y-1 rounded-sm" style={{ left: `${start}%`, width: `${Math.min(width, 100 - start)}%`, background: agent.hex, opacity: 0.7 }} /> : null}
              </span>
              <span className="w-12 shrink-0 text-right font-mono text-[11px] text-muted">{positions.length}</span>
              <span className={cn("hidden w-16 text-right text-[10px] sm:block", agent.text)}>{!lastEvent ? "Waiting" : lastEvent.kind === "agent.finished" ? "Finished" : "Observed"}</span>
            </button>
          );
        })}
      </div>
    </Card>
  );
}
