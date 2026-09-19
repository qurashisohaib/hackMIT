import { Suspense } from "react";
import { MemoryWorkspace } from "@/components/memory/memory-workspace";
import { EmptyState } from "@/components/shared/empty-state";

export default function MemoryPage() {
  return (
    <Suspense fallback={<EmptyState title="Loading financial memory" description="Preparing the graph explorer…" />}>
      <MemoryWorkspace />
    </Suspense>
  );
}
