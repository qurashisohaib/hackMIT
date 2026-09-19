import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { LoaderCircle } from "lucide-react";
import { cn } from "@/lib/utils";

export const buttonVariants = cva(
  "inline-flex shrink-0 items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium transition-[background-color,border-color,color,box-shadow,transform] duration-150 select-none disabled:pointer-events-none disabled:opacity-50 active:translate-y-px [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
  {
    variants: {
      variant: {
        default:
          "bg-emerald-500 text-emerald-950 shadow-[0_0_0_1px_rgba(16,185,129,0.35),0_8px_24px_-8px_rgba(16,185,129,0.55)] hover:bg-emerald-400",
        secondary: "border border-border-strong bg-surface-2 text-foreground hover:bg-surface-3 hover:border-subtle",
        outline: "border border-border-strong bg-transparent text-foreground hover:bg-surface-2",
        ghost: "text-muted hover:bg-surface-2 hover:text-foreground",
        destructive: "bg-rose-500/15 text-rose-200 border border-rose-500/40 hover:bg-rose-500/25",
        link: "text-emerald-300 underline-offset-4 hover:underline h-auto p-0",
      },
      size: {
        xs: "h-7 px-2.5 text-xs rounded-sm gap-1.5 [&_svg:not([class*='size-'])]:size-3.5",
        sm: "h-8 px-3 text-xs gap-1.5",
        md: "h-9 px-4",
        lg: "h-11 px-5 text-base rounded-lg",
        icon: "size-9",
        "icon-sm": "size-8",
        "icon-xs": "size-7 rounded-sm",
      },
    },
    defaultVariants: { variant: "default", size: "md" },
  },
);

export interface ButtonProps extends React.ComponentProps<"button">, VariantProps<typeof buttonVariants> {
  /** Shows a spinner and disables the button. */
  loading?: boolean;
}

/** Hand-rolled shadcn-style button. Use `buttonVariants()` for Link elements. */
export function Button({ className, variant, size, loading = false, disabled, children, type, ...props }: ButtonProps) {
  return (
    <button
      type={type ?? "button"}
      data-slot="button"
      data-loading={loading || undefined}
      className={cn(buttonVariants({ variant, size }), className)}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...props}
    >
      {loading ? <LoaderCircle className="animate-spin" aria-hidden /> : null}
      {children}
    </button>
  );
}
