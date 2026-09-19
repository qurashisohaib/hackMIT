import { RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import type { PeriodView } from "@/lib/types";

export function PeriodControls({ periods, periodId, onChange, onRefresh }: {
  periods: PeriodView[];
  periodId: string | null;
  onChange: (id: string) => void;
  onRefresh: () => void;
}) {
  return (
    <>
      <Select aria-label="Select period" value={periodId ?? ""} onChange={(event) => onChange(event.target.value)} wrapperClassName="w-52" disabled={!periods.length}>
        {!periods.some((period) => period.id === periodId) ? <option value={periodId ?? ""}>{periodId ?? "Select a period"}</option> : null}
        {periods.map((period) => <option key={period.id} value={period.id}>{period.name || period.id}</option>)}
      </Select>
      <Button size="icon" variant="outline" aria-label="Refresh period data" onClick={onRefresh}><RefreshCw /></Button>
    </>
  );
}
