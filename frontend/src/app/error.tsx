"use client";

import Link from "next/link";
import { Button, buttonVariants } from "@/components/ui/button";

export default function ErrorPage({ error, retry }: { error: Error & { digest?: string }; retry: () => void }) {
  return (
    <section role="alert" className="mx-auto flex max-w-xl flex-col items-start gap-4 rounded-xl border border-rose-500/30 bg-surface p-8">
      <p className="font-mono text-xs text-rose-300">Unable to display this page</p>
      <h1 className="text-2xl font-semibold">Something went wrong</h1>
      <p className="text-sm text-muted">Try loading the page again, or return to the dashboard to check the current close.</p>
      {error.digest ? <p className="font-mono text-xs text-subtle">Reference: {error.digest}</p> : null}
      <div className="flex gap-3">
        <Button onClick={retry}>Try again</Button>
        <Link href="/" className={buttonVariants({ variant: "secondary" })}>Dashboard</Link>
      </div>
    </section>
  );
}
