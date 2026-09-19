import * as React from "react";
import { cn } from "@/lib/utils";

export function Textarea({ className, ...props }: React.ComponentProps<"textarea">) {
  return (
    <textarea
      data-slot="textarea"
      className={cn(
        "flex min-h-20 w-full rounded-md border border-border-strong bg-surface-2 px-3 py-2 text-sm leading-relaxed text-foreground outline-none transition-[border-color,box-shadow] placeholder:text-subtle hover:border-subtle focus-visible:border-emerald-400/60 focus-visible:ring-2 focus-visible:ring-emerald-400/25 disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-rose-400/60 aria-invalid:ring-2 aria-invalid:ring-rose-400/25",
        className,
      )}
      {...props}
    />
  );
}
