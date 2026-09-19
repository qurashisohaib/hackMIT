import { Suspense } from "react";
import { ReportWorkspace } from "@/components/reports/report-workspace";
import { Skeleton } from "@/components/ui/skeleton";

export default function ReportsPage() {
  return (
    <Suspense fallback={<Skeleton className="h-96 w-full" aria-label="Loading close report" />}>
      <ReportWorkspace />
    </Suspense>
  );
}
