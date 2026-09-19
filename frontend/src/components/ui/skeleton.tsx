import * as React from "react";
import { cn } from "@/lib/utils";

/** Loading placeholder. Give it explicit width/height classes. */
export function Skeleton({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="skeleton" aria-hidden className={cn("shimmer rounded-md", className)} {...props} />;
}
