import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

export const badgeVariants = cva(
  "inline-flex shrink-0 items-center gap-1 rounded-md border font-medium whitespace-nowrap leading-none [&_svg]:size-3 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default: "border-border-strong bg-surface-2 text-foreground",
        outline: "border-border-strong bg-transparent text-muted",
        subtle: "border-transparent bg-surface-3 text-muted",
        emerald: "border-emerald-400/30 bg-emerald-500/12 text-emerald-200",
        amber: "border-amber-400/30 bg-amber-500/12 text-amber-200",
        rose: "border-rose-400/30 bg-rose-500/12 text-rose-200",
        sky: "border-sky-400/30 bg-sky-500/12 text-sky-200",
        violet: "border-violet-400/30 bg-violet-500/12 text-violet-200",
        indigo: "border-indigo-400/30 bg-indigo-500/12 text-indigo-200",
        cyan: "border-cyan-400/30 bg-cyan-500/12 text-cyan-200",
        fuchsia: "border-fuchsia-400/30 bg-fuchsia-500/12 text-fuchsia-200",
        zinc: "border-zinc-400/30 bg-zinc-500/12 text-zinc-300",
      },
      size: {
        sm: "h-5 px-1.5 text-[10.5px] tracking-wide",
        md: "h-6 px-2 text-xs",
        lg: "h-7 px-2.5 text-sm",
      },
      mono: {
        true: "font-mono tabular-nums",
        false: "",
      },
    },
    defaultVariants: { variant: "default", size: "md", mono: false },
  },
);

export type BadgeVariant = NonNullable<VariantProps<typeof badgeVariants>["variant"]>;

export interface BadgeProps extends React.ComponentProps<"span">, VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, size, mono, ...props }: BadgeProps) {
  return <span data-slot="badge" className={cn(badgeVariants({ variant, size, mono }), className)} {...props} />;
}
