"use client";

import * as React from "react";
import { RotateCcw, TriangleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { errorMessage, resetDemo } from "@/lib/api";

export interface ResetDemoButtonProps {
  /** Called after a successful reset (refresh data, clear live run). */
  onReset?: () => void;
  disabled?: boolean;
  size?: "xs" | "sm" | "md";
}

/** "Reset demo" — POST /api/reset behind a confirm dialog. */
export function ResetDemoButton({ onReset, disabled = false, size = "sm" }: ResetDemoButtonProps) {
  const [open, setOpen] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const confirm = async () => {
    setBusy(true);
    setError(null);
    try {
      await resetDemo();
      setOpen(false);
      onReset?.();
    } catch (err) {
      setError(errorMessage(err, "Reset failed"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <Button variant="outline" size={size} onClick={() => setOpen(true)} disabled={disabled} title="Reseed financial truth and clear the memory graph">
        <RotateCcw aria-hidden />
        Reset demo
      </Button>
      <Dialog
        open={open}
        onOpenChange={(v) => {
          if (!busy) setOpen(v);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <span className="inline-flex size-7 items-center justify-center rounded-md border border-amber-400/30 bg-amber-500/12 text-amber-300">
                <TriangleAlert className="size-4" aria-hidden />
              </span>
              Reset the demo?
            </DialogTitle>
            <DialogDescription>
              This reseeds the synthetic financial truth and clears the Financial Memory Graph — every learned rule, human
              correction, decision and run is deleted. The three periods return to their unreconciled state.
            </DialogDescription>
          </DialogHeader>
          {error ? (
            <p role="alert" className="rounded-md border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-200">
              {error}
            </p>
          ) : null}
          <DialogFooter>
            <Button variant="ghost" onClick={() => setOpen(false)} disabled={busy}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={() => void confirm()} loading={busy} data-autofocus>
              {busy ? "Resetting…" : "Reset everything"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
