import * as React from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

export interface SelectOption {
  value: string;
  label: string;
  disabled?: boolean;
}

export interface SelectProps extends React.ComponentProps<"select"> {
  /** Convenience: render `<option>`s from a list instead of children. */
  options?: SelectOption[];
  /** Placeholder rendered as a disabled first option (value ""). */
  placeholder?: string;
  /** Class name for the outer wrapper (width, etc.). */
  wrapperClassName?: string;
}

/** Native `<select>` styled to match inputs, with a chevron affordance. */
export function Select({ className, wrapperClassName, options, placeholder, children, ...props }: SelectProps) {
  return (
    <div data-slot="select" className={cn("relative inline-flex w-full", wrapperClassName)}>
      <select
        className={cn(
          "h-9 w-full appearance-none rounded-md border border-border-strong bg-surface-2 py-1 pr-9 pl-3 text-sm text-foreground outline-none transition-[border-color,box-shadow] hover:border-subtle focus-visible:border-emerald-400/60 focus-visible:ring-2 focus-visible:ring-emerald-400/25 disabled:cursor-not-allowed disabled:opacity-50",
          className,
        )}
        {...props}
      >
        {placeholder ? (
          <option value="" disabled>
            {placeholder}
          </option>
        ) : null}
        {options
          ? options.map((o) => (
              <option key={o.value} value={o.value} disabled={o.disabled}>
                {o.label}
              </option>
            ))
          : children}
      </select>
      <ChevronDown className="pointer-events-none absolute top-1/2 right-3 size-4 -translate-y-1/2 text-muted" aria-hidden />
    </div>
  );
}
