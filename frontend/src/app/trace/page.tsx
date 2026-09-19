import { Suspense } from "react";
import { TraceWorkspace } from "@/components/trace/trace-workspace";
import { Skeleton } from "@/components/ui/skeleton";

export default function TracePage() {
  return (
    <Suspense fallback={<Skeleton className="h-96 w-full" aria-label="Loading agent trace" />}>
      <TraceWorkspace />
    </Suspense>
  );
}
