import * as React from "react";
import { cn } from "@/lib/utils";

export interface ScrollAreaProps extends React.ComponentProps<"div"> {
  orientation?: "vertical" | "horizontal" | "both";
  /** Fade the far edge to hint at more content. */
  fade?: boolean;
}

/** Scroll container with the thin terminal scrollbar. Set a height/max-height via className. */
export function ScrollArea({ orientation = "vertical", fade = false, className, ...props }: ScrollAreaProps) {
  return (
    <div
      data-slot="scroll-area"
      className={cn(
        "relative min-h-0 min-w-0",
        orientation === "vertical" && "overflow-y-auto overflow-x-hidden",
        orientation === "horizontal" && "overflow-x-auto overflow-y-hidden",
        orientation === "both" && "overflow-auto",
        fade && orientation === "vertical" && "mask-fade-b",
        className,
      )}
      {...props}
    />
  );
}
