import { Suspense } from "react";
import { ExceptionWorkspace } from "@/components/exceptions/exception-workspace";
import { EmptyState } from "@/components/shared/empty-state";

export default function ExceptionsPage() {
  return (
    <Suspense fallback={<EmptyState title="Loading exception workspace" description="Preparing the review queue…" />}>
      <ExceptionWorkspace />
    </Suspense>
  );
}
