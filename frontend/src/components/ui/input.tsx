import * as React from "react";
import { cn } from "@/lib/utils";

export const inputClassName =
  "flex h-9 w-full min-w-0 rounded-md border border-border-strong bg-surface-2 px-3 py-1 text-sm text-foreground shadow-none transition-[border-color,box-shadow] outline-none placeholder:text-subtle hover:border-subtle focus-visible:border-emerald-400/60 focus-visible:ring-2 focus-visible:ring-emerald-400/25 disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-rose-400/60 aria-invalid:ring-2 aria-invalid:ring-rose-400/25 file:border-0 file:bg-transparent file:text-sm file:font-medium";

export function Input({ className, type = "text", ...props }: React.ComponentProps<"input">) {
  return <input type={type} data-slot="input" className={cn(inputClassName, className)} {...props} />;
}
