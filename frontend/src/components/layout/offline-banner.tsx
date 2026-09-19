"use client";

import * as React from "react";
import { RefreshCw, WifiOff } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useHealth } from "@/components/layout/health-provider";
import { API_BASE } from "@/lib/api";
import { cn } from "@/lib/utils";

/** Shown app-wide when GET /api/health fails. Mount once in the shell. */
export function OfflineBanner({ className }: { className?: string }) {
  const { online, checking, refresh } = useHealth();
  if (online !== false) return null;
  return (
    <div
      role="alert"
      className={cn(
        "flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-rose-500/30 bg-rose-500/10 px-4 py-2.5 text-sm text-rose-100 sm:px-6 lg:px-8 animate-fade-in",
        className,
      )}
    >
      <span className="inline-flex items-center gap-2 font-medium">
        <WifiOff className="size-4 text-rose-300" aria-hidden />
        Backend offline
      </span>
      <span className="text-rose-200/80">
        Start it with <code className="rounded bg-rose-500/15 px-1.5 py-0.5 font-mono text-xs text-rose-100">make backend</code>
        <span className="hidden sm:inline">
          {" "}
          — expecting <span className="font-mono text-xs">{API_BASE}</span>
        </span>
      </span>
      <Button size="xs" variant="ghost" className="ml-auto text-rose-100 hover:bg-rose-500/20 hover:text-white" onClick={() => void refresh()} loading={checking}>
        {checking ? null : <RefreshCw aria-hidden />}
        Retry
      </Button>
    </div>
  );
}
