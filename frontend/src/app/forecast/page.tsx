import { Suspense } from "react";
import { ForecastWorkspace } from "@/components/forecast/forecast-workspace";
import { Skeleton } from "@/components/ui/skeleton";

export default function ForecastPage() {
  return (
    <Suspense fallback={<Skeleton className="h-96 w-full" aria-label="Loading cash forecast" />}>
      <ForecastWorkspace />
    </Suspense>
  );
}
